"""Transcribe a public YouTube video through Gemini's YouTube link input.

For machines where YouTube refuses yt-dlp (cloud servers answer "Sign in to confirm
you're not a bot"). Gemini reads the public link itself; this sends only the link and
a transcription request. It never downloads from YouTube, never uses cookies, and
never switches player clients or proxies.

Models are tried in order. A busy model (HTTP 503/429/500) or a stream that ends
without finishReason STOP is retried once after a pause, then the next model is
tried. The result is a model transcription, not the creator's captions.
Exit 0 with a transcript, 3 when no model finished one, 2 for a bad link or missing
key, 4 when Google's answer shows billing on the key's project (stop using Gemini).
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
import urllib.request  # noqa: F401  (tests patch urlopen through this module)
from pathlib import Path

from gemini_api import API, DEADLINE_SECONDS, DEFAULT_KEY_ENV, DEFAULT_MODELS, RETRY_STATUS, BillingLinked, generate, stream  # noqa: F401
from local_config import downloads_dir

PROMPT = "Transcribe the speech"
_ID = re.compile(r"^[A-Za-z0-9_-]{11}$")


def video_id(url: str) -> str:
    """Accept watch, youtu.be, shorts and embed links, or a bare 11-character ID."""
    url = url.strip()
    if _ID.fullmatch(url):
        return url
    match = (re.search(r"[?&]v=([A-Za-z0-9_-]{11})", url)
             or re.search(r"youtu\.be/([A-Za-z0-9_-]{11})", url)
             or re.search(r"youtube\.com/(?:shorts|embed|live)/([A-Za-z0-9_-]{11})", url))
    if not match:
        raise ValueError("not a YouTube video link")
    return match[1]


def request_body(vid: str) -> dict:
    return {"contents": [{"parts": [
        {"file_data": {"file_uri": f"https://www.youtube.com/watch?v={vid}"}},
        {"text": PROMPT}]}],
        "generationConfig": {"mediaResolution": "MEDIA_RESOLUTION_LOW"}}


def stream_once(vid: str, model: str, key: str, *, opener=None) -> dict:
    """One streaming request. Returns status, text, finishReason and usage; never the key."""
    return stream(model, key, request_body(vid), opener=opener)


def transcribe(vid: str, key: str, models=DEFAULT_MODELS, *, pause_seconds: float = 20,
               opener=None, sleep=time.sleep) -> dict:
    return generate(request_body(vid), key, models, pause_seconds=pause_seconds,
                    opener=opener, sleep=sleep)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("url")
    parser.add_argument("--models", default=",".join(DEFAULT_MODELS),
                        help="Comma-separated, tried in order (default: %(default)s)")
    parser.add_argument("--key-env", default=DEFAULT_KEY_ENV,
                        help="Environment variable that holds the Gemini key (default: %(default)s)")
    parser.add_argument("--downloads", type=Path, default=downloads_dir() / "youtube")
    args = parser.parse_args(argv)
    try:
        vid = video_id(args.url)
    except ValueError as exc:
        print(json.dumps({"ok": False, "error": str(exc)}))
        return 2
    key = os.environ.get(args.key_env, "").strip()
    if not key:
        print(json.dumps({"ok": False, "error": f"{args.key_env} is not set"}))
        return 2
    models = tuple(m.strip() for m in args.models.split(",") if m.strip())
    try:
        result = transcribe(vid, key, models)
    except BillingLinked as exc:
        print(json.dumps({"ok": False, "id": vid, "error": f"billing on the key's project: {exc}"}))
        return 4
    if not result["ok"]:
        print(json.dumps({"ok": False, "id": vid, "tries": result["tries"]}, indent=1))
        return 3
    args.downloads.mkdir(parents=True, exist_ok=True)
    txt = args.downloads / f"{vid}.gemini.txt"
    txt.write_text(result["text"], encoding="utf-8")
    meta = {"ok": True, "id": vid, "model": result["model"], "usage": result["usage"],
            "tries": result["tries"], "transcript": str(txt.resolve()),
            "method": "Gemini YouTube link input, MEDIA_RESOLUTION_LOW; model transcription, "
                      "not the creator's captions"}
    (args.downloads / f"{vid}.gemini.json").write_text(json.dumps(meta, indent=1), encoding="utf-8")
    print(json.dumps(meta, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
