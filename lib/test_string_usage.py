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
        with self.assertRaisesRegex(UsageBlocked, "recovery_not_authorized"):
            self.ledger.reserve("queue1", "lost123", recovery_of=parent,
                                recovery_source_url="https://www.instagram.com/reel/lost123/")
        self.ledger.authorize_lost_response_recovery("queue1", parent,
            "One explicit extra call for paid response lost locally")
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
