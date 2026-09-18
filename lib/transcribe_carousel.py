"""Download an Instagram carousel (instagram.com/p/...)."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from extract_status import media_id_from_url
from local_config import downloads_dir
from tooling import extract_audio_aac, ocr_to_file, require_ig_cookies, ytdlp
from whisper_run import whisper_transcribe


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="igx carousel",
        description="Download Instagram carousel: caption + slides (images + videos).",
    )
    p.add_argument("url")
    p.add_argument("--model", default="small", choices=("small", "medium", "base"))
    p.add_argument(
        "--transcribe-videos",
        action="store_true",
        help="Run Whisper on video slides that have audio (default: off)",
    )
    return p


def _slide_files(slides_dir: Path) -> list[Path]:
    if not slides_dir.is_dir():
        return []
    out = []
    for p in slides_dir.iterdir():
        if not p.is_file() or p.name == "manifest.txt":
            continue
        if p.suffix.lower() in {".jpg", ".jpeg", ".webp", ".png", ".mp4", ".m4a"}:
            out.append(p)
    return out


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    cookies = require_ig_cookies()
    download_dir = downloads_dir()
    download_dir.mkdir(parents=True, exist_ok=True)

    mid = media_id_from_url(args.url)
    if not mid:
        print(f"Could not resolve post id from {args.url}", file=sys.stderr)
        return 1

    description = download_dir / f"{mid}.description.txt"
    slides_dir = download_dir / mid / "slides"
    manifest = slides_dir / "manifest.txt"
    # One extraction request: a failed caption/thumbnail probe must not trigger
    # another Instagram request in the same run. Full download also retains video
    # slides, while --ignore-no-formats-error allows image-only entries.
    slides_dir.mkdir(parents=True, exist_ok=True)
    template = str(slides_dir / "slide_%(playlist_index)02d.%(ext)s")
    print(f"[carousel] Downloading slides to {slides_dir} ...", file=sys.stderr)
    ytdlp(
        [
            "--yes-playlist",
            "--ignore-no-formats-error",
            "--write-description",
            "--write-thumbnail",
            "--convert-thumbnails",
            "jpg",
            "-o",
            template,
            args.url,
        ],
        cookies=cookies,
    )
    captions = sorted(slides_dir.glob("*.description"))
    caption = next(
        (p.read_text(encoding="utf-8", errors="replace") for p in captions if p.stat().st_size),
        "",
    )
    if caption or not description.exists():
        description.write_text(caption, encoding="utf-8")
    if not description.stat().st_size:
        print("[carousel] description empty (common on image-only posts)", file=sys.stderr)

    for thumb in list(slides_dir.glob("slide_*.jpg")) + list(slides_dir.glob("slide_*.webp")):
        base = thumb.with_suffix("")
        if not base.with_suffix(".mp4").is_file() and not base.with_suffix(".m4a").is_file():
            dest = base.with_suffix(".jpg")
            if thumb != dest:
                thumb.replace(dest)

    names = sorted(p.name for p in slides_dir.iterdir() if p.name != "manifest.txt")
    manifest.write_text(
        f"# Carousel {mid}\n# source: {args.url}\n# description: {description}\n\n"
        + "\n".join(names)
        + "\n",
        encoding="utf-8",
    )
    slide_count = len(_slide_files(slides_dir))

    if slides_dir.is_dir():
        ocr_to_file(slides_dir, out=download_dir / f"{mid}.ocr.txt")

    if args.transcribe_videos:
        for video in sorted(slides_dir.glob("slide_*.mp4")):
            audio = video.with_suffix(".m4a")
            if not extract_audio_aac(video, audio):
                continue
            whisper_transcribe(audio, video.parent, args.model)
            print(f"Transcribed: {video.with_suffix('.txt')}", file=sys.stderr)

    print("--- Carousel summary ---", file=sys.stderr)
    print(f"Post ID:      {mid}", file=sys.stderr)
    print(f"Description:  {description}", file=sys.stderr)
    print(f"Slides:       {slide_count} files in {slides_dir}", file=sys.stderr)
    print(f"Manifest:     {manifest}", file=sys.stderr)
    print("--- Description ---", file=sys.stderr)
    caption = description.read_text(encoding="utf-8", errors="replace") if description.is_file() else ""
    sys.stdout.write(caption if caption.strip() else "(no caption)\n")
    if slide_count > 0:
        return 0
    print(f"ERROR: no slides downloaded for {mid}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
