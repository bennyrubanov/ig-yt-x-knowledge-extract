#!/usr/bin/env python3
"""yt-dlp must never write back onto the live cookie jar."""
from __future__ import annotations

import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tooling import scratch_cookie_jar, ytdlp

NETSCAPE = "# Netscape HTTP Cookie File\n.instagram.com\tTRUE\t/\tTRUE\t0\tsessionid\tplaceholder\n"


class ScratchCookieJarTests(unittest.TestCase):
    def test_copy_is_what_ytdlp_sees_live_file_unchanged(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            live = Path(tmp) / "ig-cookies.txt"
            live.write_text(NETSCAPE, encoding="utf-8")
            original = live.read_bytes()
            seen: list[str] = []

            def fake_run(cmd, **kwargs):  # noqa: ANN001
                i = cmd.index("--cookies")
                jar = Path(cmd[i + 1])
                seen.append(str(jar))
                self.assertNotEqual(jar.resolve(), live.resolve())
                self.assertTrue(jar.is_file())
                jar.write_text("# mutated by yt-dlp\n", encoding="utf-8")
                return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

            with patch("tooling.require_cmd", return_value="yt-dlp"):
                with patch("tooling.subprocess.run", side_effect=fake_run):
                    ytdlp(["--skip-download", "https://example.com/"], cookies=live)

            self.assertEqual(live.read_bytes(), original)
            self.assertEqual(len(seen), 1)
            self.assertFalse(Path(seen[0]).exists())

    def test_scratch_unlinks_even_if_ytdlp_raises(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            src = Path(tmp) / "ig-cookies.txt"
            src.write_text(NETSCAPE, encoding="utf-8")
            leaked: list[Path] = []
            with self.assertRaises(RuntimeError):
                with scratch_cookie_jar(src) as jar:
                    leaked.append(jar)
                    self.assertTrue(jar.is_file())
                    raise RuntimeError("boom")
            self.assertFalse(leaked[0].exists())
            self.assertEqual(src.read_text(encoding="utf-8"), NETSCAPE)


if __name__ == "__main__":
    unittest.main()
