"""
Tests for sub-account labelling (core.insights.account_label / add_account_labels)
and for the sub-account snapshot written at sync time.

One connection can hold several cards and bank accounts — and since the
Discover/Capital One merger, cards from both brands under one Capital One
login — so each transaction needs to say which account it hit, and keep saying
so after the connection it came from is removed.
"""
from unittest.mock import patch

import pandas as pd

from core.insights import DISCONNECTED_ACCOUNT_LABEL, account_label, add_account_labels


class TestAccountLabel:
    def test_name_and_mask(self):
        assert account_label("Savor", "4874") == "Savor ••4874"

    def test_name_without_mask(self):
        assert account_label("Personal Profile", None) == "Personal Profile"

    def test_mask_without_name(self):
        assert account_label("", "5466") == "••5466"

    def test_strips_whitespace(self):
        assert account_label("  Savor ", " 4874 ") == "Savor ••4874"

    def test_disconnected_account_with_nothing_recorded(self):
        assert account_label(None, None, connected=False) == DISCONNECTED_ACCOUNT_LABEL

    def test_disconnected_account_keeps_its_snapshot(self):
        # The point of the snapshot: a removed connection still names the card.
        assert account_label("Discover it", "1234", connected=False) == "Discover it ••1234"

    def test_manual_row_has_no_label(self):
        assert account_label(None, None, connected=False, has_account=False) == ""

    def test_nan_is_treated_as_missing(self):
        assert account_label(float("nan"), float("nan")) == ""


class TestAddAccountLabels:
    def test_labels_each_row(self):
        df = pd.DataFrame([
            {"plaid_account_id": "a", "account_name": "Savor", "account_mask": "4874",
             "account_connected": True},
            {"plaid_account_id": "b", "account_name": "360 Checking", "account_mask": "5466",
             "account_connected": True},
            {"plaid_account_id": "old", "account_name": None, "account_mask": None,
             "account_connected": False},
            {"plaid_account_id": None, "account_name": None, "account_mask": None,
             "account_connected": False},
        ])
        out = add_account_labels(df)
        assert out["account_label"].tolist() == [
            "Savor ••4874", "360 Checking ••5466", DISCONNECTED_ACCOUNT_LABEL, "",
        ]

    def test_missing_connected_column_assumes_connected(self):
        df = pd.DataFrame([{"plaid_account_id": "a", "account_name": None, "account_mask": None}])
        out = add_account_labels(df)
        assert out["account_label"].tolist() == [""]
        assert out["account_connected"].tolist() == [True]

    def test_missing_account_columns(self):
        df = pd.DataFrame([{"id": "tx1"}])
        out = add_account_labels(df)
        assert out["account_label"].tolist() == [""]


class TestSyncSnapshot:
    def test_save_transactions_records_sub_account(self):
        from services import pull

        account_map = {
            "acct-savor": {"plaid_account_id": "acct-savor", "name": "Savor", "mask": "4874",
                           "type": "credit", "subtype": "credit card",
                           "institution_name": "Capital One"},
        }
        txn = {
            "transaction_id": "t1", "account_id": "acct-savor", "date": "2026-11-01",
            "name": "Coffee", "amount": 4.5, "category": ["Food and Drink"], "pending": False,
        }
        captured = []
        with patch.object(pull, "load_category_map", return_value={}), \
             patch.object(pull, "upsert_transactions", side_effect=captured.extend):
            n = pull.save_transactions([txn], "Capital One", set(), account_map, user_id=1)

        assert n == 1
        rec = captured[0]
        assert rec["institution"] == "Capital One"
        assert rec["account_name"] == "Savor"
        assert rec["account_mask"] == "4874"
        assert rec["account_subtype"] == "credit card"
