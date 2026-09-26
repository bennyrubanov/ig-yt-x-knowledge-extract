"""Batch extract with a jsonl audit. Scoreboard is disk + vault, not exit codes.

Exit != 0 with usable artifacts → ok_partial (ffmpeg mjpeg, image-only carousel
before the script fix, music-only reels). After the run, stale fails are
reconciled against downloads (and the vault if present).
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path

from extract_status import (
    DEFAULT_DOWNLOADS,
    DEFAULT_VAULT,
    append_recovered,
    artifacts,
    classify_url,
    format_report,
    load_jsonl,
    media_id_from_url,
    scoreboard,
    usable,
)
from instagram_safety import InstagramSafetyHold, assert_ready
from local_config import REPO_ROOT, default_jsonl_path

KINDS = ("reel", "carousel", "youtube", "twitter")
# IGTV /tv/ is the same download path as a reel. Saved-export queues still emit kind=tv.
KIND_ALIASES = {"tv": "reel"}
IG_KINDS = frozenset({"reel", "carousel"})
DEFAULT_WORKERS = 1
DEFAULT_IG_GAP_S = 0  # Additional wait only; the cross-process guard has a fixed floor.
_ig_gate = threading.Lock()


def normalize_kind(kind: str) -> str:
    return KIND_ALIASES.get(kind or "", kind)


def download_attempts(kind: str) -> int:
    """Instagram empty-media is a checkpoint risk, not a blip. Do not retry in-run."""
    return 1 if normalize_kind(kind) in IG_KINDS else 2


def igx_cmd(kind: str) -> list[str]:
    kind = normalize_kind(kind)
    if kind not in KINDS:
        raise ValueError(kind)
    return [sys.executable, str(REPO_ROOT / "scripts" / "igx.py"), kind]


def log_row(jsonl: Path, row: dict) -> None:
    row = {**row, "ts": datetime.now(timezone.utc).isoformat()}
    with jsonl.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(row) + "\n")


def already_done(kind: str, mid: str, downloads: Path) -> bool:
    return usable(kind, artifacts(kind, mid, downloads))


def run_one(
    *,
    kind: str,
    mid: str,
    url: str,
    extra: dict,
    jsonl: Path,
    downloads: Path,
    timeout: int,
    ig_gap_s: int = 0,
) -> dict:
    kind = normalize_kind(kind)
    base = {"kind": kind, "media_id": mid, "url": url, **extra}
    if already_done(kind, mid, downloads):
        row = {**base, "status": "skipped_exists", "got": artifacts(kind, mid, downloads)}
        log_row(jsonl, row)
        return row
    if kind in IG_KINDS:
        try:
            assert_ready()
        except InstagramSafetyHold as exc:
            return {**base, "status": "blocked", "reason": str(exc)}

    def _download() -> dict:
        t0 = time.time()
        last_err = ""
        exit_code = 1
        try:
            cmd = igx_cmd(kind) + [url]
            ok = False
            attempts = download_attempts(kind)
            for attempt in range(1, attempts + 1):
                proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
                last_err = (proc.stderr or "")[-2000:]
                exit_code = proc.returncode
                if proc.returncode == 0:
                    ok = True
                    break
                if attempt < attempts:
                    time.sleep(8 * attempt)
            got = artifacts(kind, mid, downloads)
            if exit_code == 3 and kind in IG_KINDS:
                status = "blocked"
            elif ok:
                status = "ok"
            elif usable(kind, got):
                status = "ok_partial"
            else:
                status = "fail"
            row = {
                **base,
                "status": status,
                "exit": exit_code,
                "elapsed_s": round(time.time() - t0, 1),
                "got": got,
                "stderr_tail": last_err if status == "fail" else "",
            }
        except subprocess.TimeoutExpired:
            got = artifacts(kind, mid, downloads)
            row = {
                **base,
                "status": "ok_partial" if usable(kind, got) else "timeout",
                "elapsed_s": timeout,
                "got": got,
            }
        except Exception as exc:  # noqa: BLE001 — audit row must always write
            row = {
                **base,
                "status": "error",
                "error": str(exc),
                "got": artifacts(kind, mid, downloads),
            }
        if row["status"] != "blocked":
            log_row(jsonl, row)
        return row

    if kind in IG_KINDS:
        with _ig_gate:
            row = _download()
            if ig_gap_s > 0:
                time.sleep(ig_gap_s)
            return row
    return _download()


def jobs_from_queue(path: Path) -> list[dict]:
    data = json.loads(path.read_text())
    jobs = []
    seen: set[str] = set()
    for item in data.get("items", []):
        if item.get("action") and item["action"] != "extract":
            continue
        extra = {
            "todoist_id": item.get("todoist_id"),
            "todoist": item.get("todoist"),
            "user_question": item.get("user_question"),
        }
        for media in item.get("media") or []:
            mid = media.get("media_id") or media_id_from_url(media.get("url") or "")
            kind = normalize_kind(media.get("kind") or classify_url(media.get("url") or ""))
            key = f"{kind}:{mid}"
            if not mid or key in seen:
                continue
            seen.add(key)
            jobs.append({"kind": kind, "media_id": mid, "url": media["url"], "extra": extra})
    return jobs


def jobs_from_urls(urls: list[str]) -> list[dict]:
    jobs = []
    seen: set[str] = set()
    for url in urls:
        kind = classify_url(url)
        mid = media_id_from_url(url)
        if kind == "unknown" or not mid:
            print(f"SKIP unknown URL: {url}", flush=True)
            continue
        key = f"{kind}:{mid}"
        if key in seen:
            continue
        seen.add(key)
        jobs.append({"kind": kind, "media_id": mid, "url": url, "extra": {}})
    return jobs


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Batch extract; scoreboard is disk+vault.")
    p.add_argument("urls", nargs="*", help="Reel / post / YouTube / X URLs")
    p.add_argument("--queue", type=Path, help="Overnight-style queue.json")
    p.add_argument(
        "--jsonl",
        type=Path,
        default=default_jsonl_path(),
        help="Audit log (append). Default: OS temp dir / extract.jsonl",
    )
    p.add_argument("--downloads", type=Path, default=DEFAULT_DOWNLOADS)
    p.add_argument("--vault", type=Path, default=DEFAULT_VAULT)
    p.add_argument("--no-vault", action="store_true")
    p.add_argument(
        "--workers",
        type=int,
        default=DEFAULT_WORKERS,
        help="Parallel jobs (default 1). Instagram downloads still run one at a time.",
    )
    p.add_argument(
        "--ig-gap",
        type=int,
        default=DEFAULT_IG_GAP_S,
        metavar="SEC",
        help="Additional wait after an Instagram job. Cannot disable the persistent safety guard.",
    )
    p.add_argument("--timeout", type=int, default=900)
    args = p.parse_args(argv)

    if args.queue:
        jobs = jobs_from_queue(args.queue)
    else:
        jobs = jobs_from_urls(args.urls)
    if not jobs:
        print("no jobs", file=sys.stderr)
        return 2

    args.jsonl.parent.mkdir(parents=True, exist_ok=True)
    ig_jobs = sum(1 for job in jobs if normalize_kind(job["kind"]) in IG_KINDS)
    if args.workers < 1 or args.ig_gap < 0:
        p.error("workers must be >=1 and ig-gap must be >=0")
    print(f"jobs={len(jobs)} workers={args.workers} ig_extra_gap={args.ig_gap}s jsonl={args.jsonl}", flush=True)
    if args.workers > 1 and ig_jobs:
        p.error("Instagram jobs require --workers 1; use separate workers only for local analysis")
    results = []
    def _run(job: dict) -> dict:
        return run_one(
            kind=job["kind"], mid=job["media_id"], url=job["url"], extra=job["extra"],
            jsonl=args.jsonl, downloads=args.downloads, timeout=args.timeout, ig_gap_s=args.ig_gap,
        )

    halted_ig = 0
    if ig_jobs:
        # Submit one at a time. A failure must not leave already-submitted IG
        # futures waiting to hit the account; cached jobs can still reconcile.
        stop_instagram = False
        for i, job in enumerate(jobs, 1):
            is_ig = normalize_kind(job["kind"]) in IG_KINDS
            if is_ig and stop_instagram and not already_done(job["kind"], job["media_id"], args.downloads):
                halted_ig += 1
                continue
            row = _run(job)
            results.append(row)
            print(f"[{i}/{len(jobs)}] {row['status']} {row['kind']} {row['media_id']}", flush=True)
            if is_ig and row["status"] not in {"ok", "skipped_exists"}:
                stop_instagram = True
        if halted_ig:
            print(f"[ig-safety] {halted_ig} Instagram jobs not attempted after stop signal", file=sys.stderr)
    else:
        with ThreadPoolExecutor(max_workers=args.workers) as pool:
            futs = [pool.submit(_run, job) for job in jobs]
            for i, fut in enumerate(as_completed(futs), 1):
                row = fut.result()
                results.append(row)
                print(f"[{i}/{len(jobs)}] {row['status']} {row['kind']} {row['media_id']}", flush=True)

    vault = None if args.no_vault else args.vault
    board = scoreboard(load_jsonl(args.jsonl), args.downloads, vault)
    n = append_recovered(args.jsonl, board["recovered"])
    if n:
        board = scoreboard(load_jsonl(args.jsonl), args.downloads, vault)
    print(format_report(board), flush=True)
    print("DONE log_last", board["log_last"], "now", board["now"], flush=True)
    return 1 if board["still_fail"] or halted_ig or any(r["status"] not in {"ok", "skipped_exists"} for r in results) else 0


if __name__ == "__main__":
    raise SystemExit(main())
