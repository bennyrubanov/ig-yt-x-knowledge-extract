#!/usr/bin/env python3
"""Extract scoreboard: disk + vault, not raw jsonl fail counts.

A jsonl audit is append-only. Overnight 2026-08-18 wrote 33 `fail` rows for
jobs that later grew slides, frames, or notes. Last-write `fail` is a log
event, not a missing note. This module is the scoreboard.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from local_config import config_root, default_vault

DEFAULT_DOWNLOADS = config_root() / "downloads"
DEFAULT_VAULT = default_vault()

FAIL_STATUSES = frozenset({"fail", "timeout", "error"})
OK_LOG = frozenset({"ok", "ok_partial", "skipped_exists", "recovered"})


def classify_url(url: str) -> str:
    u = url.lower()
    if "instagram.com" in u and re.search(r"/p/", u):
        return "carousel"
    if "instagram.com" in u:
        return "reel"
    if "youtube.com" in u or "youtu.be" in u:
        return "youtube"
    if "x.com/" in u or "twitter.com/" in u:
        return "twitter"
    return "unknown"


def media_id_from_url(url: str) -> str:
    m = re.search(r"instagram\.com/(?:reel|reels|p|tv)/([^/?#]+)", url, re.I)
    if m:
        return m.group(1)
    m = re.search(r"(?:status|statuses)/(\d+)", url)
    if m:
        return m.group(1)
    m = re.search(r"(?:v=|youtu\.be/|youtube\.com/(?:shorts|live|embed)/)([\w-]{6,})", url, re.I)
    if m:
        return m.group(1)
    return ""


def artifacts(kind: str, mid: str, downloads: Path) -> dict[str, Any]:
    if kind == "twitter":
        d = downloads / "twitter" / mid
        photos = d / "photos"
        frames = d / "frames"
        return {
            "dir": str(d),
            "thread": (d / "thread.txt").exists() or (d / "thread.json").exists(),
            "photos": len(list(photos.glob("*"))) if photos.exists() else 0,
            "mp4": any(d.glob("*.mp4")),
            "txt": (d / f"{mid}.txt").exists(),
            "ocr": (d / f"{mid}.ocr.txt").exists(),
            "frames": len(list(frames.glob("*.jpg"))) if frames.exists() else 0,
        }
    slides = downloads / mid / "slides"
    frames = downloads / mid / "frames"
    ytxt = downloads / "youtube" / f"{mid}.txt"
    txt = downloads / f"{mid}.txt"
    if kind == "youtube" and not txt.exists() and ytxt.exists():
        txt = ytxt
    ymp4 = downloads / "youtube" / f"{mid}.mp4"
    mp4 = downloads / f"{mid}.mp4"
    if kind == "youtube" and not mp4.exists() and ymp4.exists():
        mp4 = ymp4
    return {
        "description": (downloads / f"{mid}.description.txt").exists(),
        "txt": txt.exists(),
        "ocr": (downloads / f"{mid}.ocr.txt").exists()
        or (downloads / "youtube" / f"{mid}.ocr.txt").exists(),
        "mp4": mp4.exists(),
        "m4a": (downloads / f"{mid}.m4a").exists(),
        "slides": len(list(slides.glob("slide_*"))) if slides.exists() else 0,
        "frames": len(list(frames.glob("*.jpg"))) if frames.exists() else 0,
    }


def usable(kind: str, got: dict[str, Any]) -> bool:
    """True when an agent can file a note from what is on disk."""
    if kind == "carousel":
        return int(got.get("slides") or 0) > 0
    if kind == "twitter":
        return bool(got.get("thread") or got.get("photos") or got.get("mp4") or got.get("txt"))
    return bool(
        got.get("mp4")
        or got.get("txt")
        or int(got.get("frames") or 0) > 0
        or got.get("description")
    )


def vault_notes(mid: str, vault: Path | None) -> list[str]:
    if not mid or vault is None or not vault.is_dir():
        return []
    hits: list[str] = []
    for path in vault.rglob("*.md"):
        if mid in path.name:
            try:
                hits.append(str(path.relative_to(vault)))
            except ValueError:
                hits.append(str(path))
    return sorted(hits)


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    rows = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line:
            continue
        rows.append(json.loads(line))
    return rows


def latest_by_media(rows: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    last: dict[str, dict[str, Any]] = {}
    for row in rows:
        mid = row.get("media_id")
        if mid:
            last[str(mid)] = row
    return last


def score_one(
    row: dict[str, Any],
    downloads: Path,
    vault: Path | None,
) -> dict[str, Any]:
    kind = str(row.get("kind") or "reel")
    mid = str(row.get("media_id") or "")
    got = artifacts(kind, mid, downloads)
    notes = vault_notes(mid, vault)
    on_disk = usable(kind, got)
    log_status = str(row.get("status") or "")
    if on_disk or notes:
        if log_status in FAIL_STATUSES:
            now = "recovered"
        elif log_status in OK_LOG:
            now = log_status
        else:
            now = "ok"
    else:
        now = log_status if log_status in FAIL_STATUSES else "fail"
    return {
        "media_id": mid,
        "kind": kind,
        "log_status": log_status,
        "now": now,
        "on_disk": on_disk,
        "noted": bool(notes),
        "notes": notes,
        "got": got,
        "todoist": row.get("todoist"),
        "url": row.get("url"),
    }


def scoreboard(
    rows: list[dict[str, Any]],
    downloads: Path,
    vault: Path | None = None,
) -> dict[str, Any]:
    last = latest_by_media(rows)
    scored = [score_one(row, downloads, vault) for row in last.values()]
    log_counts = Counter(r.get("status") for r in last.values())
    now_counts = Counter(s["now"] for s in scored)
    still_fail = [s for s in scored if s["now"] in FAIL_STATUSES]
    recovered = [s for s in scored if s["now"] == "recovered"]
    return {
        "n_events": len(rows),
        "n_media": len(last),
        "log_last": dict(log_counts),
        "now": dict(now_counts),
        "still_fail": still_fail,
        "recovered": recovered,
        "scored": scored,
    }


def append_recovered(jsonl: Path, recovered: list[dict[str, Any]]) -> int:
    if not recovered:
        return 0
    with jsonl.open("a", encoding="utf-8") as fh:
        for item in recovered:
            fh.write(
                json.dumps(
                    {
                        "ts": datetime.now(timezone.utc).isoformat(),
                        "status": "recovered",
                        "media_id": item["media_id"],
                        "kind": item["kind"],
                        "url": item.get("url"),
                        "todoist": item.get("todoist"),
                        "got": item.get("got"),
                        "notes": item.get("notes"),
                        "reason": "disk+vault scoreboard (prior fail was stale)",
                    }
                )
                + "\n"
            )
    return len(recovered)


def format_report(board: dict[str, Any]) -> str:
    lines = [
        "Extract scoreboard — disk + vault, not jsonl fail count",
        f"  events={board['n_events']}  unique_media={board['n_media']}",
        f"  log last-write: {board['log_last']}",
        f"  now (disk/vault): {board['now']}",
    ]
    still = board["still_fail"]
    rec = board["recovered"]
    if rec:
        lines.append(f"  recovered since log: {len(rec)} (do not treat as missing notes)")
    if still:
        lines.append(f"  still fail: {len(still)}")
        for item in still:
            lines.append(f"    {item['kind']} {item['media_id']} {item.get('url') or ''}")
    else:
        lines.append("  still fail: 0")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        description="Scoreboard for an extract jsonl. Do not use raw fail counts."
    )
    p.add_argument("--jsonl", type=Path, required=True, help="Audit log to read")
    p.add_argument("--downloads", type=Path, default=DEFAULT_DOWNLOADS)
    p.add_argument(
        "--vault",
        type=Path,
        default=DEFAULT_VAULT,
        help="Obsidian vault (filename contains media_id). Pass a missing path to skip.",
    )
    p.add_argument(
        "--no-vault",
        action="store_true",
        help="Skip vault scan (disk only)",
    )
    p.add_argument(
        "--write-recovered",
        action="store_true",
        help="Append status=recovered rows so last-write matches disk/vault",
    )
    args = p.parse_args(argv)
    vault = None if args.no_vault else args.vault
    rows = load_jsonl(args.jsonl)
    if not rows:
        print(f"no events in {args.jsonl}", file=sys.stderr)
        return 2
    board = scoreboard(rows, args.downloads, vault)
    print(format_report(board))
    if args.write_recovered:
        n = append_recovered(args.jsonl, board["recovered"])
        print(f"appended recovered={n} → {args.jsonl}")
    return 1 if board["still_fail"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
