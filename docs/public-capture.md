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
and the free key check (`/v1/sitemap`, read only) receive the String key. Instagram receives no user account credentials.
The credential must already exist; these commands create no account or key.

**A cloud machine whose network proxy adds the key.** Set
`STRING_AI_API_KEY=proxy-injected`. Calls to String's API then go through
`HTTPS_PROXY`, which replaces the placeholder with the real key; otherwise they go
direct, and the proxy never sees them. With any other placeholder, String answers 401.
Instagram CDN downloads use their separate anonymous transport (see managed Cloud media transport below); and String fetches the page
from its own network, so the mode changes nothing Instagram sees. In this mode `fetch`
refuses to reserve anything until a key check has passed since the run was created:

```bash
python scripts/igx.py public-capture init --run approved-queue-run \
  --max-requests 30 --acknowledge-incident
python scripts/igx.py public-capture check-access --run approved-queue-run
```

**Codex or another managed proxy with its own secret placeholder.** Configure
`STRING_AI_API_KEY` as the environment's HTTP/network secret scoped to
`https://request.usestring.ai` and retain the placeholder that environment supplies.
Set `STRING_USE_HTTPS_PROXY=1` for the String commands. This explicitly routes only
String's fixed API calls through inherited `HTTPS_PROXY`; it does not replace the
credential with `proxy-injected`, add a credential, or change network policy.
The host must also be permitted by the environment's network allowlist. A missing
key or proxy stops before any reservation, and paid capture requires a passing
free `check-access --run ...` for that run, just as in `proxy-injected` mode.
The setting is inherited by the fetch worker. Instagram media downloads retain
their separate anonymous client and host/redirect/validation guards.
Never copy a secret into the command, a file or a repository. A configured
placeholder alone does not establish that credential substitution works: verify
`check-access` before fetching. Leave this option unset (or `0`) for direct-key
routing; the existing literal `proxy-injected` mode remains supported.

`check-access` is one free authenticated read (String's crawl-job list, which bills no
request type). It reserves nothing and exits 3 if String refuses the key. A failed check
is only recorded; a check that comes back billed latches a hold.

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
recheck, failed-capture retry, lost-capture re-capture, provider-error retry and empty-capture retry below are
narrow, separately audited exceptions.

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
  retains its reservation and prevents another request until it is investigated
  or released by `resume-unattended` (below), which counts it at its full
  reservation; there is no automatic retry of that source.
- Default spacing is at least 15 seconds, with a $0.03 allowance per rolling
  60 seconds. At the $0.006 reservation that allowance is five requests a minute, so
  the gap is wider than 12 seconds: a run fetching as fast as the gap lets it never
  trips the fast-spending hold (the old 10-second default let a sixth request in and
  held three saves on 2026-11-03). A pacing refusal makes no request. The run budget and request
  count stop that run; fast spending still latches a global hold. These are
  spend controls, not assurances that any
  platform permits a particular retrieval rate.
- Stop on missing/unknown billing class, non-success/error response, any charge
  above reservation, or a price increase exceeding four times the first verified
  class. No new run name clears a hold; the releases below do, each for its own
  kind of hold.
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

### Price-list settlement where the portal can't be opened

A cloud machine can't sign in to String's portal, so `init` would refuse every run after
the first. For a run that finished cleanly, the owner-approved stand-in records each
attempt's charge at the published Starter price of the billing class String reported:

```bash
python scripts/igx.py public-capture settle-from-price-list \
  --run approved-queue-run --review-note 'Cloud run settled at list price'
```

It refuses, and changes nothing, if there is any hold, any pending reservation, or any
attempt that was denied, errored or has no reported class; those runs still need portal
review. Each settled attempt keeps `actual_source: price_list` and an audit row. A later
`reconcile-portal` with the real portal rows replaces the list price with the real
charge. Reconcile against the portal regularly (monthly) on a machine that can open it.

### Reviewed release after String refused the key

If a fetch went out without a working key (for example a cloud run whose code skipped
the key-adding proxy, 2026-10-31), String answers HTTP 401 with no billing class and the
ledger latches `provider_denial`. String identifies the paying account by the key, so a
request it refused as unauthenticated fetched nothing and can't have been charged.
Fix the key, then:

```bash
python scripts/igx.py public-capture check-access
python scripts/igx.py public-capture resume-reviewed-unauthenticated \
  --run approved-queue-run --review-note 'String refused the placeholder key; nothing fetched'
```

It refuses, and changes nothing, unless every hold since the last reviewed release came
from an attempt in that run with status 401, no billing class and no charge, nothing is
pending, and the latest key check since the failure passed. Each refused attempt is
recorded at $0 (`actual_source: unauthenticated`, audit row in
`unauthenticated_retries`), and its source may be requested **once** more in a later
run: `fetch` finds the allowance itself and links the new attempt with `recovery_of`. That
request counts toward its run's request limit.

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

### Reviewed release after one source's media failed

A media or validation failure latches `media_capture_or_validation_failed`
after the paid page fetch has already succeeded, so the hold protects against a
run of useless paid fetches, not against extra spend on that source. The reason
is kept locally in `downloads/{id}.capture-error.txt`. That file has two lines:
the error, and `page: ...`, a one-line summary of the page String returned (its
size, title, canonical path, JSON block counts and what the matching nodes
hold; no media URLs). String's page itself is kept as `downloads/{id}.source.json`,
so the cause can be read offline without another paid request. It is private
source evidence: `downloads/` is gitignored, and the file is never committed,
copied or uploaded. After reviewing them (and
any files already saved), the coordinator may release the hold for that one
source; the user approved this on 2026-10-07 for queue runs:

```bash
python scripts/igx.py public-capture resume-reviewed-media-failure \
  --run approved-queue-run --source SHORTCODE \
  --review-note 'Page fetch 200; 7 slides valid; 8th asset failed validation'
```

The failed attempt must be the ledger's latest, settled with HTTP 200 and with
no other hold since the last reviewed release. It stays recorded as unusable
and is never fetched again. A run allows at most two such releases, so a third
media failure stops it for good. File the source from what was saved and state
the missing item in the note. Unattended queue runs use `resume-unattended`
instead (below), which also covers media failures with a wider limit.

### Unattended release: a run carries on by itself

The owner, 2026-11-03: "I don't want Instagram to get paused very easily and I want you
to be smart about restarting without me". Queue runs that work unattended release the
holds that can't mean a billing problem themselves, after a free key check:

```bash
python scripts/igx.py public-capture usage            # hold_reason and hold_run_id
python scripts/igx.py public-capture check-access --run HOLD_RUN_ID
python scripts/igx.py public-capture resume-unattended --run HOLD_RUN_ID \
  --review-note 'String timed out on SHORTCODE; marked fail, never refetched'
```

Within the night (the hold is under six hours old) it releases:

- a fetch that failed without a billing class with a status that means passing trouble:
  a timeout or lost connection (status 0), 408, 425, 429 or a 5xx, and a crash before
  the fetch settled (`provider_unexpected_failure`);
- a billed fetch that failed (Instagram refused String's fetch, or String's answer
  couldn't be read), and a post whose media failed after its page was fetched (deleted,
  private, or invalid);
- the fast-spending window, once it has passed (it answers `burn_window_still_open`
  with the seconds to wait).

A run allows at most eight such releases, and never the third failed request in a row,
counted across runs: that points at something wider than one post (String down, or a
page change that broke the parser), so the run stops Instagram for the night. A hold six
hours old or more was latched on an earlier night, so the next night's run may restart
once, without those two limits; the restart also covers a request String itself refused
(another 4xx). Its first request is then a probe: if that fails too, it's one more
failure in a row, and the night stops after spending one request, not three.

It never releases a billing anomaly: a 2xx answer without a known billing class, an
unexpected class, a price jump, a charge above the reservation, a billed key check, or
a failed usage log. Those wait for a person. A 401 (String refused the key) has its own
release, above. The key check must come after the hold and within the last hour.

Each failed or interrupted request is counted at its full reservation
(`actual_source: unattended_max`, an `unattended_max_charge` event), which a later
`reconcile-portal` may replace; successful fetches in the run are left for
`settle-from-price-list`. The release is recorded in `reviews` as `unattended_resume`
(within the night) or `unattended_restart` (a later night) with its note. Failed sources
stay attempted and are never fetched again, so mark their saves failed with the reason.

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
capture (a failed media capture has its own retry below), and cannot release a hold. The original attempt and charge remain.

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

### One user-directed retry after a failed media capture

If the user explicitly asks to retry a source whose paid page fetch succeeded but
yielded no usable media, an offline authorization can allow **one** more request for
that exact source. The original attempt must be settled with HTTP 200 and no usable
media, released through the reviewed media-failure path, and portal-reconciled. Create
a dedicated run with `max_requests=1`; its reserve, budget, pacing, billing and holds
stay in force. The authorization records the source URL and the user's own words in
`capture_retries`, and the retry links its original through `recovery_of`. No second
retry is permitted, even under another run name, and the retry never releases a hold:
a new media failure latches the usual hold, released the usual reviewed way.

```bash
python scripts/igx.py public-capture init --run approved-retry \
  --max-requests 1 --acknowledge-incident
python scripts/igx.py public-capture authorize-capture-retry --run approved-retry \
  --url 'https://www.instagram.com/p/SHORTCODE/' \
  --user-direction 'Exact user request to retry this post once'
python scripts/igx.py public-capture fetch 'https://www.instagram.com/p/SHORTCODE/' \
  --run approved-retry --recover-attempt ORIGINAL_ATTEMPT_ID
```

Reconcile its charge in the portal before another run. The original failed attempt
and its charge stay in history.

### One user-directed re-capture after the files were lost before filing

A paid capture can succeed and then be lost before any note is filed: on 2026-11-09 a
Codex cloud run captured two saves, then paused with their files in its own workspace,
which nothing else can reach (raw media never goes through GitHub). If the user
explicitly asks to capture such a source again, an offline authorization allows **one**
more request for it. The original attempt must be settled with HTTP 200, usable media
and a reconciled charge (portal or price list), and never filed. The authorization
records the source URL and the user's own words in `lost_capture_retries`. It makes no
request and needs no dedicated run: the source's next ordinary `fetch`, in any later
run, finds the allowance and uses it, counted as one of that run's planned requests
with its usual reserve, budget, pacing and holds, and `recovery_of` links it to the
original. No second allowance or re-request is permitted.

```bash
python scripts/igx.py public-capture authorize-lost-capture-retry \
  --url 'https://www.instagram.com/p/SHORTCODE/' \
  --user-direction 'Exact user request to capture this post again'
```

A source the ledger has never seen (its first request happened in a workspace whose
ledger was lost) needs no allowance; its first request here is an ordinary one. Record
the lost request beside the ledger instead, from the portal's request log.

### One user-directed retry after String answered a 5xx

When String itself fails a request (for example HTTP 502, recorded as `provider_error`),
the attempt settles at the full reservation and the source is marked failed. If the user
explicitly asks to try that source again, an offline authorization allows **one** more
request. The source's only attempt must be settled with a 5xx status and the
`provider_error` code, with no usable media and no filed note, and no other retry or
recovery may exist for it. Refused while a global hold is set (it never releases one;
use the existing reviewed release first) or any attempt is unsettled. It stores the source
URL and the user's own words (12-500 printable characters) in `provider_error_retries`
and makes no request. The source's next ordinary `fetch`, in any run, finds the allowance
and uses it once, counted as one of that run's planned requests with its usual reserve,
budget, request limit, spacing, burn allowance and holds; `recovery_of` links it to the
failed attempt, whose charge stays in history. It is shown under `provider_error_retries`
in `usage`.

Portal reconciliation is **not** required. A 5xx may not appear in String's portal, and
the failed attempt's charge is already counted in full (reconciled value, or the whole
reservation when settled unattended), so the retry cannot understate spending. Reconcile
the retry's own charge as usual.

```bash
python scripts/igx.py public-capture authorize-provider-error-retry \
  --url 'https://www.instagram.com/reel/SHORTCODE/' \
  --user-direction 'Exact user request to try this source again'
```

### One user-directed retry after String answered 200 with no usable media

When the only request for a source settled with HTTP 200, no error code and no usable
media, and the capture reconciliation route above cannot apply (for example the run's
other attempts cannot be reconciled to String's portal), the user can explicitly ask for
**one** more request. The offline authorization requires: no global hold (it never
releases one), no unsettled attempt anywhere, exactly one attempt for the source with
no filed note, and no earlier authorization of any kind for that source. It is allowed
once per source, ever. It stores the source URL and the user's own words (12-500
printable characters) in `empty_capture_retries` and makes no request. The source's next
ordinary `fetch` uses it once; run it in a new run created with `--max-requests 1`.
`recovery_of` links the retry to the first attempt, whose charge stays in history, and
the retry has the run's usual reserve, budget, spacing, burn allowance and holds. It is
shown under `empty_capture_retries` in `usage`. If the retry also returns nothing, do
not authorize another.

```bash
python scripts/igx.py public-capture authorize-empty-capture-retry \
  --url 'https://www.instagram.com/p/SHORTCODE/' \
  --user-direction 'Exact user request to try this source again'
```

## Validate and process locally

One public page fetch must return an exact shortcode match. Related/recommended
posts do not count. Only expected HTTPS Instagram CDN hosts may supply assets;
downloads use no Cookie/Authorization headers, no automatic proxy fallback, no
redirects and no retries. Size limits, content type, duration, required audio
and full decoding are checked before a capture manifest is finalized. A reel
thumbnail alone cannot pass. ffprobe/ffmpeg validation only allows local file
protocols, so a disguised playlist cannot fetch additional URLs.

Reels over two minutes get frames every 2 seconds (shorter ones every second) and OCR, so on-screen text, charts and products in long reels are not lost (2026-10-08; before this they got no frames).

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

### Managed Cloud media transport

When direct public CDN TCP access is unavailable but inherited managed egress works,
set `STRING_MEDIA_USE_HTTPS_PROXY=1` before starting an unattempted source. This uses
only inherited `HTTPS_PROXY` for the already host-validated anonymous HTTPS media
request. It adds no account credentials, allows no redirects or retries, and retains
all size, content and decode checks. It is managed network transport, not proxy
rotation or a fallback after an Instagram refusal. Default media routing stays direct.
A source already attempted is never retried to test this setting.

### Explicit Cloud transport recovery

On a specific user instruction to retry a failed source, `authorize-transport-recovery`
records one source-bound allowance with the exact user words, original attempt,
reviewed transport-fix commit and SHA-256 of a local JSON evidence file. This is not
an unattended retry policy and does not change the older portal-reviewed helper.
The dedicated run must be empty with one request, all charges settled, no hold, and
a fresh passing free check. Evidence must match `attempt_id`, canonical `source_url`,
`user_direction`, `transport_fix_reviewed: true`, and a full `transport_fix_commit`.

Only two modes qualify: `paid` for a status-0 transport failure already conservatively
settled at its full reservation (`unattended_max`), and `retained` for a successful
known-class page fetch with no usable media, settled by the owner-approved price list.
The latter additionally binds `retained_page_sha256`. Neither is portal-confirmed;
keep the accounting label. Unknown charges, billing anomalies, holds, successful
captures, source mismatches and already authorized/recovered originals are refused.

```
python scripts/igx.py public-capture authorize-transport-recovery --run RETRY_RUN \
  --attempt-id ORIGINAL_ID --url EXACT_CANONICAL_URL \
  --user-direction 'Exact explicit owner direction' --evidence PRIVATE_EVIDENCE.json
```

For `paid`, normal `fetch --recover-attempt ORIGINAL_ID` uses the one-request run,
reservation, access check, pacing, burn and budget guards. Its new attempt links the
original, whose status and charge stay unchanged. For `retained`, use
`recover-retained-transport` with the same run, attempt, URL and evidence. It consumes
the allowance before its anonymous media GETs and reuses the hash-bound local page,
with no String fetch or new provider charge. Every normal host, redirect, size and
decode guard remains. Its manifest keeps the original provider attempt plus the
media-recovery link; failures retain a source-specific hold and cannot retry again.
Set the reviewed managed transport options before either path. Keep private evidence
free of credentials and signed asset URLs, and never commit retained source pages.
