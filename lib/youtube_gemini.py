"""Transcribe a public YouTube video through Gemini's YouTube link input.

For machines where YouTube refuses yt-dlp (cloud servers answer "Sign in to confirm
you're not a bot"). Gemini reads the public link itself; this sends only the link and
a transcription request. It never downloads from YouTube, never uses cookies, and
never switches player clients or proxies.

Models are tried in order. A busy model (HTTP 503/429/500) or a stream that ends
without finishReason STOP is retried once after a pause, then the next model is
tried. The result is a model transcription, not the creator's captions.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

from local_config import downloads_dir

API = "https://generativelanguage.googleapis.com/v1beta/models/{model}:streamGenerateContent?alt=sse"
# Free-tier order, tested 2026-10-31: Flash-Lite answers fastest; 3.5 and 3.6 Flash
# answered while 3.8 and 3.7 Flash returned 503 "high demand" for hours.
DEFAULT_MODELS = ("gemini-3.5-flash-lite", "gemini-3.5-flash", "gemini-3.6-flash", "gemini-3.8-flash")
DEFAULT_KEY_ENV = "GEMINI_PERSONAL_API_KEY"
PROMPT = "Transcribe the speech"
RETRY_STATUS = {429, 500, 503}
DEADLINE_SECONDS = 300
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


def stream_once(vid: str, model: str, key: str, *, opener=None) -> dict:
    """One streaming request. Returns status, text, finishReason and usage; never the key."""
    opener = opener or urllib.request.urlopen
    body = {"contents": [{"parts": [
        {"file_data": {"file_uri": f"https://www.youtube.com/watch?v={vid}"}},
        {"text": PROMPT}]}],
        "generationConfig": {"mediaResolution": "MEDIA_RESOLUTION_LOW"}}
    req = urllib.request.Request(API.format(model=model), data=json.dumps(body).encode(),
                                 method="POST", headers={"Content-Type": "application/json",
                                                         "x-goog-api-key": key})
    started = time.time()
    text, finish, usage = [], None, None
    try:
        with opener(req, timeout=DEADLINE_SECONDS) as response:
            for raw in response:
                line = raw.decode("utf-8", "replace").strip()
                if not line.startswith("data:"):
                    continue
                event = json.loads(line[5:])
                for candidate in event.get("candidates", []):
                    finish = candidate.get("finishReason", finish)
                    for part in candidate.get("content", {}).get("parts", []):
                        if "text" in part:
                            text.append(part["text"])
                usage = event.get("usageMetadata", usage)
                if time.time() - started > DEADLINE_SECONDS:
                    return {"status": 200, "text": "".join(text), "finish": finish,
                            "usage": usage, "error": "deadline"}
    except urllib.error.HTTPError as exc:
        try:
            detail = exc.read(300).decode("utf-8", "replace")
        except OSError:
            detail = ""
        finally:
            exc.close()
        return {"status": exc.code, "error": detail}
    except (OSError, ValueError) as exc:
        return {"status": None, "error": type(exc).__name__}
    return {"status": 200, "text": "".join(text), "finish": finish, "usage": usage}


def transcribe(vid: str, key: str, models=DEFAULT_MODELS, *, pause_seconds: float = 20,
               opener=None, sleep=time.sleep) -> dict:
    tries = []
    for model in models:
        for attempt in (1, 2):
            result = stream_once(vid, model, key, opener=opener)
            complete = (result["status"] == 200 and result.get("finish") == "STOP"
                        and not result.get("error") and result.get("text", "").strip())
            tries.append({"model": model, "status": result["status"],
                          "finish": result.get("finish"), "error": result.get("error", "")[:200]
                          if result.get("error") else None})
            if complete:
                return {"ok": True, "model": model, "text": result["text"],
                        "usage": result.get("usage"), "tries": tries}
            retryable = result["status"] in RETRY_STATUS or result["status"] == 200 or result["status"] is None
            if not retryable:
                break
            if attempt == 1:
                sleep(pause_seconds)
    return {"ok": False, "tries": tries}


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
    result = transcribe(vid, key, models)
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
