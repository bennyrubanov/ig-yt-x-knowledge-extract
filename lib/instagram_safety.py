"""Local, cross-process guard for authenticated Instagram extraction.

This limits this repo's download paths; no numeric limit guarantees that
Instagram will accept automated access. The state lives outside any clone.
"""
from __future__ import annotations

import json
import argparse
import os
import sys
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator

MIN_GAP_SECONDS = 180
MAX_REQUESTS_24H = 8
WINDOW_SECONDS = 24 * 60 * 60


class InstagramSafetyHold(RuntimeError):
    pass


def state_path() -> Path:
    override = os.environ.get("IGX_INSTAGRAM_SAFETY_STATE", "").strip()
    return Path(override) if override else Path.home() / ".local/state/ig-yt-x-knowledge-extract/instagram-safety.json"


def _lock_file(file):  # noqa: ANN001
    if os.name == "nt":
        import msvcrt

        file.seek(0)
        msvcrt.locking(file.fileno(), msvcrt.LK_LOCK, 1)
    else:
        import fcntl

        fcntl.flock(file.fileno(), fcntl.LOCK_EX)


def _unlock_file(file):  # noqa: ANN001
    if os.name == "nt":
        import msvcrt

        file.seek(0)
        msvcrt.locking(file.fileno(), msvcrt.LK_UNLCK, 1)
    else:
        import fcntl

        fcntl.flock(file.fileno(), fcntl.LOCK_UN)


@contextmanager
def locked_state() -> Iterator[tuple[Path, dict]]:
    path = state_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    lock = path.with_suffix(".lock")
    with lock.open("a+b") as file:
        _lock_file(file)
        try:
            if path.exists():
                try:
                    state = json.loads(path.read_text(encoding="utf-8"))
                    if not isinstance(state, dict) or not isinstance(state.get("requests", []), list):
                        raise ValueError("invalid safety state")
                except (OSError, ValueError, TypeError) as exc:
                    raise InstagramSafetyHold(f"Instagram safety state unreadable at {path}; stopping closed: {exc}") from exc
            else:
                state = {"paused": False, "reason": "", "requests": []}
            yield path, state
        finally:
            _unlock_file(file)


def _save(path: Path, state: dict) -> None:
    tmp = path.with_suffix(f".{os.getpid()}.tmp")
    try:
        with tmp.open("w", encoding="utf-8") as file:
            json.dump(state, file, indent=2, sort_keys=True)
            file.write("\n")
        if os.name != "nt":
            tmp.chmod(0o600)
        tmp.replace(path)
    finally:
        tmp.unlink(missing_ok=True)


def status() -> dict:
    with locked_state() as (_, state):
        return dict(state)


def assert_ready() -> None:
    state = status()
    if state.get("paused"):
        raise InstagramSafetyHold(
            f"Instagram extraction paused: {state.get('reason') or 'review needed'}. "
            "No Instagram request was made. See docs/auth.md and `igx ig-safety status`."
        )
    now = time.time()
    try:
        recent = [float(t) for t in state.get("requests", []) if now - WINDOW_SECONDS < float(t) <= now]
    except (TypeError, ValueError) as exc:
        raise InstagramSafetyHold("Invalid Instagram safety history; stopping closed.") from exc
    if len(recent) >= MAX_REQUESTS_24H:
        until = datetime.fromtimestamp(min(recent) + WINDOW_SECONDS, timezone.utc).isoformat()
        raise InstagramSafetyHold(f"Instagram request budget reached ({MAX_REQUESTS_24H}/24h); next slot after {until}. No request made.")


def hold(reason: str) -> None:
    with locked_state() as (path, state):
        state.update(paused=True, reason=reason, held_at=datetime.now(timezone.utc).isoformat())
        _save(path, state)


def resume(*, confirmed_clear: bool) -> None:
    if not confirmed_clear:
        raise InstagramSafetyHold("Confirm the account warning is clear before resuming.")
    with locked_state() as (path, state):
        state.update(paused=False, reason="", resumed_at=datetime.now(timezone.utc).isoformat())
        _save(path, state)


@contextmanager
def instagram_request() -> Iterator[dict]:
    """Serialize and count yt-dlp invocations, including across processes."""
    with locked_state() as (path, state):
        if state.get("paused"):
            raise InstagramSafetyHold(
                f"Instagram extraction paused: {state.get('reason') or 'review needed'}. "
                "No Instagram request was made. See docs/auth.md and `igx ig-safety status`."
            )
        now = time.time()
        try:
            recent = [float(t) for t in state.get("requests", []) if now - WINDOW_SECONDS < float(t) <= now]
        except (TypeError, ValueError) as exc:
            raise InstagramSafetyHold("Invalid Instagram safety history; stopping closed.") from exc
        if len(recent) >= MAX_REQUESTS_24H:
            until = datetime.fromtimestamp(min(recent) + WINDOW_SECONDS, timezone.utc).isoformat()
            raise InstagramSafetyHold(f"Instagram request budget reached ({MAX_REQUESTS_24H}/24h); next slot after {until}. No request made.")
        if recent:
            wait = MIN_GAP_SECONDS - (now - recent[-1])
            if wait > 0:
                print(f"[ig-safety] waiting {wait:.0f}s before next request", file=sys.stderr)
                time.sleep(wait)
                now = time.time()
        # Count before the request, so a crash cannot erase the attempt.
        state["requests"] = recent + [now]
        _save(path, state)
        attempt: dict = {}
        try:
            yield attempt
        finally:
            if attempt.get("pause_reason"):
                state.update(
                    paused=True,
                    reason=attempt["pause_reason"],
                    held_at=datetime.now(timezone.utc).isoformat(),
                )
                _save(path, state)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Inspect or change the local Instagram extraction stop.")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("status")
    hold_cmd = commands.add_parser("hold")
    hold_cmd.add_argument("--reason", required=True)
    resume_cmd = commands.add_parser("resume")
    resume_cmd.add_argument("--confirmed-clear", action="store_true")
    args = parser.parse_args(argv)
    try:
        if args.command == "hold":
            hold(args.reason)
        elif args.command == "resume":
            resume(confirmed_clear=args.confirmed_clear)
        state = status()
    except InstagramSafetyHold as exc:
        print(exc, file=sys.stderr)
        return 3
    now = time.time()
    recent = [float(t) for t in state.get("requests", []) if now - WINDOW_SECONDS < float(t) <= now]
    print(json.dumps({
        "paused": bool(state.get("paused")), "reason": state.get("reason", ""),
        "requests_24h": len(recent), "max_requests_24h": MAX_REQUESTS_24H,
        "minimum_gap_seconds": MIN_GAP_SECONDS,
    }, indent=2))
    return 0
