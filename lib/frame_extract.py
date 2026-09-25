"""Extract JPG frames from a video. Canonical replacement for lib/frame-extract.sh."""
from __future__ import annotations

import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from tooling import probe_duration, require_cmd

SHOWINFO_N = re.compile(r"Parsed_showinfo.* n:")
PTS_TIME = re.compile(r"pts_time:([0-9.]+)")


@dataclass
class FrameExtractResult:
    count: int
    mode: str
    interval: str | int
    skipped_long: bool = False


def pick_auto_interval(scenes: int, duration_s: int) -> int:
    """Bias 1s when unsure. 2s only for clearly static talking-head."""
    if duration_s <= 0:
        return 2
    rate = scenes / duration_s
    if rate >= 0.08 or scenes >= 3 or scenes >= duration_s / 10:
        return 1
    return 2


def _ffmpeg_scene_log(video: Path) -> str:
    proc = subprocess.run(
        [
            require_cmd("ffmpeg"),
            "-i",
            str(video),
            "-filter:v",
            "select='gt(scene,0.35)',showinfo",
            "-f",
            "null",
            "-",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    return (proc.stderr or "") + (proc.stdout or "")


def count_scenes(video: Path) -> int:
    return len(SHOWINFO_N.findall(_ffmpeg_scene_log(video)))


def scene_timestamps(video: Path) -> list[float]:
    found: list[float] = []
    for match in PTS_TIME.finditer(_ffmpeg_scene_log(video)):
        try:
            found.append(float(match.group(1)))
        except ValueError:
            continue
    return found


def _extract_at(video: Path, frames_dir: Path, ts: float | int, idx: int) -> bool:
    outfile = frames_dir / f"frame_{idx:03d}.jpg"
    first = subprocess.run(
        [
            require_cmd("ffmpeg"),
            "-y",
            "-ss",
            str(ts),
            "-i",
            str(video),
            "-frames:v",
            "1",
            "-q:v",
            "2",
            "-strict",
            "unofficial",
            str(outfile),
            "-loglevel",
            "error",
        ],
        check=False,
    )
    if first.returncode == 0 and outfile.is_file() and outfile.stat().st_size > 0:
        return True
    second = subprocess.run(
        [
            require_cmd("ffmpeg"),
            "-y",
            "-ss",
            str(ts),
            "-i",
            str(video),
            "-frames:v",
            "1",
            "-q:v",
            "2",
            "-vf",
            "format=yuvj420p",
            str(outfile),
            "-loglevel",
            "error",
        ],
        check=False,
    )
    if second.returncode == 0 and outfile.is_file() and outfile.stat().st_size > 0:
        return True
    print(f"WARNING: skipped frame {idx} at t={ts}s (mjpeg/encode)", file=sys.stderr)
    if outfile.exists() and outfile.stat().st_size == 0:
        outfile.unlink(missing_ok=True)
    return False


def frame_extract(
    video: Path,
    frames_dir: Path,
    interval: str | int = 1,
    *,
    skip_long: bool = True,
) -> FrameExtractResult:
    duration = probe_duration(video)
    duration_int = int(duration)
    frames_dir.mkdir(parents=True, exist_ok=True)
    count = 0
    mode = "fixed"
    chosen: str | int = interval

    if skip_long and duration_int > 120:
        print(
            f"Video longer than 120s ({duration_int}s) — skipping frame extraction.",
            file=sys.stderr,
        )
        return FrameExtractResult(0, mode, chosen, skipped_long=True)

    raw = str(interval)
    if raw == "auto":
        scenes = count_scenes(video)
        chosen = pick_auto_interval(scenes, duration_int)
        rate = scenes / duration_int if duration_int else 0.0
        print(
            f"Auto frame interval: {chosen}s ({scenes} scene cuts in {duration_int}s, {rate:.2f} cuts/s).",
            file=sys.stderr,
        )
    elif raw == "scene":
        mode = "scene"
        idx = 1
        for ts in scene_timestamps(video):
            if _extract_at(video, frames_dir, ts, idx):
                count += 1
            idx += 1
        if not (frames_dir / "frame_001.jpg").is_file():
            if _extract_at(video, frames_dir, 0, 1):
                count = max(count, 1)
        print(
            f"Extracted {count} scene-change frames ({duration_int}s video).",
            file=sys.stderr,
        )
        return FrameExtractResult(count, mode, raw)

    step = int(chosen)
    if step < 1:
        step = 1
    idx = 1
    ts = 0
    while ts <= duration_int:
        if _extract_at(video, frames_dir, ts, idx):
            count += 1
        idx += 1
        ts += step
    print(
        f"Extracted {count} frames every {step}s ({duration_int}s video).",
        file=sys.stderr,
    )
    return FrameExtractResult(count, mode, step)
