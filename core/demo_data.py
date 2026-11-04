"""Deterministic sample data for the demo account.

A visitor who hasn't connected a bank should still see the app doing the thing
it's actually good at. A plain list of groceries and rent would make this look
like any other spend tracker, so the generated year deliberately includes the
cases the classification pipeline exists to solve:

  - Credit-card payments and savings transfers, which must NOT count as spend.
    Carried with the PFC values Plaid would send, because core/dedup.py treats
    PFC as the primary transfer signal and descriptor keywords only as a
    fallback.
  - P2P reimbursements with quoted memos, which net against the category of the
    purchase they're paying back — the gross -> reimbursed -> net panel has
    nothing to show without them.
  - Fixed-amount monthly subscriptions, so recurring detection finds something.
  - A couple of merchant refunds.
  - One heavy travel month, so month-over-month comparison isn't flat.

Everything derives from a fixed seed, so every visitor sees the same story and
tests can assert exact figures.

Memos are written to closely match a real purchase in the same window:
core.reimbursements resolves a memo by fuzzy-matching it against the user's own
debits first and only calls the model when that fails. Matching locally keeps
demo signup free and instant. seed_demo_memo_cache() belt-and-braces that.
"""

from __future__ import annotations

import datetime
import json
import random
import uuid

DEMO_SEED = 20260101

# Account identities. Three accounts, because transfer detection needs money to
# move *between* accounts and P2P reimbursement detection keys on the
# institution name.
CHECKING = ("Chase", "demo-checking", "Everyday Checking", "0114", "checking")
CARD = ("Capital One", "demo-card", "Quicksilver Card", "8822", "credit card")
VENMO = ("Venmo", "demo-venmo", "Venmo Balance", "3390", "checking")

_ACCOUNTS = [CHECKING, CARD, VENMO]


def _pfc(primary: str, detailed: str, confidence: str = "VERY_HIGH") -> dict:
    return {
        "pfc_primary": primary,
        "pfc_detailed": detailed,
        "pfc_confidence": confidence,
    }


# (merchant, category, low, high, PFC primary, PFC detailed)
_EVERYDAY = [
    ("BLUE BOTTLE COFFEE #22", "Food & Drink", 4.25, 7.50, "FOOD_AND_DRINK", "FOOD_AND_DRINK_COFFEE"),
    ("THAI BASIL KITCHEN", "Food & Drink", 14.0, 38.0, "FOOD_AND_DRINK", "FOOD_AND_DRINK_RESTAURANT"),
    ("SAFEWAY #1842", "Groceries", 32.0, 140.0, "FOOD_AND_DRINK", "FOOD_AND_DRINK_GROCERIES"),
    ("TRADER JOES", "Groceries", 18.0, 86.0, "FOOD_AND_DRINK", "FOOD_AND_DRINK_GROCERIES"),
    ("SHELL OIL 574", "Transport", 28.0, 64.0, "TRANSPORTATION", "TRANSPORTATION_GAS"),
    ("UBER TRIP", "Transport", 8.0, 34.0, "TRANSPORTATION", "TRANSPORTATION_TAXIS_AND_RIDE_SHARES"),
    ("TARGET T-2241", "Shopping", 12.0, 120.0, "GENERAL_MERCHANDISE", "GENERAL_MERCHANDISE_SUPERSTORES"),
    ("WALGREENS #553", "Health & Fitness", 6.0, 42.0, "MEDICAL", "MEDICAL_PHARMACIES_AND_SUPPLEMENTS"),
]

# Fixed-amount monthly charges, so recurring detection has clean signal.
_SUBSCRIPTIONS = [
    ("NETFLIX.COM", "Subscriptions", 15.49, 3),
    ("SPOTIFY USA", "Subscriptions", 11.99, 7),
    ("PLANET FITNESS", "Health & Fitness", 24.99, 12),
    ("ADOBE CREATIVE CLD", "Subscriptions", 22.99, 18),
]

# The people who pay the demo user back, and the purchase each memo refers to.
_FRIENDS = ["Sarah Chen", "Marcus Webb", "Priya Raman", "Dani Alvarez"]

# Rent needs a home of its own: filed under Utilities it was 45% of the year's
# spend behind a label that reads as power bills. Not in the default category
# set, so the demo adds it — which also shows custom categories working.
EXTRA_CATEGORIES = ["Housing"]


def _row(user_id, tx_date, name, amount, category, institution, account_id,
         pfc, merchant=None, channel="in store", counterparties=None):
    """One transaction in the shape upsert_transactions() expects."""
    return {
        "id": f"demo_{uuid.uuid4().hex[:20]}",
        "date": tx_date.isoformat(),
        "name": name,
        "amount": round(amount, 2),
        "category": category,
        "pending": False,
        "institution": institution,
        "plaid_account_id": account_id,
        "user_id": user_id,
        "payment_channel": channel,
        "plaid_merchant_name": merchant or name.title(),
        "counterparties": json.dumps(counterparties) if counterparties else None,
        **pfc,
    }


def generate_transactions(user_id: int, today: datetime.date | None = None) -> list[dict]:
    """One year of sample transactions ending today. Deterministic."""
    today = today or datetime.date.today()
    start = today - datetime.timedelta(days=365)
    rng = random.Random(DEMO_SEED)
    rows: list[dict] = []

    # ── Income: salary every other Friday ────────────────────────────────────
    d = start
    while d.weekday() != 4:
        d += datetime.timedelta(days=1)
    while d <= today:
        rows.append(_row(
            user_id, d, "NORTHWIND LABS PAYROLL", -2840.00, "Income",
            "Chase", CHECKING[1], _pfc("INCOME", "INCOME_WAGES"),
            merchant="Northwind Labs", channel="other",
        ))
        d += datetime.timedelta(days=14)

    # ── Rent, 1st of each month ──────────────────────────────────────────────
    d = start.replace(day=1)
    while d <= today:
        if d >= start:
            rows.append(_row(
                user_id, d, "GREENFIELD PROPERTY MGMT", 1850.00, "Housing",
                "Chase", CHECKING[1],
                _pfc("RENT_AND_UTILITIES", "RENT_AND_UTILITIES_RENT"),
                merchant="Greenfield Property", channel="other",
            ))
        d = (d.replace(day=28) + datetime.timedelta(days=7)).replace(day=1)

    # ── Utilities, mid-month, seasonal ───────────────────────────────────────
    d = start.replace(day=1)
    while d <= today:
        bill_day = d.replace(day=min(16, 28))
        if start <= bill_day <= today:
            winter = bill_day.month in (12, 1, 2, 7, 8)
            rows.append(_row(
                user_id, bill_day, "PACIFIC POWER & LIGHT",
                rng.uniform(140, 210) if winter else rng.uniform(70, 120),
                "Utilities", "Chase", CHECKING[1],
                _pfc("RENT_AND_UTILITIES", "RENT_AND_UTILITIES_GAS_AND_ELECTRICITY"),
                merchant="Pacific Power", channel="other",
            ))
        d = (d.replace(day=28) + datetime.timedelta(days=7)).replace(day=1)

    # ── Subscriptions: identical amount, same day each month ─────────────────
    for name, category, amount, day in _SUBSCRIPTIONS:
        d = start.replace(day=1)
        while d <= today:
            charge = d.replace(day=day)
            if start <= charge <= today:
                rows.append(_row(
                    user_id, charge, name, amount, category,
                    "Capital One", CARD[1],
                    _pfc("ENTERTAINMENT", "ENTERTAINMENT_TV_AND_MOVIES")
                    if category == "Subscriptions"
                    else _pfc("PERSONAL_CARE", "PERSONAL_CARE_GYMS_AND_FITNESS_CENTERS"),
                    channel="online",
                ))
            d = (d.replace(day=28) + datetime.timedelta(days=7)).replace(day=1)

    # ── Everyday spending on the card ────────────────────────────────────────
    d = start
    while d <= today:
        # Heavier on weekends, lighter midweek.
        n = rng.choices([0, 1, 2, 3], weights=[18, 40, 30, 12])[0]
        if d.weekday() >= 5:
            n += 1
        for _ in range(n):
            merchant, category, lo, hi, p, det = rng.choice(_EVERYDAY)
            rows.append(_row(
                user_id, d, merchant, rng.uniform(lo, hi), category,
                "Capital One", CARD[1], _pfc(p, det),
                channel="online" if "UBER" in merchant else "in store",
            ))
        d += datetime.timedelta(days=1)

    # ── Credit-card payment: a TRANSFER, not spend ───────────────────────────
    # Two legs on different accounts, which is what flag_paired_transfers looks
    # for. Mislabelling these as spending would roughly double the card total.
    d = start.replace(day=1)
    while d <= today:
        pay_day = d.replace(day=min(22, 28))
        if start <= pay_day <= today:
            amount = round(rng.uniform(900, 1600), 2)
            rows.append(_row(
                user_id, pay_day, "CAPITAL ONE MOBILE PYMT", amount, "Transfer",
                "Chase", CHECKING[1],
                _pfc("LOAN_PAYMENTS", "LOAN_PAYMENTS_CREDIT_CARD_PAYMENT"),
                merchant="Capital One", channel="other",
            ))
            rows.append(_row(
                user_id, pay_day, "CAPITAL ONE PAYMENT THANK YOU", -amount, "Transfer",
                "Capital One", CARD[1],
                _pfc("LOAN_PAYMENTS", "LOAN_PAYMENTS_CREDIT_CARD_PAYMENT"),
                merchant="Capital One", channel="other",
            ))
        d = (d.replace(day=28) + datetime.timedelta(days=7)).replace(day=1)

    # ── Monthly savings transfer: also not spend ─────────────────────────────
    d = start.replace(day=1)
    while d <= today:
        save_day = d.replace(day=min(2, 28))
        if start <= save_day <= today:
            rows.append(_row(
                user_id, save_day, "ONLINE TRANSFER TO SAVINGS", 400.00, "Transfer",
                "Chase", CHECKING[1],
                _pfc("TRANSFER_OUT", "TRANSFER_OUT_SAVINGS"),
                merchant="Chase Savings", channel="other",
            ))
        d = (d.replace(day=28) + datetime.timedelta(days=7)).replace(day=1)

    # ── A heavier travel month, five months back ─────────────────────────────
    trip_start = (today.replace(day=1) - datetime.timedelta(days=150)).replace(day=9)
    for offset, (name, amount, category, p, det) in enumerate([
        ("ALASKA AIRLINES", 612.40, "Travel", "TRAVEL", "TRAVEL_FLIGHTS"),
        ("MARRIOTT BONVOY", 884.15, "Travel", "TRAVEL", "TRAVEL_LODGING"),
        ("SIXT RENT A CAR", 243.80, "Travel", "TRAVEL", "TRAVEL_RENTAL_CARS"),
        ("HARBOUR SEAFOOD CO", 168.90, "Food & Drink", "FOOD_AND_DRINK", "FOOD_AND_DRINK_RESTAURANT"),
    ]):
        day = trip_start + datetime.timedelta(days=offset)
        if start <= day <= today:
            rows.append(_row(
                user_id, day, name, amount, category,
                "Capital One", CARD[1], _pfc(p, det), channel="online",
            ))

    # ── Group dinners the demo user fronts, then gets paid back ──────────────
    # The debit and the memo are deliberately close ("HARBOUR SEAFOOD CO" ->
    # "harbour seafood"), so the memo resolves by matching the user's own spend
    # instead of falling through to a model call.
    paybacks = [
        (trip_start + datetime.timedelta(days=4), "harbour seafood", 168.90, "Food & Drink"),
    ]
    # Deliberately inside the current month: the Overview's
    # gross -> reimbursed -> net card only renders when this month has
    # reimbursements, and that card is the clearest demonstration of what the
    # classification actually buys you.
    recent_bill_day = today - datetime.timedelta(days=min(4, max(1, today.day - 1)))
    if recent_bill_day >= start:
        recent_bill = 164.50
        rows.append(_row(
            user_id, recent_bill_day, "THAI BASIL KITCHEN", recent_bill, "Food & Drink",
            "Capital One", CARD[1],
            _pfc("FOOD_AND_DRINK", "FOOD_AND_DRINK_RESTAURANT"),
        ))
        paybacks.append((recent_bill_day + datetime.timedelta(days=1), "thai basil",
                         recent_bill, "Food & Drink"))

    for months_back in (1, 2, 4, 7):
        anchor = today - datetime.timedelta(days=30 * months_back + 3)
        if anchor < start:
            continue
        bill = round(rng.uniform(120, 240), 2)
        rows.append(_row(
            user_id, anchor, "THAI BASIL KITCHEN", bill, "Food & Drink",
            "Capital One", CARD[1],
            _pfc("FOOD_AND_DRINK", "FOOD_AND_DRINK_RESTAURANT"),
        ))
        paybacks.append((anchor + datetime.timedelta(days=1), "thai basil", bill, "Food & Drink"))

    for when, memo, bill, _cat in paybacks:
        # Two or three friends each cover a share.
        friends = rng.sample(_FRIENDS, rng.choice([2, 3]))
        share = round(bill / (len(friends) + 1), 2)
        for i, friend in enumerate(friends):
            day = when + datetime.timedelta(days=i)
            if day > today:
                continue
            rows.append(_row(
                user_id, day, f'{friend} "{memo}"', -share, "Food & Drink",
                "Venmo", VENMO[1],
                _pfc("TRANSFER_IN", "TRANSFER_IN_ACCOUNT_TRANSFER", "LOW"),
                merchant=friend, channel="online",
                counterparties=[{"name": friend, "type": "user"}],
            ))

    # ── Two merchant refunds (returned purchases) ────────────────────────────
    for days_back, amount in ((47, -64.20), (118, -129.99)):
        day = today - datetime.timedelta(days=days_back)
        if day >= start:
            rows.append(_row(
                user_id, day, "TARGET T-2241", amount, "Shopping",
                "Capital One", CARD[1],
                _pfc("GENERAL_MERCHANDISE", "GENERAL_MERCHANDISE_SUPERSTORES"),
                channel="in store",
            ))

    rows.sort(key=lambda r: r["date"])
    return rows


def demo_memo_cache_entries() -> dict[str, str]:
    """Memo -> category, pre-seeded so demo signup never calls the model.

    core.reimbursements checks this cache before asking Claude. The memos below
    should already resolve by matching the user's own spend, but seeding them
    makes that a guarantee rather than a dependency on a fuzzy-match threshold.
    """
    return {"harbour seafood": "Food & Drink", "thai basil": "Food & Drink"}


def demo_accounts() -> list[tuple]:
    """(institution, plaid_account_id, name, mask, subtype) for each account."""
    return list(_ACCOUNTS)
