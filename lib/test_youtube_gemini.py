"""Offline checks for Gemini YouTube transcription: fallbacks, completeness, no key leaks."""
import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError

import youtube_gemini as yg


class Stream(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def sse(*events):
    return Stream(b"".join(b"data: " + json.dumps(e).encode() + b"\n\n" for e in events))


def chunk(text, finish=None):
    candidate = {"content": {"parts": [{"text": text}]}}
    if finish:
        candidate["finishReason"] = finish
    return {"candidates": [candidate], "usageMetadata": {"promptTokenCount": 100}}


def busy():
    return HTTPError("https://example.invalid", 503, "busy", {}, io.BytesIO(b"high demand"))


class YoutubeGeminiTests(unittest.TestCase):
    def test_video_ids_from_common_links(self):
        for url in ("https://www.youtube.com/watch?v=Oe-1A-bG1S0&t=3",
                    "https://youtu.be/Oe-1A-bG1S0?si=x",
                    "https://www.youtube.com/shorts/Oe-1A-bG1S0",
                    "Oe-1A-bG1S0"):
            self.assertEqual(yg.video_id(url), "Oe-1A-bG1S0")
        with self.assertRaises(ValueError):
            yg.video_id("https://www.instagram.com/reel/Abc/")

    def test_busy_first_model_is_retried_once_then_the_next_model_answers(self):
        replies = [busy(), busy(), sse(chunk("Hello "), chunk("world.", "STOP"))]
        calls = []

        def opener(req, timeout):
            calls.append(req.full_url)
            reply = replies.pop(0)
            if isinstance(reply, Exception):
                raise reply
            return reply

        result = yg.transcribe("Oe-1A-bG1S0", "secret-key", ("lite", "flash"),
                               opener=opener, sleep=lambda s: None)
        self.assertTrue(result["ok"])
        self.assertEqual(result["model"], "flash")
        self.assertEqual(result["text"], "Hello world.")
        self.assertEqual([t["status"] for t in result["tries"]], [503, 503, 200])
        self.assertIn("/lite:", calls[0])
        self.assertNotIn("secret-key", json.dumps(result))

    def test_a_stream_that_ends_without_stop_is_not_accepted(self):
        replies = [sse(chunk("cut off mid")), sse(chunk("whole thing.", "STOP"))]
        result = yg.transcribe("Oe-1A-bG1S0", "k", ("lite",),
                               opener=lambda req, timeout: replies.pop(0), sleep=lambda s: None)
        self.assertTrue(result["ok"])
        self.assertEqual(result["text"], "whole thing.")
        self.assertEqual(result["tries"][0]["finish"], None)

    def test_a_refused_video_skips_to_the_next_model_without_waiting(self):
        waits = []

        def opener(req, timeout):
            raise HTTPError("https://example.invalid", 400, "bad", {}, io.BytesIO(b"private video"))

        result = yg.transcribe("Oe-1A-bG1S0", "k", ("lite", "flash"), opener=opener,
                               sleep=waits.append)
        self.assertFalse(result["ok"])
        self.assertEqual(len(result["tries"]), 2)
        self.assertEqual(waits, [])

    def test_command_writes_transcript_and_metadata_and_needs_the_key(self):
        with tempfile.TemporaryDirectory() as tmp, patch.dict("os.environ", {}, clear=True):
            out = io.StringIO()
            with redirect_stdout(out):
                self.assertEqual(yg.main(["https://youtu.be/Oe-1A-bG1S0", "--downloads", tmp]), 2)
            with patch.dict("os.environ", {"GEMINI_PERSONAL_API_KEY": "secret-key"}), \
                    patch("youtube_gemini.urllib.request.urlopen",
                          return_value=sse(chunk("Text.", "STOP"))), redirect_stdout(out):
                self.assertEqual(yg.main(["https://youtu.be/Oe-1A-bG1S0", "--downloads", tmp]), 0)
            self.assertEqual((Path(tmp) / "Oe-1A-bG1S0.gemini.txt").read_text(), "Text.")
            meta = json.loads((Path(tmp) / "Oe-1A-bG1S0.gemini.json").read_text())
            self.assertEqual(meta["model"], "gemini-3.5-flash-lite")
            self.assertNotIn("secret-key", out.getvalue())


if __name__ == "__main__":
    unittest.main()
