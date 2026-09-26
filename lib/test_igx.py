#!/usr/bin/env python3
"""igx dispatcher and extract_queue use Python, not bash."""
from __future__ import annotations

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]


class IgxTests(unittest.TestCase):
    def test_help_lists_commands(self) -> None:
        proc = subprocess.run(
            [sys.executable, str(ROOT / "scripts" / "igx.py"), "--help"],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(proc.returncode, 0)
        combined = (proc.stdout or "") + (proc.stderr or "")
        for cmd in ("reel", "carousel", "youtube", "twitter", "batch", "reextract", "cleanup"):
            self.assertIn(cmd, combined)

    def test_unknown_command_exits_2(self) -> None:
        proc = subprocess.run(
            [sys.executable, str(ROOT / "scripts" / "igx.py"), "not-a-command"],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(proc.returncode, 2)


class ExtractQueueCmdTests(unittest.TestCase):
    def test_igx_cmd_is_this_python_not_bash(self) -> None:
        from extract_queue import igx_cmd

        cmd = igx_cmd("reel")
        self.assertEqual(cmd[0], sys.executable)
        self.assertTrue(cmd[1].endswith("igx.py") or cmd[1].endswith("igx.py"))
        self.assertEqual(cmd[2], "reel")
        self.assertFalse(any(part.endswith(".sh") for part in cmd))

    def test_tv_kind_uses_reel_command(self) -> None:
        from extract_queue import igx_cmd, normalize_kind

        self.assertEqual(normalize_kind("tv"), "reel")
        self.assertEqual(igx_cmd("tv")[2], "reel")

    def test_instagram_is_not_retried_in_run(self) -> None:
        from extract_queue import DEFAULT_IG_GAP_S, DEFAULT_WORKERS, download_attempts

        self.assertEqual(download_attempts("reel"), 1)
        self.assertEqual(download_attempts("carousel"), 1)
        self.assertEqual(download_attempts("tv"), 1)
        self.assertEqual(download_attempts("youtube"), 2)
        self.assertEqual(download_attempts("twitter"), 2)
        self.assertEqual(DEFAULT_WORKERS, 1)
        self.assertEqual(DEFAULT_IG_GAP_S, 0)  # safety floor is enforced centrally

    def test_batch_stops_remaining_instagram_after_first_failure(self) -> None:
        from extract_queue import main

        jobs = [
            {"kind": kind, "media_id": mid, "url": url, "extra": {}}
            for kind, mid, url in (
                ("reel", "one", "https://www.instagram.com/reel/one/"),
                ("reel", "two", "https://www.instagram.com/reel/two/"),
                ("youtube", "three", "https://www.youtube.com/watch?v=three"),
            )
        ]
        called: list[str] = []

        def fake_run_one(**kwargs):  # noqa: ANN003
            called.append(kwargs["mid"])
            return {"status": "fail" if kwargs["mid"] == "one" else "ok", "kind": kwargs["kind"], "media_id": kwargs["mid"]}

        with tempfile.TemporaryDirectory() as tmp:
            board = {"recovered": [], "still_fail": [], "log_last": {}, "now": {}}
            with patch("extract_queue.jobs_from_urls", return_value=jobs), patch("extract_queue.run_one", side_effect=fake_run_one), patch("extract_queue.already_done", return_value=False), patch("extract_queue.load_jsonl", return_value=[]), patch("extract_queue.scoreboard", return_value=board), patch("extract_queue.append_recovered", return_value=0), patch("extract_queue.format_report", return_value="report"):
                code = main(["ignored", "--jsonl", str(Path(tmp) / "audit.jsonl"), "--no-vault"])
        self.assertEqual(code, 1)
        self.assertEqual(called, ["one", "three"])


if __name__ == "__main__":
    unittest.main()
