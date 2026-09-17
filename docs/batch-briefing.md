# Batch briefing — after a large extract queue

How to brief after many extracts without dumping every title into chat.

Cross-ref: [AGENTS.md](../AGENTS.md), [agent-workflow.md](agent-workflow.md), [obsidian-filing.md](obsidian-filing.md). Machine-specific leftovers: `AGENTS.local.md` / `BACKLOG.local.md` if present.

---

## Who actually loads this

| Reader | Sees this file? |
|--------|-----------------|
| Cursor / Claude Code on a clone | Yes, if they follow `AGENTS.md` |
| Cloud agent / fresh clone | Yes — this file is committed |
| Obsidian-only session | No — vault has the *answers*; this file is the *how to brief* |

**Vault first, canvas second.** The take they will reopen lives in the knowledge-center folder. The canvas is the visual scan for this Cursor chat — write both. A canvas-only report is not captured.

Instagram queues stay `--workers 1 --ig-gap 45`. Empty media is a `fail`, not a same-run retry — a burst plus retries is what paused login on 2026-08-23 ([auth.md](auth.md)).

---

## Do not dump titles

They remember finding things interesting, not a list of 100 URLs. The briefing is:

1. **Interesting** — few items, why
2. **Look into** — actions
3. **Questions to ask**
4. **Success rate** at a glance (extracted / noted / failed / skipped)

Open a **Cursor canvas** beside chat for the domain scan. Chat stays short.

---

## Answer quality

Hard to follow:

> Vagus (Hoolest / Pulsetto) — Company real. 30s panic / −40 BPM cards are ads.

Write the same fact as a person would say it:

- **What exists** (company, device, mechanism)
- **What the reel sold** (the overlay / CTA / number)
- **What that number actually is** (vendor study, one-session HR, comment funnel)
- **What to do** (ignore unless you already wanted the tool; official site not DM)

Source notes with a saved Name prefix must open the TL;DR with **Your prompt:** + what you did (not YAML only). The prefix is a prompt, not always a question.

When the capture intent is future music production or sampling, the deliverable includes the actual audio, not just a summary. Preserve an audio copy outside prunable `downloads/`, link it from the note and music hub, and record the source URL, supported title/artist or speaker, descriptive search terms, and useful timestamps. Use an honest unknown identity where identification is unresolved. Do not infer permission to reuse the audio from its availability.

---

## Clickable Obsidian notes

For a brief opened in Codex, use ordinary Markdown links with absolute local paths: `[Read note](</absolute/vault/path/note.md>)`. A bare Obsidian wikilink such as `[[path|Read note]]` is not a working note link in that viewer. Verify every target exists. Chat summaries should include a few words about what the original source contained before giving the takeaway; assume the reader does not remember the save.

Canvas `<Link>` opens the default browser. Custom schemes work: the browser hands off to Obsidian.

**Use the `path` form** (vault-name independent):

```ts
const VAULT = process.env.OBSIDIAN_VAULT; // or the path from local.env

function noteUrl(rel: string) {
  const file = rel.endsWith(".md") ? rel : `${rel}.md`;
  return `obsidian://open?path=${encodeURIComponent(`${VAULT}/${file}`)}`;
}
```

**Do not** use `file://` for vault notes from a canvas. **Do not put `obsidian://` in Cursor chat markdown** — the chip looks like a link and does nothing. In chat, use `https://www.instagram.com/reel/{id}/` (or `/p/`). Put the vault note on a canvas `<Link href={noteUrl(...)}>`.

Every hub and every source on the canvas must be a `noteUrl` link. Put the long tail in `CollapsibleSection` drawers.

---

## After the next large queue

1. Update hubs (1–3 line takeaways + wikilink). Do not duplicate the reel onto the hub.
2. Write or update the vault take. Then refresh the canvas: full-sentence answers, every name an `obsidian://` link.
3. Point chat at the canvas **and** the vault note. Do not paste 100 titles.
4. Put durable pipeline lessons in [agent-workflow.md](agent-workflow.md).
5. Remind open rows in `BACKLOG.local.md` if that file exists.

---

## Traps that keep biting

- Empty IG media, post still live → usually missing/stale HttpOnly `sessionid`, or Chrome was logged out. `check-setup.py` green is not enough. One attended dump; probe a copy of the jar ([auth.md](auth.md)). If siblings download, that ID is gone.
- Image-only carousels → do not `yt-dlp --print id` first. Parse the shortcode from the URL.
- Twitter disk bomb → never `cat` the combined `{id}.txt` onto itself.
- jsonl is a log, not the scoreboard → `extract-status.sh --jsonl FILE`.
- Export captions lie → trust downloaded media, Whisper, `{id}.ocr.txt`.
