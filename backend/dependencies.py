from datetime import date
from typing import Optional

import pandas as pd
from fastapi import Request
from fastapi.exceptions import HTTPException

from backend.auth import decode_token
from core import insights as ins


def get_current_user(request: Request) -> dict:
    token = request.cookies.get("access_token")
    if not token:
        raise HTTPException(status_code=401, detail="Not authenticated")
    payload = decode_token(token)
    if not payload or payload.get("type") != "access":
        raise HTTPException(status_code=401, detail="Invalid or expired token")
    return {
        "id": int(payload["sub"]),
        "username": payload.get("username", ""),
    }


def apply_filters(
    df: pd.DataFrame,
    range_param: str = "30d",
    institution: str = "all",
    account: str = "all",
    category: str = "all",
    merchant_query: str = "",
) -> pd.DataFrame:
    # Date range filter
    r = (range_param or "30d").strip().lower()

    if r.startswith("custom:"):
        parts = r.split(":")
        if len(parts) == 3:
            try:
                start = pd.Timestamp(parts[1])
                end = pd.Timestamp(parts[2])
                df = df[(df["date"] >= start) & (df["date"] <= end)].copy()
            except ValueError:
                pass
    elif r == "all":
        pass  # no date filter
    elif r == "ytd":
        today = date.today()
        cutoff = pd.Timestamp(today.year, 1, 1)
        df = df[df["date"] >= cutoff].copy()
    elif len(r) == 7 and r[4] == "-":
        # Specific month: YYYY-MM
        try:
            year = int(r[:4])
            month = int(r[5:])
            df = ins.filter_by_month(df, year, month)
        except (ValueError, IndexError):
            pass
    elif r.endswith("d"):
        try:
            days = int(r[:-1])
            df = ins.filter_by_range(df, days)
        except ValueError:
            pass
    elif r.endswith("m"):
        try:
            months = int(r[:-1])
            df = ins.filter_by_range(df, months * 30)
        except ValueError:
            pass
    else:
        # Try parsing as plain integer days
        try:
            days = int(r)
            df = ins.filter_by_range(df, days)
        except ValueError:
            pass

    # Institution filter
    if institution and institution.lower() != "all":
        df = ins.filter_by_institution(df, institution)

    # Account filter
    if account and account.lower() != "all":
        df = ins.filter_by_account(df, account)

    # Category filter — case-insensitive exact match against the canonical category
    if category and category.lower() != "all" and "category" in df.columns:
        df = df[df["category"].fillna("").str.lower() == category.lower()].copy()

    # Merchant filter — substring match on merchant_normalized or raw name
    if merchant_query and "merchant_normalized" in df.columns:
        q = merchant_query.strip().lower()
        if q:
            merch = df["merchant_normalized"].fillna("").str.lower()
            name = df["name"].fillna("").str.lower() if "name" in df.columns else merch
            df = df[merch.str.contains(q, regex=False) | name.str.contains(q, regex=False)].copy()

    return df
