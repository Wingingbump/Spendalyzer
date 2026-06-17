import threading
import pandas as pd
from datetime import date
from dateutil.relativedelta import relativedelta
import numpy as np
from cachetools import TTLCache
from core.categorize import apply_categories
from core.dedup import apply_dedup, get_clean_spending, flag_potential_duplicates
from core.db import fetch_transactions, get_merchant_overrides, get_merchant_category_overrides, get_dismissed_duplicate_pairs


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

    with _df_cache_lock:
        _df_cache[user_id] = df
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


# ── Totals ─────────────────────────────────────────────────────────────────────

def total_spent(df: pd.DataFrame) -> float:
    """Net spend: debits minus reimbursements."""
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
    return len(get_spending(df))


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
    spending = get_spending(df).copy()
    spending["month"] = spending["date"].dt.to_period("M")
    result = (
        spending.groupby("month")["amount"]
        .agg(total="sum", count="count")
        .reset_index()
        .sort_values("month")
    )
    result["month"] = result["month"].astype(str)
    result["total"] = result["total"].round(2)
    return result


# ── Category breakdown ─────────────────────────────────────────────────────────

def spending_by_category(df: pd.DataFrame) -> pd.DataFrame:
    spending = get_spending(df)
    if spending.empty:
        return pd.DataFrame(columns=["category", "total", "count", "pct"])
    total = spending["amount"].sum()
    result = (
        spending.groupby("category")["amount"]
        .agg(total="sum", count="count")
        .reset_index()
        .sort_values("total", ascending=False)
    )
    result["total"] = result["total"].round(2)
    result["pct"] = (result["total"] / total * 100).round(1)
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
    spending = get_spending(df)
    if spending.empty:
        return pd.DataFrame(columns=["merchant_normalized", "total", "count"])
    result = (
        spending.groupby("merchant_normalized")["amount"]
        .agg(total="sum", count="count")
        .reset_index()
        .sort_values("total", ascending=False)
        .head(top_n)
        .reset_index(drop=True)
    )
    result["total"] = result["total"].round(2)
    return result


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

def spending_by_dow(df: pd.DataFrame) -> pd.DataFrame:
    """
    Returns the MEDIAN spend per weekday across all weeks in the dataset.

    Aggregate totals skew badly — one expensive Friday inflates the whole bar.
    Instead: sum all transactions within each (year, week, weekday) pair to get
    a single 'spend on this weekday in this week', then take the median across
    all such weeks. This gives a reliable 'typical Monday costs me $X' figure.
    Weekdays with fewer than 2 observations are zeroed out.
    """
    spending = get_spending(df).copy()
    if spending.empty:
        return pd.DataFrame({
            "dow": ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"],
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

    # Median and count across all occurrences of that weekday
    result = (
        weekly.groupby("dow")["amount"]
        .agg(total="median", count="count")
        .reindex(["Monday", "Tuesday", "Wednesday",
                  "Thursday", "Friday", "Saturday", "Sunday"])
        .fillna(0)
        .reset_index()
    )

    # Zero out weekdays with too few observations to be reliable
    result.loc[result["count"] < 2, "total"] = 0
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
    spending = get_spending(df)
    if spending.empty:
        return None
    counts = spending["merchant_normalized"].value_counts()
    merchant = counts.idxmax()
    return clean_dict({
        "merchant": merchant,
        "count": int(counts.max()),
        "total": round(
            spending[spending["merchant_normalized"] == merchant]["amount"].sum(), 2
        )
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
    spending = get_spending(df)
    if spending.empty:
        return pd.DataFrame(columns=["institution", "total", "count"])
    result = (
        spending.groupby("institution")["amount"]
        .agg(total="sum", count="count")
        .reset_index()
        .sort_values("total", ascending=False)
    )
    result["total"] = result["total"].round(2)
    return result


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