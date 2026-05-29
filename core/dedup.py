import os
import csv
import json
import hashlib
import anthropic
import pandas as pd
import re as _re
from datetime import timedelta
from rapidfuzz import fuzz
from dotenv import load_dotenv
from core.db import load_dedup_cache, save_dedup_cache, upsert_dedup_entry

load_dotenv()

DEDUP_CACHE_PATH = "data/dedup_cache.csv"
client = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))

# ── Transfer keywords & config ─────────────────────────────────────────────────

P2P_PLATFORMS = ["venmo", "paypal", "zelle", "cashapp", "apple cash"]
TRANSFER_KEYWORDS = [
    "automatic payment", "credit card payment",
    "internet payment", "online payment", "ach transfer", "mobile payment",
    "mobile pymt",
    "bank transfer", "wire transfer", "payment thank",
    "bill pay", "online transfer", "autopay",
    "withdrawal to", "transfer to", "transfer from",
    "deposit from", "fid bkg svc", "discover",
    "standard transfer", "instant transfer",
    "paycheck percentage",
] + P2P_PLATFORMS

TRANSFER_CATEGORIES = []

P2P_INSTITUTIONS = P2P_PLATFORMS

INSTITUTION_PRIORITY = {
    "discover":    2,
    "capital one": 2,
    "venmo":       1, 
    "cashapp":     1,
    "paypal":      1,
}

ZELLE_INSTITUTIONS = ["capital one", "discover", "chase", "bank of america"]
BUSINESS_WORDS = ["restaurant", "cafe", "shop", "store", "market", "grill",
                  "bar", "hotel", "inn", "llc", "inc", "corp", "co ",
                  "tst", "amc", "metro", "google", "apple", "amazon"]


def looks_like_person_name(name: str) -> bool:
    """Heuristic — two capitalized words, no business indicators."""
    name_lower = name.lower()
    if any(bw in name_lower for bw in BUSINESS_WORDS):
        return False
    # Match "Firstname Lastname" pattern
    parts = name.strip().split()
    if len(parts) == 2 and all(p.isalpha() and p[0].isupper() for p in parts):
        return True
    return False


def flag_transfers(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()

    keyword_match = df["name"].str.lower().apply(
        lambda n: any(kw in n for kw in TRANSFER_KEYWORDS)
    )

    p2p_balance_transfer = (
        df["institution"].str.lower().isin(P2P_INSTITUTIONS) &
        df["name"].str.lower().apply(
            lambda n: any(kw in n for kw in [
                "transfer", "bank", "standard transfer",
                "instant transfer", "cashout", "withdrawal",
                "deposit", "reload"
            ])
        )
    )

    # Zelle payments on bank accounts show as person names. Credit cards can't
    # send Zelle, so gate on account subtype — otherwise 2-word merchant names
    # like "Shake Shack" or "Rice Culture" on a credit card get false-flagged.
    if "account_subtype" in df.columns:
        on_bank_account = df["account_subtype"].fillna("").str.lower().isin(
            ["checking", "savings", "money market"]
        )
    else:
        on_bank_account = pd.Series(True, index=df.index)  # backwards compat
    zelle_payment = (
        df["institution"].str.lower().isin(ZELLE_INSTITUTIONS) &
        on_bank_account &
        df["name"].apply(looks_like_person_name)
    )

    df["is_transfer"] = keyword_match | p2p_balance_transfer | zelle_payment
    return df

# ── Dedup cache ────────────────────────────────────────────────────────────────

def make_fingerprint(row: pd.Series) -> str:
    """Stable hash based only on immutable transaction properties."""
    key = f"{row['name'].lower().strip()}|{row['amount']}|{row['institution'].lower()}"
    return hashlib.md5(key.encode()).hexdigest()


def make_pair_fingerprint(row_a: pd.Series, row_b: pd.Series) -> str:
    """Stable fingerprint for a cross-institution pair."""
    keys = sorted([
        f"{row_a['name'].lower()}|{row_a['amount']}|{row_a['institution'].lower()}",
        f"{row_b['name'].lower()}|{row_b['amount']}|{row_b['institution'].lower()}"
    ])
    return hashlib.md5("|".join(keys).encode()).hexdigest()


# ── Layer 1: Cache lookup ──────────────────────────────────────────────────────

def check_cache(fingerprint: str, cache: dict) -> dict | None:
    """Returns cached decision if exists, else None."""
    return cache.get(fingerprint)


# ── Layer 2: Rule-based transfer detection ─────────────────────────────────────

def rule_based_transfer(row: pd.Series) -> tuple[bool, str] | None:
    name_lower = row["name"].lower()

    if any(kw in name_lower for kw in TRANSFER_KEYWORDS):
        return True, "keyword match"

    return None


# ── Layer 2.5: Paired internal-transfer detection ──────────────────────────────

def flag_paired_transfers(
    df: pd.DataFrame,
    amount_tolerance: float = 0.50,
    date_window_days: int = 3,
) -> pd.DataFrame:
    """
    Catch transfer pairs that the keyword pass misses: same-magnitude, opposite-sign
    transactions within a few days, on DIFFERENT plaid accounts. Covers paycheck
    auto-splits, credit-card payments, and inter-account moves regardless of merchant
    name. The different-account constraint excludes refund/repurchase, which lands on
    the same account.

    Sibling pool includes already-flagged transfers — if the keyword pass caught one
    leg (e.g. "CAPITAL ONE MOBILE PYMT"), the matching checking-side outflow
    ("CAPITAL ONE") still gets paired and flagged.
    """
    df = df.copy()
    if df.empty or "plaid_account_id" not in df.columns:
        return df

    # Exclude P2P platforms — leftover Venmo/PayPal/etc. rows that weren't already
    # flagged are person-to-person payments, not user-owned account transfers, and
    # would match unrelated same-amount purchases by coincidence. Substring match
    # to catch institution names like "Venmo - Personal".
    inst_lower = df["institution"].str.lower()
    non_p2p = ~inst_lower.apply(lambda s: any(p in s for p in P2P_INSTITUTIONS))
    pool = df[non_p2p & ~df["is_duplicate"]]
    unflagged = pool[~pool["is_transfer"]]
    if unflagged.empty:
        return df

    consumed: set = set()  # indices already part of a confirmed pair

    for i, row in unflagged.sort_values("date").iterrows():
        if i in consumed:
            continue
        date_min = row["date"] - timedelta(days=date_window_days)
        date_max = row["date"] + timedelta(days=date_window_days)
        acct = row.get("plaid_account_id", "") or ""
        opposite = "credit" if row["type"] == "debit" else "debit"

        siblings = pool[
            (pool.index != i) &
            (~pool.index.isin(consumed)) &
            (pool["type"] == opposite) &
            (pool["plaid_account_id"].fillna("") != acct) &
            (pool["date"] >= date_min) &
            (pool["date"] <= date_max) &
            ((pool["amount"] + row["amount"]).abs() <= amount_tolerance)
        ]
        if siblings.empty:
            continue

        diffs = (siblings["date"] - row["date"]).abs()
        j = diffs.sort_values().index[0]

        df.at[i, "is_transfer"] = True
        df.at[i, "dedup_reason"] = "paired transfer"
        consumed.add(i)
        consumed.add(j)

    return df


# ── Layer 3: Cross-institution duplicate detection ─────────────────────────────

def find_potential_duplicates(
    df: pd.DataFrame,
    amount_tolerance: float = 0.01,
    date_window_days: int = 2,
    name_similarity_threshold: int = 80
) -> list[tuple]:
    """
    Returns list of (index_a, index_b) pairs that are potential duplicates.

    Two cases:
    - Cross-institution: same amount + high name similarity within date window.
      Catches Venmo/card double-entries.
    - Same-institution: exact amount + very high name similarity within a 5-day
      window AND at least one side is pending. Catches the Plaid pending→posted
      ID swap (old pending ID stays in DB, new posted ID gets inserted).
    """
    pairs = []
    debits = df[df["type"] == "debit"].copy()

    seen = set()
    for i, row in debits.iterrows():
        # ── Cross-institution ────────────────────────────────────────────────
        date_min = row["date"] - timedelta(days=date_window_days)
        date_max = row["date"] + timedelta(days=date_window_days)

        cross_candidates = debits[
            (debits.index != i) &
            (debits["institution"] != row["institution"]) &
            (debits["date"] >= date_min) &
            (debits["date"] <= date_max) &
            (abs(debits["amount"] - row["amount"]) <= amount_tolerance)
        ]
        for j, candidate in cross_candidates.iterrows():
            similarity = fuzz.token_sort_ratio(row["name"].lower(), candidate["name"].lower())
            if similarity >= name_similarity_threshold:
                pair = tuple(sorted([i, j]))
                if pair not in seen:
                    seen.add(pair)
                    pairs.append(pair)

        # ── Same-institution pending→posted ──────────────────────────────────
        # Only flag if at least one side is still pending — otherwise two
        # legitimate same-bank same-merchant purchases would get flagged.
        if not row.get("pending", False):
            continue

        same_candidates = debits[
            (debits.index != i) &
            (debits["institution"] == row["institution"]) &
            (debits["date"] >= row["date"] - timedelta(days=5)) &
            (debits["date"] <= row["date"] + timedelta(days=5)) &
            (abs(debits["amount"] - row["amount"]) <= amount_tolerance)
        ]
        for j, candidate in same_candidates.iterrows():
            similarity = fuzz.token_sort_ratio(row["name"].lower(), candidate["name"].lower())
            if similarity >= 90:  # higher bar for same-institution to avoid false positives
                pair = tuple(sorted([i, j]))
                if pair not in seen:
                    seen.add(pair)
                    pairs.append(pair)

    return pairs


# ── Layer 4: AI arbitration for ambiguous pairs ────────────────────────────────

def ai_arbitrate_pair(row_a: pd.Series, row_b: pd.Series) -> tuple[bool, str]:
    """
    Asks Claude whether two transactions across institutions are duplicates.
    """
    same_institution = row_a["institution"].lower() == row_b["institution"].lower()
    p2p_institutions = [i for i in [row_a["institution"], row_b["institution"]]
                        if i.lower() in P2P_INSTITUTIONS]
    card_institutions = [i for i in [row_a["institution"], row_b["institution"]]
                         if i.lower() not in P2P_INSTITUTIONS]

    context = ""
    if same_institution:
        pending_a = row_a.get("pending", False)
        pending_b = row_b.get("pending", False)
        context = f"""
IMPORTANT CONTEXT: Both transactions are from the same institution ({row_a['institution']}).
One is pending={pending_a}, the other is pending={pending_b}.
Banks often show a pending transaction and then replace it with a settled transaction
that gets a new ID — the old pending entry can linger as a duplicate row in the database.
If amounts match exactly, names are very similar, and dates are within a few days,
this is almost certainly a pending→posted duplicate."""
    elif p2p_institutions and card_institutions:
        context = f"""
IMPORTANT CONTEXT: {p2p_institutions[0]} is a P2P payment platform in this user's setup.
When {p2p_institutions[0]} is funded by {card_institutions[0]}, the same transaction
appears on BOTH accounts. If the merchant, amount, and date match closely across
a P2P platform and a bank/credit card, treat it as a duplicate — keep the card transaction."""

    prompt = f"""You are analyzing bank transactions to detect duplicates.
{context}

Transaction A:
- Name: {row_a['name']}
- Amount: ${row_a['amount']}
- Date: {row_a['date']}
- Institution: {row_a['institution']}
- Pending: {row_a.get('pending', False)}

Transaction B:
- Name: {row_b['name']}
- Amount: ${row_b['amount']}
- Date: {row_b['date']}
- Institution: {row_b['institution']}
- Pending: {row_b.get('pending', False)}

Are these the same transaction appearing twice (duplicate)?

Rules:
- Same institution + one pending + matching amount/name/date = almost certainly duplicate
- Cross-institution P2P + card with matching merchant/amount/date = duplicate
- Peer payments between people are NOT duplicates even if amounts match
- Be decisive — if evidence strongly suggests duplicate, mark it as one

Return ONLY a JSON object:
{{"is_duplicate": true, "reason": "brief reason"}}"""

    response = client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=256,
        messages=[{"role": "user", "content": prompt}]
    )

    raw = response.content[0].text.strip()
    if raw.startswith("```"):
        raw = raw.split("```")[1]
        if raw.startswith("json"):
            raw = raw[4:]
        raw = raw.strip()

    result = json.loads(raw)
    return result["is_duplicate"], result["reason"]


# ── Main pipeline ──────────────────────────────────────────────────────────────

def apply_dedup(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df["is_duplicate"] = False
    df["dedup_reason"] = ""
    df["fingerprint"] = df.apply(make_fingerprint, axis=1)

    # Layer 1: flag_transfers handles all transfer detection holistically
    df = flag_transfers(df)
    df["dedup_reason"] = df.apply(
        lambda r: "transfer" if r["is_transfer"] else "", axis=1
    )

    cache = load_dedup_cache()

    # Layer 2: Cache and per-row rule check for anything not already flagged
    for i, row in df.iterrows():
        if df.at[i, "is_transfer"]:
            # Already flagged — cache the decision
            fp = row["fingerprint"]
            if not check_cache(fp, cache):
                upsert_dedup_entry(fp, False, True, "rule", df.at[i, "dedup_reason"])
                cache[fp] = {"is_duplicate": False, "is_transfer": True,
                              "source": "rule", "reason": df.at[i, "dedup_reason"]}
            continue

        fp = row["fingerprint"]
        cached = check_cache(fp, cache)
        if cached:
            df.at[i, "is_transfer"] = cached["is_transfer"]
            df.at[i, "is_duplicate"] = cached["is_duplicate"]
            df.at[i, "dedup_reason"] = cached["reason"]

    # Layer 2.5: Catch internal transfer pairs the keyword pass missed (paycheck
    # splits, credit-card payments, etc.). Not cached on individual fingerprint
    # because the decision is pair-dependent, not row-intrinsic.
    df = flag_paired_transfers(df)

    # Layer 3 + 4: Cross-institution duplicate detection
    pairs = find_potential_duplicates(df)

    for idx_a, idx_b in pairs:
        row_a = df.loc[idx_a]
        row_b = df.loc[idx_b]
        pair_fp = make_pair_fingerprint(row_a, row_b)

        cached_pair = check_cache(pair_fp, cache)
        if cached_pair:
            is_dup = cached_pair["is_duplicate"]
            reason = cached_pair["reason"]
        else:
            try:
                print(f"AI arbitrating: '{row_a['name']}' ({row_a['institution']}) "
                      f"vs '{row_b['name']}' ({row_b['institution']})")
                is_dup, reason = ai_arbitrate_pair(row_a, row_b)
                upsert_dedup_entry(pair_fp, is_dup, False, "ai", reason)
                cache[pair_fp] = {"is_duplicate": is_dup, "is_transfer": False,
                                   "source": "ai", "reason": reason}
            except Exception as e:
                print(f"AI arbitration failed: {e} — defaulting to not duplicate")
                is_dup, reason = False, "ai_failed"

        if is_dup:
            priority_a = INSTITUTION_PRIORITY.get(row_a["institution"].lower(), 99)
            priority_b = INSTITUTION_PRIORITY.get(row_b["institution"].lower(), 99)
            flag_idx = idx_b if priority_a <= priority_b else idx_a
            df.at[flag_idx, "is_duplicate"] = True
            df.at[flag_idx, "dedup_reason"] = reason

    return df


def get_clean_spending(df: pd.DataFrame) -> pd.DataFrame:
    """Returns only real, non-duplicate, non-transfer debits."""
    return df[
        (df["type"] == "debit") &
        (~df["is_transfer"]) &
        (~df["is_duplicate"])
    ].copy()


def flag_potential_duplicates(df: pd.DataFrame, dismissed_pairs: set) -> pd.DataFrame:
    """
    Scan for same-account, same-amount, close-date, similar-name transaction pairs
    that aren't already flagged as transfers or confirmed duplicates, and that the
    user hasn't already dismissed.

    Adds two columns:
      - is_potential_duplicate (bool)
      - potential_dup_of (str | None) — JSON: {id, name, date, amount}

    Only debits are checked. The later transaction in the pair is flagged (it's more
    likely to be the newer/duplicate ID). If dates are equal, the one with the
    higher numeric ID is flagged.
    """
    import json
    from collections import defaultdict

    df = df.copy()
    df["is_potential_duplicate"] = False
    df["potential_dup_of"] = None

    # Only scan clean debits — transfers and confirmed duplicates are already handled
    mask = (df["type"] == "debit") & (~df["is_transfer"]) & (~df["is_duplicate"])
    candidates = df[mask]

    # Bucket by (plaid_account_id, rounded_amount) for O(n) grouping
    buckets = defaultdict(list)
    for idx, row in candidates.iterrows():
        acct = row.get("plaid_account_id", "")
        if not acct:
            continue
        key = (acct, round(float(row["amount"]), 2))
        buckets[key].append(idx)

    flagged = set()

    for indices in buckets.values():
        if len(indices) < 2:
            continue

        for i in range(len(indices)):
            for j in range(i + 1, len(indices)):
                idx_a, idx_b = indices[i], indices[j]
                row_a, row_b = df.loc[idx_a], df.loc[idx_b]

                pair = frozenset([str(row_a["id"]), str(row_b["id"])])
                if pair in dismissed_pairs:
                    continue

                days_diff = abs((row_a["date"] - row_b["date"]).days)
                if days_diff > 5:
                    continue

                sim = fuzz.token_sort_ratio(
                    str(row_a["name"]).lower(),
                    str(row_b["name"]).lower()
                )
                if sim < 80:
                    continue

                # Flag the later transaction (more likely to be the new ID);
                # tie-break on id so we're deterministic
                if row_a["date"] < row_b["date"]:
                    flag_idx, other_idx = idx_b, idx_a
                elif row_b["date"] < row_a["date"]:
                    flag_idx, other_idx = idx_a, idx_b
                else:
                    flag_idx, other_idx = (idx_b, idx_a) if str(row_a["id"]) < str(row_b["id"]) else (idx_a, idx_b)

                if flag_idx not in flagged:
                    flagged.add(flag_idx)
                    other = df.loc[other_idx]
                    other_date = other["date"]
                    if hasattr(other_date, "date"):
                        other_date = other_date.date().isoformat()
                    else:
                        other_date = str(other_date)[:10]
                    df.at[flag_idx, "is_potential_duplicate"] = True
                    df.at[flag_idx, "potential_dup_of"] = json.dumps({
                        "id":     str(other["id"]),
                        "name":   str(other["name"]),
                        "date":   other_date,
                        "amount": float(other["amount"]),
                    })

    return df


def get_dedup_summary(df: pd.DataFrame) -> dict:
    """Useful for dashboard — shows what got filtered and why."""
    if df.empty or "is_transfer" not in df.columns or "is_duplicate" not in df.columns:
        return {
            "total_transactions": 0,
            "transfers_flagged": 0,
            "duplicates_flagged": 0,
            "clean_transactions": 0,
            "flagged_detail": []
        }
    flagged = df[df["is_transfer"] | df["is_duplicate"]].copy()
    if "dedup_reason" not in flagged.columns:
        flagged["dedup_reason"] = ""
    return {
        "total_transactions": len(df),
        "transfers_flagged": int(df["is_transfer"].sum()),
        "duplicates_flagged": int(df["is_duplicate"].sum()),
        "clean_transactions": int((~df["is_transfer"] & ~df["is_duplicate"]).sum()),
        "flagged_detail": flagged[
            ["date", "name", "institution", "amount", "is_transfer", "is_duplicate", "dedup_reason"]
        ].to_dict("records")
    }