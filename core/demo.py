"""Provisioning for "Try the demo" accounts.

Each visitor gets their own throwaway account rather than sharing one, so they
can recategorise, set budgets and delete things — the parts of the app worth
trying — without the first visitor who clears the ledger ruining it for
everyone after them.

The account self-destructs: create_demo_user() stamps deletion_scheduled_at,
and the hourly purge in backend/main.py reaps it. No bespoke cleanup job.
"""

from __future__ import annotations

import logging
import secrets

from core import demo_data
from core.db import (
    add_connected_account,
    add_user_category,
    count_live_demo_users,
    create_demo_user,
    save_memo_category,
    upsert_budget,
    seed_category_map,
    upsert_plaid_accounts,
    upsert_transactions,
)

log = logging.getLogger(__name__)

# Ceiling on un-expired demo accounts. Each one writes ~800 rows, so without a
# cap a script hitting the endpoint could fill the database. Tuned well above
# plausible concurrent interest and well below anything that hurts.
MAX_LIVE_DEMO_USERS = 250

DEMO_TTL_HOURS = 24


class DemoCapacityError(RuntimeError):
    """Too many live demo accounts right now."""


def provision_demo_user() -> tuple[int, str]:
    """Create and populate a demo account. Returns (user_id, username).

    Raises DemoCapacityError when the live-demo ceiling is reached.
    """
    if count_live_demo_users() >= MAX_LIVE_DEMO_USERS:
        raise DemoCapacityError(
            f"{MAX_LIVE_DEMO_USERS} demo accounts are already live"
        )

    username = f"demo-{secrets.token_hex(5)}"
    # No one ever signs in with this. A random hash means the row can't be
    # logged into even if the demo endpoint is later removed, rather than
    # leaving an account with a guessable or empty password.
    password_hash = f"demo-no-login-{secrets.token_hex(32)}"

    user_id = create_demo_user(username, password_hash, ttl_hours=DEMO_TTL_HOURS)
    seed_category_map(user_id)
    for name in demo_data.EXTRA_CATEGORIES:
        add_user_category(user_id, name)
    _seed_accounts(user_id)
    _seed_transactions(user_id)
    _seed_memo_cache()
    _seed_budgets(user_id)

    log.info("demo: provisioned %s (user %d)", username, user_id)
    return user_id, username


def _seed_accounts(user_id: int) -> None:
    """Register the three accounts the sample data posts to.

    Without these the account filter shows blank entries, since account_name
    and account_mask come from a join on plaid_accounts. The access token is
    deliberate nonsense — demo accounts are blocked from syncing, and this makes
    an accidental sync fail loudly rather than reach Plaid with a real token.
    """
    connected_id = add_connected_account(
        name="Demo Bank",
        account_type="bank",
        access_token="demo-account-no-plaid-access",
        user_id=user_id,
    )
    upsert_plaid_accounts(connected_id, [
        {
            "account_id": account_id,
            "name": name,
            "official_name": f"{institution} {name}",
            "mask": mask,
            "type": "credit" if subtype == "credit card" else "depository",
            "subtype": subtype,
        }
        for institution, account_id, name, mask, subtype in demo_data.demo_accounts()
    ])


def _seed_transactions(user_id: int) -> None:
    rows = demo_data.generate_transactions(user_id)
    upsert_transactions(rows)
    log.info("demo: seeded %d transactions for user %d", len(rows), user_id)


def _seed_memo_cache() -> None:
    """Pre-resolve the sample P2P memos.

    core.reimbursements consults this shared cache before calling the model, so
    seeding it guarantees demo signup costs nothing and doesn't wait on an API
    round-trip. Idempotent — later demo users find the entries already there.
    """
    for memo, category in demo_data.demo_memo_cache_entries().items():
        try:
            save_memo_category(memo, category)
        except Exception:  # pragma: no cover - cache is an optimisation
            log.warning("demo: could not pre-seed memo %r", memo, exc_info=True)


# Set a little under what the sample year actually spends in each category, so
# the Tracker shows a mix of comfortable and over-budget rather than all green.
_DEMO_BUDGETS = {"Groceries": 850.0, "Food & Drink": 300.0, "Transport": 400.0}


def _seed_budgets(user_id: int) -> None:
    """Without a budget the Tracker and the right-hand panel are empty, which
    makes two of the app's surfaces look broken rather than unconfigured."""
    for category, amount in _DEMO_BUDGETS.items():
        upsert_budget(user_id, category, amount, "monthly")
