import io
from typing import Optional

import pandas as pd
from fastapi import APIRouter, Depends, Query
from fastapi.responses import StreamingResponse

from backend.dependencies import apply_filters, get_current_user
from core import insights as ins

router = APIRouter(prefix="/ledger", tags=["ledger"])


def _df_to_records(df: pd.DataFrame) -> list:
    cleaned = df.where(pd.notna(df), None)
    records = cleaned.to_dict(orient="records")
    for row in records:
        for k, v in row.items():
            if hasattr(v, "isoformat"):
                row[k] = v.isoformat()
    return records


@router.get("")
def ledger(
    range: str = Query("30d"),
    institution: str = Query("all"),
    account: str = Query("all"),
    search: Optional[str] = Query(None),
    category: str = Query("all"),
    merchant_query: str = Query(""),
    types: Optional[str] = Query(None),
    show_transfers: bool = Query(False),
    show_duplicates: bool = Query(False),
    current_user: dict = Depends(get_current_user),
):
    df = ins.load_data(current_user["id"])
    df = apply_filters(df, range, institution, account, category, merchant_query)

    cols = [c for c in ["id", "date", "name", "merchant_normalized", "category",
                         "amount", "institution", "pending", "type",
                         "is_transfer", "is_duplicate", "notes",
                         "has_user_override", "is_manual",
                         "is_reimbursement", "needs_review", "is_excluded",
                         "is_potential_duplicate", "potential_dup_of"]
            if c in df.columns]
    result = df[cols].sort_values("date", ascending=False).reset_index(drop=True)

    if not show_transfers:
        if "is_transfer" in result.columns:
            result = result[~result["is_transfer"].fillna(False)]

    if not show_duplicates:
        if "is_duplicate" in result.columns:
            result = result[~result["is_duplicate"].fillna(False)]

    if types:
        type_list = [t.strip().lower() for t in types.split(",") if t.strip()]
        if type_list and "type" in result.columns:
            result = result[result["type"].str.lower().isin(type_list)]

    if search:
        s = search.lower()
        mask = (
            result.get("name", pd.Series(dtype=str)).fillna("").str.lower().str.contains(s, regex=False)
            | result.get("merchant_normalized", pd.Series(dtype=str)).fillna("").str.lower().str.contains(s, regex=False)
            | result.get("category", pd.Series(dtype=str)).fillna("").str.lower().str.contains(s, regex=False)
        )
        result = result[mask]

    records = _df_to_records(result)

    transfer_count = int(df["is_transfer"].fillna(False).sum()) if "is_transfer" in df.columns else 0
    total_count = len(records)
    # Net of reimbursements; income kept separate so it doesn't dilute spend.
    spent = ins.total_spent(df)
    income = ins.total_income(df)
    reimbursements = ins.total_reimbursements(df)
    net = round(spent - income, 2)

    return {
        "rows": records,
        "summary": {
            "spent": spent,
            "income": income,
            "reimbursements": reimbursements,
            "net": net,
            "transfer_count": transfer_count,
            "transactions": total_count,
        },
    }


@router.get("/export")
def export_ledger(
    range: str = Query("30d"),
    institution: str = Query("all"),
    account: str = Query("all"),
    search: Optional[str] = Query(None),
    category: str = Query("all"),
    merchant_query: str = Query(""),
    types: Optional[str] = Query(None),
    show_transfers: bool = Query(False),
    show_duplicates: bool = Query(False),
    current_user: dict = Depends(get_current_user),
):
    df = ins.load_data(current_user["id"])
    df = apply_filters(df, range, institution, account, category, merchant_query)

    cols = [c for c in ["date", "name", "merchant_normalized", "category",
                         "amount", "institution", "type", "pending",
                         "is_transfer", "is_duplicate", "notes"]
            if c in df.columns]
    result = df[cols].sort_values("date", ascending=False).reset_index(drop=True)

    if not show_transfers and "is_transfer" in result.columns:
        result = result[~result["is_transfer"].fillna(False)]
    if not show_duplicates and "is_duplicate" in result.columns:
        result = result[~result["is_duplicate"].fillna(False)]
    if types:
        type_list = [t.strip().lower() for t in types.split(",") if t.strip()]
        if type_list and "type" in result.columns:
            result = result[result["type"].str.lower().isin(type_list)]
    if search:
        s = search.lower()
        mask = (
            result.get("name", pd.Series(dtype=str)).fillna("").str.lower().str.contains(s, regex=False)
            | result.get("merchant_normalized", pd.Series(dtype=str)).fillna("").str.lower().str.contains(s, regex=False)
            | result.get("category", pd.Series(dtype=str)).fillna("").str.lower().str.contains(s, regex=False)
        )
        result = result[mask]

    buf = io.StringIO()
    result.to_csv(buf, index=False)
    buf.seek(0)

    return StreamingResponse(
        io.BytesIO(buf.read().encode("utf-8")),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=ledger.csv"},
    )
