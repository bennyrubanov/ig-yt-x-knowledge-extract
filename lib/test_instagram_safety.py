"""Account warnings block requests; limits survive separate invocations."""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import instagram_safety as safety
from tooling import ytdlp


class InstagramSafetyTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        state = Path(self.temp.name) / "state.json"
        env = patch.dict(os.environ, {"IGX_INSTAGRAM_SAFETY_STATE": str(state)})
        env.start()
        self.addCleanup(env.stop)

    def test_hold_survives_process_and_blocks_before_subprocess(self) -> None:
        safety.hold("account warning")
        with patch("tooling.subprocess.run") as run:
            with self.assertRaises(safety.InstagramSafetyHold):
                ytdlp(["https://www.instagram.com/reel/abc/"])
            run.assert_not_called()
        child_env = os.environ.copy()
        child_env["PYTHONPATH"] = str(Path(__file__).resolve().parent)
        child = subprocess.run(
            [sys.executable, "-c", "import instagram_safety; print(instagram_safety.status()['paused'])"],
            capture_output=True, text=True, check=True, env=child_env,
        )
        self.assertEqual(child.stdout.strip(), "True")

    def test_failure_latches_hold_but_other_sources_are_unaffected(self) -> None:
        failed = subprocess.CompletedProcess(["yt-dlp"], 1, "", "unavailable")
        ok = subprocess.CompletedProcess(["yt-dlp"], 0, "", "")
        with patch("tooling.require_cmd", return_value="yt-dlp"), patch("tooling.subprocess.run", side_effect=[failed, ok]) as run:
            result = ytdlp(["https://www.instagram.com/reel/abc/"], capture=True)
            self.assertEqual(result.returncode, 1)
            self.assertTrue(safety.status()["paused"])
            with self.assertRaises(safety.InstagramSafetyHold):
                ytdlp(["https://www.instagram.com/reel/def/"], capture=True)
            self.assertEqual(ytdlp(["https://www.youtube.com/watch?v=abc"], capture=True).returncode, 0)
            self.assertEqual(run.call_count, 2)

    def test_budget_persists_and_resume_does_not_erase_it(self) -> None:
        ok = subprocess.CompletedProcess(["yt-dlp"], 0, "", "")
        with patch.object(safety, "MAX_REQUESTS_24H", 2), patch.object(safety, "MIN_GAP_SECONDS", 0):
            with patch("tooling.require_cmd", return_value="yt-dlp"), patch("tooling.subprocess.run", return_value=ok) as run:
                for _ in range(2):
                    ytdlp(["https://www.instagram.com/reel/abc/"], capture=True)
                with self.assertRaisesRegex(safety.InstagramSafetyHold, "budget reached"):
                    ytdlp(["https://www.instagram.com/reel/abc/"], capture=True)
                safety.hold("warning")
                with self.assertRaises(safety.InstagramSafetyHold):
                    safety.resume(confirmed_clear=False)
                safety.resume(confirmed_clear=True)
                with self.assertRaisesRegex(safety.InstagramSafetyHold, "budget reached"):
                    ytdlp(["https://www.instagram.com/reel/abc/"], capture=True)
                self.assertEqual(run.call_count, 2)
        state = json.loads(safety.state_path().read_text())
        self.assertEqual(len(state["requests"]), 2)


if __name__ == "__main__":
    unittest.main()
