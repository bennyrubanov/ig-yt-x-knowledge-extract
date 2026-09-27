"""The paid-request ledger must fail closed across restarts and processes."""
from __future__ import annotations

import tempfile
import threading
import unittest
from pathlib import Path

from string_usage import Ledger, UsageBlocked


class StringUsageTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.path = Path(temp.name) / "usage.sqlite3"
        self.now = [1_000.0]
        self.ledger = Ledger(self.path, clock=lambda: self.now[0])

    def run_with(self, budget="0.03", **kwargs):
        self.ledger.create_run("queue1", budget, **kwargs)

    def test_price_estimate_and_unknown_actual_are_separate(self):
        self.run_with()
        attempt = self.ledger.reserve("queue1", "abc123")
        before = self.ledger.report("queue1")
        self.assertEqual(before["reserved_outstanding_micro"], 6000)
        self.assertEqual(before["cost_envelope_micro"], 6000)
        settled = self.ledger.complete(attempt, 200, billed_request_type="request_standard")
        self.assertEqual(settled["estimated_micro"], 300)
        self.assertEqual(settled["known_actual_micro"], 0)
        self.assertEqual(settled["unknown_actual_count"], 1)
        self.assertEqual(settled["cost_envelope_micro"], 6000)
        self.assertIsNone(settled["attempt_records"][0]["actual_micro"])
        self.assertIsNone(settled["global_hold"])

    def test_pending_survives_restart_and_blocks_new_run(self):
        self.run_with()
        self.ledger.reserve("queue1", "abc123")
        reopened = Ledger(self.path, clock=lambda: self.now[0])
        with self.assertRaisesRegex(UsageBlocked, "pending_attempt"):
            reopened.reserve("queue1", "def456")
        with self.assertRaisesRegex(UsageBlocked, "pending_attempt"):
            reopened.create_run("queue2", "1")
        self.assertEqual(reopened.report()["reserved_outstanding_micro"], 6000)

    def test_missing_billing_class_latches_hold_across_runs(self):
        self.run_with()
        attempt = self.ledger.reserve("queue1", "abc123")
        result = self.ledger.complete(attempt, 200, billed_request_type=None)
        self.assertEqual(result["global_hold"], "unknown_billing_class")
        self.assertEqual(result["cost_envelope_micro"], 6000)
        self.assertEqual(result["unknown_actual_count"], 1)
        reopened = Ledger(self.path)
        with self.assertRaisesRegex(UsageBlocked, "global_hold"):
            reopened.create_run("queue2", "1")

    def test_expensive_class_or_actual_above_reservation_holds(self):
        self.run_with(max_request_usd="0.003")
        attempt = self.ledger.reserve("queue1", "abc123", expected_billing_class="browser_premium")
        result = self.ledger.complete(
            attempt, 200, billed_request_type="browser_premium", actual_usd="0.007"
        )
        self.assertEqual(result["global_hold"], "cost_above_reservation")
        self.assertEqual(result["estimated_micro"], 6000)
        self.assertEqual(result["known_actual_micro"], 7000)
        self.assertEqual(result["cost_envelope_micro"], 7000)

    def test_expensive_class_without_actual_exceeds_reservation_envelope(self):
        self.run_with(max_request_usd="0.0003")
        attempt = self.ledger.reserve("queue1", "abc123")
        result = self.ledger.complete(attempt, 200, "browser_premium")
        self.assertEqual(result["global_hold"], "class_above_reservation")
        self.assertEqual(result["reserved_outstanding_micro"], 0)
        self.assertEqual(result["estimated_micro"], 6000)
        self.assertEqual(result["unknown_actual_count"], 1)
        self.assertEqual(result["cost_envelope_micro"], 6000)

    def test_run_constraints_are_immutable_and_invalid_amounts_rejected(self):
        self.run_with()
        with self.assertRaisesRegex(ValueError, "immutable"):
            self.ledger.create_run("queue1", "1")
        for bad in ("NaN", "Infinity", "-0.01", "0"):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                self.ledger.create_run("other", bad)
        self.assertEqual(self.ledger.report("queue1")["attempt_count"], 0)

    def test_budget_exhaustion_latches_hold_and_new_run_cannot_bypass(self):
        self.run_with(budget="0.006", min_gap_seconds=0)
        attempt = self.ledger.reserve("queue1", "abc123", expected_billing_class="browser_premium")
        self.ledger.complete(attempt, 200, billed_request_type="browser_premium")
        with self.assertRaisesRegex(UsageBlocked, "budget_limit"):
            self.ledger.reserve("queue1", "def456")
        self.assertEqual(self.ledger.report()["global_hold"], "budget_limit")
        with self.assertRaisesRegex(UsageBlocked, "global_hold"):
            self.ledger.create_run("queue2", "1")

    def test_dedupe_failed_attempt_and_minimum_gap(self):
        self.run_with()
        attempt = self.ledger.reserve("queue1", "abc123")
        self.ledger.complete(attempt, 200, billed_request_type="request_standard")
        with self.assertRaisesRegex(UsageBlocked, "source_already_attempted"):
            self.ledger.reserve("queue1", "abc123")
        with self.assertRaises(UsageBlocked) as gap:
            self.ledger.reserve("queue1", "def456")
        self.assertEqual(gap.exception.reason, "minimum_gap")
        self.assertEqual(gap.exception.wait_seconds, 10)
        self.now[0] += 10
        self.assertIsInstance(self.ledger.reserve("queue1", "def456"), int)

    def test_burn_limit_latches_before_burst(self):
        self.run_with(budget="1", min_gap_seconds=0, burn_limit_usd="0.006")
        attempt = self.ledger.reserve("queue1", "abc123", expected_billing_class="browser_premium")
        self.ledger.complete(attempt, 200, billed_request_type="browser_premium")
        with self.assertRaisesRegex(UsageBlocked, "burn_limit"):
            self.ledger.reserve("queue1", "def456")
        self.assertEqual(self.ledger.report()["global_hold"], "burn_limit")

    def test_unexpected_premium_class_stops_immediately(self):
        self.run_with()
        attempt = self.ledger.reserve("queue1", "abc123", expected_billing_class="request_standard")
        report = self.ledger.complete(attempt, 200, billed_request_type="request_premium")
        self.assertEqual(report["global_hold"], "unexpected_billing_class")
        self.assertEqual(report["estimated_micro"], 3000)
        self.assertEqual(report["cost_envelope_micro"], 6000)

    def test_rate_jump_from_first_verified_class_holds(self):
        self.run_with(min_gap_seconds=0)
        first = self.ledger.reserve("queue1", "abc123")
        self.ledger.complete(first, 200, "request_standard")
        second = self.ledger.reserve("queue1", "def456")
        report = self.ledger.complete(second, 200, "request_premium")
        self.assertEqual(report["global_hold"], "rate_jump")

    def test_parallel_reserve_can_only_create_one_pending(self):
        self.run_with(min_gap_seconds=0)
        barrier = threading.Barrier(2)
        results = []
        lock = threading.Lock()

        def reserve(source):
            ledger = Ledger(self.path, clock=lambda: self.now[0])
            barrier.wait()
            try:
                result = ledger.reserve("queue1", source)
            except UsageBlocked as exc:
                result = exc.reason
            with lock:
                results.append(result)

        threads = [threading.Thread(target=reserve, args=(source,)) for source in ("abc123", "def456")]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        self.assertEqual(sum(isinstance(item, int) for item in results), 1)
        self.assertIn("pending_attempt_requires_reconciliation", results)
        self.assertEqual(self.ledger.report()["attempt_count"], 1)

    def test_sensitive_error_never_enters_report_and_filing_requires_file(self):
        self.run_with()
        attempt = self.ledger.reserve("queue1", "abc123")
        report = self.ledger.complete(
            attempt, 502, billed_request_type="request_standard",
            error="https://private.example/?token=SECRET",
        )
        self.assertNotIn("SECRET", str(report))
        self.assertEqual(report["global_hold"], "provider_denial")
        with self.assertRaises(ValueError):
            self.ledger.mark_filed(attempt, self.path.parent / "missing.md")
        with self.assertRaises(ValueError):
            self.ledger.mark_media(attempt)

    def test_filing_requires_verified_local_note(self):
        self.run_with()
        attempt = self.ledger.reserve("queue1", "abc123")
        self.ledger.complete(attempt, 200, "request_standard")
        with self.assertRaises(ValueError):
            self.ledger.mark_filed(attempt, self.path.parent / "missing.md")
        note = self.path.parent / "note.md"
        note.write_text("Knowledge filed")
        self.ledger.mark_media(attempt)
        self.ledger.mark_filed(attempt, note)
        self.assertEqual(self.ledger.report()["filed_count"], 1)


if __name__ == "__main__":
    unittest.main()
