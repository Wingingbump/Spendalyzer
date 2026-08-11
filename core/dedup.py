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

# Descriptor keywords that indicate an INTERNAL transfer or a credit-card/loan
# payment — never a P2P platform name. P2P platforms were removed: a Zelle/Venmo
# payment to a person is real spending, not a transfer, and must not vanish from
# reports. This list is only a fallback for rows Plaid didn't enrich with a PFC.
TRANSFER_KEYWORDS = [
    "automatic payment", "credit card payment",
    "internet payment", "online payment", "ach transfer", "mobile payment",
    "mobile pymt",
    "bank transfer", "wire transfer", "payment thank",
    "bill pay", "online transfer", "autopay",
    "withdrawal to", "transfer to", "transfer from",
    "deposit from", "fid bkg svc",
    "standard transfer", "instant transfer",
    "paycheck percentage",
]

TRANSFER_CATEGORIES = []

P2P_INSTITUTIONS = P2P_PLATFORMS

# Aliases used to recognise which P2P platform a descriptor or institution refers
# to, tolerating variants like "Venmo - Personal", "CASH APP", or "PP*". An alias
# ending in "*" is a processor PREFIX and is matched only at the start of the text
# — otherwise the short "pp*" would substring-match unrelated "APP*…" descriptors.
_PLATFORM_ALIASES = {
    "venmo":      ["venmo"],
    "paypal":     ["paypal", "pp*"],
    "cashapp":    ["cash app", "cashapp", "cash-app"],
    "zelle":      ["zelle"],
    "apple cash": ["apple cash"],
}


def platform_of(text: str) -> str | None:
    """Return the canonical P2P platform named in `text`, or None."""
    t = (text or "").lower().strip()
    for plat, aliases in _PLATFORM_ALIASES.items():
        for a in aliases:
            if a.endswith("*"):
                if t.startswith(a):
                    return plat
            elif a in t:
                return plat
    return None


INSTITUTION_PRIORITY = {
    "discover":    2,
    "capital one": 2,
    "venmo":       1,
    "cashapp":     1,
    "paypal":      1,
}

# ── Personal Finance Category (PFC) signals ────────────────────────────────────
# PFC is Plaid's classification of a transaction's intent and is the primary,
# most reliable transfer signal — descriptor keywords are only a fallback for
# rows Plaid didn't enrich. Ingested in services/pull.py.

# Detailed PFC types meaning "money moved between the user's OWN accounts, or a
# credit-card payment" — never spending, never income. A card payment counts as a
# transfer so it doesn't double-count spend already recorded on the card itself.
PFC_TRANSFER_DETAILED = {
    "TRANSFER_IN_ACCOUNT_TRANSFER",
    "TRANSFER_OUT_ACCOUNT_TRANSFER",
    "TRANSFER_IN_SAVINGS",
    "TRANSFER_OUT_SAVINGS",
    "TRANSFER_IN_INVESTMENT_AND_RETIREMENT_FUNDS",
    "TRANSFER_OUT_INVESTMENT_AND_RETIREMENT_FUNDS",
    "LOAN_PAYMENTS_CREDIT_CARD_PAYMENT",
}
# PFC primary buckets that clearly mean spending or income. When Plaid is
# confident a row is one of these, a descriptor keyword must NOT override it into
# a transfer (e.g. "SQ *…PAYMENT" that is really a coffee shop). Note: other
# LOAN_PAYMENTS (mortgage, auto, student) are intentionally real spend, not here
# and not treated as transfers.
PFC_NONTRANSFER_PRIMARY = {
    "FOOD_AND_DRINK", "GENERAL_MERCHANDISE", "GENERAL_SERVICES",
    "ENTERTAINMENT", "MEDICAL", "PERSONAL_CARE", "RENT_AND_UTILITIES",
    "TRANSPORTATION", "TRAVEL", "HOME_IMPROVEMENT",
    "GOVERNMENT_AND_NON_PROFIT", "BANK_FEES", "INCOME",
}
# Confidence levels trusted for an automatic decision in either direction.
PFC_TRUSTED_CONFIDENCE = {"VERY_HIGH", "HIGH"}


def flag_transfers(df: pd.DataFrame) -> pd.DataFrame:
    """Flag internal transfers and credit-card payments — movements that are
    neither spending nor income.

    A P2P payment to a *person* (Zelle/Venmo to a friend) is deliberately NOT a
    transfer here: it is real spending unless flag_paired_transfers later matches
    it to another of the user's own accounts. Cashing a P2P *balance* out to your
    own bank is still an internal move and is kept.

    Precedence: trust Plaid's PFC when present and confident; fall back to
    descriptor keywords only for rows Plaid didn't enrich.
    """
    df = df.copy()
    name_l = df["name"].fillna("").str.lower()
    inst_l = df["institution"].fillna("").str.lower()

    # ── PFC signal (primary) ──
    if "pfc_detailed" in df.columns:
        detailed = df["pfc_detailed"].fillna("")
        primary = (df["pfc_primary"].fillna("") if "pfc_primary" in df.columns
                   else pd.Series("", index=df.index))
        confidence = (df["pfc_confidence"].fillna("") if "pfc_confidence" in df.columns
                      else pd.Series("", index=df.index))
        trusted = confidence.isin(PFC_TRUSTED_CONFIDENCE)
        pfc_transfer = detailed.isin(PFC_TRANSFER_DETAILED) & trusted
        pfc_shields_spend = primary.isin(PFC_NONTRANSFER_PRIMARY) & trusted
    else:
        pfc_transfer = pd.Series(False, index=df.index)
        pfc_shields_spend = pd.Series(False, index=df.index)

    # ── Keyword fallback (no P2P platforms, no person-name guessing) ──
    keyword_match = name_l.apply(lambda n: any(kw in n for kw in TRANSFER_KEYWORDS))

    # Cashing a P2P balance out to your own bank IS an internal move (distinct
    # from paying a person). Gated to P2P institutions + balance-movement words.
    # Substring (not exact) institution match so multi-word names like
    # "Venmo - Personal" are recognised — same rule flag_paired_transfers uses.
    on_p2p_account = inst_l.apply(lambda s: any(p in s for p in P2P_INSTITUTIONS))
    p2p_balance_transfer = (
        on_p2p_account &
        name_l.apply(lambda n: any(kw in n for kw in [
            "transfer", "bank", "standard transfer", "instant transfer",
            "cashout", "withdrawal", "deposit", "reload",
        ]))
    )

    keyword_transfer = (keyword_match | p2p_balance_transfer) & ~pfc_shields_spend

    df["is_transfer"] = pfc_transfer | keyword_transfer
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


# ── Layer 2.6: P2P-mirror detection (funded-by-card double entries) ─────────────

def flag_p2p_mirror_duplicates(
    df: pd.DataFrame,
    amount_tolerance: float = 0.01,
    date_window_days: int = 5,
) -> pd.DataFrame:
    """Collapse the two-sided entry a P2P payment creates when it's funded by a
    linked card/bank.

    A single Venmo/PayPal/Cash App payment shows up twice:
      1. on the P2P account, with the real payee and memo (e.g. Venmo →
         'Courtney L "borgerking"'), and
      2. on the funding card, as a generic settlement line (e.g. Capital One →
         'Venmo').
    Name-similarity dedup can't pair these (the descriptors share no words), so we
    match on the platform link + amount + date instead:

      • Funding a payment  — card-side DEBIT ↔ same-amount P2P-account DEBIT.
        The card line is hidden (is_duplicate); the detailed P2P row is kept.
      • Balance cash-out    — card-side CREDIT ↔ P2P-account balance transfer of
        the same magnitude. The card credit is an internal move → is_transfer.

    The P2P side always carries the useful detail, so it is the survivor.
    """
    df = df.copy()
    if df.empty or "type" not in df.columns:
        return df
    # Degrade gracefully if an upstream caller hasn't initialised the flag columns.
    for _col in ("is_duplicate", "is_transfer"):
        if _col not in df.columns:
            df[_col] = False

    plat_name = df["name"].fillna("").apply(platform_of)         # platform the descriptor names
    plat_inst = df["institution"].fillna("").apply(platform_of)  # platform of the account itself

    # Card-side settlement lines: the descriptor names a platform, but the account
    # itself is not that platform (i.e. the funding card/bank, not Venmo).
    card_side = df[plat_name.notna() & plat_inst.isna() & ~df["is_duplicate"].fillna(False)]
    if card_side.empty:
        return df

    p2p_idx = df.index[plat_inst.notna()]
    if len(p2p_idx) == 0:
        return df

    consumed: set = set()
    for i, r in card_side.sort_values("date").iterrows():
        plat = plat_name[i]
        within_window = (df.loc[p2p_idx, "date"] - r["date"]).abs() <= pd.Timedelta(days=date_window_days)
        same_plat = plat_inst.loc[p2p_idx] == plat
        base = p2p_idx[same_plat.values & within_window.values]
        base = [j for j in base if j not in consumed]
        if not base:
            continue
        cand = df.loc[base]

        if r["type"] == "debit":
            # Funding a payment — match a same-amount P2P debit that isn't itself a
            # balance transfer. Hide the generic card line; keep the detailed row.
            m = cand[
                (cand["type"] == "debit")
                & (~cand["is_transfer"].fillna(False))
                & ((cand["amount"] - r["amount"]).abs() <= amount_tolerance)
            ]
            if m.empty:
                continue
            j = (m["date"] - r["date"]).abs().sort_values().index[0]
            df.at[i, "is_duplicate"] = True
            df.at[i, "dedup_reason"] = f"p2p funding ({plat})"
            consumed.add(j)
        else:
            # Card credit — a P2P balance cash-out landing on the card. Match the
            # outgoing balance transfer by magnitude; flag the card side a transfer.
            target = abs(float(r["amount"]))
            m = cand[
                (cand["type"] == "debit")
                & ((cand["amount"] - target).abs() <= amount_tolerance)
                & (cand["is_transfer"].fillna(False)
                   | cand["name"].fillna("").str.lower().str.contains("transfer"))
            ]
            if m.empty:
                continue
            j = (m["date"] - r["date"]).abs().sort_values().index[0]
            df.at[i, "is_transfer"] = True
            df.at[i, "dedup_reason"] = f"p2p cashout ({plat})"
            # Flag the P2P leg too — self-contained rather than relying on
            # flag_transfers having already caught its descriptor.
            df.at[j, "is_transfer"] = True
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

    # Layer 1: PFC + keyword transfer detection. Authoritative and recomputed on
    # every run, so it is deliberately NOT cached — otherwise a stale per-row
    # decision (e.g. a Zelle payment flagged as a transfer under the old rules)
    # would resurrect itself from the cache and re-hide the transaction.
    df = flag_transfers(df)
    df.loc[df["is_transfer"], "dedup_reason"] = "transfer"

    # Layer 2: Collapse P2P payments that also appear as a generic funding line on
    # the linked card (e.g. Capital One "Venmo" mirroring a Venmo payment). Runs
    # before paired detection so the hidden card line is excluded from its pool.
    df = flag_p2p_mirror_duplicates(df)

    # Layer 2.5: Catch internal transfer pairs the keyword pass missed (paycheck
    # splits, credit-card payments, inter-account moves). Pair-dependent, so also
    # recomputed each run rather than cached.
    df = flag_paired_transfers(df)

    # Layer 3 + 4: Cross-institution duplicate detection. Only the expensive AI
    # duplicate arbitration is cached, keyed on the transaction pair.
    cache = load_dedup_cache()
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