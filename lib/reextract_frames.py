"""Re-extract frames from an already-downloaded reel (no re-download)."""
from __future__ import annotations

import argparse
import os
import shutil
import sys
from pathlib import Path

from frame_extract import frame_extract
from local_config import downloads_dir
from tooling import ocr_to_file, require_cmd


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="igx reextract",
        description="Replace downloads/{id}/frames/*.jpg using existing downloads/{id}.mp4",
    )
    p.add_argument("reel_id")
    p.add_argument(
        "--frame-interval",
        default=os.environ.get("IG_REEL_FRAME_INTERVAL", "1"),
    )
    return p


def output_dirs(video: Path, mid: str, download_dir: Path) -> tuple[Path, Path]:
    try:
        rel = video.resolve().relative_to(download_dir.resolve())
    except ValueError:
        rel = Path(video.name)
    parts = rel.parts
    if parts and parts[0] == "twitter":
        return video.parent / "frames", video.parent / f"{mid}.ocr.txt"
    if parts and parts[0] == "youtube":
        return video.parent / mid / "frames", video.parent / f"{mid}.ocr.txt"
    return download_dir / mid / "frames", download_dir / f"{mid}.ocr.txt"


def find_video(mid: str, download_dir: Path) -> Path | None:
    for cand in (
        download_dir / f"{mid}.mp4",
        download_dir / "youtube" / f"{mid}.mp4",
        download_dir / "twitter" / mid / f"{mid}.mp4",
    ):
        if cand.is_file():
            return cand
    return None


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    require_cmd("ffmpeg")
    download_dir = downloads_dir()
    video = find_video(args.reel_id, download_dir)
    if video is None:
        print(f"Video not found for {args.reel_id} under {download_dir}", file=sys.stderr)
        print("Run igx reel first, or pass the correct id.", file=sys.stderr)
        return 1
    frames_dir, ocr_out = output_dirs(video, args.reel_id, download_dir)

    if frames_dir.exists():
        shutil.rmtree(frames_dir)
    # The 120-second limit protects automatic downloads; an explicit local
    # re-extraction is the operator opting into frame generation.
    result = frame_extract(video, frames_dir, args.frame_interval, skip_long=False)
    if frames_dir.is_dir():
        ocr_to_file(frames_dir, out=ocr_out)
    print("--- Re-extract summary ---", file=sys.stderr)
    print(f"Reel ID:    {args.reel_id}", file=sys.stderr)
    print(f"Video:      {video}", file=sys.stderr)
    print(f"Frames:     {result.count} in {frames_dir}", file=sys.stderr)
    print(f"Interval:   {args.frame_interval}", file=sys.stderr)
    print(f"OCR:        {ocr_out}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
