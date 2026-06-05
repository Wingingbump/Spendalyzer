from typing import Optional
from urllib.parse import unquote

import pandas as pd
from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel

from backend.dependencies import apply_filters, get_current_user
from core import insights as ins
from core.db import (
    save_merchant_override, delete_merchant_override, get_merchant_overrides,
    get_merchant_category_overrides, upsert_merchant_category_override,
    delete_merchant_category_override, bulk_apply_category_override,
)

router = APIRouter(prefix="/merchants", tags=["merchants"])


class MerchantRenameBody(BaseModel):
    display_name: str


class MerchantCategoryBody(BaseModel):
    category: str


def _df_to_records(df: pd.DataFrame) -> list:
    cleaned = df.where(pd.notna(df), None)
    records = cleaned.to_dict(orient="records")
    for row in records:
        for k, v in row.items():
            if hasattr(v, "isoformat"):
                row[k] = v.isoformat()
    return records


@router.get("")
def list_merchants(
    range: str = Query("30d"),
    institution: str = Query("all"),
    account: str = Query("all"),
    category: str = Query("all"),
    merchant_query: str = Query(""),
    current_user: dict = Depends(get_current_user),
):
    df = ins.load_data(current_user["id"])
    df = apply_filters(df, range, institution, account, category, merchant_query)
    result = ins.spending_by_merchant(df, top_n=15)
    return _df_to_records(result)


@router.get("/overrides")
def list_overrides(current_user: dict = Depends(get_current_user)):
    return get_merchant_overrides(current_user["id"])


@router.put("/overrides/{raw_name}")
def upsert_override(
    raw_name: str,
    body: MerchantRenameBody,
    current_user: dict = Depends(get_current_user),
):
    save_merchant_override(current_user["id"], unquote(raw_name), body.display_name.strip())
    ins.invalidate_user_cache(current_user["id"])
    return {"ok": True}


@router.delete("/overrides/{raw_name}")
def remove_override(
    raw_name: str,
    current_user: dict = Depends(get_current_user),
):
    delete_merchant_override(current_user["id"], unquote(raw_name))
    ins.invalidate_user_cache(current_user["id"])
    return {"ok": True}


@router.get("/category-overrides")
def list_category_overrides(current_user: dict = Depends(get_current_user)):
    return get_merchant_category_overrides(current_user["id"])


@router.put("/category-overrides/{merchant_name}")
def upsert_category_override(
    merchant_name: str,
    body: MerchantCategoryBody,
    current_user: dict = Depends(get_current_user),
):
    upsert_merchant_category_override(current_user["id"], unquote(merchant_name), body.category.strip())
    ins.invalidate_user_cache(current_user["id"])
    return {"ok": True}


@router.delete("/category-overrides/{merchant_name}")
def remove_category_override(
    merchant_name: str,
    current_user: dict = Depends(get_current_user),
):
    delete_merchant_category_override(current_user["id"], unquote(merchant_name))
    ins.invalidate_user_cache(current_user["id"])
    return {"ok": True}


@router.post("/category-overrides/{merchant_name}/apply-historical")
def apply_category_historical(
    merchant_name: str,
    body: MerchantCategoryBody,
    current_user: dict = Depends(get_current_user),
):
    merchant = unquote(merchant_name)
    df = ins.load_data(current_user["id"])
    spending = ins.get_spending(df)
    matching = spending[spending["merchant_normalized"] == merchant]
    ids = matching["id"].tolist()
    count = bulk_apply_category_override(current_user["id"], ids, body.category.strip())
    ins.invalidate_user_cache(current_user["id"])
    return {"count": count}


@router.get("/{merchant_name}")
def merchant_detail(
    merchant_name: str,
    range: str = Query("30d"),
    institution: str = Query("all"),
    account: str = Query("all"),
    current_user: dict = Depends(get_current_user),
):
    merchant = unquote(merchant_name)
    df = ins.load_data(current_user["id"])
    df = apply_filters(df, range, institution, account)
    result = ins.drill_down_merchant(df, merchant)
    return _df_to_records(result)
