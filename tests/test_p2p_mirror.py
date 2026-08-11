"""P2P-mirror dedup: a card-funded Venmo/PayPal payment must not show twice.

The P2P account holds the payee + memo (the survivor); the linked card shows a
generic "Venmo" settlement line (hidden). Cash-outs (opposite signs) are internal
transfers, not duplicates.
"""

import pandas as pd

from core.dedup import flag_p2p_mirror_duplicates, platform_of


def _row(date, name, amount, inst, is_transfer=False):
    return {
        "date": pd.Timestamp(date), "name": name, "amount": amount,
        "institution": inst, "type": "credit" if amount < 0 else "debit",
        "is_transfer": is_transfer, "is_duplicate": False, "dedup_reason": "",
    }


def _run(rows):
    return flag_p2p_mirror_duplicates(pd.DataFrame(rows))


class TestPlatformOf:
    def test_recognises_variants(self):
        assert platform_of("Venmo - Personal") == "venmo"
        assert platform_of("CASH APP") == "cashapp"
        assert platform_of("PP*STEAM") == "paypal"  # PayPal processor prefix

    def test_pp_prefix_does_not_match_app_descriptors(self):
        # "pp*" is a prefix, not a substring — "APP*…" must not read as PayPal.
        assert platform_of("APP*NETFLIX") is None
        assert platform_of("APP*SPOTIFY 8005551234") is None

    def test_none_for_ordinary_merchant(self):
        assert platform_of("Chipotle") is None
        assert platform_of("") is None


class TestFundingDuplicate:
    def test_card_funding_line_is_hidden_p2p_survives(self):
        out = _run([
            _row("2026-07-27", "Venmo", 53.95, "Capital One"),
            _row("2026-07-26", 'Courtney L "Kusshi"', 53.95, "Venmo - Personal"),
        ])
        card = out[out["institution"] == "Capital One"].iloc[0]
        p2p = out[out["institution"] == "Venmo - Personal"].iloc[0]
        assert bool(card["is_duplicate"]) and not bool(p2p["is_duplicate"])
        assert card["dedup_reason"] == "p2p funding (venmo)"

    def test_match_within_multi_day_settlement_window(self):
        out = _run([
            _row("2026-07-06", "Venmo", 8.00, "Capital One"),
            _row("2026-07-03", 'Courtney L "smooth"', 8.00, "Venmo - Personal"),
        ])
        assert bool(out[out["institution"] == "Capital One"].iloc[0]["is_duplicate"])

    def test_card_line_without_a_match_stays_visible(self):
        # Balance-funded payment: card shows nothing to match, so the lone card
        # line (from an unrelated Venmo top-up) must not be hidden without a peer.
        out = _run([_row("2026-07-27", "Venmo", 40.00, "Capital One")])
        assert not bool(out.iloc[0]["is_duplicate"])

    def test_each_card_line_consumes_one_payment(self):
        # Two $5 payments and two $5 card lines -> both hidden, both kept (1:1).
        out = _run([
            _row("2026-07-10", "Venmo", 5.00, "Capital One"),
            _row("2026-07-10", "Venmo", 5.00, "Capital One"),
            _row("2026-07-09", 'A "x"', 5.00, "Venmo - Personal"),
            _row("2026-07-09", 'B "y"', 5.00, "Venmo - Personal"),
        ])
        hidden = out[(out["institution"] == "Capital One") & out["is_duplicate"]]
        assert len(hidden) == 2


class TestCashoutAndNonMatches:
    def test_cashout_credit_becomes_transfer(self):
        # The P2P leg is NOT pre-flagged — the mirror pass must flag both legs
        # itself (self-contained), not rely on flag_transfers having caught it.
        out = _run([
            _row("2026-07-24", "Venmo", -83.67, "Capital One"),
            _row("2026-07-23", "Standard transfer", 83.67, "Venmo - Personal"),
        ])
        card = out[out["institution"] == "Capital One"].iloc[0]
        p2p = out[out["institution"] == "Venmo - Personal"].iloc[0]
        assert bool(card["is_transfer"]) and not bool(card["is_duplicate"])
        assert bool(p2p["is_transfer"])  # survivor leg flagged too
        assert card["dedup_reason"] == "p2p cashout (venmo)"

    def test_received_money_is_untouched(self):
        # Money received on Venmo (a reimbursement) has no card mirror.
        out = _run([_row("2026-07-20", 'Collin Togher "Uber"', -7.00, "Venmo - Personal")])
        assert not bool(out.iloc[0]["is_duplicate"]) and not bool(out.iloc[0]["is_transfer"])

    def test_unrelated_card_purchase_is_untouched(self):
        out = _run([_row("2026-07-15", "Chipotle", 20.00, "Discover")])
        assert not bool(out.iloc[0]["is_duplicate"])

    def test_different_platforms_do_not_cross_match(self):
        # A card "Venmo" line must not be deduped against a PayPal payment.
        out = _run([
            _row("2026-07-10", "Venmo", 30.00, "Capital One"),
            _row("2026-07-10", 'Someone "x"', 30.00, "PayPal"),
        ])
        assert not bool(out[out["institution"] == "Capital One"].iloc[0]["is_duplicate"])
