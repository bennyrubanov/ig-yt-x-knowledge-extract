"""The paid-request ledger must fail closed across restarts and processes."""
from __future__ import annotations

import tempfile
import threading
import unittest
import json
import sqlite3
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path

from string_usage import Ledger, UsageBlocked, recommended_run_budget_usd


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

    def test_recommended_budget_uses_three_times_observed_cost_with_headroom(self):
        self.assertEqual(recommended_run_budget_usd(2), "0.05")
        self.assertEqual(recommended_run_budget_usd(3), "0.05")
        self.assertEqual(recommended_run_budget_usd(8), "0.10")
        self.assertEqual(recommended_run_budget_usd(26), "0.25")
        self.assertEqual(recommended_run_budget_usd(30), "0.30")
        with self.assertRaisesRegex(ValueError, "smaller bounded run"):
            recommended_run_budget_usd(112)
        with self.assertRaisesRegex(ValueError, "approved"):
            recommended_run_budget_usd(1, "0.007")
        with self.assertRaisesRegex(ValueError, "per-run maximum"):
            self.ledger.create_run("too_many", "1", max_requests=112)

    def test_budget_exhaustion_stops_only_that_run(self):
        self.run_with(budget="0.006", min_gap_seconds=0)
        attempt = self.ledger.reserve("queue1", "abc123", expected_billing_class="browser_premium")
        self.ledger.complete(attempt, 200, billed_request_type="browser_premium",
                             actual_usd="0.006")
        with self.assertRaisesRegex(UsageBlocked, "budget_limit"):
            self.ledger.reserve("queue1", "def456")
        self.assertIsNone(self.ledger.report()["global_hold"])
        self.ledger.create_run("queue2", recommended_run_budget_usd(2),
                               max_requests=2, min_gap_seconds=0)
        second = self.ledger.reserve("queue2", "def456")
        self.ledger.complete(second, 200, billed_request_type="request_premium",
                             actual_usd="0.003")
        with self.assertRaisesRegex(UsageBlocked, "budget_limit"):
            self.ledger.reserve("queue1", "ghi789")

    def test_new_run_requires_prior_actual_charge_reconciliation(self):
        self.ledger.create_run("queue1", "0.05", max_requests=1, min_gap_seconds=0)
        attempt = self.ledger.reserve("queue1", "abc123")
        self.ledger.complete(attempt, 200, billed_request_type="request_premium")
        with self.assertRaisesRegex(UsageBlocked, "unreconciled_actual_charge"):
            self.ledger.create_run("queue2", "0.05", max_requests=1)
        self.assertIsNone(self.ledger.report()["global_hold"])

    def test_request_limit_stops_only_that_run_and_one_dollar_cap_is_enforced(self):
        self.ledger.create_run("queue1", "0.05", max_requests=1, min_gap_seconds=0)
        attempt = self.ledger.reserve("queue1", "abc123")
        self.ledger.complete(attempt, 200, billed_request_type="request_premium",
                             actual_usd="0.003")
        with self.assertRaisesRegex(UsageBlocked, "request_limit"):
            self.ledger.reserve("queue1", "def456")
        self.assertIsNone(self.ledger.report()["global_hold"])
        with self.assertRaisesRegex(ValueError, r"approved \$1"):
            self.ledger.create_run("too_large", "1.01")
        self.ledger.create_run("queue2", "0.05", max_requests=1, min_gap_seconds=0)
        self.assertIsInstance(self.ledger.reserve("queue2", "def456"), int)

    def test_dedupe_failed_attempt_and_minimum_gap(self):
        self.run_with()
        attempt = self.ledger.reserve("queue1", "abc123")
        self.ledger.complete(attempt, 200, billed_request_type="request_standard")
        with self.assertRaisesRegex(UsageBlocked, "source_already_attempted"):
            self.ledger.reserve("queue1", "abc123")
        with self.assertRaises(UsageBlocked) as gap:
            self.ledger.reserve("queue1", "def456")
        self.assertEqual(gap.exception.reason, "minimum_gap")
        self.assertEqual(gap.exception.wait_seconds, 15)
        self.now[0] += 15
        self.assertIsInstance(self.ledger.reserve("queue1", "def456"), int)

    def test_fetching_as_fast_as_the_default_gap_allows_never_trips_the_burn_limit(self):
        self.ledger.create_run("queue1", "1", max_requests=30)
        for i in range(20):
            while True:
                try:
                    attempt = self.ledger.reserve("queue1", f"src{i}", expected_billing_class="browser_premium")
                    break
                except UsageBlocked as blocked:
                    self.assertEqual(blocked.reason, "minimum_gap")
                    self.now[0] += blocked.wait_seconds
            self.ledger.complete(attempt, 200, billed_request_type="browser_premium")
        self.assertIsNone(self.ledger.report()["global_hold"])

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

    def test_price_list_settlement_releases_only_a_clean_run(self):
        self.run_with(min_gap_seconds=0)
        for source in ("abc123", "def456"):
            self.ledger.complete(self.ledger.reserve("queue1", source), 200, "request_premium")
        report = self.ledger.settle_from_price_list("queue1", "Cloud run settled at list price")
        self.assertEqual(report["known_actual_micro"], 6000)
        self.assertEqual(report["unknown_actual_count"], 0)
        self.assertEqual({r["actual_source"] for r in report["attempt_records"]}, {"price_list"})
        self.ledger.create_run("queue2", "0.05", max_requests=1)

    def test_price_list_settlement_refuses_pending_holds_and_unknown_classes(self):
        self.run_with(min_gap_seconds=0)
        first = self.ledger.reserve("queue1", "abc123")
        self.ledger.complete(first, 200, "request_premium")
        self.ledger.reserve("queue1", "def456")
        with self.assertRaisesRegex(UsageBlocked, "pending_attempt"):
            self.ledger.settle_from_price_list("queue1", "Cloud run settled at list price")
        self.assertIsNone(self.ledger.report("queue1")["attempt_records"][0]["actual_micro"])
        self.setUp()
        self.run_with(min_gap_seconds=0)
        self.ledger.complete(self.ledger.reserve("queue1", "abc123"), 200, billed_request_type=None)
        with self.assertRaisesRegex(UsageBlocked, "global_hold"):
            self.ledger.settle_from_price_list("queue1", "Cloud run settled at list price")
        with self.assertRaisesRegex(UsageBlocked, "global_hold"):
            self.ledger.create_run("queue2", "0.05", max_requests=1)

    def test_portal_reconciliation_replaces_a_price_list_charge(self):
        self.run_with(min_gap_seconds=0)
        self.ledger.complete(self.ledger.reserve("queue1", "abc123"), 200, "request_premium")
        self.ledger.settle_from_price_list("queue1", "Cloud run settled at list price")
        path = self.portal_evidence(["abc123"], total="0.0024")
        data = json.loads(path.read_text())
        data["rows"][0]["actual_usd"] = "0.0024"
        path.write_text(json.dumps(data))
        report = self.ledger.reconcile_portal("queue1", path, "Monthly portal check of cloud runs")
        self.assertEqual(report["known_actual_micro"], 2400)
        self.assertEqual(report["attempt_records"][0]["actual_source"], "portal_reconciled")
        self.assertEqual(report["reconciliations"][0]["prior_actual_micro"], 3000)

    def unauthenticated_hold(self):
        self.run_with(min_gap_seconds=0)
        attempt = self.ledger.reserve("queue1", "abc123")
        self.ledger.complete(attempt, 401, None, error="provider_http_error")
        self.assertEqual(self.ledger.report()["hold_reason"], "provider_denial")
        return attempt

    def test_unauthenticated_release_needs_a_passing_check_and_allows_one_retry(self):
        attempt = self.unauthenticated_hold()
        note = "String refused the placeholder key; nothing fetched"
        with self.assertRaisesRegex(UsageBlocked, "passing_access_check_required"):
            self.ledger.resume_reviewed_unauthenticated("queue1", note)
        self.ledger.record_access_check(200)
        self.ledger.record_access_check(401)
        with self.assertRaisesRegex(UsageBlocked, "passing_access_check_required"):
            self.ledger.resume_reviewed_unauthenticated("queue1", note)
        self.ledger.record_access_check(200)
        report = self.ledger.resume_reviewed_unauthenticated("queue1", note)
        self.assertIsNone(report["hold_reason"])
        record = report["attempt_records"][0]
        self.assertEqual((record["actual_micro"], record["actual_source"]), (0, "unauthenticated"))
        self.assertEqual(report["unauthenticated_retries"][0]["parent_attempt_id"], attempt)
        self.ledger.create_run("queue2", "0.03", min_gap_seconds=0)
        self.assertEqual(self.ledger.unauthenticated_retry_for("abc123"), attempt)
        with self.assertRaisesRegex(UsageBlocked, "source_already_attempted"):
            self.ledger.reserve("queue2", "abc123")
        with self.assertRaisesRegex(UsageBlocked, "recovery_not_authorized"):
            self.ledger.reserve("queue2", "abc123", recovery_of=attempt,
                                recovery_source_url="https://www.instagram.com/reel/other1/")
        retry = self.ledger.reserve("queue2", "abc123", recovery_of=attempt,
                                    recovery_source_url="https://www.instagram.com/reel/abc123/")
        self.ledger.complete(retry, 200, "request_premium")
        self.assertIsNone(self.ledger.unauthenticated_retry_for("abc123"))
        with self.assertRaisesRegex(UsageBlocked, "recovery_not_authorized"):
            self.ledger.reserve("queue2", "abc123", recovery_of=attempt,
                                recovery_source_url="https://www.instagram.com/reel/abc123/")

    def test_unauthenticated_retry_counts_toward_the_request_limit(self):
        attempt = self.unauthenticated_hold()
        self.ledger.record_access_check(200)
        self.ledger.resume_reviewed_unauthenticated("queue1", "String refused the placeholder key")
        self.ledger.create_run("queue2", "0.03", max_requests=1, min_gap_seconds=0)
        retry = self.ledger.reserve("queue2", "abc123", recovery_of=attempt,
                                    recovery_source_url="https://www.instagram.com/reel/abc123/")
        self.ledger.complete(retry, 200, "request_premium")
        with self.assertRaisesRegex(UsageBlocked, "request_limit"):
            self.ledger.reserve("queue2", "def456")

    def lost_capture(self, source="abc123"):
        """A paid, usable, settled capture that was never filed."""
        if not self.ledger.report()["runs"]:
            self.run_with(min_gap_seconds=0)
        attempt = self.ledger.reserve("queue1", source)
        self.ledger.complete(attempt, 200, "request_premium")
        self.ledger.mark_media(attempt, True)
        return attempt

    def test_lost_capture_retry_allows_one_rerequest_in_a_later_run(self):
        attempt = self.lost_capture()
        url = "https://www.instagram.com/p/abc123/"
        direction = "Benny approved one re-capture of this save"
        with self.assertRaisesRegex(UsageBlocked, "requires_settled_usable_unfiled"):
            self.ledger.authorize_lost_capture_retry(attempt, url, direction)  # charge unreconciled
        self.ledger.settle_from_price_list("queue1", "Cloud run settled at list price")
        with self.assertRaisesRegex(ValueError, "user_direction"):
            self.ledger.authorize_lost_capture_retry(attempt, url, "yes")
        with self.assertRaisesRegex(UsageBlocked, "requires_settled_usable_unfiled"):
            self.ledger.authorize_lost_capture_retry(attempt, "https://www.instagram.com/p/other1/", direction)
        report = self.ledger.authorize_lost_capture_retry(attempt, url, direction)
        self.assertEqual(report["lost_capture_retries"][0]["parent_attempt_id"], attempt)
        self.assertIn("user_directed_lost_capture_retry_authorized", [e["code"] for e in report["events"]])
        with self.assertRaisesRegex(UsageBlocked, "already_authorized"):
            self.ledger.authorize_lost_capture_retry(attempt, url, direction)
        self.ledger.create_run("queue2", "0.03", min_gap_seconds=0)
        self.assertEqual(self.ledger.lost_capture_retry_for("abc123"), attempt)
        with self.assertRaisesRegex(UsageBlocked, "source_already_attempted"):
            self.ledger.reserve("queue2", "abc123")
        retry = self.ledger.reserve("queue2", "abc123", recovery_of=attempt,
                                    recovery_source_url="https://www.instagram.com/reel/abc123/")
        self.ledger.complete(retry, 200, "request_premium")
        report = self.ledger.report("queue2")
        self.assertEqual(report["attempt_records"][0]["recovery_of"], attempt)
        self.assertIn("lost_capture_retry_reserved", [e["code"] for e in report["events"]])
        self.assertIsNone(self.ledger.lost_capture_retry_for("abc123"))
        with self.assertRaisesRegex(UsageBlocked, "recovery_not_authorized"):
            self.ledger.reserve("queue2", "abc123", recovery_of=attempt,
                                recovery_source_url="https://www.instagram.com/reel/abc123/")
        with self.assertRaisesRegex(UsageBlocked, "already"):
            self.ledger.authorize_lost_capture_retry(attempt, url, direction)

    def test_lost_capture_retry_counts_toward_the_request_limit(self):
        attempt = self.lost_capture()
        self.ledger.settle_from_price_list("queue1", "Cloud run settled at list price")
        self.ledger.authorize_lost_capture_retry(attempt, "https://www.instagram.com/p/abc123/",
                                                 "Benny approved one re-capture of this save")
        self.ledger.create_run("queue2", "0.03", max_requests=1, min_gap_seconds=0)
        retry = self.ledger.reserve("queue2", "abc123", recovery_of=attempt,
                                    recovery_source_url="https://www.instagram.com/p/abc123/")
        self.ledger.complete(retry, 200, "request_premium")
        with self.assertRaisesRegex(UsageBlocked, "request_limit"):
            self.ledger.reserve("queue2", "def456")

    def test_lost_capture_retry_refuses_filed_or_unusable_captures(self):
        filed = self.lost_capture("abc123")
        unusable = self.ledger.reserve("queue1", "def456")
        self.ledger.complete(unusable, 200, "request_premium")
        self.ledger.settle_from_price_list("queue1", "Cloud run settled at list price")
        note = self.path.parent / "note.md"
        note.write_text("filed", encoding="utf-8")
        self.ledger.mark_filed(filed, note)
        direction = "Benny approved one re-capture of this save"
        for attempt, source in ((filed, "abc123"), (unusable, "def456")):
            with self.assertRaisesRegex(UsageBlocked, "requires_settled_usable_unfiled"):
                self.ledger.authorize_lost_capture_retry(
                    attempt, f"https://www.instagram.com/p/{source}/", direction)
        self.assertEqual(self.ledger.report()["lost_capture_retries"], [])

    def provider_error(self, source="abc123", status=502, clear_hold=True):
        """A settled String 5xx at the full reservation, with its hold cleared as a release would."""
        if not self.ledger.report()["runs"]:
            self.run_with(min_gap_seconds=0)
        attempt = self.ledger.reserve("queue1", source)
        self.ledger.complete(attempt, status, None, actual_usd="0.006", error="bad gateway")
        if clear_hold:
            with sqlite3.connect(self.path) as db:
                db.execute("UPDATE control SET hold_reason=NULL,hold_at=NULL WHERE singleton=1")
        return attempt

    def test_provider_error_retry_allows_one_rerequest_in_any_later_run(self):
        attempt = self.provider_error()
        url = "https://www.instagram.com/reel/abc123/"
        direction = "Benny asked to try this reel again"
        with self.assertRaisesRegex(ValueError, "user_direction"):
            self.ledger.authorize_provider_error_retry(attempt, url, "yes")
        with self.assertRaisesRegex(UsageBlocked, "requires_settled_5xx"):
            self.ledger.authorize_provider_error_retry(attempt, "https://www.instagram.com/reel/other1/", direction)
        report = self.ledger.authorize_provider_error_retry(attempt, url, direction)
        self.assertEqual(report["provider_error_retries"][0]["parent_attempt_id"], attempt)
        self.assertIn("user_directed_provider_error_retry_authorized", [e["code"] for e in report["events"]])
        self.assertEqual(report["attempt_records"][0]["error_code"], "provider_error")
        self.assertEqual(report["known_actual_micro"], 6000)  # the failed charge stays
        with self.assertRaisesRegex(UsageBlocked, "already_authorized"):
            self.ledger.authorize_provider_error_retry(attempt, url, direction)
        self.ledger.create_run("queue2", "0.03", min_gap_seconds=0)
        self.assertEqual(self.ledger.provider_error_retry_for("abc123"), attempt)
        with self.assertRaisesRegex(UsageBlocked, "source_already_attempted"):
            self.ledger.reserve("queue2", "abc123")
        retry = self.ledger.reserve("queue2", "abc123", recovery_of=attempt, recovery_source_url=url)
        self.ledger.complete(retry, 200, "request_premium")
        report = self.ledger.report("queue2")
        self.assertEqual(report["attempt_records"][0]["recovery_of"], attempt)
        self.assertIn("provider_error_retry_reserved", [e["code"] for e in report["events"]])
        self.assertIsNone(self.ledger.provider_error_retry_for("abc123"))
        with self.assertRaisesRegex(UsageBlocked, "recovery_not_authorized"):
            self.ledger.reserve("queue2", "abc123", recovery_of=attempt, recovery_source_url=url)
        with self.assertRaisesRegex(UsageBlocked, "already"):
            self.ledger.authorize_provider_error_retry(attempt, url, direction)

    def test_provider_error_retry_keeps_the_runs_limits_and_holds(self):
        attempt = self.provider_error()
        url = "https://www.instagram.com/reel/abc123/"
        self.ledger.authorize_provider_error_retry(attempt, url, "Benny asked to try this reel again")
        self.ledger.create_run("queue2", "0.03", max_requests=1, min_gap_seconds=600)
        with self.assertRaisesRegex(UsageBlocked, "minimum_gap"):
            self.ledger.reserve("queue2", "abc123", recovery_of=attempt, recovery_source_url=url)
        self.now[0] += 601
        retry = self.ledger.reserve("queue2", "abc123", recovery_of=attempt, recovery_source_url=url)
        self.ledger.complete(retry, 200, "request_premium")
        self.now[0] += 601
        with self.assertRaisesRegex(UsageBlocked, "request_limit"):
            self.ledger.reserve("queue2", "def456")

    def test_provider_error_retry_refuses_holds_unsettled_and_other_failures(self):
        direction = "Benny asked to try this reel again"
        held = self.provider_error(clear_hold=False)
        url = "https://www.instagram.com/reel/abc123/"
        with self.assertRaisesRegex(UsageBlocked, "global_hold"):
            self.ledger.authorize_provider_error_retry(held, url, direction)
        with sqlite3.connect(self.path) as db:
            db.execute("UPDATE control SET hold_reason=NULL,hold_at=NULL WHERE singleton=1")
        denied = self.ledger.reserve("queue1", "def456")
        self.ledger.complete(denied, 403, None, actual_usd="0.006")
        with sqlite3.connect(self.path) as db:
            db.execute("UPDATE control SET hold_reason=NULL,hold_at=NULL WHERE singleton=1")
        for attempt, source in ((denied, "def456"),):  # a 403 without provider_error is not a 5xx
            with self.assertRaisesRegex(UsageBlocked, "requires_settled_5xx"):
                self.ledger.authorize_provider_error_retry(
                    attempt, f"https://www.instagram.com/reel/{source}/", direction)
        ok = self.ledger.reserve("queue1", "ghi789")
        self.ledger.complete(ok, 200, "request_premium")
        self.ledger.mark_media(ok, True)
        with self.assertRaisesRegex(UsageBlocked, "requires_settled_5xx"):
            self.ledger.authorize_provider_error_retry(ok, "https://www.instagram.com/reel/ghi789/", direction)
        pending = self.ledger.reserve("queue1", "jkl012")
        with self.assertRaisesRegex(UsageBlocked, "pending_attempt_requires_reconciliation"):
            self.ledger.authorize_provider_error_retry(held, url, direction)
        self.ledger.complete(pending, 200, "request_premium")
        with sqlite3.connect(self.path) as db:
            db.execute("UPDATE control SET hold_reason=NULL,hold_at=NULL WHERE singleton=1")
        self.assertEqual(self.ledger.report()["provider_error_retries"], [])

    def test_provider_error_retry_refuses_when_another_retry_exists(self):
        attempt = self.provider_error()
        url = "https://www.instagram.com/reel/abc123/"
        with sqlite3.connect(self.path) as db:
            db.execute("INSERT INTO lost_capture_retries VALUES(?,?,?,?)",
                       (attempt, 1.0, url, "Benny asked for a different retry"))
        with self.assertRaisesRegex(UsageBlocked, "already_authorized_or_other_retry"):
            self.ledger.authorize_provider_error_retry(attempt, url, "Benny asked to try this reel again")
        # and the reverse: the lost-capture route does not accept a 5xx attempt
        with self.assertRaisesRegex(UsageBlocked, "requires_settled_usable_unfiled"):
            self.ledger.authorize_lost_capture_retry(attempt, url, "Benny asked to try this reel again")

    def empty_capture(self, source="abc123", clear_hold=True):
        """A settled HTTP 200 that carried no usable media, with any hold cleared as a release would."""
        if not self.ledger.report()["runs"]:
            self.run_with(min_gap_seconds=0)
        attempt = self.ledger.reserve("queue1", source)
        self.ledger.complete(attempt, 200, "request_premium", actual_usd="0.003")
        self.ledger.mark_media(attempt, False)
        if clear_hold:
            with sqlite3.connect(self.path) as db:
                db.execute("UPDATE control SET hold_reason=NULL,hold_at=NULL WHERE singleton=1")
        return attempt

    def test_empty_capture_retry_allows_one_rerequest_once_per_source(self):
        attempt = self.empty_capture()
        url = "https://www.instagram.com/p/abc123/"
        direction = "Benny said yes, retry this post once more"
        with self.assertRaisesRegex(ValueError, "user_direction"):
            self.ledger.authorize_empty_capture_retry(attempt, url, "yes")
        with self.assertRaisesRegex(UsageBlocked, "requires_settled_200"):
            self.ledger.authorize_empty_capture_retry(attempt, "https://www.instagram.com/p/other1/", direction)
        report = self.ledger.authorize_empty_capture_retry(attempt, url, direction)
        self.assertEqual(report["empty_capture_retries"][0]["parent_attempt_id"], attempt)
        self.assertIn("user_directed_empty_capture_retry_authorized", [e["code"] for e in report["events"]])
        with self.assertRaisesRegex(UsageBlocked, "already_authorized"):
            self.ledger.authorize_empty_capture_retry(attempt, url, direction)
        self.ledger.create_run("queue2", "0.03", max_requests=1, min_gap_seconds=0)
        self.assertEqual(self.ledger.empty_capture_retry_for("abc123"), attempt)
        with self.assertRaisesRegex(UsageBlocked, "source_already_attempted"):
            self.ledger.reserve("queue2", "abc123")
        retry = self.ledger.reserve("queue2", "abc123", recovery_of=attempt, recovery_source_url=url)
        self.ledger.complete(retry, 200, "request_premium")
        report = self.ledger.report("queue2")
        self.assertEqual(report["attempt_records"][0]["recovery_of"], attempt)
        self.assertIn("empty_capture_retry_reserved", [e["code"] for e in report["events"]])
        self.assertIsNone(self.ledger.empty_capture_retry_for("abc123"))
        with self.assertRaisesRegex(UsageBlocked, "recovery_not_authorized"):
            self.ledger.reserve("queue2", "abc123", recovery_of=attempt, recovery_source_url=url)
        # a second use is refused, whatever happened to the retry
        with self.assertRaisesRegex(UsageBlocked, "already"):
            self.ledger.authorize_empty_capture_retry(attempt, url, direction)
        with self.assertRaisesRegex(UsageBlocked, "requires_settled_200"):
            self.ledger.authorize_empty_capture_retry(retry, url, direction)

    def test_empty_capture_retry_refuses_holds_unsettled_and_other_outcomes(self):
        direction = "Benny said yes, retry this post once more"
        url = "https://www.instagram.com/p/abc123/"
        held = self.empty_capture(clear_hold=False)
        with sqlite3.connect(self.path) as db:
            db.execute("UPDATE control SET hold_reason='media_unusable',hold_at=1 WHERE singleton=1")
        with self.assertRaisesRegex(UsageBlocked, "global_hold"):
            self.ledger.authorize_empty_capture_retry(held, url, direction)
        with sqlite3.connect(self.path) as db:
            db.execute("UPDATE control SET hold_reason=NULL,hold_at=NULL WHERE singleton=1")
        pending = self.ledger.reserve("queue1", "jkl012")
        with self.assertRaisesRegex(UsageBlocked, "pending_attempt_requires_reconciliation"):
            self.ledger.authorize_empty_capture_retry(held, url, direction)
        self.ledger.complete(pending, 200, "request_premium")
        self.ledger.mark_media(pending, True)
        with sqlite3.connect(self.path) as db:
            db.execute("UPDATE control SET hold_reason=NULL,hold_at=NULL WHERE singleton=1")
        with self.assertRaisesRegex(UsageBlocked, "requires_settled_200"):  # usable media
            self.ledger.authorize_empty_capture_retry(pending, "https://www.instagram.com/p/jkl012/", direction)
        failed = self.ledger.reserve("queue1", "def456")
        self.ledger.complete(failed, 502, None, actual_usd="0.006", error="bad gateway")
        with sqlite3.connect(self.path) as db:
            db.execute("UPDATE control SET hold_reason=NULL,hold_at=NULL WHERE singleton=1")
        with self.assertRaisesRegex(UsageBlocked, "requires_settled_200"):  # a 5xx, not an empty 200
            self.ledger.authorize_empty_capture_retry(failed, "https://www.instagram.com/p/def456/", direction)
        self.assertEqual(self.ledger.report()["empty_capture_retries"], [])

    def test_empty_capture_retry_refuses_filed_multi_attempt_and_other_retry(self):
        direction = "Benny said yes, retry this post once more"
        attempt = self.empty_capture()
        url = "https://www.instagram.com/p/abc123/"
        with sqlite3.connect(self.path) as db:
            db.execute("UPDATE attempts SET note_path='/tmp/note.md' WHERE attempt_id=?", (attempt,))
        with self.assertRaisesRegex(UsageBlocked, "requires_settled_200"):
            self.ledger.authorize_empty_capture_retry(attempt, url, direction)
        with sqlite3.connect(self.path) as db:
            db.execute("UPDATE attempts SET note_path=NULL WHERE attempt_id=?", (attempt,))
            db.execute("INSERT INTO provider_error_retries VALUES(?,?,?,?)",
                       (attempt, 1.0, url, "Benny asked for a different retry"))
        with self.assertRaisesRegex(UsageBlocked, "already_authorized_or_other_retry"):
            self.ledger.authorize_empty_capture_retry(attempt, url, direction)
        with sqlite3.connect(self.path) as db:
            db.execute("DELETE FROM provider_error_retries")
        # a source with two attempts is refused
        self.ledger.create_run("queue2", "0.03", min_gap_seconds=0)
        self.ledger.authorize_empty_capture_retry(attempt, url, direction)
        retry = self.ledger.reserve("queue2", "abc123", recovery_of=attempt, recovery_source_url=url)
        self.ledger.complete(retry, 200, "request_premium")
        with self.assertRaisesRegex(UsageBlocked, "already"):
            self.ledger.authorize_empty_capture_retry(attempt, url, direction)

    def test_unauthenticated_release_refuses_every_other_failure(self):
        self.run_with(min_gap_seconds=0)
        self.ledger.complete(self.ledger.reserve("queue1", "abc123"), 403, None,
                             error="provider_http_error")
        self.ledger.record_access_check(200)
        with self.assertRaisesRegex(UsageBlocked, "hold_not_from_unauthenticated_attempt"):
            self.ledger.resume_reviewed_unauthenticated("queue1", "Reviewed a forbidden answer")
        self.assertEqual(self.ledger.report()["hold_reason"], "provider_denial")

    def test_unauthenticated_release_refuses_a_billed_401_or_a_second_hold_kind(self):
        self.run_with(min_gap_seconds=0)
        self.ledger.complete(self.ledger.reserve("queue1", "abc123"), 401, "request_premium",
                             error="provider_http_error")
        self.ledger.record_access_check(200)
        with self.assertRaisesRegex(UsageBlocked, "hold_not_from_unauthenticated_attempt"):
            self.ledger.resume_reviewed_unauthenticated("queue1", "Reviewed a billed refusal")
        other = Ledger(self.path.parent / "other.sqlite3")
        other.create_run("queue1", "0.03", min_gap_seconds=0)
        other.complete(other.reserve("queue1", "abc123"), 401, None, error="provider_http_error")
        other.hold("shared_usage_log_failed")
        other.record_access_check(200)
        with self.assertRaisesRegex(UsageBlocked, "other_safety_hold_requires_review"):
            other.resume_reviewed_unauthenticated("queue1", "Reviewed refusal and a log failure")

    NOTE = "String timed out; marked fail, never refetched"

    def failed_fetch(self, run, source, status=0, billed=None, error="provider_deadline_exceeded"):
        attempt = self.ledger.reserve(run, source)
        self.ledger.complete(attempt, status, billed, error=error)
        return attempt

    def good_fetch(self, run, source):
        attempt = self.ledger.reserve(run, source)
        self.ledger.complete(attempt, 200, "request_premium")
        self.ledger.mark_media(attempt)
        return attempt

    def test_unattended_release_carries_a_run_past_a_timeout(self):
        self.run_with(budget="0.1", min_gap_seconds=0, burn_limit_usd="1")
        failed = self.failed_fetch("queue1", "abc123")
        report = self.ledger.report()
        self.assertEqual((report["hold_reason"], report["hold_run_id"]), ("provider_denial", "queue1"))
        with self.assertRaisesRegex(UsageBlocked, "passing_access_check_required"):
            self.ledger.resume_unattended("queue1", self.NOTE)
        self.ledger.record_access_check(503, run_id="queue1")
        with self.assertRaisesRegex(UsageBlocked, "passing_access_check_required"):
            self.ledger.resume_unattended("queue1", self.NOTE)
        self.ledger.record_access_check(200, run_id="queue1")
        report = self.ledger.resume_unattended("queue1", self.NOTE)
        self.assertIsNone(report["hold_reason"])
        record = report["attempt_records"][0]
        self.assertEqual((record["attempt_id"], record["actual_micro"], record["actual_source"]),
                         (failed, 6000, "unattended_max"))
        self.assertEqual(report["reviews"][-1]["code"], "unattended_resume")
        with self.assertRaisesRegex(UsageBlocked, "source_already_attempted"):
            self.ledger.reserve("queue1", "abc123")
        self.good_fetch("queue1", "def456")
        report = self.ledger.settle_from_price_list("queue1", "Cloud run settled at list price")
        self.assertEqual(report["known_actual_micro"], 9000)
        self.ledger.create_run("queue2", "0.03", min_gap_seconds=0)

    def test_unattended_release_settles_a_crash_mid_fetch(self):
        self.run_with(budget="0.1", min_gap_seconds=0)
        self.ledger.reserve("queue1", "abc123")
        self.ledger.hold("provider_unexpected_failure")
        self.assertEqual(self.ledger.report()["hold_run_id"], "queue1")
        self.ledger.record_access_check(200)
        report = self.ledger.resume_unattended("queue1", "Fetch worker crashed; marked fail")
        record = report["attempt_records"][0]
        self.assertFalse(record["pending"])
        self.assertEqual((record["actual_micro"], record["actual_source"]), (6000, "unattended_max"))
        self.ledger.create_run("queue2", "0.03", min_gap_seconds=0)

    def test_unattended_release_covers_billed_failures_and_dead_posts(self):
        self.run_with(budget="0.1", min_gap_seconds=0, burn_limit_usd="1")
        self.failed_fetch("queue1", "abc123", status=404, billed="request_premium",
                          error="origin_access_error")
        self.ledger.record_access_check(200)
        self.ledger.resume_unattended("queue1", "Instagram answered 404; marked fail")
        self.good_fetch("queue1", "def456")
        dead = self.ledger.reserve("queue1", "ghi789")
        self.ledger.complete(dead, 200, "request_premium")
        self.ledger.hold("media_capture_or_validation_failed")
        self.assertEqual(self.ledger.report()["hold_run_id"], "queue1")
        self.ledger.record_access_check(200)
        report = self.ledger.resume_unattended("queue1", "Post deleted; marked fail, never refetched")
        self.assertIsNone(report["hold_reason"])
        by_source = {r["source_id"]: r for r in report["attempt_records"]}
        self.assertEqual(by_source["abc123"]["actual_micro"], 6000)
        self.assertIsNone(by_source["ghi789"]["actual_micro"])
        report = self.ledger.settle_from_price_list("queue1", "Cloud run settled at list price")
        self.assertEqual(report["known_actual_micro"], 12000)

    def test_unattended_release_stops_at_the_third_failure_in_a_row(self):
        self.run_with(budget="0.2", min_gap_seconds=0, burn_limit_usd="1")
        for n, source in enumerate(("aaa111", "bbb222")):
            self.failed_fetch("queue1", source)
            self.ledger.record_access_check(200)
            self.ledger.resume_unattended("queue1", self.NOTE)
        self.good_fetch("queue1", "ccc333")
        for source in ("ddd444", "eee555"):
            self.failed_fetch("queue1", source, status=503, error="provider_http_error")
            self.ledger.record_access_check(200)
            self.ledger.resume_unattended("queue1", "String was busy; marked fail")
        self.failed_fetch("queue1", "fff666")
        self.ledger.record_access_check(200)
        with self.assertRaisesRegex(UsageBlocked, "failures_in_a_row_limit_reached"):
            self.ledger.resume_unattended("queue1", self.NOTE)
        self.assertEqual(self.ledger.report()["hold_reason"], "provider_denial")
        # The next night restarts once, after a fresh key check.
        self.now[0] += 6 * 3600
        with self.assertRaisesRegex(UsageBlocked, "passing_access_check_required"):
            self.ledger.resume_unattended("queue1", "Next night restart after three timeouts")
        self.ledger.record_access_check(200)
        report = self.ledger.resume_unattended("queue1", "Next night restart after three timeouts")
        self.assertEqual(report["reviews"][-1]["code"], "unattended_restart")
        self.ledger.settle_from_price_list("queue1", "Cloud run settled at list price")
        # The restart's first request is a probe: one more failure stops the night again.
        self.ledger.create_run("queue2", "0.1", min_gap_seconds=0, burn_limit_usd="1")
        self.ledger.record_access_check(200, run_id="queue2")
        self.failed_fetch("queue2", "ggg777")
        self.ledger.record_access_check(200)
        with self.assertRaisesRegex(UsageBlocked, "failures_in_a_row_limit_reached"):
            self.ledger.resume_unattended("queue2", self.NOTE)
        self.now[0] += 6 * 3600
        self.ledger.record_access_check(200)
        self.ledger.resume_unattended("queue2", "Next night restart after the probe failed")
        self.good_fetch("queue2", "hhh888")
        self.failed_fetch("queue2", "iii999")
        self.ledger.record_access_check(200)
        self.ledger.resume_unattended("queue2", self.NOTE)

    def test_unattended_release_is_capped_per_run(self):
        self.run_with(budget="0.3", max_requests=30, min_gap_seconds=0, burn_limit_usd="1")
        for n in range(9):
            self.good_fetch("queue1", f"good{n}")
            self.failed_fetch("queue1", f"fail{n}")
            self.ledger.record_access_check(200)
            if n < 8:
                self.ledger.resume_unattended("queue1", self.NOTE)
        with self.assertRaisesRegex(UsageBlocked, "run_unattended_resume_limit_reached"):
            self.ledger.resume_unattended("queue1", self.NOTE)

    def retry_fetch(self, run, source, status=0, billed=None, error="provider_deadline_exceeded",
                    usable=False):
        parent = self.ledger.automatic_retry_for(source)
        self.assertIsNotNone(parent)
        attempt = self.ledger.reserve(run, source, recovery_of=parent,
                                      recovery_source_url=f"https://www.instagram.com/reel/{source}/")
        self.ledger.complete(attempt, status, billed, error=error)
        if usable:
            self.ledger.mark_media(attempt)
        return attempt

    def release(self, run, note="Failed fetch released; retrying it later"):
        self.ledger.record_access_check(200)
        return self.ledger.resume_unattended(run, note)

    def test_automatic_retry_follows_a_failed_source_three_times_then_never(self):
        """Benny, 2026-11-10: "by default you can retry each a few times"."""
        self.run_with(budget="0.2", max_requests=10, min_gap_seconds=0, burn_limit_usd="1")
        first = self.failed_fetch("queue1", "abc123", status=502, error="provider_http_error")
        with self.assertRaisesRegex(UsageBlocked, "global_hold"):  # nothing goes out until released
            self.ledger.reserve("queue1", "abc123", recovery_of=first,
                                recovery_source_url="https://www.instagram.com/reel/abc123/")
        self.release("queue1")
        self.assertEqual(self.ledger.automatic_retry_for("abc123"), first)
        with self.assertRaisesRegex(UsageBlocked, "recovery_not_authorized"):
            self.ledger.reserve("queue1", "abc123", recovery_of=first,
                                recovery_source_url="https://www.instagram.com/reel/other99/")
        second = self.retry_fetch("queue1", "abc123", status=200, billed="request_premium", error=None)
        self.ledger.hold("media_capture_or_validation_failed")
        self.release("queue1")
        third = self.retry_fetch("queue1", "abc123", status=403, billed="request_premium", error=None)
        self.release("queue1")
        # A later run can take the last retry; the chain links each try to the one before.
        self.ledger.settle_from_price_list("queue1", "Cloud run settled at list price")
        self.ledger.create_run("queue2", "0.1", min_gap_seconds=0, burn_limit_usd="1")
        fourth = self.retry_fetch("queue2", "abc123", status=200, billed="request_premium", error=None)
        self.ledger.hold("media_capture_or_validation_failed")
        self.assertEqual(self.release("queue2")["automatic_retries"][0]["retry_number"], 3)
        report = self.ledger.report()
        self.assertEqual([r["recovery_of"] for r in report["attempt_records"]], [None, first, second, third])
        self.assertEqual([(r["parent_attempt_id"], r["child_attempt_id"], r["retry_number"])
                          for r in report["automatic_retries"]],
                         [(first, second, 1), (second, third, 2), (third, fourth, 3)])
        self.assertTrue(all("a few times" in r["rule"] for r in report["automatic_retries"]))
        self.assertIn("automatic_retry_reserved", [e["code"] for e in report["events"]])
        self.assertIsNone(self.ledger.automatic_retry_for("abc123"))
        with self.assertRaisesRegex(UsageBlocked, "source_already_attempted"):
            self.ledger.reserve("queue2", "abc123")
        with self.assertRaisesRegex(UsageBlocked, "recovery_not_authorized"):
            self.ledger.reserve("queue2", "abc123", recovery_of=fourth,
                                recovery_source_url="https://www.instagram.com/reel/abc123/")

    def test_automatic_retry_never_follows_a_usable_post_or_a_billing_problem(self):
        self.run_with(budget="0.2", max_requests=10, min_gap_seconds=0, burn_limit_usd="1")
        self.good_fetch("queue1", "good11")
        self.assertIsNone(self.ledger.automatic_retry_for("good11"))  # never paid for twice
        oddity = self.ledger.reserve("queue1", "odd222")
        self.ledger.complete(oddity, 200, None)  # a 2xx with no billing class
        with sqlite3.connect(self.path) as db:
            db.execute("UPDATE control SET hold_reason=NULL,hold_at=NULL WHERE singleton=1")
        self.assertIsNone(self.ledger.automatic_retry_for("odd222"))
        jump = self.ledger.reserve("queue1", "jmp333", expected_billing_class="request_standard")
        self.ledger.complete(jump, 403, "request_premium")
        with sqlite3.connect(self.path) as db:
            db.execute("UPDATE control SET hold_reason=NULL,hold_at=NULL WHERE singleton=1")
        self.assertIsNone(self.ledger.automatic_retry_for("jmp333"))
        self.assertIsNone(self.ledger.automatic_retry_for("never44"))

    def test_automatic_retry_keeps_the_runs_limits_and_yields_to_an_allowance(self):
        self.run_with(budget="0.1", max_requests=2, min_gap_seconds=0, burn_limit_usd="1")
        failed = self.failed_fetch("queue1", "abc123", status=502, error="provider_http_error")
        self.release("queue1")
        self.good_fetch("queue1", "def456")
        url = "https://www.instagram.com/reel/abc123/"
        with self.assertRaisesRegex(UsageBlocked, "request_limit"):  # a retry is a planned request
            self.ledger.reserve("queue1", "abc123", recovery_of=failed, recovery_source_url=url)
        self.ledger.settle_from_price_list("queue1", "Cloud run settled at list price")
        self.ledger.create_run("queue2", "0.03", min_gap_seconds=0, burn_limit_usd="1")
        self.ledger.authorize_provider_error_retry(failed, url, "Benny asked to try this reel again")
        self.assertIsNone(self.ledger.automatic_retry_for("abc123"))
        retry = self.ledger.reserve("queue2", "abc123", recovery_of=failed, recovery_source_url=url)
        self.ledger.complete(retry, 502, None, error="bad gateway")
        self.release("queue2")
        # After the user-directed try, the standing rule still allows the rest of the three.
        self.assertEqual(self.ledger.automatic_retry_for("abc123"), retry)
        report = self.ledger.report("queue2")
        self.assertIn("provider_error_retry_reserved", [e["code"] for e in report["events"]])

    def test_retry_releases_spare_the_eight_and_post_failures_are_not_in_a_row(self):
        self.run_with(budget="0.5", max_requests=40, min_gap_seconds=0, burn_limit_usd="1")
        for n in range(8):
            self.good_fetch("queue1", f"good{n}")
            self.failed_fetch("queue1", f"fail{n}", status=200, billed="request_premium", error=None)
            self.ledger.hold("media_capture_or_validation_failed")
            self.release("queue1")
        # All eight releases are used, yet each failed post still gets its retries, back to
        # back: Instagram answering without the post says nothing about String.
        for _ in range(3):
            self.retry_fetch("queue1", "fail7", status=200, billed="request_premium", error=None)
            self.ledger.hold("media_capture_or_validation_failed")
            report = self.release("queue1")
            self.assertEqual(report["reviews"][-1]["code"], "unattended_retry_resume")
        # A new failed post still meets the cap of eight.
        self.failed_fetch("queue1", "fail8", status=200, billed="request_premium", error=None)
        self.ledger.hold("media_capture_or_validation_failed")
        with self.assertRaisesRegex(UsageBlocked, "run_unattended_resume_limit_reached"):
            self.release("queue1")

    def test_string_failing_a_retry_still_counts_in_a_row(self):
        self.run_with(budget="0.2", max_requests=10, min_gap_seconds=0, burn_limit_usd="1")
        self.failed_fetch("queue1", "abc123", status=502, error="provider_http_error")
        self.release("queue1")
        self.retry_fetch("queue1", "abc123", status=503, error="provider_http_error")
        self.release("queue1")
        self.retry_fetch("queue1", "abc123", status=0)
        with self.assertRaisesRegex(UsageBlocked, "failures_in_a_row_limit_reached"):
            self.release("queue1")

    def test_unattended_release_waits_out_the_burn_window(self):
        self.run_with(budget="1", min_gap_seconds=0, burn_limit_usd="0.006")
        self.good_fetch("queue1", "abc123")
        with self.assertRaisesRegex(UsageBlocked, "burn_limit"):
            self.ledger.reserve("queue1", "def456")
        self.assertEqual(self.ledger.report()["hold_run_id"], "queue1")
        self.ledger.record_access_check(200)
        with self.assertRaises(UsageBlocked) as early:
            self.ledger.resume_unattended("queue1", "Fast-spending window passed")
        self.assertEqual(early.exception.reason, "burn_window_still_open")
        self.assertEqual(early.exception.wait_seconds, 60)
        self.now[0] += 61
        self.ledger.resume_unattended("queue1", "Fast-spending window passed")
        self.assertIsInstance(self.ledger.reserve("queue1", "def456"), int)

    def test_unattended_release_waits_a_night_for_a_string_refusal(self):
        self.run_with(budget="0.1", min_gap_seconds=0)
        self.failed_fetch("queue1", "abc123", status=403, error="provider_http_error")
        self.ledger.record_access_check(200)
        with self.assertRaisesRegex(UsageBlocked, "string_refused_the_request_restart_next_night"):
            self.ledger.resume_unattended("queue1", "String refused the fetch")
        self.now[0] += 6 * 3600 - 1
        self.ledger.record_access_check(200)
        with self.assertRaisesRegex(UsageBlocked, "string_refused_the_request_restart_next_night"):
            self.ledger.resume_unattended("queue1", "String refused the fetch")
        self.now[0] += 1
        report = self.ledger.resume_unattended("queue1", "Restarting the night after a 403")
        self.assertEqual(report["reviews"][-1]["code"], "unattended_restart")
        self.assertEqual(report["attempt_records"][0]["actual_source"], "unattended_max")
        self.ledger.create_run("queue2", "0.03", min_gap_seconds=0)

    def test_unattended_release_never_clears_a_billing_anomaly_or_a_refused_key(self):
        cases = {
            "billing_anomaly_requires_review": lambda l: l.complete(l.reserve("queue1", "abc123"), 200, None),
            "refused_key_use_resume_reviewed_unauthenticated":
                lambda l: l.complete(l.reserve("queue1", "abc123"), 401, None, error="provider_http_error"),
            "hold_not_eligible_for_unattended_resume:rate_jump": lambda l: (
                l.complete(l.reserve("queue1", "abc123"), 200, "request_standard"),
                l.complete(l.reserve("queue1", "def456"), 200, "request_premium")),
            "hold_not_eligible_for_unattended_resume:unexpected_billing_class": lambda l: l.complete(
                l.reserve("queue1", "abc123", expected_billing_class="request_standard"), 200, "request_premium"),
            "other_safety_hold_requires_review": lambda l: (
                l.complete(l.reserve("queue1", "abc123"), 0, None, error="provider_deadline_exceeded"),
                l.hold("shared_usage_log_failed")),
            "hold_not_eligible_for_unattended_resume:access_check_billed":
                lambda l: l.record_access_check(200, "request_standard"),
        }
        for n, (reason, latch) in enumerate(cases.items()):
            with self.subTest(reason=reason):
                ledger = Ledger(self.path.parent / f"anomaly{n}.sqlite3", clock=lambda: self.now[0])
                ledger.create_run("queue1", "0.1", min_gap_seconds=0)
                latch(ledger)
                hold = ledger.report()["hold_reason"]
                ledger.record_access_check(200)
                for later in (0, 7 * 24 * 3600):
                    self.now[0] += later
                    with self.assertRaises(UsageBlocked) as refused:
                        ledger.resume_unattended("queue1", "Tried to release a billing anomaly")
                    self.assertEqual(refused.exception.reason, reason)
                    self.assertEqual(ledger.report()["hold_reason"], hold)

    def test_unattended_release_names_the_run_that_held(self):
        self.ledger.create_run("queue0", "0.1", min_gap_seconds=0)
        self.run_with(budget="0.1", min_gap_seconds=0)
        self.failed_fetch("queue1", "abc123")
        self.ledger.record_access_check(200)
        with self.assertRaisesRegex(UsageBlocked, "hold_from_another_run:queue1"):
            self.ledger.resume_unattended("queue0", self.NOTE)
        with self.assertRaisesRegex(UsageBlocked, "unknown_run"):
            self.ledger.resume_unattended("queue9", self.NOTE)
        with self.assertRaises(ValueError):
            self.ledger.resume_unattended("queue1", "short")
        self.assertIsNone(self.ledger.report("queue0")["attempt_records"] or None)
        self.ledger.resume_unattended("queue1", self.NOTE)
        self.assertIsNone(self.ledger.report()["hold_run_id"])

    def test_access_checks_are_free_reads_and_a_charge_latches_a_hold(self):
        self.run_with()
        self.assertFalse(self.ledger.access_checked("queue1"))
        self.assertFalse(self.ledger.record_access_check(401, run_id="queue1")["access_ok"])
        self.assertFalse(self.ledger.access_checked("queue1"))
        self.assertIsNone(self.ledger.report()["hold_reason"])
        self.assertTrue(self.ledger.record_access_check(200, run_id="queue1")["access_ok"])
        self.assertTrue(self.ledger.access_checked("queue1"))
        self.assertFalse(self.ledger.record_access_check(200, "request_standard")["access_ok"])
        self.assertEqual(self.ledger.report()["hold_reason"], "access_check_billed")
        self.assertFalse(self.ledger.access_checked("queue1"))

    def portal_evidence(self, sources, *, total=None):
        rows = [{"source_id": source, "url": f"https://www.instagram.com/reel/{source}/",
                 "time_local": datetime.fromtimestamp(self.now[0], timezone.utc).isoformat(),
                 "status_code": 200, "result": "OK", "portal_type": "Fetch",
                 "billing_class": None, "actual_usd": "0.003"} for source in sources]
        evidence = {"evidence_source": "https://portal.usestring.ai/web-access",
                    "run_id": "queue1", "total_actual_usd": total or str(.003 * len(rows)),
                    "rows": rows}
        path = self.path.parent / "portal.json"
        path.write_text(json.dumps(evidence))
        return path

    def soundtrack_parent(self):
        self.run_with(min_gap_seconds=0)
        parent = self.ledger.reserve("queue1", "abc123")
        self.ledger.complete(parent, 200, "request_premium", usable_media=True)
        path = self.portal_evidence(["abc123"], total="0.003")
        data = json.loads(path.read_text())
        data["rows"][0]["url"] = "https://www.instagram.com/p/abc123/"
        path.write_text(json.dumps(data))
        self.ledger.reconcile_portal("queue1", path, "Reviewed successful photo capture")
        self.ledger.create_run("music", "0.006", max_requests=1, min_gap_seconds=0)
        return parent

    def test_soundtrack_recheck_is_exact_once_and_preserves_charges(self):
        parent = self.soundtrack_parent()
        url = "https://www.instagram.com/p/abc123/"
        self.ledger.authorize_soundtrack_recheck("music", parent, url, "a" * 64,
                                                "User asks to resolve this soundtrack or try again")
        with self.assertRaisesRegex(UsageBlocked, "exact_source"):
            self.ledger.reserve("music", "other")
        with self.assertRaisesRegex(UsageBlocked, "recovery_not_authorized"):
            self.ledger.reserve("music", "abc123", recovery_of=parent,
                                recovery_source_url="https://www.instagram.com/reel/abc123/")
        attempt = self.ledger.reserve("music", "abc123", recovery_of=parent, recovery_source_url=url)
        self.ledger.complete(attempt, 200, "request_premium", actual_usd="0.003")
        report = self.ledger.report()
        self.assertEqual(report["known_actual_micro"], 6000)
        self.assertEqual(report["attempt_records"][1]["recovery_of"], parent)
        with self.assertRaisesRegex(UsageBlocked, "already_used"):
            self.ledger.reserve("music", "abc123", recovery_of=parent, recovery_source_url=url)
        self.ledger.create_run("again", ".05", max_requests=1)
        with self.assertRaisesRegex(UsageBlocked, "already_authorized_or_used"):
            self.ledger.authorize_soundtrack_recheck("again", parent, url, "a" * 64,
                                                    "A second repeat must be rejected")

    def test_soundtrack_recheck_cannot_release_hold_or_increase_budget(self):
        parent = self.soundtrack_parent()
        self.ledger.authorize_soundtrack_recheck("music", parent, "https://www.instagram.com/p/abc123/",
                                                "a" * 64, "Explicit user request to retry soundtrack")
        with self.assertRaisesRegex(ValueError, "immutable"):
            self.ledger.create_run("music", "1", max_requests=1)
        self.ledger.hold("provider_denial")
        with self.assertRaisesRegex(UsageBlocked, "global_hold"):
            self.ledger.reserve("music", "abc123", recovery_of=parent,
                                recovery_source_url="https://www.instagram.com/p/abc123/")

    def failed_media_parent(self, reviewed=True):
        self.run_with(min_gap_seconds=0)
        parent = self.ledger.reserve("queue1", "abc123")
        self.ledger.complete(parent, 200, "request_premium")
        if reviewed:
            self.ledger.hold("media_capture_or_validation_failed")
            self.ledger.resume_reviewed_media_failure("queue1", "abc123", "Reviewed: page fetched, no media")
        path = self.portal_evidence(["abc123"], total="0.003")
        data = json.loads(path.read_text())
        data["rows"][0]["url"] = "https://www.instagram.com/p/abc123/"
        path.write_text(json.dumps(data))
        self.ledger.reconcile_portal("queue1", path, "Reviewed failed photo capture")
        self.ledger.create_run("retry", "0.006", max_requests=1, min_gap_seconds=0)
        return parent

    def test_capture_retry_is_exact_once_and_preserves_charges(self):
        parent = self.failed_media_parent()
        url = "https://www.instagram.com/p/abc123/"
        self.ledger.authorize_capture_retry("retry", parent, url, "User asks to retry this failed carousel once")
        with self.assertRaisesRegex(UsageBlocked, "exact_source"):
            self.ledger.reserve("retry", "other")
        with self.assertRaisesRegex(UsageBlocked, "recovery_not_authorized"):
            self.ledger.reserve("retry", "abc123", recovery_of=parent,
                                recovery_source_url="https://www.instagram.com/reel/abc123/")
        attempt = self.ledger.reserve("retry", "abc123", recovery_of=parent, recovery_source_url=url)
        self.ledger.complete(attempt, 200, "request_premium", actual_usd="0.003")
        report = self.ledger.report()
        self.assertEqual(report["known_actual_micro"], 6000)
        self.assertEqual(report["attempt_records"][1]["recovery_of"], parent)
        self.assertEqual(report["capture_retries"][0]["parent_attempt_id"], parent)
        with self.assertRaisesRegex(UsageBlocked, "already_used"):
            self.ledger.reserve("retry", "abc123", recovery_of=parent, recovery_source_url=url)
        self.ledger.create_run("again", ".05", max_requests=1)
        with self.assertRaisesRegex(UsageBlocked, "already_authorized_or_used"):
            self.ledger.authorize_capture_retry("again", parent, url, "A second retry must be rejected")

    def test_capture_retry_needs_reviewed_failure_and_keeps_holds(self):
        parent = self.soundtrack_parent()
        url = "https://www.instagram.com/p/abc123/"
        with self.assertRaisesRegex(UsageBlocked, "requires_reviewed_failed_media"):
            self.ledger.authorize_capture_retry("music", parent, url, "User asks to retry a capture that worked")
        self.setUp()
        parent = self.failed_media_parent(reviewed=False)
        with self.assertRaisesRegex(UsageBlocked, "requires_reviewed_failed_media"):
            self.ledger.authorize_capture_retry("retry", parent, url, "User asks to retry an unreviewed failure")
        self.setUp()
        parent = self.failed_media_parent()
        self.ledger.authorize_capture_retry("retry", parent, url, "User asks to retry this failed carousel once")
        self.ledger.hold("provider_denial")
        with self.assertRaisesRegex(UsageBlocked, "global_hold"):
            self.ledger.reserve("retry", "abc123", recovery_of=parent, recovery_source_url=url)

    def test_portal_reconciliation_preserves_original_class_and_pending_history(self):
        self.run_with(min_gap_seconds=0)
        first = self.ledger.reserve("queue1", "abc123")
        self.ledger.complete(first, 200, "request_premium")
        second = self.ledger.reserve("queue1", "def456")
        self.ledger.hold("provider_wall_time_exceeded")
        path = self.portal_evidence(["abc123", "def456"], total="0.006")
        with self.assertRaises(UsageBlocked):
            self.ledger.resume_reviewed_timeout("queue1", "Reviewed exact portal rows")
        report = self.ledger.reconcile_portal("queue1", path, "Reviewed exact portal rows")
        self.assertEqual(report["known_actual_micro"], 6000)
        self.assertEqual(report["estimated_micro"], 3000)
        self.assertEqual(report["cost_envelope_micro"], 6000)
        self.assertEqual(report["unknown_actual_count"], 0)
        self.assertEqual(report["pending_count"], 0)
        self.assertEqual(report["attempt_records"][0]["billing_class"], "request_premium")
        self.assertIsNone(report["attempt_records"][1]["billing_class"])
        self.assertEqual(report["reconciliations"][1]["prior_pending"], 1)
        self.assertIsNone(report["reconciliations"][1]["prior_status"])
        self.assertEqual(report["global_hold"], "provider_wall_time_exceeded")
        with self.assertRaisesRegex(UsageBlocked, "global_hold"):
            self.ledger.reserve("queue1", "ghi789")
        resumed = self.ledger.resume_reviewed_timeout("queue1", "Reviewed portal charge and lost local response")
        self.assertIsNone(resumed["global_hold"])
        self.assertEqual(resumed["reviews"][0]["code"], "reviewed_timeout_resume")
        with self.assertRaisesRegex(UsageBlocked, "source_already_attempted"):
            self.ledger.reserve("queue1", "def456")
        self.assertIsInstance(self.ledger.reserve("queue1", "ghi789"), int)
        self.assertEqual(second, 2)

    def test_portal_reconciliation_rejects_incomplete_or_mismatched_evidence_atomically(self):
        self.run_with(min_gap_seconds=0)
        first = self.ledger.reserve("queue1", "abc123")
        self.ledger.complete(first, 200, "request_premium")
        self.ledger.reserve("queue1", "def456")
        path = self.portal_evidence(["abc123"], total="0.003")
        with self.assertRaisesRegex(ValueError, "every run attempt"):
            self.ledger.reconcile_portal("queue1", path, "Reviewed portal rows")
        evidence = json.loads(path.read_text())
        evidence["rows"].append(dict(evidence["rows"][0], source_id="def456", url="https://www.instagram.com/reel/wrong/"))
        path.write_text(json.dumps(evidence))
        with self.assertRaisesRegex(ValueError, "URL/ID mismatch"):
            self.ledger.reconcile_portal("queue1", path, "Reviewed portal rows")
        evidence["rows"][1]["url"] = "https://www.instagram.com/reel/def456/"
        evidence["rows"][1]["actual_usd"] = "0.009"
        evidence["total_actual_usd"] = "0.012"
        path.write_text(json.dumps(evidence))
        with self.assertRaisesRegex(ValueError, "exceeds reservation"):
            self.ledger.reconcile_portal("queue1", path, "Reviewed portal rows")
        self.assertEqual(self.ledger.report("queue1")["reconciliations"], [])
        self.assertEqual(self.ledger.report("queue1")["pending_count"], 1)

    def test_denial_or_anomaly_hold_cannot_be_resumed_by_portal_review(self):
        for hold_reason in ("provider_denial", "unknown_billing_class", "cost_above_reservation"):
            with self.subTest(hold_reason=hold_reason):
                temp = tempfile.TemporaryDirectory()
                self.addCleanup(temp.cleanup)
                ledger = Ledger(Path(temp.name) / "ledger.sqlite", clock=lambda: self.now[0])
                ledger.create_run("queue1", ".03")
                ledger.reserve("queue1", "abc123")
                ledger.hold(hold_reason)
                ledger.reconcile_portal("queue1", self.portal_evidence(["abc123"], total="0.003"),
                                        "Reviewed exact portal row")
                with self.assertRaisesRegex(UsageBlocked, "hold_not_eligible"):
                    ledger.resume_reviewed_timeout("queue1", "Reviewed exact portal row")
                self.assertEqual(ledger.report()["global_hold"], hold_reason)

    def test_reviewed_media_failure_resume_is_capped_per_run(self):
        self.run_with(min_gap_seconds=0)
        for n, source in enumerate(("abc123", "def456", "ghi789")):
            attempt = self.ledger.reserve("queue1", source)
            self.ledger.complete(attempt, 200, billed_request_type="request_premium")
            self.ledger.hold("media_capture_or_validation_failed")
            if n < 2:
                report = self.ledger.resume_reviewed_media_failure("queue1", source, "Reviewed failed slide")
                self.assertIsNone(report["global_hold"])
            else:
                with self.assertRaisesRegex(UsageBlocked, "run_media_failure_limit_reached"):
                    self.ledger.resume_reviewed_media_failure("queue1", source, "Reviewed failed slide")
        self.assertEqual(self.ledger.report()["global_hold"], "media_capture_or_validation_failed")

    def test_media_resume_refuses_other_holds(self):
        self.run_with(min_gap_seconds=0)
        attempt = self.ledger.reserve("queue1", "abc123")
        self.ledger.complete(attempt, 200, billed_request_type="request_premium")
        self.ledger.hold("provider_wall_time_exceeded")
        with self.assertRaisesRegex(UsageBlocked, "hold_not_eligible_for_media_resume"):
            self.ledger.resume_reviewed_media_failure("queue1", "abc123", "Reviewed failed slide")

    def test_other_latched_hold_event_prevents_timeout_resume(self):
        self.run_with()
        self.ledger.reserve("queue1", "abc123")
        self.ledger.hold("provider_wall_time_exceeded")
        self.ledger.hold("media_capture_or_validation_failed")
        self.ledger.reconcile_portal("queue1", self.portal_evidence(["abc123"], total="0.003"),
                                     "Reviewed exact portal row")
        with self.assertRaisesRegex(UsageBlocked, "other_safety_hold"):
            self.ledger.resume_reviewed_timeout("queue1", "Reviewed exact portal row")

    def test_later_portal_snapshot_reuses_immutable_old_audit_and_adds_new_charges(self):
        self.run_with(min_gap_seconds=0)
        self.ledger.reserve("queue1", "abc123")
        self.ledger.hold("provider_wall_time_exceeded")
        path = self.portal_evidence(["abc123"], total="0.003")
        first = self.ledger.reconcile_portal("queue1", path, "Reviewed first timeout in portal")
        self.ledger.resume_reviewed_timeout("queue1", "Reviewed first timeout and killed worker")
        old_digest = first["reconciliations"][0]["evidence_sha256"]
        self.now[0] += 20
        second = self.ledger.reserve("queue1", "def456")
        self.ledger.complete(second, 200, "request_premium")
        evidence = json.loads(path.read_text())
        evidence["rows"].append({"source_id": "def456", "url": "https://www.instagram.com/reel/def456/",
                                 "time_local": datetime.fromtimestamp(self.now[0], timezone.utc).isoformat(),
                                 "status_code": 200, "result": "OK", "portal_type": "Fetch",
                                 "billing_class": None, "actual_usd": "0.003"})
        evidence["total_actual_usd"] = "0.006"
        path.write_text(json.dumps(evidence))
        updated = self.ledger.reconcile_portal("queue1", path, "Reviewed complete portal snapshot")
        self.assertEqual(updated["known_actual_micro"], 6000)
        self.assertEqual(updated["reconciliations"][0]["evidence_sha256"], old_digest)
        self.assertNotEqual(updated["reconciliations"][1]["evidence_sha256"], old_digest)
        self.assertEqual(len(updated["reconciliations"]), 2)
        self.assertEqual(len(self.ledger.reconcile_portal("queue1", path, "Reviewed again")["reconciliations"]), 2)
        evidence["rows"][0]["actual_usd"] = "0.002"
        evidence["total_actual_usd"] = "0.005"
        path.write_text(json.dumps(evidence))
        with self.assertRaisesRegex(ValueError, "conflicts with existing actual"):
            self.ledger.reconcile_portal("queue1", path, "Reviewed conflicting snapshot")
        self.assertEqual(self.ledger.report("queue1")["known_actual_micro"], 6000)

    def test_one_recovery_slot_keeps_budget_and_original_count(self):
        self.ledger.create_run("queue1", "0.015", max_requests=2, min_gap_seconds=0)
        parent = self.ledger.reserve("queue1", "lost123")
        self.ledger.hold("provider_wall_time_exceeded")
        path = self.portal_evidence(["lost123"], total="0.003")
        self.ledger.reconcile_portal("queue1", path, "Reviewed portal-paid lost response")
        self.ledger.resume_reviewed_timeout("queue1", "Worker stopped and portal charge confirmed")
        # No usable post came back, so the standing rule would retry it; an allowance with
        # its own path takes precedence once it exists.
        self.assertEqual(self.ledger.automatic_retry_for("lost123"), parent)
        self.ledger.authorize_lost_response_recovery("queue1", parent,
            "One explicit extra call for paid response lost locally")
        self.assertIsNone(self.ledger.automatic_retry_for("lost123"))
        with self.assertRaisesRegex(UsageBlocked, "run_recovery_allowance"):
            self.ledger.authorize_lost_response_recovery("queue1", parent, "Second attempt is prohibited")
        other = self.ledger.reserve("queue1", "other456")
        self.ledger.complete(other, 200, "request_premium")
        self.now[0] += 20
        with self.assertRaisesRegex(UsageBlocked, "recovery_not_authorized"):
            self.ledger.reserve("queue1", "wrong789", recovery_of=parent,
                                recovery_source_url="https://www.instagram.com/reel/wrong789/")
        with self.assertRaisesRegex(UsageBlocked, "recovery_not_authorized"):
            self.ledger.reserve("queue1", "lost123", recovery_of=parent,
                                recovery_source_url="https://www.instagram.com/p/lost123/")
        recovery = self.ledger.reserve("queue1", "lost123", recovery_of=parent,
                                       recovery_source_url="https://www.instagram.com/reel/lost123/")
        self.assertEqual(self.ledger.report("queue1")["attempt_count"], 3)
        self.assertEqual(self.ledger.report("queue1")["cost_envelope_micro"], 15000)
        self.assertEqual(self.ledger.report("queue1")["attempt_records"][-1]["recovery_of"], parent)
        self.ledger.complete(recovery, 200, "request_premium")
        with self.assertRaisesRegex(UsageBlocked, "source_already_attempted"):
            self.ledger.reserve("queue1", "lost123")
        with self.assertRaisesRegex(UsageBlocked, "recovery_not_authorized"):
            self.ledger.reserve("queue1", "lost123", recovery_of=parent,
                                recovery_source_url="https://www.instagram.com/reel/lost123/")
        self.assertEqual(self.ledger.report("queue1")["runs"][0]["max_requests"], 2)

    def test_recovery_fails_if_exact_budget_cannot_reserve(self):
        self.ledger.create_run("queue1", "0.006", max_requests=1, min_gap_seconds=0)
        parent = self.ledger.reserve("queue1", "lost123")
        self.ledger.hold("provider_wall_time_exceeded")
        self.ledger.reconcile_portal("queue1", self.portal_evidence(["lost123"], total="0.003"),
                                     "Reviewed portal-paid lost response")
        self.ledger.resume_reviewed_timeout("queue1", "Worker stopped and portal charge confirmed")
        self.ledger.authorize_lost_response_recovery("queue1", parent,
            "One explicit extra call for paid response lost locally")
        with self.assertRaisesRegex(UsageBlocked, "budget_limit"):
            self.ledger.reserve("queue1", "lost123", recovery_of=parent,
                                recovery_source_url="https://www.instagram.com/reel/lost123/")
        self.assertEqual(self.ledger.report("queue1")["attempt_count"], 1)

    def test_recovery_duplicate_source_portal_rows_require_attempt_ids(self):
        self.ledger.create_run("queue1", "0.02", max_requests=1, min_gap_seconds=0)
        parent = self.ledger.reserve("queue1", "lost123")
        self.ledger.hold("provider_wall_time_exceeded")
        path = self.portal_evidence(["lost123"], total="0.003")
        old_row = json.loads(path.read_text())["rows"][0]
        self.ledger.reconcile_portal("queue1", path, "Reviewed portal-paid lost response")
        self.ledger.resume_reviewed_timeout("queue1", "Worker stopped and portal charge confirmed")
        self.ledger.authorize_lost_response_recovery("queue1", parent,
            "One explicit extra call for paid response lost locally")
        self.now[0] += 20
        recovery = self.ledger.reserve("queue1", "lost123", recovery_of=parent,
                                       recovery_source_url="https://www.instagram.com/reel/lost123/")
        self.ledger.complete(recovery, 200, "request_premium")
        new_row = dict(old_row, attempt_id=recovery,
                       time_local=datetime.fromtimestamp(self.now[0], timezone.utc).isoformat())
        evidence = {"evidence_source": "https://portal.usestring.ai/web-access", "run_id": "queue1",
                    "total_actual_usd": "0.006", "rows": [old_row, new_row]}
        path.write_text(json.dumps(evidence))
        with self.assertRaisesRegex(ValueError, "exact attempt_id"):
            self.ledger.reconcile_portal("queue1", path, "Reviewed two exact charges")
        evidence["rows"][0]["attempt_id"] = parent
        path.write_text(json.dumps(evidence))
        result = self.ledger.reconcile_portal("queue1", path, "Reviewed two exact charges")
        self.assertEqual(result["known_actual_micro"], 6000)
        self.assertEqual(len(result["reconciliations"]), 2)

    def test_legacy_unique_schema_migrates_without_losing_attempt_ids(self):
        path = self.path.parent / "legacy.sqlite"
        with closing(sqlite3.connect(path)) as db:
            db.executescript("""
                CREATE TABLE runs(run_id TEXT PRIMARY KEY,created_at REAL NOT NULL,budget_micro INTEGER NOT NULL,
                    max_requests INTEGER NOT NULL,max_request_micro INTEGER NOT NULL,min_gap_seconds REAL NOT NULL,
                    burn_limit_micro INTEGER NOT NULL,burn_window_seconds REAL NOT NULL);
                CREATE TABLE attempts(attempt_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    run_id TEXT NOT NULL REFERENCES runs(run_id),source_id TEXT NOT NULL,
                    source_host TEXT NOT NULL,reserved_at REAL NOT NULL,reserve_micro INTEGER NOT NULL,
                    expected_class TEXT,settled_at REAL,status_code INTEGER,billing_class TEXT,
                    estimate_micro INTEGER,actual_micro INTEGER,actual_source TEXT,request_id TEXT,
                    error_code TEXT,usable_media INTEGER NOT NULL DEFAULT 0,note_path TEXT,
                    UNIQUE(source_host,source_id));
                CREATE TABLE reconciliations(attempt_id INTEGER PRIMARY KEY REFERENCES attempts(attempt_id),
                    at REAL NOT NULL,evidence_sha256 TEXT NOT NULL,evidence_path TEXT NOT NULL,
                    review_note TEXT NOT NULL,source_url TEXT NOT NULL,portal_time TEXT NOT NULL,
                    portal_status INTEGER NOT NULL,portal_type TEXT NOT NULL,actual_micro INTEGER NOT NULL,
                    prior_pending INTEGER NOT NULL,prior_status INTEGER,prior_billing_class TEXT,
                    prior_actual_micro INTEGER,prior_error_code TEXT);
                INSERT INTO runs VALUES('queue1',1000,1000000,26,6000,0,30000,60);
                INSERT INTO attempts(run_id,source_id,source_host,reserved_at,reserve_micro)
                    VALUES('queue1','lost123','www.instagram.com',1000,6000);
                INSERT INTO reconciliations VALUES(1,1001,'digest','/tmp/evidence','reviewed',
                    'https://www.instagram.com/reel/lost123/','1970-01-01T00:16:40+00:00',
                    200,'Fetch',3000,1,NULL,NULL,NULL,NULL);
            """)
        migrated = Ledger(path, clock=lambda: self.now[0])
        report = migrated.report("queue1")
        self.assertEqual(report["attempt_records"][0]["attempt_id"], 1)
        self.assertIsNone(report["attempt_records"][0]["recovery_of"])
        self.assertEqual(report["reconciliations"][0]["attempt_id"], 1)
        with closing(sqlite3.connect(path)) as db:
            self.assertEqual(db.execute("PRAGMA foreign_key_check").fetchall(), [])
            self.assertEqual(db.execute("SELECT COUNT(*) FROM attempts").fetchone()[0], 1)


if __name__ == "__main__":
    unittest.main()
