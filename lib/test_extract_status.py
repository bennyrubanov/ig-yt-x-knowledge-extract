#!/usr/bin/env python3
"""Unit tests for extract_status — last jsonl fail is not a missing note."""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from extract_status import (
    append_recovered,
    artifacts,
    classify_url,
    latest_by_media,
    load_jsonl,
    media_id_from_url,
    scoreboard,
    usable,
)


class ClassifyTests(unittest.TestCase):
    def test_urls(self) -> None:
        self.assertEqual(classify_url("https://www.instagram.com/p/AbC/"), "carousel")
        self.assertEqual(classify_url("https://www.instagram.com/reel/AbC/"), "reel")
        self.assertEqual(classify_url("https://youtu.be/dQw4w9wgGcI"), "youtube")
        self.assertEqual(classify_url("https://x.com/u/status/123"), "twitter")
        self.assertEqual(media_id_from_url("https://www.instagram.com/p/Da5k5eGEghT/"), "Da5k5eGEghT")
        self.assertEqual(media_id_from_url("https://x.com/u/status/2080603050327097694"), "2080603050327097694")

    def test_youtube_id_variants(self) -> None:
        for url in (
            "https://www.youtube.com/watch?v=dQw4w9wgGcI&t=20",
            "https://youtu.be/dQw4w9wgGcI?si=share",
            "https://youtube.com/shorts/dQw4w9wgGcI?si=share",
            "https://www.youtube.com/live/dQw4w9wgGcI",
            "https://www.youtube.com/embed/dQw4w9wgGcI",
        ):
            with self.subTest(url=url):
                self.assertEqual(media_id_from_url(url), "dQw4w9wgGcI")
        self.assertEqual(media_id_from_url("https://youtube.com/shorts/"), "")


class ScoreboardTests(unittest.TestCase):
    def test_stale_fail_is_recovered_when_slides_exist(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            dl = root / "downloads"
            slides = dl / "Da5k5eGEghT" / "slides"
            slides.mkdir(parents=True)
            (slides / "slide_01.jpg").write_bytes(b"x")
            vault = root / "vault"
            vault.mkdir()
            (vault / "instagram").mkdir()
            (vault / "instagram" / "Da5k5eGEghT-github-repos.md").write_text("# n\n")
            jsonl = root / "extract.jsonl"
            jsonl.write_text(
                json.dumps(
                    {
                        "status": "fail",
                        "kind": "carousel",
                        "media_id": "Da5k5eGEghT",
                        "url": "https://www.instagram.com/p/Da5k5eGEghT/",
                    }
                )
                + "\n"
            )
            board = scoreboard(load_jsonl(jsonl), dl, vault)
            self.assertEqual(board["log_last"].get("fail"), 1)
            self.assertEqual(board["now"].get("recovered"), 1)
            self.assertEqual(board["still_fail"], [])
            self.assertTrue(board["recovered"][0]["noted"])
            n = append_recovered(jsonl, board["recovered"])
            self.assertEqual(n, 1)
            last = latest_by_media(load_jsonl(jsonl))
            self.assertEqual(last["Da5k5eGEghT"]["status"], "recovered")
            board2 = scoreboard(load_jsonl(jsonl), dl, vault)
            self.assertEqual(board2["now"].get("recovered"), 1)
            self.assertFalse(board2["still_fail"])

    def test_reel_usable_from_frames_without_transcript(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            dl = Path(tmp)
            frames = dl / "DYcu-OZArsi" / "frames"
            frames.mkdir(parents=True)
            (frames / "frame_001.jpg").write_bytes(b"x")
            (dl / "DYcu-OZArsi.mp4").write_bytes(b"x")
            got = artifacts("reel", "DYcu-OZArsi", dl)
            self.assertTrue(usable("reel", got))
            self.assertEqual(got["frames"], 1)

    def test_true_fail_stays_fail(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            dl = root / "downloads"
            dl.mkdir()
            jsonl = root / "extract.jsonl"
            jsonl.write_text(
                json.dumps({"status": "fail", "kind": "reel", "media_id": "MISSINGID"}) + "\n"
            )
            board = scoreboard(load_jsonl(jsonl), dl, root / "novault")
            self.assertEqual(len(board["still_fail"]), 1)
            self.assertEqual(board["still_fail"][0]["media_id"], "MISSINGID")


if __name__ == "__main__":
    unittest.main()
