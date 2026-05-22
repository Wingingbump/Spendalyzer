from typing import Optional

import pandas as pd
from fastapi import APIRouter, Depends, Query, Path

from backend.dependencies import apply_filters, get_current_user
from core import insights as ins
from core.db import get_active_nudges, dismiss_nudge, mark_nudges_read, get_unread_nudge_count

router = APIRouter(prefix="/insights", tags=["insights"])


def _safe(val):
    """Convert numpy scalars / NaN to plain Python types safe for JSON."""
    import math
    import numpy as np
    if val is None:
        return None
    if isinstance(val, float) and math.isnan(val):
        return None
    if isinstance(val, np.floating):
        return float(val)
    if isinstance(val, np.integer):
        return int(val)
    if isinstance(val, np.bool_):
        return bool(val)
    return val


def _df_to_records(df: pd.DataFrame) -> list:
    """Convert DataFrame to list of dicts, replacing NaN/NaT with None."""
    cleaned = df.where(pd.notna(df), None)
    records = cleaned.to_dict(orient="records")
    for row in records:
        for k, v in row.items():
            if hasattr(v, "isoformat"):
                row[k] = v.isoformat()
    return records


@router.get("/summary")
def summary(
    range: str = Query("30d"),
    institution: str = Query("all"),
    account: str = Query("all"),
    current_user: dict = Depends(get_current_user),
):
    df = ins.load_data(current_user["id"])
    df = apply_filters(df, range, institution, account)

    mom = ins.this_month_vs_last(df)
    bp = ins.biggest_purchase(df)
    mvm = ins.most_visited_merchant(df)
    bsd = ins.biggest_spending_day(df)

    return {
        "total_spent": _safe(ins.total_spent(df)),
        "total_credits": _safe(ins.total_credits(df)),
        "transaction_count": _safe(ins.transaction_count(df)),
        "net_spend": _safe(ins.net_spend(df)),
        "this_month": _safe(mom.get("this_month")),
        "last_month": _safe(mom.get("last_month")),
        "delta": _safe(mom.get("delta")),
        "delta_pct": _safe(mom.get("delta_pct")),
        "biggest_purchase": bp,
        "most_visited_merchant": mvm,
        "biggest_spending_day": bsd,
    }


@router.get("/monthly")
def monthly(
    range: str = Query("30d"),
    institution: str = Query("all"),
    account: str = Query("all"),
    category: str = Query("all"),
    merchant_query: str = Query(""),
    current_user: dict = Depends(get_current_user),
):
    df = ins.load_data(current_user["id"])
    df = apply_filters(df, range, institution, account, category, merchant_query)
    result = ins.spending_by_month(df)
    return _df_to_records(result)


@router.get("/categories")
def categories(
    range: str = Query("30d"),
    institution: str = Query("all"),
    account: str = Query("all"),
    merchant_query: str = Query(""),
    current_user: dict = Depends(get_current_user),
):
    df = ins.load_data(current_user["id"])
    # No category filter here — would empty the result. Merchant filter is fine.
    df = apply_filters(df, range, institution, account, "all", merchant_query)
    result = ins.spending_by_category(df)
    return _df_to_records(result)


@router.get("/dow")
def dow(
    range: str = Query("30d"),
    institution: str = Query("all"),
    account: str = Query("all"),
    category: str = Query("all"),
    merchant_query: str = Query(""),
    current_user: dict = Depends(get_current_user),
):
    df = ins.load_data(current_user["id"])
    df = apply_filters(df, range, institution, account, category, merchant_query)
    result = ins.spending_by_dow(df)
    # Rename 'dow' column to 'day' if present
    if "dow" in result.columns:
        result = result.rename(columns={"dow": "day"})
    return _df_to_records(result)


@router.get("/institutions")
def institutions(
    current_user: dict = Depends(get_current_user),
):
    df = ins.load_data(current_user["id"])
    if df.empty or "institution" not in df.columns:
        return []
    names = df["institution"].dropna().unique().tolist()
    return sorted(names)


@router.get("/health")
def health(current_user: dict = Depends(get_current_user)):
    """
    Data health check — returns staleness warnings and missing recurring transactions.
    Runs against the full unfiltered transaction history.
    """
    import datetime, math
    from core.db import get_item_health

    user_id = current_user["id"]
    df = ins.load_data(user_id)
    today = datetime.date.today()
    warnings = []

    # ── 1. Plaid item connection status ──────────────────────────────────────
    # Authoritative: uses cached /item/get results stored during each sync.
    # Only populated after at least one sync has run.
    item_health_rows = get_item_health(user_id)
    warned_institutions = set()  # track which institutions already have a connection warning

    for row in item_health_rows:
        name = row["institution_name"]
        error_code = row.get("error_code")
        consent_exp = row.get("consent_expiration_time")
        last_ok = row.get("last_successful_update")
        last_fail = row.get("last_failed_update")

        # Active connection error (ITEM_LOGIN_REQUIRED, ITEM_LOCKED, etc.)
        if error_code:
            warnings.append({
                "type": "item_error",
                "severity": "error",
                "institution": name,
                "error_code": error_code,
                "message": f"{name} · connection error ({error_code}) — reconnect required",
            })
            warned_institutions.add(name)
            continue

        # Sync failure: last_failed_update is more recent than last_successful_update
        if last_fail and last_ok and last_fail > last_ok:
            warnings.append({
                "type": "sync_failure",
                "severity": "error",
                "institution": name,
                "message": f"{name} · last sync attempt failed — try syncing again",
            })
            warned_institutions.add(name)
            continue

        # Consent expiring soon (within 7 days) or already expired
        if consent_exp:
            exp_date = consent_exp.date() if hasattr(consent_exp, "date") else consent_exp
            days_until_exp = (exp_date - today).days
            if days_until_exp <= 0:
                warnings.append({
                    "type": "consent_expired",
                    "severity": "error",
                    "institution": name,
                    "message": f"{name} · connection authorization expired — reconnect required",
                })
                warned_institutions.add(name)
            elif days_until_exp <= 7:
                warnings.append({
                    "type": "consent_expiring",
                    "severity": "warning",
                    "institution": name,
                    "days_until_exp": days_until_exp,
                    "message": f"{name} · connection expires in {days_until_exp} day(s) — reconnect soon",
                })
                warned_institutions.add(name)

    if df.empty:
        status = "ok" if not warnings else ("error" if any(w["severity"] == "error" for w in warnings) else "warning")
        return {"status": status, "warnings": warnings}

    # Work on a local copy — load_data() returns a cached DataFrame shared across
    # requests, so mutating df["date"] in place would poison every other endpoint.
    df = df.copy()
    df["date"] = pd.to_datetime(df["date"]).dt.date

    # ── 2. Transaction-based staleness (fallback / cross-check) ──────────────
    # Only flag institutions not already covered by a connection error above.
    # Threshold raised to 14 days; sparse accounts (< 3 txns/month) are skipped
    # because they're legitimately quiet and don't have a meaningful baseline.
    STALE_DAYS = 14
    STALE_ERROR_DAYS = 25
    MIN_TXNS_PER_MONTH = 3  # below this, account is considered sparse — skip

    if "institution" in df.columns:
        for institution, grp in df.groupby("institution"):
            if institution in warned_institutions:
                continue  # already have a more authoritative connection warning
            last_date = grp["date"].max()
            days_ago = (today - last_date).days

            # Skip sparse accounts — they're legitimately quiet
            span_days = max((today - grp["date"].min()).days, 1)
            txns_per_month = len(grp) / (span_days / 30)
            if txns_per_month < MIN_TXNS_PER_MONTH:
                continue

            if days_ago > STALE_DAYS:
                warnings.append({
                    "type": "stale_institution",
                    "severity": "warning" if days_ago < STALE_ERROR_DAYS else "error",
                    "institution": institution,
                    "last_seen": last_date.isoformat(),
                    "days_ago": days_ago,
                    "message": f"{institution} · last transaction {days_ago} days ago",
                })

    # ── 3. Stuck pending transactions ────────────────────────────────────────
    # Pending transactions older than 7 days indicate a settlement issue.
    STUCK_PENDING_DAYS = 7
    if "pending" in df.columns:
        pending_df = df[df["pending"].fillna(False)]
        if not pending_df.empty:
            stuck = pending_df[(today - pending_df["date"]).apply(lambda d: d.days) > STUCK_PENDING_DAYS]
            if len(stuck) > 0:
                oldest_days = int((today - stuck["date"].min()).days)
                warnings.append({
                    "type": "stuck_pending",
                    "severity": "warning",
                    "count": len(stuck),
                    "oldest_days": oldest_days,
                    "message": f"{len(stuck)} transaction(s) pending for {STUCK_PENDING_DAYS}+ days (oldest: {oldest_days}d)",
                })

    # ── 4. Transaction volume drop ────────────────────────────────────────────
    # Flag if the last 7 days' rate is < 40% of the prior 30-day rate,
    # but only for users whose accounts show meaningful regular activity (≥ 2/day avg).
    spend_df_all = df[df.get("is_transfer", pd.Series(False, index=df.index)).fillna(False) == False] \
        if "is_transfer" in df.columns else df
    last_30 = spend_df_all[spend_df_all["date"] >= today - datetime.timedelta(days=30)]
    last_7  = spend_df_all[spend_df_all["date"] >= today - datetime.timedelta(days=7)]
    per_day_30 = len(last_30) / 30
    per_day_7  = len(last_7) / 7

    if per_day_30 >= 2.0 and per_day_7 < per_day_30 * 0.4:
        warnings.append({
            "type": "volume_drop",
            "severity": "warning",
            "rate_30d": round(per_day_30, 1),
            "rate_7d": round(per_day_7, 1),
            "message": f"Transaction volume down — {per_day_7:.1f}/day this week vs {per_day_30:.1f}/day prior 30 days",
        })

    # ── 5. Missing recurring transactions ────────────────────────────────────
    # Only look at the last 6 months to avoid flagging cancelled subscriptions.
    # Requires 5+ occurrences in the recency window to establish a real pattern.
    # Flags if days since last > 2× median interval (raised from 1.5× to reduce noise).
    MIN_OCCURRENCES = 5
    CV_THRESHOLD = 0.4
    OVERDUE_FACTOR = 2.0
    RECENCY_DAYS = 180

    recency_cutoff = today - datetime.timedelta(days=RECENCY_DAYS)
    merchant_col = "merchant_normalized" if "merchant_normalized" in df.columns else "name"
    recent_spend = df[(df["amount"] > 0) & (df["date"] >= recency_cutoff)].copy()
    if "is_transfer" in recent_spend.columns:
        recent_spend = recent_spend[~recent_spend["is_transfer"].fillna(False)]

    for merchant, grp in recent_spend.groupby(merchant_col):
        if not merchant:
            continue
        dates = sorted(grp["date"].unique())
        if len(dates) < MIN_OCCURRENCES:
            continue

        intervals = [(dates[i] - dates[i - 1]).days for i in range(1, len(dates))]
        median_interval = sorted(intervals)[len(intervals) // 2]
        if median_interval < 5:  # skip daily/near-daily merchants
            continue

        mean_interval = sum(intervals) / len(intervals)
        if mean_interval == 0:
            continue
        std_interval = math.sqrt(sum((x - mean_interval) ** 2 for x in intervals) / len(intervals))
        cv = std_interval / mean_interval

        if cv > CV_THRESHOLD:
            continue

        days_since_last = (today - dates[-1]).days
        if days_since_last > median_interval * OVERDUE_FACTOR:
            cadence = (
                "weekly" if median_interval <= 9
                else "bi-weekly" if median_interval <= 18
                else "monthly" if median_interval <= 40
                else f"every ~{median_interval}d"
            )
            warnings.append({
                "type": "missing_recurring",
                "severity": "warning",
                "merchant": merchant,
                "cadence": cadence,
                "median_interval": median_interval,
                "last_seen": dates[-1].isoformat(),
                "days_since_last": days_since_last,
                "message": f"{merchant} · {cadence} charge — last seen {days_since_last} days ago",
            })

    status = "ok" if not warnings else ("error" if any(w["severity"] == "error" for w in warnings) else "warning")
    return {"status": status, "warnings": warnings}


@router.post("/analyze")
def analyze(current_user: dict = Depends(get_current_user)):
    """
    Run the analysis pipeline against existing transaction data in the DB.
    No Plaid call — just reruns detectors on what's already stored.
    Called automatically when the user opens the Overview page.
    """
    from core.analysis import run_analysis
    count = run_analysis(current_user["id"])
    return {"nudges_generated": count}


@router.get("/nudges")
def nudges(current_user: dict = Depends(get_current_user)):
    """Return all active (non-dismissed) nudges for the current user."""
    rows = get_active_nudges(current_user["id"])
    return [
        {
            "id": r["id"],
            "type": r["type"],
            "severity": r["severity"],
            "title": r["title"],
            "body": r["body"],
            "data": r["data"],
            "created_at": r["created_at"].isoformat() if r["created_at"] else None,
            "read": r["read_at"] is not None,
        }
        for r in rows
    ]


@router.get("/nudges/unread-count")
def nudges_unread_count(current_user: dict = Depends(get_current_user)):
    return {"count": get_unread_nudge_count(current_user["id"])}


@router.post("/nudges/read")
def nudges_mark_read(current_user: dict = Depends(get_current_user)):
    mark_nudges_read(current_user["id"])
    return {"ok": True}


@router.post("/nudges/{nudge_id}/dismiss")
def nudge_dismiss(
    nudge_id: int = Path(..., gt=0),
    current_user: dict = Depends(get_current_user),
):
    dismiss_nudge(nudge_id, current_user["id"])
    return {"ok": True}


@router.get("/accounts")
def accounts(
    current_user: dict = Depends(get_current_user),
):
    df = ins.load_data(current_user["id"])
    if df.empty:
        return []
    cols = [c for c in ["plaid_account_id", "account_name", "account_mask", "institution"] if c in df.columns]
    if not cols:
        return []
    sub = df[cols].drop_duplicates(
        subset=["plaid_account_id"] if "plaid_account_id" in cols else cols
    )
    sub = sub.rename(columns={"account_name": "name", "account_mask": "mask"})
    return _df_to_records(sub)
