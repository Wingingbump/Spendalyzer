"""Bounded AI usage for work that happens on the request path.

load_data() rebuilds a user's DataFrame inside a per-user lock, and three steps
of that rebuild call Claude:

  1. apply_categories()         — cleaning up a low-confidence merchant name
  2. apply_dedup()              — arbitrating an ambiguous duplicate pair
  3. categorize_reimbursements() — reading a P2P memo

Each is cached, so a warm user costs nothing. But on a cold cache over a long
history it is hundreds of sequential calls, all while every other request for
that user queues behind the same lock — the gateway gives up long before the
build finishes. (This is what the build lock in core/insights.py was reaching
for; coalescing the builds doesn't help if one build takes minutes.)

So each rebuild gets a call budget. Work that doesn't fit falls back to exactly
the same default it already uses when the API errors, and is deliberately NOT
written to any cache — so the next rebuild picks it up and makes progress. A
handful of rebuilds chew through a large backlog without any single request
hanging on it.

Outside a budget (CLI scripts, one-off backfills) there is no ceiling.
"""

import threading
from contextlib import contextmanager

# Per-rebuild ceiling. These are small Haiku calls (~1s each), so this bounds
# the AI share of one rebuild to roughly half a minute.
DEFAULT_BUDGET = 30

# Shared timeout/retry settings for the request-path clients. The SDK's default
# is a 10-minute timeout with retries, which inside the build lock means one
# unlucky call can hold up every request for that user.
CALL_TIMEOUT_SECONDS = 15.0
CALL_MAX_RETRIES = 1

_state = threading.local()


@contextmanager
def budget(limit: int = DEFAULT_BUDGET):
    """Open a call budget for the current thread.

    Reentrant: if a budget is already open, nested callers share it instead of
    starting a fresh one, so the ceiling applies to the whole rebuild rather
    than to each step within it.
    """
    if getattr(_state, "remaining", None) is not None:
        yield
        return
    _state.remaining = limit
    try:
        yield
    finally:
        _state.remaining = None


def claim() -> bool:
    """Reserve one AI call.

    False means the budget is spent and the caller should take its fallback path
    *without* persisting the result. Returns True when no budget is open.
    """
    remaining = getattr(_state, "remaining", None)
    if remaining is None:
        return True
    if remaining <= 0:
        return False
    _state.remaining = remaining - 1
    return True


def remaining() -> int | None:
    """Calls left in the open budget, or None when none is open. For logging."""
    return getattr(_state, "remaining", None)
