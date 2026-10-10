"""Per-counterparty transfer memory (core.insights.apply_transfer_overrides).

A user's ruling on a counterparty must win over auto-detection and apply to every
transaction from that payee, in both directions, without disturbing unrelated rows.
"""

import pandas as pd

from core.insights import apply_transfer_overrides


def _df(rows):
    base_cols = {
        "merchant_normalized": None, "is_transfer": False,
        "is_reimbursement": False, "needs_review": False, "dedup_reason": "",
    }
    return pd.DataFrame([{**base_cols, **r} for r in rows])


def test_force_true_hides_a_missed_transfer():
    df = _df([
        {"merchant_normalized": "My Savings", "is_transfer": False},
        {"merchant_normalized": "Chipotle", "is_transfer": False},
    ])
    out = apply_transfer_overrides(df, {"My Savings": True})
    assert out.loc[0, "is_transfer"] is True or bool(out.loc[0, "is_transfer"])
    assert not bool(out.loc[1, "is_transfer"])  # unrelated row untouched
    assert out.loc[0, "dedup_reason"] == "transfer (user)"


def test_force_false_unhides_a_false_positive():
    # A Zelle to a person that auto-detection wrongly flagged; user says "not a transfer".
    df = _df([{"merchant_normalized": "John Smith", "is_transfer": True}])
    out = apply_transfer_overrides(df, {"John Smith": False})
    assert not bool(out.loc[0, "is_transfer"])


def test_force_true_clears_reimbursement_and_review():
    df = _df([{
        "merchant_normalized": "Jane Doe", "is_transfer": False,
        "is_reimbursement": True, "needs_review": True,
    }])
    out = apply_transfer_overrides(df, {"Jane Doe": True})
    assert bool(out.loc[0, "is_transfer"])
    assert not bool(out.loc[0, "is_reimbursement"])
    assert not bool(out.loc[0, "needs_review"])


def test_applies_to_all_rows_from_the_same_counterparty():
    df = _df([
        {"merchant_normalized": "Landlord LLC", "is_transfer": True},
        {"merchant_normalized": "Landlord LLC", "is_transfer": True},
        {"merchant_normalized": "Landlord LLC", "is_transfer": True},
    ])
    out = apply_transfer_overrides(df, {"Landlord LLC": False})
    assert not out["is_transfer"].any()


def test_no_overrides_is_a_noop():
    df = _df([{"merchant_normalized": "Chipotle", "is_transfer": False}])
    out = apply_transfer_overrides(df, {})
    assert not bool(out.loc[0, "is_transfer"])


def test_empty_df_does_not_raise():
    df = pd.DataFrame(columns=["merchant_normalized", "is_transfer"])
    out = apply_transfer_overrides(df, {"X": True})
    assert out.empty
