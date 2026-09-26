#!/usr/bin/env python3
"""Cross-platform CLI. Unix shells still wrap this as transcribe-reel.sh etc."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "lib"))

COMMANDS = {
    "reel": "transcribe_reel",
    "carousel": "transcribe_carousel",
    "youtube": "transcribe_youtube",
    "twitter": "transcribe_twitter",
    "batch": "extract_queue",
    "status": "extract_status",
    "reextract": "reextract_frames",
    "cleanup": "cleanup_downloads",
    "ocr-backfill": "ocr_backfill",
    "ig-safety": "instagram_safety",
}


def _usage() -> None:
    print(
        "Usage: python scripts/igx.py "
        "{reel|carousel|youtube|twitter|batch|status|reextract|cleanup|ocr-backfill|ig-safety} ...",
        file=sys.stderr,
    )
    print("Unix aliases: transcribe-reel.sh, transcribe-batch.sh, extract-status.sh, …", file=sys.stderr)


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if not args or args[0] in {"-h", "--help", "help"}:
        _usage()
        return 0 if args else 2
    cmd = args[0]
    rest = args[1:]
    mod_name = COMMANDS.get(cmd)
    if not mod_name:
        print(f"Unknown command: {cmd}", file=sys.stderr)
        _usage()
        return 2
    module = __import__(mod_name)
    return int(module.main(rest))


if __name__ == "__main__":
    raise SystemExit(main())
