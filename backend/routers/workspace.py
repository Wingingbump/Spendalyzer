import datetime
import json
from typing import Optional

import pandas as pd
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from backend.dependencies import get_current_user
from core import insights as ins
from core.db import get_user_conn

router = APIRouter(prefix="/workspace", tags=["workspace"])


# ── Helpers ───────────────────────────────────────────────────────────────────

def _df_to_records(df: pd.DataFrame) -> list:
    cleaned = df.where(pd.notna(df), None)
    records = cleaned.to_dict(orient="records")
    for row in records:
        for k, v in row.items():
            if hasattr(v, "isoformat"):
                row[k] = v.isoformat()
    return records


# ── Budget models ─────────────────────────────────────────────────────────────

class BudgetBody(BaseModel):
    amount: float
    period: str = "monthly"


# ── Budget endpoints ──────────────────────────────────────────────────────────

@router.get("/budgets")
def list_budgets(current_user: dict = Depends(get_current_user)):
    user_id = current_user["id"]

    with get_user_conn(user_id) as conn:
        rows = conn.execute(
            "SELECT id, category, amount, period FROM budgets WHERE user_id = %s ORDER BY category",
            (user_id,)
        ).fetchall()

    budgets = [dict(r) for r in rows]

    df = ins.load_data(user_id)
    cat_spend: dict = {}
    if not df.empty and "is_transfer" in df.columns:
        now = datetime.date.today()
        month_start = now.replace(day=1)
        month_df = df[
            (df["date"].dt.date >= month_start) &
            (~df["is_transfer"].fillna(False)) &
            (~df["is_duplicate"].fillna(False)) &
            (df["type"] == "debit")
        ]
        if "category" in month_df.columns:
            cat_spend = month_df.groupby("category")["amount"].sum().to_dict()

    for b in budgets:
        b["spent"] = round(float(cat_spend.get(b["category"], 0)), 2)
        b["amount"] = float(b["amount"])

    return budgets


@router.put("/budgets/{category}")
def upsert_budget(category: str, body: BudgetBody, current_user: dict = Depends(get_current_user)):
    user_id = current_user["id"]
    with get_user_conn(user_id) as conn:
        conn.execute("""
            INSERT INTO budgets (user_id, category, amount, period)
            VALUES (%s, %s, %s, %s)
            ON CONFLICT (user_id, category)
            DO UPDATE SET amount = EXCLUDED.amount, period = EXCLUDED.period
        """, (user_id, category, body.amount, body.period))
    return {"ok": True}


@router.delete("/budgets/{category}")
def delete_budget(category: str, current_user: dict = Depends(get_current_user)):
    user_id = current_user["id"]
    with get_user_conn(user_id) as conn:
        conn.execute(
            "DELETE FROM budgets WHERE user_id = %s AND category = %s",
            (user_id, category)
        )
    return {"ok": True}


# ── Recurring detection ───────────────────────────────────────────────────────

FREQ_RANGES = [
    ("weekly",     6,   8),
    ("biweekly",  12,  16),
    ("monthly",   25,  35),
    ("quarterly", 85,  95),
    ("annual",   350, 380),
]


def _infer_frequency(diffs: list[int]) -> str | None:
    if not diffs:
        return None
    sorted_diffs = sorted(diffs)
    median_diff = sorted_diffs[len(sorted_diffs) // 2]
    for label, lo, hi in FREQ_RANGES:
        if lo <= median_diff <= hi:
            return label
    return None


def _infer_frequency_loose(diffs: list[int]) -> str:
    """Like _infer_frequency, but always returns a label by snapping to the closest bucket.
    Used for user-marked rules where we trust the user's intent over interval cleanliness."""
    if not diffs:
        return "monthly"
    sorted_diffs = sorted(diffs)
    median_diff = sorted_diffs[len(sorted_diffs) // 2]
    centers = [("weekly", 7), ("biweekly", 14), ("monthly", 30), ("quarterly", 91), ("annual", 365)]
    return min(centers, key=lambda c: abs(c[1] - median_diff))[0]


def _detect_recurring(df: pd.DataFrame, user_rules: list[dict] | None = None) -> list:
    # Both spending (debit) and income (credit) can recur — e.g. subscriptions
    # and paychecks. Transfers and duplicates are excluded. Sign is preserved so
    # downstream UI can tell income from spending.
    clean = df[
        (~df["is_transfer"].fillna(False)) &
        (~df["is_duplicate"].fillna(False))
    ].copy()

    if clean.empty:
        return []

    # Key by normalized merchant, fall back to raw name
    clean["_key"] = clean.apply(
        lambda r: (r.get("merchant_normalized") or "").strip() or str(r["name"]),
        axis=1,
    )

    user_rule_keys = {r["merchant_key"] for r in (user_rules or [])}
    results = []

    # ── Auto-detection ────────────────────────────────────────────────────────
    for key, group in clean.groupby("_key"):
        if key in user_rule_keys:
            continue  # rule path will handle this merchant
        if len(group) < 2:
            continue

        group = group.sort_values("date")
        amounts = group["amount"].tolist()

        # Amount must be consistent. Use $1.00 absolute OR 5% of median, whichever
        # is larger. $1.00 handles tax rounding on cheap subs (e.g. $2.99 iCloud);
        # the 5% floor catches mid-priced subs whose price gets bumped (e.g. AMC
        # going from $25.99 → $27.03 is a 4% hike, clearly still a subscription).
        # Variable merchants (grocery, restaurants) routinely swing $10–$80, so
        # 5% on a $30 median still rejects them.
        sorted_amt = sorted(amounts)
        median_amt = sorted_amt[len(sorted_amt) // 2]
        if median_amt == 0:
            continue
        # abs() so the tolerance works for income (negative) too. A mixed-sign
        # group (e.g. a purchase and its refund) has a wide spread and is rejected.
        amount_tolerance = max(1.00, abs(median_amt) * 0.05)
        if max(amounts) - min(amounts) > amount_tolerance:
            continue

        # Date interval analysis — use median interval, not average.
        dates = group["date"].tolist()
        diffs = [(dates[i + 1] - dates[i]).days for i in range(len(dates) - 1)]
        freq = _infer_frequency(diffs)
        if freq is None:
            continue

        sorted_diffs = sorted(diffs)
        median_diff = sorted_diffs[len(sorted_diffs) // 2]
        if len(diffs) > 1 and any(abs(d - median_diff) / median_diff > 0.4 for d in diffs):
            continue

        results.append({
            "name": str(key),
            "amount": round(float(median_amt), 2),
            "frequency": freq,
            "occurrences": len(group),
            "last_date": group["date"].max().date().isoformat(),
            "source": "auto",
        })

    # ── User-rule matching ────────────────────────────────────────────────────
    for rule in (user_rules or []):
        center = float(rule["amount_center"])
        tol = max(float(rule["amount_tolerance_abs"]), center * float(rule["amount_tolerance_pct"]) / 100.0)
        group = clean[
            (clean["_key"] == rule["merchant_key"]) &
            ((clean["amount"] - center).abs() <= tol)
        ]
        if group.empty:
            continue
        group = group.sort_values("date")
        dates = group["date"].tolist()
        diffs = [(dates[i + 1] - dates[i]).days for i in range(len(dates) - 1)]
        freq = rule.get("frequency_hint") or _infer_frequency_loose(diffs)
        amounts = group["amount"].tolist()
        sorted_amt = sorted(amounts)
        median_amt = sorted_amt[len(sorted_amt) // 2]

        results.append({
            "name": rule.get("label") or str(rule["merchant_key"]),
            "amount": round(float(median_amt), 2),
            "frequency": freq,
            "occurrences": len(group),
            "last_date": group["date"].max().date().isoformat(),
            "source": "manual",
            "rule_id": rule["id"],
        })

    results.sort(key=lambda x: x["amount"], reverse=True)
    return results[:40]


def _fetch_user_rules(user_id: int) -> list[dict]:
    with get_user_conn(user_id) as conn:
        rows = conn.execute("""
            SELECT id, merchant_key, amount_center, amount_tolerance_abs,
                   amount_tolerance_pct, label, frequency_hint
            FROM user_recurring_rules
            WHERE user_id = %s
            ORDER BY id
        """, (user_id,)).fetchall()
    return [dict(r) for r in rows]


@router.get("/recurring")
def list_recurring(current_user: dict = Depends(get_current_user)):
    user_id = current_user["id"]
    df = ins.load_data(user_id)
    rules = _fetch_user_rules(user_id)
    if df.empty:
        return []
    return _detect_recurring(df, rules)


# ── User recurring rule CRUD ──────────────────────────────────────────────────

class RecurringRuleBody(BaseModel):
    merchant_key: str
    amount_center: float
    amount_tolerance_abs: Optional[float] = 2.00
    amount_tolerance_pct: Optional[float] = 15.00
    label: Optional[str] = None
    frequency_hint: Optional[str] = None


class RecurringRuleFromTransactionBody(BaseModel):
    transaction_id: str
    label: Optional[str] = None
    frequency_hint: Optional[str] = None


class RecurringRulePatchBody(BaseModel):
    amount_center: Optional[float] = None
    amount_tolerance_abs: Optional[float] = None
    amount_tolerance_pct: Optional[float] = None
    label: Optional[str] = None
    frequency_hint: Optional[str] = None


@router.get("/recurring/rules")
def list_recurring_rules(current_user: dict = Depends(get_current_user)):
    rules = _fetch_user_rules(current_user["id"])
    return [
        {
            "id": r["id"],
            "merchant_key": r["merchant_key"],
            "amount_center": float(r["amount_center"]),
            "amount_tolerance_abs": float(r["amount_tolerance_abs"]),
            "amount_tolerance_pct": float(r["amount_tolerance_pct"]),
            "label": r.get("label"),
            "frequency_hint": r.get("frequency_hint"),
        }
        for r in rules
    ]


@router.post("/recurring/rules")
def create_recurring_rule(body: RecurringRuleBody, current_user: dict = Depends(get_current_user)):
    user_id = current_user["id"]
    with get_user_conn(user_id) as conn:
        row = conn.execute("""
            INSERT INTO user_recurring_rules
                (user_id, merchant_key, amount_center, amount_tolerance_abs,
                 amount_tolerance_pct, label, frequency_hint)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (user_id, merchant_key)
            DO UPDATE SET
                amount_center = EXCLUDED.amount_center,
                amount_tolerance_abs = EXCLUDED.amount_tolerance_abs,
                amount_tolerance_pct = EXCLUDED.amount_tolerance_pct,
                label = EXCLUDED.label,
                frequency_hint = EXCLUDED.frequency_hint,
                updated_at = NOW()
            RETURNING id
        """, (
            user_id, body.merchant_key.strip(), body.amount_center,
            body.amount_tolerance_abs or 2.00, body.amount_tolerance_pct or 15.00,
            body.label, body.frequency_hint,
        )).fetchone()
    return {"id": row["id"]}


@router.post("/recurring/rules/from-transaction")
def create_rule_from_transaction(
    body: RecurringRuleFromTransactionBody,
    current_user: dict = Depends(get_current_user),
):
    user_id = current_user["id"]
    df = ins.load_data(user_id)
    if df.empty:
        raise HTTPException(status_code=404, detail="No transactions found")
    row = df[df["id"].astype(str) == str(body.transaction_id)]
    if row.empty:
        raise HTTPException(status_code=404, detail="Transaction not found")
    tx = row.iloc[0]
    merchant_key = (tx.get("merchant_normalized") or "").strip() or str(tx["name"])
    amount = float(tx["amount"])
    with get_user_conn(user_id) as conn:
        result = conn.execute("""
            INSERT INTO user_recurring_rules
                (user_id, merchant_key, amount_center, label, frequency_hint)
            VALUES (%s, %s, %s, %s, %s)
            ON CONFLICT (user_id, merchant_key)
            DO UPDATE SET
                amount_center = EXCLUDED.amount_center,
                label = COALESCE(EXCLUDED.label, user_recurring_rules.label),
                frequency_hint = COALESCE(EXCLUDED.frequency_hint, user_recurring_rules.frequency_hint),
                updated_at = NOW()
            RETURNING id
        """, (user_id, merchant_key, amount, body.label, body.frequency_hint)).fetchone()
    return {"id": result["id"], "merchant_key": merchant_key, "amount_center": amount}


@router.put("/recurring/rules/{rule_id}")
def update_recurring_rule(
    rule_id: int,
    body: RecurringRulePatchBody,
    current_user: dict = Depends(get_current_user),
):
    user_id = current_user["id"]
    fields = []
    params: list = []
    for col, val in [
        ("amount_center", body.amount_center),
        ("amount_tolerance_abs", body.amount_tolerance_abs),
        ("amount_tolerance_pct", body.amount_tolerance_pct),
        ("label", body.label),
        ("frequency_hint", body.frequency_hint),
    ]:
        if val is not None:
            fields.append(f"{col} = %s")
            params.append(val)
    if not fields:
        return {"ok": True}
    fields.append("updated_at = NOW()")
    params.extend([rule_id, user_id])
    with get_user_conn(user_id) as conn:
        conn.execute(
            f"UPDATE user_recurring_rules SET {', '.join(fields)} WHERE id = %s AND user_id = %s",
            tuple(params),
        )
    return {"ok": True}


@router.delete("/recurring/rules/{rule_id}")
def delete_recurring_rule(rule_id: int, current_user: dict = Depends(get_current_user)):
    user_id = current_user["id"]
    with get_user_conn(user_id) as conn:
        conn.execute(
            "DELETE FROM user_recurring_rules WHERE id = %s AND user_id = %s",
            (rule_id, user_id),
        )
    return {"ok": True}


# ── Group models ──────────────────────────────────────────────────────────────

class GroupBody(BaseModel):
    name: str
    color: str = "#c8ff00"
    goal: Optional[float] = None


class AddTransactionBody(BaseModel):
    transaction_id: str


# ── Group endpoints ───────────────────────────────────────────────────────────

@router.get("/groups")
def list_groups(current_user: dict = Depends(get_current_user)):
    user_id = current_user["id"]

    with get_user_conn(user_id) as conn:
        rows = conn.execute(
            "SELECT id, name, color, goal FROM custom_groups WHERE user_id = %s ORDER BY created_at",
            (user_id,)
        ).fetchall()

    groups = [dict(r) for r in rows]
    if not groups:
        return groups

    df = ins.load_data(user_id)

    with get_user_conn(user_id) as conn:
        for g in groups:
            tx_rows = conn.execute(
                "SELECT transaction_id FROM group_transactions WHERE group_id = %s",
                (g["id"],)
            ).fetchall()
            tx_ids = [r["transaction_id"] for r in tx_rows]
            g["count"] = len(tx_ids)
            g["goal"] = float(g["goal"]) if g["goal"] is not None else None
            if tx_ids and not df.empty and "id" in df.columns:
                matched = df[df["id"].astype(str).isin([str(t) for t in tx_ids])]
                g["total"] = round(float(matched["amount"].sum()), 2) if not matched.empty else 0.0
            else:
                g["total"] = 0.0

    return groups


@router.post("/groups")
def create_group(body: GroupBody, current_user: dict = Depends(get_current_user)):
    user_id = current_user["id"]
    with get_user_conn(user_id) as conn:
        row = conn.execute("""
            INSERT INTO custom_groups (user_id, name, color, goal)
            VALUES (%s, %s, %s, %s)
            RETURNING id
        """, (user_id, body.name, body.color, body.goal)).fetchone()
    return {"id": row["id"]}


@router.put("/groups/{group_id}")
def update_group(group_id: int, body: GroupBody, current_user: dict = Depends(get_current_user)):
    user_id = current_user["id"]
    with get_user_conn(user_id) as conn:
        conn.execute("""
            UPDATE custom_groups SET name = %s, color = %s, goal = %s
            WHERE id = %s AND user_id = %s
        """, (body.name, body.color, body.goal, group_id, user_id))
    return {"ok": True}


@router.delete("/groups/{group_id}")
def delete_group(group_id: int, current_user: dict = Depends(get_current_user)):
    user_id = current_user["id"]
    with get_user_conn(user_id) as conn:
        conn.execute(
            "DELETE FROM custom_groups WHERE id = %s AND user_id = %s",
            (group_id, user_id)
        )
    return {"ok": True}


@router.get("/groups/{group_id}/transactions")
def group_transactions(group_id: int, current_user: dict = Depends(get_current_user)):
    user_id = current_user["id"]

    with get_user_conn(user_id) as conn:
        exists = conn.execute(
            "SELECT id FROM custom_groups WHERE id = %s AND user_id = %s",
            (group_id, user_id)
        ).fetchone()
        if not exists:
            raise HTTPException(status_code=404, detail="Group not found")

        tx_rows = conn.execute(
            "SELECT transaction_id FROM group_transactions WHERE group_id = %s",
            (group_id,)
        ).fetchall()

    tx_ids = [r["transaction_id"] for r in tx_rows]

    if not tx_ids:
        return {"rows": [], "total": 0.0, "count": 0, "transaction_ids": []}

    df = ins.load_data(user_id)
    if df.empty or "id" not in df.columns:
        return {"rows": [], "total": 0.0, "count": 0, "transaction_ids": tx_ids}

    matched = df[df["id"].astype(str).isin([str(t) for t in tx_ids])]
    if matched.empty:
        return {"rows": [], "total": 0.0, "count": 0, "transaction_ids": tx_ids}

    cols = [c for c in ["id", "date", "name", "merchant_normalized", "category",
                         "amount", "institution", "pending", "notes"]
            if c in matched.columns]
    result = matched[cols].sort_values("date", ascending=False)

    return {
        "rows": _df_to_records(result),
        "total": round(float(matched["amount"].sum()), 2),
        "count": len(result),
        "transaction_ids": tx_ids,
    }


@router.post("/groups/{group_id}/transactions")
def add_transaction(group_id: int, body: AddTransactionBody, current_user: dict = Depends(get_current_user)):
    user_id = current_user["id"]
    with get_user_conn(user_id) as conn:
        exists = conn.execute(
            "SELECT id FROM custom_groups WHERE id = %s AND user_id = %s",
            (group_id, user_id)
        ).fetchone()
        if not exists:
            raise HTTPException(status_code=404, detail="Group not found")
        owns_tx = conn.execute(
            "SELECT 1 FROM transactions WHERE id = %s AND user_id = %s",
            (body.transaction_id, user_id)
        ).fetchone()
        if not owns_tx:
            raise HTTPException(status_code=404, detail="Transaction not found")
        conn.execute("""
            INSERT INTO group_transactions (group_id, transaction_id)
            VALUES (%s, %s) ON CONFLICT DO NOTHING
        """, (group_id, body.transaction_id))
    return {"ok": True}


@router.delete("/groups/{group_id}/transactions/{transaction_id}")
def remove_transaction(group_id: int, transaction_id: str, current_user: dict = Depends(get_current_user)):
    user_id = current_user["id"]
    with get_user_conn(user_id) as conn:
        exists = conn.execute(
            "SELECT id FROM custom_groups WHERE id = %s AND user_id = %s",
            (group_id, user_id)
        ).fetchone()
        if not exists:
            raise HTTPException(status_code=404, detail="Group not found")
        conn.execute(
            "DELETE FROM group_transactions WHERE group_id = %s AND transaction_id = %s",
            (group_id, transaction_id)
        )
    return {"ok": True}
