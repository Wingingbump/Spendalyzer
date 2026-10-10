from typing import Optional
from urllib.parse import unquote

import pandas as pd
from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel

from backend.dependencies import apply_filters, get_current_user
from backend.serialization import df_to_records as _df_to_records
from core import insights as ins
from core.db import (
    load_category_map, upsert_category_mapping, delete_category_mapping,
    get_user_categories, add_user_category, delete_user_category,
)

router = APIRouter(prefix="/categories", tags=["categories"])


class MappingBody(BaseModel):
    external_category: str
    internal_category: str


class UserCategoryBody(BaseModel):
    name: str


@router.get("/user")
def list_user_categories(current_user: dict = Depends(get_current_user)):
    return get_user_categories(current_user["id"])


@router.post("/user")
def create_user_category(body: UserCategoryBody, current_user: dict = Depends(get_current_user)):
    name = body.name.strip()
    if not name:
        from fastapi import HTTPException
        raise HTTPException(status_code=422, detail="Category name cannot be empty")
    add_user_category(current_user["id"], name)
    ins.invalidate_user_cache(current_user["id"])
    return {"ok": True}


@router.delete("/user/{name}")
def remove_user_category(name: str, current_user: dict = Depends(get_current_user)):
    delete_user_category(current_user["id"], unquote(name))
    ins.invalidate_user_cache(current_user["id"])
    return {"ok": True}


@router.get("/mappings")
def get_mappings(current_user: dict = Depends(get_current_user)):
    mapping = load_category_map(current_user["id"])
    return [
        {"external_category": k, "internal_category": v}
        for k, v in mapping.items()
    ]


@router.post("/mappings")
def post_mapping(body: MappingBody, current_user: dict = Depends(get_current_user)):
    upsert_category_mapping(current_user["id"], body.external_category, body.internal_category)
    ins.invalidate_user_cache(current_user["id"])
    return {"ok": True}


@router.delete("/mappings/{external_category}")
def remove_mapping(external_category: str, current_user: dict = Depends(get_current_user)):
    delete_category_mapping(current_user["id"], unquote(external_category))
    ins.invalidate_user_cache(current_user["id"])
    return {"ok": True}


@router.get("")
def list_categories(
    range: str = Query("30d"),
    institution: str = Query("all"),
    account: str = Query("all"),
    current_user: dict = Depends(get_current_user),
):
    df = ins.load_data(current_user["id"])
    df = apply_filters(df, range, institution, account)
    result = ins.spending_by_category(df)
    return _df_to_records(result)


@router.get("/{category_name}")
def category_detail(
    category_name: str,
    range: str = Query("30d"),
    institution: str = Query("all"),
    account: str = Query("all"),
    current_user: dict = Depends(get_current_user),
):
    category = unquote(category_name)
    df = ins.load_data(current_user["id"])
    df = apply_filters(df, range, institution, account)
    result = ins.drill_down_category(df, category)
    return _df_to_records(result)
