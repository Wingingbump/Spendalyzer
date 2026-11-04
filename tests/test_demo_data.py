"""Tests for the demo account's generated year.

The demo exists to show the classification pipeline doing the thing it's for,
so these assert the data actually contains the hard cases — transfers that must
not count as spend, reimbursements that net against a category, fixed-amount
subscriptions — rather than just that some rows came out.
"""
import datetime
import json

import pandas as pd
import pytest

from core.demo_data import (
    DEMO_SEED,
    EXTRA_CATEGORIES,
    demo_accounts,
    demo_memo_cache_entries,
    generate_transactions,
)

TODAY = datetime.date(2026, 10, 10)


@pytest.fixture(scope="module")
def rows():
    return generate_transactions(user_id=1, today=TODAY)


class TestShape:
    def test_covers_a_full_year_ending_today(self, rows):
        dates = sorted(r["date"] for r in rows)
        span = datetime.date.fromisoformat(dates[-1]) - datetime.date.fromisoformat(dates[0])
        assert 360 <= span.days <= 366
        assert dates[-1] == TODAY.isoformat()

    def test_enough_volume_to_be_worth_exploring(self, rows):
        assert 500 <= len(rows) <= 1500

    def test_every_row_has_the_columns_upsert_expects(self, rows):
        required = {
            "id", "date", "name", "amount", "category", "pending", "institution",
            "plaid_account_id", "user_id", "pfc_primary", "pfc_detailed",
            "pfc_confidence", "payment_channel", "plaid_merchant_name", "counterparties",
        }
        for r in rows:
            assert required <= set(r), f"missing {required - set(r)}"

    def test_transaction_ids_are_unique(self, rows):
        ids = [r["id"] for r in rows]
        assert len(ids) == len(set(ids))

    def test_ids_are_namespaced_so_demo_rows_are_identifiable(self, rows):
        assert all(r["id"].startswith("demo_") for r in rows)

    def test_posts_only_to_the_declared_accounts(self, rows):
        declared = {a[1] for a in demo_accounts()}
        assert {r["plaid_account_id"] for r in rows} <= declared


class TestDeterminism:
    def test_same_day_yields_the_same_money(self):
        a = generate_transactions(1, today=TODAY)
        b = generate_transactions(1, today=TODAY)
        assert [(r["date"], r["amount"], r["name"]) for r in a] == \
               [(r["date"], r["amount"], r["name"]) for r in b]

    def test_ids_still_differ_so_two_demo_users_dont_collide(self):
        a = {r["id"] for r in generate_transactions(1, today=TODAY)}
        b = {r["id"] for r in generate_transactions(2, today=TODAY)}
        assert not (a & b)

    def test_seed_is_pinned(self):
        """Changing the seed silently rewrites every figure in the demo."""
        assert DEMO_SEED == 20260101


class TestTheHardCases:
    """Without these the demo is just a list of groceries."""

    def test_card_payments_are_carried_as_transfers_not_spend(self, rows):
        pay = [r for r in rows if r["pfc_detailed"] == "LOAN_PAYMENTS_CREDIT_CARD_PAYMENT"]
        assert len(pay) >= 12, "expect roughly monthly card payments"
        # Both legs, on different accounts, so paired-transfer detection fires.
        assert {r["plaid_account_id"] for r in pay} == {"demo-checking", "demo-card"}
        assert any(r["amount"] > 0 for r in pay) and any(r["amount"] < 0 for r in pay)

    def test_card_payment_legs_cancel_out(self, rows):
        pay = [r for r in rows if r["pfc_detailed"] == "LOAN_PAYMENTS_CREDIT_CARD_PAYMENT"]
        assert round(sum(r["amount"] for r in pay), 2) == 0.0

    def test_savings_transfers_are_present_and_trusted(self, rows):
        sav = [r for r in rows if r["pfc_detailed"] == "TRANSFER_OUT_SAVINGS"]
        assert len(sav) >= 10
        # core/dedup.py only trusts PFC at VERY_HIGH/HIGH confidence.
        assert all(r["pfc_confidence"] in ("VERY_HIGH", "HIGH") for r in sav)

    def test_p2p_reimbursements_are_credits_on_a_p2p_institution(self, rows):
        p2p = [r for r in rows if r["institution"] == "Venmo"]
        assert len(p2p) >= 6
        assert all(r["amount"] < 0 for r in p2p), "a payback is money coming back"

    def test_reimbursements_carry_a_quoted_memo(self, rows):
        """core.reimbursements extracts the category from the quoted note."""
        import re
        p2p = [r for r in rows if r["institution"] == "Venmo"]
        assert all(re.search(r'"[^"]+"', r["name"]) for r in p2p)

    def test_every_memo_is_pre_resolved_so_signup_calls_no_model(self, rows):
        import re
        cached = demo_memo_cache_entries()
        memos = {
            re.search(r'"([^"]+)"', r["name"]).group(1)
            for r in rows if r["institution"] == "Venmo"
        }
        assert memos <= set(cached), f"unseeded memos would hit the API: {memos - set(cached)}"

    def test_each_payback_is_smaller_than_the_bill_it_repays(self, rows):
        """A share of a bill, not the whole thing — otherwise a category could
        net negative and get floored, hiding the netting the demo is showing."""
        p2p = [abs(r["amount"]) for r in rows if r["institution"] == "Venmo"]
        assert all(amount < 250 for amount in p2p)

    def test_subscriptions_are_fixed_amount_and_monthly(self, rows):
        netflix = [r for r in rows if r["name"] == "NETFLIX.COM"]
        assert len(netflix) >= 10
        assert len({r["amount"] for r in netflix}) == 1, "recurring detection wants a stable amount"
        days = sorted(datetime.date.fromisoformat(r["date"]) for r in netflix)
        gaps = [(b - a).days for a, b in zip(days, days[1:])]
        assert all(25 <= g <= 35 for g in gaps), gaps

    def test_has_refunds(self, rows):
        refunds = [r for r in rows
                   if r["amount"] < 0 and r["institution"] == "Capital One"
                   and r["pfc_primary"] == "GENERAL_MERCHANDISE"]
        assert len(refunds) >= 1

    def test_income_is_negative_and_biweekly(self, rows):
        pay = [r for r in rows if r["category"] == "Income"]
        assert len(pay) >= 24
        assert all(r["amount"] < 0 for r in pay)

    def test_one_month_is_noticeably_heavier(self, rows):
        """Flat months make month-over-month comparison pointless."""
        by_month: dict[str, float] = {}
        for r in rows:
            if r["amount"] > 0:
                by_month[r["date"][:7]] = by_month.get(r["date"][:7], 0) + r["amount"]
        totals = sorted(by_month.values())
        assert totals[-1] > totals[len(totals) // 2] * 1.25


class TestCategories:
    def test_rent_is_not_filed_under_utilities(self, rows):
        """Filed as Utilities it was 45% of the year behind a label that reads
        as power bills."""
        rent = [r for r in rows if "GREENFIELD" in r["name"]]
        assert rent and all(r["category"] == "Housing" for r in rent)

    def test_extra_categories_covers_everything_not_in_the_defaults(self, rows):
        from core.db import DEFAULT_CATEGORIES
        used = {r["category"] for r in rows}
        unknown = used - set(DEFAULT_CATEGORIES) - set(EXTRA_CATEGORIES)
        assert not unknown, f"demo uses categories the user won't have: {unknown}"


class TestPipelineCompatibility:
    """The generated rows have to survive the real classification pipeline."""

    def test_builds_a_dataframe_with_the_expected_signals(self, rows):
        df = pd.DataFrame(rows)
        df["date"] = pd.to_datetime(df["date"])
        df["amount"] = df["amount"].astype(float)
        df["type"] = df["amount"].apply(lambda x: "credit" if x < 0 else "debit")

        from core.dedup import flag_transfers
        flagged = flag_transfers(df)
        assert flagged["is_transfer"].sum() >= 20, "transfer detection found nothing"

        # Spending must not be swamped by transfers.
        spend = flagged[(~flagged["is_transfer"]) & (flagged["type"] == "debit")]
        assert len(spend) > len(flagged) * 0.5

    def test_counterparties_is_valid_json_when_present(self, rows):
        for r in rows:
            if r["counterparties"]:
                parsed = json.loads(r["counterparties"])
                assert isinstance(parsed, list) and parsed[0]["name"]
