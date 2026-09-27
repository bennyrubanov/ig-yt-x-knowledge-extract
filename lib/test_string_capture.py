"""Offline integration checks: budgets precede network, billing precedes media."""
import io
import json
import tempfile
import subprocess
import sys
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError

from string_capture import CaptureStopped, capture, fetch_page, bounded_fetch_page, process_local
from string_usage import Ledger, UsageBlocked


class Response(io.BytesIO):
    status = 200
    headers = {"x-billed-request-type": "request_standard"}


class CaptureTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.ledger = Ledger(self.root / "usage.sqlite")
        self.ledger.create_run("run", ".10", min_gap_seconds=0)

    def tearDown(self):
        self.tmp.cleanup()

    def test_request_contains_no_instagram_credentials_or_expensive_modes(self):
        response = Response(json.dumps({"statusCode": 200, "data": "html"}).encode())
        with patch("string_capture.urllib.request.build_opener") as make:
            make.return_value.open.return_value = response
            result = fetch_page("https://www.instagram.com/reels/Test123/?secret=remove", "fake-provider-key")
            req = make.return_value.open.call_args.args[0]
        self.assertEqual(req.full_url, "https://request.usestring.ai/v1/fetch")
        body = json.loads(req.data)
        self.assertEqual(body["url"], "https://www.instagram.com/reel/Test123/")
        self.assertFalse(body["solveCaptcha"])
        self.assertFalse(body["executeJS"])
        self.assertFalse(body["requireWSS"])
        self.assertNotIn("headers", body)
        self.assertNotIn("Cookie", req.headers)
        self.assertEqual(result["billed_request_type"], "request_standard")

    def test_unknown_billing_stops_before_media_and_blocks_next_run(self):
        with patch("string_capture.api_key", return_value="fake"), patch("string_capture.bounded_fetch_page", return_value={"status_code":200,"data":"html"}), patch("string_capture.download_asset") as dl:
            with self.assertRaises(CaptureStopped):
                capture("https://www.instagram.com/reel/Test123/", "run", self.ledger, self.root)
            dl.assert_not_called()
        self.assertTrue(self.ledger.report()["hold_reason"])
        with self.assertRaises(UsageBlocked):
            self.ledger.create_run("newrun", ".10")

    def test_missing_key_does_not_reserve_or_fetch(self):
        with patch("string_capture.api_key", side_effect=CaptureStopped("missing_string_api_key")), patch("string_capture.bounded_fetch_page") as fetch:
            with self.assertRaises(CaptureStopped):
                capture("https://www.instagram.com/reel/Test123/", "run", self.ledger, self.root)
            fetch.assert_not_called()
        self.assertEqual(self.ledger.report()["attempt_count"], 0)

    def test_parser_failure_keeps_billing_and_stops(self):
        with patch("string_capture.api_key", return_value="fake"), patch("string_capture.bounded_fetch_page", return_value={"status_code":200,"billed_request_type":"request_standard","data":"unavailable"}):
            with self.assertRaises(CaptureStopped):
                capture("https://www.instagram.com/reel/Test123/", "run", self.ledger, self.root)
        report = self.ledger.report()
        self.assertEqual(report["estimated_micro"], 300)
        self.assertTrue(report["hold_reason"])

    def test_unexpected_media_exception_latches_hold(self):
        with patch("string_capture.api_key", return_value="fake"), patch("string_capture.bounded_fetch_page", return_value={"status_code":200,"billed_request_type":"request_standard","data":"html"}), patch("string_capture.extract_media", side_effect=RecursionError("untrusted")):
            with self.assertRaises(CaptureStopped):
                capture("https://www.instagram.com/reel/Test123/", "run", self.ledger, self.root)
        self.assertEqual(self.ledger.report()["hold_reason"], "media_capture_or_validation_failed")

    def test_single_image_is_isolated_and_legacy_slide_named(self):
        def download(url, path, **kwargs):
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"valid image fixture")
        media = {"caption":"caption", "media":[{"kind":"image", "url":"https://a.cdninstagram.com/image"}]}
        with patch("string_capture.api_key", return_value="fake"), patch("string_capture.bounded_fetch_page", return_value={"status_code":200,"billed_request_type":"request_standard","data":"html"}), patch("string_capture.extract_media", return_value=media), patch("string_capture.download_asset", side_effect=download), patch("string_capture.validate_media", return_value={"has_image":True}):
            result = capture("https://www.instagram.com/p/Test123/", "run", self.ledger, self.root)
        manifest = json.loads(Path(result["manifest"]).read_text())
        path = Path(manifest["files"][0]["path"])
        self.assertEqual(path, (self.root / "Test123/slides/slide_01.jpg").resolve())
        with patch("tooling.ocr_to_file") as ocr, patch("tooling.warn_ollama"):
            process_local(Path(result["manifest"]))
            self.assertEqual(ocr.call_args.args[0], path.parent)
        with patch("string_capture.bounded_fetch_page") as network:
            cached = capture("https://www.instagram.com/p/Test123/", "run", self.ledger, self.root)
            self.assertTrue(cached["cached"])
            with self.assertRaises(CaptureStopped):
                capture("https://www.instagram.com/reel/Test123/", "run", self.ledger, self.root)
            network.assert_not_called()

    def test_provider_error_body_and_key_not_exposed(self):
        error = HTTPError("https://request.usestring.ai/v1/fetch", 403, "secret token", {"x-billed-request-type":"request_standard"}, io.BytesIO(b"private body"))
        with patch("string_capture.urllib.request.build_opener") as make:
            make.return_value.open.side_effect = error
            result = fetch_page("https://www.instagram.com/reel/Test123/", "fake-key")
        self.assertEqual(result["status_code"],403)
        self.assertNotIn("private", json.dumps(result))
        self.assertNotIn("secret", json.dumps(result))

    def test_usage_reservation_precedes_network_and_deduplicates(self):
        seen = []
        def fetch(*args):
            seen.append(self.ledger.report()["pending_count"])
            return {"status_code":403,"error":"provider_http_error"}
        with patch("string_capture.api_key", return_value="fake"), patch("string_capture.bounded_fetch_page", side_effect=fetch):
            with self.assertRaises(CaptureStopped):
                capture("https://www.instagram.com/reel/Test123/", "run", self.ledger, self.root)
        self.assertEqual(seen, [1])

    def test_wall_deadline_kills_worker_without_retry_or_media(self):
        with patch("string_capture.subprocess.Popen") as start, patch("string_capture.api_key", return_value="fake-secret"), patch("string_capture.download_asset") as download:
            worker = start.return_value
            worker.communicate.side_effect = [subprocess.TimeoutExpired("worker", 120), ("", None)]
            with self.assertRaises(CaptureStopped):
                capture("https://www.instagram.com/reel/Test123/", "run", self.ledger, self.root)
            worker.kill.assert_called_once()
            start.assert_called_once()
            download.assert_not_called()
            self.assertNotIn("fake-secret", str(start.call_args))
            self.assertEqual(worker.communicate.call_args_list[0].kwargs["timeout"], 120)
        report = self.ledger.report()
        self.assertTrue(report["hold_reason"])
        self.assertEqual(report["attempt_count"], 1)
        self.assertEqual(report["unknown_actual_count"], 1)
        self.assertEqual(report["cost_envelope_micro"], 6000)
        self.assertEqual(report["attempt_records"][0]["error_code"], "provider_error")

    def test_worker_returns_billing_metadata_and_uses_stdin_for_key(self):
        expected = {"status_code":200,"billed_request_type":"request_premium","data":"html"}
        with patch("string_capture.subprocess.Popen") as start:
            worker = start.return_value
            worker.returncode = 0
            worker.communicate.return_value = (json.dumps(expected), None)
            result = bounded_fetch_page("https://www.instagram.com/reel/Test123/", "fake-secret")
            self.assertEqual(result, expected)
            self.assertEqual(json.loads(worker.communicate.call_args.args[0])["key"], "fake-secret")
            self.assertNotIn("fake-secret", str(start.call_args))
            worker.kill.assert_not_called()

    def test_worker_failure_has_no_unfiltered_output(self):
        with patch("string_capture.subprocess.Popen") as start:
            worker = start.return_value
            worker.returncode = 1
            worker.communicate.return_value = ("private diagnostic", None)
            result = bounded_fetch_page("https://www.instagram.com/reel/Test123/", "fake-secret")
        self.assertEqual(result, {"status_code":0,"error":"provider_worker_failed"})

    def test_real_stalled_worker_is_terminated_offline(self):
        real_popen = subprocess.Popen
        children = []
        def sleeping_worker(*args, **kwargs):
            child = real_popen([sys.executable, "-c", "import time; time.sleep(30)"], **kwargs)
            children.append(child)
            return child
        with patch("string_capture.subprocess.Popen", side_effect=sleeping_worker), patch("string_capture.PROVIDER_DEADLINE_SECONDS", 0.1):
            result = bounded_fetch_page("https://www.instagram.com/reel/Test123/", "fake-secret")
        self.assertEqual(result["error"], "provider_deadline_exceeded")
        self.assertIsNotNone(children[0].poll())
        self.assertNotEqual(children[0].returncode, 0)


if __name__ == "__main__":
    unittest.main()
