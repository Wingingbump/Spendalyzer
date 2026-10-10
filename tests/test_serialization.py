"""Tests for backend/serialization.py.

Starlette renders with json.dumps(allow_nan=False), so anything these helpers
let through un-scrubbed becomes a 500 rather than a null. json.dumps with the
same flag therefore stands in for the real response encoder here.
"""
import json
import math

import numpy as np
import pandas as pd
import pytest

from backend.serialization import df_to_records, json_safe


def _render(records) -> str:
    """Encode exactly the way Starlette's JSONResponse does."""
    return json.dumps(records, allow_nan=False)


class TestFloatNaN:
    def test_nan_in_a_float_column_becomes_null(self):
        """The regression this module exists for: df.where(pd.notna(df), None)
        coerces the None straight back to NaN on a float column, so the old
        guard silently did nothing for the one dtype that produces NaN."""
        df = pd.DataFrame({"total": [1.0, np.nan]})
        records = df_to_records(df)
        assert records[1]["total"] is None
        assert "NaN" not in _render(records)

    def test_an_all_nan_float_column_is_fully_scrubbed(self):
        df = pd.DataFrame({"pct": [np.nan, np.nan]})
        records = df_to_records(df)
        assert [r["pct"] for r in records] == [None, None]
        _render(records)  # must not raise

    def test_infinity_becomes_null(self):
        """A ratio with a zero denominator. Infinity is as fatal as NaN."""
        df = pd.DataFrame({"ratio": [math.inf, -math.inf, 2.0]})
        records = df_to_records(df)
        assert [r["ratio"] for r in records] == [None, None, 2.0]
        assert "Infinity" not in _render(records)

    def test_finite_floats_are_untouched(self):
        df = pd.DataFrame({"total": [0.0, -12.5, 1e9]})
        assert [r["total"] for r in df_to_records(df)] == [0.0, -12.5, 1e9]


class TestMissingValuesInOtherDtypes:
    def test_none_in_an_object_column_stays_none(self):
        df = pd.DataFrame({"mask": ["1234", None]})
        assert df_to_records(df)[1]["mask"] is None

    def test_nat_becomes_null(self):
        df = pd.DataFrame({"date": [pd.Timestamp("2025-03-24"), pd.NaT]})
        records = df_to_records(df)
        assert records[0]["date"].startswith("2025-03-24")
        assert records[1]["date"] is None
        _render(records)


class TestNumpyScalars:
    def test_numpy_int_becomes_python_int(self):
        df = pd.DataFrame({"count": pd.Series([3], dtype="int64")})
        v = df_to_records(df)[0]["count"]
        assert isinstance(v, int) and not isinstance(v, np.integer)
        _render(df_to_records(df))

    def test_numpy_bool_becomes_python_bool(self):
        df = pd.DataFrame({"pending": pd.Series([True], dtype="bool")})
        v = df_to_records(df)[0]["pending"]
        assert v is True
        _render(df_to_records(df))


class TestDates:
    def test_timestamps_are_isoformatted(self):
        df = pd.DataFrame({"date": [pd.Timestamp("2025-03-24")]})
        assert df_to_records(df)[0]["date"] == "2025-03-24T00:00:00"


class TestEmptyFrame:
    def test_empty_frame_yields_no_records(self):
        assert df_to_records(pd.DataFrame({"a": pd.Series(dtype=float)})) == []


class TestJsonSafeScalars:
    @pytest.mark.parametrize("value", [None, pd.NaT, pd.NA, np.nan, math.inf, -math.inf])
    def test_missing_and_non_finite_values_map_to_none(self, value):
        assert json_safe(value) is None

    @pytest.mark.parametrize("value", ["text", 0, 1.5, True, [], {}])
    def test_ordinary_values_pass_through(self, value):
        assert json_safe(value) == value
