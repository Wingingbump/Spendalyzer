CATEGORIES = [
    "Food & Drink",
    "Transport",
    "Shopping",
    "Subscriptions",
    "Health",
    "Utilities",
    "Travel",
    "Payments",
    "Income / Interest",
    "Other"
]


def apply_categories(df):
    """Resolve df['name'] -> df['merchant_normalized'] dynamically.

    Pipeline (no hardcoded brands or locations):
      1. Universal structural stem (core.merchant_normalize).
      2. Cross-transaction clustering collapses variants ("Heytea Tysons" +
         "Heytea Mclean" -> "Heytea"), seeded by the shared merchant dictionary
         so a brand resolved once snaps lone variants into line.
      3. AI fallback for residual low-confidence names — at most once per unique
         descriptor (then reused from the global cache).
    Resolved names feed back into the shared dictionary so every user benefits.
    """
    from rapidfuzz import fuzz
    from core.db import (
        load_normalization_cache, save_normalization_entry,
        load_merchant_dictionary, upsert_merchant_dictionary,
    )
    from core.merchant_normalize import (
        normalize_merchant, looks_low_confidence, ai_clean_merchant,
    )
    from core.merchant_cluster import build_canonical_map, bucket_key, SIMILARITY_THRESHOLD

    df = df.copy()
    if "name" not in df.columns or df.empty:
        df["merchant_normalized"] = []
        return df

    norm_cache = load_normalization_cache()
    brand_dict = load_merchant_dictionary()

    uniq = [n for n in df["name"].dropna().unique().tolist()]
    cluster_map = build_canonical_map(uniq, seed_canonicals=list(brand_dict.values()))

    resolved: dict[str, str] = {}
    dict_updates: dict[str, str] = {}

    for raw in uniq:
        stem = normalize_merchant(raw)
        bkey = bucket_key(stem)

        # 1. Shared dictionary wins when the stem clearly matches a known brand.
        cand = None
        if bkey in brand_dict and fuzz.token_set_ratio(
            stem.lower(), brand_dict[bkey].lower()
        ) >= SIMILARITY_THRESHOLD:
            cand = brand_dict[bkey]
        # 2. Otherwise the clustering result for this corpus.
        if cand is None:
            cand = cluster_map.get(raw, stem)

        # 3. Low-confidence -> reuse a prior cached/AI result, else call AI once.
        if looks_low_confidence(raw, cand):
            if raw in norm_cache:
                cand = norm_cache[raw]
            else:
                try:
                    ai = ai_clean_merchant(raw, cand)
                    if ai:
                        cand = ai
                except Exception:
                    pass

        resolved[raw] = cand
        if norm_cache.get(raw) != cand:
            save_normalization_entry(raw, cand)
            norm_cache[raw] = cand
        if bkey and brand_dict.get(bkey) != cand:
            dict_updates[bkey] = cand

    upsert_merchant_dictionary(dict_updates)
    df["merchant_normalized"] = df["name"].map(resolved)
    return df

