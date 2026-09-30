# Public Instagram capture with String

This is the supported replacement acquisition path after the
[personal-account incident](instagram-account-incident.md). It never reads
Instagram cookies, uses the signed-in browser, or clears the authenticated
extractor hold. The user authorized this capture approach and the per-run
budget policy below.
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
# Fresh bounded run; the budget is calculated from --max-requests.
python scripts/igx.py public-capture init \
  --run approved-queue-run --max-requests 30 \
  --acknowledge-incident

python scripts/igx.py public-capture fetch \
  'https://www.instagram.com/reel/SHORTCODE/' --run approved-queue-run

python scripts/igx.py public-capture usage --run approved-queue-run
```

**Budget resets for each new, bounded run.** The four measured runs through
September 29 cost $0.081/27 requests (26 original sources plus one reviewed
recovery), $0.009/3, $0.024/8 and $0.006/2: $0.003 for every charged request.
The default budget is `max($0.05, round up to $0.05 of $0.009 × planned
requests)`: three times the observed unit cost, and above the $0.006
per-request reservation ceiling. For 2, 3, 8, 26 and 30 planned requests this
gives $0.05, $0.05, $0.10, $0.25 and $0.30, respectively. The hard ceiling is
**$1 per run**, including an explicit `--budget-usd` override. Larger planned
batches must be split into bounded runs of at most 111 requests. This is
headroom, not a prediction or a prepaid charge; request count and the
$0.006 per-request ceiling remain separate limits. Review the provider's
current pricing and actual portal charges before changing these assumptions.

The ledger keeps lifetime actual spend visible, but it is not a cumulative
authorization limit. Use a fresh run ID for new work; old run limits remain
immutable. A new run requires all earlier attempts to be settled **and their
actual charges reconciled**. It cannot clear an unresolved provider, billing,
media, or fast-spending hold. Do not create a fresh run to silently retry an already
attempted source or work around a safety hold. The user-directed carousel music
recheck below is a narrow, separately audited exception.

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
  60 seconds. A pacing refusal makes no request. The run budget and request
  count stop that run; fast spending still latches a global hold. These are
  spend controls, not assurances that any
  platform permits a particular retrieval rate.
- Stop on missing/unknown billing class, non-success/error response, any charge
  above reservation, or a price increase exceeding four times the first verified
  class. No new run name clears a hold.
- A separate request worker enforces a 120-second total wall-clock deadline,
  including connection attempts and response reads. On expiry it is terminated;
  the charge stays unknown, its reservation remains accounted for, and collection
  holds. A socket timeout alone does not provide this total deadline. Do not
  retry the source to discover its charge.
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

### Reviewed portal reconciliation after an interrupted request

If a coordinator was terminated with a request still reserved, first stop its
worker and inspect the signed-in String Usage table. Save a local JSON evidence
file with `evidence_source: "https://portal.usestring.ai/web-access"`, the exact
`run_id`, `total_actual_usd`, and one `rows` entry for **every** attempt in the
run. Each row needs the exact canonical Instagram `url`, `source_id`, portal
`time_local` with timezone, `status_code`, `result`, `portal_type`, and
`actual_usd`. Set `billing_class` to `null` when the portal only says “Fetch”;
the dollar amount does not establish a premium class. Keep the evidence file
private. Reconciliation makes no network request.

```bash
python scripts/igx.py public-capture reconcile-portal \
  --run approved-queue-run --evidence /absolute/private/portal-billing.json \
  --review-note 'Matched every run attempt against the portal Usage table'
python scripts/igx.py public-capture usage --run approved-queue-run
python scripts/igx.py public-capture resume-reviewed-timeout \
  --run approved-queue-run \
  --review-note 'Worker terminated; all attempts charged and reviewed in portal'
```

The first command checks the exact attempt set, source IDs, reservation times,
successful portal status, itemized charges, total and original per-request
ceiling in one transaction. It stores the evidence path and SHA-256 digest,
review note, and the original pending/status/class/cost fields in an audit
table before recording actual charges. Existing REST billing classes remain
unchanged; a class missing from the portal remains unknown. Reconciliation
does not itself release any hold. A later, complete portal snapshot can add
new attempts to the same run; earlier audit entries stay unchanged and any
conflicting charge is rejected.

The second command releases only a `provider_wall_time_exceeded` hold after
all ledger attempts have known actual charges and the interrupted reservation
has been reconciled. Denial, billing anomalies, media failures and other holds
remain blocked. Source deduplication, the approved run budget, request limit,
spacing and burn allowance remain in force. The lost provider response and
media are still unavailable locally; a portal HTTP 200 does not create a
capture or authorize an automatic retry. The shared String JSONL mirror logs
requests only; do not append a reconciliation as another request or infer a
charge from its original billing class. Reconcile that mirror separately with
an audited correction to the original request records.

### One reviewed recovery when a paid response was lost locally

Only after the original interrupted attempt is portal-reconciled and its
timeout hold released, a reviewer may allow **one** extra call for that exact
source if the portal shows HTTP 200 and a real charge but no response or media
was retained locally. It is a fresh paid request, not a free replay. The
original attempt and charge stay in history. The extra call consumes its own
reservation and actual charge within the **same** run budget and burn limits.
It is the sole exception to the original run's request-count limit; normal
sources remain limited by the original `max_requests`.

```bash
python scripts/igx.py public-capture authorize-lost-response-recovery \
  --run approved-queue-run --attempt-id ORIGINAL_ATTEMPT_ID \
  --review-note 'Portal shows paid HTTP 200; worker stopped and local response was lost'
python scripts/igx.py public-capture fetch \
  'https://www.instagram.com/reel/EXACT_ORIGINAL_SHORTCODE/' \
  --run approved-queue-run --recover-attempt ORIGINAL_ATTEMPT_ID
```

Authorization is offline and records an audit note. The fetch requires that
exact prior attempt, run, host and shortcode. A second authorization or second
recovery is rejected even if the recovery fails. All normal billing and media
stops still apply. A cached verified manifest returns locally without using
the allowance. This recovery path never changes the personal-account hold.

The first use of the recovery-capable code migrates the old SQLite attempt
table to a schema with a linked `recovery_of` field and partial uniqueness
indexes. Stop all existing capture coordinators and preserve a backup before
running that version on the production ledger. The migration runs under one
SQLite write transaction, retains attempt IDs and checks foreign keys.

When the portal later shows both the original and recovery charges for one
URL, include `attempt_id` on **both** rows in the complete evidence snapshot.
The timestamp must match each reservation. The reconciliation audit for the
original charge stays unchanged; only new attempt charges are added.

The currently exposed MCP result may omit String's outer billing header. The
production adapter uses REST and captures `x-billed-request-type` from the
provider response before any media processing. It does not infer that field
from destination HTML. Request IDs are sanitized; bodies, cookies, keys and
temporary signed media URLs are excluded from usage records.

### One user-directed carousel soundtrack recheck

If the user explicitly asks to retry a missing carousel song, first check local
evidence and the parser. Photo posts may use `music_metadata.music_info`, whereas
reels use `clips_metadata`. Supporting both fields does not prove that public
HTML exposes either field for a particular post. Music credits and download URLs
must belong to the exact source; never borrow them from recommendations.

After a successful, portal-reconciled image-only capture, an explicit user request
can authorize **one** separate public music lookup. Create a dedicated run with
`max_requests=1`; its usual per-request reserve, budget, pacing, billing and holds
remain enforced. This cannot retry a denial, interrupted request, or failed media
capture, and cannot release a hold. The original attempt and charge remain.

```bash
python scripts/igx.py public-capture init --run approved-music-check \
  --max-requests 1 --acknowledge-incident
python scripts/igx.py public-capture authorize-soundtrack-recheck \
  --run approved-music-check --manifest downloads/SHORTCODE.public.json \
  --user-direction 'Exact user request to resolve or retry this missing song'
python scripts/igx.py public-capture soundtrack-recheck \
  --run approved-music-check --manifest downloads/SHORTCODE.public.json \
  --output-dir exports/private-music-check
```

Authorization records the source, verified manifest hash and user direction in
`soundtrack_rechecks`. The new attempt links its original through `recovery_of`,
with a distinct recheck audit event. It consumes the dedicated run's one request;
the lost-response recovery allowance is not used. No second recheck is permitted,
even under another run name. Existing photos and their manifest stay unchanged.

The lookup retains private public-source evidence for offline diagnosis and
downloads only an exposed soundtrack asset. A missing public asset is a documented
incomplete music request, not completed filing or a reason to try other accounts.
Reconcile its charge in the portal before another run. An MP4 container served as
`video/mp4` may hold audio alone; local probing, audible activity and full decoding
still must pass. A separate track/preview is a `music_reference`, not proof of the
exact synchronized excerpt, rights to sample, or a complete song.

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
