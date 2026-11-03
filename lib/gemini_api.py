"""Gemini API calls shared by the cloud commands (youtube-gemini, note-gemini).

One streaming request per try: the plain generateContent endpoint can hold a busy
request open with no answer, while streamGenerateContent returns a 503 within seconds.
An answer counts only when Gemini ends it with finishReason STOP and the caller's
check accepts it. A busy, cut-off or rejected answer is retried once after a pause,
then the next model is tried. A model whose free daily allowance is used up, or that
stayed busy or silent through both tries on two items, is skipped for the rest of the
run; the limit Google reports is kept in the tries.

Billing guard. A key has no billing of its own: it is free while its Google project has
no billing account, and past the free allowance Google refuses requests (429) rather
than charging (ai.google.dev/gemini-api/docs/billing, read 2026-11-03). Charges start
only if someone links billing to that project. `tier_check` finds out before a run:
it asks a model that has no free tier, which a free project refuses with quotas named
"...-FreeTier" and a billed one answers. During a run, a 402 (billing linked, no
credit) or a quota that isn't a free-tier one raises `BillingLinked`, and callers stop
using Gemini.

Nothing here returns or prints the key.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
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
# No free tier on Google's pricing page (2026-11-03). On 2026-11-03 the cloud key's
# project got 429 with four "-FreeTier" quotas from both: proof it was on the free tier.
PROBE_MODELS = ("gemini-3.1-pro-preview", "gemini-4-argon")
PROBE_BODY = {"contents": [{"parts": [{"text": "Reply OK."}]}], "generationConfig": {"maxOutputTokens": 1}}


class BillingLinked(RuntimeError):
    """Google's answer shows the key's project has billing, so further requests could be charged."""


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


def quota_ids(raw: str) -> list[str]:
    """The quota IDs a refusal names, e.g. GenerateRequestsPerDayPerProjectPerModel-FreeTier."""
    try:
        details = json.loads(raw).get("error", {}).get("details", []) or []
    except (ValueError, AttributeError):
        return []
    return [str(v.get("quotaId", "")) for d in details if str(d.get("@type", "")).endswith("QuotaFailure")
            for v in d.get("violations", []) or [] if v.get("quotaId")]


def billing_sign(result: dict) -> str | None:
    """Why this answer shows billing on the key's project, or None."""
    if result.get("status") == 402:
        return "402 Payment Required: the key's project has billing linked"
    paid = [q for q in result.get("quota_ids") or [] if "FreeTier" not in q]
    if result.get("status") == 429 and paid:
        return f"quota {paid[0]} is not a free-tier quota"
    return None


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
        return {"status": exc.code, "error": detail, "retry_after": wait, "quota_ids": quota_ids(raw)}
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
    putting it in `skip` too. Raises `BillingLinked` on any sign of billing.
    """
    tries = []
    for model in models:
        if skip is not None and model in skip:
            continue
        unanswered = 0
        for attempt in (1, 2):
            result = stream(model, key, body, opener=opener, deadline=deadline)
            sign = billing_sign(result)
            if sign:
                raise BillingLinked(f"{model}: {sign}")
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


def tier_check(key: str, models=PROBE_MODELS, *, opener=None) -> dict:
    """Is the key's project still on the free tier? One tiny request per probe model at most.

    Returns {"tier": "free" | "billed" | "unknown", "model", "detail"}. A probe model has no
    free tier, so a free project gets a 429 naming "-FreeTier" quotas (nothing is charged),
    while a billed project answers (a fraction of a cent) or refuses with a paid sign.
    "unknown" means no probe model gave a clear answer (retired, busy or silent).
    """
    notes = []
    for model in models:
        result = stream(model, key, PROBE_BODY, opener=opener, deadline=60)
        sign = billing_sign(result)
        if sign or result.get("status") == 200:
            return {"tier": "billed", "model": model,
                    "detail": sign or "a model with no free tier answered, so billing is linked"}
        if result.get("status") == 429 and any("FreeTier" in q for q in result.get("quota_ids") or []):
            return {"tier": "free", "model": model, "detail": "refused with free-tier quotas"}
        notes.append(f"{model}: {result.get('status')} {result.get('error') or ''}".strip())
    return {"tier": "unknown", "model": None, "detail": "; ".join(notes)[:300]}


def main(argv: list[str] | None = None) -> int:
    """`igx.py gemini-tier`: exit 0 free tier, 4 billing linked (don't use Gemini), 5 unknown, 2 no key."""
    parser = argparse.ArgumentParser(description="Check that the Gemini key's project is still on the free tier.")
    parser.add_argument("--key-env", default=DEFAULT_KEY_ENV,
                        help="Environment variable holding the Gemini key (default: %(default)s)")
    args = parser.parse_args(argv)
    key = os.environ.get(args.key_env, "").strip()
    if not key:
        print(json.dumps({"ok": False, "error": f"{args.key_env} is not set"}))
        return 2
    result = tier_check(key)
    print(json.dumps(result))
    if result["tier"] == "billed":
        print("Billing is linked to the Gemini key's project: don't use Gemini this run.", file=sys.stderr)
    return {"free": 0, "billed": 4}.get(result["tier"], 5)
