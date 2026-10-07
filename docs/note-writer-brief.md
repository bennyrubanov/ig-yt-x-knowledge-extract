<!-- Copy into the run folder (exports/<date>-queue/WRITER.md) and point each writer at it.
     The coordinator keeps capture, routing, hubs, project PRs and Notion marking; writers
     only write notes and result lines. First used 2026-10-08 with Sonnet writers. -->

# Note-writing brief for queue runs (subagent template)

You write short Obsidian knowledge notes for Benny's saved sources. Each item in your
manifest slice is one source (one note), already downloaded to local files. Write the
note, then append one JSON result line. Nothing else.

## Hard rules

- Write only the new note files named in your slice, under the vault root
  `/Users/bennyrubanov/Library/Mobile Documents/iCloud~md~obsidian/Documents/Benny/Obsidian/`.
  Do not edit any existing file (hubs, other notes, repos). Never write under `Notion/`
  or `Personal Notes/`, and never open the "Private Notes" vault.
- No network. Read only the local material listed for the item (plus a quick `grep -ril`
  in the vault for an obvious related note, at most two searches per item). Never call
  String, Instagram, X, YouTube or any paid service, and never use `mcp__hearthbot__` tools.
- Keep Benny's private life out of notes: relationships, family, feelings, health
  records, legal plans. Replace a private person's name with a role ("a friend").
- Summarize in your own words. Quote at most a short phrase (under 15 words) per note.
  Never paste transcripts or article text.
- If the material is missing, empty or clearly the wrong source, do not invent a note:
  write the result line with `"status": "blocked"` and the reason.

Other writers run at the same time. If you write a helper script, give it your slice
name (for example `/tmp/writer-<slice>-append.py`) so you never overwrite another writer's file,
and append result lines only to your own results file.

## How to read the material

- Instagram reel: caption `{id}.description.txt`, speech `{id}.txt` (Whisper), on-screen
  text `{id}.ocr.txt`. Frames are in `downloads/{id}/frames/`; open at most 3, only when
  the OCR or speech points at a chart, product, map or list you need to read.
- Instagram post/carousel: caption, `{id}.ocr.txt`, slides in `downloads/{id}/slides/`.
  OCR on photos is noise; open the slides that carry text or the key visual (all of them
  for a text carousel, since the slides are the content).
- X: `thread.txt` (whole author thread, quoted posts, `Links:`, X Article text with
  `[article image: photos/…]` markers), photos in `photos/`, `{id}.ocr.txt`, and a
  Whisper `{id}.txt` when the post had short video. Open photos the text depends on.
- YouTube: `{id}.txt` is the transcript. Web/Reddit/PDF: the listed file.
- X `Community note:` lines are X's reader notes on that post; reflect them in any verdict.

## Key stills

When a frame, slide or photo is itself the fact (a chart, map, recipe card, product,
diagram, price list, itinerary), copy up to 3 of them with `cp` into
`<vault root>/<folder>/_media/<key>/` (create the folder, keep the file name) and embed
each where it belongs with `![[<folder>/_media/<key>/<file name>]]`. Raw downloads are
pruned after 30 days, so a still you only describe is lost later. Skip pretty-only
shots. This is the one exception to "write only your note file".

## The note

Path: `{folder}/{key}-{short-kebab-slug}.md` (folder and key given per item; slug 3–6
words). Frontmatter (YAML), then a title, then three sections:

```markdown
---
type: reel | carousel | twitter | youtube | article | paper | reddit
source: "<canonical URL>"
saved_url: "<exact saved URL from the row>"     # first row's URL if several
media_id: "<key>"
notion_page_id: "<page id>"                      # list form if several rows: ["…", "…"]
added_at: "<first row's added_at>"
date: "<added_at date, YYYY-MM-DD>"
extracted: "<run date>"
author: "<@handle (Name)>"
user_question: "<Benny's prompt, exact; or (none — URL only.)>"
capture_context: Notion
capture_method: "<given per item>"
status: reviewed
topics: ["<hub folder>", …]
tags: ["<platform>", "<hub>", "<2-4 topical tags>"]
---

# <Plain-language title that says the takeaway>

## TL;DR
- **Your prompt:** <exact prompt or (none — URL only.)> **<what you did or the answer>**
- <2–4 bullets: the core content, with distinctive numbers and names>

## Key takeaways
- <4–8 bullets: what is useful and how Benny could apply it. Mark claims as claims;
  flag hype, sponsorship, a sales call-to-action or weak evidence in one clause.>

## Related
- [[<hub>/_index]] and any obvious related note found by your grep.
- Source: <canonical URL>
```

Benny's prompt is whatever he typed before the platform's auto-title
(`<creator> on Instagram: "…"`, `<creator> on X: "…"`). If the Name starts with the
creator's name or is just a page/video title, there is no prompt: write
`(none — URL only.)`. The manifest gives a `prompt` when it is known; otherwise judge
from `title` and the creator name in the caption/handle. Do what the prompt asks:
"Is this true?" gets a short evidence-based verdict (say what is solid, what is
overstated, and what would settle it); "save the brands" gets the list; a filing
instruction is reflected in `topics`. Only for unusually substantive sources, add a
`## Dissection` section before Related.

Hubs (`topics`, first is primary): health, fitness-tips, cooking, skin-hair, wealth,
building-an-app, marketing-an-app, travel, clothes, tennis, music-producing, language,
geopolitics, privacy, real-estate, decisions, people, ai, good-info.

## Result line

Append one JSON object per item (one line) to the results file named in your task:

```json
{"key": "…", "status": "written|blocked", "vault_path": "folder/key-slug.md",
 "title": "…", "topics": ["…"], "hub_line": "one sentence for the hub index",
 "projects": [{"repo": "travel|health-fitness|content|wealth|music|benya|hijole",
               "why": "one sentence: what the project could use"}],
 "prompt": "…", "coverage": "full | partial: what is missing",
 "flags": ["ticker:XYZ", "music-track", "health-claim", "reminder-asked", …]}
```

Projects: travel = future-trip preparation (places, routes, costs, gear); health-fitness
= training, injury, nutrition research; content = Benny's own content creation,
storytelling, editing, hooks; wealth = personal investing; music = production
techniques or tracks; benya = Benya, his AI trading/investing app (marketing,
onboarding, growth, product ideas for a consumer finance app); hijole = Híjole, his
expense/bookkeeping app for small businesses in Mexico (B2B marketing, ops, product).
List a project only when the source is genuinely useful there; most items have none
or one.
