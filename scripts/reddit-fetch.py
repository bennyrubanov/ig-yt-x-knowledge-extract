#!/usr/bin/env python3
"""Fetch a public Reddit post and its comments without login.

Usage: python3 scripts/reddit-fetch.py REDDIT_URL OUTPUT_DIR

Accepts share links (/r/x/s/...), post URLs and comment URLs. Reddit blocks the
anonymous .json API (403) but serves the post's Atom feed. Writes:
  thread.txt  - post, then the original poster's replies, then other comments
  thread.json - the same entries as data
The feed is flat (no reply nesting) and Reddit caps it at a few hundred comments.
"""
from __future__ import annotations

import html
import json
import re
import sys
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path

UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_0) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/129.0 Safari/537.36"
ATOM = {"a": "http://www.w3.org/2005/Atom"}
POST_RE = re.compile(r"reddit\.com/r/([^/]+)/comments/([a-z0-9]+)")


def get(url: str) -> tuple[str, bytes]:
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=30) as resp:
        return resp.geturl(), resp.read()


def text_of(entry: ET.Element) -> str:
    node = entry.find("a:content", ATOM)
    raw = html.unescape(node.text or "") if node is not None else ""
    raw = re.sub(r"<br\s*/?>|</p>|</li>", "\n", raw)
    raw = re.sub(r"submitted by .*$", "", re.sub(r"<[^>]+>", "", raw), flags=re.S)
    return re.sub(r"\n{3,}", "\n\n", html.unescape(raw)).strip()


def author_of(entry: ET.Element) -> str:
    node = entry.find("a:author/a:name", ATOM)
    return node.text if node is not None and node.text else "[deleted]"


def main() -> None:
    if len(sys.argv) != 3:
        raise SystemExit("Usage: reddit-fetch.py REDDIT_URL OUTPUT_DIR")
    final, _ = get(sys.argv[1])  # resolves /s/ share links
    m = POST_RE.search(final)
    if not m:
        raise SystemExit(f"Not a Reddit post URL: {final}")
    sub, post_id = m.groups()
    _, body = get(f"https://www.reddit.com/r/{sub}/comments/{post_id}/.rss?limit=500")
    entries = ET.fromstring(body).findall("a:entry", ATOM)
    if not entries:
        raise SystemExit("Reddit returned no entries")
    rows = [
        {"id": (e.find("a:id", ATOM).text or "").replace("t3_", "").replace("t1_", ""),
         "author": author_of(e),
         "title": (e.find("a:title", ATOM).text or ""),
         "text": text_of(e)}
        for e in entries
    ]
    post, comments = rows[0], rows[1:]
    op = post["author"]
    focus = re.search(r"/comment/([a-z0-9]+)", final)
    lines = [f"POST r/{sub} {post_id} by {op}\n{post['title']}\n\n{post['text']}\n"]
    if focus:
        hit = [c for c in comments if c["id"] == focus.group(1)]
        lines.append("=== Saved comment ===")
        lines += [f"{c['author']} ({c['id']}):\n{c['text']}\n" for c in hit] or ["(not in feed)\n"]
    lines.append(f"=== Replies by the original poster ({op}) ===")
    lines += [f"({c['id']}):\n{c['text']}\n" for c in comments if c["author"] == op]
    lines.append("=== Other comments ===")
    lines += [f"{c['author']} ({c['id']}):\n{c['text']}\n" for c in comments if c["author"] != op]
    out = Path(sys.argv[2])
    out.mkdir(parents=True, exist_ok=True)
    (out / "thread.txt").write_text("\n".join(lines), encoding="utf-8")
    (out / "thread.json").write_text(json.dumps({"url": final, "post": post, "comments": comments}, indent=2), encoding="utf-8")
    print(f"{post_id}\t{op}\t{len(comments)} comments\t{sum(c['author'] == op for c in comments)} by OP")


if __name__ == "__main__":
    main()
