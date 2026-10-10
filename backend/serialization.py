"""JSON serialization helpers shared by the routers.

Starlette renders responses with `json.dumps(..., allow_nan=False)`, so a NaN or
an Infinity that reaches a response body raises instead of serializing — the
client sees a 500, not a null. Pandas produces both routinely (a missing join
column, a division with no denominator), so every DataFrame has to be scrubbed
on the way out.
"""

import math

import numpy as np
import pandas as pd


def json_safe(v):
    """Coerce one cell to something `json.dumps(allow_nan=False)` accepts."""
    if v is None or v is pd.NaT or v is pd.NA:
        return None
    # numpy scalars are not JSON-serializable. np.float64 subclasses float, so it
    # falls through to the float branch below; the others need converting.
    if isinstance(v, np.integer):
        return int(v)
    if isinstance(v, np.bool_):
        return bool(v)
    if isinstance(v, float):
        # Infinity is as fatal as NaN under allow_nan=False.
        return None if (math.isnan(v) or math.isinf(v)) else v
    if hasattr(v, "isoformat"):  # Timestamp / datetime / date
        return None if pd.isna(v) else v.isoformat()
    return v


def df_to_records(df: pd.DataFrame) -> list:
    """DataFrame → list of JSON-safe dicts, with NaN/NaT/Inf as None.

    Note for anyone tempted to shorten this back to
    `df.where(pd.notna(df), None).to_dict(orient="records")`: that does not work.
    On a float column the None is coerced straight back to NaN, so the guard
    silently does nothing for exactly the dtype that produces NaN in the first
    place. The conversion has to happen per value, after to_dict().
    """
    records = df.to_dict(orient="records")
    for row in records:
        for k, v in row.items():
            row[k] = json_safe(v)
    return records
