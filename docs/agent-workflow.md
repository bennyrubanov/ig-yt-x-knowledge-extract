# Agent workflow — extraction & analysis

Canonical instructions for agents. **Not in Obsidian** — this repo only.

Cross-ref: [AGENTS.md](../AGENTS.md) (entry point), [README.md](../README.md) (setup & CLI), [obsidian-filing.md](obsidian-filing.md) (where notes go), [paste-inbox.md](paste-inbox.md) (optional URL queue), [batch-briefing.md](batch-briefing.md) (after a large queue). Machine notes: `AGENTS.local.md` if present.

---

## Where things live

| Layer | Location | Role |
|-------|----------|------|
| **This repo** | clone / config symlink | Scripts, agent docs, venv, downloads |
| **Config symlink** | `~/.config/ig-yt-x-knowledge-extract` | Optional. Windows uses the clone. Legacy names still resolve. |
| **Instagram auth** | `~/.config/ig-cookies.txt` | Netscape jar. **No OAuth.** Recipe: [auth.md](auth.md) |
| **Obsidian vault** | `OBSIDIAN_VAULT` or `local.env` | **Knowledge only** — no agent/tooling docs |
| **Paste inbox** | optional; IDs in `local.env` | URL queue. Poll with `scripts/notion-extract-inbox.py`. Habit: [paste-inbox.md](paste-inbox.md). |

---

## Tools (local, free)

| Tool | Role |
|------|------|
| **yt-dlp** | Download Instagram/YouTube; description; optional native subs |
| **X / Twitter** | `transcribe-twitter.sh` — FixTweet for thread text + photos; yt-dlp for video. `WebFetch` on x.com is **403**. Optional `~/.config/x-cookies.txt`. Not the official X API. |
| **ffmpeg / ffprobe** | Audio, frames, duration, scene detection |
| **faster-whisper** (default) | Transcription (typically several× realtime; ~2–3× vs openai-whisper on the same machine) |
| **openai-whisper** | Fallback: `WHISPER_BACKEND=openai` (~2–3× RTF) |
| **Cursor Read** | Vision on frame JPGs |

No paid APIs. Scripts already warn if `ollama ps` shows a loaded model.

## Auth (read this before downloading Instagram)

There is no Instagram Connect / Graph API. If `~/.config/ig-cookies.txt` is missing, stop and follow [auth.md](auth.md). Cloud agents cannot complete that step. Never echo the jar.

Extract copies the jar before yt-dlp so a failed fetch cannot wipe `sessionid` from the live file. Instagram batches still run **one at a time** with `--ig-gap 45` (default). Do **not** retry empty-media Instagram URLs in the same run. If Chrome shows a login pause, wait; do not hit Instagram until a reel plays logged-in. [auth.md](auth.md).

---

## Step 1 — Ollama check

```bash
ollama ps
```

If a model is loaded, warn and confirm before a long Whisper run. If `ollama` is not installed, skip. The transcribe scripts already print the same warning.

---

## Step 2 — Run extraction script

### Instagram: `transcribe-reel.sh`

1. Validate cookies + venv + ffmpeg; warn if Ollama loaded
2. `yt-dlp --print id` → reel ID
3. Caption → `{id}.description.txt`
4. Full video + thumbnail → `{id}.mp4`, `{id}.jpg`
5. Audio → `{id}.m4a`
6. Frames (≤120s only) → `{id}/frames/frame_NNN.jpg`
7. OCR frames → `{id}.ocr.txt` (tesseract; keep-forever)
8. faster-whisper → `{id}.txt` + `{id}.whisper.log`

### X / Twitter: `transcribe-twitter.sh`

```bash
~/.config/ig-yt-x-knowledge-extract/transcribe-twitter.sh 'https://x.com/user/status/…'
```

| Get | How |
|-----|-----|
| Thread text + quotes | FixTweet (`api.fxtwitter.com`), walk parents |
| Photos | Download into `downloads/twitter/{id}/photos/` |
| Video | yt-dlp (public posts work without login) |
| Spoken audio | faster-whisper unless `--skip-whisper`; **skip if video >180s** (thread text is source of truth) |
| Frames | ≤120s videos, same as reels |

**Disk bomb (fixed 2026-08-18):** never `cat` the combined `{id}.txt` onto itself. Whisper writes `{id}.audio.m4a` / a sidecar; stdout is `thread.txt` only. If a `{id}.txt` starts growing without bound, kill the script + the `cat` child and restore from `thread.txt`.

**Login / age-gate / 403 on video:** export Netscape cookies to `~/.config/x-cookies.txt` (`chmod 600`) — same Chrome export as IG. Do not commit.

**Won’t get:** protected accounts you can’t see, DMs, Spaces, deleted tweets, other people’s reply trees (author thread only).

**Obsidian:** `twitter/{id}-{slug}.md` (or topic hub). Read photos + frames with vision.

### YouTube: `transcribe-youtube.sh`

Native EN captions first (~15–30s). Else audio + Whisper.

**Manual vs auto captions** — `yt-dlp --list-subs URL`:

| Listing | Meaning | Use as |
|---------|---------|--------|
| **Available subtitles** (`en`) | Creator-uploaded | **Source of truth** |
| **Available automatic captions** (`en-en`, `en-orig`) | YouTube ASR | Fallback if no manual track |

Script prefers `{id}.en.srt` and writes `{id}.captions.meta`. `--force-whisper` writes `{id}.whisper.txt` for comparison; never overwrite caption `{id}.txt`.

Measured (Dwarkesh `oZBGAuANX6I`): manual captions beat Whisper `small` on names/numbers (`Fable` vs `available`, `10x'd` vs `10x to`). ~91% word overlap.

### Instagram carousel: `transcribe-carousel.sh`

For **`instagram.com/p/...`** posts — series of images ± video slides + one caption.

```bash
~/.config/ig-yt-x-knowledge-extract/transcribe-carousel.sh 'https://www.instagram.com/p/...'
~/.config/ig-yt-x-knowledge-extract/transcribe-carousel.sh 'URL' --transcribe-videos   # Whisper on video slides
```

**Outputs:**

- `{id}.description.txt` — full carousel caption
- `{id}.ocr.txt` — tesseract on image slides (keep-forever)
- `{id}/slides/slide_01.jpg`, `slide_02.jpg`, … — image slides (via yt-dlp thumbnails)
- `{id}/slides/slide_N.mp4` — video slides (if any)
- `{id}/slides/manifest.txt` — file list

**Agent analysis:** read each slide with vision (selectively — not all 10+ in one turn if large). Caption often describes the whole series; cross-check per-slide text/charts.

**Image-only posts (fixed 2026-08-18):** do **not** start with `yt-dlp --print id` — that exits 1 (`No video formats`) under `set -e` before thumbnails run. Parse the shortcode from the URL. Thumbnail-first (`--skip-download --write-thumbnail`). Exit 0 if slides exist; empty caption is OK. Video-first is the fallback only when zero slides landed.

**No Whisper by default** — carousels are mostly static images. Use `--transcribe-videos` only when slides include talking-head video.

File Obsidian note as `type: carousel` with `source:` URL permanent (see [storage-retention.md](storage-retention.md)).

### Options (reels)

```bash
-o FILE                    # copy transcript elsewhere
--model medium             # if small mishears clear speech
--frame-interval auto|1|2|scene
```

Env: `IG_REEL_FRAME_INTERVAL=1` forces 1s for all reels (good for trading content).

---

## Frame extraction

**Strategy:** capture densely on disk; **read selectively** in chat (never load 90 frames in one turn).

### Modes

| Mode | When | ~90s reel |
|------|------|-----------|
| **1** (default) | Trading, chart-heavy, when unsure | ~90 frames, ~13 MB |
| **auto** | Static vs dynamic — biases 1s unless very few cuts | GEX (5 cuts / 89s) → **1s** |
| **2** | Clearly static talking-head | ~45 frames, ~6.5 MB |
| **scene** | Montage / jump cuts only | ~12+ frames; may miss slow chart updates on static slides |

GEX test reel (Da3iJOOIEkw): 5 scene cuts / 89s → **1s** with default or auto.

**Policy:** When unsure, bias **more frames** (default 1s). Disk cost is cheap; `cleanup-downloads.sh` prunes raw media after Obsidian notes exist. Re-run `reextract-frames.sh {id} --frame-interval 1` if analysis still finds gaps.

**Sub-second ticker flash:** a 1 s grid can miss the name. `DcRtJdPSchS` (Andy Luo) said “the stock … is this company” and showed **AAOI** for ~0.3 s after “POTENTIAL IS THIS.” Whisper and 19 one-second frames both missed it. If speech points at a name and the 1 s grid has no ticker, dump the last ~6 s at 10 fps (`mkdir /tmp/{id}-dense` first; do not wipe the 1 s set):

```bash
ffmpeg -y -ss START -i ~/.config/ig-yt-x-knowledge-extract/downloads/{id}.mp4 \
  -vf fps=10 /tmp/{id}-dense/f%03d.jpg
```

`START` = duration minus 6 (ffprobe). Also check the end of the 1 s grid — the name is often the last card.

### Agent read strategy

1. Read transcript + caption first
2. Sample frames (start / middle / end)
3. Deep-read frames aligned to transcript timestamps (charts, tickers, levels)
4. One-off grab if gap:

```bash
ffmpeg -y -ss 42 -i ~/.config/ig-yt-x-knowledge-extract/downloads/{id}.mp4 \
  -frames:v 1 -q:v 2 /tmp/frame_42.jpg
```

### Missed frames? Re-run denser extraction

If analysis reveals gaps (chart mentioned at t=37 but nearest frame is t=36 or t=38, or auto picked 2s and cuts were missed):

**Preferred — re-extract frames only** (mp4 already on disk; skips download + Whisper):

```bash
~/.config/ig-yt-x-knowledge-extract/reextract-frames.sh {id} --frame-interval 1
# or denser still:
~/.config/ig-yt-x-knowledge-extract/reextract-frames.sh {id} --frame-interval 1
# scene mode for montage-heavy reels:
~/.config/ig-yt-x-knowledge-extract/reextract-frames.sh {id} --frame-interval scene
```

This **replaces** `{id}/frames/*.jpg`. Transcript and video unchanged.

**Full re-run** only if video missing or you need re-transcription:

```bash
~/.config/ig-yt-x-knowledge-extract/transcribe-reel.sh 'REEL_URL' --frame-interval 1
```

Videos **>120s**: frames skipped by default; use manual `ffmpeg -ss` seeks on `{id}.mp4`. For Travel / Good info, dump a handful of stills yourself if the place or list is on screen — then copy **keep frames** into `instagram/extractions/_media/{id}/` (1–4 stills that carry the fact, not the full grid).

---

## Step 3 — Post-process mishears

Trading/finance reels:

- **gamma / GIMMA** — options gamma vs brand
- **EMA** — exponential moving average
- Fast numbers → cross-check frames + caption

Upgrade to `--model medium` only when `small` clearly fails on clear speech.

---

## Step 4 — Deliver (transcription)

1. Cleaned transcript
2. Brief summary (topic, key claims, caveats)
3. Caption/description context (links, disclaimers, hashtags)

---

## Step 5 — Analysis mode ("is this legit")

Do **not** rely on transcript alone. Read:

1. **Frames** (selectively) — charts, heatmaps, on-screen text
2. **Transcript** — spoken claims
3. **Description** — caption, hashtags, CTAs
4. **Thumbnail** — often shows key chart

Synthesize: shown vs said vs caption. Flag inconsistencies and missing context.

If frames insufficient → `reextract-frames.sh` with `--frame-interval 1`, then re-read targeted timestamps.

---

## Step 6 — Write Obsidian note

See [obsidian-filing.md](obsidian-filing.md). Obsidian gets **summaries + analysis**, not raw transcripts or JPGs.

**Routing:**

| Goal | Path |
|------|------|
| Reel / carousel | `instagram/extractions/{id}-{slug}.md` |
| General video | `youtube/{id}-{slug}.md` |
| Investment / thesis | `wealth/investments/{id}-{slug}.md` + update `wealth/companies/` hubs |
| IG Saved “building / marketing an app” | Source in `instagram/extractions/`; `topics:` + update `building-an-app/_index.md` / `marketing-an-app/_index.md` |

Investment notes: **Source summary** (author’s argument) and **Investment implications** (user’s lens) in one file. User bookmarks like “40% in” = timestamp in media, not a content percentage.

After note exists, optional cleanup (see [storage-retention.md](storage-retention.md)):

```bash
~/.config/ig-yt-x-knowledge-extract/cleanup-downloads.sh --dry-run --days 30 --keep-noted
~/.config/ig-yt-x-knowledge-extract/cleanup-downloads.sh --days 30 --keep-noted
```

---

## Outputs per reel

```
~/.config/ig-yt-x-knowledge-extract/downloads/
  {id}.mp4
  {id}.m4a
  {id}.txt
  {id}.description.txt
  {id}.ocr.txt          # on-screen text (tesseract); survives frame prune
  {id}.jpg
  {id}.whisper.log
  {id}/frames/frame_NNN.jpg
```

YouTube: `downloads/youtube/{id}.*`

---

## Storage & retention

**Policy:** extract densely → write Obsidian note → prune raw media monthly.

| Scope | ~100 reels (90s each) |
|-------|------------------------|
| Full cache @ 1s | ~2.6 GB |
| Full cache @ 2s / auto | ~2.0 GB |
| Transcripts only (after cleanup) | ~10 MB |

Full details, cleanup commands, and **source link retention**: **[storage-retention.md](storage-retention.md)**.

---

## Time estimates (example Apple Silicon, 32 GB)

| Step | ~90s reel |
|------|-----------|
| Full IG pipeline (download + frames + whisper) | ~1–2 min |
| Frame re-extract only (`reextract-frames.sh`) | ~30–60s |
| YouTube w/ captions | ~15–30s |
| 51 min talk, faster-whisper small | **~7.7 min** (measured 462s, RTF 6.6×) |
| 51 min talk, openai-whisper small | **~17 min** (measured 1022s, RTF 3.0×) |

Whisper: `{id}.whisper.log` + live segment progress on stderr.

Parallel batch: `transcribe-batch.sh URL…` (routes `/reel/` `/p/` YouTube X) or `extract-queue.py --queue queue.json`. Writes `--jsonl` (default `/tmp/extract.jsonl`). **Scoreboard:** `extract-status.sh --jsonl FILE` — disk + vault, not raw `fail` counts. A job that exits 234 (mjpeg) or 1 (old image-only carousel) but left slides/frames is `ok_partial` / `recovered`, not a missing note. Queue `kind: tv` (`/tv/` IGTV) is treated as a reel — do not let it `ValueError` the worker (Fitness 2026-08-20 died at 242/243 on that).

**Paste inbox (optional, not the Saved ZIP):** public repo = extractor; the ping is a separate habit — [paste-inbox.md](paste-inbox.md). Nothing downloads until the operator says to run the queue on a machine with cookies. Then:

```bash
python3 scripts/notion-extract-inbox.py list
python3 scripts/notion-extract-inbox.py urls   # includes comment-keyword rows; still extract those
# or: python3 scripts/notion-extract-inbox.py queue-json --out /tmp/notion-queue.json
#     python3 extract-queue.py --queue /tmp/notion-queue.json
```

File per [obsidian-filing.md](obsidian-filing.md). Mark the row (`noted` / `skip` / `fail`) and set `Vault path`. `user_question` is the **Name** prefix before `on Instagram:` (minus the creator). A Notion **Question** column is an optional override — usually empty. Empty **Status** is queued — do not only SQL `Status = queued`. Look up `page_id` by URL if `update_page` 404s (IDs in a dump can swap `3c03`/`3c13`). Comment-keyword lines (Comment “Sued”) — **extract anyway**. Do not mark `skip`. The keyword is a DM trick; the keep is on the reel (ScrapeGraph-AI `DX9fGa4SgX_` was the miss that changed this). yt-dlp often returns **one slide** for a carousel; file that slide and say so.

More URLs: paste in chat, or a bookmark-HTML export of *chosen folders* into `exports/` (gitignored). Agents should not log into Google. Live handoffs: `BACKLOG.local.md` if present.

---

## Security

- Never commit, log, echo, or paste cookie contents. Recipe: [auth.md](auth.md)
- Re-export cookies only after a reel plays in Chrome logged-in (a checkpoint is not “export harder”)
- Downloads may contain PII — don't upload externally without asking

**`sessionid`:** a Netscape export that skips HttpOnly cookies will 403 / empty-media even when the post is live. Chrome usually has `sessionid` as HttpOnly. `check-setup.py` green (row present) is **not** a live session — Chrome can be logged out and the row still exists. Human logs into instagram.com first. Then the **agent** runs **one** attended `--cookies-from-browser` (Keychain **Allow**, 1–2 prompts per dump) and keeps an Instagram-only jar. This operator does not use a cookie-extension export; do not ask them to. Extract copies the jar before yt-dlp; a manual probe must still use a **copy**. After empty-media, do not retry that Instagram URL in the same run. Do not commit, echo, or paste cookie files. Recipe: [auth.md](auth.md).

---

## Known failure modes

| Symptom | Cause | What to do |
|---------|--------|------------|
| Image-only carousel dies before slides | `--print id` / video-first + `set -e` | Current `transcribe-carousel.sh` (URL shortcode + thumbnail-first) |
| `{id}.txt` grows to tens of GB | `cat` combined Twitter transcript onto itself | Current `transcribe-twitter.sh`; restore `thread.txt` |
| Empty IG media, post still live | Missing HttpOnly `sessionid`, **or** Chrome logged out (stale row; check-setup still green), **or** the post is gone | Log into Instagram in the browser. One attended dump ([auth.md](auth.md) B). Probe a *copy* of the jar. If a sibling post downloads, treat this ID as gone. Do not multi-dump Keychain. Do **not** retry this URL in the same burst. |
| Chrome login paused / “try again later” after a batch | Same session hit media endpoints in a burst; retries on empty posts make it worse. yt-dlp `--cookies` on the **live** jar can also delete the `sessionid` row (extract now copies the jar) | Wait out the timer. Do not resubmit the login form. Do not re-export until a reel plays in Chrome logged-in. Do not run this pipeline against Instagram until then. Batches: `--workers 1 --ig-gap 45`. Incident 2026-08-23: [auth.md](auth.md) |
| Instagram comment threads empty | yt-dlp `--write-comments` → `i.instagram.com/api/v1/media/{pk}/comments/` returns `status: fail` (trial 2026-08-19). The working media/info payload has `comment_count` (946 / 3689) but `preview_comments` is empty and `hide_view_all_comment_entrypoint` is true. iOS `app_id` extractor-arg 400s video info. gallery-dl does not support IG comments; not installed here. Graph API is **your** professional media only. | Paste the comment or a screenshot. Open the reel in Instagram yourself. Do not add a second IG client. |
| jsonl says `fail`, media/note exists | Append-only log; last-write is stale | `extract-status.sh --jsonl FILE` (optionally `--write-recovered`) |
| Frames skipped | Video >120s | `ffmpeg -ss T -frames:v 1` or `reextract-frames.sh` |
| Export caption ≠ slides | IG Saved JSON caption/owner can attach to the wrong post. The same caption blob is often reused on many IDs | Trust downloaded slides / Whisper / `{id}.description.txt`. Re-check `inventory.jsonl` vs media |

---

## Changelog

- **2026-08-28** — Retrying historical empty-media fails: a **0-byte `{id}.description.txt`** still counts as `usable()` on disk, so `igx batch` logs `skipped_exists` without downloading. Delete stale sidecars (and any empty `{id}/` tree) before retry. Two Aug-23 empty-media rows recovered; `DcYw0OSu8x_` still HTTP 400 (isolated retry, same error).
- **2026-08-27** — Notion IG drain finished in two sittings (32, pause, then 29). Resume by **skipping jsonl `exit==0` URLs** — do not restart the list (that re-hits Instagram). Random **90–180 s** after each success + stop-on-nonzero; carousels were fast. Instagram served all 29. Empty-media / HTTP 400 still fail-once. [auth.md](auth.md).
- **2026-08-26** — After the 23 Aug checkpoint: do not try to empty a large Notion IG pile in one sitting. Random **150–210 s** gaps still produced Instagram **HTTP 400** on the 9th reel of a long run (`DcYw0OSu8x_`). Stop. Do not retry. Do not re-export until a reel plays in Chrome. [auth.md](auth.md).
- **2026-08-25** — Cookie refresh on this Mac is agent option B (`yt-dlp --cookies-from-browser`), not a human cookie-extension export. [auth.md](auth.md). `faster-whisper` can die with `No position encodings are defined for positions >= 448`; openai-whisper fallback still produced a usable transcript (`DY53rK6POxn`).
- **2026-08-23** — Notion inbox batch (19 IG URLs, then two empty-media retries against the live jar) paused Chrome login and deleted the `sessionid` row. Extract now copies the cookie jar before every yt-dlp call. Instagram jobs: one at a time, `--ig-gap 45`, no same-run retry. Wait out checkpoints; do not hammer login. [auth.md](auth.md).
- **2026-08-22** — Empty-media with a green `check-setup.py` means Chrome was logged out (or the post is gone), not “no sessionid row.” `yt-dlp --cookies FILE` write-back can strip `sessionid` after a failed fetch — probe a copy. One `--cookies-from-browser` can prompt Keychain twice; do not retry profiles. Seed a Netscape header before using `--cookies` as a dump target. [auth.md](auth.md).
- **2026-08-19** — Paste inbox is documented as optional and out of this repo: [paste-inbox.md](paste-inbox.md). `user_question` is parsed from the Name prefix before `on Instagram:`. Notion **Question** / **Topics** stay in the schema as unused overlays; do not fill them on paste. Public docs do not include one-off “draft a comment to post” voice. Instagram comment threads are not fetched.
- **2026-08-19** — Extract CLI is Python (`python scripts/igx.py …`) so Windows and Mac share one implementation. `.sh` files are thin Unix wrappers. No WSL required. Cookie path is still `~/.config/ig-cookies.txt`.
- **2026-08-19** — Auth is documented for clones: [auth.md](auth.md). No Instagram OAuth. `scripts/check-setup.py` verifies the jar without printing values. `CLAUDE.md` / `GEMINI.md` / `.cursor/rules` point at `AGENTS.md`.
- **2026-08-19** — Renamed public GitHub repo to `ig-yt-x-knowledge-extract`. Config symlink is `~/.config/ig-yt-x-knowledge-extract`; `ig-reels-knowledge-extract` and `ig-reel` still resolve. Local clone folder can match that name; scripts use the symlink, not the directory name.
- **2026-08-19** — Public origin is pipeline-only. Saved inventories and machine notes stay gitignored (`AGENTS.local.md`, `local.env`, `exports/`). Vault/Notion IDs load from `local.env`.
- **2026-08-18 (evening)** — Optional inbox email: one ping when queued first hits the configured threshold, then quiet until drained.
- **2026-08-21** — Travel extract died silently at 41/123 (process gone, jsonl stopped). Resume with a remain-only queue; append the same jsonl. Keep frames belong in the vault (`instagram/extractions/_media/{id}/`), not only a sentence about the JPG. Videos >120s still skip frames — for place lists, pull a handful of stills with `ffmpeg -ss` yourself.
- **2026-08-18 (evening)** — Remix/sample folders: title/artist from frames or speech, else `track_id: unknown` and continue. Add `vibe:` tags. No Shazam/`songrec`. See `AGENTS.md`.
- **2026-08-18 (evening)** — Parked handoffs live in `BACKLOG.local.md` (gitignored). Committed [BACKLOG.md](BACKLOG.md) is the pattern only.
- **2026-08-18 (evening)** — Optional Notion paste-inbox: Status / Question / Topics / Media ID / Vault path. Local poll `scripts/notion-extract-inbox.py`. Inbox IDs stay in `local.env`. No cookies on a remote poller. Saved ZIP dumps stay dated and local.
- **2026-08-18 (evening)** — Cross-repo build keeps stay in the operator’s skills library, not in this vault. Receipts stay in the vault.
- **2026-08-18 (evening)** — Official IG Saved ZIP (`exports/` inventory; keep the ZIP local). `scripts/ig-saved-inventory.py` is the audit. Optional `wanted-collections.txt` limits which folders get queues. Knowledge = tools/workflows/patterns on the hub, not a title list. **Export captions lie** — the same caption blob is often reused on many IDs.
- **2026-08-18 (evening)** — Vault: knowledge centers at root (`health/`, `wealth/`, `building-an-app/`, …). Reports live *in* that folder. Instagram is `extractions/` + `runs/` only. Investing lives in `wealth/`.
- **2026-08-18 (afternoon)** — Document-as-you-go is required (see `AGENTS.md`). Earlier split (`_reports/` / `_runs/` / nested sources) superseded the same evening. No IG Saved OAuth — official export JSON. Reports the operator will reread live in Obsidian; a Cursor canvas is a twin, never the only copy.
- **2026-08-18** — Vault: `_reports/` (takes), `_runs/` (process), `instagram/` (hubs + `_sources/`). Folder maps: `_index.md`, `instagram/_index.md`. `{id}.ocr.txt` (tesseract); keep-forever; `ocr-backfill.sh`. First TL;DR bullet is **Your question:** when `user_question` is set. `extract-status.sh` scoreboard; `extract-queue.py`; reel/twitter survive mjpeg / no-audio; image-only carousel; Twitter disk-bomb fix; live hotwords; [batch-briefing.md](batch-briefing.md)
- **2026-08-17** — IG Saved topic hubs (`building-an-app`, `marketing-an-app`); `--keep-noted` matches ID anywhere in vault
- **2026-08-17** — faster-whisper actually measured (~6.5× RTF, ~2.2–2.8× vs openai); fixed silent `$SCRIPT_DIR/lib` path bug; YouTube `--force-whisper` + manual-vs-auto captions; inbox `user_question` fields
- **2026-08-17** — Storage retention policy; cleanup `--keep-noted` fix; source link retention; `docs/storage-retention.md`
- **2026-08-17** — Repo `ig-reels-knowledge-extract`; auto/scene frame modes; cleanup script; agent docs moved from Obsidian
- **2026-08-17** — faster-whisper default; YouTube captions-first; progress logs
- **2026-07-27** — Initial pipeline; cookie export; first GEX reel
