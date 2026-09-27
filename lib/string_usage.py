"""Durable, conservative metering for credentialless String capture.

Amounts are integer microdollars. Published STARTER prices are estimates, not
an invoice; actual charges remain unknown unless the provider reports them.
This ledger never makes a network request. Portal reconciliation can release only
an explicitly reviewed, fully accounted timeout hold.
"""
from __future__ import annotations

import re
import hashlib
import json
import sqlite3
import time
from contextlib import contextmanager
from decimal import Decimal, InvalidOperation, ROUND_CEILING
from datetime import datetime
from pathlib import Path


STARTER_MICRODOLLARS = {
    "request_standard": 300,
    "request_premium": 3000,
    "browser_standard": 1500,
    "browser_premium": 6000,
}
_TOKEN = re.compile(r"^[A-Za-z0-9_-]{1,80}$")
_HOST = re.compile(r"^[a-z0-9.-]{1,120}$")
_SOURCE_URL = re.compile(r"^https://www\.instagram\.com/(?:reel|p)/([A-Za-z0-9_-]{1,80})/$")
_RESUMABLE_HOLDS = frozenset({"provider_wall_time_exceeded"})


class UsageBlocked(RuntimeError):
    """A preflight limit or persistent hold stopped the next paid request."""

    def __init__(self, reason: str, wait_seconds: float = 0):
        self.reason = reason
        self.wait_seconds = max(0.0, wait_seconds)
        super().__init__(reason)


def _money(value: object, *, positive: bool = False) -> int:
    try:
        amount = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise ValueError("cost must be a finite dollar amount") from exc
    if not amount.is_finite() or amount < 0 or (positive and amount == 0):
        raise ValueError("cost must be finite and nonnegative; budget must be positive")
    if amount > Decimal("9000000000"):
        raise ValueError("cost is too large")
    micros = int((amount * 1_000_000).to_integral_value(rounding=ROUND_CEILING))
    return micros


def _token(value: object, field: str) -> str:
    if not isinstance(value, str) or not _TOKEN.fullmatch(value):
        raise ValueError(f"{field} must be a short plain identifier")
    return value


def _seconds(value: object, field: str) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field} must be finite and nonnegative") from exc
    if number < 0 or number == float("inf") or number != number:
        raise ValueError(f"{field} must be finite and nonnegative")
    return number


class Ledger:
    def __init__(self, path: str | Path | None = None, *, clock=None):
        self.path = Path(path) if path is not None else (
            Path.home() / ".local/state/ig-yt-x-knowledge-extract/string-usage.sqlite3"
        )
        self._clock = clock or time.time
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        with self._db() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS runs (
                    run_id TEXT PRIMARY KEY, created_at REAL NOT NULL,
                    budget_micro INTEGER NOT NULL, max_requests INTEGER NOT NULL,
                    max_request_micro INTEGER NOT NULL, min_gap_seconds REAL NOT NULL,
                    burn_limit_micro INTEGER NOT NULL, burn_window_seconds REAL NOT NULL
                );
                CREATE TABLE IF NOT EXISTS attempts (
                    attempt_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    run_id TEXT NOT NULL REFERENCES runs(run_id),
                    source_id TEXT NOT NULL, source_host TEXT NOT NULL,
                    reserved_at REAL NOT NULL, reserve_micro INTEGER NOT NULL,
                    expected_class TEXT, settled_at REAL, status_code INTEGER,
                    billing_class TEXT, estimate_micro INTEGER,
                    actual_micro INTEGER, actual_source TEXT,
                    request_id TEXT, error_code TEXT,
                    usable_media INTEGER NOT NULL DEFAULT 0,
                    note_path TEXT,
                    UNIQUE(source_host, source_id)
                );
                CREATE INDEX IF NOT EXISTS attempts_run ON attempts(run_id);
                CREATE TABLE IF NOT EXISTS control (
                    singleton INTEGER PRIMARY KEY CHECK(singleton = 1),
                    hold_reason TEXT, hold_at REAL
                );
                INSERT OR IGNORE INTO control(singleton) VALUES (1);
                CREATE TABLE IF NOT EXISTS events (
                    event_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    at REAL NOT NULL, run_id TEXT, attempt_id INTEGER,
                    code TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS reconciliations (
                    attempt_id INTEGER PRIMARY KEY REFERENCES attempts(attempt_id),
                    at REAL NOT NULL, evidence_sha256 TEXT NOT NULL,
                    evidence_path TEXT NOT NULL, review_note TEXT NOT NULL,
                    source_url TEXT NOT NULL, portal_time TEXT NOT NULL,
                    portal_status INTEGER NOT NULL, portal_type TEXT NOT NULL,
                    actual_micro INTEGER NOT NULL,
                    prior_pending INTEGER NOT NULL, prior_status INTEGER,
                    prior_billing_class TEXT, prior_actual_micro INTEGER,
                    prior_error_code TEXT
                );
                CREATE TABLE IF NOT EXISTS reviews (
                    review_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    at REAL NOT NULL, run_id TEXT NOT NULL, code TEXT NOT NULL,
                    note TEXT NOT NULL
                );
            """)
        if self.path.exists():
            self.path.chmod(0o600)

    @contextmanager
    def _db(self):
        db = sqlite3.connect(self.path, timeout=20, isolation_level=None)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA busy_timeout=20000")
        db.execute("PRAGMA foreign_keys=ON")
        try:
            yield db
        finally:
            db.close()

    @contextmanager
    def _write(self):
        with self._db() as db:
            db.execute("BEGIN IMMEDIATE")
            try:
                yield db
            except UsageBlocked:
                # Limit checks may latch a hold immediately before rejecting.
                db.commit()
                raise
            except BaseException:
                db.rollback()
                raise
            else:
                db.commit()

    def _event(self, db, code: str, run_id=None, attempt_id=None):
        db.execute("INSERT INTO events(at,run_id,attempt_id,code) VALUES(?,?,?,?)",
                   (self._clock(), run_id, attempt_id, code))

    def _hold(self, db, reason: str, run_id=None, attempt_id=None):
        db.execute("UPDATE control SET hold_reason=COALESCE(hold_reason,?), "
                   "hold_at=COALESCE(hold_at,?) WHERE singleton=1",
                   (reason, self._clock()))
        self._event(db, "hold:" + reason, run_id, attempt_id)

    def hold(self, reason: str) -> dict:
        """Latch a generic local failure reason; there is deliberately no clear API."""
        reason = _token(reason, "reason")
        with self._write() as db:
            self._hold(db, reason)
        return self.report()

    def create_run(
        self, run_id: str, budget_usd: object, *, max_requests: int = 30,
        max_request_usd: object = "0.006", min_gap_seconds: object = 10,
        burn_limit_usd: object = "0.03", burn_window_seconds: object = 60,
    ) -> dict:
        run_id = _token(run_id, "run_id")
        if isinstance(max_requests, bool) or not isinstance(max_requests, int) or max_requests < 1:
            raise ValueError("max_requests must be a positive integer")
        values = (_money(budget_usd, positive=True), max_requests,
                  _money(max_request_usd, positive=True),
                  _seconds(min_gap_seconds, "min_gap_seconds"),
                  _money(burn_limit_usd, positive=True),
                  _seconds(burn_window_seconds, "burn_window_seconds"))
        if values[2] > values[0]:
            raise ValueError("per-request ceiling exceeds run budget")
        with self._write() as db:
            old = db.execute("SELECT * FROM runs WHERE run_id=?", (run_id,)).fetchone()
            if old:
                original = tuple(old[key] for key in (
                    "budget_micro", "max_requests", "max_request_micro", "min_gap_seconds",
                    "burn_limit_micro", "burn_window_seconds"))
                if original != values:
                    raise ValueError("existing run constraints are immutable")
            else:
                hold = db.execute("SELECT hold_reason FROM control WHERE singleton=1").fetchone()[0]
                if hold:
                    raise UsageBlocked("global_hold:" + hold)
                pending = db.execute("SELECT 1 FROM attempts WHERE settled_at IS NULL LIMIT 1").fetchone()
                if pending:
                    raise UsageBlocked("pending_attempt_requires_reconciliation")
                db.execute("INSERT INTO runs VALUES(?,?,?,?,?,?,?,?)",
                           (run_id, self._clock(), *values))
                self._event(db, "run_created", run_id)
        return self.report(run_id)

    @staticmethod
    def _envelope(row) -> int:
        if row["settled_at"] is None:
            return row["reserve_micro"]
        if row["actual_micro"] is None:
            return max(row["reserve_micro"], row["estimate_micro"] or 0)
        return max(row["actual_micro"], row["estimate_micro"] or 0)

    def reserve(self, run_id: str, source_id: str,
                source_host: str = "www.instagram.com", *,
                expected_billing_class: str | None = None) -> int:
        run_id = _token(run_id, "run_id")
        source_id = _token(source_id, "source_id")
        if not isinstance(source_host, str) or not _HOST.fullmatch(source_host):
            raise ValueError("source_host must be a plain hostname")
        if expected_billing_class is not None and expected_billing_class not in STARTER_MICRODOLLARS:
            raise ValueError("expected_billing_class is unknown")
        now = self._clock()
        with self._write() as db:
            run = db.execute("SELECT * FROM runs WHERE run_id=?", (run_id,)).fetchone()
            if not run:
                raise ValueError("unknown run")
            hold = db.execute("SELECT hold_reason FROM control WHERE singleton=1").fetchone()[0]
            if hold:
                raise UsageBlocked("global_hold:" + hold)
            if db.execute("SELECT 1 FROM attempts WHERE settled_at IS NULL LIMIT 1").fetchone():
                raise UsageBlocked("pending_attempt_requires_reconciliation")
            if db.execute("SELECT 1 FROM attempts WHERE source_host=? AND source_id=?",
                          (source_host, source_id)).fetchone():
                raise UsageBlocked("source_already_attempted")
            last = db.execute("SELECT MAX(reserved_at) FROM attempts").fetchone()[0]
            if last is not None and now - last < run["min_gap_seconds"]:
                raise UsageBlocked("minimum_gap", run["min_gap_seconds"] - (now - last))
            rows = db.execute("SELECT * FROM attempts WHERE run_id=?", (run_id,)).fetchall()
            if len(rows) >= run["max_requests"]:
                self._hold(db, "request_limit", run_id)
                raise UsageBlocked("request_limit")
            consumed = sum(self._envelope(row) for row in rows)
            if consumed + run["max_request_micro"] > run["budget_micro"]:
                self._hold(db, "budget_limit", run_id)
                raise UsageBlocked("budget_limit")
            recent = db.execute("SELECT * FROM attempts WHERE reserved_at>?",
                                (now - run["burn_window_seconds"],)).fetchall()
            burn = sum(self._envelope(row) for row in recent)
            if burn + run["max_request_micro"] > run["burn_limit_micro"]:
                self._hold(db, "burn_limit", run_id)
                raise UsageBlocked("burn_limit")
            cursor = db.execute("INSERT INTO attempts(run_id,source_id,source_host,reserved_at,"
                                "reserve_micro,expected_class) VALUES(?,?,?,?,?,?)",
                                (run_id, source_id, source_host, now, run["max_request_micro"],
                                 expected_billing_class))
            attempt_id = cursor.lastrowid
            self._event(db, "reserved", run_id, attempt_id)
            return attempt_id

    def complete(self, attempt_id: int, status_code: int | None,
                 billed_request_type: str | None = None, *, actual_usd: object | None = None,
                 request_id: str | None = None, error: str | None = None,
                 usable_media: bool = False) -> dict:
        """Settle once. Unknown provider charges remain unknown and held at reserve."""
        with self._write() as db:
            row = db.execute("SELECT * FROM attempts WHERE attempt_id=?", (attempt_id,)).fetchone()
            if not row:
                raise ValueError("unknown attempt")
            if row["settled_at"] is not None:
                raise ValueError("attempt already settled")
            reasons = []
            if not isinstance(status_code, int) or isinstance(status_code, bool) or not 200 <= status_code < 300:
                reasons.append("provider_denial")
                status_code = status_code if isinstance(status_code, int) and not isinstance(status_code, bool) else None
            if billed_request_type not in STARTER_MICRODOLLARS:
                reasons.append("unknown_billing_class")
            elif row["expected_class"] is not None and billed_request_type != row["expected_class"]:
                reasons.append("unexpected_billing_class")
            estimate = STARTER_MICRODOLLARS.get(billed_request_type)
            first_class = db.execute(
                "SELECT billing_class FROM attempts WHERE run_id=? AND attempt_id<>? "
                "AND billing_class IS NOT NULL ORDER BY attempt_id LIMIT 1",
                (row["run_id"], attempt_id),
            ).fetchone()
            # A fourfold jump from the first verified class is a surprise even
            # when it remains under the worst-case reservation.
            if first_class and estimate is not None and estimate > 4 * STARTER_MICRODOLLARS[first_class[0]]:
                reasons.append("rate_jump")
            actual = None
            if actual_usd is not None:
                try:
                    actual = _money(actual_usd)
                except ValueError:
                    reasons.append("invalid_actual_cost")
            if error:
                reasons.append("provider_error")
            if actual is not None and actual > row["reserve_micro"]:
                reasons.append("cost_above_reservation")
            if estimate is not None and estimate > row["reserve_micro"]:
                reasons.append("class_above_reservation")
            safe_request_id = request_id if isinstance(request_id, str) and _TOKEN.fullmatch(request_id) else None
            if request_id and safe_request_id is None:
                reasons.append("invalid_request_id")
            db.execute("UPDATE attempts SET settled_at=?,status_code=?,billing_class=?,"
                       "estimate_micro=?,actual_micro=?,actual_source=?,request_id=?,"
                       "error_code=?,usable_media=? WHERE attempt_id=?",
                       (self._clock(), status_code,
                        billed_request_type if estimate is not None else None,
                        estimate, actual, "provider_reported" if actual is not None else None,
                        safe_request_id, "provider_error" if error else None,
                        int(bool(usable_media)), attempt_id))
            self._event(db, "settled", row["run_id"], attempt_id)
            for reason in reasons:
                self._hold(db, reason, row["run_id"], attempt_id)
        return self.report(row["run_id"])

    def mark_media(self, attempt_id: int, usable: bool = True) -> None:
        with self._write() as db:
            row = db.execute("SELECT * FROM attempts WHERE attempt_id=?", (attempt_id,)).fetchone()
            if (not row or row["settled_at"] is None or row["status_code"] is None
                    or not 200 <= row["status_code"] < 300 or row["error_code"]):
                raise ValueError("successful settled attempt is required before marking media")
            db.execute("UPDATE attempts SET usable_media=? WHERE attempt_id=?",
                       (int(bool(usable)), attempt_id))
            self._event(db, "media_verified" if usable else "media_unusable", row["run_id"], attempt_id)

    def mark_filed(self, attempt_id: int, note_path: str | Path) -> None:
        path = Path(note_path)
        if not path.is_file():
            raise ValueError("note file must exist before marking filed")
        with self._write() as db:
            row = db.execute("SELECT * FROM attempts WHERE attempt_id=?", (attempt_id,)).fetchone()
            if not row or not row["usable_media"] or row["settled_at"] is None:
                raise ValueError("usable settled media is required before filing")
            db.execute("UPDATE attempts SET note_path=? WHERE attempt_id=?",
                       (str(path.resolve()), attempt_id))
            self._event(db, "filed", row["run_id"], attempt_id)

    def reconcile_portal(self, run_id: str, evidence_path: str | Path, review_note: str) -> dict:
        """Account for every attempt in a run using a reviewed portal snapshot.

        Evidence is local JSON, never fetched here. The transaction is all-or-none.
        Original attempt fields are retained in the append-only audit table before
        a pending reservation is settled or an actual charge is updated.
        """
        run_id = _token(run_id, "run_id")
        if (not isinstance(review_note, str) or not 12 <= len(review_note) <= 500
                or any(ord(ch) < 32 for ch in review_note)):
            raise ValueError("review_note must be 12-500 printable characters")
        path = Path(evidence_path).resolve(strict=True)
        raw = path.read_bytes()
        if len(raw) > 1_000_000:
            raise ValueError("portal evidence is too large")
        try:
            evidence = json.loads(raw)
        except (ValueError, UnicodeError) as exc:
            raise ValueError("portal evidence must be JSON") from exc
        if not isinstance(evidence, dict) or evidence.get("run_id") != run_id:
            raise ValueError("portal evidence run_id differs")
        if evidence.get("evidence_source") != "https://portal.usestring.ai/web-access":
            raise ValueError("portal evidence source is not recognized")
        rows = evidence.get("rows")
        if not isinstance(rows, list) or not rows:
            raise ValueError("portal evidence has no rows")
        seen = {}
        total = 0
        for item in rows:
            if not isinstance(item, dict):
                raise ValueError("portal row must be an object")
            source_id = _token(item.get("source_id"), "source_id")
            match = _SOURCE_URL.fullmatch(item.get("url", "")) if isinstance(item.get("url"), str) else None
            if not match or match.group(1) != source_id or source_id in seen:
                raise ValueError("portal URL/ID mismatch or duplicate")
            if item.get("status_code") != 200 or item.get("result") != "OK" or item.get("portal_type") != "Fetch":
                raise ValueError("portal row is not a successful Fetch")
            if item.get("billing_class") is not None:
                raise ValueError("portal class must remain unknown; do not infer it from price")
            try:
                portal_time = datetime.fromisoformat(item["time_local"])
            except (KeyError, TypeError, ValueError) as exc:
                raise ValueError("portal row needs a timestamp with timezone") from exc
            if portal_time.tzinfo is None:
                raise ValueError("portal row needs a timestamp with timezone")
            actual = _money(item.get("actual_usd"), positive=True)
            seen[source_id] = (item, portal_time.timestamp(), actual)
            total += actual
        if total != _money(evidence.get("total_actual_usd")):
            raise ValueError("portal total differs from its rows")
        digest = hashlib.sha256(raw).hexdigest()
        with self._write() as db:
            attempts = db.execute("SELECT * FROM attempts WHERE run_id=? ORDER BY attempt_id", (run_id,)).fetchall()
            if not attempts or {row["source_id"] for row in attempts} != set(seen):
                raise ValueError("portal rows must match every run attempt exactly")
            for row in attempts:
                item, portal_at, actual = seen[row["source_id"]]
                if row["source_host"] != "www.instagram.com":
                    raise ValueError("unexpected source host")
                if abs(portal_at - row["reserved_at"]) > 15 * 60:
                    raise ValueError("portal time does not match reservation")
                if row["actual_micro"] is not None and row["actual_micro"] != actual:
                    raise ValueError("portal charge conflicts with existing actual")
                if actual > row["reserve_micro"]:
                    raise ValueError("portal charge exceeds reservation")
                prior = db.execute("SELECT * FROM reconciliations WHERE attempt_id=?",
                                   (row["attempt_id"],)).fetchone()
                if prior is not None:
                    if (prior["source_url"] != item["url"] or prior["portal_time"] != item["time_local"]
                            or prior["portal_status"] != 200 or prior["portal_type"] != "Fetch"
                            or prior["actual_micro"] != actual or row["actual_micro"] != actual):
                        raise ValueError("portal snapshot conflicts with prior reconciliation")
                    continue
                if row["settled_at"] is not None and (row["status_code"] != 200 or row["error_code"]):
                    raise ValueError("cannot reconcile a denied or failed settled attempt")
                db.execute("INSERT INTO reconciliations VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                           (row["attempt_id"], self._clock(), digest, str(path), review_note,
                            item["url"], item["time_local"], 200, "Fetch", actual,
                            int(row["settled_at"] is None), row["status_code"], row["billing_class"],
                            row["actual_micro"], row["error_code"]))
                db.execute("UPDATE attempts SET settled_at=COALESCE(settled_at,?), "
                           "actual_micro=?,actual_source='portal_reconciled' WHERE attempt_id=?",
                           (self._clock(), actual, row["attempt_id"]))
                self._event(db, "portal_reconciled", run_id, row["attempt_id"])
        return self.report(run_id)

    def resume_reviewed_timeout(self, run_id: str, review_note: str) -> dict:
        """Release only a timeout hold after complete portal billing review."""
        run_id = _token(run_id, "run_id")
        if (not isinstance(review_note, str) or not 12 <= len(review_note) <= 500
                or any(ord(ch) < 32 for ch in review_note)):
            raise ValueError("review_note must be 12-500 printable characters")
        with self._write() as db:
            hold = db.execute("SELECT hold_reason FROM control WHERE singleton=1").fetchone()[0]
            if hold not in _RESUMABLE_HOLDS:
                raise UsageBlocked("hold_not_eligible_for_reviewed_resume:" + str(hold))
            attempts = db.execute("SELECT * FROM attempts WHERE run_id=?", (run_id,)).fetchall()
            if not attempts or any(row["settled_at"] is None or row["actual_micro"] is None for row in attempts):
                raise UsageBlocked("run_has_unreconciled_attempts")
            if db.execute("SELECT 1 FROM attempts WHERE settled_at IS NULL OR actual_micro IS NULL LIMIT 1").fetchone():
                raise UsageBlocked("ledger_has_unreconciled_attempts")
            reconciled = db.execute("SELECT COUNT(*) FROM reconciliations WHERE attempt_id IN "
                                    "(SELECT attempt_id FROM attempts WHERE run_id=?)", (run_id,)).fetchone()[0]
            if reconciled != len(attempts):
                raise UsageBlocked("run_missing_portal_evidence")
            # Never release denial, price, class, media or logging anomalies merely
            # because the first latched reason happened to be a timeout.
            last_resume = db.execute("SELECT COALESCE(MAX(event_id),0) FROM events "
                                     "WHERE code='reviewed_timeout_resume'").fetchone()[0]
            hold_events = [r[0][5:] for r in db.execute(
                "SELECT code FROM events WHERE event_id>? AND code LIKE 'hold:%'", (last_resume,))]
            if any(reason not in _RESUMABLE_HOLDS for reason in hold_events):
                raise UsageBlocked("other_safety_hold_requires_review")
            # A single unresolved pending attempt is the supported interruption;
            # it remains historically visible in reconciliations.
            interrupted = db.execute(
                "SELECT COUNT(*) FROM events e JOIN reconciliations r ON e.attempt_id=r.attempt_id "
                "WHERE e.event_id>? AND e.code='portal_reconciled' AND e.run_id=? AND r.prior_pending=1",
                (last_resume, run_id)).fetchone()[0]
            if interrupted != 1:
                raise UsageBlocked("timeout_attempt_evidence_missing")
            db.execute("UPDATE control SET hold_reason=NULL,hold_at=NULL WHERE singleton=1")
            self._event(db, "reviewed_timeout_resume", run_id)
            db.execute("INSERT INTO reviews(at,run_id,code,note) VALUES(?,?,?,?)",
                       (self._clock(), run_id, "reviewed_timeout_resume", review_note))
        return self.report(run_id)

    def report(self, run_id: str | None = None) -> dict:
        if run_id is not None:
            _token(run_id, "run_id")
        with self._db() as db:
            hold = db.execute("SELECT hold_reason,hold_at FROM control WHERE singleton=1").fetchone()
            if run_id is not None and not db.execute("SELECT 1 FROM runs WHERE run_id=?", (run_id,)).fetchone():
                raise ValueError("unknown run")
            rows = db.execute("SELECT * FROM attempts WHERE run_id=? ORDER BY attempt_id", (run_id,)).fetchall() if run_id else db.execute("SELECT * FROM attempts ORDER BY attempt_id").fetchall()
            events = db.execute("SELECT at,run_id,attempt_id,code FROM events WHERE run_id=? ORDER BY event_id", (run_id,)).fetchall() if run_id else db.execute("SELECT at,run_id,attempt_id,code FROM events ORDER BY event_id").fetchall()
            reviews = db.execute("SELECT at,run_id,code,note FROM reviews WHERE run_id=? ORDER BY review_id", (run_id,)).fetchall() if run_id else db.execute("SELECT at,run_id,code,note FROM reviews ORDER BY review_id").fetchall()
            recs = db.execute("SELECT attempt_id,evidence_sha256,evidence_path,review_note,source_url,portal_time,portal_status,portal_type,actual_micro,prior_pending,prior_status,prior_billing_class,prior_actual_micro,prior_error_code FROM reconciliations WHERE attempt_id IN (SELECT attempt_id FROM attempts WHERE run_id=?) ORDER BY attempt_id", (run_id,)).fetchall() if run_id else db.execute("SELECT attempt_id,evidence_sha256,evidence_path,review_note,source_url,portal_time,portal_status,portal_type,actual_micro,prior_pending,prior_status,prior_billing_class,prior_actual_micro,prior_error_code FROM reconciliations ORDER BY attempt_id").fetchall()
            runs = db.execute("SELECT * FROM runs WHERE run_id=?", (run_id,)).fetchall() if run_id else db.execute("SELECT * FROM runs ORDER BY created_at").fetchall()
        pending = [row for row in rows if row["settled_at"] is None]
        return {
            "run_id": run_id,
            "runs": [{"run_id": r["run_id"], "budget_micro": r["budget_micro"],
                      "max_requests": r["max_requests"], "max_request_micro": r["max_request_micro"]} for r in runs],
            "global_hold": hold["hold_reason"], "hold_reason": hold["hold_reason"],
            "hold_at": hold["hold_at"],
            "attempt_count": len(rows), "attempts": len(rows),
            "pending_count": len(pending), "outstanding_attempts": len(pending),
            "reserved_outstanding_micro": sum(r["reserve_micro"] for r in pending),
            "estimated_micro": sum(r["estimate_micro"] or 0 for r in rows),
            "known_actual_micro": sum(r["actual_micro"] or 0 for r in rows),
            "unknown_actual_count": sum(r["actual_micro"] is None for r in rows),
            "cost_envelope_micro": sum(self._envelope(r) for r in rows),
            "usable_media_count": sum(bool(r["usable_media"]) for r in rows),
            "filed_count": sum(r["note_path"] is not None for r in rows),
            "attempt_records": [{"attempt_id": r["attempt_id"], "run_id": r["run_id"],
                          "source_id": r["source_id"], "source_host": r["source_host"],
                          "pending": r["settled_at"] is None,
                          "status_code": r["status_code"], "expected_billing_class": r["expected_class"],
                          "billing_class": r["billing_class"],
                          "estimate_micro": r["estimate_micro"], "actual_micro": r["actual_micro"],
                          "actual_source": r["actual_source"], "request_id": r["request_id"],
                          "error_code": r["error_code"], "usable_media": bool(r["usable_media"]),
                          "filed": r["note_path"] is not None,
                          "cost_envelope_micro": self._envelope(r)} for r in rows],
            "events": [dict(e) for e in events],
            "reviews": [dict(r) for r in reviews],
            "reconciliations": [dict(r) for r in recs],
        }
