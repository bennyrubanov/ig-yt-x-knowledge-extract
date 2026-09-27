"""Strict parsing and anonymous retrieval of public Instagram media from String data.

This module never opens an Instagram page or reads browser/account credentials.
The caller supplies String's already obtained HTML/JSON and controls the request
budget.  A parsing failure is final for that source; callers must not retry it.
"""
from __future__ import annotations

from html.parser import HTMLParser
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import HTTPSHandler, HTTPHandler, HTTPRedirectHandler, ProxyHandler, Request, build_opener


MAX_ASSET_BYTES = 250 * 1024 * 1024
_CODE = re.compile(r"^[A-Za-z0-9_-]{2,64}$")
_MEDIA_SUFFIXES = {".mp4": "video/", ".mov": "video/", ".m4v": "video/",
                   ".m4a": "audio/", ".mp3": "audio/", ".aac": "audio/",
                   ".jpg": "image/", ".jpeg": "image/", ".png": "image/", ".webp": "image/"}


class MediaError(ValueError):
    """A source or media asset cannot be accepted safely."""


CaptureError = MediaError


def canonical_source(url: str) -> tuple[str, str]:
    """Return the canonical public post URL and its exact shortcode."""
    if not isinstance(url, str) or any(ord(c) <= 32 or ord(c) == 127 or c == "\\" for c in url):
        raise MediaError("Invalid Instagram source URL")
    try:
        parsed = urlsplit(url)
        allowed = (parsed.scheme == "https" and parsed.hostname in {"instagram.com", "www.instagram.com"}
                   and not parsed.username and not parsed.password and parsed.port in {None, 443})
    except ValueError:
        allowed = False
    if not allowed:
        raise MediaError("Invalid Instagram source URL")
    pieces = parsed.path.strip("/").split("/")
    if len(pieces) != 2 or pieces[0] not in {"reel", "reels", "p"} or not _CODE.fullmatch(pieces[1]):
        raise MediaError("Unsupported Instagram source URL")
    kind = "reel" if pieces[0] == "reels" else pieces[0]
    return f"https://www.instagram.com/{kind}/{pieces[1]}/", pieces[1]


class _Scripts(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.in_json = False
        self.parts: list[str] = []
        self.scripts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "script":
            self.in_json = dict(attrs).get("type", "").split(";", 1)[0].strip().lower() == "application/json"
            self.parts = []

    def handle_data(self, data: str) -> None:
        if self.in_json:
            self.parts.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag == "script" and self.in_json:
            self.scripts.append("".join(self.parts))
            self.in_json = False


def _walk(value):
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from _walk(child)
    elif isinstance(value, list):
        for child in value:
            yield from _walk(child)


def _string(value) -> str | None:
    return value if isinstance(value, str) and value else None


def _best(candidates, url_key: str) -> str | None:
    if not isinstance(candidates, list):
        return None
    eligible = [c for c in candidates if isinstance(c, dict) and _string(c.get(url_key))]
    if not eligible:
        return None
    def dimension(value):
        try:
            return max(0, int(value))
        except (ValueError, TypeError, OverflowError):
            return 0
    best = max(eligible, key=lambda c: (dimension(c.get("width")) * dimension(c.get("height")),
                                        dimension(c.get("width"))))
    return best[url_key]


def _duration(node: dict) -> float | None:
    value = node.get("video_duration", node.get("duration"))
    if isinstance(value, (int, float)) and not isinstance(value, bool) and 0 < value < 24 * 3600:
        return float(value)
    manifest = node.get("video_dash_manifest")
    if isinstance(manifest, str):
        match = re.search(r'mediaPresentationDuration=["\']PT(?:(\d+)H)?(?:(\d+)M)?([\d.]+)S["\']', manifest)
        if match:
            duration = 3600 * int(match[1] or 0) + 60 * int(match[2] or 0) + float(match[3])
            if 0 < duration < 24 * 3600:
                return duration
    return None


def _caption(node: dict) -> str:
    value = node.get("caption")
    if isinstance(value, dict):
        value = value.get("text")
    if isinstance(value, str):
        return value
    edges = (node.get("edge_media_to_caption") or {}).get("edges") if isinstance(node.get("edge_media_to_caption"), dict) else None
    if isinstance(edges, list) and edges and isinstance(edges[0], dict):
        caption_node = edges[0].get("node")
        return (_string(caption_node.get("text")) or "") if isinstance(caption_node, dict) else ""
    return ""


def _one_media(node: dict) -> dict | None:
    video = _best(node.get("video_versions"), "url") or _string(node.get("video_url"))
    image = (_best((node.get("image_versions2") or {}).get("candidates"), "url")
             if isinstance(node.get("image_versions2"), dict) else None) or _string(node.get("display_url"))
    if video:
        result = {"kind": "video", "url": video}
        duration = _duration(node)
        if duration is not None:
            result["expected_duration"] = duration
        if isinstance(node.get("has_audio"), bool):
            result["has_audio"] = node["has_audio"]
        return result
    if image:
        return {"kind": "image", "url": image}
    return None


def _media(node: dict) -> list[dict]:
    children = node.get("carousel_media")
    if isinstance(children, list):
        items = [_one_media(c) for c in children if isinstance(c, dict)]
        if not items or not all(items) or len(items) != len(children):
            return []
    else:
        sidecar = node.get("edge_sidecar_to_children")
        edges = sidecar.get("edges") if isinstance(sidecar, dict) else None
        if isinstance(edges, list):
            items = [_one_media(e["node"]) for e in edges
                     if isinstance(e, dict) and isinstance(e.get("node"), dict)]
            if not items or not all(items) or len(items) != len(edges):
                return []
        else:
            item = _one_media(node)
            items = [item] if item else []
    audio = _audio_metadata(node)
    if items and all(item["kind"] == "image" for item in items) and audio and audio.get("url"):
        items.append({"kind": "audio", "role": "music_reference", "url": audio["url"]})
    return items


def _audio_metadata(node: dict) -> dict | None:
    clips = node.get("clips_metadata")
    if not isinstance(clips, dict):
        return None
    for field in ("music_info", "original_sound_info"):
        raw = clips.get(field)
        if not isinstance(raw, dict):
            continue
        music = raw.get("music_asset_info") if isinstance(raw.get("music_asset_info"), dict) else raw
        result = {k: music[k] for k in ("title", "artist_name", "audio_asset_id")
                  if isinstance(music.get(k), (str, int)) and music.get(k)}
        url = _string(music.get("progressive_download_url")) or _string(music.get("audio_url"))
        if url:
            result["url"] = url
        if result:
            result["provenance"] = f"clips_metadata.{field}"
            return result
    return None


def extract_media(html_or_json, shortcode: str) -> dict:
    """Extract only media belonging to the exact shortcode from embedded JSON.

    Returns ``caption``, ``source_url`` and ordered ``media`` asset records.
    A supplied JSON object is also accepted for isolated fixture/provider data.
    """
    if not isinstance(shortcode, str) or not _CODE.fullmatch(shortcode):
        raise MediaError("Invalid source shortcode")
    if isinstance(html_or_json, (dict, list)):
        roots = [html_or_json]
    elif isinstance(html_or_json, str):
        if re.search(r"(?:login|challenge|checkpoint|private account|this account is private)",
                     html_or_json, re.IGNORECASE) and "application/json" not in html_or_json:
            raise MediaError("Public source is unavailable")
        if html_or_json.lstrip().startswith(("{", "[")):
            try:
                roots = [json.loads(html_or_json)]
            except json.JSONDecodeError as exc:
                raise MediaError("Invalid source JSON") from exc
        else:
            parser = _Scripts()
            parser.feed(html_or_json)
            roots = []
            for script in parser.scripts:
                try:
                    roots.append(json.loads(script))
                except json.JSONDecodeError:
                    continue
    else:
        raise MediaError("Unsupported source format")
    matches = [node for root in roots for node in _walk(root)
               if node.get("code") == shortcode or node.get("shortcode") == shortcode]
    if not matches:
        raise MediaError("Exact public source was not found")
    candidates = []
    for node in matches:
        owners = [node.get("owner"), node.get("user")]
        if (node.get("is_private") is True or node.get("private") is True
                or any(isinstance(owner, dict) and owner.get("is_private") is True
                       for owner in owners)):
            raise MediaError("Public source is unavailable")
        media = _media(node)
        if media:
            candidates.append((node, media))
    if not candidates:
        raise MediaError("Exact source has no usable media")
    # Different matching payloads are unsafe to reconcile: one may be stale or
    # unrelated despite carrying a duplicated shortcode field.
    signatures = {json.dumps(media, sort_keys=True) for _, media in candidates}
    if len(signatures) != 1:
        raise MediaError("Conflicting media for exact source")
    node, media = candidates[0]
    source_url = f"https://www.instagram.com/reel/{shortcode}/"
    for candidate, _ in candidates:
        candidate_url = candidate.get("seo_canonical_url")
        try:
            canonical, code = canonical_source(candidate_url)
            if code == shortcode:
                source_url = canonical
                break
        except (MediaError, ValueError, TypeError):
            pass
    result = {"caption": next((_caption(n) for n, _ in candidates if _caption(n)), ""),
              "source_url": source_url,
              "media": media}
    audio = next((_audio_metadata(n) for n, _ in candidates if _audio_metadata(n)), None)
    if audio:
        result["audio"] = audio
    duration = _duration(node)
    if duration is not None:
        result["expected_duration"] = duration
    return result


def _asset_host(url: str) -> str:
    if not isinstance(url, str) or any(ord(c) <= 32 or ord(c) == 127 or c == "\\" for c in url):
        raise MediaError("Disallowed media URL")
    try:
        parsed = urlsplit(url)
        host = parsed.hostname or ""
        allowed = (parsed.scheme == "https" and not parsed.username and not parsed.password
                   and parsed.port in {None, 443} and parsed.path.startswith("/")
                   and any(host.endswith("." + suffix) for suffix in ("cdninstagram.com", "fbcdn.net")))
    except ValueError:
        allowed = False
    if not allowed:
        raise MediaError("Disallowed media URL")
    return host


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, msg, headers, newurl):
        raise MediaError("Media redirect refused")


def download_asset(url: str, dest: str | Path, max_bytes: int = MAX_ASSET_BYTES) -> Path:
    """Stream one anonymous CDN asset to an atomic destination; never retry."""
    _asset_host(url)
    destination = Path(dest)
    expected_type = _MEDIA_SUFFIXES.get(destination.suffix.lower())
    if not expected_type or not isinstance(max_bytes, int) or max_bytes <= 0:
        raise MediaError("Invalid media destination or byte limit")
    destination.parent.mkdir(parents=True, exist_ok=True)
    opener = build_opener(ProxyHandler({}), HTTPSHandler(), HTTPHandler(), _NoRedirect())
    request = Request(url, headers={"Accept": expected_type + "*"}, method="GET")
    part = None
    try:
        with opener.open(request, timeout=30) as response:
            if response.status != 200 or response.geturl() != url:
                raise MediaError("Media response refused")
            content_type = response.headers.get("Content-Type", "").split(";", 1)[0].strip().lower()
            if not content_type.startswith(expected_type):
                raise MediaError("Unexpected media content type")
            length_header = response.headers.get("Content-Length")
            length = int(length_header) if length_header is not None else None
            if length is not None and (length <= 0 or length > max_bytes):
                raise MediaError("Media size outside limit")
            with tempfile.NamedTemporaryFile(dir=destination.parent, prefix=destination.name + ".",
                                             suffix=".part", delete=False) as output:
                part = Path(output.name)
                total = 0
                while chunk := response.read(1024 * 1024):
                    total += len(chunk)
                    if total > max_bytes:
                        raise MediaError("Media size outside limit")
                    output.write(chunk)
                if total == 0 or (length is not None and total != length):
                    raise MediaError("Incomplete media response")
                output.flush()
                os.fsync(output.fileno())
        part.replace(destination)
        return destination
    except (HTTPError, URLError, OSError, ValueError) as exc:
        if isinstance(exc, MediaError):
            raise
        raise MediaError("Media download failed") from None
    finally:
        if part is not None:
            part.unlink(missing_ok=True)


def validate_media(path: str | Path, expected_duration: float | None = None,
                   require_audio: bool = False, full_decode: bool = False,
                   check_silence: bool = True) -> dict:
    """Probe local media, sample audio activity, and optionally decode every packet."""
    media = Path(path)
    if not media.is_file() or media.stat().st_size <= 0:
        raise MediaError("Empty or missing media file")
    is_image = media.suffix.lower() in {".jpg", ".jpeg", ".png", ".webp"}
    try:
        probe = subprocess.run(["ffprobe", "-v", "error", "-show_entries",
                                "format=duration:stream=codec_type,duration,width,height", "-of", "json",
                                "-protocol_whitelist", "file,pipe", str(media)],
                               capture_output=True, text=True, timeout=45, check=True)
        info = json.loads(probe.stdout)
        streams = info.get("streams", [])
        raw_duration = info.get("format", {}).get("duration")
        duration = 0.0 if is_image and raw_duration in {None, "N/A"} else float(raw_duration or 0)
    except (OSError, subprocess.SubprocessError, ValueError, TypeError, KeyError) as exc:
        raise MediaError("Media probe failed") from None
    kinds = {s.get("codec_type") for s in streams if isinstance(s, dict)}
    if not kinds.intersection({"video", "audio"}):
        raise MediaError("Media has no decodable stream")
    if is_image:
        try:
            has_dimensions = any(s.get("codec_type") == "video" and int(s.get("width") or 0) > 0
                                 and int(s.get("height") or 0) > 0 for s in streams if isinstance(s, dict))
        except (ValueError, TypeError, OverflowError):
            has_dimensions = False
        if not has_dimensions:
            raise MediaError("Image dimensions missing")
    elif duration <= 0:
        raise MediaError("Media duration missing")
    if media.suffix.lower() in {".mp4", ".mov", ".m4v"} and "video" not in kinds:
        raise MediaError("Video stream missing")
    is_audio_file = media.suffix.lower() in {".m4a", ".mp3", ".aac"}
    if (require_audio or is_audio_file) and "audio" not in kinds:
        raise MediaError("Audio stream missing")
    if expected_duration is not None and duration + max(1.0, expected_duration * 0.03) < expected_duration:
        raise MediaError("Media is shorter than expected")
    audio_max_db = None
    if (require_audio or is_audio_file) and check_silence:
        # A 30-second bounded sample detects a wholly silent short clip without
        # treating a long clip's quiet opening as proof that all audio is silent.
        try:
            sample = subprocess.run(["ffmpeg", "-v", "info", "-protocol_whitelist", "file,pipe",
                                     "-i", str(media), "-t",
                                     str(min(30.0, duration)), "-vn", "-af", "volumedetect",
                                     "-f", "null", "-"], capture_output=True, text=True,
                                    timeout=75, check=True)
        except (OSError, subprocess.SubprocessError):
            raise MediaError("Audio activity check failed") from None
        match = re.search(r"max_volume:\s*(-?(?:inf|[\d.]+))\s*dB", sample.stderr or "", re.IGNORECASE)
        if match:
            audio_max_db = float(match[1])
            if duration <= 30 and audio_max_db == float("-inf"):
                raise MediaError("Audio stream is silent")
    if full_decode:
        try:
            command = ["ffmpeg", "-v", "error", "-xerror", "-protocol_whitelist", "file,pipe",
                       "-i", str(media)]
            if is_image:
                command.extend(["-frames:v", "1"])
            subprocess.run(command + ["-f", "null", "-"], capture_output=True, timeout=600, check=True)
        except (OSError, subprocess.SubprocessError):
            raise MediaError("Media decode failed") from None
    return {"duration": duration, "streams": streams, "has_video": "video" in kinds and not is_image,
            "has_image": is_image,
            "has_audio": "audio" in kinds, "audio_sample_max_db": audio_max_db,
            "bytes": media.stat().st_size}
