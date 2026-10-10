"""Tests for the shared current-month helpers in core/insights.py.

These exist because each consumer used to compute these figures itself and they
drifted: the Overview hero reported "on pace for $191.00" while the Tracker strip
reported $191.24 for the same month, and two separate budget queries summed
category spend with no zero floor, surfacing "-$0.21 spent of $600.00".
"""
import calendar
from datetime import date

import pandas as pd
import pytest

from core.insights import month_to_date, category_spend_for_month, total_spent


def make_df(rows: list[dict]) -> pd.DataFrame:
    defaults = {
        "type": "debit",
        "is_transfer": False,
        "is_duplicate": False,
        "is_reimbursement": False,
        "is_excluded": False,
        "category": "Food",
        "merchant_normalized": "Merchant A",
        "institution": "BankA",
        "name": "Test Transaction",
        "pending": False,
        "plaid_account_id": "acct1",
        "notes": None,
    }
    df = pd.DataFrame([{**defaults, **r} for r in rows])
    df["date"] = pd.to_datetime(df["date"])
    df["amount"] = df["amount"].astype(float)
    return df


class TestMonthToDate:
    def test_projects_month_to_date_across_the_full_month(self):
        # $10/day for the first 10 days of a 31-day month -> $310 projected.
        today = date(2026, 3, 10)
        rows = [{"date": pd.Timestamp(2026, 3, d), "amount": 10.0} for d in range(1, 11)]
        mtd = month_to_date(make_df(rows), today=today)
        assert mtd["spent"] == 100.0
        assert mtd["daily_rate"] == 10.0
        assert mtd["pace"] == 310.0
        assert mtd["days_elapsed"] == 10
        assert mtd["days_in_month"] == 31
        assert mtd["days_left"] == 21

    def test_ignores_other_months(self):
        today = date(2026, 3, 10)
        rows = [
            {"date": pd.Timestamp(2026, 3, 5), "amount": 50.0},
            {"date": pd.Timestamp(2026, 2, 5), "amount": 900.0},   # last month
            {"date": pd.Timestamp(2026, 4, 5), "amount": 900.0},   # next month
        ]
        assert month_to_date(make_df(rows), today=today)["spent"] == 50.0

    def test_spent_is_net_of_reimbursements(self):
        today = date(2026, 3, 10)
        rows = [
            {"date": pd.Timestamp(2026, 3, 2), "amount": 100.0},
            {"date": pd.Timestamp(2026, 3, 3), "amount": -30.0,
             "type": "credit", "is_reimbursement": True},
        ]
        mtd = month_to_date(make_df(rows), today=today)
        assert mtd["spent"] == 70.0
        assert mtd["spent"] == total_spent(make_df(rows))  # same definition as everywhere else

    def test_empty_month_is_zero_not_a_crash(self):
        today = date(2026, 3, 10)
        mtd = month_to_date(make_df([{"date": pd.Timestamp(2026, 1, 5), "amount": 10.0}]), today=today)
        assert mtd["spent"] == 0.0 and mtd["pace"] == 0.0 and mtd["daily_rate"] == 0.0

    def test_last_day_of_month_pace_equals_spend(self):
        today = date(2026, 2, 28)  # 2026 is not a leap year
        rows = [{"date": pd.Timestamp(2026, 2, d), "amount": 10.0} for d in range(1, 29)]
        mtd = month_to_date(make_df(rows), today=today)
        assert mtd["days_in_month"] == 28
        assert mtd["pace"] == mtd["spent"] == 280.0
        assert mtd["days_left"] == 0

    def test_rounded_once_to_cents(self):
        """The drift was a rounding mismatch — pin the precision."""
        today = date(2026, 10, 10)
        rows = [{"date": pd.Timestamp(2026, 10, 1), "amount": 61.69}]
        mtd = month_to_date(make_df(rows), today=today)
        # 61.69 / 10 * 31 = 191.239 -> 191.24, never 191.0
        assert mtd["pace"] == 191.24


class TestCategorySpendForMonth:
    def test_nets_reimbursements_against_the_category(self):
        today = date(2026, 3, 10)
        rows = [
            {"date": pd.Timestamp(2026, 3, 2), "amount": 100.0, "category": "Food"},
            {"date": pd.Timestamp(2026, 3, 3), "amount": -30.0, "category": "Food",
             "type": "credit", "is_reimbursement": True},
        ]
        assert category_spend_for_month(make_df(rows), today=today)["Food"] == 70.0

    def test_over_reimbursed_category_floors_at_zero(self):
        """A budget must never read "-$0.21 spent of $600.00"."""
        today = date(2026, 3, 10)
        rows = [
            {"date": pd.Timestamp(2026, 3, 2), "amount": 50.0, "category": "Food"},
            {"date": pd.Timestamp(2026, 3, 3), "amount": -900.0, "category": "Food",
             "type": "credit", "is_reimbursement": True},
        ]
        spend = category_spend_for_month(make_df(rows), today=today)
        assert spend["Food"] == 0.0
        assert all(v >= 0 for v in spend.values())

    def test_keeps_zeroed_categories_so_a_budget_still_renders(self):
        """drop_empty=False matters here: a budgeted category that nets to zero
        must still appear, otherwise its card would fall back to 'no spend yet'
        rather than showing $0 against the budget."""
        today = date(2026, 3, 10)
        rows = [
            {"date": pd.Timestamp(2026, 3, 2), "amount": 50.0, "category": "Food"},
            {"date": pd.Timestamp(2026, 3, 3), "amount": -900.0, "category": "Food",
             "type": "credit", "is_reimbursement": True},
        ]
        assert "Food" in category_spend_for_month(make_df(rows), today=today)

    def test_excludes_other_months_and_transfers(self):
        today = date(2026, 3, 10)
        rows = [
            {"date": pd.Timestamp(2026, 3, 2), "amount": 40.0, "category": "Food"},
            {"date": pd.Timestamp(2026, 2, 2), "amount": 500.0, "category": "Food"},
            {"date": pd.Timestamp(2026, 3, 4), "amount": 700.0, "category": "Food",
             "is_transfer": True},
        ]
        assert category_spend_for_month(make_df(rows), today=today)["Food"] == 40.0

    def test_empty_frame_returns_empty_mapping(self):
        today = date(2026, 3, 10)
        rows = [{"date": pd.Timestamp(2026, 1, 1), "amount": 10.0}]
        assert category_spend_for_month(make_df(rows), today=today) == {}


class TestMonthlyEquivalent:
    """The right panel summed only frequency == 'monthly' ($47.93) while the
    Tracker amortised every cadence ($50.26). One helper now serves both."""

    def test_amortises_each_cadence(self):
        from backend.routers.workspace import monthly_equivalent
        assert monthly_equivalent(30.0, "monthly") == 30.0
        assert monthly_equivalent(7.0, "quarterly") == 2.33
        assert monthly_equivalent(120.0, "annual") == 10.0
        assert monthly_equivalent(10.0, "weekly") == 43.3
        assert monthly_equivalent(10.0, "biweekly") == 21.7

    def test_unknown_cadence_passes_through(self):
        from backend.routers.workspace import monthly_equivalent
        assert monthly_equivalent(12.5, "irregular") == 12.5

    def test_quarterly_charge_is_included_not_dropped(self):
        from backend.routers.workspace import monthly_equivalent
        charges = [(27.03, "monthly"), (20.00, "monthly"), (0.90, "monthly"), (7.00, "quarterly")]
        total = round(sum(monthly_equivalent(a, f) for a, f in charges), 2)
        assert total == 50.26          # the Tracker's figure
        assert total != 47.93          # not the right panel's old monthly-only sum


class TestSamePointLastMonthComparison:
    """The hero card used to compare a part-month against a whole one and report
    a ~97% drop on the 10th. These pin the like-for-like comparison."""

    def test_compares_the_same_stretch_of_days(self):
        today = date(2026, 3, 10)
        rows = []
        # This month: $10/day for 10 days = $100
        rows += [{"date": pd.Timestamp(2026, 3, d), "amount": 10.0} for d in range(1, 11)]
        # Last month: $20/day for the whole 28 days = $560, but only the first
        # 10 days ($200) should be compared against.
        rows += [{"date": pd.Timestamp(2026, 2, d), "amount": 20.0} for d in range(1, 29)]
        mtd = month_to_date(make_df(rows), today=today)

        assert mtd["spent"] == 100.0
        assert mtd["prior_month_to_date"] == 200.0      # not the full 560
        assert mtd["prior_month_to_date_days"] == 10
        assert mtd["delta_vs_prior_mtd"] == -100.0
        assert mtd["delta_vs_prior_mtd_pct"] == -50.0   # not -82% vs the full month

    def test_clamps_when_last_month_is_shorter(self):
        """March 31 has no February 31 — compare against all 28 days, and say so."""
        today = date(2026, 3, 31)
        rows = [{"date": pd.Timestamp(2026, 2, d), "amount": 1.0} for d in range(1, 29)]
        mtd = month_to_date(make_df(rows), today=today)
        assert mtd["prior_month_to_date_days"] == 28
        assert mtd["prior_month_to_date"] == 28.0

    def test_crosses_the_year_boundary(self):
        today = date(2026, 1, 5)
        rows = [{"date": pd.Timestamp(2025, 12, d), "amount": 7.0} for d in range(1, 6)]
        mtd = month_to_date(make_df(rows), today=today)
        assert mtd["prior_month_to_date"] == 35.0

    def test_no_prior_spend_yields_zero_pct_not_a_divide_by_zero(self):
        today = date(2026, 3, 10)
        rows = [{"date": pd.Timestamp(2026, 3, 2), "amount": 50.0}]
        mtd = month_to_date(make_df(rows), today=today)
        assert mtd["prior_month_to_date"] == 0.0
        assert mtd["delta_vs_prior_mtd_pct"] == 0.0

    def test_prior_window_is_net_of_reimbursements_like_everything_else(self):
        today = date(2026, 3, 10)
        rows = [
            {"date": pd.Timestamp(2026, 2, 3), "amount": 100.0},
            {"date": pd.Timestamp(2026, 2, 4), "amount": -40.0,
             "type": "credit", "is_reimbursement": True},
        ]
        assert month_to_date(make_df(rows), today=today)["prior_month_to_date"] == 60.0

    def test_later_days_of_last_month_are_excluded(self):
        today = date(2026, 3, 3)
        rows = [
            {"date": pd.Timestamp(2026, 2, 2), "amount": 10.0},   # counted
            {"date": pd.Timestamp(2026, 2, 25), "amount": 900.0},  # after day 3
        ]
        assert month_to_date(make_df(rows), today=today)["prior_month_to_date"] == 10.0
