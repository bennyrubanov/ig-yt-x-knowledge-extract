"""Gemini API calls shared by the cloud commands (youtube-gemini, note-gemini).

One streaming request per try: the plain generateContent endpoint can hold a busy
request open with no answer, while streamGenerateContent returns a 503 within seconds.
An answer counts only when Gemini ends it with finishReason STOP and the caller's
check accepts it. A busy, cut-off or rejected answer is retried once after a pause,
then the next model is tried. A model whose free daily allowance is used up, or that
stayed busy or silent through both tries on two items, is skipped for the rest of the
run; the limit Google reports is kept in the tries.

Nothing here returns or prints the key.
"""
from __future__ import annotations

import json
import re
import time
import urllib.error
import urllib.request

API = "https://generativelanguage.googleapis.com/v1beta/models/{model}:streamGenerateContent?alt=sse"
# Free-tier order, tested 2026-10-31: Flash-Lite answers fastest; 3.5 and 3.6 Flash
# answered while 3.8 and 3.7 Flash returned 503 "high demand" for hours.
DEFAULT_MODELS = ("gemini-3.5-flash-lite", "gemini-3.5-flash", "gemini-3.6-flash", "gemini-3.8-flash")
DEFAULT_KEY_ENV = "GEMINI_PERSONAL_API_KEY"
RETRY_STATUS = {429, 500, 503}
DEADLINE_SECONDS = 300
MAX_RETRY_WAIT = 90


def error_summary(raw: str) -> tuple[str, float | None]:
    """Short text for a failed request, plus Google's suggested wait in seconds.

    Keeps the status, the quota that was hit and its limit (for example
    "RESOURCE_EXHAUSTED; quota GenerateRequestsPerDayPerProjectPerModel-FreeTier limit 20"),
    so a run report can say which allowance ran out.
    """
    try:
        error = json.loads(raw).get("error", {})
    except (ValueError, AttributeError):
        return raw.strip()[:300], None
    parts = [str(error.get("status") or error.get("code") or "error")]
    wait = None
    for detail in error.get("details", []) or []:
        kind = str(detail.get("@type", ""))
        if kind.endswith("QuotaFailure"):
            for violation in detail.get("violations", []) or []:
                parts.append(f"quota {violation.get('quotaId', '?')} limit {violation.get('quotaValue', '?')}")
        elif kind.endswith("RetryInfo"):
            match = re.fullmatch(r"([\d.]+)s", str(detail.get("retryDelay", "")))
            if match:
                wait = float(match[1])
    if len(parts) == 1:
        parts.append(str(error.get("message", ""))[:200])
    return "; ".join(p for p in parts if p)[:300], wait


def stream(model: str, key: str, body: dict, *, opener=None, deadline: float = DEADLINE_SECONDS) -> dict:
    """One streaming request. Returns status, text, finishReason and usage; never the key."""
    opener = opener or urllib.request.urlopen
    req = urllib.request.Request(API.format(model=model), data=json.dumps(body).encode(),
                                 method="POST", headers={"Content-Type": "application/json",
                                                         "x-goog-api-key": key})
    started = time.time()
    text, finish, usage = [], None, None
    try:
        with opener(req, timeout=deadline) as response:
            for raw in response:
                line = raw.decode("utf-8", "replace").strip()
                if not line.startswith("data:"):
                    continue
                event = json.loads(line[5:])
                for candidate in event.get("candidates", []):
                    finish = candidate.get("finishReason", finish)
                    for part in candidate.get("content", {}).get("parts", []):
                        if "text" in part and not part.get("thought"):
                            text.append(part["text"])
                usage = event.get("usageMetadata", usage)
                if time.time() - started > deadline:
                    return {"status": 200, "text": "".join(text), "finish": finish,
                            "usage": usage, "error": "deadline"}
    except urllib.error.HTTPError as exc:
        try:
            raw = exc.read(4000).decode("utf-8", "replace")
        except OSError:
            raw = ""
        finally:
            exc.close()
        detail, wait = error_summary(raw)
        return {"status": exc.code, "error": detail, "retry_after": wait}
    except (OSError, ValueError) as exc:
        return {"status": None, "error": type(exc).__name__}
    return {"status": 200, "text": "".join(text), "finish": finish, "usage": usage}


def daily_allowance_used_up(result: dict) -> bool:
    return result.get("status") == 429 and "PerDay" in (result.get("error") or "")


def generate(body: dict, key: str, models=DEFAULT_MODELS, *, accept=None, pause_seconds: float = 20,
             opener=None, sleep=time.sleep, skip: set | None = None, strikes: dict | None = None,
             deadline: float = DEADLINE_SECONDS) -> dict:
    """Try each model in order until one returns a complete answer that `accept` takes.

    `accept(text)` returns None when the answer is usable, else a short reason; a
    rejected answer is treated like a cut-off one. Pass the same `skip` set and `strikes`
    dict to every call in a run: a model whose daily allowance ran out goes straight into
    `skip`, and one that was busy or silent on both tries gets a strike, two strikes
    putting it in `skip` too.
    """
    tries = []
    for model in models:
        if skip is not None and model in skip:
            continue
        unanswered = 0
        for attempt in (1, 2):
            result = stream(model, key, body, opener=opener, deadline=deadline)
            complete = (result["status"] == 200 and result.get("finish") == "STOP"
                        and not result.get("error") and result.get("text", "").strip())
            problem = accept(result["text"]) if complete and accept else None
            reason = result.get("error") or problem
            tries.append({"model": model, "status": result["status"], "finish": result.get("finish"),
                          "error": reason[:300] if reason else None})
            if complete and not problem:
                return {"ok": True, "model": model, "text": result["text"],
                        "usage": result.get("usage"), "tries": tries}
            if daily_allowance_used_up(result):
                if skip is not None:
                    skip.add(model)
                break
            unanswered += result["status"] in (None, 503)
            if unanswered == 2 and skip is not None and strikes is not None:
                strikes[model] = strikes.get(model, 0) + 1
                if strikes[model] >= 2:
                    skip.add(model)
            retryable = result["status"] in RETRY_STATUS or result["status"] == 200 or result["status"] is None
            if not retryable:
                break
            if attempt == 1:
                sleep(min(max(pause_seconds, result.get("retry_after") or 0), MAX_RETRY_WAIT))
    return {"ok": False, "tries": tries}
