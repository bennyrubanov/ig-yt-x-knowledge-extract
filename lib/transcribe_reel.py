"""Download an Instagram reel, frames, optional Whisper."""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from extract_status import media_id_from_url
from frame_extract import frame_extract
from local_config import downloads_dir, venv_python
from tooling import (
    extract_audio_aac,
    has_audio_stream,
    ocr_to_file,
    require_cmd,
    require_ig_cookies,
    warn_ollama,
    ytdlp,
)
from whisper_run import whisper_transcribe


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="igx reel",
        description="Download Instagram reel video, extract caption/frames, transcribe audio.",
    )
    p.add_argument("url")
    p.add_argument("-o", dest="output", help="Copy transcript to this path")
    p.add_argument("--model", default="small", choices=("small", "medium", "base"))
    p.add_argument(
        "--frame-interval",
        default=os.environ.get("IG_REEL_FRAME_INTERVAL", "1"),
        help="Seconds between frames, or auto|scene (default 1)",
    )
    return p


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    cookies = require_ig_cookies()
    require_cmd("ffmpeg")
    if not venv_python().is_file():
        print(f"Whisper venv Python not found at {venv_python()} — run setup first.", file=sys.stderr)
        return 1
    warn_ollama()

    download_dir = downloads_dir()
    download_dir.mkdir(parents=True, exist_ok=True)

    # Resolve direct reel/reels/p/tv URLs locally. For a share/redirect URL,
    # yt-dlp resolves the id during this same download, never via a prior probe.
    mid = media_id_from_url(args.url)
    dl = ytdlp(
        [
            "--write-description",
            "--write-info-json",
            "--write-thumbnail",
            "--convert-thumbnails",
            "jpg",
            "-o",
            str(download_dir / f"{mid or '%(id)s'}.%(ext)s"),
            "--print",
            "after_move:filepath",
            args.url,
        ],
        cookies=cookies,
        live_stderr=True,
    )
    video_s = (dl.stdout or "").strip().splitlines()
    video = Path(video_s[-1]) if video_s else download_dir / f"{mid}.mp4"
    if dl.returncode != 0 or not video.is_file() or not video.stat().st_size:
        print(
            "Download failed — stop this Instagram run; inspect the error and docs/auth.md. "
            "Do not retry the same URL or assume the browser needs another login.",
            file=sys.stderr,
        )
        return 1
    if not mid:
        mid = video.stem

    description = download_dir / f"{mid}.description.txt"
    frames_dir = download_dir / mid / "frames"
    caption_file = download_dir / f"{mid}.description"
    info_file = download_dir / f"{mid}.info.json"
    caption = ""
    if caption_file.is_file():
        caption = caption_file.read_text(encoding="utf-8", errors="replace")
    elif info_file.is_file():
        try:
            info = json.loads(info_file.read_text(encoding="utf-8"))
            if isinstance(info, dict) and isinstance(info.get("description"), str):
                caption = info["description"]
        except (OSError, UnicodeError, ValueError):
            print("[transcribe] metadata unreadable — continuing with saved media", file=sys.stderr)
    # Keep the wrapper/filing path stable; yt-dlp's sidecar has no .txt suffix.
    # A failed download above creates no empty caption that could look usable.
    description.write_text(caption, encoding="utf-8")

    thumbnail = ""
    for ext in (".jpg", ".webp", ".png"):
        cand = download_dir / f"{mid}{ext}"
        if cand.is_file():
            thumbnail = str(cand)
            break

    audio = download_dir / f"{mid}.m4a"
    has_audio = 0
    if has_audio_stream(video):
        if extract_audio_aac(video, audio):
            has_audio = 1
        else:
            print("[transcribe] audio extract failed — continuing (video/frames still usable)", file=sys.stderr)
    else:
        print("[transcribe] no audio stream — skip Whisper", file=sys.stderr)

    result = frame_extract(video, frames_dir, args.frame_interval)
    if frames_dir.is_dir():
        ocr_to_file(frames_dir, out=download_dir / f"{mid}.ocr.txt")

    txt = download_dir / f"{mid}.txt"
    if has_audio and audio.is_file():
        print(
            f"[transcribe] faster-whisper (ETA below; log: {download_dir / (mid + '.whisper.log')})...",
            file=sys.stderr,
        )
        whisper_transcribe(audio, download_dir, args.model)

    if not txt.is_file():
        print("[transcribe] no transcript (music-only or Whisper skipped) — video/frames are enough to file", file=sys.stderr)

    if args.output and txt.is_file():
        Path(args.output).write_text(txt.read_text(encoding="utf-8"), encoding="utf-8")

    print("--- Summary ---", file=sys.stderr)
    print(f"Reel ID:       {mid}", file=sys.stderr)
    print(f"Description:   {description}", file=sys.stderr)
    print(f"Transcript:    {txt}", file=sys.stderr)
    print(f"Video:         {video}", file=sys.stderr)
    print(f"Frames:        {result.count} in {frames_dir}", file=sys.stderr)
    if thumbnail:
        print(f"Thumbnail:     {thumbnail}", file=sys.stderr)
    print("--- Transcript ---", file=sys.stderr)
    out = Path(args.output) if args.output else txt
    if out.is_file():
        sys.stdout.write(out.read_text(encoding="utf-8", errors="replace"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
