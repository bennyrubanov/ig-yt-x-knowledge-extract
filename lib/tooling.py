"""Shared subprocess helpers. Never print cookie values."""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
from contextlib import contextmanager
from collections.abc import Iterator
from pathlib import Path
from urllib.parse import urlsplit

from instagram_safety import InstagramSafetyHold, assert_ready, instagram_request
from local_config import ig_cookies_path, x_cookies_path
from ocr_frames import write_ocr


def which_or_none(name: str) -> str | None:
    return shutil.which(name)


def require_cmd(name: str) -> str:
    path = shutil.which(name)
    if not path:
        print(f"{name} not found — required.", file=sys.stderr)
        raise SystemExit(1)
    return path


def require_ig_cookies() -> Path:
    try:
        assert_ready()
    except InstagramSafetyHold as exc:
        print(exc, file=sys.stderr)
        raise SystemExit(3) from exc
    path = ig_cookies_path()
    if not path.is_file():
        print(f"Cookie file missing: {path}", file=sys.stderr)
        print("No Instagram OAuth / Graph API / Connect Instagram.", file=sys.stderr)
        print("Scripts need a Netscape jar at ~/.config/ig-cookies.txt (HttpOnly sessionid).", file=sys.stderr)
        print("Recipe: docs/auth.md  —  python3 scripts/check-setup.py", file=sys.stderr)
        print("Do not commit, log, echo, or paste the file.", file=sys.stderr)
        raise SystemExit(1)
    return path


def optional_x_cookies() -> Path | None:
    path = x_cookies_path()
    return path if path.is_file() else None


def warn_ollama(*, for_whisper: bool = True) -> None:
    if not shutil.which("ollama"):
        return
    try:
        proc = subprocess.run(
            ["ollama", "ps"],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return
    lines = [ln for ln in (proc.stdout or "").splitlines()[1:] if ln.strip()]
    if not lines:
        return
    extra = " — Whisper uses RAM. Unload with: ollama stop <model>" if for_whisper else ""
    print(f"WARNING: Ollama models loaded{extra}", file=sys.stderr)
    print("\n".join(lines), file=sys.stderr)


def probe_duration(path: Path) -> float:
    proc = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "default=noprint_wrappers=1:nokey=1",
            str(path),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    raw = (proc.stdout or "").strip()
    try:
        return float(raw) if raw else 0.0
    except ValueError:
        return 0.0


def has_audio_stream(path: Path) -> bool:
    proc = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-select_streams",
            "a:0",
            "-show_entries",
            "stream=index",
            "-of",
            "csv=p=0",
            str(path),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    return bool((proc.stdout or "").strip())


@contextmanager
def scratch_cookie_jar(src: Path) -> Iterator[Path]:
    """Copy a Netscape jar for yt-dlp ``--cookies`` write-back.

    yt-dlp mutates the file passed to ``--cookies``. After a failed Instagram
    fetch that write-back can strip ``sessionid`` from the live export. Always
    give yt-dlp a throwaway copy; never write the mutated copy back onto
    ``~/.config/ig-cookies.txt``.
    """
    fd, raw = tempfile.mkstemp(prefix="igx-cookies-", suffix=".txt")
    os.close(fd)
    dest = Path(raw)
    try:
        shutil.copy2(src, dest)
        try:
            dest.chmod(0o600)
        except OSError:
            pass
        yield dest
    finally:
        dest.unlink(missing_ok=True)


def ytdlp(
    args: list[str],
    *,
    cookies: Path | None = None,
    check: bool = False,
    capture: bool = False,
    live_stderr: bool = False,
) -> subprocess.CompletedProcess[str]:
    def _is_instagram_arg(value: str) -> bool:
        try:
            host = (urlsplit(value).hostname or "").lower()
        except ValueError:
            return False
        return host == "instagram.com" or host.endswith(".instagram.com")

    is_instagram = any(_is_instagram_arg(arg) for arg in args)
    if cookies is not None and cookies.resolve() == ig_cookies_path().resolve():
        is_instagram = True

    def _run(cookie_path: Path | None) -> subprocess.CompletedProcess[str]:
        cmd = [require_cmd("yt-dlp")]
        if cookie_path is not None:
            cmd += ["--cookies", str(cookie_path)]
        cmd += args
        if live_stderr:
            return subprocess.run(
                cmd,
                check=False,
                stdout=subprocess.PIPE,
                stderr=None,
                text=True,
            )
        return subprocess.run(
            cmd,
            check=False,
            capture_output=capture,
            text=True,
        )

    def _with_cookies() -> subprocess.CompletedProcess[str]:
        if cookies is not None:
            with scratch_cookie_jar(cookies) as jar:
                return _run(jar)
        return _run(None)

    if is_instagram:
        with instagram_request() as attempt:
            try:
                proc = _with_cookies()
            except (OSError, subprocess.SubprocessError):
                attempt["pause_reason"] = "Instagram downloader failed; review before another request"
                raise
            if proc.returncode != 0:
                attempt["pause_reason"] = f"Instagram downloader exited {proc.returncode}; review before another request"
    else:
        proc = _with_cookies()
    if check:
        proc.check_returncode()
    return proc


def ytdlp_print(query: str, url: str, *, cookies: Path | None = None) -> str:
    proc = ytdlp(["--print", query, url], cookies=cookies, capture=True)
    return (proc.stdout or "").strip()


def extract_audio_aac(video: Path, audio: Path) -> bool:
    proc = subprocess.run(
        [
            require_cmd("ffmpeg"),
            "-y",
            "-i",
            str(video),
            "-vn",
            "-acodec",
            "aac",
            "-b:a",
            "128k",
            str(audio),
            "-loglevel",
            "error",
        ],
        check=False,
    )
    return proc.returncode == 0 and audio.is_file()


def ocr_to_file(*dirs: Path, out: Path, jobs: int = 6) -> None:
    try:
        if not shutil.which("tesseract"):
            print("WARNING: tesseract not on PATH — skip OCR", file=sys.stderr)
            return
        existing = [d for d in dirs if d.is_dir()]
        if not existing:
            return
        write_ocr(*existing, out=out, jobs=jobs)
    except Exception as exc:  # noqa: BLE001 — extract continues without OCR
        print(f"WARNING: OCR failed — extract continues ({exc})", file=sys.stderr)
