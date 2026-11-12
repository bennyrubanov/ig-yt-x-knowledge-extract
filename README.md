# ig-yt-x-knowledge-extract

Turns saved Instagram, YouTube and X posts into searchable notes. It downloads the media, pulls the caption, samples frames, reads on-screen text with OCR and transcribes speech locally with Whisper. Then it gives an AI agent (Claude Code, Codex, Cursor or Gemini CLI) everything it needs to write a reviewed note and file it in the right place in an Obsidian knowledge vault.

It has filed more than 2,000 notes in my own vault so far, most of them from saved Instagram posts, plus YouTube videos and X threads.

## What it does

- **One CLI for every source.** `igx` handles Instagram reels and carousels, YouTube videos and X posts, plus batch queues with a status scoreboard.
- **Local media processing.** ffmpeg frame sampling (fixed interval or scene changes), Tesseract OCR on frames and slides, and Whisper or faster-whisper transcription with no transcription API.
- **Notes written for agents and people.** A fixed note format (TL;DR, what was said, claims vs evidence, related links), a checker that runs before anything is filed, and optional Gemini first drafts.
- **Queue sources.** Instagram Saved exports, a Notion paste inbox, or plain URL lists.
- **Guardrails.** A hard spend ceiling per run on metered capture, cross-process rate limits and a safety hold for Instagram, usage logging before every billed request, and 239 unit tests.

## How it works

```text
URL, Saved export or inbox
        │
        ▼
  igx CLI ──► capture: yt-dlp (YouTube), X fetch, String public capture (Instagram)
        │
        ▼
  media: ffmpeg frames ─► Tesseract OCR ─► Whisper transcript
        │
        ▼
  downloads/<source>/<id>/ + a JSONL result row per item
        │
        ▼
  agent or Gemini draft ─► note-check ─► note filed under a topic hub in the vault
```

## Example

```bash
python scripts/igx.py youtube 'https://www.youtube.com/watch?v=…'
```

Each item gets its own folder with the video, audio, a Whisper transcript (`{id}.txt`), the caption (`{id}.description.txt`), on-screen text (`{id}.ocr.txt`) and sampled frames or carousel slides. The agent then writes a note like this (shape only; the values are placeholders):

```markdown
---
source: https://www.youtube.com/watch?v=…
date: 2026-11-03
type: youtube
tags: [youtube, ai-tools]
author: "@creator"
---

## TL;DR
- Three bullets an agent or a person can read first.

## What they said
Speech, on-screen text and caption combined, so you can skip the video.

## Dissection
| Claim | Evidence | Verdict |
|---|---|---|

## Related
[[topic-hub]]
```

## Instagram access

Instagram capture goes through the metered [String public-capture path](docs/public-capture.md), which never uses Instagram login cookies. The older signed-in extractor is paused and is not a fallback. Read [docs/instagram-account-incident.md](docs/instagram-account-incident.md) before setting up any Instagram access.

**Point an agent at this clone** (Claude Code, Cursor, Codex, Gemini CLI): it should read `AGENTS.md` first.

## Setup

**macOS / Linux**

```bash
git clone git@github.com:bennyrubanov/ig-yt-x-knowledge-extract.git
cd ig-yt-x-knowledge-extract
ln -sfn "$(pwd)" ~/.config/ig-yt-x-knowledge-extract
python3 -m venv whisper-venv
./whisper-venv/bin/pip install openai-whisper faster-whisper
cp local.env.example local.env   # set OBSIDIAN_VAULT
python3 scripts/check-setup.py
```

**Windows (PowerShell)** — no symlink required. The clone *is* the install root.

```powershell
git clone git@github.com:bennyrubanov/ig-yt-x-knowledge-extract.git
cd ig-yt-x-knowledge-extract
winget install -e --id yt-dlp.yt-dlp
winget install -e --id Gyan.FFmpeg
python -m venv whisper-venv
.\whisper-venv\Scripts\pip install openai-whisper faster-whisper
copy local.env.example local.env
python scripts\check-setup.py
```

Use `Gyan.FFmpeg` so `ffmpeg` is on PATH (`yt-dlp.FFmpeg` often is not). Optional: Tesseract for on-screen OCR.

Dependencies: `yt-dlp`, `ffmpeg`, `ffprobe`, optional `tesseract`.

Tests: `python3 -m unittest discover -s lib -p 'test_*.py'`.

Authentication details (there is no Instagram OAuth; the legacy cookie jar is paused, and the optional X cookie jar) are in [docs/auth.md](docs/auth.md). Never commit, log or paste a cookie file.

## Usage

Python is the CLI on every OS. `.sh` files are Unix wrappers around the same commands.

```bash
python scripts/igx.py reel 'https://www.instagram.com/reel/…'
python scripts/igx.py carousel 'https://www.instagram.com/p/…'
python scripts/igx.py youtube 'https://www.youtube.com/watch?v=…'
python scripts/igx.py twitter 'https://x.com/user/status/…'
python scripts/igx.py batch URL1 URL2          # --workers 1; Instagram safety guard applies; --jsonl FILE
python scripts/igx.py status --jsonl FILE
python scripts/igx.py reextract SHORTCODE --frame-interval 1
python scripts/igx.py note-check --vault "$KNOWLEDGE_ROOT" --results review.jsonl --require-reviewed --require-hub-links
```

Unix aliases (same flags): `transcribe-reel.sh`, `transcribe-carousel.sh`, `transcribe-youtube.sh`, `transcribe-twitter.sh`, `transcribe-batch.sh`, `extract-status.sh`, `reextract-frames.sh`.

`igx status` / `extract-status.sh` is the scoreboard (disk + vault). Do not count jsonl `fail` rows.

The pipeline warns if `ollama ps` shows a loaded model. Harmless when Ollama is absent or idle.

## What you get per item

Under `downloads/` (gitignored): video, audio, Whisper `{id}.txt`, caption `{id}.description.txt`, OCR `{id}.ocr.txt`, frames or carousel slides. Videos longer than 120s skip frames; re-run `python scripts/igx.py reextract {id}` if you need them.

Then write a receipt in your vault (`instagram/extractions/{id}-{slug}.md`) and a human page in a knowledge-center folder. See [docs/obsidian-filing.md](docs/obsidian-filing.md).

## Saved collections and inboxes

Instagram Saved: official Accounts Center export (Saved only, JSON ZIP). Keep the ZIP local. Parse it with:

```bash
python3 scripts/ig-saved-inventory.py \
  --zip ~/path/to/instagram-saved.zip \
  --out exports/YYYY-MM-DD-ig-saved
```

`exports/` is gitignored. Optional `wanted-collections.txt` limits which folders get queue files.

Optional paste-inbox (Notion or any URL list): this repo does not include a hosted poller. Habit + schema + ping rules: [docs/paste-inbox.md](docs/paste-inbox.md). `scripts/notion-extract-inbox.py` lists and marks rows. It does not download or transcribe.

## Docs

| File | For |
|------|-----|
| [AGENTS.md](AGENTS.md) | Agent entry point (Claude / Cursor / Codex / Gemini) |
| [docs/public-capture.md](docs/public-capture.md) | Metered Instagram capture without login cookies |
| [docs/instagram-account-incident.md](docs/instagram-account-incident.md) | Instagram access history and hold rules (read before any Instagram setup) |
| [docs/auth.md](docs/auth.md) | Cookie jars (legacy Instagram, optional X); no OAuth |
| [docs/paste-inbox.md](docs/paste-inbox.md) | Optional URL queue + ping (not this repo) |
| [docs/agent-workflow.md](docs/agent-workflow.md) | Extraction steps, failures |
| [docs/obsidian-filing.md](docs/obsidian-filing.md) | Where notes go |
| [docs/obsidian-quality.md](docs/obsidian-quality.md) | Offline batch check before filing; parser setup and scope |
| [docs/batch-briefing.md](docs/batch-briefing.md) | After a large queue |
| [docs/storage-retention.md](docs/storage-retention.md) | Prune downloads |
| [docs/BACKLOG.md](docs/BACKLOG.md) | How to park work (local list is `BACKLOG.local.md`) |

Machine-only overrides: `AGENTS.local.md`, `local.env`, `wanted-collections.txt` — all gitignored.

## License

Use and adapt. You are responsible for Instagram/YouTube/X terms and for anything in your cookie jar or vault.
