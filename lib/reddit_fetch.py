"""Fetch a public Reddit post and its comments without login.

    python scripts/igx.py reddit REDDIT_URL [OUTPUT_DIR]
    python3 scripts/reddit-fetch.py REDDIT_URL [OUTPUT_DIR]     (the same command)

Accepts share links (/r/x/s/...), post URLs, comment URLs and redd.it short links.
Reddit refuses the anonymous .json API (403) but serves the post's Atom feed. Writes
to OUTPUT_DIR (default: downloads/reddit-<post id>):
  thread.txt  - the post with its link or image, the saved comment, every reply by the
                original poster, then the other comments; links keep their URLs
  thread.json - the same entries as data, plus every outside link the thread holds
  feed.xml    - the feed as Reddit sent it, so a re-read needs no new request
The feed is flat (no reply nesting, no scores) and Reddit caps it at a few hundred
comments. A gallery's images are not in the feed; its link is kept.

Reddit sometimes answers a cloud machine with 429 Too Many Requests (2026-11-12: the
first request was refused, the same one ten minutes later went through). Each request
waits out a 429, a 5xx or a dropped connection up to twice, honouring Retry-After when
it asks for at most MAX_WAIT seconds. Exit codes: 0 captured, 2 not a Reddit post link,
3 Reddit refused or couldn't be reached (the reason is printed), so the caller can try
once more after its other saves and then file the save from its queue row.
"""
from __future__ import annotations

import argparse
import html
import json
import re
import sys
import time
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path
from urllib.parse import urljoin, urlsplit

UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 14_0) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/129.0 Safari/537.36")
ATOM = {"a": "http://www.w3.org/2005/Atom"}
POST_RE = re.compile(r"reddit\.com/((?:r|user)/[^/?#]+)/comments/([a-z0-9]+)")
# The path of a link to one comment: /comments/<post>/<slug>/<comment>/, /comments/<post>/comment/<comment>/
# or /comments/<post>/<slug>/comment/<comment>/. A post link has one segment after the post ID.
FOCUS_RE = re.compile(r"/comments/[a-z0-9]+/(?:[^/]+/comment|comment|[^/]+)/([a-z0-9]+)/?$")
ANCHOR_RE = re.compile(r'<a\s[^>]*?href="([^"]*)"[^>]*>(.*?)</a>', re.S | re.I)
IMG_RE = re.compile(r'<img\s[^>]*?src="([^"]*)"', re.I)
IMAGE_HOSTS = ("i.redd.it", "preview.redd.it", "i.imgur.com")
IMAGE_EXT = re.compile(r"\.(?:jpe?g|png|gif|webp)(?:[?#]|$)", re.I)

RETRY_STATUS = {429, 500, 502, 503, 504}
WAITS = (30, 90)          # seconds before the second and third try
MAX_WAIT = 120            # a Retry-After longer than this ends the attempt
TIMEOUT = 30

# Patched by tests.
urlopen = urllib.request.urlopen
sleep = time.sleep


class RedditRefused(RuntimeError):
    """Reddit refused or couldn't be reached after the waits."""


def get(url: str, log=None) -> tuple[str, bytes]:
    """GET with the waits above. Returns (final URL, body)."""
    log = log or (lambda msg: print(msg, file=sys.stderr))
    tries = len(WAITS) + 1
    for n in range(tries):
        req = urllib.request.Request(url, headers={"User-Agent": UA})
        try:
            with urlopen(req, timeout=TIMEOUT) as resp:
                return resp.geturl(), resp.read()
        except urllib.error.HTTPError as exc:
            code = exc.code
            retry_after = (exc.headers.get("Retry-After") or "").strip() if exc.headers else ""
            exc.close()
            if code not in RETRY_STATUS:
                raise RedditRefused(f"HTTP {code}") from None
            reason = f"HTTP {code}"
            wait = WAITS[n] if n < len(WAITS) else 0
            if retry_after.isdigit():
                if int(retry_after) > MAX_WAIT:
                    raise RedditRefused(f"{reason}, Retry-After {retry_after}s") from None
                wait = max(wait, int(retry_after))
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            reason = f"network error ({getattr(exc, 'reason', exc)})"
            wait = WAITS[n] if n < len(WAITS) else 0
        if n == tries - 1:
            raise RedditRefused(f"{reason} after {tries} tries")
        log(f"reddit: {reason}; waiting {wait}s before try {n + 2} of {tries}")
        sleep(wait)
    raise AssertionError("unreachable")


def _is_image(url: str) -> bool:
    host = re.sub(r"^https?://([^/]+).*$", r"\1", url).lower()
    return host in IMAGE_HOSTS or bool(IMAGE_EXT.search(url))


def _absolute(url: str) -> str:
    return urljoin("https://www.reddit.com/", html.unescape(url.strip()))


def _is_profile_or_subreddit(url: str) -> bool:
    """A link to a user or a whole subreddit: kept as its text only."""
    return bool(re.match(r"https?://(?:www\.|old\.)?reddit\.com/(?:u/|user/|r/[^/]+/?$)", url))


def _is_outside(url: str) -> bool:
    host = (urlsplit(url).hostname or "").lower()
    return not (host == "reddit.com" or host.endswith(".reddit.com") or host == "redd.it"
                or host.endswith(".redd.it"))


def _flatten(fragment: str, links: list[str]) -> str:
    """HTML from the feed to plain text; anchors keep their URL as 'text (url)'."""
    def anchor(m: re.Match) -> str:
        url = _absolute(m.group(1))
        text = re.sub(r"<[^>]+>", "", m.group(2)).strip()
        if not url.startswith("http"):
            return text
        if _is_outside(url) and url not in links:
            links.append(url)
        if not text or text.rstrip("/") == url.rstrip("/") or _is_profile_or_subreddit(url):
            return text or url
        return f"{text} ({url})"

    out = ANCHOR_RE.sub(anchor, fragment)
    out = re.sub(r"<br\s*/?>|</p>|</li>|</h[1-6]>|</blockquote>", "\n", out, flags=re.I)
    out = re.sub(r"<li[^>]*>", "- ", out, flags=re.I)
    out = re.sub(r"<[^>]+>", "", out)
    out = html.unescape(out)
    out = re.sub(r"[ \t]+\n", "\n", out)
    out = re.sub(r"\n[ \t]+", "\n", out)
    return re.sub(r"\n{3,}", "\n\n", out).strip()


def parse_entry(entry: ET.Element, is_post: bool) -> dict:
    def child(path: str) -> str:
        node = entry.find(path, ATOM)
        return node.text if node is not None and node.text else ""

    raw = child("a:content")
    links: list[str] = []
    row = {
        "id": re.sub(r"^t[13]_", "", child("a:id")),
        "author": child("a:author/a:name") or "[deleted]",
        "title": child("a:title"),
        "published": child("a:published") or child("a:updated"),
        "text": "",
        "links": links,
    }
    body, footer = raw, ""
    split = re.search(r"(?:&#32;|\s)*submitted by(?:&#32;|\s)", raw) if is_post else None
    if split:
        body, footer = raw[:split.start()], raw[split.end():]
    if is_post:
        # A link or image post's thumbnail sits in a table cell before "submitted by";
        # the post's own target is the footer's [link] anchor.
        for m in ANCHOR_RE.finditer(footer):
            if re.sub(r"<[^>]+>", "", m.group(2)).strip() == "[link]":
                target = _absolute(m.group(1))
                if "/comments/" in target and "reddit.com" in target:
                    break  # a text post links to itself
                if "reddit.com/gallery/" in target:
                    row["gallery"] = target
                elif _is_image(target):
                    row["image"] = target
                else:
                    row["link"] = target
                    if _is_outside(target):
                        links.append(target)
                break
        thumbs = [_absolute(u) for u in IMG_RE.findall(body)]
        if thumbs and "link" in row:
            row["thumbnail"] = thumbs[0]
        body = re.sub(r"<table>.*?</td>\s*<td>", "", body, flags=re.S)  # drop the thumbnail cell
    row["text"] = _flatten(body, links)
    return row


def parse_feed(body: bytes) -> tuple[dict, list[dict]]:
    entries = ET.fromstring(body).findall("a:entry", ATOM)
    if not entries:
        raise RedditRefused("Reddit returned a feed with no entries")
    post = parse_entry(entries[0], is_post=True)
    return post, [parse_entry(e, is_post=False) for e in entries[1:]]


def focus_comment(url: str) -> str | None:
    m = FOCUS_RE.search(urlsplit(url).path)
    return m.group(1) if m else None


def render(sub: str, post_id: str, post: dict, comments: list[dict], focus: str | None) -> str:
    op = post["author"]
    head = f"POST {sub} {post_id} by {op}"
    if post.get("published"):
        head += f", posted {post['published'][:10]}"
    lines = [head, post["title"], ""]
    if post["text"]:
        lines += [post["text"], ""]
    for key, label in (("link", "Link"), ("image", "Image"), ("gallery", "Gallery (images not in the feed)"),
                       ("thumbnail", "Thumbnail")):
        if post.get(key):
            lines.append(f"{label}: {post[key]}")
    lines.append("")
    if focus:
        hit = [c for c in comments if c["id"] == focus]
        lines.append("=== Saved comment ===")
        lines += [f"{c['author']} ({c['id']}):\n{c['text']}\n" for c in hit] or ["(not in the feed)\n"]
    lines.append(f"=== Replies by the original poster ({op}) ===")
    lines += [f"({c['id']}):\n{c['text']}\n" for c in comments if c["author"] == op]
    lines.append("=== Other comments ===")
    lines += [f"{c['author']} ({c['id']}):\n{c['text']}\n" for c in comments if c["author"] != op]
    return "\n".join(lines)


def thread_links(post: dict, comments: list[dict]) -> list[str]:
    seen: list[str] = []
    for row in [post, *comments]:
        for url in row.get("links", []):
            if url not in seen:
                seen.append(url)
    return seen


def capture(url: str, out: Path | None) -> dict:
    final, _ = get(url)  # resolves share and short links
    m = POST_RE.search(final)
    if not m:
        raise ValueError(f"Not a Reddit post link: {final}")
    sub, post_id = m.groups()
    _, body = get(f"https://www.reddit.com/{sub}/comments/{post_id}/.rss?limit=500")
    post, comments = parse_feed(body)
    focus = focus_comment(final)
    if out is None:
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        from local_config import downloads_dir
        out = downloads_dir() / f"reddit-{post_id}"
    out.mkdir(parents=True, exist_ok=True)
    (out / "feed.xml").write_bytes(body)
    (out / "thread.txt").write_text(render(sub, post_id, post, comments, focus), encoding="utf-8")
    data = {"url": final, "saved_url": url, "subreddit": sub, "post_id": post_id,
            "focus_comment": focus, "post": post, "comments": comments,
            "links": thread_links(post, comments)}
    (out / "thread.json").write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    return {"post_id": post_id, "author": post["author"], "comments": len(comments),
            "by_op": sum(c["author"] == post["author"] for c in comments), "dir": str(out)}


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="igx reddit", description=__doc__.split("\n")[0])
    p.add_argument("url")
    p.add_argument("out", nargs="?", type=Path, help="output folder (default downloads/reddit-<post id>)")
    args = p.parse_args(argv)
    try:
        r = capture(args.url, args.out)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    except (RedditRefused, ET.ParseError) as exc:
        print(f"reddit: not captured: {exc}", file=sys.stderr)
        return 3
    print(f"{r['post_id']}\t{r['author']}\t{r['comments']} comments\t{r['by_op']} by OP\t{r['dir']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
