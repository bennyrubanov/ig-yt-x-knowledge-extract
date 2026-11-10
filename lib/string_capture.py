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
from string_media import MediaError, canonical_source, describe_source, download_asset, extract_media, validate_media
from string_usage import Ledger, UsageBlocked, recommended_run_budget_usd

API_URL = "https://request.usestring.ai/v1/fetch"
# A free authenticated read (the account's crawl-job list): String bills no
# request type for it, and only its status code is kept.
ACCESS_CHECK_URL = "https://request.usestring.ai/v1/sitemap"
# STRING_AI_API_KEY value for a cloud machine whose HTTPS proxy adds the real key.
PROXY_INJECTED_KEY = "proxy-injected"
MAX_RESPONSE_BYTES = 8 * 1024 * 1024
PROVIDER_DEADLINE_SECONDS = 120


class CaptureStopped(RuntimeError):
    pass


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def https_proxy() -> str | None:
    value = (os.environ.get("HTTPS_PROXY") or os.environ.get("https_proxy") or "").strip()
    return value or None


def uses_https_proxy(key: str) -> bool:
    """Explicit managed-secret routing; never replace the supplied credential."""
    option = os.environ.get("STRING_USE_HTTPS_PROXY", "").strip()
    if option not in {"", "0", "1"}:
        raise CaptureStopped("invalid_string_use_https_proxy")
    return key == PROXY_INJECTED_KEY or option == "1"


def api_key() -> str:
    key = os.environ.get("STRING_AI_API_KEY", "").strip()
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
    if uses_https_proxy(key) and not https_proxy():
        raise CaptureStopped("proxy_injected_key_needs_https_proxy")
    return key


def string_opener(key: str):
    """Open String's API directly, or through HTTPS_PROXY when that proxy adds the key.

    Only calls to String's fixed API endpoints use this opener. Proxy-injected
    mode or STRING_USE_HTTPS_PROXY=1 selects the inherited proxy without changing
    the key (including a managed network-secret placeholder). Instagram CDN
    downloads never use one. String fetches the page from its own network either
    way, so this changes nothing Instagram sees.
    """
    proxy = None
    if uses_https_proxy(key):
        proxy = https_proxy()
        if not proxy:
            raise CaptureStopped("proxy_injected_key_needs_https_proxy")
    return urllib.request.build_opener(
        urllib.request.ProxyHandler({"https": proxy} if proxy else {}), NoRedirect())


def check_access(key: str) -> dict:
    """One free authenticated read to confirm String accepts the key; no page fetch."""
    req = urllib.request.Request(ACCESS_CHECK_URL, method="GET",
        headers={"Authorization": "Bearer " + key, "Accept": "application/json",
                 "Accept-Encoding": "identity"})
    try:
        response = string_opener(key).open(req, timeout=30)
    except urllib.error.HTTPError as exc:
        try:
            return {"status_code": exc.code, "billed_request_type": exc.headers.get("x-billed-request-type")}
        finally:
            exc.close()
    except (OSError, urllib.error.URLError, TimeoutError):
        return {"status_code": 0, "error": "provider_transport_error"}
    with response:
        return {"status_code": response.status,
                "billed_request_type": response.headers.get("x-billed-request-type")}


def fetch_page(url: str, key: str) -> dict:
    """One fixed-endpoint REST call. Return only whitelisted billing headers."""
    canonical, _ = canonical_source(url)
    body = {"url": canonical, "format": "json", "solveCaptcha": False,
            "executeJS": False, "requireWSS": False}
    req = urllib.request.Request(API_URL, data=json.dumps(body).encode(), method="POST",
        headers={"Authorization": "Bearer " + key, "Content-Type": "application/json",
                 "Accept": "application/json", "Accept-Encoding": "identity"})
    try:
        response = string_opener(key).open(req, timeout=120)
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


def bounded_fetch_page(url: str, key: str) -> dict:
    """Bound total wall time, including DNS, connection attempts and response reads.

    Socket timeouts alone can restart across connection/read operations. A child
    process lets the coordinator stop the entire request without another fetch.
    The key travels through stdin only, never argv, files or logs.
    """
    proc = subprocess.Popen(
        [sys.executable, str(Path(__file__).resolve()), "--fetch-worker"],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
        text=True,
    )
    try:
        output, _ = proc.communicate(json.dumps({"url": url, "key": key}),
                                     timeout=PROVIDER_DEADLINE_SECONDS)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.communicate()
        return {"status_code": 0, "error": "provider_deadline_exceeded"}
    if proc.returncode:
        return {"status_code": 0, "error": "provider_worker_failed"}
    try:
        result = json.loads(output)
    except (ValueError, UnicodeError):
        return {"status_code": 0, "error": "provider_worker_invalid_output"}
    return result if isinstance(result, dict) else {
        "status_code": 0, "error": "provider_worker_invalid_output"}


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


def image_only_manifest(path: Path) -> tuple[dict, str]:
    value = json.loads(path.read_text())
    canonical, mid = canonical_source(value["source_url"])
    verified = cached_manifest(path.parent, mid)
    if (verified is None or path.name != f"{mid}.public.json" or "/p/" not in canonical
            or any(f.get("kind") != "image" for f in verified["files"])):
        raise CaptureStopped("soundtrack_recheck_requires_verified_image_only_capture")
    return verified, hashlib.sha256(path.read_bytes()).hexdigest()


def soundtrack_recheck(manifest_path: Path, run: str, ledger: Ledger,
                       output_dir: Path, shared_ledger: Path | None = None) -> dict:
    """One explicitly authorized public music lookup; retain photos unchanged."""
    prior, digest = image_only_manifest(manifest_path)
    canonical, mid = canonical_source(prior["source_url"])
    parent = prior["attempt_id"]
    allowances = ledger.report(run)["soundtrack_rechecks"]
    if not any(a["parent_attempt_id"] == parent and a["manifest_sha256"] == digest
               and a["source_url"] == canonical for a in allowances):
        raise CaptureStopped("soundtrack_recheck_not_authorized_for_manifest")
    key = api_key()
    if uses_https_proxy(key) and not ledger.access_checked(run):
        raise CaptureStopped("string_access_check_required")
    attempt = ledger.reserve(run, mid, recovery_of=parent, recovery_source_url=canonical)
    try:
        receipt = bounded_fetch_page(canonical, key)
    except Exception:
        ledger.hold("provider_unexpected_failure")
        raise CaptureStopped("provider_unexpected_failure") from None
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
        # Private source evidence permits offline parser repair without another
        # paid request. It is excluded from both usage logs and Git.
        write_json(output_dir / f"{mid}.source.json", {"data": receipt["data"]})
        media = extract_media(receipt["data"], mid)
        audio = media.get("audio") or {}
        result = {"source_id": mid, "source_url": canonical, "run_id": run,
                  "attempt_id": attempt, "recovery_of": parent,
                  "purpose": "user_directed_carousel_soundtrack_recheck",
                  "checked_at": datetime.now(timezone.utc).isoformat(),
                  "personal_instagram_credentials_used": False,
                  "source_response": str((output_dir / f"{mid}.source.json").resolve()),
                  "audio_metadata": {k: v for k, v in audio.items() if k != "url"},
                  "audio_available": bool(audio.get("url")), "files": []}
        if audio.get("url"):
            dest = output_dir / f"{mid}.music-reference.m4a"
            download_asset(audio["url"], dest)
            probe = validate_media(dest, require_audio=True, full_decode=True)
            result["files"].append({"path": str(dest.resolve()), "kind": "audio",
                                    "role": "music_reference", "probe": probe,
                                    "sha256": hashlib.sha256(dest.read_bytes()).hexdigest()})
            ledger.mark_media(attempt, usable=True)
        else:
            result["remaining"] = "No public soundtrack asset exposed; user audio still missing"
        write_json(output_dir / f"{mid}.soundtrack-check.json", result)
    except Exception:
        ledger.hold("media_capture_or_validation_failed")
        raise CaptureStopped("media_capture_or_validation_failed") from None
    return dict(result, usage=ledger.report(run))


def capture(url: str, run: str, ledger: Ledger, directory: Path, shared_ledger: Path | None = None,
            recovery_of: int | None = None) -> dict:
    canonical, mid = canonical_source(url)
    cached = cached_manifest(directory, mid)
    if cached:
        if "/reel/" in canonical and not any(f.get("kind") == "video" for f in cached["files"]):
            raise CaptureStopped("cached_reel_video_missing")
        return {"cached": True, "manifest": str(directory / f"{mid}.public.json"), "source_id": mid,
                "usage": ledger.report(run)}
    key = api_key()  # Missing credentials must not consume a reservation.
    if uses_https_proxy(key) and not ledger.access_checked(run):
        raise CaptureStopped("string_access_check_required")
    if recovery_of is None:
        recovery_of = (ledger.unauthenticated_retry_for(mid) or ledger.lost_capture_retry_for(mid)
                       or ledger.provider_error_retry_for(mid))
    attempt = ledger.reserve(run, mid, recovery_of=recovery_of,
                             recovery_source_url=canonical if recovery_of is not None else None)
    try:
        receipt = bounded_fetch_page(canonical, key)
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
    return capture_media(receipt, canonical, mid, attempt, run, ledger, directory, recovery_of)


def capture_media(receipt, canonical, mid, attempt, run, ledger, directory, recovery_of=None, hold_attempt=None):
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
                    "attempt_id": attempt, "recovery_of": recovery_of,
                    "caption": media.get("caption", ""), "files": files,
                    "capture_complete": True, "personal_instagram_credentials_used": False,
                    "audio_metadata": {k:v for k,v in (media.get("audio") or {}).items() if k != "url"},
                    "soundtrack_note": "An image-only capture does not establish carousel soundtrack availability."}
        write_json(directory / f"{mid}.public.json", manifest)
        ledger.mark_media(attempt, usable=True)
    except Exception as exc:
        # The page was fetched and charged: keep it, and say what it was, so the
        # cause can be read offline without another paid request.
        try:
            directory.mkdir(parents=True, exist_ok=True)
            write_json(directory / f"{mid}.source.json", {"data": receipt.get("data")})
        except Exception:
            pass
        try:
            (directory / f"{mid}.capture-error.txt").write_text(
                f"{type(exc).__name__}: {exc}\n"
                f"page: {describe_source(receipt.get('data'), mid)}\n", encoding="utf-8")
        except OSError:
            pass
        if hold_attempt is None:
            ledger.hold("media_capture_or_validation_failed")
        else:
            ledger.hold_retained_transport_failure(hold_attempt)
        raise CaptureStopped("media_capture_or_validation_failed") from None
    return {"cached": False, "manifest": str(directory / f"{mid}.public.json"), "source_id": mid,
            "files": len(files), "usage": ledger.report(run)}


def recover_retained_transport(run, attempt_id, url, evidence_path, ledger, directory):
    canonical, mid = canonical_source(url)
    evidence = json.loads(evidence_path.read_text())
    page = directory / f"{mid}.source.json"
    if hashlib.sha256(page.read_bytes()).hexdigest() != evidence.get("retained_page_sha256"):
        raise CaptureStopped("retained_page_hash_mismatch")
    receipt = json.loads(page.read_text())
    extract_media(receipt["data"],mid)
    if os.environ.get("STRING_MEDIA_USE_HTTPS_PROXY") != "1":
        raise CaptureStopped("reviewed_managed_media_transport_required")
    ledger.consume_retained_transport_recovery(run,attempt_id,canonical,evidence_path)
    original = next(a for a in ledger.report()["attempt_records"] if a["attempt_id"] == attempt_id)
    result = capture_media(receipt,canonical,mid,attempt_id,original["run_id"],ledger,directory,hold_attempt=attempt_id)
    manifest_path = Path(result["manifest"])
    manifest = json.loads(manifest_path.read_text())
    manifest["media_recovery"] = {"authorization_run":run,"original_attempt_id":attempt_id,
                                  "method":"retained exact-source page; no additional String fetch"}
    write_json(manifest_path,manifest)
    result.update(reused_retained_page=True,additional_string_requests=0,recovered_original_attempt=attempt_id)
    return result


def process_local(manifest_path: Path, model: str = "small", frame_interval: str = "1", skip_whisper: bool = False) -> dict:
    from frame_extract import frame_extract
    from tooling import extract_audio_aac, has_audio_stream, ocr_to_file, probe_duration, warn_ollama
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
            # Reels over two minutes still get frames (every 2s): on-screen text,
            # charts and products matter as much there (2026-10-08).
            long_video = probe_duration(media) > 120
            result = frame_extract(media, frames, "2" if long_video else frame_interval, skip_long=False)
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
    init.add_argument("--budget-usd", help="Override the approved per-run estimate (maximum $1)")
    init.add_argument("--max-requests", type=int, default=30)
    init.add_argument("--max-request-usd", default="0.006")
    init.add_argument("--min-gap-seconds", type=float, default=15)
    init.add_argument("--acknowledge-incident", action="store_true", required=True)
    fetch = sub.add_parser("fetch", help="One metered public fetch, then anonymous validated media; no retries")
    fetch.add_argument("url")
    fetch.add_argument("--run", required=True)
    fetch.add_argument("--recover-attempt", type=int,
                       help="One reviewed exact-source recovery of a paid request with a lost local response,"
                            " or the attempt an authorized capture retry repeats")
    fetch.add_argument("--downloads", type=Path, default=downloads_dir())
    fetch.add_argument("--shared-ledger", type=Path,
                       default=Path(load_local_env()["STRING_USAGE_LEDGER"]).expanduser()
                       if load_local_env().get("STRING_USAGE_LEDGER") else None)
    access = sub.add_parser("check-access",
                            help="One free authenticated read to confirm String accepts the key; no page fetch")
    access.add_argument("--run", help="The run this check is for (fetches in proxy-injected mode need one)")
    report = sub.add_parser("usage", help="Read durable usage and any hold")
    report.add_argument("--run")
    reconcile = sub.add_parser("reconcile-portal", help="Offline, audited portal charge reconciliation; does not resume")
    reconcile.add_argument("--run", required=True)
    reconcile.add_argument("--evidence", type=Path, required=True)
    reconcile.add_argument("--review-note", required=True)
    price_list = sub.add_parser("settle-from-price-list",
                                help="Offline: record listed prices for a clean run when the portal is unreachable")
    price_list.add_argument("--run", required=True)
    price_list.add_argument("--review-note", required=True)
    resume = sub.add_parser("resume-reviewed-timeout", help="Release only a fully reconciled timeout hold")
    resume.add_argument("--run", required=True)
    resume.add_argument("--review-note", required=True)
    auth_resume = sub.add_parser("resume-reviewed-unauthenticated",
                                 help="Release a hold from String refusing the key (HTTP 401), after a passing check-access")
    auth_resume.add_argument("--run", required=True)
    auth_resume.add_argument("--review-note", required=True)
    unattended = sub.add_parser("resume-unattended",
                                help="Release a hold that can't mean a billing problem (a timeout, crash, busy "
                                     "String, dead post or fast spending) after a recent passing check-access; "
                                     "failed requests count at their full reservation")
    unattended.add_argument("--run", required=True)
    unattended.add_argument("--review-note", required=True)
    media_resume = sub.add_parser("resume-reviewed-media-failure",
                                  help="Release one reviewed media-validation hold (at most two per run)")
    media_resume.add_argument("--run", required=True)
    media_resume.add_argument("--source", required=True)
    media_resume.add_argument("--review-note", required=True)
    recovery = sub.add_parser("authorize-lost-response-recovery",
                              help="Offline one-time allowance for a portal-confirmed paid response lost locally")
    recovery.add_argument("--run", required=True)
    recovery.add_argument("--attempt-id", type=int, required=True)
    recovery.add_argument("--review-note", required=True)
    authorize_music = sub.add_parser("authorize-soundtrack-recheck",
                                    help="Offline one-time allowance after an explicit user request")
    authorize_music.add_argument("--run", required=True)
    authorize_music.add_argument("--manifest", type=Path, required=True)
    authorize_music.add_argument("--user-direction", required=True)
    retry = sub.add_parser("authorize-capture-retry",
                           help="Offline one-time retry of a reviewed failed media capture, on the user's request")
    retry.add_argument("--run", required=True)
    retry.add_argument("--url", required=True)
    retry.add_argument("--user-direction", required=True)
    lost = sub.add_parser("authorize-lost-capture-retry",
                          help="Offline one-time re-request of a paid, usable capture whose files were lost"
                               " before filing, on the user's request; the source's next capture uses it")
    lost.add_argument("--url", required=True)
    lost.add_argument("--user-direction", required=True)
    perr = sub.add_parser("authorize-provider-error-retry",
                          help="Offline one-time re-request of a source whose only request got a String 5xx"
                               " (provider_error), on the user's request; the source's next capture uses it")
    perr.add_argument("--url", required=True)
    perr.add_argument("--user-direction", required=True)
    transport = sub.add_parser("authorize-transport-recovery",help="Explicit audited Cloud transport recovery")
    transport.add_argument("--run",required=True)
    transport.add_argument("--attempt-id",type=int,required=True)
    transport.add_argument("--url",required=True)
    transport.add_argument("--user-direction",required=True)
    transport.add_argument("--evidence",type=Path,required=True)
    retained = sub.add_parser("recover-retained-transport",help="One authorized anonymous media completion; no String fetch")
    retained.add_argument("--run",required=True)
    retained.add_argument("--attempt-id",type=int,required=True)
    retained.add_argument("--url",required=True)
    retained.add_argument("--evidence",type=Path,required=True)
    retained.add_argument("--downloads",type=Path,default=downloads_dir())
    music = sub.add_parser("soundtrack-recheck", help="One authorized public music lookup; no photo re-downloads")
    music.add_argument("--run", required=True)
    music.add_argument("--manifest", type=Path, required=True)
    music.add_argument("--output-dir", type=Path, required=True)
    music.add_argument("--shared-ledger", type=Path, default=fetch.get_default("shared_ledger"))
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
                budget = args.budget_usd or recommended_run_budget_usd(
                    args.max_requests, args.max_request_usd)
                ledger.create_run(args.run, budget, max_requests=args.max_requests,
                                  max_request_usd=args.max_request_usd, min_gap_seconds=args.min_gap_seconds)
                result = ledger.report(args.run)
            elif args.command == "usage":
                result = ledger.report(args.run)
            elif args.command == "check-access":
                receipt = check_access(api_key())
                result = ledger.record_access_check(receipt["status_code"],
                                                    receipt.get("billed_request_type"), args.run)
                if not result["access_ok"]:
                    print(json.dumps({"stopped": True, "reason": "string_rejected_access_check",
                                      **result}, indent=2))
                    return 3
            elif args.command == "resume-reviewed-unauthenticated":
                result = ledger.resume_reviewed_unauthenticated(args.run, args.review_note)
            elif args.command == "resume-unattended":
                result = ledger.resume_unattended(args.run, args.review_note)
            elif args.command == "reconcile-portal":
                result = ledger.reconcile_portal(args.run, args.evidence, args.review_note)
            elif args.command == "settle-from-price-list":
                result = ledger.settle_from_price_list(args.run, args.review_note)
            elif args.command == "resume-reviewed-timeout":
                result = ledger.resume_reviewed_timeout(args.run, args.review_note)
            elif args.command == "resume-reviewed-media-failure":
                result = ledger.resume_reviewed_media_failure(args.run, args.source, args.review_note)
            elif args.command == "authorize-lost-response-recovery":
                result = ledger.authorize_lost_response_recovery(args.run, args.attempt_id,
                                                                 args.review_note)
            elif args.command == "authorize-soundtrack-recheck":
                prior, digest = image_only_manifest(args.manifest)
                result = ledger.authorize_soundtrack_recheck(args.run, prior["attempt_id"],
                            prior["source_url"], digest, args.user_direction)
            elif args.command == "authorize-transport-recovery":
                canonical,_ = canonical_source(args.url)
                result = ledger.authorize_transport_recovery(args.run,args.attempt_id,canonical,args.user_direction,args.evidence)
            elif args.command == "recover-retained-transport":
                result = recover_retained_transport(args.run,args.attempt_id,args.url,args.evidence,ledger,args.downloads)
            elif args.command == "authorize-capture-retry":
                canonical, mid = canonical_source(args.url)
                prior = [a for a in ledger.report()["attempt_records"]
                         if a["source_id"] == mid and a["recovery_of"] is None]
                if len(prior) != 1:
                    raise CaptureStopped("capture_retry_needs_exactly_one_original_attempt")
                result = ledger.authorize_capture_retry(args.run, prior[0]["attempt_id"], canonical,
                                                        args.user_direction)
            elif args.command == "authorize-lost-capture-retry":
                canonical, mid = canonical_source(args.url)
                prior = [a for a in ledger.report()["attempt_records"]
                         if a["source_id"] == mid and a["recovery_of"] is None]
                if len(prior) != 1:
                    raise CaptureStopped("lost_capture_retry_needs_exactly_one_original_attempt")
                result = ledger.authorize_lost_capture_retry(prior[0]["attempt_id"], canonical,
                                                             args.user_direction)
            elif args.command == "authorize-provider-error-retry":
                canonical, mid = canonical_source(args.url)
                prior = [a for a in ledger.report()["attempt_records"]
                         if a["source_id"] == mid and a["recovery_of"] is None]
                if len(prior) != 1:
                    raise CaptureStopped("provider_error_retry_needs_exactly_one_original_attempt")
                result = ledger.authorize_provider_error_retry(prior[0]["attempt_id"], canonical,
                                                               args.user_direction)
            elif args.command == "soundtrack-recheck":
                result = soundtrack_recheck(args.manifest, args.run, ledger, args.output_dir,
                                             args.shared_ledger)
            else:
                result = capture(args.url, args.run, ledger, args.downloads, args.shared_ledger,
                                 recovery_of=args.recover_attempt)
        print(json.dumps(result, indent=2))
        return 0
    except (CaptureStopped, MediaError, UsageBlocked) as exc:
        print(json.dumps({"stopped": True, "reason": str(exc),
                          "wait_seconds": getattr(exc, "wait_seconds", 0),
                          "usage": ledger.report() if ledger else None}, indent=2))
        return 3


if __name__ == "__main__":
    if sys.argv[1:] == ["--fetch-worker"]:
        try:
            request = json.load(sys.stdin)
            print(json.dumps(fetch_page(request["url"], request["key"])))
        except Exception:
            # No provider body, key or unfiltered exception on worker stdout.
            print(json.dumps({"status_code": 0, "error": "provider_worker_failed"}))
    else:
        raise SystemExit(main())
