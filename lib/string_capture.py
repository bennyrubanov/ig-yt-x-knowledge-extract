"""Public Instagram capture through String; never reads Instagram credentials.

Paid network work is separate from offline processing. No retries, browser actions,
cookie fallback, remote browser sessions or automatic budget resets exist here.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import http.client
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

from local_config import downloads_dir, load_local_env
from string_media import MediaError, canonical_source, download_asset, extract_media, validate_media
from string_usage import Ledger, UsageBlocked

API_URL = "https://request.usestring.ai/v1/fetch"
MAX_RESPONSE_BYTES = 8 * 1024 * 1024


class CaptureStopped(RuntimeError):
    pass


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def api_key() -> str:
    key = os.environ.get("STRING_AI_API_KEY", "").strip()
    if key == "proxy-injected":
        raise CaptureStopped("proxy_injected_credentials_not_supported")
    if not key and sys.platform == "darwin":
        try:
            result = subprocess.run(
                ["security", "find-generic-password", "-s", "string-ai", "-w"],
                capture_output=True, text=True, timeout=10, check=False,
            )
            if result.returncode == 0:
                key = result.stdout.strip()
        except (OSError, subprocess.SubprocessError):
            pass
    if not key:
        raise CaptureStopped("missing_string_api_key")
    if "\n" in key or "\r" in key:
        raise CaptureStopped("invalid_string_api_key")
    return key


def fetch_page(url: str, key: str) -> dict:
    """One fixed-endpoint REST call. Return only whitelisted billing headers."""
    canonical, _ = canonical_source(url)
    body = {"url": canonical, "format": "json", "solveCaptcha": False,
            "executeJS": False, "requireWSS": False}
    req = urllib.request.Request(API_URL, data=json.dumps(body).encode(), method="POST",
        headers={"Authorization": "Bearer " + key, "Content-Type": "application/json",
                 "Accept": "application/json", "Accept-Encoding": "identity"})
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
    try:
        response = opener.open(req, timeout=120)
    except urllib.error.HTTPError as exc:
        # Do not echo provider bodies, request URLs or authorization values.
        try:
            return {"status_code": exc.code, "billed_request_type": exc.headers.get("x-billed-request-type"),
                    "request_id": exc.headers.get("x-request-id"), "error": "provider_http_error"}
        finally:
            exc.close()
    except (OSError, urllib.error.URLError, TimeoutError):
        return {"status_code": 0, "error": "provider_transport_error"}
    with response:
        result = {"status_code": response.status,
                  "billed_request_type": response.headers.get("x-billed-request-type"),
                  "request_id": response.headers.get("x-request-id")}
        try:
            raw = response.read(MAX_RESPONSE_BYTES + 1)
        except (OSError, TimeoutError, http.client.HTTPException):
            return dict(result, error="provider_read_error")
        if len(raw) > MAX_RESPONSE_BYTES:
            return dict(result, error="provider_response_too_large")
        try:
            envelope = json.loads(raw)
        except (ValueError, UnicodeError):
            return dict(result, error="provider_invalid_json")
        if not isinstance(envelope, dict) or envelope.get("statusCode") != 200:
            return dict(result, status_code=envelope.get("statusCode", 0) if isinstance(envelope, dict) else 0,
                        error="origin_access_error")
        data = envelope.get("data")
        if not isinstance(data, (str, dict, list)) or not data:
            return dict(result, error="origin_empty_body")
        return dict(result, data=data, response_bytes=len(raw))


def write_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + f".{os.getpid()}.tmp")
    try:
        tmp.write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n")
        tmp.chmod(0o600)
        tmp.replace(path)
    finally:
        tmp.unlink(missing_ok=True)


def shared_record(path: Path | None, url: str, run: str, receipt: dict) -> None:
    if path is None:
        return
    # Compatible with the existing String-wide ledger; these are independent REST
    # calls, so the MCP bridge will not also record them.
    row = {"ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
           "caller": "igx:" + run, "url": url, "http_status": receipt.get("status_code", 0),
           "billed": receipt.get("billed_request_type") or "", "execute_js": False,
           "require_wss": False, "had_actions": False,
           "raw_len": receipt.get("response_bytes", 0), "plan": "starter", "via": "rest"}
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as out:
        out.write(json.dumps(row) + "\n")
        out.flush()
        os.fsync(out.fileno())


def cached_manifest(directory: Path, shortcode: str) -> dict | None:
    path = directory / f"{shortcode}.public.json"
    if not path.is_file():
        return None
    try:
        value = json.loads(path.read_text())
        if (value.get("source_id") != shortcode or value.get("capture_complete") is not True
                or canonical_source(value.get("source_url", ""))[1] != shortcode):
            return None
        files = value.get("files")
        if not isinstance(files, list) or not files:
            return None
        for file in files:
            dest = Path(file["path"])
            if not dest.is_file() or hashlib.sha256(dest.read_bytes()).hexdigest() != file["sha256"]:
                return None
        return value
    except (OSError, ValueError, KeyError, TypeError):
        return None


def capture(url: str, run: str, ledger: Ledger, directory: Path, shared_ledger: Path | None = None) -> dict:
    canonical, mid = canonical_source(url)
    cached = cached_manifest(directory, mid)
    if cached:
        if "/reel/" in canonical and not any(f.get("kind") == "video" for f in cached["files"]):
            raise CaptureStopped("cached_reel_video_missing")
        return {"cached": True, "manifest": str(directory / f"{mid}.public.json"), "source_id": mid,
                "usage": ledger.report(run)}
    key = api_key()  # Missing credentials must not consume a reservation.
    attempt = ledger.reserve(run, mid)
    try:
        receipt = fetch_page(canonical, key)
    except Exception:
        ledger.hold("provider_unexpected_failure")
        raise CaptureStopped("provider_unexpected_failure") from None
    # Complete and persist the charge envelope before parsing or further network.
    report = ledger.complete(attempt, receipt["status_code"], receipt.get("billed_request_type"),
                             request_id=receipt.get("request_id"), error=receipt.get("error"))
    try:
        shared_record(shared_ledger, canonical, run, receipt)
    except OSError:
        ledger.hold("shared_usage_log_failed")
        raise CaptureStopped("shared_usage_log_failed") from None
    if report.get("hold_reason") or receipt.get("error") or receipt.get("status_code") != 200:
        raise CaptureStopped(report.get("hold_reason") or receipt.get("error") or "capture_held")
    try:
        media = extract_media(receipt["data"], mid)
        if "/reel/" in canonical and not any(a.get("kind") == "video" for a in media["media"]):
            raise CaptureStopped("reel_video_missing")
        directory.mkdir(parents=True, exist_ok=True)
        (directory / f"{mid}.description.txt").write_text(media.get("caption", ""), encoding="utf-8")
        files = []
        assets = media["media"]
        if (not 1 <= len(assets) <= 21 or sum(a["kind"] != "audio" for a in assets) > 20
                or sum(a["kind"] == "audio" for a in assets) > 1):
            raise CaptureStopped("unexpected_asset_count")
        total_bytes = 0
        for index, asset in enumerate(assets):
            kind = asset["kind"]
            ext = {"video": ".mp4", "image": ".jpg", "audio": ".m4a"}[kind]
            use_slides = kind == "image" or "/p/" in canonical or len(assets) > 1
            dest = directory / (f"{mid}/slides/slide_{index+1:02d}{ext}" if use_slides else f"{mid}{ext}")
            download_asset(asset["url"], dest, max_bytes=min(250*1024*1024, 500*1024*1024-total_bytes))
            total_bytes += dest.stat().st_size
            probe = validate_media(dest, expected_duration=asset.get("expected_duration"),
                                   require_audio=bool(asset.get("has_audio")), full_decode=True)
            files.append({"path": str(dest.resolve()), "kind": kind, "role": asset.get("role", "source_media"), "sha256": hashlib.sha256(dest.read_bytes()).hexdigest(),
                          "bytes": dest.stat().st_size, "probe": probe})
        manifest = {"source_id": mid, "source_url": canonical, "provider": "String public fetch",
                    "captured_at": datetime.now(timezone.utc).isoformat(), "run_id": run,
                    "attempt_id": attempt, "caption": media.get("caption", ""), "files": files,
                    "capture_complete": True, "personal_instagram_credentials_used": False,
                    "audio_metadata": {k:v for k,v in (media.get("audio") or {}).items() if k != "url"},
                    "soundtrack_note": "An image-only capture does not establish carousel soundtrack availability."}
        write_json(directory / f"{mid}.public.json", manifest)
        ledger.mark_media(attempt, usable=True)
    except Exception:
        ledger.hold("media_capture_or_validation_failed")
        raise CaptureStopped("media_capture_or_validation_failed") from None
    return {"cached": False, "manifest": str(directory / f"{mid}.public.json"), "source_id": mid,
            "files": len(files), "usage": ledger.report(run)}


def process_local(manifest_path: Path, model: str = "small", frame_interval: str = "1", skip_whisper: bool = False) -> dict:
    from frame_extract import frame_extract
    from tooling import extract_audio_aac, has_audio_stream, ocr_to_file, warn_ollama
    from whisper_run import whisper_transcribe
    manifest = json.loads(manifest_path.read_text())
    mid = manifest["source_id"]
    canonical_source(manifest["source_url"])
    directory = manifest_path.parent
    verified = cached_manifest(directory, mid)
    if verified is None:
        raise CaptureStopped("local_capture_cache_invalid")
    warn_ollama()
    outputs, images = [], []
    for index, file in enumerate(verified["files"]):
        media = Path(file["path"])
        if file["kind"] == "image":
            images.append(media)
            continue
        stem = mid if len(verified["files"]) == 1 else f"{mid}-{index+1:03d}"
        audio = directory / f"{stem}.m4a"
        frames = directory / mid / ("frames" if len(verified["files"]) == 1 else f"frames-{index+1:03d}")
        if has_audio_stream(media):
            if not audio.is_file() and not extract_audio_aac(media, audio):
                raise CaptureStopped("local_audio_extract_failed")
            if not skip_whisper and not (directory / f"{stem}.txt").is_file():
                if not whisper_transcribe(audio, directory, model):
                    raise CaptureStopped("local_transcription_failed")
        if file["kind"] == "video":
            result = frame_extract(media, frames, frame_interval)
            if result.count:
                ocr_to_file(frames, out=directory / f"{stem}.ocr.txt")
        outputs.append({"source_id": mid, "audio": str(audio) if audio.is_file() else None,
                        "transcript": str(directory / f"{stem}.txt") if (directory / f"{stem}.txt").is_file() else None,
                        "frames": str(frames) if frames.is_dir() else None})
    if images:
        ocr_to_file(images[0].parent, out=directory / f"{mid}.ocr.txt")
    return {"source_id": mid, "local_only": True, "outputs": outputs, "images": len(images)}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ledger", type=Path)
    sub = parser.add_subparsers(dest="command", required=True)
    init = sub.add_parser("init", help="Record an explicitly approved capture budget; no network")
    init.add_argument("--run", required=True)
    init.add_argument("--budget-usd", required=True)
    init.add_argument("--max-requests", type=int, default=30)
    init.add_argument("--max-request-usd", default="0.006")
    init.add_argument("--min-gap-seconds", type=float, default=10)
    init.add_argument("--acknowledge-incident", action="store_true", required=True)
    fetch = sub.add_parser("fetch", help="One metered public fetch, then anonymous validated media; no retries")
    fetch.add_argument("url")
    fetch.add_argument("--run", required=True)
    fetch.add_argument("--downloads", type=Path, default=downloads_dir())
    fetch.add_argument("--shared-ledger", type=Path,
                       default=Path(load_local_env()["STRING_USAGE_LEDGER"]).expanduser()
                       if load_local_env().get("STRING_USAGE_LEDGER") else None)
    report = sub.add_parser("usage", help="Read durable usage and any hold")
    report.add_argument("--run")
    local = sub.add_parser("process", help="Transcribe/OCR verified local capture; no source requests")
    local.add_argument("manifest", type=Path)
    local.add_argument("--model", choices=("base", "small", "medium"), default="small")
    local.add_argument("--frame-interval", default="1")
    local.add_argument("--skip-whisper", action="store_true")
    args = parser.parse_args(argv)
    ledger = None
    try:
        if args.command == "process":
            result = process_local(args.manifest, args.model, args.frame_interval, args.skip_whisper)
        else:
            ledger = Ledger(args.ledger)
            if args.command == "init":
                ledger.create_run(args.run, args.budget_usd, max_requests=args.max_requests,
                                  max_request_usd=args.max_request_usd, min_gap_seconds=args.min_gap_seconds)
                result = ledger.report(args.run)
            elif args.command == "usage":
                result = ledger.report(args.run)
            else:
                result = capture(args.url, args.run, ledger, args.downloads, args.shared_ledger)
        print(json.dumps(result, indent=2))
        return 0
    except (CaptureStopped, MediaError, UsageBlocked) as exc:
        print(json.dumps({"stopped": True, "reason": str(exc),
                          "wait_seconds": getattr(exc, "wait_seconds", 0),
                          "usage": ledger.report() if ledger else None}, indent=2))
        return 3


if __name__ == "__main__":
    raise SystemExit(main())
