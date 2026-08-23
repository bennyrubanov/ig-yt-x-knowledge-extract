# Agent instructions — ig-yt-x-knowledge-extract

Read this file first. Auth (no Instagram OAuth): [docs/auth.md](docs/auth.md). Extraction: [docs/agent-workflow.md](docs/agent-workflow.md). Filing: [docs/obsidian-filing.md](docs/obsidian-filing.md). Optional URL queue: [docs/paste-inbox.md](docs/paste-inbox.md). After a large queue: [docs/batch-briefing.md](docs/batch-briefing.md).

**If `AGENTS.local.md` exists, read it next.** That file is gitignored machine notes (inbox IDs, parked work, collection order). Cloud clones will not have it.

| Tool | What it loads in this repo |
|------|----------------------------|
| Claude Code | [CLAUDE.md](CLAUDE.md) → this file |
| Cursor | this file + [.cursor/rules](.cursor/rules) |
| Codex | this file (`AGENTS.md`) |
| Gemini CLI | [GEMINI.md](GEMINI.md) → this file |

Obsidian is **knowledge-only** — summaries, analysis, wikilinks. All tooling stays in this repo.

## First run (new clone)

Someone pointed you at this GitHub repo. Do this on a **machine that can log into Instagram in a browser**. Cloud / Codespace cannot.

```bash
python3 scripts/check-setup.py
```

1. Clone + venv + `local.env` — [README.md](README.md). Symlink is optional (skip on Windows).
2. **Cookies are not automatic.** There is no Graph API, no “Connect Instagram,” no app permission to grant Cursor/Claude/Codex. Scripts hard-require `~/.config/ig-cookies.txt` (Netscape, HttpOnly `sessionid`). If that file is missing, **stop** and give the human [docs/auth.md](docs/auth.md). Do not invent OAuth. Do not echo the jar. A green `check-setup.py` only means the `sessionid` **row** exists. Empty media after that → the browser is logged out (or the post is gone), **or** you just clobbered the jar by passing the live file to `yt-dlp --cookies` (extract now copies it; hand-rolled probes must still copy). Human logs into instagram.com, then **one** re-export. Do not run `--cookies-from-browser` in a loop (each dump can be two Keychain prompts). Instagram batches: `--workers 1` (default) and `--ig-gap 45` (default). Do **not** retry empty-media Instagram URLs in the same run. If Chrome shows a login pause, wait — do not hit Instagram from this repo until a reel plays logged-in. Detail: [docs/auth.md](docs/auth.md).
3. Optional X video: `~/.config/x-cookies.txt` — same pattern, not the X API.

Never commit, log, echo, or paste cookie contents.

## Triggers

- User pastes `instagram.com/reel/` or `instagram.com/p/` (carousel) or YouTube or `x.com` / `twitter.com` status URL
- “Transcribe this reel/video/post”
- Legitimacy / fact-check / chart review
- Investment case / thesis research
- Inbox paste: URL + whatever they typed when they saved it — question, statement, or filing instruction (`user_question`). **First TL;DR bullet must be `Your prompt:` + what you did.** Ticker prompts also go on the vault wealth trackers.

Do not extract a paste-inbox pile until they say so on a machine that has cookies and the vault.

Scripts warn if `ollama ps` shows a loaded model. Whisper also uses RAM; confirm before a long run if something else is already using the GPU/RAM.

## Quick start

```bash
python scripts/igx.py reel "<REEL_URL>"
python scripts/igx.py carousel "<POST_URL>"
python scripts/igx.py youtube "<YOUTUBE_URL>"
python scripts/igx.py twitter "<TWEET_URL>"
python scripts/igx.py batch URL URL   # default --workers 1 --ig-gap 45
python scripts/igx.py status --jsonl FILE
```

Unix wrappers (`transcribe-reel.sh`, …) call the same Python. Config symlink `~/.config/ig-yt-x-knowledge-extract` is optional (legacy `ig-reels-knowledge-extract` / `ig-reel` still resolve). Windows: run from the clone; no symlink.

Scoreboard is disk + vault. Do not count jsonl `fail` rows.

Optional paste-inbox (IDs in `local.env`). Public = extractor; the ping is a separate habit: [docs/paste-inbox.md](docs/paste-inbox.md).

```bash
python3 scripts/notion-extract-inbox.py list
python3 scripts/notion-extract-inbox.py urls
```

Then `igx batch` those URLs (`--workers 1 --ig-gap 45` are the defaults) and `mark --status noted` (or `fail`). Empty Instagram media → `fail` once; do **not** retry in the same run. **Do not skip** because the caption says comment a word for a link — extract the reel; ignore the keyword. The prompt is the **Name** text before `on Instagram:` (question, statement, or filing instruction), not a Question column. Ticker prompts belong on the vault wealth trackers, not only in chat.

## Paths

| Item | Path |
|------|------|
| Repo | your clone |
| Config symlink | `~/.config/ig-yt-x-knowledge-extract` → repo (optional). Windows uses the clone. Legacy `ig-reels-knowledge-extract` / `ig-reel` still resolve. |
| Cookies | `~/.config/ig-cookies.txt` (IG, required) · `~/.config/x-cookies.txt` (X, optional). Recipe: [docs/auth.md](docs/auth.md) |
| Vault | `OBSIDIAN_VAULT` or `local.env` (gitignored) |
| Raw downloads | `downloads/` under the config symlink |
| Saved ZIP | keep local; parse with `scripts/ig-saved-inventory.py` — do not commit the ZIP |

Copy `local.env.example` → `local.env`. Token for the Notion CLI: `NOTION_TOKEN` or `notion.env` (gitignored).

## After extraction → Obsidian

Write the **receipt** under `instagram/extractions/` (or `youtube/` / `twitter/`). Write the **human page** in a knowledge-center folder. Filing: [docs/obsidian-filing.md](docs/obsidian-filing.md).

Document as you go. A learning that is only in chat is not captured. Pipeline lessons go in [docs/agent-workflow.md](docs/agent-workflow.md). Personal parked work goes in `BACKLOG.local.md` if that file exists.

**YouTube captions:** creator-uploaded `en` beats automatic captions (`en-en`, `en-orig`). Whisper is a comparison pass only (`--force-whisper`).

**IG Saved collections:** no OAuth. Official Accounts Center export (Saved only) → parse URLs. Do not crawl collections with the cookie jar. Do not commit the ZIP. `noted` = id appears in a vault filename. Folder is a weak signal — file the keep on screen. Export captions often attach to the wrong post; trust downloaded media, Whisper, `{id}.ocr.txt`.

**Music / remix folders:** title + artist from frames or speech; else a lyrics hook + `track_id: unknown`. Add `vibe:` tags. There is no Shazam in this pipeline.

Videos **>120s** skip frames by default; `python scripts/igx.py reextract {id}` (or `reextract-frames.sh`) if needed.

This pipeline does **not** fetch Instagram comment threads (caption, frames, and transcript only). `yt-dlp --write-comments` exists; Instagram’s comments endpoint currently fails (see [docs/agent-workflow.md](docs/agent-workflow.md)). Graph API is comments on **your** professional posts only. If a source is in the thread, paste the text or a screenshot.

## Missed frames?

```bash
python scripts/igx.py reextract {id} --frame-interval 1
```

## After a batch

Do **not** paste every title into chat. Patterns first, then hubs. Runbook: [docs/batch-briefing.md](docs/batch-briefing.md). In Cursor chat, link `https://www.instagram.com/reel/{id}/` — not `obsidian://`.
