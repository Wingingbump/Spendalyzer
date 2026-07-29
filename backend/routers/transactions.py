from typing import Optional

import pandas as pd
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel

from backend.dependencies import apply_filters, get_current_user
from backend.limiter import limiter
from core import insights as ins
from core.db import (
    delete_transaction, insert_manual_transaction, save_override,
    dismiss_duplicate_pair, transaction_belongs_to_user,
    upsert_transfer_override, delete_transfer_override,
)

router = APIRouter(prefix="/transactions", tags=["transactions"])


def _df_to_records(df: pd.DataFrame) -> list:
    cleaned = df.where(pd.notna(df), None)
    records = cleaned.to_dict(orient="records")
    for row in records:
        for k, v in row.items():
            if hasattr(v, "isoformat"):
                row[k] = v.isoformat()
    return records


class OverrideBody(BaseModel):
    category: Optional[str] = None
    amount: Optional[float] = None
    notes: Optional[str] = None
    excluded: Optional[bool] = None


class CreateTransactionBody(BaseModel):
    name: str
    date: str
    amount: float
    category: Optional[str] = None
    notes: Optional[str] = None


class DismissDuplicateBody(BaseModel):
    other_id: str


class TransferBody(BaseModel):
    # True = always treat this counterparty as a transfer; False = never;
    # None = clear the remembered rule and revert to auto-detection.
    is_transfer: Optional[bool] = None


@router.get("")
def list_transactions(
    range: str = Query("30d"),
    institution: str = Query("all"),
    account: str = Query("all"),
    search: Optional[str] = Query(None),
    category: str = Query("all"),
    merchant_query: str = Query(""),
    current_user: dict = Depends(get_current_user),
):
    df = ins.load_data(current_user["id"])
    df = apply_filters(df, range, institution, account, category, merchant_query)

    spending = ins.get_spending(df)
    cols = [c for c in ["id", "date", "name", "merchant_normalized", "category",
                         "amount", "institution", "pending", "notes",
                         "has_user_override", "is_manual",
                         "is_potential_duplicate", "potential_dup_of"]
            if c in spending.columns]
    result = spending[cols].sort_values("date", ascending=False).reset_index(drop=True)

    if search:
        s = search.lower()
        mask = (
            result.get("name", pd.Series(dtype=str)).fillna("").str.lower().str.contains(s, regex=False)
            | result.get("merchant_normalized", pd.Series(dtype=str)).fillna("").str.lower().str.contains(s, regex=False)
            | result.get("category", pd.Series(dtype=str)).fillna("").str.lower().str.contains(s, regex=False)
        )
        result = result[mask]

    return _df_to_records(result)


@router.post("")
@limiter.limit("30/minute")
def create_transaction(
    request: Request,
    body: CreateTransactionBody,
    current_user: dict = Depends(get_current_user),
):
    tx_id = insert_manual_transaction(
        user_id=current_user["id"],
        name=body.name,
        date=body.date,
        amount=body.amount,
        category=body.category,
        notes=body.notes,
    )
    ins.invalidate_user_cache(current_user["id"])
    return {"ok": True, "id": tx_id}


@router.patch("/{transaction_id}")
def patch_transaction(
    transaction_id: str,
    body: OverrideBody,
    current_user: dict = Depends(get_current_user),
):
    if not transaction_belongs_to_user(transaction_id, current_user["id"]):
        raise HTTPException(status_code=404, detail="Transaction not found")
    save_override(
        current_user["id"],
        transaction_id,
        category=body.category,
        amount=body.amount,
        notes=body.notes,
        excluded=body.excluded,
    )
    ins.invalidate_user_cache(current_user["id"])
    return {"ok": True}


@router.post("/{transaction_id}/transfer")
def set_transfer_treatment(
    transaction_id: str,
    body: TransferBody,
    current_user: dict = Depends(get_current_user),
):
    """Remember how to treat this transaction's counterparty (transfer vs. not).

    The ruling is stored against the counterparty (its resolved merchant name), so
    it applies to every past and future transaction from the same payee — the user
    only ever has to correct a given counterparty once.
    """
    if not transaction_belongs_to_user(transaction_id, current_user["id"]):
        raise HTTPException(status_code=404, detail="Transaction not found")

    df = ins.load_data(current_user["id"])
    row = df[df["id"] == transaction_id]
    if row.empty:
        raise HTTPException(status_code=404, detail="Transaction not found")

    merchant_key = row.iloc[0].get("merchant_normalized")
    if not merchant_key or pd.isna(merchant_key):
        raise HTTPException(
            status_code=422, detail="Transaction has no counterparty to apply a rule to"
        )

    if body.is_transfer is None:
        delete_transfer_override(current_user["id"], merchant_key)
    else:
        upsert_transfer_override(current_user["id"], merchant_key, body.is_transfer)

    ins.invalidate_user_cache(current_user["id"])
    return {"ok": True, "merchant": merchant_key, "is_transfer": body.is_transfer}


@router.post("/{transaction_id}/dismiss-duplicate")
def dismiss_duplicate(
    transaction_id: str,
    body: DismissDuplicateBody,
    current_user: dict = Depends(get_current_user),
):
    dismiss_duplicate_pair(current_user["id"], transaction_id, body.other_id)
    ins.invalidate_user_cache(current_user["id"])
    return {"ok": True}


@router.delete("/{transaction_id}")
@limiter.limit("30/minute")
def delete_transaction_endpoint(
    request: Request,
    transaction_id: str,
    current_user: dict = Depends(get_current_user),
):
    deleted = delete_transaction(transaction_id, current_user["id"])
    if not deleted:
        raise HTTPException(status_code=404, detail="Transaction not found")
    ins.invalidate_user_cache(current_user["id"])
    return {"ok": True}
