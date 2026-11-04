"""Tests for the /insights/health feed-quiet check.

This replaced a global "transaction volume down" warning that compared the last
7 days' overall rate against the prior 30. That measured spending behaviour and
reported it as a data fault: a quiet week fired it, under a banner reading
"Data issue detected" next to a Full history sync button that fixes nothing.

The distinction these tests pin: a broken feed is ONE source going silent while
the others keep reporting, not everything slowing down together.
"""
import datetime
from unittest.mock import patch

import pandas as pd
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.dependencies import get_current_user
from backend.routers import insights as ins_router

FAKE_USER = {"id": 1, "username": "testuser"}
TODAY = datetime.date.today()


@pytest.fixture()
def client():
    app = FastAPI()
    app.include_router(ins_router.router)
    app.dependency_overrides[get_current_user] = lambda: FAKE_USER
    return TestClient(app, raise_server_exceptions=True)


def make_df(rows):
    defaults = {
        "id": "t", "name": "Coffee", "amount": 5.0, "category": "Food & Drink",
        "pending": False, "institution": "Chase", "plaid_account_id": "a1",
        "merchant_normalized": "Cafe", "is_transfer": False, "is_duplicate": False,
        "type": "debit",
    }
    out = []
    for i, r in enumerate(rows):
        out.append({**defaults, **r, "id": f"t{i}"})
    df = pd.DataFrame(out)
    df["date"] = pd.to_datetime(df["date"])
    df["amount"] = df["amount"].astype(float)
    return df


def daily(institution, start_days_ago, end_days_ago, per_day=1):
    """Transactions from `start_days_ago` down to `end_days_ago` (exclusive)."""
    rows = []
    for d in range(start_days_ago, end_days_ago, -1):
        for _ in range(per_day):
            rows.append({"institution": institution,
                         "date": TODAY - datetime.timedelta(days=d)})
    return rows


def health(client, df):
    with patch("backend.routers.insights.ins.load_data", return_value=df), \
         patch("core.db.get_item_health", return_value=[]):
        return client.get("/insights/health").json()


def types_of(body):
    return [w["type"] for w in body["warnings"]]


class TestNoFalsePositiveOnAQuietWeek:
    def test_spending_less_everywhere_is_not_a_data_issue(self, client):
        """The reported case: every feed still delivering, just fewer rows.
        Previously fired volume_drop at 0.9/day vs 2.3/day."""
        rows = daily("Capital One", 90, 7, per_day=2) + daily("Discover", 90, 7, per_day=1)
        # The last week is quiet but not silent - all feeds still reporting.
        rows += [
            {"institution": "Capital One", "date": TODAY - datetime.timedelta(days=2)},
            {"institution": "Capital One", "date": TODAY - datetime.timedelta(days=5)},
            {"institution": "Discover", "date": TODAY - datetime.timedelta(days=3)},
        ]
        assert "feed_quiet" not in types_of(health(client, make_df(rows)))

    def test_a_single_recent_transaction_is_enough_to_count_as_alive(self, client):
        rows = daily("Capital One", 90, 7, per_day=3)
        rows += [{"institution": "Capital One", "date": TODAY - datetime.timedelta(days=6)}]
        assert "feed_quiet" not in types_of(health(client, make_df(rows)))

    def test_the_old_volume_drop_warning_is_gone_entirely(self, client):
        rows = daily("Capital One", 90, 7, per_day=4)
        rows += [{"institution": "Capital One", "date": TODAY - datetime.timedelta(days=1)}]
        assert "volume_drop" not in types_of(health(client, make_df(rows)))


class TestStillCatchesARealFailure:
    def test_a_regular_feed_going_silent_is_flagged(self, client):
        """One source stops while another keeps reporting - the actual signal."""
        rows = daily("Capital One", 90, 7, per_day=2)          # stops 7 days ago
        rows += daily("Discover", 90, 0, per_day=1)            # still reporting
        body = health(client, make_df(rows))
        quiet = [w for w in body["warnings"] if w["type"] == "feed_quiet"]
        assert len(quiet) == 1
        assert quiet[0]["institution"] == "Capital One"
        assert "connection may have dropped" in quiet[0]["message"]

    def test_the_healthy_feed_is_not_also_flagged(self, client):
        rows = daily("Capital One", 90, 7, per_day=2) + daily("Discover", 90, 0, per_day=1)
        quiet = [w for w in health(client, make_df(rows))["warnings"] if w["type"] == "feed_quiet"]
        assert [w["institution"] for w in quiet] == ["Capital One"]

    def test_reports_the_usual_rate_so_the_warning_is_judgeable(self, client):
        rows = daily("Capital One", 90, 7, per_day=2)
        quiet = [w for w in health(client, make_df(rows))["warnings"] if w["type"] == "feed_quiet"]
        assert quiet and quiet[0]["expected_per_week"] > 0
        assert quiet[0]["last_seen"]


class TestNoiseGuards:
    def test_a_sparse_account_is_never_flagged(self, client):
        """A card used twice a month shouldn't warn every time it rests."""
        rows = [{"institution": "Amex", "date": TODAY - datetime.timedelta(days=d)}
                for d in (80, 60, 40, 20, 10)]
        assert "feed_quiet" not in types_of(health(client, make_df(rows)))

    def test_manual_entries_are_not_a_feed(self, client):
        rows = daily("Manual", 90, 7, per_day=2)
        assert "feed_quiet" not in types_of(health(client, make_df(rows)))

    def test_no_duplicate_warning_when_the_connection_already_errored(self, client):
        """item_health is authoritative; don't stack a second warning on it."""
        rows = daily("Capital One", 90, 7, per_day=2)
        item = [{"institution_name": "Capital One", "error_code": "ITEM_LOGIN_REQUIRED",
                 "consent_expiration_time": None, "last_successful_update": None,
                 "last_failed_update": None}]
        with patch("backend.routers.insights.ins.load_data", return_value=make_df(rows)), \
             patch("core.db.get_item_health", return_value=item):
            body = client.get("/insights/health").json()
        assert "item_error" in types_of(body)
        assert "feed_quiet" not in types_of(body)

    def test_transfers_do_not_keep_a_dead_feed_looking_alive(self, client):
        rows = daily("Capital One", 90, 7, per_day=2)
        rows += [{"institution": "Capital One", "is_transfer": True,
                  "date": TODAY - datetime.timedelta(days=1)}]
        assert "feed_quiet" in types_of(health(client, make_df(rows)))
