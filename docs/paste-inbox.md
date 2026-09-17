# Paste inbox (optional)

This repo is the **extractor**. A paste inbox is how you **feed** it. Neither
Notion nor a hosted ping is required to transcribe a URL.

Someone else does not need Railway, your Notion workspace, or an Instagram app
grant. The hard part to copy is the habit: a paste inbox, a ping, then “run the
queue on the laptop.” Replicating that ping is a second product (hosted poller +
mail + a database). Do not fold a poller into this repo unless you want to
productize the ping.

## Split

| Piece | Where it lives | What it does |
|-------|----------------|--------------|
| **Extractor** | this repo, on a machine with cookies + a vault | Download, Whisper, frames, Obsidian notes |
| **Paste inbox** | whatever list you already use (Notion, a note, chat) | URLs + the question you typed when you saved them |
| **Ping** | optional; a cron or a small host **without** cookies | Count queued rows; email once when the pile is large |

Nothing downloads until you say so **on the cookie machine**. Cloud / Codespace
cannot finish Instagram auth. Recipe: [auth.md](auth.md).

## What this operator runs

1. **Inbox:** the Notion **Knowledge Extractions** database (formerly Instagram Extractions), for mixed sources, not only Instagram. Phone or browser paste: URL in the URL column.
   The analysis prompt is typed **in the title, immediately before** Instagram’s
   auto-title (`{creator} on Instagram: "{caption}"`). Example:

   `Should I buy? TrendSpider on Instagram: "CEO Buy Alert…"`

2. **Ping:** a Railway job in a **different** personal repo counts rows whose
   Status is empty or `queued` and that have a URL. It emails **once** when that
   count first hits **100**, then stays quiet until no queued or extracting
   items remain. Reminder state persists across Railway sleep, restarts, and deployments. No cookies, Whisper, or vault on that host. Resend subject
   looks like `Knowledge Extractions: N queued — ready when you are`. The email
   links to this repository and its agent instructions, for any AI tool.

3. **Extract:** on the configured machine, only when you ask. First list and
   classify the mixed queue. Send supported media URLs to `igx`; read articles,
   text posts, repositories, and other sources with the appropriate tool. Do not
   pass the whole mixed queue blindly to the media downloader. For example:

```bash
python3 scripts/notion-extract-inbox.py list
python3 scripts/notion-extract-inbox.py urls
python scripts/igx.py batch "<supported-media-url>"
# file notes, then for each id:
python3 scripts/notion-extract-inbox.py mark --media-id SHORTCODE --status noted \
    --vault-path instagram/extractions/SHORTCODE-slug.md
```

Comment-keyword lines (`Comment “Sued”`, …) are **not** a reason to skip.
Extract the reel. Ignore the keyword. Empty **Status** + URL still counts as
queued.

IDs for the CLI stay in gitignored `local.env` (`NOTION_DATABASE_ID`,
`NOTION_DATA_SOURCE_ID`). Token: `NOTION_TOKEN` or `notion.env`.

## Copy the habit without copying the stack

Minimum viable inbox: any list of URLs plus a reminder to run this repo when the
laptop is on. Chat paste works. A Notion database is optional.

If you do use Notion, create a database with:

| Property | Type | On paste | After extract |
|----------|------|----------|----------------|
| **Name** | title | Instagram fills `{creator} on Instagram: "…"`. Type your question **in front of that**. | leave it |
| **URL** | url | required | leave it |
| **Status** | select: `queued` / `extracting` / `noted` / `skip` / `fail` | omit or `queued` | agent sets `noted` / `skip` / `fail` |
| **Media ID** | text | empty | agent sets the shortcode / video id |
| **Vault path** | text | empty | agent sets the receipt path |

Put the database and data-source IDs in `local.env`. Point
`scripts/notion-extract-inbox.py` at them. That script does not download.

Optional ping (keep it out of this repo): poll the same queued definition, email
when `count` first reaches N, remember you already mailed this pile, re-arm only
after there are no queued or extracting items left. Reserve the reminder in
durable storage before delivery; a process-local variable does not survive host sleep. Do not put `ig-cookies.txt` on that host.

## Name prefix is the prompt

`scripts/notion-extract-inbox.py queue-json` sets `user_question` from the Name
prefix (text before `on Instagram:` minus the creator). That prefix is not
always a question. A statement or instruction (“Important distinguish the
shorts from the longs”) is still the job: extract, then do what it says, and
file any tickers on the wealth claim map / hub. A separate **Question**
column is an optional override — you do not need to fill it. First TL;DR
bullet is `Your prompt:` when that field is set. Filing:
[obsidian-filing.md](obsidian-filing.md).

Do not tell the operator to comment or not comment a keyword. They are not
posting. Mark those rows `skip`.

## Unused Notion columns

If you already added **Question** and **Topics**, hide them on the paste views
rather than deleting them:

- **Question** — leftover. The prompt lives in Name. Keep the property so the
  CLI can still read an override; do not fill it on paste.
- **Topics** — leftover. Filing happens in the vault (`health/`, `wealth/`, …).
  Do not tag rows on paste. `mark --topic` still exists if you want it.

Keep **Name**, **URL**, **Status**, **Media ID**, **Vault path** visible.

## Saved-item intent and filing

Every item saved to **Knowledge Extractions** is intentional: something should be extracted even when the title has no question or filing hint. Read the source and any saved context, infer the useful takeaway, and find the matching existing knowledge hub. The unresolved questions are **what to extract** and **where to file**; ask only when the ambiguity materially changes the result. Do not silently skip sparse-context items, text-only posts, articles, repositories, or links unsupported by the media downloader. Use an appropriate reading/extraction tool, retain the source URL and saved prompt, and record inaccessible sources as blocked/fail with the reason. A comment-keyword CTA is not a reason to skip. Only mark `noted` after the knowledge has actually been filed.

The current queue drain is deferred until explicitly requested. Receiving a reminder does not authorize extraction.
