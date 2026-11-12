#!/usr/bin/env python3
"""Reddit capture: feed parsing, links kept, and the rate-limit waits. No network."""
from __future__ import annotations

import html
import io
import json
import sys
import tempfile
import unittest
import urllib.error
from email.message import Message
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))

import reddit_fetch  # noqa: E402


def entry(eid: str, author: str, title: str, content_html: str, published: str = "2026-11-11T21:34:17+00:00") -> str:
    return (f"<entry><author><name>{author}</name></author>"
            f"<content type=\"html\">{html.escape(content_html)}</content>"
            f"<id>{eid}</id><published>{published}</published><title>{html.escape(title)}</title></entry>")


def feed(*entries: str) -> bytes:
    return ("<?xml version=\"1.0\" encoding=\"UTF-8\"?>"
            "<feed xmlns=\"http://www.w3.org/2005/Atom\">" + "".join(entries) + "</feed>").encode()


POST_URL = "https://www.reddit.com/r/micro_saas/comments/abc123/first_users/"
FOOTER_SELF = (' &#32; submitted by &#32; <a href="https://www.reddit.com/user/op_name"> /u/op_name </a>'
               f' <br/> <span><a href="{POST_URL}">[link]</a></span> &#32; '
               f'<span><a href="{POST_URL}">[comments]</a></span>')
SELF_POST = entry(
    "t3_abc123", "/u/op_name", "Getting first users",
    '<!-- SC_OFF --><div class="md"><p>Read <a href="http://paulgraham.com/ds.html">the essay</a> '
    'and ask <a href="https://www.reddit.com/user/someone">/u/someone</a> in '
    '<a href="https://www.reddit.com/r/startups/">r/startups</a>.</p> <ol><li>Recruit by hand.</li>'
    '<li>Go where users are.</li></ol> <p>Tool: https://example.com/tool</p></div><!-- SC_ON -->' + FOOTER_SELF)
COMMENT_OP = entry("t1_c1", "/u/op_name", "/u/op_name on Getting first users",
                   '<div class="md"><p>Thanks, see <a href="https://docs.example.org/guide">this guide</a>.</p></div>')
COMMENT_OTHER = entry("t1_c2", "/u/other", "/u/other on Getting first users",
                      '<div class="md"><p>Same as <a href="https://www.reddit.com/r/x/comments/zz9/old/">'
                      'this thread</a>.</p></div>')


class ParseTests(unittest.TestCase):
    def test_self_post_keeps_link_urls_and_drops_footer(self) -> None:
        post, comments = reddit_fetch.parse_feed(feed(SELF_POST, COMMENT_OP, COMMENT_OTHER))
        self.assertEqual(post["id"], "abc123")
        self.assertEqual(post["author"], "/u/op_name")
        self.assertIn("the essay (http://paulgraham.com/ds.html)", post["text"])
        self.assertIn("ask /u/someone in r/startups.", post["text"])  # profiles and subreddits: text only
        self.assertIn("- Recruit by hand.", post["text"])
        self.assertNotIn("submitted by", post["text"])
        self.assertNotIn("[comments]", post["text"])
        self.assertNotIn("link", post)  # a text post's [link] points at itself
        self.assertEqual(post["links"], ["http://paulgraham.com/ds.html"])
        self.assertEqual([c["id"] for c in comments], ["c1", "c2"])
        self.assertIn("this guide (https://docs.example.org/guide)", comments[0]["text"])
        # A link to another Reddit thread keeps its URL in the text but isn't an outside link.
        self.assertIn("this thread (https://www.reddit.com/r/x/comments/zz9/old/)", comments[1]["text"])
        self.assertEqual(comments[1]["links"], [])

    def test_link_post_records_target_and_thumbnail(self) -> None:
        content = (f'<table> <tr><td> <a href="{POST_URL}"> <img src="https://external-preview.redd.it/t.jpg?a=1&amp;b=2" '
                   'alt="x" title="x" /> </a> </td><td> &#32; submitted by &#32; '
                   '<a href="https://www.reddit.com/user/op_name"> /u/op_name </a> <br/> '
                   '<span><a href="https://news.example.com/article">[link]</a></span> &#32; '
                   f'<span><a href="{POST_URL}">[comments]</a></span> </td></tr></table>')
        post, _ = reddit_fetch.parse_feed(feed(entry("t3_abc123", "/u/op_name", "An article", content)))
        self.assertEqual(post["link"], "https://news.example.com/article")
        self.assertEqual(post["thumbnail"], "https://external-preview.redd.it/t.jpg?a=1&b=2")
        self.assertEqual(post["links"], ["https://news.example.com/article"])
        self.assertEqual(post["text"], "")

    def test_image_and_gallery_posts(self) -> None:
        for target, key in (("https://i.redd.it/photo.jpeg", "image"),
                            ("https://www.reddit.com/gallery/abc123", "gallery")):
            content = ('<table> <tr><td> </td><td> &#32; submitted by &#32; <a href="https://www.reddit.com/user/op">'
                       f' /u/op </a> <br/> <span><a href="{target}">[link]</a></span> </td></tr></table>')
            post, _ = reddit_fetch.parse_feed(feed(entry("t3_abc123", "/u/op", "Pic", content)))
            self.assertEqual(post.get(key), target)
            self.assertEqual(post["links"], [])

    def test_empty_feed_is_refused(self) -> None:
        with self.assertRaises(reddit_fetch.RedditRefused):
            reddit_fetch.parse_feed(feed())

    def test_focus_comment_from_each_link_shape(self) -> None:
        f = reddit_fetch.focus_comment
        self.assertEqual(f("https://www.reddit.com/r/s/comments/abc123/slug/def456/?share_id=x"), "def456")
        self.assertEqual(f("https://www.reddit.com/r/s/comments/abc123/comment/def456/"), "def456")
        self.assertEqual(f("https://www.reddit.com/r/s/comments/abc123/slug/comment/def456/"), "def456")
        self.assertIsNone(f("https://www.reddit.com/r/s/comments/abc123/slug/?utm_source=share"))
        self.assertIsNone(f("https://www.reddit.com/r/s/comments/abc123/reddit/"))  # an all-letters slug
        self.assertIsNone(f("https://www.reddit.com/r/s/comments/abc123/"))

    def test_render_orders_post_saved_comment_op_then_others(self) -> None:
        post, comments = reddit_fetch.parse_feed(feed(SELF_POST, COMMENT_OP, COMMENT_OTHER))
        text = reddit_fetch.render("r/micro_saas", "abc123", post, comments, "c2")
        self.assertTrue(text.startswith("POST r/micro_saas abc123 by /u/op_name, posted 2026-11-11\nGetting first users"))
        order = [text.index(s) for s in ("=== Saved comment ===", "/u/other (c2)", "=== Replies by the original poster",
                                         "(c1):", "=== Other comments ===")]
        self.assertEqual(order, sorted(order))


class FakeResponse(io.BytesIO):
    def __init__(self, url: str, body: bytes) -> None:
        super().__init__(body)
        self._url = url

    def geturl(self) -> str:
        return self._url

    def __enter__(self):
        return self

    def __exit__(self, *exc) -> None:
        self.close()


def http_error(url: str, code: int, retry_after: str | None = None) -> urllib.error.HTTPError:
    headers = Message()
    if retry_after is not None:
        headers["Retry-After"] = retry_after
    return urllib.error.HTTPError(url, code, "refused", headers, io.BytesIO(b""))


class GetTests(unittest.TestCase):
    def run_get(self, outcomes: list):
        calls, waits = [], []

        def fake_urlopen(req, timeout):
            calls.append(req.full_url)
            item = outcomes.pop(0)
            if isinstance(item, Exception):
                raise item
            return item

        with patch.object(reddit_fetch, "urlopen", fake_urlopen), patch.object(reddit_fetch, "sleep", waits.append):
            try:
                return reddit_fetch.get("https://www.reddit.com/x", log=lambda m: None), calls, waits
            except reddit_fetch.RedditRefused as exc:
                return exc, calls, waits

    def test_429_then_success_waits_once(self) -> None:
        url = "https://www.reddit.com/x"
        result, calls, waits = self.run_get([http_error(url, 429), FakeResponse(url, b"ok")])
        self.assertEqual(result, (url, b"ok"))
        self.assertEqual(len(calls), 2)
        self.assertEqual(waits, [30])

    def test_retry_after_is_honoured_up_to_the_cap(self) -> None:
        url = "https://www.reddit.com/x"
        result, _, waits = self.run_get([http_error(url, 429, "75"), FakeResponse(url, b"ok")])
        self.assertEqual(result[1], b"ok")
        self.assertEqual(waits, [75])
        result, calls, waits = self.run_get([http_error(url, 429, "3600")])
        self.assertIsInstance(result, reddit_fetch.RedditRefused)
        self.assertIn("Retry-After 3600s", str(result))
        self.assertEqual((len(calls), waits), (1, []))

    def test_gives_up_after_three_tries(self) -> None:
        url = "https://www.reddit.com/x"
        result, calls, waits = self.run_get([http_error(url, 429), http_error(url, 503), http_error(url, 429)])
        self.assertIsInstance(result, reddit_fetch.RedditRefused)
        self.assertEqual(str(result), "HTTP 429 after 3 tries")
        self.assertEqual((len(calls), waits), (3, [30, 90]))

    def test_403_and_404_are_not_retried(self) -> None:
        url = "https://www.reddit.com/x"
        for code in (403, 404):
            result, calls, waits = self.run_get([http_error(url, code)])
            self.assertEqual(str(result), f"HTTP {code}")
            self.assertEqual((len(calls), waits), (1, []))

    def test_network_error_is_retried(self) -> None:
        url = "https://www.reddit.com/x"
        result, _, waits = self.run_get([urllib.error.URLError("reset"), FakeResponse(url, b"ok")])
        self.assertEqual(result[1], b"ok")
        self.assertEqual(waits, [30])


class MainTests(unittest.TestCase):
    def test_share_link_capture_writes_files(self) -> None:
        share = "https://www.reddit.com/r/micro_saas/s/AbCdEf"
        resolved = POST_URL + "c2/?share_id=1"
        body = feed(SELF_POST, COMMENT_OP, COMMENT_OTHER)
        seen = []

        def fake_get(url, log=None):
            seen.append(url)
            return (resolved, b"<html>") if url == share else (url, body)

        with tempfile.TemporaryDirectory() as tmp, patch.object(reddit_fetch, "get", fake_get):
            out = Path(tmp) / "r"
            self.assertEqual(reddit_fetch.main([share, str(out)]), 0)
            self.assertEqual(seen[1], "https://www.reddit.com/r/micro_saas/comments/abc123/.rss?limit=500")
            data = json.loads((out / "thread.json").read_text())
            self.assertEqual((data["post_id"], data["focus_comment"], data["saved_url"]), ("abc123", "c2", share))
            self.assertEqual(data["links"], ["http://paulgraham.com/ds.html", "https://docs.example.org/guide"])
            self.assertIn("=== Saved comment ===\n/u/other (c2):", (out / "thread.txt").read_text())
            self.assertEqual((out / "feed.xml").read_bytes(), body)

    def test_exit_codes(self) -> None:
        def refused(url, log=None):
            raise reddit_fetch.RedditRefused("HTTP 429 after 3 tries")

        with patch.object(reddit_fetch, "get", refused), patch("sys.stderr", io.StringIO()) as err:
            self.assertEqual(reddit_fetch.main(["https://www.reddit.com/r/x/s/abc", "/tmp/unused"]), 3)
            self.assertIn("not captured: HTTP 429 after 3 tries", err.getvalue())
        with patch.object(reddit_fetch, "get", lambda url, log=None: ("https://www.reddit.com/r/x/", b"")), \
                patch("sys.stderr", io.StringIO()):
            self.assertEqual(reddit_fetch.main(["https://www.reddit.com/r/x/", "/tmp/unused"]), 2)
        share = "https://www.reddit.com/r/x/s/AbC123"
        with patch.object(reddit_fetch, "get", lambda url, log=None: (share, b"<html>")), \
                patch("sys.stderr", io.StringIO()) as err:
            self.assertEqual(reddit_fetch.main([share, "/tmp/unused"]), 3)
            self.assertIn("share link did not lead to a post", err.getvalue())


if __name__ == "__main__":
    unittest.main()
