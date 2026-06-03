"""Reimbursement vs income classification, and memo-based categorization.

A *reimbursement* is money back for something you spent — it should reduce the
matching category's spend. *Income* (paycheck, interest, tax refund) must stay
separate so it never dilutes spending.

Two kinds of reimbursement:
  - P2P peer payments (Venmo/PayPal/Zelle/Cash App from a person). Their category
    is inferred from the memo (the quoted note in the descriptor).
  - Merchant refunds (a non-P2P credit whose merchant matches one of your debits,
    e.g. a returned purchase). It already carries the merchant's category.

Memo categorization is confidence-gated to avoid guessing on jokes/emoji notes:
  Layer 1 — match the memo to one of your real purchases and inherit its category.
  Layer 2 — ask the AI, which is allowed to answer "Uncategorized".
  Anything unresolved -> Uncategorized + needs_review (highlighted for the user).
"""

import os
import re
from datetime import timedelta

import anthropic
import pandas as pd
from rapidfuzz import fuzz
from dotenv import load_dotenv

from core.categorize import CATEGORIES

load_dotenv()

_client = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))

P2P_PLATFORMS = ["venmo", "paypal", "zelle", "cashapp", "cash app", "apple cash"]
# Categories too vague to be a useful netting target — treat as "no category".
_GENERIC_CATS = {"", "uncategorized", "payments", "other", "income / interest"}

_MATCH_THRESHOLD = 82
_MATCH_WINDOW_BEFORE = 45  # days before the reimbursement to look for the spend
_MATCH_WINDOW_AFTER = 7


def extract_memo(name: str) -> str:
    """The quoted note in a P2P descriptor: Joe "dinner" -> "dinner"."""
    m = re.search(r'["““]([^"””]+)["””]', str(name or ""))
    return m.group(1).strip() if m else ""


def is_p2p(institution: str) -> bool:
    s = (institution or "").lower()
    return any(p in s for p in P2P_PLATFORMS)


def flag_reimbursements(df: pd.DataFrame) -> pd.DataFrame:
    """Add an `is_reimbursement` bool column."""
    df = df.copy()
    if df.empty:
        df["is_reimbursement"] = pd.Series(dtype=bool)
        return df

    credit = (
        (df["type"] == "credit")
        & (~df["is_transfer"].fillna(False))
        & (~df["is_duplicate"].fillna(False))
    )
    p2p = df["institution"].apply(is_p2p)
    debit_merchants = set(
        df[df["type"] == "debit"]["merchant_normalized"].dropna()
    )
    refund = credit & (~p2p) & df["merchant_normalized"].isin(debit_merchants)

    df["is_reimbursement"] = (credit & p2p) | refund
    return df


def _match_to_spend(memo: str, when, debits: pd.DataFrame) -> str | None:
    """Best-effort category from a confident match to one of the user's debits."""
    if not memo or debits.empty:
        return None
    lo = when - timedelta(days=_MATCH_WINDOW_BEFORE)
    hi = when + timedelta(days=_MATCH_WINDOW_AFTER)
    cand = debits[(debits["date"] >= lo) & (debits["date"] <= hi)]
    memo_l = memo.lower()

    best_cat, best_score = None, 0
    for _, d in cand.iterrows():
        merchant = str(d.get("merchant_normalized", "") or "").lower()
        blob = f"{merchant} {str(d.get('name', '') or '').lower()}"
        score = max(
            fuzz.token_set_ratio(memo_l, blob),
            fuzz.partial_ratio(memo_l, merchant) if merchant else 0,
        )
        if score > best_score:
            best_score, best_cat = score, d.get("category")
    if best_score >= _MATCH_THRESHOLD and best_cat and str(best_cat).lower() not in _GENERIC_CATS:
        return best_cat
    return None


def ai_classify_memo(memo: str) -> str:
    """Classify a P2P note into a category, or 'Uncategorized' if it isn't clearly
    an expense. The model is told to refuse rather than guess."""
    prompt = f"""A peer-to-peer payment (Venmo/PayPal) has this note: "{memo}"

Classify what the money was for into exactly one of these categories:
{", ".join(CATEGORIES)}

Rules:
- Return ONLY the category name, exactly as written above.
- If the note is a joke, an inside reference, emoji-only, a person's name, or does
  NOT clearly describe a real purchase or expense, return "Uncategorized".
- Do not guess. When unsure, return "Uncategorized"."""
    resp = _client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=16,
        messages=[{"role": "user", "content": prompt}],
    )
    out = resp.content[0].text.strip().strip('"')
    return out if out in CATEGORIES else "Uncategorized"


def categorize_reimbursements(df: pd.DataFrame, user_id: int) -> pd.DataFrame:
    """Assign categories to P2P reimbursements (memo -> category) and set
    `needs_review` on the ones we couldn't confidently resolve.

    Merchant refunds keep their own merchant category. User-overridden rows are
    never touched.
    """
    from core.db import load_memo_category_cache, save_memo_category

    df = df.copy()
    df["needs_review"] = False
    if "is_reimbursement" not in df.columns or not df["is_reimbursement"].any():
        return df

    debits = df[df["type"] == "debit"]
    memo_cache = load_memo_category_cache()

    for idx, row in df[df["is_reimbursement"]].iterrows():
        if bool(row.get("has_user_override")):
            continue
        # Merchant refunds already carry a real merchant category.
        if not is_p2p(row.get("institution", "")):
            continue

        memo = extract_memo(row["name"])
        cat = _match_to_spend(memo, row["date"], debits) if memo else None

        if cat is None and memo:
            if memo in memo_cache:
                cat = memo_cache[memo]
            else:
                try:
                    cat = ai_classify_memo(memo)
                except Exception:
                    cat = "Uncategorized"
                save_memo_category(memo, cat)
                memo_cache[memo] = cat

        if cat and str(cat).lower() not in ("uncategorized", ""):
            df.at[idx, "category"] = cat
        else:
            df.at[idx, "category"] = "Uncategorized"
            df.at[idx, "needs_review"] = True

    return df
