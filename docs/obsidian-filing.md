# Obsidian filing — knowledge & organization only

Agents write **knowledge notes** here. Tooling, scripts, and extraction workflow live in the **git repo** ([AGENTS.md](../AGENTS.md), [agent-workflow.md](agent-workflow.md), [storage-retention.md](storage-retention.md)).

**Vault root:** `OBSIDIAN_VAULT` (or `local.env`). Knowledge only.

---

## What belongs in Obsidian

| Yes | No |
|-----|-----|
| TL;DR, claims, analysis, evidence summaries | Full Whisper transcripts |
| **Keep frames** — 1–4 stills that carry the fact (map, menu, hotel name, itinerary, chart, visa table) | The full 90-frame dump; talking-head stills |
| Wikilinks, tags, open questions | Script paths, Whisper settings, yt-dlp steps |
| Folder structure & categorization decisions | Agent runbooks (→ git repo) |
| Synthesized trading/options learnings | Cookie/setup instructions |

Copy keep frames into `instagram/extractions/_media/{id}/` and embed them on the receipt (`![[instagram/extractions/_media/{id}/{slug}.jpg]]`). Name the file after what it shows (`hoi-an-blue-eye-tailor.jpg`, not `frame_12.jpg`). Raw mp4 + unused frames stay in `downloads/` and can still be pruned.

---

## Folder map

**Rule:** a knowledge center is a **top-level folder**. Opening it should show `_index.md` (underscore sorts first) plus a human-readable set of reports. Instagram is the extract archive. Link `[[instagram/extractions/{id}-{slug}]]` from the human page; do not copy the reel.

Shortcodes start with `D`. That is why extractions are nested — otherwise they bury every other note.

| Folder | Contents |
|--------|----------|
| `health/` | Health notes. Start at `health/_index.md` |
| `wealth/` | Investing (no separate `investing/` folder). Books, ticker map, `investments/`, `companies/` |
| `building-an-app/` | Shipping / hosting takeaways. **Tight pair with `marketing-an-app/`** (one product loop: ship ↔ get users). Cross-link both hubs; a reel may sit in both. |
| `marketing-an-app/` | Marketing notes from Saved or other sources. Sister of `building-an-app/`. |
| `fitness-tips/` | IG Saved Fitness tips. Principles first, then named plans |
| `privacy/` `real-estate/` `travel/` `decisions/` | That domain’s `_index.md` |
| `people/` `ai/` | Person hubs / papers |
| `language/` | Mandarin first: pronunciation, input, characters. Joke reels stay on `good-info/` |
| `instagram/extractions/{id}-{slug}.md` | One reel / carousel (receipt) |
| `instagram/runs/` | Extract plan / audit (process) |
| `youtube/` `twitter/` | Other extract archives |
| `_system/` | Vault organization only |
| `_inbox/` | Unsorted captures |

Expand as domains grow (e.g. `wealth/gex/`). Do **not** revive `_reports/` or `_runs/` at vault root.

---

## Two-phase workflow (fluid)

Structure will evolve as more content is extracted — **don't over-organize early**.

### Phase 1 — Per-source extraction notes (default)

After extract/analyze, write **one note per source** in `instagram/extractions/`, `youtube/`, or `_inbox/`:

- Summarize what matters (TL;DR, claims, evidence, analysis)
- Keep `source:` URL + platform ID in filename
- **Do not** split into `wealth/` or cross-domain synthesis unless the user asks or the content clearly belongs there

Examples: GEX reel → `instagram/`; Jawed talk → `youtube/` (no separate startup note needed yet).

### Phase 2 — Consolidation (later, optional)

Periodically (or when patterns emerge):

- Move or merge learnings into `wealth/{topic}/` or new synthesis notes
- Wikilink back to source notes; preserve URLs
- Reorganize folders as domains clarify

Agents may create folders and move notes when it helps, but **prefer leaving source notes in place** until consolidation is intentional.

### Rules of thumb

| Situation | Action |
|-----------|--------|
| New reel / video / carousel | One extraction note in `instagram/extractions/` or `youtube/` |
| User asks "is existing note enough?" | Usually yes — don't duplicate into `wealth/` yet |
| Same concept across many sources | Optional synthesis note; keep source notes |
| Unsure where it goes | `_inbox/` or tag heavily; consolidate later |

---

## Note format (AI-first, human-readable)

```yaml
---
source: https://instagram.com/reel/...
date: YYYY-MM-DD
type: reel | carousel | article | synthesis
tags: [instagram, wealth, options, gex]
author: "@handle"
user_question: "Is this true? Should I migrate?"   # if they pasted a saved comment
capture_context: Inbox                             # Todoist / Notion / chat
---
```

**Inbox pastes:** whatever they typed **immediately before** Instagram’s auto-title (`{creator} on Instagram: "…"`) is the **prompt** — a question, a statement, or a filing instruction (`Important distinguish the shorts from the longs`). Put the exact prefix in `user_question`. Do the work it asks (answer, split shorts vs longs, file tickers on the wealth trackers). YAML alone is not enough. A **Question** column is an optional override (`capture_context: Notion`). See [paste-inbox.md](paste-inbox.md).

If `user_question` is set, the **first** TL;DR bullet is:

```markdown
- **Your prompt:** <their words.> **<what you did / the answer>**
```

Empty / URL-only inbox titles: write `(none — URL only.)` and still say what you did with the save. Do not invent a prompt they did not write.

They do not post Instagram comments from this workflow. Never tell them “don’t comment KEYWORD.” Extract the saved source and its useful content; a comment-keyword CTA is not a skip reason.

**Body structure:**

1. **TL;DR** — first bullet **Your prompt:** if `user_question` is set; then 2–4 more bullets (agents read first)
2. **Claims** — what the creator asserts
3. **Evidence** — what frames/transcript/caption support or contradict (describe charts; don't paste raw transcript)
4. **Analysis** — legitimacy, gaps, context
5. **Related** — `[[wikilinks]]`
6. **Open questions** — checkbox follow-ups

### Naming

- Reels: `instagram/extractions/{id}-{short-slug}.md`
- Carousels: `instagram/extractions/{id}-{short-slug}.md` with `type: carousel` — one note per post, slide summaries in body
- YouTube: `youtube/{id}-{short-slug}.md`
- Tweets: `twitter/{id}-{short-slug}.md`
- Concepts: `wealth/{topic}.md`

---

## Where to file content

| Content | Path |
|---------|------|
| Reel analysis | `instagram/extractions/` |
| Video summaries | `youtube/` |
| Trading/options concepts | `wealth/` or `wealth/{topic}/` |
| Cross-cutting synthesis / ranked book / do-list | the matching knowledge-center folder (`health/`, `wealth/`, …) |
| Investment-relevant source | `wealth/investments/{id}-{slug}.md` (see below) |
| IG Saved “building an app” / “marketing an app” | Receipt in `instagram/extractions/`; takeaways on `building-an-app/_index.md` / `marketing-an-app/_index.md` |
| IG Saved “Music producing/DJ” | Receipt in `instagram/extractions/`; takeaways on `music-producing/_index.md`. Remix/sample tracks need title/artist or `track_id: unknown`, plus `vibe:` tags (tiktok / chill / orchestral / …) |
| IG Saved “Fitness tips” | Receipt in `instagram/extractions/`; takeaways on `fitness-tips/_index.md`. **Principles first**, then named plans. Confused / CTA → BACKLOG later-review pile D. Cheap surviving habits also get one line on `health/practical-recommendations.md` |
| IG Saved “Travel destinations/things to do” | Receipt in `instagram/extractions/`; takeaways on `travel/_index.md`. File **places**, **things to do on trips you already take**, **mechanics**, **points**, **food/hike/nightlife/Kami**, **someday**. Pretty-only → one line or skip. Ask why / when / with whom when there is a real place. Copy keep frames (map / sign / menu / itinerary) into `instagram/extractions/_media/{id}/`. |
| IG Saved “Good info” | Receipt in `instagram/extractions/`; file to the matching hub (`health/`, `wealth/`, `travel/`, …). Leftovers on `good-info/_index.md`. Folder is a weak signal. Keep frames when the still is the fact. |
| IG Saved “Clothes/fashion/scents” | Receipt in `instagram/extractions/`; takeaways on `clothes/_index.md`. Wear / scents / care. Pretty-only → one line or skip. |
| IG Saved “Tennis tips” | Receipt in `instagram/extractions/`; takeaways on `tennis/_index.md`. Strokes / footwork / gear. Pretty match clips → one line. |
| IG Saved “Cooking/Nutrition” | Receipt in `instagram/extractions/`; takeaways on `cooking/_index.md`. Folder is a weak signal. Cheap surviving habits also get one line on `health/practical-recommendations.md`. Pretty plating → one line. |
| IG Saved “Skin/hair care” | Receipt in `instagram/extractions/`; takeaways on `skin-hair/_index.md`. Cheap surviving habits also get one line on `health/practical-recommendations.md`. Product dumps → one line. |
| IG Saved “Taxes” | Receipt in `instagram/extractions/`; takeaways on `wealth/_index.md` when it is a real filing/tax mechanic. CTA software → one line. |
| IG Saved “Technology” | Receipt in `instagram/extractions/`; shipping curiosity on `building-an-app/_index.md`. Gadget / science leftover stays a hub line, not a new hub. |
| IG Saved “Language” | Receipt in `instagram/extractions/`. **Mandarin method** (YouTube + study tools) → `language/_index.md`. Joke / tone-bit leftovers stay on `good-info/_index.md`. |
| IG Saved “Parenting” | Receipt in `instagram/extractions/`; leftovers on `good-info/_index.md`. Do not spawn a Parenting hub for one post. |
| IG Saved “Israel-Palestine” | Receipt in `instagram/extractions/`; takeaways on `geopolitics/_index.md`. Date the tape. Maps/mechanics keep; 72-hour opinion cards do not. |
| Unsorted | `_inbox/` → classify later |

---

## Music production handoff

When local instructions identify a paired music-production repository, a music save can
have two separate deliverables. File the source analysis in Obsidian in both cases.

- **Technique, tutorial or production workflow:** also update the production repo's
  existing technique/reference guide in the same turn. Capture the useful method,
  original URL, clickable vault note, when it helps, one small experiment and the
  source's limits. Distinguish demonstrated settings from suggested adaptations; do
  not invent exact values, plugin requirements or evidence of a successful audition.
- **Song, voice or performance saved for reuse:** preserve the actual audio outside
  prunable downloads and update the production repo's audio inventory/handoff. A
  technique catalog entry is not a replacement for requested audio. One source can
  need both deliverables.

Read the destination repo's instructions and current teaching/track records first.
Deduplicate by source and technique, extend an existing backlog item when relevant,
and make the reference discoverable from its agent entry point. Imported ideas remain
references/proposed exercises until actually taught or tried; do not add them to a
completed-learning record or change a Live Set just to finish filing.

For backfills, reuse existing source notes and retain their evidence limits instead
of redownloading completed sources. Record the scope covered. If the configured repo
is unavailable, keep a concrete pending handoff and report it; do not imply the
project filing is complete. Keep private repository paths and personal mappings in
local instructions rather than this public repo.

## Instagram Saved categories → topic hubs

Instagram Saved collections map to vault **topic hubs**. Same idea as company hubs: **one source note**, index from/to the hub — don’t copy the reel into a second page.

| Piece | Where | Role |
|-------|-------|------|
| **Source note** | `instagram/extractions/{id}-{slug}.md` | Full extract + analysis |
| **Topic hub** | `{kebab}/_index.md` at vault root (e.g. `building-an-app/_index.md`) | Index + short takeaways |
| **Synthesis** | a sibling note *in that folder* (e.g. `health/practical-recommendations.md`) | Only when a lesson outgrows one reel |
| **YAML** | `topics: [building-an-app]` | Plus matching tag |

**From / to:** source **Related** → `[[building-an-app/_index]]`; hub **Sources** → `[[instagram/…]]`. A reel can sit in more than one topic.

**Don’t** duplicate the full reel onto the hub. Hub takeaways should be **queryable knowledge** (tools, UX/UI patterns, ad examples, workflows), not a title list. One reel can contribute several bullets. If you cannot tell why it was saved, say so on the source note and ask.

**Optional move:** if the topic folder is clearly the home, move the source there — **keep `{id}` in the filename**. `--keep-noted` matches the ID anywhere in the vault.

**New IG Saved category:** kebab-case the name (`shipping-an-app` → `shipping-an-app/_index.md` at vault root), add to vault `_index.md` and this table. Don’t invent categories the user doesn’t use.

**Domain hubs (not an IG Saved name):** same shape when a queue is clearly one domain. Source stays in `instagram/extractions/`; `topics: […]`; hub is index + 1–3 line takeaways. Don’t fork a second thesis page per reel.

**Health:** the do-list is vault `health/practical-recommendations.md`. If a cheap habit survives a check, add one line there + a source wikilink. Grocery/home claim receipts stay on `health/microplastics-everyday-life.md` — do not fork a twin. Do not start a supplement protocol from a comment keyword.

Overnight run plan (agents re-read): vault `instagram/runs/2026-08-18-overnight-plan.md`. After a large queue, brief from repo [batch-briefing.md](batch-briefing.md) — full sentences, every note an `obsidian://open?path=` link on the Cursor canvas, not a title dump.

### Obsidian is the report; a canvas is the twin

Anything the operator will **reread or cite later** goes in the vault first (knowledge-center report, hub `_index`, or extraction). Cursor canvases are for the visual scan **in this chat** — do both, same turn. Point the canvas at the note. Do not leave a ranked book, ticker map, or do-list only in a canvas (not git, not the vault, cloud-invisible). The global canvas skill’s “don’t dump a table in chat” does not replace the vault note.

### Opening notes from a Cursor canvas

Vault notes are outside the git repo, so Cursor `open_resource` cannot open them. From a canvas, use:

`obsidian://open?path=` + `encodeURIComponent(absolutePathToMd)`

Vault root: `OBSIDIAN_VAULT`. Clicks go through the default browser, which hands off to Obsidian. Do not use `file://` (browsers block it). Full recipe: [batch-briefing.md](batch-briefing.md).

Example: [[building-an-app/_index]] ← [[instagram/extractions/DbBpI79RixV-deonnahodges-free-hosting-stack]]

---

## Investment cases — filing pattern

### Categorization lesson (canonical example)

**User task:** “investment case for SpaceX”  
**Source:** Dwarkesh Patel macro essay on compute pricing ([oZBGAuANX6I](https://www.youtube.com/watch?v=oZBGAuANX6I)) — SpaceX is a **case study**, not the whole video.

**Pattern:** Task title = **your lens** (which company, which angle). Source = **broader**. One note holds both; don’t fork duplicate pages per ticker or per capture date.

| Piece | Where | Role |
|-------|-------|------|
| **One source note** | `wealth/investments/{video-id}-{slug}.md` | Single durable artifact per URL/memo |
| **Source summary** | Same note, `## Source summary` | What the author argues (neutral) |
| **Investment implications** | Same note, `## Investment implications` | Your lens (SpaceX, GOOGL, etc.) |
| **Company hubs** | `wealth/companies/SpaceX.md` | **Links only** — index sources; no duplicate thesis |
| **YAML** | Frontmatter | `content_kind`, `companies[]`, `date`, `captured`, `extracted` |

**Don’t:** split into separate “by stock” and “by date” pages with copied content. **Do:** wikilinks + tags + company hubs — scales as volume grows.

### When to use `wealth/investments/`

| Signal | Route |
|--------|-------|
| User asks for investment case, thesis, position research | `wealth/investments/` |
| Source mentions tickers/companies in investable context | `wealth/investments/` + update company hubs |
| Pure trading mechanics (GEX, spreads) | `wealth/` or `instagram/` — not investments |
| General interest video, no position angle | `youtube/` or `instagram/` only |

A source can stay in `youtube/` **or** move to `wealth/investments/` when the capture goal is explicitly investment-oriented. Prefer **one note**, not both.

### Note body (investment cases)

```markdown
## TL;DR
- Source + scope (e.g. “macro essay, SpaceX case study at ~4:30”)
- Author’s core claim (1–2 bullets)
- Your investment takeaway (1–2 bullets)

## Source summary
What the author argues — tables, claims, skepticism. Neutral tone.

## [Optional] Bookmarked segment (~MM:SS)
User flagged “40% in” = **~40% into the video**, not a % in the content.
Record as section heading or frontmatter `investment_timestamp: ~4:30`.

## Investment implications
Your lens: per-company table, bull/bear, caveats, what’s *not* in the source.

## Claims vs evidence
| Claim | In source? | Confidence |

## Related
- [[wealth/companies/SpaceX]]
- [[wealth/companies/Google]]

## Open questions
- [ ] Primary source for $900M/mo cite
```

### Company hub (`wealth/companies/`)

Hubs are **indexes**, not thesis documents.

```yaml
---
type: company-hub
tags: [company, spacex]
ticker: SPCX   # or private (Anthropic), or GOOGL, etc.
---
```

```markdown
# SpaceX

Hub — links investment cases and learnings. Thesis lives in source notes.

## Investment cases & sources
- [[wealth/investments/oZBGAuANX6I-spacex-compute-thesis]] — one-line hook

## Key facts (from sources)
- Bullet facts **sourced** from linked notes — not unsourced opinion

## Related
- [[wealth/companies/Google]]
```

**After filing a new investment note:** add a wikilink line to each `companies[]` hub. Hubs stay short.

### Frontmatter (investment cases)

```yaml
source: https://www.youtube.com/watch?v=...    # permanent
source_text: https://...                        # optional: blog, 10-K, memo
date: 2026-08-07                                # source publish or first noted
captured: 2026-08-17                            # filed to vault
extracted: 2026-08-17                            # transcript/analysis done
type: investment-case
content_kind: macro-thesis                      # see values below
status: stub | extracted | reviewed
companies: [SpaceX, Google, Anthropic]
tags: [investment-case, ai-compute, macro, private-markets]
author: Dwarkesh Patel
video_id: oZBGAuANX6I                           # or reel/post id
duration: ~11m                                  # optional
investment_timestamp: ~4:30                     # optional; user bookmark
```

**`content_kind` values:**

| Value | Use when |
|-------|----------|
| `macro-thesis` | Sector/macro argument spanning multiple names |
| `company-thesis` | Source is mainly about one company |
| `sector` | Industry dynamics (semis, power, datacenter) |
| `memo` | Written memo, letter, deck — not AV |

**Date fields:**

| Field | Meaning |
|-------|---------|
| `date` | When the **source** was published or you first cared about it |
| `captured` | When the note entered the vault |
| `extracted` | When transcript/analysis finished |

Sort/search by YAML — **no daily journal pages** unless you want a separate workflow.

### Timestamp bookmarks

User shorthand like **“40% in”** or **“~4:30 SpaceX bit”** means **position in the media**, not a percentage claim in the content.

Record as:

- Frontmatter: `investment_timestamp: ~4:30`
- Section: `## SpaceX investment bit (~4:30 / ~40% into video)`

### Anti-patterns

| Avoid | Why | Instead |
|-------|-----|---------|
| `SpaceX-2026-08-07.md` + `oZBGAuANX6I.md` | Duplicate thesis | One source note |
| Full thesis copied onto `wealth/companies/SpaceX.md` | Hubs bloat; edits diverge | Hub links + 1-line hook |
| Separate note per ticker from same video | Same evidence, 3× maintenance | One note, `companies: [...]` + implications table |
| Pasting full transcript | Vault is summaries | Link `downloads/{id}.txt` under **Raw assets**. Overlays: `{id}.ocr.txt` (survives frame prune). Charts still need the JPGs or the note’s description. |

### Optional: Dataview queries

If Obsidian Dataview is enabled:

```dataview
TABLE date, content_kind, status
FROM "wealth/investments"
WHERE contains(companies, "SpaceX")
SORT date DESC
```

Not required day one — wikilinks + hubs work without plugins.

### Reference note

Live example: `wealth/investments/oZBGAuANX6I-spacex-compute-thesis.md` — macro source + SpaceX bookmark + `## Investment implications`.

---

## Where to file content (quick reference)

| Content | Path |
|---------|------|
| Reel analysis | `instagram/extractions/` (+ `topics:` → hub) |
| Lifted take / ranked book / do-list | the knowledge-center folder (`health/`, `wealth/`, …) |
| Video summaries (non-investment) | `youtube/` |
| IG Saved / domain hub | `{kebab}/_index.md` at vault root |
| Trading/options concepts | `wealth/` |
| Cross-cutting synthesis | the matching knowledge center |
| Investment-relevant source | `wealth/investments/{id}-{slug}.md` |
| Company index | `wealth/companies/{Name}.md` |
| Unsorted | `_inbox/` → classify later |

*(Investment section above is canonical; this table is a shortcut.)*

---

## Tags (starter set)

`#instagram` `#youtube` `#wealth` `#options` `#gex` `#strategy` `#reference` `#analysis` `#skeptical-review`

**Investment:** `#investment-case` `#macro` `#private-markets` `#ai-compute` `#ai-infra` — plus company/sector tags in frontmatter `companies:` and `tags:`.

---

## Index maintenance

Update `Obsidian/_index.md` when adding a **knowledge center**.

Update that center’s `_index.md` when adding a take.

Update `instagram/_index.md` only when the extract archive itself changes (extractions naming, runs).

---

## Legitimacy reviews

Separate clearly:

- *What they show* (frame/chart observations)
- *What they say* (transcript summary)
- *What the caption claims*
- *What's missing* (data, time frame, counter-evidence)

Cross-link related notes (e.g. Robinhood spreads vs GEX reel claims).

---

## Source link retention

**Source URLs are permanent** — they survive raw media cleanup and KB consolidation.

| Rule | Detail |
|------|--------|
| Per-source notes | Always keep `source:` in frontmatter (Instagram reel or YouTube URL). Include platform ID in filename (`{id}-{slug}.md`). |
| Synthesis notes | Wikilink to source notes **and** list source URLs explicitly (e.g. under **Sources**). |
| Consolidation passes | Merge knowledge, not provenance. Never delete a source note without archiving its URL elsewhere. |
| Raw media | mp4/frames may be pruned via `cleanup-downloads.sh`; Obsidian `source:` links must remain. |

Git repo policy: [storage-retention.md](storage-retention.md).
