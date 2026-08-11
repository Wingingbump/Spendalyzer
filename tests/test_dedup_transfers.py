"""Transfer detection: PFC-driven, with P2P-to-a-person treated as spending.

Regression coverage for the Zelle bug — a Zelle/Venmo payment to a person must
NOT be flagged as a transfer (which would silently drop it from spending). Only
internal account moves and credit-card payments are transfers.
"""

import pandas as pd

from core.dedup import flag_transfers


def _df(rows):
    # flag_transfers reads name/institution and optional PFC columns.
    return pd.DataFrame(rows)


def _is_transfer(row):
    out = flag_transfers(_df([row]))
    return bool(out["is_transfer"].iloc[0])


BASE = {
    "name": "", "institution": "Chase", "account_subtype": "checking",
    "pfc_primary": "", "pfc_detailed": "", "pfc_confidence": "",
}


def r(**over):
    return {**BASE, **over}


class TestP2PIsNotTransfer:
    def test_zelle_to_person_is_spending(self):
        # The reported bug: a Zelle payment to a person was flagged transfer.
        assert not _is_transfer(r(
            name="Zelle payment to John Smith",
            pfc_primary="TRANSFER_OUT",
            pfc_detailed="TRANSFER_OUT_OTHER_TRANSFER_OUT",
            pfc_confidence="LOW",
        ))

    def test_bare_person_name_is_spending(self):
        # Old heuristic flagged any 2-word person name on a bank account.
        assert not _is_transfer(r(name="John Smith", institution="Capital One"))

    def test_venmo_to_person_is_spending(self):
        assert not _is_transfer(r(name="Venmo payment - Jane Doe", institution="Venmo"))


class TestRealTransfers:
    def test_pfc_account_transfer(self):
        assert _is_transfer(r(
            name="Online Banking Transfer",
            pfc_primary="TRANSFER_OUT",
            pfc_detailed="TRANSFER_OUT_ACCOUNT_TRANSFER",
            pfc_confidence="VERY_HIGH",
        ))

    def test_pfc_credit_card_payment(self):
        assert _is_transfer(r(
            name="CHASE CREDIT CRD AUTOPAY",
            pfc_primary="LOAN_PAYMENTS",
            pfc_detailed="LOAN_PAYMENTS_CREDIT_CARD_PAYMENT",
            pfc_confidence="HIGH",
        ))

    def test_venmo_balance_cashout_to_bank(self):
        assert _is_transfer(r(name="Venmo Standard Transfer", institution="Venmo"))

    def test_balance_cashout_on_multiword_p2p_institution(self):
        # Institution match is substring, so "Venmo - Personal" is recognised and a
        # balance-movement descriptor ("Cashout") flags as a transfer.
        assert _is_transfer(r(name="Cashout", institution="Venmo - Personal"))

    def test_keyword_fallback_when_no_pfc(self):
        assert _is_transfer(r(name="WIRE TRANSFER TO SAVINGS"))


class TestPFCShieldsSpend:
    def test_payment_keyword_shielded_by_confident_spend_pfc(self):
        # "PAYMENT" in the descriptor must not override a confident food category.
        assert not _is_transfer(r(
            name="SQ *BLUE BOTTLE PAYMENT",
            institution="Amex",
            account_subtype="credit card",
            pfc_primary="FOOD_AND_DRINK",
            pfc_detailed="FOOD_AND_DRINK_COFFEE",
            pfc_confidence="VERY_HIGH",
        ))

    def test_low_confidence_pfc_does_not_shield(self):
        # If PFC is not confident, the keyword fallback still applies.
        assert _is_transfer(r(
            name="ONLINE TRANSFER TO CHECKING",
            pfc_primary="FOOD_AND_DRINK",
            pfc_confidence="LOW",
        ))


class TestMissingPFCColumns:
    def test_works_without_pfc_columns_at_all(self):
        # Backwards compat: a frame with no PFC columns must not raise.
        df = pd.DataFrame([{"name": "WIRE TRANSFER TO SAVINGS", "institution": "Chase"}])
        out = flag_transfers(df)
        assert bool(out["is_transfer"].iloc[0])
