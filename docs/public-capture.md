# Public Instagram capture with String

This is the supported replacement acquisition path after the
[personal-account incident](instagram-account-incident.md). It never reads
Instagram cookies, uses the signed-in browser, or clears the authenticated
extractor hold. The user must authorize this capture approach and its budget.
The original `reel`, `carousel` and `batch` paths remain held; they are not
fallbacks when public retrieval fails. Only the acquisition step changes:
Notion source/date/prompt, local analysis, Obsidian filing, project references
and permanent music audio still use the existing workflow.

## Initialize an authorized run

String credentials come from `STRING_AI_API_KEY` or the existing macOS Keychain
generic password service `string-ai`. Never paste a key into CLI arguments,
logs or Git. Only the fixed `https://request.usestring.ai/v1/fetch` endpoint
receives the String key. Instagram receives no user account credentials.
The credential must already exist; these commands create no account or key.

```bash
# Example only: choose a budget the user has actually approved.
python scripts/igx.py public-capture init \
  --run approved-queue-run --budget-usd 1 --max-requests 30 \
  --acknowledge-incident

python scripts/igx.py public-capture fetch \
  'https://www.instagram.com/reel/SHORTCODE/' --run approved-queue-run

python scripts/igx.py public-capture usage --run approved-queue-run
```

`--shared-ledger PATH` on `fetch` also appends to an existing String-wide JSONL
ledger, using its `ts/caller/url/billed/plan/via` schema. `STRING_USAGE_LEDGER` in
`local.env` or the environment supplies the default. Configure it when the
machine has a central spend report. This additional log is not the budget guard.
The guard is a durable SQLite ledger at
`~/.local/state/ig-yt-x-knowledge-extract/string-usage.sqlite3`, shared by clones
and processes. `--ledger` exists for offline tests; never change its path to
evade a hold. Production runs must use the same ledger.

## Automatic stops and accounting

- Reserve the full per-request allowance **before** contacting String. Default
  maximum is $0.006/request; run budget and request count are immutable.
- Only one outstanding paid request across the ledger. An interrupted request
  retains its reservation and prevents another request until explicitly
  investigated; there is no automatic reset/retry command.
- Default spacing is at least 10 seconds, with a $0.03 allowance per rolling
  60 seconds. A pacing refusal makes no request. A spending-cap/burn-rate
  violation latches a hold. These are spend controls, not assurances that any
  platform permits a particular retrieval rate.
- Stop on missing/unknown billing class, non-success/error response, any charge
  above reservation, or a price increase exceeding four times the first verified
  class. No new run name clears a hold.
- CAPTCHA solving, JavaScript forcing, browser actions and remote browser
  WebSocket sessions are disabled. There is no cookie/browser/account/proxy
  fallback and no retry after denial/challenge/rate limit.
- Reuse hash-verified cached captures without a provider request. A previously
  attempted source is not silently fetched again under a different run.

Every report separates the provider-reported request class, the price-derived
estimate, known actual charges, unknown actual-charge count and conservative
reserved cost envelope. Published Starter prices checked September 27, 2026:
standard fetch $0.0003, premium fetch $0.003, standard browser $0.0015, premium
browser $0.006. These are not invoice measurements or proof of a free allowance.
Do not mark an absent cost as zero. The fixed monthly subscription is separate.
Reconcile with the portal; the API adapter does not change billing plans,
auto-top-up or free-credit settings. [Provider pricing](https://portal.usestring.ai/docs/get-started/pricing).

The currently exposed MCP result may omit String's outer billing header. The
production adapter uses REST and captures `x-billed-request-type` from the
provider response before any media processing. It does not infer that field
from destination HTML. Request IDs are sanitized; bodies, cookies, keys and
temporary signed media URLs are excluded from usage records.

## Validate and process locally

One public page fetch must return an exact shortcode match. Related/recommended
posts do not count. Only expected HTTPS Instagram CDN hosts may supply assets;
downloads use no Cookie/Authorization headers, no proxy environment, no
redirects and no retries. Size limits, content type, duration, required audio
and full decoding are checked before a capture manifest is finalized. A reel
thumbnail alone cannot pass. ffprobe/ffmpeg validation only allows local file
protocols, so a disguised playlist cannot fetch additional URLs.

Successful output includes `SHORTCODE.public.json`, caption and media files in
`downloads/`. The manifest stores file hashes, probes, provider attempt and
source provenance, without temporary download URLs. Then:

```bash
python scripts/igx.py public-capture process downloads/SHORTCODE.public.json
# Music-only material can omit speech transcription:
python scripts/igx.py public-capture process downloads/SHORTCODE.public.json --skip-whisper
```

Processing is local and can proceed while network collection is held. Agent
analysis can run concurrently; keep paid retrieval with one coordinator.
Subagents must not independently invoke String, Instagram or other paid
providers during the queue run. Hand each filer a bounded manifest and sources.

Image-only carousel capture does not establish soundtrack retrieval. An exposed
separate music asset is labeled `music_reference`: it may be a track/preview,
not the synchronized post excerpt. Preserve that distinction. For a music save,
filing is incomplete until the requested usable audio is retained outside
prunable downloads with searchable context. Follow [filing](obsidian-filing.md)
and [project handoffs](project-handoffs.md).

Only mark Notion `noted` after the actual note, media and required project
references are verified. Record the note against the ledger attempt with
`Ledger.mark_filed`; caption-only or blocked cases must state their limits.
At each batch boundary report requests, estimates/unknowns, usable captures,
completed notes and remaining items. At the end reconcile both usage ledgers
and the account portal. Never call an incomplete queue cleared.
