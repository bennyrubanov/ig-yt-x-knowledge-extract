"""Download YouTube: native captions first, else Whisper."""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

from local_config import REPO_ROOT, downloads_dir, venv_python
from tooling import require_cmd, warn_ollama, ytdlp, ytdlp_print
from whisper_run import whisper_transcribe


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="igx youtube",
        description="Download YouTube: native captions first, else faster-whisper.",
    )
    p.add_argument("url")
    p.add_argument("-o", dest="output")
    p.add_argument("--model", default="small", choices=("small", "medium", "base"))
    p.add_argument(
        "--force-whisper",
        action="store_true",
        help="Run Whisper anyway; writes {id}.whisper.txt (keeps caption .txt)",
    )
    return p


def _pick_sub(download_dir: Path, mid: str) -> tuple[Path | None, str]:
    for name in (f"{mid}.en.srt", f"{mid}.en.vtt"):
        path = download_dir / name
        if path.is_file():
            return path, "manual"
    for path in sorted(download_dir.glob(f"{mid}*.en*.srt")):
        return path, "auto"
    for path in sorted(download_dir.glob(f"{mid}*.srt")):
        if path.name.endswith(".srt"):
            return path, "auto"
    for path in sorted(download_dir.glob(f"{mid}*.en*.vtt")):
        return path, "auto"
    for path in sorted(download_dir.glob(f"{mid}*.vtt")):
        return path, "auto"
    return None, ""


def _convert_subs(sub_file: Path, txt: Path) -> None:
    import subprocess

    python_bin = venv_python()
    exe = str(python_bin) if python_bin.is_file() else sys.executable
    script = REPO_ROOT / "lib" / "subs-to-txt.py"
    subprocess.run([exe, str(script), str(sub_file), str(txt)], check=False)


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    require_cmd("yt-dlp")
    warn_ollama()
    download_dir = downloads_dir() / "youtube"
    download_dir.mkdir(parents=True, exist_ok=True)
    pipeline_start = time.time()

    print("[1/4] Resolving video...", file=sys.stderr)
    mid = ytdlp_print("id", args.url)
    if not mid:
        # yt-dlp prints nothing when YouTube answers "Sign in to confirm you're not
        # a bot". Never continue with an empty ID: the caption glob below would
        # then pick up another video's subtitles.
        raise SystemExit(
            "yt-dlp could not resolve this video (YouTube may be asking to confirm "
            "you're not a bot). Wait and retry; nothing was written."
        )
    title = ytdlp_print("title", args.url)
    desc_file = download_dir / f"{mid}.description.txt"
    desc_file.write_text(ytdlp(["--print", "description", args.url], capture=True).stdout or "", encoding="utf-8")
    print(f"      ID: {mid}", file=sys.stderr)
    print(f"      Title: {title}", file=sys.stderr)

    print("[2/4] Checking for native captions (en)...", file=sys.stderr)
    sub_start = time.time()
    sub_log = download_dir / f"{mid}.subs.log"
    proc = ytdlp(
        [
            "--write-subs",
            "--write-auto-subs",
            "--sub-langs",
            "en,en-US,en-GB,en.*",
            "--sub-format",
            "srt/best,vtt/best",
            "--convert-subs",
            "srt",
            "--skip-download",
            "-o",
            str(download_dir / f"{mid}.%(ext)s"),
            args.url,
        ],
        capture=True,
    )
    sub_log.write_text((proc.stdout or "") + (proc.stderr or ""), encoding="utf-8")
    sub_file, sub_kind = _pick_sub(download_dir, mid)
    if not sub_file and "429" in sub_log.read_text(encoding="utf-8"):
        # YouTube rate-limits the translated "en" auto track (HTTP 429) and yt-dlp
        # then stops before the original-language track, which usually still works.
        print("      Captions hit HTTP 429 — retrying the original auto track (en-orig)...", file=sys.stderr)
        time.sleep(5)
        retry = ytdlp(
            [
                "--write-auto-subs",
                "--sub-langs",
                "en-orig",
                "--sub-format",
                "srt/best,vtt/best",
                "--convert-subs",
                "srt",
                "--skip-download",
                "-o",
                str(download_dir / f"{mid}.%(ext)s"),
                args.url,
            ],
            capture=True,
        )
        with sub_log.open("a", encoding="utf-8") as f:
            f.write("\n--- retry en-orig ---\n" + (retry.stdout or "") + (retry.stderr or ""))
        sub_file, sub_kind = _pick_sub(download_dir, mid)
    meta = download_dir / f"{mid}.captions.meta"
    if sub_file:
        meta.write_text(
            "\n".join(
                [
                    f"kind: {sub_kind or 'unknown'}",
                    f"file: {sub_file.name}",
                    "manual_track: en (creator upload when present)",
                    "auto_tracks: en-en, en-orig, … (YouTube speech recognition)",
                    "check: yt-dlp --list-subs URL  → 'Available subtitles' vs 'automatic captions'",
                    "",
                ]
            ),
            encoding="utf-8",
        )
    sub_elapsed = int(time.time() - sub_start)
    txt = download_dir / f"{mid}.txt"
    audio = ""
    source = ""

    if sub_file and not args.force_whisper:
        source = "native-captions"
        print(f"      Found captions ({sub_kind}) in {sub_elapsed}s: {sub_file}", file=sys.stderr)
        print("      Skipping Whisper — converting subtitles to text...", file=sys.stderr)
        _convert_subs(sub_file, txt)
        (download_dir / f"{mid}.whisper.log").write_text(
            "\n".join(
                [
                    "=== Native captions (Whisper skipped) ===",
                    f"Kind: {sub_kind}",
                    f"Source: {sub_file}",
                    f"Meta: {meta}",
                    f"Elapsed: {sub_elapsed}s",
                    f"Transcript: {txt}",
                    "",
                ]
            ),
            encoding="utf-8",
        )
    elif sub_file and args.force_whisper:
        source = "native-captions+whisper"
        print(f"      Found captions ({sub_kind}): {sub_file} — keeping as source of truth", file=sys.stderr)
        _convert_subs(sub_file, txt)
        captions_backup = txt.read_text(encoding="utf-8")
        print("[3/4] --force-whisper: downloading audio for comparison pass...", file=sys.stderr)
        dl_start = time.time()
        audio_proc = ytdlp(
            [
                "-x",
                "--audio-format",
                "m4a",
                "-o",
                str(download_dir / f"{mid}.%(ext)s"),
                "--print",
                "after_move:filepath",
                args.url,
            ],
            capture=True,
        )
        audio = (audio_proc.stdout or "").strip().splitlines()[-1] if audio_proc.stdout else ""
        print(f"      Downloaded in {int(time.time() - dl_start)}s: {audio}", file=sys.stderr)
        print(f"      Transcribing → {mid}.whisper.txt (captions unchanged in {mid}.txt)...", file=sys.stderr)
        whisper_out = whisper_transcribe(Path(audio), download_dir, args.model) if audio else None
        if whisper_out and whisper_out.is_file():
            whisper_out.replace(download_dir / f"{mid}.whisper.txt")
        txt.write_text(captions_backup, encoding="utf-8")
        with (download_dir / f"{mid}.whisper.log").open("a", encoding="utf-8") as fh:
            fh.write(
                "\n".join(
                    [
                        "=== Captions (source of truth) + Whisper comparison ===",
                        f"Caption kind: {sub_kind}",
                        f"Caption file: {sub_file}",
                        f"Captions txt: {txt}",
                        f"Whisper txt:  {download_dir / (mid + '.whisper.txt')}",
                        f"Meta: {meta}",
                        "",
                    ]
                )
            )
    else:
        source = "faster-whisper"
        print(f"      No captions found in {sub_elapsed}s — downloading audio...", file=sys.stderr)
        dl_start = time.time()
        audio_proc = ytdlp(
            [
                "-x",
                "--audio-format",
                "m4a",
                "-o",
                str(download_dir / f"{mid}.%(ext)s"),
                "--print",
                "after_move:filepath",
                args.url,
            ],
            capture=True,
        )
        audio = (audio_proc.stdout or "").strip().splitlines()[-1] if audio_proc.stdout else ""
        print(f"      Downloaded in {int(time.time() - dl_start)}s: {audio}", file=sys.stderr)
        print(f"[3/4] Transcribing (ETA printed below; log: {mid}.whisper.log)...", file=sys.stderr)
        whisper_out = whisper_transcribe(Path(audio), download_dir, args.model) if audio else None
        if whisper_out:
            txt = whisper_out

    print("[4/4] Done.", file=sys.stderr)
    elapsed = int(time.time() - pipeline_start)
    print("--- Summary ---", file=sys.stderr)
    print(f"Title:       {title}", file=sys.stderr)
    print(f"ID:          {mid}", file=sys.stderr)
    print(f"Source:      {source}", file=sys.stderr)
    print(f"Description: {desc_file}", file=sys.stderr)
    print(f"Transcript:  {txt}", file=sys.stderr)
    whisper_cmp = download_dir / f"{mid}.whisper.txt"
    if whisper_cmp.is_file():
        print(f"Whisper cmp: {whisper_cmp}", file=sys.stderr)
    if meta.is_file():
        print(f"Caption meta: {meta}", file=sys.stderr)
    if audio:
        print(f"Audio:       {audio}", file=sys.stderr)
    if sub_file:
        print(f"Captions:    {sub_file}", file=sys.stderr)
    print(f"Total wall:  {elapsed}s (~{(elapsed + 59) // 60}m)", file=sys.stderr)

    if args.output and txt.is_file():
        Path(args.output).write_text(txt.read_text(encoding="utf-8"), encoding="utf-8")
    if txt.is_file():
        sys.stdout.write(txt.read_text(encoding="utf-8", errors="replace"))
        return 0
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
