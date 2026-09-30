"""Offline fixtures for strict String media parsing and anonymous asset checks."""
from __future__ import annotations

from io import BytesIO
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import Mock, patch
from urllib.error import URLError

import string_media as media


CODE = "Day24Bnu4V5"
VIDEO = "https://scontent.cdninstagram.com/video.mp4?token=private-signature"
IMAGE = "https://scontent.fbcdn.net/cover.jpg?token=private-signature"


def embedded(value):
    return '<html><script type="application/json">' + json.dumps(value) + "</script></html>"


class SourceTests(unittest.TestCase):
    def test_canonical_source_strict(self):
        self.assertEqual(media.canonical_source("https://instagram.com/reels/Day24Bnu4V5/?igsh=x"),
                         ("https://www.instagram.com/reel/Day24Bnu4V5/", CODE))
        self.assertEqual(media.canonical_source("https://www.instagram.com/p/AbC_2/"),
                         ("https://www.instagram.com/p/AbC_2/", "AbC_2"))
        for url in ("http://instagram.com/reel/AbC/", "https://instagram.com.evil/reel/AbC/",
                    "https://user@instagram.com/reel/AbC/", "https://instagram.com:444/reel/AbC/",
                    "https://instagram.com/tv/AbC/", "https://instagram.com/reel/AbC/extra/",
                    "https://instagram.com/reel/AbC%2Fbad/", "https://instagram.com\\@evil/reel/AbC/",
                    "https://instagram.com:bad/reel/AbC/"):
            with self.subTest(url=url), self.assertRaises(media.MediaError):
                media.canonical_source(url)

    def test_exact_match_ignores_recommendations_and_picks_best_rendition(self):
        source = {"code": CODE, "caption": {"text": "Useful lesson"}, "has_audio": True,
                  "video_duration": 111.83,
                  "video_versions": [{"url": VIDEO + "low", "width": 320, "height": 480},
                                     {"url": VIDEO, "width": 1080, "height": 1920}],
                  "recommendations": [{"code": "Other", "video_url": "https://bad.invalid/x.mp4"}]}
        result = media.extract_media(embedded({"data": {"media": source}}), CODE)
        self.assertEqual(result["caption"], "Useful lesson")
        self.assertEqual(result["media"], [{"kind": "video", "url": VIDEO,
                                             "expected_duration": 111.83, "has_audio": True}])
        self.assertEqual(result["expected_duration"], 111.83)

    def test_carousel_and_graphql_sidecar(self):
        result = media.extract_media({"shortcode": CODE, "edge_sidecar_to_children": {
            "edges": [{"node": {"is_video": True, "video_url": VIDEO}},
                      {"node": {"display_url": IMAGE}}]}}, CODE)
        self.assertEqual(result["media"], [{"kind": "video", "url": VIDEO},
                                             {"kind": "image", "url": IMAGE}])
        result = media.extract_media({"code": CODE, "carousel_media": [
            {"image_versions2": {"candidates": [{"url": IMAGE, "width": 500, "height": 500}]}}]}, CODE)
        self.assertEqual(result["media"], [{"kind": "image", "url": IMAGE}])

    def test_missing_private_and_ambiguous_rejected(self):
        cases = [
            {"items": [{"code": "Another", "video_url": VIDEO}]},
            {"code": CODE},
            {"code": CODE, "is_private": True, "video_url": VIDEO},
            {"code": CODE, "user": {"is_private": True}, "video_url": VIDEO},
            {"code": CODE, "owner": {"is_private": True}, "video_url": VIDEO},
            {"items": [{"code": CODE, "video_url": VIDEO},
                       {"shortcode": CODE, "video_url": VIDEO + "different"}]},
        ]
        for obj in cases:
            with self.subTest(obj=obj), self.assertRaises(media.MediaError):
                media.extract_media(obj, CODE)
        with self.assertRaises(media.MediaError):
            media.extract_media("<title>Login</title>", CODE)
        public = media.extract_media({"data": [{"code": CODE, "video_url": VIDEO},
                                               {"code": "Other", "user": {"is_private": True}}]}, CODE)
        self.assertEqual(len(public["media"]), 1)

    def test_music_metadata_preserves_provenance(self):
        source = {"code": CODE, "video_url": VIDEO, "clips_metadata": {
            "music_info": {"music_asset_info": {"title": "Song", "artist_name": "Artist",
                                                 "audio_asset_id": 123}}}}
        self.assertEqual(media.extract_media(source, CODE)["audio"],
                         {"title": "Song", "artist_name": "Artist", "audio_asset_id": 123,
                          "provenance": "clips_metadata.music_info"})

    def test_external_music_reference_only_for_image_source(self):
        metadata = {"music_info": {"music_asset_info": {"title": "Track",
                                                  "progressive_download_url": VIDEO}}}
        video = media.extract_media({"code": CODE, "video_url": VIDEO,
                                     "clips_metadata": metadata}, CODE)
        self.assertEqual(len(video["media"]), 1)
        image = media.extract_media({"code": CODE, "display_url": IMAGE,
                                     "clips_metadata": metadata}, CODE)
        self.assertEqual(image["media"][1], {"kind": "audio", "role": "music_reference",
                                               "url": VIDEO})

    def test_carousel_music_field_and_display_artist(self):
        source = {"code": CODE, "carousel_media": [{"display_url": IMAGE}],
                  "music_metadata": {"music_info": {"music_asset_info": {
                      "title": "Carousel song", "display_artist": "Artist",
                      "audio_asset_id": "123", "progressive_download_url": VIDEO}}}}
        parsed = media.extract_media(embedded(source), CODE)
        self.assertEqual(parsed["audio"]["artist_name"], "Artist")
        self.assertEqual(parsed["audio"]["provenance"], "music_metadata.music_info")
        self.assertEqual(parsed["media"][-1]["role"], "music_reference")
        source["music_metadata"] = None
        source["recommendations"] = [{"code": "Other", "music_metadata": {
            "music_info": {"music_asset_info": {"title": "Unrelated", "audio_url": VIDEO}}}}]
        self.assertNotIn("audio", media.extract_media(source, CODE))


class FakeResponse:
    def __init__(self, body=b"video", content_type="video/mp4", length=None, final_url=VIDEO, status=200):
        self.io = BytesIO(body)
        self.headers = {"Content-Type": content_type}
        if length is not None:
            self.headers["Content-Length"] = str(length)
        self.final_url = final_url
        self.status = status

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.io.close()

    def read(self, count):
        return self.io.read(count)

    def geturl(self):
        return self.final_url


class DownloadTests(unittest.TestCase):
    def test_rejects_trick_hosts_before_network(self):
        with patch.object(media, "build_opener") as opener, tempfile.TemporaryDirectory() as tmp:
            for url in ("http://scontent.fbcdn.net/a.mp4", "https://fbcdn.net/a.mp4",
                        "https://scontent.fbcdn.net.evil/a.mp4", "https://user@scontent.fbcdn.net/a.mp4",
                        "https://scontent.fbcdn.net:444/a.mp4", "https://scontent.fbcdn.net:bad/a.mp4",
                        "https://127.0.0.1/a.mp4"):
                with self.subTest(url=url), self.assertRaises(media.MediaError):
                    media.download_asset(url, Path(tmp) / "a.mp4")
            opener.assert_not_called()

    def test_download_anonymous_atomic_and_capped(self):
        with tempfile.TemporaryDirectory() as tmp:
            dest = Path(tmp) / "a.mp4"
            fake_opener = Mock()
            fake_opener.open.return_value = FakeResponse(body=b"video", length=5)
            with patch.object(media, "build_opener", return_value=fake_opener) as build:
                self.assertEqual(media.download_asset(VIDEO, dest), dest)
            self.assertEqual(dest.read_bytes(), b"video")
            self.assertFalse(list(Path(tmp).glob("*.part")))
            request = fake_opener.open.call_args.args[0]
            self.assertNotIn("Cookie", request.headers)
            self.assertNotIn("Authorization", request.headers)
            self.assertEqual(fake_opener.open.call_args.kwargs["timeout"], 30)
            self.assertIsInstance(build.call_args.args[0], media.ProxyHandler)
            self.assertEqual(build.call_args.args[0].proxies, {})

    def test_audio_mp4_container_type_is_allowed_but_html_is_not(self):
        with tempfile.TemporaryDirectory() as tmp:
            dest = Path(tmp) / "reference.m4a"
            opener = Mock()
            opener.open.return_value = FakeResponse(content_type="video/mp4")
            with patch.object(media, "build_opener", return_value=opener):
                media.download_asset(VIDEO, dest)
            opener.open.return_value = FakeResponse(content_type="text/html")
            with patch.object(media, "build_opener", return_value=opener):
                with self.assertRaises(media.MediaError):
                    media.download_asset(VIDEO, dest)

    def test_truncated_oversized_type_redirect_and_errors_are_sanitized(self):
        variants = [FakeResponse(body=b"short", length=99),
                    FakeResponse(body=b"longer", length=6),
                    FakeResponse(content_type="text/html"),
                    FakeResponse(final_url="https://evil.invalid/secret"),
                    FakeResponse(status=403)]
        with tempfile.TemporaryDirectory() as tmp:
            dest = Path(tmp) / "a.mp4"
            for fake in variants:
                opener = Mock()
                opener.open.return_value = fake
                with self.subTest(fake=fake), patch.object(media, "build_opener", return_value=opener):
                    with self.assertRaises(media.MediaError) as caught:
                        media.download_asset(VIDEO, dest, max_bytes=5)
                    self.assertNotIn("private-signature", str(caught.exception))
                    self.assertFalse(dest.exists())
                    self.assertFalse(list(Path(tmp).glob("*.part")))
            with self.assertRaises(media.MediaError):
                media._NoRedirect().redirect_request(None, None, 302, "Found", {}, "https://evil.invalid/")
            opener = Mock()
            opener.open.side_effect = URLError(VIDEO)
            with patch.object(media, "build_opener", return_value=opener):
                with self.assertRaises(media.MediaError) as caught:
                    media.download_asset(VIDEO, dest)
            self.assertNotIn("private-signature", str(caught.exception))


class ValidationTests(unittest.TestCase):
    def test_probe_streams_and_duration(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "v.mp4"
            path.write_bytes(b"not real; ffprobe is mocked")
            probe = subprocess.CompletedProcess([], 0, stdout=json.dumps({
                "format": {"duration": "111.8"}, "streams": [{"codec_type": "video"},
                                                               {"codec_type": "audio"}]}))
            with patch.object(media.subprocess, "run", return_value=probe) as run:
                result = media.validate_media(path, expected_duration=111.83, require_audio=True,
                                              full_decode=True)
            self.assertTrue(result["has_audio"])
            self.assertEqual(run.call_count, 3)
            self.assertEqual(run.call_args.args[0][0], "ffmpeg")
            for call in run.call_args_list:
                self.assertIn("-protocol_whitelist", call.args[0])
                self.assertIn("file,pipe", call.args[0])

    def test_still_image_and_direct_audio_are_valid(self):
        with tempfile.TemporaryDirectory() as tmp:
            image = Path(tmp) / "slide.jpg"
            image.write_bytes(b"image bytes")
            probe = subprocess.CompletedProcess([], 0, stdout=json.dumps({
                "format": {"duration": "N/A"}, "streams": [{"codec_type": "video",
                                                               "width": 800, "height": 600}]}))
            with patch.object(media.subprocess, "run", return_value=probe):
                result = media.validate_media(image)
            self.assertTrue(result["has_image"])
            self.assertFalse(result["has_video"])
            self.assertEqual(result["duration"], 0)
            audio = Path(tmp) / "track.m4a"
            audio.write_bytes(b"audio bytes")
            probe = subprocess.CompletedProcess([], 0, stdout=json.dumps({
                "format": {"duration": "12"}, "streams": [{"codec_type": "audio"}]}))
            with patch.object(media.subprocess, "run", return_value=probe):
                result = media.validate_media(audio, require_audio=True, check_silence=False)
            self.assertTrue(result["has_audio"])
            self.assertFalse(result["has_video"])

    def test_audio_extension_requires_audio_even_without_caller_flag(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "reference.m4a"
            path.write_bytes(b"video bytes disguised as audio")
            probe = subprocess.CompletedProcess([], 0, stdout=json.dumps({
                "format": {"duration": "12"}, "streams": [{"codec_type": "video"}]}))
            with patch.object(media.subprocess, "run", return_value=probe):
                with self.assertRaisesRegex(media.MediaError, "Audio stream missing"):
                    media.validate_media(path)

    def test_missing_audio_or_truncated_duration(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "v.mp4"
            path.write_bytes(b"x")
            probe = subprocess.CompletedProcess([], 0, stdout=json.dumps({
                "format": {"duration": "12"}, "streams": [{"codec_type": "video"}]}))
            with patch.object(media.subprocess, "run", return_value=probe):
                with self.assertRaises(media.MediaError):
                    media.validate_media(path, require_audio=True)
                with self.assertRaises(media.MediaError):
                    media.validate_media(path, expected_duration=100)

    def test_wholly_silent_short_audio_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "v.mp4"
            path.write_bytes(b"x")
            probe = subprocess.CompletedProcess([], 0, stdout=json.dumps({
                "format": {"duration": "8"}, "streams": [{"codec_type": "video"},
                                                             {"codec_type": "audio"}]}))
            silence = subprocess.CompletedProcess([], 0, stderr="max_volume: -inf dB")
            with patch.object(media.subprocess, "run", side_effect=[probe, silence]):
                with self.assertRaisesRegex(media.MediaError, "silent"):
                    media.validate_media(path, require_audio=True)


if __name__ == "__main__":
    unittest.main()
