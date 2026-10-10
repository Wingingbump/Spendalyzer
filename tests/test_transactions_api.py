"""
Tests for backend/routers/transactions.py

Uses a minimal FastAPI app with the transactions router mounted, the
get_current_user dependency overridden to skip real auth, and all DB
calls mocked so no real database is required.
"""
import pytest
from unittest.mock import patch, MagicMock
import pandas as pd
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.dependencies import get_current_user
from backend.routers import transactions as tx_router

FAKE_USER = {"id": 1, "username": "testuser"}


def _make_app() -> FastAPI:
    app = FastAPI()
    app.include_router(tx_router.router)
    app.dependency_overrides[get_current_user] = lambda: FAKE_USER
    return app


@pytest.fixture()
def client():
    return TestClient(_make_app(), raise_server_exceptions=True)


def _make_tx_df(rows: list[dict] | None = None) -> pd.DataFrame:
    """Minimal DataFrame that load_data + get_spending would return."""
    recent = pd.Timestamp.today().normalize() - pd.Timedelta(days=5)
    defaults = {
        "id": "tx1",
        "date": recent,
        "name": "Coffee",
        "amount": 5.50,
        "category": "Food & Drink",
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
        "merchant_normalized": "Starbucks",
        "dedup_reason": None,
        "has_user_override": False,
        "is_manual": False,
    }
    records = [{**defaults, **r} for r in (rows if rows is not None else [{}])]
    df = pd.DataFrame(records)
    df["date"] = pd.to_datetime(df["date"])
    df["amount"] = df["amount"].astype(float)
    return df


# ── GET /transactions ──────────────────────────────────────────────────────────

class TestListTransactions:
    def test_returns_200(self, client):
        with patch("backend.routers.transactions.ins.load_data", return_value=_make_tx_df()):
            r = client.get("/transactions")
        assert r.status_code == 200

    def test_returns_list(self, client):
        with patch("backend.routers.transactions.ins.load_data", return_value=_make_tx_df()):
            r = client.get("/transactions")
        assert isinstance(r.json(), list)

    def test_contains_expected_fields(self, client):
        with patch("backend.routers.transactions.ins.load_data", return_value=_make_tx_df()):
            r = client.get("/transactions")
        record = r.json()[0]
        for field in ("id", "date", "name", "amount", "category", "institution"):
            assert field in record, f"Missing field: {field}"

    def test_is_manual_field_present(self, client):
        with patch("backend.routers.transactions.ins.load_data", return_value=_make_tx_df()):
            r = client.get("/transactions")
        assert "is_manual" in r.json()[0]

    def test_has_user_override_field_present(self, client):
        with patch("backend.routers.transactions.ins.load_data", return_value=_make_tx_df()):
            r = client.get("/transactions")
        assert "has_user_override" in r.json()[0]

    def test_search_filters_by_name(self, client):
        rows = [
            {"id": "tx1", "name": "Starbucks Coffee", "merchant_normalized": "Starbucks", "category": "Food & Drink"},
            {"id": "tx2", "name": "Amazon Order", "merchant_normalized": "Amazon", "category": "Shopping"},
        ]
        with patch("backend.routers.transactions.ins.load_data", return_value=_make_tx_df(rows)):
            r = client.get("/transactions", params={"search": "starbucks"})
        names = [row["name"] for row in r.json()]
        assert all("Starbucks" in n for n in names)
        assert not any("Amazon" in n for n in names)

    def test_search_filters_by_category(self, client):
        rows = [
            {"id": "tx1", "name": "Coffee", "merchant_normalized": "Starbucks", "category": "Food & Drink"},
            {"id": "tx2", "name": "Amazon", "merchant_normalized": "Amazon", "category": "Shopping"},
        ]
        with patch("backend.routers.transactions.ins.load_data", return_value=_make_tx_df(rows)):
            r = client.get("/transactions", params={"search": "shopping"})
        categories = [row["category"] for row in r.json()]
        assert all(c == "Shopping" for c in categories)

    def test_empty_dataset_returns_empty_list(self, client):
        empty = pd.DataFrame(columns=_make_tx_df().columns)
        empty["date"] = pd.to_datetime(empty["date"])
        empty["amount"] = empty["amount"].astype(float)
        with patch("backend.routers.transactions.ins.load_data", return_value=empty):
            r = client.get("/transactions")
        assert r.json() == []

    def test_excludes_transfers(self, client):
        rows = [
            {"id": "tx1", "name": "Direct Deposit", "is_transfer": True, "type": "credit", "amount": -1000.0},
            {"id": "tx2", "name": "Coffee", "is_transfer": False},
        ]
        with patch("backend.routers.transactions.ins.load_data", return_value=_make_tx_df(rows)):
            r = client.get("/transactions")
        names = [row["name"] for row in r.json()]
        assert "Coffee" in names
        assert "Direct Deposit" not in names

    def test_excludes_duplicates(self, client):
        rows = [
            {"id": "tx1", "name": "Coffee", "is_duplicate": False},
            {"id": "tx2", "name": "Coffee Dup", "is_duplicate": True},
        ]
        with patch("backend.routers.transactions.ins.load_data", return_value=_make_tx_df(rows)):
            r = client.get("/transactions")
        names = [row["name"] for row in r.json()]
        assert "Coffee Dup" not in names

    def test_unauthenticated_returns_401(self):
        # Build a fresh app WITHOUT the dependency override
        app = FastAPI()
        app.include_router(tx_router.router)
        c = TestClient(app, raise_server_exceptions=False)
        r = c.get("/transactions")
        assert r.status_code == 401


# ── POST /transactions ─────────────────────────────────────────────────────────

class TestCreateTransaction:
    def test_returns_200_and_ok(self, client):
        with patch("backend.routers.transactions.insert_manual_transaction", return_value="manual_abc123"):
            r = client.post("/transactions", json={
                "name": "Cash lunch",
                "date": "2025-03-15",
                "amount": 12.50,
            })
        assert r.status_code == 200
        assert r.json()["ok"] is True

    def test_returns_generated_id(self, client):
        with patch("backend.routers.transactions.insert_manual_transaction", return_value="manual_xyz"):
            r = client.post("/transactions", json={
                "name": "Cash lunch",
                "date": "2025-03-15",
                "amount": 12.50,
            })
        assert r.json()["id"] == "manual_xyz"

    def test_calls_insert_with_correct_user_id(self, client):
        with patch("backend.routers.transactions.insert_manual_transaction", return_value="manual_1") as mock_insert:
            client.post("/transactions", json={
                "name": "Test",
                "date": "2025-03-15",
                "amount": 5.0,
            })
        assert mock_insert.call_args.kwargs["user_id"] == FAKE_USER["id"]

    def test_passes_optional_category(self, client):
        with patch("backend.routers.transactions.insert_manual_transaction", return_value="manual_1") as mock_insert:
            client.post("/transactions", json={
                "name": "Lunch",
                "date": "2025-03-15",
                "amount": 10.0,
                "category": "Food & Drink",
            })
        assert mock_insert.call_args.kwargs["category"] == "Food & Drink"

    def test_passes_optional_notes(self, client):
        with patch("backend.routers.transactions.insert_manual_transaction", return_value="manual_1") as mock_insert:
            client.post("/transactions", json={
                "name": "Lunch",
                "date": "2025-03-15",
                "amount": 10.0,
                "notes": "team lunch",
            })
        assert mock_insert.call_args.kwargs["notes"] == "team lunch"

    def test_missing_name_returns_422(self, client):
        r = client.post("/transactions", json={"date": "2025-03-15", "amount": 10.0})
        assert r.status_code == 422

    def test_missing_amount_returns_422(self, client):
        r = client.post("/transactions", json={"name": "Test", "date": "2025-03-15"})
        assert r.status_code == 422

    def test_missing_date_returns_422(self, client):
        r = client.post("/transactions", json={"name": "Test", "amount": 10.0})
        assert r.status_code == 422

    def test_negative_amount_allowed(self, client):
        """Negative amounts represent income/credits."""
        with patch("backend.routers.transactions.insert_manual_transaction", return_value="manual_1") as mock_insert:
            r = client.post("/transactions", json={
                "name": "Paycheck",
                "date": "2025-03-15",
                "amount": -2000.0,
            })
        assert r.status_code == 200
        assert mock_insert.call_args.kwargs["amount"] == -2000.0


# ── PATCH /transactions/{id} ───────────────────────────────────────────────────

class TestPatchTransaction:
    @pytest.fixture()
    def owned(self):
        """PATCH checks ownership before writing, so every happy-path test needs
        the transaction to resolve to the current user."""
        with patch("backend.routers.transactions.transaction_belongs_to_user",
                   return_value=True) as m:
            yield m

    def test_returns_ok(self, client, owned):
        with patch("backend.routers.transactions.save_override"):
            r = client.patch("/transactions/tx1", json={"category": "Shopping"})
        assert r.status_code == 200
        assert r.json()["ok"] is True

    def test_patch_category_calls_save_override(self, client, owned):
        with patch("backend.routers.transactions.save_override") as mock_save:
            client.patch("/transactions/tx1", json={"category": "Travel"})
        mock_save.assert_called_once_with(
            FAKE_USER["id"], "tx1",
            category="Travel", amount=None, notes=None, excluded=None,
        )

    def test_patch_notes_calls_save_override(self, client, owned):
        with patch("backend.routers.transactions.save_override") as mock_save:
            client.patch("/transactions/tx1", json={"notes": "reimbursed"})
        mock_save.assert_called_once_with(
            FAKE_USER["id"], "tx1",
            category=None, amount=None, notes="reimbursed", excluded=None,
        )

    def test_patch_amount_calls_save_override(self, client, owned):
        with patch("backend.routers.transactions.save_override") as mock_save:
            client.patch("/transactions/tx1", json={"amount": 99.99})
        mock_save.assert_called_once_with(
            FAKE_USER["id"], "tx1",
            category=None, amount=99.99, notes=None, excluded=None,
        )

    def test_patch_excluded_calls_save_override(self, client, owned):
        with patch("backend.routers.transactions.save_override") as mock_save:
            client.patch("/transactions/tx1", json={"excluded": True})
        mock_save.assert_called_once_with(
            FAKE_USER["id"], "tx1",
            category=None, amount=None, notes=None, excluded=True,
        )

    def test_empty_body_still_returns_ok(self, client, owned):
        with patch("backend.routers.transactions.save_override"):
            r = client.patch("/transactions/tx1", json={})
        assert r.status_code == 200

    def test_other_users_transaction_returns_404_without_writing(self, client):
        with patch("backend.routers.transactions.transaction_belongs_to_user",
                   return_value=False), \
             patch("backend.routers.transactions.save_override") as mock_save:
            r = client.patch("/transactions/tx1", json={"category": "Travel"})
        assert r.status_code == 404
        mock_save.assert_not_called()


# ── DELETE /transactions/{id} ──────────────────────────────────────────────────

class TestDeleteTransaction:
    def test_returns_ok_when_deleted(self, client):
        with patch("backend.routers.transactions.delete_transaction", return_value=True):
            r = client.delete("/transactions/tx1")
        assert r.status_code == 200
        assert r.json()["ok"] is True

    def test_returns_404_when_not_found(self, client):
        with patch("backend.routers.transactions.delete_transaction", return_value=False):
            r = client.delete("/transactions/tx1")
        assert r.status_code == 404

    def test_calls_delete_with_correct_user_id(self, client):
        with patch("backend.routers.transactions.delete_transaction", return_value=True) as mock_del:
            client.delete("/transactions/manual_abc")
        mock_del.assert_called_once_with("manual_abc", FAKE_USER["id"])

    def test_delete_plaid_transaction_succeeds(self, client):
        """Plaid-sourced transactions can also be deleted (e.g. to remove duplicates)."""
        with patch("backend.routers.transactions.delete_transaction", return_value=True):
            r = client.delete("/transactions/plaid_tx_id_123")
        assert r.status_code == 200
