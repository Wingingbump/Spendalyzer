"""Tests for core/insights.py — spending aggregations on mock DataFrames."""
import pytest
import pandas as pd
from core.insights import (
    total_spent,
    total_gross_spent,
    spending_by_category,
    spending_by_merchant,
    spending_by_month,
    spending_by_institution,
    spending_by_dow,
    biggest_purchase,
    most_visited_merchant,
    biggest_spending_day,
    transaction_count,
    clean_dict,
)


def make_spending_df(rows: list[dict]) -> pd.DataFrame:
    """
    Build a minimal DataFrame that passes get_clean_spending:
    type=debit, is_transfer=False, is_duplicate=False.
    """
    today = pd.Timestamp.today().normalize()
    defaults = {
        "type": "debit",
        "is_transfer": False,
        "is_duplicate": False,
        "category": "Food",
        "merchant_normalized": "Merchant A",
        "institution": "BankA",
        "name": "Test Transaction",
        "pending": False,
        "plaid_account_id": "acct1",
        "notes": None,
    }
    records = []
    for r in rows:
        row = {**defaults, **r}
        if "date" not in row:
            row["date"] = today
        records.append(row)
    df = pd.DataFrame(records)
    df["date"] = pd.to_datetime(df["date"])
    df["amount"] = df["amount"].astype(float)
    return df


def make_empty_df() -> pd.DataFrame:
    return pd.DataFrame(columns=[
        "date", "amount", "type", "is_transfer", "is_duplicate",
        "category", "merchant_normalized", "institution", "name",
        "pending", "plaid_account_id", "notes",
    ])


class TestGrossVsNetReimbursements:
    def test_gross_ignores_reimbursements_net_subtracts_them(self):
        df = make_spending_df([
            {"amount": 100.0, "category": "Food"},
            {"amount": -30.0, "type": "credit", "is_reimbursement": True, "category": "Food"},
        ])
        assert total_gross_spent(df) == 100.0  # a payback never inflates gross
        assert total_spent(df) == 70.0         # net = gross − reimbursed

    def test_category_shows_amount_after_reimbursement(self):
        df = make_spending_df([
            {"amount": 100.0, "category": "Food"},
            {"amount": -30.0, "type": "credit", "is_reimbursement": True, "category": "Food"},
        ])
        cat = spending_by_category(df)
        assert float(cat[cat["category"] == "Food"]["total"].iloc[0]) == 70.0  # net, not 100

    def test_category_floored_not_negative_from_group_collection(self):
        # Someone collecting for a group: paybacks exceed their own category spend.
        df = make_spending_df([
            {"amount": 50.0, "category": "Travel"},
            {"amount": -900.0, "type": "credit", "is_reimbursement": True, "category": "Travel"},
        ])
        cat = spending_by_category(df)
        assert (cat["total"] >= 0).all()
        assert "Travel" not in set(cat["category"])  # nets to 0 → dropped, never -850

    def test_category_totals_sum_to_net_when_not_over_collected(self):
        df = make_spending_df([
            {"amount": 100.0, "category": "Food"},
            {"amount": 40.0, "category": "Shopping"},
            {"amount": -25.0, "type": "credit", "is_reimbursement": True, "category": "Food"},
        ])
        cat = spending_by_category(df)
        assert round(cat["total"].sum(), 2) == total_spent(df) == 115.0


class TestTotalSpent:
    def test_sums_debit_amounts(self):
        df = make_spending_df([{"amount": 10.0}, {"amount": 20.0}])
        assert total_spent(df) == 30.0

    def test_excludes_transfers(self):
        df = make_spending_df([
            {"amount": 100.0, "is_transfer": True},
            {"amount": 25.0},
        ])
        assert total_spent(df) == 25.0

    def test_excludes_duplicates(self):
        df = make_spending_df([
            {"amount": 50.0, "is_duplicate": True},
            {"amount": 15.0},
        ])
        assert total_spent(df) == 15.0

    def test_empty_df_returns_zero(self):
        assert total_spent(make_empty_df()) == 0.0

    def test_rounds_to_two_decimals(self):
        df = make_spending_df([{"amount": 10.005}])
        result = total_spent(df)
        assert result == round(result, 2)


class TestSpendingByCategory:
    def test_groups_by_category(self):
        df = make_spending_df([
            {"amount": 30.0, "category": "Food"},
            {"amount": 20.0, "category": "Food"},
            {"amount": 50.0, "category": "Travel"},
        ])
        result = spending_by_category(df)
        assert set(result["category"]) == {"Food", "Travel"}

    def test_sums_correctly(self):
        df = make_spending_df([
            {"amount": 30.0, "category": "Food"},
            {"amount": 20.0, "category": "Food"},
        ])
        result = spending_by_category(df)
        food_row = result[result["category"] == "Food"].iloc[0]
        assert food_row["total"] == 50.0

    def test_sorted_by_total_descending(self):
        df = make_spending_df([
            {"amount": 10.0, "category": "Food"},
            {"amount": 100.0, "category": "Travel"},
        ])
        result = spending_by_category(df)
        assert result.iloc[0]["category"] == "Travel"

    def test_pct_sums_to_100(self):
        df = make_spending_df([
            {"amount": 25.0, "category": "A"},
            {"amount": 75.0, "category": "B"},
        ])
        result = spending_by_category(df)
        assert abs(result["pct"].sum() - 100.0) < 0.1

    def test_empty_returns_empty_df(self):
        result = spending_by_category(make_empty_df())
        assert result.empty

    def test_count_column_correct(self):
        df = make_spending_df([
            {"amount": 10.0, "category": "Food"},
            {"amount": 20.0, "category": "Food"},
            {"amount": 5.0, "category": "Food"},
        ])
        result = spending_by_category(df)
        assert result.iloc[0]["count"] == 3


class TestSpendingByMerchant:
    def test_groups_by_merchant(self):
        df = make_spending_df([
            {"amount": 10.0, "merchant_normalized": "Starbucks"},
            {"amount": 5.0, "merchant_normalized": "Starbucks"},
            {"amount": 20.0, "merchant_normalized": "Amazon"},
        ])
        result = spending_by_merchant(df)
        assert "Starbucks" in result["merchant_normalized"].values

    def test_respects_top_n(self):
        merchants = [{"amount": float(i), "merchant_normalized": f"M{i}"} for i in range(1, 15)]
        df = make_spending_df(merchants)
        result = spending_by_merchant(df, top_n=5)
        assert len(result) == 5

    def test_sorted_descending(self):
        df = make_spending_df([
            {"amount": 5.0, "merchant_normalized": "Small"},
            {"amount": 100.0, "merchant_normalized": "Big"},
        ])
        result = spending_by_merchant(df)
        assert result.iloc[0]["merchant_normalized"] == "Big"

    def test_empty_returns_empty_df(self):
        result = spending_by_merchant(make_empty_df())
        assert result.empty


class TestSpendingByDow:
    def test_returns_all_seven_days(self):
        df = make_spending_df([{"amount": 10.0}])
        result = spending_by_dow(df)
        assert len(result) == 7

    def test_days_in_correct_order(self):
        df = make_spending_df([{"amount": 10.0}])
        result = spending_by_dow(df)
        expected = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
        assert list(result["dow"]) == expected

    def test_amounts_sum_correctly(self):
        monday = pd.Timestamp("2025-03-24")  # known Monday
        df = make_spending_df([
            {"amount": 10.0, "date": monday},
            {"amount": 5.0, "date": monday},
        ])
        result = spending_by_dow(df)
        mon_total = result[result["dow"] == "Monday"]["total"].iloc[0]
        assert mon_total == 15.0

    def test_empty_days_have_zero(self):
        monday = pd.Timestamp("2025-03-24")
        df = make_spending_df([{"amount": 10.0, "date": monday}])
        result = spending_by_dow(df)
        tuesday_total = result[result["dow"] == "Tuesday"]["total"].iloc[0]
        assert tuesday_total == 0.0


class TestHighlights:
    def test_biggest_purchase_returns_max(self):
        df = make_spending_df([
            {"amount": 10.0, "name": "Small"},
            {"amount": 500.0, "name": "Big Purchase"},
        ])
        result = biggest_purchase(df)
        assert result is not None
        assert result["amount"] == 500.0

    def test_biggest_purchase_empty_returns_none(self):
        assert biggest_purchase(make_empty_df()) is None

    def test_most_visited_merchant_returns_highest_count(self):
        df = make_spending_df([
            {"amount": 5.0, "merchant_normalized": "Starbucks"},
            {"amount": 5.0, "merchant_normalized": "Starbucks"},
            {"amount": 50.0, "merchant_normalized": "Amazon"},
        ])
        result = most_visited_merchant(df)
        assert result is not None
        assert result["merchant"] == "Starbucks"
        assert result["count"] == 2

    def test_most_visited_merchant_empty_returns_none(self):
        assert most_visited_merchant(make_empty_df()) is None

    def test_biggest_spending_day(self):
        df = make_spending_df([
            {"amount": 100.0, "date": pd.Timestamp("2025-01-10")},
            {"amount": 10.0, "date": pd.Timestamp("2025-01-11")},
        ])
        result = biggest_spending_day(df)
        assert result is not None
        assert result["total"] == 100.0

    def test_biggest_spending_day_empty_returns_none(self):
        assert biggest_spending_day(make_empty_df()) is None


class TestCleanDict:
    def test_converts_numpy_float(self):
        import numpy as np
        d = {"val": np.float64(3.14)}
        result = clean_dict(d)
        assert isinstance(result["val"], float)

    def test_converts_numpy_int(self):
        import numpy as np
        d = {"val": np.int64(42)}
        result = clean_dict(d)
        assert isinstance(result["val"], int)

    def test_converts_numpy_bool(self):
        import numpy as np
        d = {"val": np.bool_(True)}
        result = clean_dict(d)
        assert isinstance(result["val"], bool)

    def test_leaves_native_types_unchanged(self):
        d = {"a": 1, "b": "text", "c": 3.14}
        assert clean_dict(d) == d


class TestReimbursementsAcrossBreakdowns:
    """get_spending() carries reimbursements as negative rows so they net against
    what they paid back. Every breakdown has to floor at zero and count debits
    only — otherwise a payback reads as negative spend or as an extra purchase."""

    @staticmethod
    def _over_collected():
        # Fronted $50 of own spend, collected $900 back for the group.
        return make_spending_df([
            {"amount": 50.0, "merchant_normalized": "Trip", "category": "Travel",
             "institution": "BankA"},
            {"amount": -900.0, "type": "credit", "is_reimbursement": True,
             "merchant_normalized": "Trip", "category": "Travel",
             "institution": "BankA"},
        ])

    def test_merchant_total_never_negative(self):
        result = spending_by_merchant(self._over_collected())
        assert (result["total"] >= 0).all()
        assert "Trip" not in set(result["merchant_normalized"])

    def test_institution_total_never_negative(self):
        result = spending_by_institution(self._over_collected())
        assert (result["total"] >= 0).all()

    def test_month_total_never_negative_but_period_is_kept(self):
        result = spending_by_month(self._over_collected())
        assert (result["total"] >= 0).all()
        assert len(result) == 1  # the month stays on the axis as a zero

    def test_reimbursement_is_not_counted_as_a_purchase(self):
        df = make_spending_df([
            {"amount": 20.0, "merchant_normalized": "Cafe"},
            {"amount": 30.0, "merchant_normalized": "Cafe"},
            {"amount": -10.0, "type": "credit", "is_reimbursement": True,
             "merchant_normalized": "Cafe"},
        ])
        result = spending_by_merchant(df)
        row = result[result["merchant_normalized"] == "Cafe"].iloc[0]
        assert int(row["count"]) == 2      # two purchases, not three rows
        assert float(row["total"]) == 40.0  # 20 + 30 - 10

    def test_most_visited_ranks_on_purchases_only(self):
        df = make_spending_df([
            # One real purchase, but three paybacks trickling in.
            {"amount": 100.0, "merchant_normalized": "Venmo Friend"},
            {"amount": -20.0, "type": "credit", "is_reimbursement": True,
             "merchant_normalized": "Venmo Friend"},
            {"amount": -20.0, "type": "credit", "is_reimbursement": True,
             "merchant_normalized": "Venmo Friend"},
            {"amount": -20.0, "type": "credit", "is_reimbursement": True,
             "merchant_normalized": "Venmo Friend"},
            # Two genuine visits.
            {"amount": 5.0, "merchant_normalized": "Cafe"},
            {"amount": 5.0, "merchant_normalized": "Cafe"},
        ])
        result = most_visited_merchant(df)
        assert result["merchant"] == "Cafe"
        assert result["count"] == 2

    def test_transaction_count_excludes_reimbursements(self):
        df = make_spending_df([
            {"amount": 20.0},
            {"amount": -5.0, "type": "credit", "is_reimbursement": True},
        ])
        assert transaction_count(df) == 1


class TestSpendingByDowThinHistory:
    def test_single_week_reports_actuals_instead_of_zeroing_out(self):
        """Under two weeks of data no weekday can reach 2 observations, so the
        median path would blank the whole chart."""
        monday = pd.Timestamp("2025-03-24")  # known Monday
        df = make_spending_df([
            {"amount": 10.0, "date": monday},
            {"amount": 5.0, "date": monday},
        ])
        result = spending_by_dow(df)
        assert float(result[result["dow"] == "Monday"]["total"].iloc[0]) == 15.0
        assert float(result["total"].sum()) == 15.0

    def test_median_path_still_applies_once_there_are_enough_weeks(self):
        # Three consecutive Mondays: $10, $10, $100 → median 10, not mean 40.
        mondays = [pd.Timestamp("2025-03-24"), pd.Timestamp("2025-03-31"),
                   pd.Timestamp("2025-04-07")]
        df = make_spending_df([
            {"amount": 10.0, "date": mondays[0]},
            {"amount": 10.0, "date": mondays[1]},
            {"amount": 100.0, "date": mondays[2]},
        ])
        result = spending_by_dow(df)
        assert float(result[result["dow"] == "Monday"]["total"].iloc[0]) == 10.0

    def test_sparse_weekday_still_zeroed_when_history_is_long_enough(self):
        mondays = [pd.Timestamp("2025-03-24"), pd.Timestamp("2025-03-31")]
        df = make_spending_df([
            {"amount": 10.0, "date": mondays[0]},
            {"amount": 10.0, "date": mondays[1]},
            # A lone Friday — one observation, not enough for a "typical Friday".
            {"amount": 500.0, "date": pd.Timestamp("2025-03-28")},
        ])
        result = spending_by_dow(df)
        assert float(result[result["dow"] == "Friday"]["total"].iloc[0]) == 0.0
