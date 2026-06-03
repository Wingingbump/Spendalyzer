"""Tests for reimbursement classification, netting, and memo categorization."""
import pandas as pd
import pytest

import core.db as db
import core.reimbursements as reimb
from core.reimbursements import flag_reimbursements, categorize_reimbursements
import core.insights as ins


def _df(rows):
    base = {
        "type": "debit", "is_transfer": False, "is_duplicate": False,
        "category": "Food & Drink", "merchant_normalized": "M", "institution": "BankA",
        "name": "Tx", "has_user_override": False,
    }
    recs = []
    for r in rows:
        x = {**base, **r}
        x.setdefault("date", pd.Timestamp("2026-05-15"))
        recs.append(x)
    d = pd.DataFrame(recs)
    d["date"] = pd.to_datetime(d["date"])
    d["amount"] = d["amount"].astype(float)
    return d


@pytest.fixture(autouse=True)
def _no_db_ai(monkeypatch):
    """Keep tests offline: no memo cache DB hit, no AI calls by default."""
    monkeypatch.setattr(db, "load_memo_category_cache", lambda: {})
    monkeypatch.setattr(db, "save_memo_category", lambda *a, **k: None)
    monkeypatch.setattr(reimb, "ai_classify_memo", lambda memo: "Uncategorized")


class TestClassification:
    def test_p2p_credit_is_reimbursement(self):
        d = flag_reimbursements(_df([
            {"name": 'Joe "dinner"', "amount": -20, "type": "credit",
             "institution": "Venmo - Personal", "merchant_normalized": "Joe"},
        ]))
        assert bool(d.iloc[0]["is_reimbursement"]) is True

    def test_merchant_refund_is_reimbursement(self):
        d = flag_reimbursements(_df([
            {"name": "AMAZON", "amount": 30, "merchant_normalized": "Amazon"},          # debit
            {"name": "AMAZON", "amount": -30, "type": "credit", "merchant_normalized": "Amazon"},  # refund
        ]))
        assert bool(d.iloc[1]["is_reimbursement"]) is True

    def test_paycheck_is_not_reimbursement(self):
        d = flag_reimbursements(_df([
            {"name": "LEIDOS INC", "amount": -2000, "type": "credit",
             "institution": "Capital One", "merchant_normalized": "Leidos"},
        ]))
        assert bool(d.iloc[0]["is_reimbursement"]) is False


class TestNetting:
    def test_reimbursement_reduces_category_spend(self):
        d = flag_reimbursements(_df([
            {"name": "Restaurant", "amount": 50, "category": "Food & Drink"},
            {"name": 'Joe "dinner"', "amount": -15, "type": "credit",
             "institution": "Venmo - Personal", "category": "Food & Drink", "merchant_normalized": "Joe"},
        ]))
        assert ins.total_spent(d) == 35.0  # 50 - 15

    def test_income_excluded_from_spending(self):
        d = flag_reimbursements(_df([
            {"name": "Restaurant", "amount": 50, "category": "Food & Drink"},
            {"name": "LEIDOS INC", "amount": -2000, "type": "credit",
             "institution": "Capital One", "merchant_normalized": "Leidos"},
        ]))
        assert ins.total_spent(d) == 50.0          # income does not reduce spend
        assert ins.total_income(d) == 2000.0
        assert ins.total_reimbursements(d) == 0.0


class TestMemoCategorization:
    def test_user_override_is_respected(self):
        """The reported bug: a manually set category must survive re-categorization."""
        d = flag_reimbursements(_df([
            {"name": 'glen "get jumpin!!!"', "amount": -25, "type": "credit",
             "institution": "Venmo - Personal", "merchant_normalized": "Glen",
             "category": "Entertainment", "has_user_override": True},
        ]))
        out = categorize_reimbursements(d, user_id=1)
        assert out.iloc[0]["category"] == "Entertainment"
        assert bool(out.iloc[0]["needs_review"]) is False

    def test_unmatched_memo_flagged_for_review(self):
        d = flag_reimbursements(_df([
            {"name": 'glen "inside joke"', "amount": -25, "type": "credit",
             "institution": "Venmo - Personal", "merchant_normalized": "Glen",
             "category": "Payments"},
        ]))
        out = categorize_reimbursements(d, user_id=1)
        assert out.iloc[0]["category"] == "Uncategorized"
        assert bool(out.iloc[0]["needs_review"]) is True

    def test_memo_matches_a_real_purchase(self):
        # A debit at "Stellina" (Food) + a Venmo memo "Stellina" -> inherits Food.
        d = flag_reimbursements(_df([
            {"name": "Stellina", "amount": 60, "category": "Food & Drink",
             "merchant_normalized": "Stellina"},
            {"name": 'Joe "Stellina"', "amount": -30, "type": "credit",
             "institution": "Venmo - Personal", "merchant_normalized": "Joe",
             "category": "Payments"},
        ]))
        out = categorize_reimbursements(d, user_id=1)
        assert out[out["name"] == 'Joe "Stellina"'].iloc[0]["category"] == "Food & Drink"
