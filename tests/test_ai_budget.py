"""Tests for core/ai_budget.py — the per-rebuild ceiling on AI calls."""
import threading

from core import ai_budget


class TestClaimOutsideBudget:
    def test_unbudgeted_callers_are_never_throttled(self):
        """CLI scripts and backfills run without a budget and must not be capped."""
        assert ai_budget.remaining() is None
        assert all(ai_budget.claim() for _ in range(200))


class TestBudget:
    def test_allows_exactly_the_limit_then_refuses(self):
        with ai_budget.budget(3):
            assert [ai_budget.claim() for _ in range(5)] == [True, True, True, False, False]

    def test_remaining_counts_down(self):
        with ai_budget.budget(2):
            assert ai_budget.remaining() == 2
            ai_budget.claim()
            assert ai_budget.remaining() == 1

    def test_budget_is_released_on_exit(self):
        with ai_budget.budget(1):
            ai_budget.claim()
            assert ai_budget.claim() is False
        assert ai_budget.remaining() is None
        assert ai_budget.claim() is True  # unbudgeted again

    def test_released_even_when_the_body_raises(self):
        try:
            with ai_budget.budget(1):
                raise RuntimeError("build failed")
        except RuntimeError:
            pass
        assert ai_budget.remaining() is None

    def test_nested_budgets_share_the_outer_ceiling(self):
        """Each rebuild step opens no budget of its own, but if one ever did, it
        must not reset the ceiling and hand itself a fresh allowance."""
        with ai_budget.budget(2):
            ai_budget.claim()
            with ai_budget.budget(50):
                assert ai_budget.remaining() == 1
                assert ai_budget.claim() is True
                assert ai_budget.claim() is False
            assert ai_budget.remaining() == 0

    def test_a_later_rebuild_gets_a_fresh_budget(self):
        """Deferred work has to be able to make progress on the next build."""
        with ai_budget.budget(1):
            assert ai_budget.claim() is True
            assert ai_budget.claim() is False
        with ai_budget.budget(1):
            assert ai_budget.claim() is True

    def test_budgets_are_per_thread(self):
        """Builds for different users run on different threadpool threads; one
        exhausting its budget must not throttle another."""
        results: dict[str, bool] = {}

        def worker():
            with ai_budget.budget(1):
                ai_budget.claim()
                results["exhausted_in_thread"] = ai_budget.claim()

        with ai_budget.budget(5):
            t = threading.Thread(target=worker)
            t.start()
            t.join()
            results["outer_still_has_budget"] = ai_budget.claim()

        assert results["exhausted_in_thread"] is False
        assert results["outer_still_has_budget"] is True
