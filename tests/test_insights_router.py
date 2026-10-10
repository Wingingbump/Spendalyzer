"""
Tests for backend/routers/insights.py

Mocks ins.load_data so no DB connection is required. Validates that each
endpoint transforms the DataFrame correctly and returns well-formed JSON.
"""
import pytest
from unittest.mock import patch
import pandas as pd
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.dependencies import get_current_user
from backend.routers import insights as ins_router

FAKE_USER = {"id": 1, "username": "testuser"}


def _make_app() -> FastAPI:
    app = FastAPI()
    app.include_router(ins_router.router)
    app.dependency_overrides[get_current_user] = lambda: FAKE_USER
    return app


@pytest.fixture()
def client():
    return TestClient(_make_app(), raise_server_exceptions=True)


def _make_df(rows: list[dict] | None = None) -> pd.DataFrame:
    """Full-featured DataFrame matching what fetch_transactions produces."""
    today = pd.Timestamp.today().normalize()
    defaults = {
        "id": "tx1",
        "date": today - pd.Timedelta(days=5),
        "name": "Grocery Run",
        "amount": 85.0,
        "category": "Groceries",
        "pending": False,
        "institution": "Chase",
        "plaid_account_id": "acct1",
        "account_name": "Checking",
        "account_mask": "1234",
        "account_subtype": "checking",
        "notes": "",
        "type": "debit",
        "is_transfer": False,
        "is_duplicate": False,
        "merchant_normalized": "Whole Foods",
        "dedup_reason": None,
        "has_user_override": False,
        "is_manual": False,
    }
    records = [({**defaults, **r}) for r in (rows or [defaults])]
    df = pd.DataFrame(records)
    df["date"] = pd.to_datetime(df["date"])
    df["amount"] = df["amount"].astype(float)
    return df


def _empty_df() -> pd.DataFrame:
    df = _make_df()
    return df.iloc[0:0].copy()


# ── GET /insights/summary ──────────────────────────────────────────────────────

class TestSummaryEndpoint:
    def test_returns_200(self, client):
        with patch("backend.routers.insights.ins.load_data", return_value=_make_df()):
            r = client.get("/insights/summary")
        assert r.status_code == 200

    def test_contains_required_fields(self, client):
        with patch("backend.routers.insights.ins.load_data", return_value=_make_df()):
            r = client.get("/insights/summary")
        body = r.json()
        for field in ("total_spent", "transaction_count", "net_spend",
                      "this_month", "last_month", "delta", "delta_pct",
                      "biggest_purchase", "most_visited_merchant", "biggest_spending_day"):
            assert field in body, f"Missing field: {field}"

    def test_total_spent_is_numeric(self, client):
        with patch("backend.routers.insights.ins.load_data", return_value=_make_df()):
            r = client.get("/insights/summary")
        assert isinstance(r.json()["total_spent"], (int, float))

    def test_transaction_count_correct(self, client):
        rows = [
            {"id": "tx1", "amount": 10.0},
            {"id": "tx2", "amount": 20.0},
        ]
        with patch("backend.routers.insights.ins.load_data", return_value=_make_df(rows)):
            r = client.get("/insights/summary")
        assert r.json()["transaction_count"] == 2

    def test_empty_data_returns_nulls_not_nan(self, client):
        """NaN must never appear in JSON output."""
        with patch("backend.routers.insights.ins.load_data", return_value=_empty_df()):
            r = client.get("/insights/summary")
        assert r.status_code == 200
        text = r.text
        assert "NaN" not in text
        assert "Infinity" not in text

    def test_calendar_month_figures_ignore_the_range_filter(self, client):
        """this_month/last_month/delta describe calendar months, so narrowing the
        range filter must not truncate them. A 30d range reaches only part way
        into last month; last_month has to stay the full month regardless."""
        today = pd.Timestamp.today().normalize()
        last_month_end = today.replace(day=1) - pd.Timedelta(days=1)
        first_of_last_month = last_month_end.replace(day=1)

        rows = [{"id": "cur", "date": today, "amount": 50.0}]
        # $10/day across the whole of last month.
        for i, d in enumerate(pd.date_range(first_of_last_month, last_month_end)):
            rows.append({"id": f"prev{i}", "date": d, "amount": 10.0})
        expected_last_month = round(10.0 * len(pd.date_range(first_of_last_month,
                                                             last_month_end)), 2)

        with patch("backend.routers.insights.ins.load_data",
                   return_value=_make_df(rows)):
            wide = client.get("/insights/summary", params={"range": "all"}).json()
            narrow = client.get("/insights/summary", params={"range": "30d"}).json()

        assert wide["last_month"] == expected_last_month
        assert narrow["last_month"] == expected_last_month
        assert narrow["this_month"] == wide["this_month"] == 50.0
        assert narrow["delta"] == wide["delta"]

    def test_range_filter_still_applies_to_window_figures(self, client):
        """The range filter is not ignored wholesale — total_spent still follows
        it, otherwise picking a range would do nothing at all."""
        today = pd.Timestamp.today().normalize()
        rows = [
            {"id": "recent", "date": today - pd.Timedelta(days=2), "amount": 25.0},
            {"id": "old", "date": today - pd.Timedelta(days=200), "amount": 400.0},
        ]
        with patch("backend.routers.insights.ins.load_data",
                   return_value=_make_df(rows)):
            wide = client.get("/insights/summary", params={"range": "all"}).json()
            narrow = client.get("/insights/summary", params={"range": "30d"}).json()

        assert wide["total_spent"] == 425.0
        assert narrow["total_spent"] == 25.0

    def test_total_spent_sums_correctly(self, client):
        rows = [
            {"id": "tx1", "amount": 30.0},
            {"id": "tx2", "amount": 70.0},
        ]
        with patch("backend.routers.insights.ins.load_data", return_value=_make_df(rows)):
            r = client.get("/insights/summary")
        assert r.json()["total_spent"] == 100.0

    def test_unauthenticated_returns_401(self):
        app = FastAPI()
        app.include_router(ins_router.router)
        c = TestClient(app, raise_server_exceptions=False)
        r = c.get("/insights/summary")
        assert r.status_code == 401


# ── GET /insights/categories ───────────────────────────────────────────────────

class TestCategoriesEndpoint:
    def test_returns_200(self, client):
        with patch("backend.routers.insights.ins.load_data", return_value=_make_df()):
            r = client.get("/insights/categories")
        assert r.status_code == 200

    def test_returns_list(self, client):
        with patch("backend.routers.insights.ins.load_data", return_value=_make_df()):
            r = client.get("/insights/categories")
        assert isinstance(r.json(), list)

    def test_each_record_has_category_and_total(self, client):
        with patch("backend.routers.insights.ins.load_data", return_value=_make_df()):
            r = client.get("/insights/categories")
        for record in r.json():
            assert "category" in record
            assert "total" in record

    def test_groups_by_category(self, client):
        rows = [
            {"id": "tx1", "amount": 30.0, "category": "Food & Drink"},
            {"id": "tx2", "amount": 20.0, "category": "Food & Drink"},
            {"id": "tx3", "amount": 50.0, "category": "Travel"},
        ]
        with patch("backend.routers.insights.ins.load_data", return_value=_make_df(rows)):
            r = client.get("/insights/categories")
        cats = {rec["category"]: rec["total"] for rec in r.json()}
        assert cats["Food & Drink"] == 50.0
        assert cats["Travel"] == 50.0

    def test_empty_data_returns_empty_list(self, client):
        with patch("backend.routers.insights.ins.load_data", return_value=_empty_df()):
            r = client.get("/insights/categories")
        assert r.json() == []

    def test_no_nan_in_response(self, client):
        with patch("backend.routers.insights.ins.load_data", return_value=_make_df()):
            r = client.get("/insights/categories")
        assert "NaN" not in r.text


# ── GET /insights/monthly ──────────────────────────────────────────────────────

class TestMonthlyEndpoint:
    def test_returns_200(self, client):
        with patch("backend.routers.insights.ins.load_data", return_value=_make_df()):
            r = client.get("/insights/monthly")
        assert r.status_code == 200

    def test_returns_list(self, client):
        with patch("backend.routers.insights.ins.load_data", return_value=_make_df()):
            r = client.get("/insights/monthly")
        assert isinstance(r.json(), list)

    def test_records_have_month_and_total(self, client):
        with patch("backend.routers.insights.ins.load_data", return_value=_make_df()):
            r = client.get("/insights/monthly")
        for record in r.json():
            assert "month" in record or "year" in record or "total" in record

    def test_empty_data_returns_empty_list(self, client):
        with patch("backend.routers.insights.ins.load_data", return_value=_empty_df()):
            r = client.get("/insights/monthly")
        assert r.json() == []

    def test_no_nan_in_response(self, client):
        with patch("backend.routers.insights.ins.load_data", return_value=_make_df()):
            r = client.get("/insights/monthly")
        assert "NaN" not in r.text


# ── GET /insights/dow ─────────────────────────────────────────────────────────

class TestDowEndpoint:
    def test_returns_200(self, client):
        with patch("backend.routers.insights.ins.load_data", return_value=_make_df()):
            r = client.get("/insights/dow")
        assert r.status_code == 200

    def test_returns_seven_records(self, client):
        with patch("backend.routers.insights.ins.load_data", return_value=_make_df()):
            r = client.get("/insights/dow")
        assert len(r.json()) == 7

    def test_uses_day_column_not_dow(self, client):
        """Router renames 'dow' → 'day' for the frontend."""
        with patch("backend.routers.insights.ins.load_data", return_value=_make_df()):
            r = client.get("/insights/dow")
        for record in r.json():
            assert "day" in record, "Expected 'day' column (renamed from 'dow')"
            assert "dow" not in record

    def test_contains_all_weekdays(self, client):
        with patch("backend.routers.insights.ins.load_data", return_value=_make_df()):
            r = client.get("/insights/dow")
        days = [rec["day"] for rec in r.json()]
        for expected in ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"):
            assert expected in days

    def test_no_nan_in_response(self, client):
        with patch("backend.routers.insights.ins.load_data", return_value=_empty_df()):
            r = client.get("/insights/dow")
        assert "NaN" not in r.text


# ── GET /insights/institutions ────────────────────────────────────────────────

class TestInstitutionsEndpoint:
    def test_returns_200(self, client):
        with patch("backend.routers.insights.ins.load_data", return_value=_make_df()):
            r = client.get("/insights/institutions")
        assert r.status_code == 200

    def test_returns_list_of_strings(self, client):
        with patch("backend.routers.insights.ins.load_data", return_value=_make_df()):
            r = client.get("/insights/institutions")
        for item in r.json():
            assert isinstance(item, str)

    def test_deduplicates_institutions(self, client):
        rows = [
            {"id": "tx1", "institution": "Chase"},
            {"id": "tx2", "institution": "Chase"},
            {"id": "tx3", "institution": "Discover"},
        ]
        with patch("backend.routers.insights.ins.load_data", return_value=_make_df(rows)):
            r = client.get("/insights/institutions")
        assert r.json().count("Chase") == 1

    def test_returns_sorted_alphabetically(self, client):
        rows = [
            {"id": "tx1", "institution": "Zelle"},
            {"id": "tx2", "institution": "Chase"},
            {"id": "tx3", "institution": "Amex"},
        ]
        with patch("backend.routers.insights.ins.load_data", return_value=_make_df(rows)):
            r = client.get("/insights/institutions")
        result = r.json()
        assert result == sorted(result)

    def test_empty_data_returns_empty_list(self, client):
        with patch("backend.routers.insights.ins.load_data", return_value=_empty_df()):
            r = client.get("/insights/institutions")
        assert r.json() == []


# ── GET /insights/accounts ────────────────────────────────────────────────────

class TestAccountsEndpoint:
    def test_returns_200(self, client):
        with patch("backend.routers.insights.ins.load_data", return_value=_make_df()):
            r = client.get("/insights/accounts")
        assert r.status_code == 200

    def test_returns_list(self, client):
        with patch("backend.routers.insights.ins.load_data", return_value=_make_df()):
            r = client.get("/insights/accounts")
        assert isinstance(r.json(), list)

    def test_each_record_has_plaid_account_id(self, client):
        with patch("backend.routers.insights.ins.load_data", return_value=_make_df()):
            r = client.get("/insights/accounts")
        for record in r.json():
            assert "plaid_account_id" in record

    def test_deduplicates_accounts(self, client):
        rows = [
            {"id": "tx1", "plaid_account_id": "acct1"},
            {"id": "tx2", "plaid_account_id": "acct1"},
            {"id": "tx3", "plaid_account_id": "acct2"},
        ]
        with patch("backend.routers.insights.ins.load_data", return_value=_make_df(rows)):
            r = client.get("/insights/accounts")
        ids = [rec["plaid_account_id"] for rec in r.json()]
        assert ids.count("acct1") == 1

    def test_empty_data_returns_empty_list(self, client):
        with patch("backend.routers.insights.ins.load_data", return_value=_empty_df()):
            r = client.get("/insights/accounts")
        assert r.json() == []

    def test_no_nan_in_response(self, client):
        with patch("backend.routers.insights.ins.load_data", return_value=_make_df()):
            r = client.get("/insights/accounts")
        assert "NaN" not in r.text
