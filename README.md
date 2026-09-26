# ig-yt-x-knowledge-extract

Download Instagram, YouTube, or X media, pull captions and frames, transcribe with Whisper, and file notes in Obsidian. Built for agent-assisted review (charts, on-screen text, spoken claims).

No paid APIs. Cookies stay on your machine. Scripts warn if `ollama ps` shows a loaded model (Whisper also needs RAM).

**Point an agent at this clone** (Claude Code, Cursor, Codex, Gemini CLI, …): they should read `AGENTS.md` first. Instagram is **not** an OAuth grant — [docs/auth.md](docs/auth.md).

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

Cookies: `%USERPROFILE%\.config\ig-cookies.txt` (same `~/.config/ig-cookies.txt` path). Recipe: [docs/auth.md](docs/auth.md).

Dependencies: `yt-dlp`, `ffmpeg`, `ffprobe`, optional `tesseract`.

**Instagram cookies:** there is no Instagram OAuth, Graph API, or “Connect Instagram.” You log into instagram.com in a browser. The pipeline reuses that session via a Netscape jar at `~/.config/ig-cookies.txt` (include HttpOnly `sessionid`; `chmod 600` on Unix). Scripts exit if the file is missing. Full recipe: [docs/auth.md](docs/auth.md). Never commit, log, or paste the file. X login wall (optional): `~/.config/x-cookies.txt`.

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
| [docs/auth.md](docs/auth.md) | Cookie jar — no OAuth |
| [docs/paste-inbox.md](docs/paste-inbox.md) | Optional URL queue + ping (not this repo) |
| [docs/agent-workflow.md](docs/agent-workflow.md) | Extraction steps, failures |
| [docs/obsidian-filing.md](docs/obsidian-filing.md) | Where notes go |
| [docs/batch-briefing.md](docs/batch-briefing.md) | After a large queue |
| [docs/storage-retention.md](docs/storage-retention.md) | Prune downloads |
| [docs/BACKLOG.md](docs/BACKLOG.md) | How to park work (local list is `BACKLOG.local.md`) |

Machine-only overrides: `AGENTS.local.md`, `local.env`, `wanted-collections.txt` — all gitignored.

## License

Use and adapt. You are responsible for Instagram/YouTube/X terms and for anything in your cookie jar or vault.
