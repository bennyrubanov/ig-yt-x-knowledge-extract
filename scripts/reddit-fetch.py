#!/usr/bin/env python3
"""Same as `python scripts/igx.py reddit URL [OUTPUT_DIR]`; see lib/reddit_fetch.py."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))

from reddit_fetch import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main())
