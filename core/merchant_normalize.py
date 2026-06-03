"""Universal, locale-agnostic structural cleaning of bank descriptors.

This module deliberately contains **no city list and no brand list** — nothing
specific to any user or region. It only knows structural facts that hold for any
US bank descriptor: processor prefixes, masked account tails, store numbers,
transaction codes, currency-conversion strings, "APPLE PAY ENDING IN…", the 50
state codes, corporate suffixes, and English possessive/casing rules.

The output is a *structural stem* — the leading brand run with trailing noise
removed. Brand canonicalization and variant collapsing (e.g. dropping a city the
descriptor happens to include) are NOT done here; they are learned dynamically by
core.merchant_cluster from the data itself, and by the shared merchant dictionary.
Anything still ambiguous is handed to the AI fallback and cached globally.
"""

import os
import re
import anthropic
from dotenv import load_dotenv

load_dotenv()

_client = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))

# ── Universal reference data (no locale/brand specifics) ─────────────────────

ACRONYMS = {
    "CVS", "AMC", "IRS", "NHL", "NBA", "NFL", "MLB", "USA", "ATM", "BBQ",
    "KBBQ", "VGK", "DC", "US", "UK", "TJ", "GNC", "CPA", "KFC", "IHOP", "H&M",
}
CORP_SUFFIX = {"LLC", "INC", "CO", "CORP", "LTD", "SA", "SRL", "PTE", "GMBH",
               "NV", "AG", "PLC", "LP", "SR"}
GENERIC_TRAIL = {"RESTAURANT", "ONLINE", "STORE", "STORES"}
STATES = {
    "AL", "AK", "AZ", "AR", "CA", "CO", "CT", "DE", "FL", "GA", "HI", "ID",
    "IL", "IN", "IA", "KS", "KY", "LA", "ME", "MD", "MA", "MI", "MN", "MS",
    "MO", "MT", "NE", "NV", "NH", "NJ", "NM", "NY", "NC", "ND", "OH", "OK",
    "OR", "PA", "RI", "SC", "SD", "TN", "TX", "UT", "VT", "VA", "WA", "WV",
    "WI", "WY",
}

_PREFIX_RE = re.compile(r"^[A-Za-z]{1,4}\s?\*\s*")
_TAIL_RES = [
    re.compile(r"\bAPPLE\s+PAY\s+ENDING\s+IN\s+\d+.*$", re.I),
    re.compile(r"\bENDING\s+IN\s+\d+.*$", re.I),
    re.compile(r"\d+\.\d+\s*@\s*[\d.]+.*$"),
    re.compile(r"\bAPPLE\s+PAY\b.*$", re.I),
]


def _has_vowel(w: str) -> bool:
    return any(c in "AEIOUaeiou" for c in w)


def _title_word(w: str) -> str:
    U = w.upper()
    if U in ACRONYMS:
        return U
    if w.isupper() and len(w) <= 4 and not _has_vowel(w):
        return U
    return w[:1].upper() + w[1:].lower()


def title_case(s: str) -> str:
    return " ".join(_title_word(w) for w in s.split() if w)


def _looks_like_person(s: str) -> bool:
    s = s.strip()
    if not s or re.search(r"\d", s):
        return False
    parts = s.split()
    if not (1 < len(parts) <= 3):
        return False
    BUSINESS = {"llc", "inc", "co", "corp", "store", "shop", "cafe", "grill",
                "restaurant", "market", "bar"}
    return not any(b in s.lower().split() for b in BUSINESS)


def normalize_merchant(name: str) -> str:
    """Universal structural stem. Pure function — no network, no locale data."""
    raw = (name or "").strip()
    if not raw:
        return ""

    # P2P peer payment: "Firstname Lastname \"memo\"" -> the person.
    m = re.match(r'^(.+?)\s*["“‘]', raw)
    if m and _looks_like_person(m.group(1)):
        return title_case(m.group(1).strip())

    # Strip processor prefix + known trailing noise phrases.
    s = _PREFIX_RE.sub("", raw)
    for tre in _TAIL_RES:
        s = tre.sub("", s)

    # Normalize separators; keep word chars, & and apostrophes.
    s = re.sub(r"[._/\\]+", " ", s)
    s = re.sub(r"[^A-Za-z0-9 &']", " ", s)
    tokens = s.split()

    def _is_noise(t: str) -> bool:
        return (
            t.isdigit()
            or (set(t) <= set("Xx") and len(t) >= 4)
            or (bool(re.search(r"\d", t)) and bool(re.search(r"[A-Za-z]", t)) and len(t) >= 5)
        )

    # Keep the leading brand run; stop at the first noise boundary. A state code
    # only ends the brand when it's *trailing* (last token or followed by noise) —
    # otherwise a 2-letter token like "AL" in "DC AL TOQUE" is part of the name.
    kept: list[str] = []
    for idx, tok in enumerate(tokens):
        U = tok.upper().strip("'&")
        has_alpha = any(c.isalpha() for c in tok)
        nxt = tokens[idx + 1] if idx + 1 < len(tokens) else None
        trailing_state = U in STATES and (nxt is None or _is_noise(nxt) or nxt.upper() in STATES)

        if kept:
            if trailing_state:
                if len(kept) > 1:
                    kept.pop()  # drop the city that precedes the state
                break
            if _is_noise(tok):
                break
            kept.append(tok)
        else:
            # Keep a leading number when it's part of the brand ("7 Eleven",
            # "7 Brew Coffee"); skip leading store-ids / masks / codes.
            if tok.isdigit() and nxt is not None and any(c.isalpha() for c in nxt):
                kept.append(tok)
            elif _is_noise(tok):
                continue
            elif has_alpha:
                kept.append(tok)

    # Trim trailing corporate / generic suffixes, single letters, and
    # bank-truncated generic words (e.g. "RESTAURA" for RESTAURANT).
    while len(kept) > 1:
        last = kept[-1].upper().strip("'&")
        if (
            last in (CORP_SUFFIX | GENERIC_TRAIL)
            or len(last) == 1
            or (len(last) >= 4 and any(g.startswith(last) for g in ("RESTAURANT",)))
        ):
            kept.pop()
        else:
            break

    if not kept:
        kept = [t for t in tokens if any(c.isalpha() for c in t)][:3]
    if not kept:
        return raw

    return title_case(" ".join(kept))


# ── Low-confidence gate ──────────────────────────────────────────────────────

_GENERIC = {"Llc", "Inc", "Co", "Store", "Stores", "Restaurant", "Online",
            "The", "To", "From", "Sa", "Sr", "Us"}
_STOP = {"tst", "sq", "sp", "pp", "dda", "the", "to", "from", "us", "online",
         "restaurant", "la", "de", "and", "of"}


def _sig_tokens(s: str) -> list[str]:
    return [t for t in re.findall(r"[A-Za-z][A-Za-z'&]+", s.lower())
            if t not in _STOP and len(t) > 1]


def looks_low_confidence(raw: str, normalized: str) -> bool:
    if not normalized or len(normalized) < 3:
        return True
    if re.search(r"[\d*@]", normalized):
        return True
    toks = normalized.split()
    if len(toks) == 1 and toks[0] in _GENERIC:
        return True
    if len(toks) <= 2 and all(t in _GENERIC for t in toks):
        return True
    if not re.match(r'^.+?\s*["“‘]', raw):
        rt = _sig_tokens(raw)
        ct = set(_sig_tokens(normalized))
        if rt and rt[0] not in ct and len(toks) <= 2:
            return True
    return False


# ── AI fallback ──────────────────────────────────────────────────────────────

def ai_clean_merchant(raw_name: str, normalized: str) -> str:
    prompt = f"""Clean up this bank transaction descriptor into a short, readable business name.

Raw: "{raw_name}"
Rule-based attempt (may be wrong): "{normalized}"

Rules:
- Return ONLY the merchant/business name, 1-3 words.
- Keep the brand. Never drop the brand word to keep a generic word
  (e.g. "CHIPOTLE MEXICAN GRILL" is "Chipotle", NOT "Mexican Grill").
- Remove store numbers, cities, states, phone/order/transaction codes,
  "APPLE PAY ENDING IN…", currency conversions.
- Keep apostrophes correct: "Lei'd", "Joe's", "McDonald's", "Buc-ee's".
- Preserve real acronyms (CVS, AMC, IRS, NHL, H&M).
- Title case unless it's an acronym.

Return ONLY the clean name, nothing else."""

    response = _client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=32,
        messages=[{"role": "user", "content": prompt}],
    )
    return response.content[0].text.strip().strip('"')
