"""Fetch an X/Twitter status: thread text, photos, optional video + Whisper."""
from __future__ import annotations

import argparse
import os
import sys
import tempfile
from pathlib import Path

from extract_status import media_id_from_url
from frame_extract import frame_extract
from local_config import REPO_ROOT, downloads_dir
from tooling import (
    extract_audio_aac,
    ocr_to_file,
    optional_x_cookies,
    probe_duration,
    warn_ollama,
    ytdlp,
)
from whisper_run import whisper_transcribe


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="igx twitter",
        description="Fetch X/Twitter: thread text, photos, optional video + Whisper.",
    )
    p.add_argument("url")
    p.add_argument("--model", default="small", choices=("small", "medium", "base"))
    p.add_argument("--skip-whisper", action="store_true")
    return p


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if not args.skip_whisper:
        warn_ollama()
    mid = media_id_from_url(args.url)
    if not mid:
        print("Could not parse tweet id from URL", file=sys.stderr)
        return 1
    out = downloads_dir() / "twitter" / mid
    out.mkdir(parents=True, exist_ok=True)

    print("[1/4] Fetching tweet + thread (FixTweet)...", file=sys.stderr)
    fetch_mod = REPO_ROOT / "lib" / "twitter-fetch.py"
    import subprocess

    from local_config import venv_python

    python_bin = venv_python()
    exe = str(python_bin) if python_bin.is_file() else sys.executable
    proc = subprocess.run(
        [exe, str(fetch_mod), args.url, str(out)],
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        print(proc.stderr or proc.stdout or "twitter-fetch failed", file=sys.stderr)
        return proc.returncode or 1
    meta = (proc.stdout or "").strip().split("\t")
    if len(meta) >= 4:
        mid, handle, photo_count, has_video = meta[0], meta[1], meta[2], meta[3]
    else:
        handle, photo_count, has_video = "", "0", "0"
    print(
        f"      ID: {mid}  @{handle}  photos: {photo_count}  video_flag: {has_video}",
        file=sys.stderr,
    )

    cookies = optional_x_cookies()
    video = ""
    print("[2/4] Trying yt-dlp for video...", file=sys.stderr)
    probe = ytdlp(["--print", "id", args.url], cookies=cookies, capture=True)
    if probe.returncode == 0:
        ytdlp_log = out / f"{mid}.ytdlp.log"
        dl = ytdlp(
            [
                "-o",
                str(out / f"{mid}.%(ext)s"),
                "--print",
                "after_move:filepath",
                args.url,
            ],
            cookies=cookies,
            capture=True,
        )
        ytdlp_log.write_text((dl.stderr or "") + (dl.stdout or ""), encoding="utf-8")
        lines = [ln.strip() for ln in (dl.stdout or "").splitlines() if ln.strip()]
        if lines and Path(lines[-1]).is_file():
            video = lines[-1]
            print(f"      Video: {video}", file=sys.stderr)
            has_video = "1"
        else:
            print(
                f"      No video file (text/photos only, or login wall — export cookies to {optional_x_cookies() or '~/.config/x-cookies.txt'})",
                file=sys.stderr,
            )
    else:
        print("      yt-dlp could not resolve media (ok for text/photo tweets).", file=sys.stderr)

    txt = out / f"{mid}.txt"
    thread = out / "thread.txt"
    if thread.is_file():
        txt.write_text(thread.read_text(encoding="utf-8"), encoding="utf-8")

    if video and not args.skip_whisper:
        duration_int = int(probe_duration(Path(video)))
        if duration_int > 600:
            print(
                f"[3/4] Skip Whisper (video {duration_int}s > 600s; thread.txt is source of truth).",
                file=sys.stderr,
            )
        else:
            audio = out / f"{mid}.audio.m4a"
            print("[3/4] Audio + frames + Whisper...", file=sys.stderr)
            if not extract_audio_aac(Path(video), audio):
                print("      Audio extract failed — thread.txt stays source of truth", file=sys.stderr)
                audio = None
            interval = os.environ.get("IG_REEL_FRAME_INTERVAL", "1")
            if duration_int <= 120:
                result = frame_extract(Path(video), out / "frames", interval)
                print(f"      Frames: {result.count}", file=sys.stderr)
            else:
                print(f"      Frames skipped (>120s). Use igx reextract {mid}", file=sys.stderr)
            whisper_out = None
            if audio and audio.is_file():
                whisper_out = whisper_transcribe(audio, out, args.model)
            if whisper_out and whisper_out.is_file() and whisper_out.resolve() != txt.resolve():
                combined = (
                    "=== Tweet / thread ===\n"
                    + thread.read_text(encoding="utf-8", errors="replace")
                    + "\n\n=== Video transcript ===\n"
                    + whisper_out.read_text(encoding="utf-8", errors="replace")
                )
                fd, tmp_name = tempfile.mkstemp(suffix=".txt")
                os.close(fd)
                tmp = Path(tmp_name)
                tmp.write_text(combined, encoding="utf-8")
                tmp.replace(txt)
    else:
        print("[3/4] Skip Whisper (no video or --skip-whisper).", file=sys.stderr)

    ocr_dirs = []
    if (out / "photos").is_dir():
        ocr_dirs.append(out / "photos")
    if (out / "frames").is_dir():
        ocr_dirs.append(out / "frames")
    if ocr_dirs:
        ocr_to_file(*ocr_dirs, out=out / f"{mid}.ocr.txt")

    # Videos inside quoted posts (downloaded by twitter-fetch.py): transcribe short ones.
    for qv in sorted(out.glob("quote_video_*.mp4")):
        if args.skip_whisper or probe_duration(qv) > 600:
            continue
        qa = qv.with_suffix(".m4a")
        if extract_audio_aac(qv, qa):
            qt = whisper_transcribe(qa, out, args.model)
            if qt and qt.is_file():
                with txt.open("a", encoding="utf-8") as f:
                    f.write(f"\n\n=== Quoted video transcript ({qv.name}) ===\n" + qt.read_text(encoding="utf-8", errors="replace"))
    print("[4/4] Done.", file=sys.stderr)
    print("--- Summary ---", file=sys.stderr)
    print(f"ID:       {mid}", file=sys.stderr)
    print(f"Handle:   @{handle}", file=sys.stderr)
    print(f"Thread:   {out / 'thread.txt'}", file=sys.stderr)
    print(f"Photos:   {out / 'photos'}/ ({photo_count})", file=sys.stderr)
    if video:
        print(f"Video:    {video}", file=sys.stderr)
    print(f"Combined: {txt}", file=sys.stderr)
    if thread.is_file():
        sys.stdout.write(thread.read_text(encoding="utf-8", errors="replace"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
