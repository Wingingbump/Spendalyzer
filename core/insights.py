import calendar
import threading
import pandas as pd
from datetime import date
from dateutil.relativedelta import relativedelta
import numpy as np
from cachetools import TTLCache
from core.categorize import apply_categories
from core.dedup import apply_dedup, get_clean_spending, flag_potential_duplicates
from core.db import fetch_transactions, get_merchant_overrides, get_merchant_category_overrides, get_dismissed_duplicate_pairs, get_transfer_overrides
from core import ai_budget


# ── Per-user DataFrame cache ───────────────────────────────────────────────────
# TTL is a safety net; mutations explicitly call invalidate_user_cache().
_df_cache: TTLCache = TTLCache(maxsize=256, ttl=300)
_df_cache_lock = threading.Lock()
# Per-user build locks coalesce concurrent cold-cache builds. Without this, the
# 6-8 parallel queries the Overview page fires on first load all run the full
# DataFrame build in parallel, which on a slow host produces gateway timeouts.
_user_build_locks: dict[int, threading.Lock] = {}
_user_build_locks_meta = threading.Lock()


def _get_build_lock(user_id: int) -> threading.Lock:
    with _user_build_locks_meta:
        lock = _user_build_locks.get(user_id)
        if lock is None:
            lock = threading.Lock()
            _user_build_locks[user_id] = lock
        return lock


def invalidate_user_cache(user_id: int) -> None:
    with _df_cache_lock:
        _df_cache.pop(user_id, None)


# ── Data loading ───────────────────────────────────────────────────────────────

def load_data(user_id: int) -> pd.DataFrame:
    with _df_cache_lock:
        cached = _df_cache.get(user_id)
    if cached is not None:
        return cached

    # Coalesce concurrent builds: only one request per user actually rebuilds;
    # the rest wait, then re-read from the now-warm cache.
    with _get_build_lock(user_id):
        with _df_cache_lock:
            cached = _df_cache.get(user_id)
        if cached is not None:
            return cached
        return _build_df(user_id)


def _build_df(user_id: int) -> pd.DataFrame:
    # Bound the AI calls this rebuild may make. apply_categories, apply_dedup and
    # categorize_reimbursements each consult Claude for work they can't resolve
    # from cache; on a cold cache over a long history that is hundreds of
    # sequential calls, and every other request for this user is waiting on the
    # build lock meanwhile. Anything over budget takes its existing fallback and
    # is left uncached, so the next rebuild continues where this one stopped.
    with ai_budget.budget():
        return _build_df_inner(user_id)


def _build_df_inner(user_id: int) -> pd.DataFrame:
    rows = fetch_transactions(user_id)
    if not rows:
        df = pd.DataFrame({
            "id": pd.Series(dtype=str),
            "date": pd.Series(dtype="datetime64[ns]"),
            "name": pd.Series(dtype=str),
            "amount": pd.Series(dtype=float),
            "category": pd.Series(dtype=str),
            "pending": pd.Series(dtype=bool),
            "institution": pd.Series(dtype=str),
            "plaid_account_id": pd.Series(dtype=str),
            "pfc_primary": pd.Series(dtype=str),
            "pfc_detailed": pd.Series(dtype=str),
            "pfc_confidence": pd.Series(dtype=str),
            "payment_channel": pd.Series(dtype=str),
            "plaid_merchant_name": pd.Series(dtype=str),
            "counterparties": pd.Series(dtype=str),
            "account_name": pd.Series(dtype=str),
            "account_mask": pd.Series(dtype=str),
            "account_subtype": pd.Series(dtype=str),
            "notes": pd.Series(dtype=str),
            "type": pd.Series(dtype=str),
            "is_transfer": pd.Series(dtype=bool),
            "is_duplicate": pd.Series(dtype=bool),
            "is_potential_duplicate": pd.Series(dtype=bool),
            "potential_dup_of": pd.Series(dtype=str),
            "merchant_normalized": pd.Series(dtype=str),
            "dedup_reason": pd.Series(dtype=str),
            "has_user_override": pd.Series(dtype=bool),
            "is_reimbursement": pd.Series(dtype=bool),
            "needs_review": pd.Series(dtype=bool),
            "is_excluded": pd.Series(dtype=bool),
        })
        return df
    df = pd.DataFrame(rows)
    df["date"] = pd.to_datetime(df["date"])
    df["amount"] = pd.to_numeric(df["amount"], errors="coerce").astype(float)
    df["pending"] = df["pending"].astype(bool)
    df["type"] = df["amount"].apply(lambda x: "credit" if x < 0 else "debit")

    # Protect only real user overrides (rows with an entry in the overrides table)
    df["has_user_override"] = df["has_user_override"].astype(bool)
    if "is_excluded" in df.columns:
        df["is_excluded"] = df["is_excluded"].astype(bool)
    df["override_category"] = df["category"].where(df["has_user_override"])

    df = apply_categories(df)

    # Apply merchant-level category overrides (wins over Plaid, but not per-tx overrides)
    merchant_cat_overrides = get_merchant_category_overrides(user_id)
    if merchant_cat_overrides:
        for merchant, cat in merchant_cat_overrides.items():
            mco_mask = (df["merchant_normalized"] == merchant) & ~df["has_user_override"]
            df.loc[mco_mask, "category"] = cat

    # Restore user override categories — user edits always win
    mask = df["has_user_override"]
    df.loc[mask, "category"] = df.loc[mask, "override_category"]
    df = df.drop(columns=["override_category"])

    df = apply_dedup(df)

    dismissed = get_dismissed_duplicate_pairs(user_id)
    df = flag_potential_duplicates(df, dismissed)

    # Classify reimbursements (P2P money-back + merchant refunds) vs income, then
    # categorize P2P reimbursements from their memo so they net against the right
    # category. Unresolved ones become Uncategorized + needs_review.
    # Keep has_user_override available so a user-set category is never overwritten.
    from core.reimbursements import flag_reimbursements, categorize_reimbursements
    df = flag_reimbursements(df)
    df = categorize_reimbursements(df, user_id)

    # Apply per-user merchant display-name overrides. Two passes, in precedence
    # order, both reading the same flat {key -> display} map:
    #   1. Group-level — key is the clustered merchant name. Renames every raw
    #      descriptor that resolves to that name (e.g. all "Starbucks" variants).
    #   2. Descriptor-level — key is the exact raw transaction descriptor. Applied
    #      last so it WINS, letting a single descriptor be split out of a group it
    #      was wrongly merged into (two payees that normalized to the same name).
    overrides = get_merchant_overrides(user_id)
    if overrides:
        df["merchant_normalized"] = df["merchant_normalized"].map(
            lambda v: overrides.get(v, v)
        )
        df["merchant_normalized"] = [
            overrides.get(name, norm)
            for name, norm in zip(df["name"], df["merchant_normalized"])
        ]

    # Apply per-counterparty transfer rulings the user has taught us. Runs last so
    # it keys on the final displayed merchant name — the same value the correction
    # endpoint reads — and always wins over auto-detection.
    df = apply_transfer_overrides(df, get_transfer_overrides(user_id))

    with _df_cache_lock:
        _df_cache[user_id] = df
    return df

def apply_transfer_overrides(df: pd.DataFrame, overrides: dict) -> pd.DataFrame:
    """Force `is_transfer` for counterparties the user has ruled on.

    `overrides` maps merchant_normalized -> bool. Forcing a row to a transfer also
    clears any reimbursement/review flags, since a transfer is neither spending,
    income, nor a reimbursement. A user's per-transaction category edit is on a
    different column and is untouched.
    """
    if not overrides or df.empty or "merchant_normalized" not in df.columns:
        return df
    forced = df["merchant_normalized"].map(overrides)
    has_ruling = forced.notna()
    if not has_ruling.any():
        return df
    df.loc[has_ruling, "is_transfer"] = forced[has_ruling].astype(bool)
    now_transfer = has_ruling & df["is_transfer"].fillna(False)
    df.loc[now_transfer, "dedup_reason"] = "transfer (user)"
    if "is_reimbursement" in df.columns:
        df.loc[now_transfer, "is_reimbursement"] = False
    if "needs_review" in df.columns:
        df.loc[now_transfer, "needs_review"] = False
    return df


# ── Filtering ──────────────────────────────────────────────────────────────────

def filter_by_range(df: pd.DataFrame, days: int = None) -> pd.DataFrame:
    if days is None:
        return df.copy()
    cutoff = pd.Timestamp.today() - pd.Timedelta(days=days)
    return df[df["date"] >= cutoff].copy()


def filter_by_month(df: pd.DataFrame, year: int, month: int) -> pd.DataFrame:
    return df[
        (df["date"].dt.year == year) &
        (df["date"].dt.month == month)
    ].copy()


def filter_by_institution(df: pd.DataFrame, institution: str) -> pd.DataFrame:
    return df[df["institution"].str.lower() == institution.lower()].copy()


def filter_by_account(df: pd.DataFrame, plaid_account_id: str) -> pd.DataFrame:
    return df[df["plaid_account_id"] == plaid_account_id].copy()


# ── Base spending/credits ──────────────────────────────────────────────────────

def _reimbursement_mask(df: pd.DataFrame) -> pd.Series:
    if "is_reimbursement" in df.columns:
        return df["is_reimbursement"].fillna(False)
    return pd.Series(False, index=df.index)


def _excluded_mask(df: pd.DataFrame) -> pd.Series:
    """Rows the user flagged 'exclude from reports' — dropped from all spending/
    income analytics (the monthly Overview) but kept in the raw ledger list."""
    if "is_excluded" in df.columns:
        return df["is_excluded"].fillna(False)
    return pd.Series(False, index=df.index)


def get_spending(df: pd.DataFrame) -> pd.DataFrame:
    """Clean debits PLUS reimbursements. Reimbursements are money-back (negative
    amounts) that net against their category. Transfers, duplicates, and true
    income are excluded."""
    if df.empty:
        return df.copy()
    reimb = _reimbursement_mask(df)
    mask = (
        (~df["is_transfer"].fillna(False))
        & (~df["is_duplicate"].fillna(False))
        & ((df["type"] == "debit") | reimb)
        & (~_excluded_mask(df))
    )
    return df[mask].copy()


def get_credits(df: pd.DataFrame) -> pd.DataFrame:
    """All clean credits — transfers and duplicates excluded (income + reimbursements)."""
    return df[
        (df["type"] == "credit") &
        (~df["is_transfer"].fillna(False)) &
        (~df["is_duplicate"].fillna(False)) &
        (~_excluded_mask(df))
    ].copy()


def get_income(df: pd.DataFrame) -> pd.DataFrame:
    """True income only — clean credits that are NOT reimbursements (paycheck,
    interest, tax refunds). Kept separate so it never dilutes spending."""
    if df.empty:
        return df.copy()
    reimb = _reimbursement_mask(df)
    mask = (
        (df["type"] == "credit")
        & (~df["is_transfer"].fillna(False))
        & (~df["is_duplicate"].fillna(False))
        & (~reimb)
        & (~_excluded_mask(df))
    )
    return df[mask].copy()


def get_reimbursements(df: pd.DataFrame) -> pd.DataFrame:
    """Clean reimbursements only (money back for a spend)."""
    if df.empty:
        return df.copy()
    return df[_reimbursement_mask(df) & (~_excluded_mask(df))].copy()


# ── Grouped aggregation ────────────────────────────────────────────────────────

def _net_by(spending: pd.DataFrame, key: str, *, drop_empty: bool = True) -> pd.DataFrame:
    """Group clean spending by `key` into net totals and purchase counts.

    `spending` is get_spending() output, which carries reimbursements as negative
    rows so they net against whatever they paid back. That makes two corrections
    necessary in every breakdown, so they live here rather than in each one:

      - `total` is floored at zero. Over-collecting on a group expense (you front
        $900 for a trip and get paid back) would otherwise net to a *negative*
        spend, which sorts to the bottom of a descending top-N list and renders as
        a bar below the axis.
      - `count` counts debits only, so a payback is never counted as a purchase.

    `drop_empty` removes groups that net to nothing. A time series passes False to
    keep the period on the axis as a zero instead of punching a hole in it.
    """
    debits = spending[spending["type"] == "debit"]
    net = spending.groupby(key)["amount"].sum().clip(lower=0).round(2)
    counts = debits.groupby(key)["amount"].count()
    result = net.rename("total").reset_index()
    result["count"] = result[key].map(counts).fillna(0).astype(int)
    if drop_empty:
        result = result[result["total"] > 0]
    return result.reset_index(drop=True)


# ── Totals ─────────────────────────────────────────────────────────────────────

def total_gross_spent(df: pd.DataFrame) -> float:
    """Gross spend — clean debits only, BEFORE reimbursements are subtracted.
    Reimbursements (money people paid back) never inflate this."""
    s = get_spending(df)
    if s.empty:
        return 0.0
    return round(s[s["type"] == "debit"]["amount"].sum(), 2)


def total_spent(df: pd.DataFrame) -> float:
    """Net spend: gross debits minus reimbursements."""
    return round(get_spending(df)["amount"].sum(), 2)


def total_income(df: pd.DataFrame) -> float:
    """True income only (excludes reimbursements)."""
    return round(get_income(df)["amount"].abs().sum(), 2)


def total_reimbursements(df: pd.DataFrame) -> float:
    return round(get_reimbursements(df)["amount"].abs().sum(), 2)


def total_credits(df: pd.DataFrame) -> float:
    """All clean credits (income + reimbursements). Retained for compatibility."""
    return round(get_credits(df)["amount"].abs().sum(), 2)


def net_spend(df: pd.DataFrame) -> float:
    """Net spend after income — positive means spent more than earned."""
    return round(total_spent(df) - total_income(df), 2)


def transaction_count(df: pd.DataFrame) -> int:
    """Number of purchases. Reimbursement credits ride along in get_spending() so
    they can net against a category, but they aren't transactions the user made."""
    spending = get_spending(df)
    if spending.empty:
        return 0
    return int((spending["type"] == "debit").sum())


# ── Current-month figures ──────────────────────────────────────────────────────
# One implementation each, because these were previously recomputed per caller and
# drifted: the Overview hero said "on pace for $191.00" while the Tracker strip
# said $191.24 for the same month, and two separate budget queries each summed
# category spend without a zero floor, surfacing "-$0.21 spent of $600.00".

def month_to_date(df: pd.DataFrame, today: date | None = None) -> dict:
    """Current-month spend, the month-end projection, and the same-point
    comparison against last month.

    `pace` extrapolates the month-to-date total across the full month. It is only
    meaningful once a few days have elapsed — on the 1st it multiplies a single
    day by 31 — so callers should lean on the rolling-30-day figures early in the
    month. Returned rounded to cents exactly once, here, so no caller re-rounds
    to a different precision.

    `prior_month_to_date` is last month's spend over the *same stretch of days*
    (the 1st through today's day-of-month). Comparing a part-month against a
    whole one is the single most misleading thing a spend dashboard can do — on
    the 10th it reports a 97% drop that is really just 20 unelapsed days — so the
    honest comparison is computed here rather than left to each caller.
    """
    today = today or date.today()
    days_in_month = calendar.monthrange(today.year, today.month)[1]
    days_elapsed = today.day
    spent = total_spent(filter_by_month(df, today.year, today.month))
    daily_rate = (spent / days_elapsed) if days_elapsed else 0.0

    # Same stretch of last month. Clamped when last month is shorter, so
    # March 31 compares against all of February rather than silently dropping
    # the tail — the label reports the day count actually used.
    prev_year, prev_month = (
        (today.year - 1, 12) if today.month == 1 else (today.year, today.month - 1)
    )
    prior_days = min(days_elapsed, calendar.monthrange(prev_year, prev_month)[1])
    prior_df = filter_by_month(df, prev_year, prev_month)
    if not prior_df.empty:
        prior_df = prior_df[prior_df["date"].dt.day <= prior_days]
    prior_mtd = total_spent(prior_df)

    delta = round(spent - prior_mtd, 2)
    return {
        "spent": spent,
        "daily_rate": round(daily_rate, 2),
        "pace": round(daily_rate * days_in_month, 2),
        "days_elapsed": days_elapsed,
        "days_in_month": days_in_month,
        "days_left": max(0, days_in_month - days_elapsed),
        "prior_month_to_date": prior_mtd,
        "prior_month_to_date_days": prior_days,
        "delta_vs_prior_mtd": delta,
        "delta_vs_prior_mtd_pct": (
            round(delta / prior_mtd * 100, 1) if prior_mtd > 0 else 0.0
        ),
    }


def category_spend_for_month(df: pd.DataFrame, today: date | None = None) -> dict[str, float]:
    """Net spend per category for the current calendar month, keyed by category.

    Routed through _net_by() so budgets inherit the same zero floor as every other
    breakdown: a category whose reimbursements exceed its spend reads as 0, not as
    a negative amount that renders as "-$0.21 spent" and a negative progress bar.
    """
    today = today or date.today()
    month_df = filter_by_month(df, today.year, today.month)
    spending = get_spending(month_df)
    if spending.empty or "category" not in spending.columns:
        return {}
    rows = _net_by(spending, "category", drop_empty=False)
    return {r["category"]: float(r["total"]) for _, r in rows.iterrows()}


# ── Month over month ───────────────────────────────────────────────────────────

def this_month_vs_last(df: pd.DataFrame) -> dict:
    today = date.today()
    this_month = filter_by_month(df, today.year, today.month)
    last = today - relativedelta(months=1)
    last_month = filter_by_month(df, last.year, last.month)

    this_total = total_spent(this_month)
    last_total = total_spent(last_month)
    delta = round(this_total - last_total, 2)
    pct = round((delta / last_total * 100), 1) if last_total > 0 else 0.0

    return clean_dict({
        "this_month": this_total,
        "last_month": last_total,
        "delta": delta,
        "delta_pct": pct,
        "trending_up": delta > 0
    })


def spending_by_month(df: pd.DataFrame) -> pd.DataFrame:
    """Net spend per calendar month. Months that net to zero are kept so the
    series stays continuous across the chart's x-axis."""
    spending = get_spending(df).copy()
    if spending.empty:
        return pd.DataFrame(columns=["month", "total", "count"])
    spending["month"] = spending["date"].dt.to_period("M")
    result = (
        _net_by(spending, "month", drop_empty=False)
        .sort_values("month")
        .reset_index(drop=True)
    )
    result["month"] = result["month"].astype(str)
    return result


# ── Category breakdown ─────────────────────────────────────────────────────────

def spending_by_category(df: pd.DataFrame) -> pd.DataFrame:
    """Net spend per category — debits minus the reimbursements filed to that
    category — so each row reflects what you actually paid after paybacks. Floored
    See _net_by() for the zero-floor and purchase-count rules; the excess from an
    over-collected category still shows in the top-level breakdown. Percentages are
    of net category spend."""
    spending = get_spending(df)
    if spending.empty:
        return pd.DataFrame(columns=["category", "total", "count", "pct"])
    result = (
        _net_by(spending, "category")
        .sort_values("total", ascending=False)
        .reset_index(drop=True)
    )
    total = result["total"].sum()
    result["pct"] = (result["total"] / total * 100).round(1) if total else 0.0
    return result


def drill_down_category(df: pd.DataFrame, category: str) -> pd.DataFrame:
    """All transactions for a given category sorted by date desc."""
    spending = get_spending(df)
    return (
        spending[spending["category"] == category]
        [["date", "name", "merchant_normalized", "amount", "institution"]]
        .sort_values("date", ascending=False)
        .reset_index(drop=True)
    )


# ── Merchant breakdown ─────────────────────────────────────────────────────────

def spending_by_merchant(df: pd.DataFrame, top_n: int = 10) -> pd.DataFrame:
    """Top merchants by net spend. See _net_by() for the zero-floor and
    purchase-count rules."""
    spending = get_spending(df)
    if spending.empty:
        return pd.DataFrame(columns=["merchant_normalized", "total", "count"])
    return (
        _net_by(spending, "merchant_normalized")
        .sort_values("total", ascending=False)
        .head(top_n)
        .reset_index(drop=True)
    )


def drill_down_merchant(df: pd.DataFrame, merchant: str) -> pd.DataFrame:
    """All transactions for a given merchant sorted by date desc."""
    spending = get_spending(df)
    return (
        spending[spending["merchant_normalized"] == merchant]
        [["date", "name", "amount", "category", "institution"]]
        .sort_values("date", ascending=False)
        .reset_index(drop=True)
    )


# ── Day of week ────────────────────────────────────────────────────────────────

_DOW_ORDER = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]

# A median needs at least this many weeks of history to describe a "typical" day.
_DOW_MIN_WEEKS = 2


def spending_by_dow(df: pd.DataFrame) -> pd.DataFrame:
    """
    Returns the MEDIAN spend per weekday across all weeks in the dataset.

    Aggregate totals skew badly — one expensive Friday inflates the whole bar.
    Instead: sum all transactions within each (year, week, weekday) pair to get
    a single 'spend on this weekday in this week', then take the median across
    all such weeks. This gives a reliable 'typical Monday costs me $X' figure.
    Weekdays with fewer than 2 observations are zeroed out as unreliable.

    Below _DOW_MIN_WEEKS of history no weekday can clear that bar, which would
    zero out the entire chart for a brand-new account. In that case fall back to
    reporting actual spend per weekday — less meaningful than a median, but it is
    the data the user actually has, and an all-zero chart reads as "broken".
    """
    spending = get_spending(df).copy()
    if spending.empty:
        return pd.DataFrame({
            "dow": _DOW_ORDER,
            "total": [0.0] * 7,
            "count": [0] * 7,
        })

    spending["dow"] = spending["date"].dt.day_name()
    iso = spending["date"].dt.isocalendar()
    spending["iso_year"] = iso["year"].astype(int)
    spending["iso_week"] = iso["week"].astype(int)

    # Sum per (year, week, weekday) → one value per weekday-occurrence
    weekly = (
        spending.groupby(["iso_year", "iso_week", "dow"])["amount"]
        .sum()
        .reset_index()
    )

    weeks_covered = len(weekly[["iso_year", "iso_week"]].drop_duplicates())

    if weeks_covered < _DOW_MIN_WEEKS:
        # Thin history — report what was actually spent on each weekday.
        result = (
            weekly.groupby("dow")["amount"]
            .agg(total="sum", count="count")
            .reindex(_DOW_ORDER)
            .fillna(0)
            .reset_index()
        )
        result["count"] = result["count"].astype(int)
        result["total"] = result["total"].round(2)
        return result

    # Median and count across all occurrences of that weekday
    result = (
        weekly.groupby("dow")["amount"]
        .agg(total="median", count="count")
        .reindex(_DOW_ORDER)
        .fillna(0)
        .reset_index()
    )

    # Zero out weekdays with too few observations to be reliable
    result.loc[result["count"] < 2, "total"] = 0
    result["count"] = result["count"].astype(int)
    result["total"] = result["total"].round(2)
    return result


# ── Highlights ─────────────────────────────────────────────────────────────────

def biggest_purchase(df: pd.DataFrame) -> dict | None:
    spending = get_spending(df)
    if spending.empty:
        return None
    row = spending.loc[spending["amount"].idxmax()]
    return clean_dict({
        "name": row["name"],
        "merchant": row["merchant_normalized"],
        "amount": round(row["amount"], 2),
        "date": row["date"].strftime("%b %d, %Y"),
        "category": row["category"],
        "institution": row["institution"]
    })


def most_visited_merchant(df: pd.DataFrame) -> dict | None:
    """The merchant with the most purchases, and the net spend there. Ranked on
    debits only — being paid back by a friend isn't a visit."""
    spending = get_spending(df)
    if spending.empty:
        return None
    by_merchant = _net_by(spending, "merchant_normalized", drop_empty=False)
    by_merchant = by_merchant[by_merchant["count"] > 0]
    if by_merchant.empty:
        return None
    row = by_merchant.loc[by_merchant["count"].idxmax()]
    return clean_dict({
        "merchant": row["merchant_normalized"],
        "count": int(row["count"]),
        "total": float(row["total"]),
    })


def biggest_spending_day(df: pd.DataFrame) -> dict | None:
    spending = get_spending(df)
    if spending.empty:
        return None
    by_day = spending.groupby("date")["amount"].sum()
    day = by_day.idxmax()
    return clean_dict({
        "date": day.strftime("%b %d, %Y"),
        "total": round(by_day.max(), 2)
    })


# ── Institution breakdown ──────────────────────────────────────────────────────

def spending_by_institution(df: pd.DataFrame) -> pd.DataFrame:
    """Net spend per institution. See _net_by() for the zero-floor and
    purchase-count rules."""
    spending = get_spending(df)
    if spending.empty:
        return pd.DataFrame(columns=["institution", "total", "count"])
    return (
        _net_by(spending, "institution")
        .sort_values("total", ascending=False)
        .reset_index(drop=True)
    )


# ── Full transaction table ─────────────────────────────────────────────────────

def transaction_table(df: pd.DataFrame) -> pd.DataFrame:
    spending = get_spending(df)
    return (
        spending[["id", "date", "name", "merchant_normalized",
                  "category", "amount", "institution", "pending", "notes"]]
        .sort_values("date", ascending=False)
        .reset_index(drop=True)
        .rename(columns={
            "id": "ID",
            "name": "Raw Name",
            "merchant_normalized": "Merchant",
            "category": "Category",
            "amount": "Amount",
            "date": "Date",
            "institution": "Institution",
            "pending": "Pending",
            "notes": "Notes"
        })
    )

def clean_dict(d: dict) -> dict:
    """Converts numpy types to native Python for safe Streamlit rendering."""
    return {
        k: (float(v) if isinstance(v, np.floating) else
            bool(v) if isinstance(v, np.bool_) else
            int(v) if isinstance(v, np.integer) else v)
        for k, v in d.items()
    }

def full_ledger(df: pd.DataFrame) -> pd.DataFrame:
    """Every transaction — spending, income, transfers, duplicates. Full picture."""
    data = df
    return (
        data[["id", "date", "name", "merchant_normalized",
              "category", "amount", "institution", "pending",
              "type", "is_transfer", "is_duplicate", "notes"]]
        .sort_values("date", ascending=False)
        .reset_index(drop=True)
        .rename(columns={
            "id": "ID",
            "name": "Raw Name",
            "merchant_normalized": "Merchant",
            "category": "Category",
            "amount": "Amount",
            "date": "Date",
            "institution": "Institution",
            "pending": "Pending",
            "type": "Type",
            "is_transfer": "Transfer",
            "is_duplicate": "Duplicate",
            "notes": "Notes"
        })
    )