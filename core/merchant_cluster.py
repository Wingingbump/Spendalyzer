"""Dynamic, data-driven canonicalization of merchant names.

The structural pass (core.merchant_normalize) removes universal noise but cannot
know that "Heytea Tysons Corner" and "Heytea Mclean" are the same place without a
city list. This module figures that out from the data: the brand is the part that
stays *stable* across a merchant's transactions, while store numbers and cities
*vary*. We bucket stems by their first token and collapse each bucket to its
longest common leading token-run. No locale or brand knowledge is hardcoded.

Guards keep distinct merchants that merely share a first word apart:
  - never collapse to a 1-token name that is a 2-letter token or a US state code
    (e.g. "DC Al Toque" + "DC Brau Brewing" stay separate, not "DC");
  - if a bucket contains 2+ distinct *recurring* sub-names, keep them separate
    (e.g. "American Airlines" + "American Eagle", not "American").

Seed canonicals from the shared merchant dictionary join the buckets, so a brand
resolved once (by any user) pulls a new user's lone variant into line.
"""

from collections import Counter, defaultdict

from core.merchant_normalize import normalize_merchant, title_case, _sig_tokens, STATES

# Used by the caller to match a stem against a known dictionary canonical.
SIMILARITY_THRESHOLD = 72

# Bank-label words that are never a brand on their own — never collapse a bucket
# to a prefix made only of these (keeps "Payment To Apple" out of "Payment To").
_GENERIC_TOKENS = {
    "payment", "to", "from", "the", "withdrawal", "deposit", "transfer",
    "internet", "online", "standard", "mobile", "pymt", "of", "and", "for",
}


def bucket_key(stem: str) -> str:
    toks = _sig_tokens(stem)
    return toks[0] if toks else stem.lower().strip()


def _lcp(token_lists: list[list[str]]) -> list[str]:
    if not token_lists:
        return []
    shortest = min(token_lists, key=len)
    out: list[str] = []
    for idx, tok in enumerate(shortest):
        if all(len(tl) > idx and tl[idx].lower() == tok.lower() for tl in token_lists):
            out.append(tok)
        else:
            break
    return out


def build_canonical_map(
    raw_names: list[str],
    seed_canonicals: list[str] | None = None,
) -> dict[str, str]:
    """Map each raw descriptor -> canonical merchant name."""
    stems: dict[str, str] = {raw: normalize_merchant(raw) for raw in set(raw_names)}
    freq = Counter(stems.values())

    buckets: dict[str, set] = defaultdict(set)
    stem_raws: dict[str, list] = defaultdict(list)
    for raw, st in stems.items():
        buckets[bucket_key(st)].add(st)
        stem_raws[st].append(raw)

    # Seeds influence the common prefix but produce no mappings themselves.
    for c in (seed_canonicals or []):
        buckets[bucket_key(c)].add(c)

    result: dict[str, str] = {}
    for stemset in buckets.values():
        stems_list = sorted(stemset)
        cp = _lcp([s.split() for s in stems_list])

        collapse = bool(cp)
        # Never collapse to a prefix made only of generic bank-label words.
        if cp and all(t.lower() in _GENERIC_TOKENS for t in cp):
            collapse = False
        if cp and len(cp) == 1:
            tok = cp[0].strip("'&")
            recurring = sum(1 for s in stems_list if freq.get(s, 0) >= 2)
            if len(tok) <= 2 or tok.upper() in STATES or recurring >= 2:
                collapse = False

        if collapse:
            canon = title_case(" ".join(cp))
            for s in stems_list:
                for raw in stem_raws.get(s, []):
                    result[raw] = canon
        else:
            for s in stems_list:
                canon = title_case(s)
                for raw in stem_raws.get(s, []):
                    result[raw] = canon

    return result
