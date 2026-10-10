"""Runtime feature flags.

Currently just the AI advisor, which is gated because it is the app's only
endpoint that spends money on an LLM call per request, on our Anthropic key,
for any account that can register.

Default-deny on purpose: a missing, misspelled, or unset ADVISOR_ENABLED leaves
the feature OFF. For a cost control the safe state has to be the one you get by
doing nothing, so a bad deploy can't silently re-open the spend.
"""

import os


def _env_flag(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in ("1", "true", "yes", "on")


def advisor_enabled() -> bool:
    """Whether the LLM-backed advisor endpoints (/chat, /chat/stream, /onboard)
    will serve requests.

    Read per call rather than cached at import so a process restart is all that's
    needed to flip it, and so tests can monkeypatch the environment.

    Note this gates only the endpoints that call Claude. The advisor router also
    serves /tracker, /goals and /profile, which are plain database reads that the
    Tracker page and the right-rail Goals section depend on — those stay on.
    """
    return _env_flag("ADVISOR_ENABLED", default=False)


def require_advisor_enabled() -> None:
    """FastAPI dependency guarding the LLM-backed advisor endpoints.

    Used as a dependency rather than an `if` inside each handler so it runs
    *before* the handler body. _build_system_prompt() calls Voyage for a query
    embedding and hits the database, so an in-body check would still cost money
    on a request we intend to reject.
    """
    from fastapi import HTTPException

    if not advisor_enabled():
        raise HTTPException(
            status_code=503,
            detail="The AI advisor is currently disabled.",
        )
