"""
Proactive analysis pipeline — runs after each Plaid sync and generates nudges.

Detectors:
  1. monthly_pace        — always shows current month pace vs last month
  2. category_spike      — a category is on pace to exceed its 3-month average by ≥20%
  3. recurring_subs      — detect existing subscription/recurring charges
  4. price_change        — a recurring merchant's latest charge is ≥10% above their prior median
  5. large_transaction   — a single charge is ≥2.5× the merchant's typical amount AND ≥$40

All detectors operate on clean spending (transfers and confirmed duplicates excluded).

The pipeline is idempotent. Every nudge carries a `key` identifying what it is
about; save_nudge() upserts on it, so re-running after each sync refreshes the
existing rows rather than stacking fresh copies, and a dismissal survives the
re-run. Nudges no run has produced for 7 days are then cleared as stale.
"""

from __future__ import annotations

import calendar
import datetime
import logging
from typing import Any

import numpy as np
import pandas as pd
from dateutil.relativedelta import relativedelta

from core.insights import load_data, get_spending

log = logging.getLogger(__name__)


# ── Date helpers ──────────────────────────────────────────────────────────────

def _prior_n_full_months(df: pd.DataFrame, n: int) -> pd.DataFrame:
    """Rows from the last n complete calendar months (not including the current month)."""
    today = datetime.date.today()
    end = datetime.date(today.year, today.month, 1)
    start = end - relativedelta(months=n)
    mask = (df["date"].dt.date >= start) & (df["date"].dt.date < end)
    return df[mask].copy()


def _current_month(df: pd.DataFrame) -> pd.DataFrame:
    today = datetime.date.today()
    mask = (df["date"].dt.year == today.year) & (df["date"].dt.month == today.month)
    return df[mask].copy()


# ── Formatting helpers ────────────────────────────────────────────────────────

def _usd(amount: float) -> str:
    return f"${amount:,.0f}"


# ── Detector 1: Monthly spending pace (always fires when data exists) ─────────

def detect_monthly_pace(spending: pd.DataFrame) -> list[dict]:
    """
    Always generate a pace nudge when we have both current and last month's data.
    Info when on track or under, warning when 15%+ over, alert when 30%+ over.
    """
    nudges: list[dict] = []
    curr = _current_month(spending)
    hist = _prior_n_full_months(spending, 1)

    if curr.empty or hist.empty:
        return nudges

    today = datetime.date.today()
    days_in_month = calendar.monthrange(today.year, today.month)[1]
    elapsed = today.day / days_in_month

    curr_total = float(curr["amount"].sum())
    last_total = float(hist["amount"].sum())

    if last_total < 10:
        return nudges

    projected = curr_total / elapsed if elapsed > 0 else curr_total
    delta_pct = (projected - last_total) / last_total

    if delta_pct >= 0.30:
        severity = "alert"
        direction = f"up {delta_pct * 100:.0f}% vs last month"
    elif delta_pct >= 0.15:
        severity = "warning"
        direction = f"up {delta_pct * 100:.0f}% vs last month"
    elif delta_pct <= -0.10:
        severity = "info"
        direction = f"down {abs(delta_pct) * 100:.0f}% vs last month — nice"
    else:
        severity = "info"
        direction = "roughly on pace with last month"

    nudges.append({
        "type": "monthly_pace",
        # One pace nudge per calendar month, so next month gets a fresh one
        # rather than silently reusing (and inheriting the dismissal of) this one.
        "key": f"monthly_pace:{today:%Y-%m}",
        "severity": severity,
        "title": f"On pace for {_usd(projected)} this month",
        "body": (
            f"You've spent {_usd(curr_total)} so far ({today.day}/{days_in_month} days), "
            f"projecting {_usd(projected)} by month end — {direction} ({_usd(last_total)})."
        ),
        "data": {
            "projected": round(projected, 2),
            "last_month": round(last_total, 2),
            "current_actual": round(curr_total, 2),
            "delta_pct": round(delta_pct * 100, 1),
            "elapsed_fraction": round(elapsed, 2),
        },
    })

    return nudges


# ── Detector 2: Category spending spike ──────────────────────────────────────

def detect_category_spikes(spending: pd.DataFrame, threshold: float = 0.20) -> list[dict]:
    """
    For each category, project the month-end total based on elapsed days.
    Flag if projection exceeds the 3-month monthly average by >= threshold.
    Skip categories with < $30/month average (noise).
    """
    nudges: list[dict] = []
    hist = _prior_n_full_months(spending, 3)
    curr = _current_month(spending)

    if hist.empty or curr.empty:
        return nudges

    today = datetime.date.today()
    days_in_month = calendar.monthrange(today.year, today.month)[1]
    elapsed = today.day / days_in_month

    hist = hist.copy()
    hist["ym"] = hist["date"].dt.to_period("M")
    avg_by_cat = (
        hist.groupby(["category", "ym"])["amount"].sum()
        .groupby("category").mean()
        .round(2)
    )

    curr_by_cat = curr.groupby("category")["amount"].sum().round(2)

    for cat, curr_total in curr_by_cat.items():
        if cat not in avg_by_cat.index:
            continue
        avg = float(avg_by_cat[cat])
        if avg < 30:
            continue

        projected = float(curr_total) / elapsed if elapsed > 0 else float(curr_total)
        delta_pct = (projected - avg) / avg

        if delta_pct >= threshold:
            nudges.append({
                "type": "category_spike",
                "key": f"category_spike:{cat}:{today:%Y-%m}",
                "severity": "alert" if delta_pct >= 0.50 else "warning",
                "title": f"{cat} up {delta_pct * 100:.0f}% this month",
                "body": (
                    f"You're on pace to spend {_usd(projected)} on {cat} this month, "
                    f"vs your 3-month average of {_usd(avg)}."
                ),
                "data": {
                    "category": cat,
                    "projected": round(projected, 2),
                    "avg_3m": round(avg, 2),
                    "delta_pct": round(delta_pct * 100, 1),
                    "current_actual": round(float(curr_total), 2),
                },
            })

    return nudges


# ── Detector 3: Recurring subscriptions ──────────────────────────────────────

BILLING_CYCLES = {
    "weekly":    (6,  8),
    "bi-weekly": (12, 16),
    "monthly":   (25, 35),
    "quarterly": (85, 95),
    "annual":    (350, 380),
}


def detect_recurring_subs(spending: pd.DataFrame) -> list[dict]:
    """
    Detect recurring subscription charges — any merchant with a consistent billing
    cycle and fixed amount, regardless of when it started.

    Criteria:
      - At least 2 charges total in the last 12 months
      - Median interval falls in a known billing cycle (weekly/monthly/quarterly/annual)
      - Amount spread (max - min) <= $2.00 (fixed fee, not variable spend)
      - Last charge was within the last 2 billing cycles (still active)
    """
    nudges: list[dict] = []
    if spending.empty:
        return nudges

    today = datetime.date.today()
    cutoff = today - datetime.timedelta(days=365)
    recent = spending[spending["date"].dt.date >= cutoff].copy()

    for merchant, grp in recent.groupby("merchant_normalized"):
        if not merchant:
            continue

        grp = grp.sort_values("date")
        if len(grp) < 2:
            continue

        amounts = grp["amount"].values
        if float(amounts.max() - amounts.min()) > 2.00:
            continue  # variable spend, not a subscription

        dates = [d.date() for d in grp["date"]]
        intervals = [(dates[i] - dates[i - 1]).days for i in range(1, len(dates))]
        median_interval = sorted(intervals)[len(intervals) // 2]

        cycle_label = next(
            (label for label, (lo, hi) in BILLING_CYCLES.items() if lo <= median_interval <= hi),
            None,
        )
        if cycle_label is None:
            continue

        # Must still be active — last charge within 2 billing cycles
        days_since_last = (today - dates[-1]).days
        if days_since_last > median_interval * 2:
            continue

        mean_amt = float(np.mean(amounts))
        if mean_amt < 1:
            continue

        nudges.append({
            "type": "new_recurring",
            # Per merchant and not time-boxed: once the user has acknowledged that
            # this subscription exists, saying so again every week is just noise.
            "key": f"new_recurring:{merchant}",
            "severity": "info",
            "title": f"Recurring: {merchant}",
            "body": (
                f"{_usd(mean_amt)}/{cycle_label} — {len(grp)} charges, "
                f"last {days_since_last} day{'s' if days_since_last != 1 else ''} ago."
            ),
            "data": {
                "merchant": merchant,
                "amount": round(mean_amt, 2),
                "cycle": cycle_label,
                "median_interval_days": median_interval,
                "occurrences": len(grp),
                "last_seen": dates[-1].isoformat(),
            },
        })

    return nudges


# ── Detector 4: Subscription price increase ──────────────────────────────────

PRICE_CHANGE_CYCLES = [
    (6,  8),    # weekly
    (12, 16),   # bi-weekly
    (25, 35),   # monthly
    (85, 95),   # quarterly
    (350, 380), # annual
]

def detect_price_changes(spending: pd.DataFrame, threshold: float = 0.10) -> list[dict]:
    """
    Flag merchants whose most recent charge is >= 10% above their prior median.
    Only applies to subscription-like merchants (consistent billing cycle, fixed amounts).
    Requires at least 3 prior charges for a reliable median.
    """
    nudges: list[dict] = []
    if spending.empty:
        return nudges

    today = datetime.date.today()
    cutoff = datetime.date(today.year, today.month, 1) - relativedelta(months=6)
    recent = spending[spending["date"].dt.date >= cutoff].copy()

    if recent.empty:
        return nudges

    for merchant, grp in recent.groupby("merchant_normalized"):
        if not merchant:
            continue

        grp = grp.sort_values("date")

        if len(grp) < 4:
            continue

        dates = [d.date() for d in grp["date"]]
        intervals = [(dates[i] - dates[i - 1]).days for i in range(1, len(dates))]
        median_interval = sorted(intervals)[len(intervals) // 2]
        in_cycle = any(lo <= median_interval <= hi for lo, hi in PRICE_CHANGE_CYCLES)
        if not in_cycle:
            continue

        prev_amounts = grp.iloc[:-1]["amount"]
        latest = float(grp.iloc[-1]["amount"])
        prev_median = float(prev_amounts.median())

        if prev_median < 5:
            continue

        # Only flag if amount spread was small before the latest charge (true price change)
        prev_spread = float(prev_amounts.max() - prev_amounts.min())
        if prev_spread > 2.00:
            continue  # was already variable, not a fixed-fee subscription

        delta_pct = (latest - prev_median) / prev_median
        if delta_pct >= threshold:
            nudges.append({
                "type": "price_change",
                # Keyed on the charge that moved, so the next hike re-alerts.
                "key": f"price_change:{merchant}:{dates[-1].isoformat()}",
                "severity": "warning",
                "title": f"Price increase: {merchant}",
                "body": (
                    f"{merchant}'s last charge was {_usd(latest)}, "
                    f"up {delta_pct * 100:.0f}% from your typical {_usd(prev_median)}."
                ),
                "data": {
                    "merchant": merchant,
                    "latest_charge": round(latest, 2),
                    "prev_median": round(prev_median, 2),
                    "delta_pct": round(delta_pct * 100, 1),
                    "latest_date": grp.iloc[-1]["date"].strftime("%Y-%m-%d"),
                },
            })

    return nudges


# ── Detector 5: Unusually large transaction ───────────────────────────────────

def detect_large_transactions(
    spending: pd.DataFrame,
    multiplier: float = 2.5,
    min_amount: float = 40.0,
) -> list[dict]:
    """
    Within the past 30 days, flag any transaction that is >= 2.5× the merchant's
    median charge AND >= $40. Requires at least 2 prior charges to establish a baseline.
    """
    nudges: list[dict] = []
    today = datetime.date.today()
    window_start = today - datetime.timedelta(days=30)

    # Need full history for baseline, but only flag recent transactions
    for merchant, grp in spending.groupby("merchant_normalized"):
        if not merchant or len(grp) < 3:
            continue

        grp = grp.sort_values("date")
        amounts = grp["amount"].values
        median = float(np.median(amounts[:-1]))  # exclude latest for baseline
        if median < 5:
            continue

        recent = grp[grp["date"].dt.date >= window_start]
        for _, row in recent.iterrows():
            amt = float(row["amount"])
            if amt >= multiplier * median and amt >= min_amount:
                nudges.append({
                    "type": "large_transaction",
                    # Keyed on the charge itself — one alert per unusual charge.
                    "key": f"large_transaction:{row.get('id')}",
                    "severity": "warning",
                    "title": f"Unusual charge at {merchant}",
                    "body": (
                        f"A {_usd(amt)} charge from {merchant} on "
                        f"{row['date'].strftime('%b %d')} is "
                        f"{amt / median:.1f}× your typical {_usd(median)} there."
                    ),
                    "data": {
                        "merchant": merchant,
                        "amount": round(amt, 2),
                        "typical": round(median, 2),
                        "multiplier": round(amt / median, 1),
                        "date": row["date"].strftime("%Y-%m-%d"),
                        "transaction_name": str(row.get("name", "")),
                    },
                })

    return nudges


# ── Pipeline entry point ──────────────────────────────────────────────────────

def run_analysis(user_id: int) -> int:
    """
    Run all detectors for a user and persist new nudges to the database.
    Clears nudges older than 7 days before generating fresh ones.
    Returns the number of nudges generated.
    """
    from core.db import clear_stale_nudges, save_nudge

    try:
        df = load_data(user_id)
        if df.empty:
            return 0

        spending = get_spending(df)

        log.info(
            "analysis: %d spending rows, %s – %s",
            len(spending),
            spending["date"].min().date() if not spending.empty else "N/A",
            spending["date"].max().date() if not spending.empty else "N/A",
        )

        # Reap anything the detectors stopped producing a week ago. Runs before
        # the fresh pass so a nudge regenerated below keeps its dismissal.
        clear_stale_nudges(user_id, days=7)

        all_nudges: list[dict[str, Any]] = []
        all_nudges.extend(detect_monthly_pace(spending))
        all_nudges.extend(detect_category_spikes(spending))
        all_nudges.extend(detect_recurring_subs(spending))
        all_nudges.extend(detect_price_changes(spending))
        all_nudges.extend(detect_large_transactions(spending))

        log.info(
            "analysis: pace=%d spikes=%d recurring=%d price=%d large=%d -> %d total for user %d",
            len([n for n in all_nudges if n["type"] == "monthly_pace"]),
            len([n for n in all_nudges if n["type"] == "category_spike"]),
            len([n for n in all_nudges if n["type"] == "new_recurring"]),
            len([n for n in all_nudges if n["type"] == "price_change"]),
            len([n for n in all_nudges if n["type"] == "large_transaction"]),
            len(all_nudges),
            user_id,
        )

        for n in all_nudges:
            save_nudge(
                user_id=user_id,
                nudge_type=n["type"],
                severity=n["severity"],
                title=n["title"],
                body=n["body"],
                data=n["data"],
                dedup_key=n["key"],
            )

        return len(all_nudges)

    except Exception:
        log.exception("analysis pipeline failed for user %d", user_id)
        return 0
