"""Tests for the ADVISOR_ENABLED feature flag.

The advisor is the only endpoint that makes an LLM call per request on our own
API key, reachable by anyone who can register. These tests pin the two things
that make the flag a real cost control: it is default-deny, and when it is off
the expensive endpoints reject the request *before* doing any paid work.
"""
import os
from unittest.mock import patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.dependencies import get_current_user
from backend.features import advisor_enabled
from backend.routers import advisor as advisor_router

FAKE_USER = {"id": 1, "username": "testuser"}


@pytest.fixture()
def client():
    app = FastAPI()
    app.include_router(advisor_router.router)
    app.dependency_overrides[get_current_user] = lambda: FAKE_USER
    return TestClient(app, raise_server_exceptions=False)


class TestFlagParsing:
    def test_unset_is_off(self):
        """Default-deny: doing nothing must leave the spend closed."""
        with patch.dict(os.environ, {}, clear=True):
            assert advisor_enabled() is False

    @pytest.mark.parametrize("value", ["true", "TRUE", "True", "1", "yes", "on", " true "])
    def test_truthy_values_enable(self, value):
        with patch.dict(os.environ, {"ADVISOR_ENABLED": value}):
            assert advisor_enabled() is True

    @pytest.mark.parametrize("value", ["false", "0", "no", "off", "", "maybe", "ture"])
    def test_everything_else_disables(self, value):
        """A typo must fail closed, not silently re-open the endpoint."""
        with patch.dict(os.environ, {"ADVISOR_ENABLED": value}):
            assert advisor_enabled() is False

    def test_read_per_call_not_cached_at_import(self):
        with patch.dict(os.environ, {"ADVISOR_ENABLED": "true"}):
            assert advisor_enabled() is True
        with patch.dict(os.environ, {"ADVISOR_ENABLED": "false"}):
            assert advisor_enabled() is False


class TestDisabledEndpoints:
    """The three endpoints that call Claude must refuse when the flag is off."""

    @pytest.mark.parametrize("path,body", [
        ("/advisor/chat", {"message": "hello"}),
        ("/advisor/chat/stream", {"message": "hello"}),
        ("/advisor/onboard", {"message": "hello"}),
    ])
    def test_returns_503_when_disabled(self, client, path, body):
        with patch.dict(os.environ, {"ADVISOR_ENABLED": "false"}):
            r = client.post(path, json=body)
        assert r.status_code == 503

    @pytest.mark.parametrize("path,body", [
        ("/advisor/chat", {"message": "hello"}),
        ("/advisor/chat/stream", {"message": "hello"}),
        ("/advisor/onboard", {"message": "hello"}),
    ])
    def test_no_paid_work_happens_before_the_rejection(self, client, path, body):
        """The gate is a dependency, not an in-body check, specifically so the
        request is refused before _build_system_prompt() runs — that function
        calls Voyage for a query embedding and would cost money on a request we
        are rejecting anyway."""
        with patch.dict(os.environ, {"ADVISOR_ENABLED": "false"}), \
             patch.object(advisor_router, "_anthropic") as mock_claude, \
             patch.object(advisor_router, "get_query_embedding") as mock_embed, \
             patch.object(advisor_router, "_build_system_prompt") as mock_prompt:
            r = client.post(path, json=body)

        assert r.status_code == 503
        mock_claude.messages.create.assert_not_called()
        mock_claude.messages.stream.assert_not_called()
        mock_embed.assert_not_called()
        mock_prompt.assert_not_called()


class TestDataEndpointsStayUp:
    """Disabling the advisor must not take the Tracker page or the Goals panel
    with it — those read /advisor/tracker and /advisor/goals, which are plain
    database queries with no LLM call."""

    def test_goals_still_served_when_advisor_disabled(self, client):
        with patch.dict(os.environ, {"ADVISOR_ENABLED": "false"}), \
             patch.object(advisor_router, "list_goals", return_value=[]):
            r = client.get("/advisor/goals")
        assert r.status_code == 200

    def test_only_the_three_ai_routes_carry_the_gate(self):
        """If a new LLM endpoint is added without the gate, or the gate spreads
        to a data route, this fails and makes someone look."""
        gated = set()
        for route in advisor_router.router.routes:
            deps = getattr(route, "dependant", None)
            if deps is None:
                continue
            names = [
                d.call.__name__
                for d in deps.dependencies
                if getattr(d, "call", None) is not None
            ]
            if "require_advisor_enabled" in names:
                gated.add(route.path)
        assert gated == {"/advisor/chat", "/advisor/chat/stream", "/advisor/onboard"}
