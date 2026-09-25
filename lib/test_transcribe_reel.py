"""Reels use one extraction invocation, including when Instagram refuses media."""
from __future__ import annotations

from contextlib import ExitStack, redirect_stderr, redirect_stdout
import io
import json
from pathlib import Path
import subprocess
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import transcribe_reel as reel


class ReelTests(unittest.TestCase):
    def run_case(self, url="https://www.instagram.com/reel/AbC/", *,
                 failure=False, stale_video=False, no_video=False,
                 empty_video=False, caption_sidecar=True, malformed_info=False,
                 has_audio=True):
        with tempfile.TemporaryDirectory() as tmp, ExitStack() as stack:
            root = Path(tmp) / "downloads with spaces"
            root.mkdir()
            cookies = Path(tmp) / "ig-cookies.txt"
            original = b"# Netscape HTTP Cookie File\n# fixture, no real credentials\n"
            cookies.write_bytes(original)
            copied = root / "copied-transcript.txt"
            if stale_video:
                (root / "AbC.mp4").write_bytes(b"stale video from another run")
            jars = []

            def download(cmd, **kwargs):
                jar = Path(cmd[cmd.index("--cookies") + 1])
                jars.append(jar)
                self.assertNotEqual(jar, cookies)
                self.assertEqual(jar.read_bytes(), original)
                jar.write_bytes(b"# downloader write-back must stay temporary\n")
                self.assertIn("--write-description", cmd)
                self.assertIn("--write-info-json", cmd)
                self.assertIn("--write-thumbnail", cmd)
                self.assertEqual(cmd[cmd.index("--print") + 1], "after_move:filepath")
                if failure:
                    return subprocess.CompletedProcess(cmd, 1, stdout="", stderr=None)
                template = cmd[cmd.index("-o") + 1].replace("%(id)s", "Resolved123")
                video = Path(template.replace("%(ext)s", "mp4"))
                if not no_video:
                    video.write_bytes(b"" if empty_video else b"video")
                if caption_sidecar:
                    video.with_suffix(".description").write_text("saved caption — music", encoding="utf-8")
                video.with_suffix(".info.json").write_text(
                    "{broken" if malformed_info else json.dumps({"description": "caption from metadata"}),
                    encoding="utf-8",
                )
                video.with_suffix(".jpg").write_bytes(b"thumbnail")
                return subprocess.CompletedProcess(cmd, 0, stdout=str(video) + "\n", stderr=None)

            def extract_audio(video, audio):
                audio.write_bytes(b"audio")
                return True

            def whisper(audio, download_dir, model):
                audio.with_suffix(".txt").write_text("saved transcript\n", encoding="utf-8")

            def frames(video, frames_dir, interval):
                frames_dir.mkdir(parents=True)
                return SimpleNamespace(count=3)

            stack.enter_context(patch.object(reel, "downloads_dir", return_value=root))
            stack.enter_context(patch.object(reel, "require_ig_cookies", return_value=cookies))
            stack.enter_context(patch.object(reel, "require_cmd", return_value="ffmpeg"))
            stack.enter_context(patch.object(reel, "venv_python", return_value=cookies))
            stack.enter_context(patch.object(reel, "warn_ollama"))
            stack.enter_context(patch("tooling.require_cmd", return_value="yt-dlp"))
            fetch = stack.enter_context(patch("tooling.subprocess.run", side_effect=download))
            stack.enter_context(patch.object(reel, "has_audio_stream", return_value=has_audio))
            audio = stack.enter_context(patch.object(reel, "extract_audio_aac", side_effect=extract_audio))
            frame = stack.enter_context(patch.object(reel, "frame_extract", side_effect=frames))
            ocr = stack.enter_context(patch.object(reel, "ocr_to_file"))
            transcribe = stack.enter_context(patch.object(reel, "whisper_transcribe", side_effect=whisper))
            stdout, stderr = io.StringIO(), io.StringIO()
            with redirect_stdout(stdout), redirect_stderr(stderr):
                code = reel.main([url, "--model", "medium", "--frame-interval", "2", "-o", str(copied)])

            self.assertEqual(fetch.call_count, 1)
            self.assertEqual(cookies.read_bytes(), original)
            self.assertTrue(jars and all(not jar.exists() for jar in jars))
            if code:
                frame.assert_not_called()
                transcribe.assert_not_called()
                self.assertFalse(list(root.glob("*.description.txt")))
            else:
                mid = "Resolved123" if "/share/" in url else "AbC"
                self.assertIn(f"Reel ID:       {mid}", stderr.getvalue())
                self.assertIn(f"Description:   {root / (mid + '.description.txt')}", stderr.getvalue())
                self.assertIn(f"Thumbnail:     {root / (mid + '.jpg')}", stderr.getvalue())
                self.assertEqual(frame.call_args.args, (root / f"{mid}.mp4", root / mid / "frames", "2"))
                ocr.assert_called_once_with(root / mid / "frames", out=root / f"{mid}.ocr.txt")
                if has_audio:
                    audio.assert_called_once_with(root / f"{mid}.mp4", root / f"{mid}.m4a")
                    transcribe.assert_called_once_with(root / f"{mid}.m4a", root, "medium")
                    self.assertEqual(copied.read_text(), "saved transcript\n")
                    self.assertEqual(stdout.getvalue(), "saved transcript\n")
                else:
                    audio.assert_not_called()
                    transcribe.assert_not_called()
                    self.assertFalse(copied.exists())
                caption = (root / f"{mid}.description.txt").read_text()
                expected = "saved caption — music" if caption_sidecar else (
                    "" if malformed_info else "caption from metadata")
                self.assertEqual(caption, expected)
            return code

    def test_known_url_variants_keep_paths_and_pipeline(self):
        for path in ("reel", "reels", "p", "tv"):
            with self.subTest(path=path):
                self.assertEqual(self.run_case(f"https://www.instagram.com/{path}/AbC/?igsh=example"), 0)

    def test_share_url_resolves_in_the_same_download(self):
        self.assertEqual(self.run_case("https://www.instagram.com/share/example/"), 0)

    def test_empty_media_does_not_probe_or_retry(self):
        self.assertEqual(self.run_case(failure=True), 1)

    def test_failed_download_does_not_use_stale_video_as_success(self):
        self.assertEqual(self.run_case(failure=True, stale_video=True), 1)

    def test_metadata_without_video_is_failure(self):
        self.assertEqual(self.run_case(no_video=True), 1)

    def test_empty_video_is_failure(self):
        self.assertEqual(self.run_case(empty_video=True), 1)

    def test_caption_can_come_from_downloaded_info_json(self):
        self.assertEqual(self.run_case(caption_sidecar=False), 0)

    def test_bad_metadata_never_triggers_a_second_network_fetch(self):
        self.assertEqual(self.run_case(caption_sidecar=False, malformed_info=True), 0)

    def test_silent_video_retains_frames_without_whisper(self):
        self.assertEqual(self.run_case(has_audio=False), 0)


if __name__ == "__main__":
    unittest.main()
