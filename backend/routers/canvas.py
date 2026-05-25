import json

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from backend.dependencies import apply_filters, get_current_user
from core import insights as ins
from core.db import get_user_conn

router = APIRouter(prefix="/canvas", tags=["canvas"])


# ── Pydantic models ───────────────────────────────────────────────────────────

class CanvasCreate(BaseModel):
    name: str


class CanvasSave(BaseModel):
    name: str
    layout: list
    widgets: dict


# ── Canvas CRUD ───────────────────────────────────────────────────────────────

@router.get("")
def list_canvases(current_user: dict = Depends(get_current_user)):
    with get_user_conn(current_user["id"]) as conn:
        rows = conn.execute(
            "SELECT id, name, created_at FROM canvases WHERE user_id = %s ORDER BY created_at",
            (current_user["id"],)
        ).fetchall()
    return [{"id": r["id"], "name": r["name"], "created_at": str(r["created_at"])} for r in rows]


@router.post("")
def create_canvas(body: CanvasCreate, current_user: dict = Depends(get_current_user)):
    with get_user_conn(current_user["id"]) as conn:
        count = conn.execute(
            "SELECT COUNT(*) AS n FROM canvases WHERE user_id = %s",
            (current_user["id"],)
        ).fetchone()["n"]
        if count >= 3:
            raise HTTPException(status_code=400, detail="Maximum of 3 canvases allowed")
        row = conn.execute(
            "INSERT INTO canvases (user_id, name) VALUES (%s, %s) RETURNING id, name, created_at",
            (current_user["id"], body.name.strip() or "My Canvas")
        ).fetchone()
    return {"id": row["id"], "name": row["name"], "created_at": str(row["created_at"])}


@router.get("/sankey")
def get_sankey(
    range: str = Query("30d"),
    institution: str = Query("all"),
    account: str = Query("all"),
    current_user: dict = Depends(get_current_user),
):
    """
    Three-column flow funneled through a single balance hub:

        Income sources → Balance → Outflow categories

    Routing every income line into one middle node and every outflow line back
    out of it makes the diagram visually clean — no crossings between sources,
    no crossings between destinations. Surplus becomes a "Savings" outflow;
    deficit becomes a "Drawn from balance" income.
    """
    import pandas as pd

    df = ins.load_data(current_user["id"])
    df = apply_filters(df, range, institution, account)

    if df.empty:
        return {"nodes": [], "links": []}

    clean = df[
        (~df["is_transfer"].fillna(False)) &
        (~df["is_duplicate"].fillna(False))
    ] if "is_transfer" in df.columns else df

    if clean.empty:
        return {"nodes": [], "links": []}

    TOP_INCOME = 6
    TOP_CATEGORIES = 10

    # ── Income totals by source ───────────────────────────────────────────────
    credits_df = clean[clean["type"] == "credit"].copy()
    if not credits_df.empty and "merchant_normalized" in credits_df.columns:
        credits_df["abs_amount"] = credits_df["amount"].abs()
        credits_df["source"] = credits_df["merchant_normalized"].fillna("").replace("", "Other income")
        src_totals = credits_df.groupby("source")["abs_amount"].sum()
        if len(src_totals) > TOP_INCOME:
            top = src_totals.nlargest(TOP_INCOME)
            other = src_totals.drop(top.index).sum()
            income_totals = top.copy()
            if other > 0:
                income_totals["Other income"] = income_totals.get("Other income", 0) + other
        else:
            income_totals = src_totals.copy()
        income_totals = income_totals[income_totals > 0]
    else:
        income_totals = pd.Series(dtype=float)

    # ── Outflow totals by category ────────────────────────────────────────────
    debits_df = clean[clean["type"] == "debit"].copy()
    if not debits_df.empty and "category" in debits_df.columns:
        debits_df["category"] = debits_df["category"].fillna("Uncategorized")
        cat_raw_totals = debits_df.groupby("category")["amount"].sum()
        if len(cat_raw_totals) > TOP_CATEGORIES:
            top = cat_raw_totals.nlargest(TOP_CATEGORIES)
            other = cat_raw_totals.drop(top.index).sum()
            cat_totals = top.copy()
            if other > 0:
                cat_totals["Other"] = cat_totals.get("Other", 0) + other
        else:
            cat_totals = cat_raw_totals.copy()
        cat_totals = cat_totals[cat_totals > 0]
    else:
        cat_totals = pd.Series(dtype=float)

    if income_totals.empty and cat_totals.empty:
        return {"nodes": [], "links": []}

    total_income = float(income_totals.sum())
    total_outflow = float(cat_totals.sum())

    # Balance the diagram so the hub's inflow and outflow match.
    if total_outflow > total_income and total_outflow > 0:
        income_totals = income_totals.copy()
        income_totals["Drawn from balance"] = total_outflow - total_income
        total_income = total_outflow
    elif total_income > total_outflow and total_income > 0:
        cat_totals = cat_totals.copy()
        cat_totals["Savings"] = total_income - total_outflow
        total_outflow = total_income

    # ── Nodes ordered largest → smallest within each column, hub in middle ────
    income_nodes = list(income_totals.sort_values(ascending=False).index)
    cat_nodes = list(cat_totals.sort_values(ascending=False).index)
    hub_name = "Balance"

    nodes = (
        [{"name": n, "kind": "income"} for n in income_nodes]
        + [{"name": hub_name, "kind": "balance"}]
        + [{"name": n, "kind": "category"} for n in cat_nodes]
    )

    income_idx = {n: i for i, n in enumerate(income_nodes)}
    hub_idx = len(income_nodes)
    cat_idx = {n: i + len(income_nodes) + 1 for i, n in enumerate(cat_nodes)}

    # ── Links: source → hub, hub → category. One line per node. ───────────────
    links = []
    for src in income_nodes:
        links.append({
            "source": income_idx[src],
            "target": hub_idx,
            "value": round(float(income_totals[src]), 2),
        })
    for cat in cat_nodes:
        links.append({
            "source": hub_idx,
            "target": cat_idx[cat],
            "value": round(float(cat_totals[cat]), 2),
        })

    return {"nodes": nodes, "links": links}


@router.get("/{canvas_id}")
def load_canvas(canvas_id: int, current_user: dict = Depends(get_current_user)):
    with get_user_conn(current_user["id"]) as conn:
        row = conn.execute(
            "SELECT id, name, layout, widgets FROM canvases WHERE id = %s AND user_id = %s",
            (canvas_id, current_user["id"])
        ).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Canvas not found")
    return {
        "id": row["id"],
        "name": row["name"],
        "layout": row["layout"] or [],
        "widgets": row["widgets"] or {},
    }


@router.put("/{canvas_id}")
def save_canvas(canvas_id: int, body: CanvasSave, current_user: dict = Depends(get_current_user)):
    with get_user_conn(current_user["id"]) as conn:
        updated = conn.execute("""
            UPDATE canvases
            SET name = %s, layout = %s, widgets = %s
            WHERE id = %s AND user_id = %s
            RETURNING id
        """, (
            body.name.strip() or "My Canvas",
            json.dumps(body.layout),
            json.dumps(body.widgets),
            canvas_id,
            current_user["id"],
        )).fetchone()
    if not updated:
        raise HTTPException(status_code=404, detail="Canvas not found")
    return {"ok": True}


@router.delete("/{canvas_id}")
def delete_canvas(canvas_id: int, current_user: dict = Depends(get_current_user)):
    with get_user_conn(current_user["id"]) as conn:
        conn.execute(
            "DELETE FROM canvases WHERE id = %s AND user_id = %s",
            (canvas_id, current_user["id"])
        )
    return {"ok": True}
