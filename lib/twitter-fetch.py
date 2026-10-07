#!/usr/bin/env python3
"""Fetch a public X/Twitter status via FixTweet and save text + photos.

Usage: twitter-fetch.py TWEET_URL_OR_ID OUTPUT_DIR
Writes: tweet.json, thread.json, thread.txt, photos/photo_NN.ext
Prints: id\\thandle\\tphoto_count\\thas_video

thread.txt holds the parents, the saved post, the author's later posts in the
same thread (FixTweet v2 thread endpoint), quoted posts, X Article bodies and
expanded links.
"""
from __future__ import annotations

import json
import re
import sys
import urllib.request
from pathlib import Path
from urllib.parse import urlparse

API = "https://api.fxtwitter.com/status/{id}"
THREAD_API = "https://api.fxtwitter.com/2/thread/{id}"
ID_RE = re.compile(r"(?:status|statuses)/(\d+)")


def status_id(arg: str) -> str:
    m = ID_RE.search(arg)
    if m:
        return m.group(1)
    if arg.isdigit():
        return arg
    raise SystemExit(f"Could not parse tweet id from: {arg}")


def get_json(url: str) -> dict:
    req = urllib.request.Request(url, headers={"User-Agent": "ig-yt-x-knowledge-extract/1.0"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode("utf-8"))


def fetch(tid: str) -> dict:
    data = get_json(API.format(id=tid))
    if data.get("code") != 200 or "tweet" not in data:
        raise SystemExit(f"FixTweet error for {tid}: {data}")
    return data["tweet"]


def download(url: str, dest: Path) -> None:
    req = urllib.request.Request(url, headers={"User-Agent": "ig-yt-x-knowledge-extract/1.0"})
    with urllib.request.urlopen(req, timeout=60) as resp:
        dest.write_bytes(resp.read())


def ext_from_url(url: str) -> str:
    path = urlparse(url).path
    suffix = Path(path).suffix.lower()
    return suffix if suffix in {".jpg", ".jpeg", ".png", ".webp", ".gif"} else ".jpg"


def walk_thread(start: dict) -> list[dict]:
    tweets = [start]
    parent = start.get("replying_to_status")
    seen = {start.get("id")}
    while parent and parent not in seen:
        seen.add(parent)
        try:
            t = fetch(parent)
        except Exception:
            break
        tweets.append(t)
        parent = t.get("replying_to_status")
    tweets.reverse()
    return tweets


def fetch_v2(tid: str) -> dict:
    """FixTweet v2 thread payload ({} when unavailable)."""
    try:
        data = get_json(THREAD_API.format(id=tid))
    except Exception as e:
        print(f"WARNING: FixTweet v2 thread unavailable: {e}", file=sys.stderr)
        return {}
    return data if data.get("code") == 200 else {}


def later_posts(start: dict, v2: dict) -> list[dict]:
    """The author's own posts after the saved one (a 1/N thread's continuation)."""
    handle = ((start.get("author") or {}).get("screen_name") or "").lower()
    out = []
    for t in v2.get("thread") or []:
        author = ((t.get("author") or {}).get("screen_name") or "").lower()
        if author == handle and str(t.get("id", "")).isdigit() and int(t["id"]) > int(start["id"]):
            out.append(t)
    return sorted(out, key=lambda t: int(t["id"]))


def article_media(article: dict) -> dict[str, tuple[str, str]]:
    """media_id -> (kind, image URL) for an X Article's images and video previews."""
    out: dict[str, tuple[str, str]] = {}
    for m in article.get("media_entities") or []:
        info = m.get("media_info") or {}
        if info.get("original_img_url"):
            out[str(m.get("media_id"))] = ("image", info["original_img_url"])
        elif (info.get("preview_image") or {}).get("original_img_url"):
            out[str(m.get("media_id"))] = ("video preview", info["preview_image"]["original_img_url"])
    return out


def article_text(article: dict, names: dict[str, str] | None = None) -> str:
    """Title and paragraphs of an X Article (draft.js blocks), with image markers."""
    names = names or {}
    content = article.get("content") or {}
    emap = content.get("entityMap") or {}
    if isinstance(emap, list):
        emap = {str(e.get("key")): e.get("value") or {} for e in emap}
    lines = [(article.get("title") or "").strip(), ""]
    for b in content.get("blocks") or []:
        if b.get("type") == "atomic":
            for r in b.get("entityRanges") or []:
                data = (emap.get(str(r.get("key"))) or {}).get("data") or {}
                for item in data.get("mediaItems") or []:
                    if names.get(str(item.get("mediaId"))):
                        lines.append(f"[{names[str(item.get('mediaId'))]}]")
            continue
        text = (b.get("text") or "").strip()
        if text:
            lines.append(text)
    return "\n".join(lines).strip()


def extras(tweet: dict) -> list[str]:
    """Community note and poll lines for one post (both change how a post reads)."""
    out = []
    note = tweet.get("community_note")
    if isinstance(note, dict) and (note.get("text") or "").strip():
        out.append("  Community note: " + note["text"].strip().replace("\n", " "))
    poll = tweet.get("poll")
    if isinstance(poll, dict) and poll.get("choices"):
        choices = " | ".join(f"{c.get('label')} ({c.get('percentage', '?')}%)" for c in poll["choices"])
        out.append(f"  Poll ({poll.get('total_votes', '?')} votes): {choices}")
    return out


def video_urls(tweet: dict) -> list[tuple[str, float]]:
    media = tweet.get("media") or {}
    return [(v["url"], float(v.get("duration") or 0)) for v in media.get("videos") or []
            if isinstance(v, dict) and v.get("url")]


def links(tweet: dict) -> list[str]:
    urls = []
    for f in (tweet.get("raw_text") or {}).get("facets") or []:
        u = f.get("replacement") or ""
        if f.get("type") == "url" and u and u not in urls:
            urls.append(u)
    return urls


def media_photos(tweet: dict) -> list[str]:
    media = tweet.get("media") or {}
    urls: list[str] = []
    for p in media.get("photos") or []:
        u = p.get("url") if isinstance(p, dict) else None
        if u:
            urls.append(u)
    for item in media.get("all") or []:
        if isinstance(item, dict) and item.get("type") == "photo" and item.get("url"):
            if item["url"] not in urls:
                urls.append(item["url"])
    return urls


def has_video(tweet: dict) -> bool:
    media = tweet.get("media") or {}
    if media.get("videos"):
        return True
    for item in media.get("all") or []:
        if isinstance(item, dict) and item.get("type") in {"video", "gif"}:
            return True
    return bool(tweet.get("video"))


def main() -> None:
    if len(sys.argv) != 3:
        print("Usage: twitter-fetch.py TWEET_URL_OR_ID OUTPUT_DIR", file=sys.stderr)
        sys.exit(1)
    tid = status_id(sys.argv[1])
    out = Path(sys.argv[2])
    out.mkdir(parents=True, exist_ok=True)
    photos_dir = out / "photos"
    photos_dir.mkdir(exist_ok=True)

    tweet = fetch(tid)
    v2 = fetch_v2(tid)
    thread = walk_thread(tweet) + later_posts(tweet, v2)
    article = (v2.get("status") or {}).get("article") or tweet.get("article")
    (out / "tweet.json").write_text(json.dumps(tweet, indent=2) + "\n", encoding="utf-8")
    (out / "thread.json").write_text(json.dumps(thread, indent=2) + "\n", encoding="utf-8")

    lines: list[str] = []
    photo_n = 0
    quote_videos = 0
    video = False
    for t in thread:
        handle = (t.get("author") or {}).get("screen_name") or "?"
        text = (t.get("text") or "").strip()
        lines.append(f"@{handle} ({t.get('id')}):\n{text}\n")
        if links(t):
            lines.append("  Links: " + " ".join(links(t)) + "\n")
        lines += [x + "\n" for x in extras(t)]
        if t.get("quote"):
            q = t["quote"]
            qh = (q.get("author") or {}).get("screen_name") or "?"
            lines.append(f"  QT @{qh} ({q.get('id')}): {(q.get('text') or '').strip()}\n")
            lines += [x + "\n" for x in extras(q)]
            for url, seconds in video_urls(q):
                quote_videos += 1
                dest = out / f"quote_video_{quote_videos:02d}.mp4"
                try:
                    download(url, dest)
                    lines.append(f"  QT video ({int(seconds)}s): {dest.name}\n")
                except Exception as e:
                    print(f"WARNING: quoted video download failed: {e}", file=sys.stderr)
            if isinstance(q.get("article"), dict):
                lines.append("  QT article: " + article_text(q["article"]) + "\n")
            for url in media_photos(q):
                photo_n += 1
                try:
                    download(url, photos_dir / f"photo_{photo_n:02d}{ext_from_url(url)}")
                except Exception as e:
                    print(f"WARNING: quoted photo download failed: {e}", file=sys.stderr)
        video = video or has_video(t)
        for url in media_photos(t):
            photo_n += 1
            dest = photos_dir / f"photo_{photo_n:02d}{ext_from_url(url)}"
            try:
                download(url, dest)
            except Exception as e:
                print(f"WARNING: photo download failed: {e}", file=sys.stderr)

    if isinstance(article, dict):
        names: dict[str, str] = {}
        for n, (mid, (kind, url)) in enumerate(article_media(article).items(), 1):
            dest = photos_dir / f"article_{n:02d}{ext_from_url(url)}"
            try:
                download(url, dest)
                names[mid] = f"article {kind}: photos/{dest.name}"
            except Exception as e:
                print(f"WARNING: article media download failed: {e}", file=sys.stderr)
        lines.append("=== X Article ===\n" + article_text(article, names) + "\n")
    (out / "thread.txt").write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")
    handle = (tweet.get("author") or {}).get("screen_name") or "unknown"
    print(f"{tid}\t{handle}\t{photo_n}\t{int(video)}")


if __name__ == "__main__":
    main()
