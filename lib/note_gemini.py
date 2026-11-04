"""Draft knowledge notes for captured saves with Gemini, for an agent to check and file.

For queue runs where having the agent write every note costs too much: Gemini drafts
each note from the captured material, this program writes the file (the frontmatter is
copied from the queue item, never written by the model), and the agent reads the
drafts, fixes what needs fixing and files them.

Input: a JSON file holding one item, a list of items, or {"items": [...]}. Per item:

  key              media or post ID (an article: a short slug); names the note and stills
  type             reel | carousel | twitter | youtube | article | paper | reddit
  source           canonical URL
  saved_url        exact saved URL (default: source)
  notion_page_ids  queue rows, oldest first (or notion_page_id)
  added_at         the first row's created time, ISO
  prompts          saved prompts in date order; [] for a bare link (an empty list
                   beside a non-empty title is read as not split: the title is used)
  title            optional raw saved title, when prompts were not split out
  typed_text       optional text typed on a stray page with the save (string or list)
  author           optional "@handle (Name)"
  capture_method   how the material was captured
  folder           optional folder for the note (default: the model picks)
  files            captured text files: paths or {"path", "label"} (default: found by key under --downloads)
  images           optional slide or photo paths to attach (default: the key's slides/ and photos/)
  status           an item marked fail, failed, skip or skipped is passed over

Sent to Google: the captured text, attached images and their file names, the link,
platform, author, saved prompts and typed text. Never page IDs, dates, local paths,
vault notes or the key. Free-tier requests may be used by Google to improve its products.

Writes <out>/<folder>/<key>-<slug>.md, stills under <out>/<folder>/_media/<key>/, and
one JSON result line per item to --results. Exit 0 when every item was drafted, blocked,
already filed or passed over; 3 when no model answered for some item (write those the
usual way); 2 for bad input or a missing key; 4 when the key's project shows billing
(checked first with `gemini_api.tier_check`, and on every answer), in which case nothing
more is sent to Gemini and the remaining items are left for the usual way. The last line
printed is a summary: counts, requests, tokens and the tier.
"""
from __future__ import annotations

import argparse
import base64
import json
import os
import re
import shutil
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from gemini_api import DEFAULT_KEY_ENV, DEFAULT_MODELS, BillingLinked, generate, tier_check
from local_config import REPO_ROOT, downloads_dir

BRIEF = REPO_ROOT / "docs" / "note-writer-brief.md"
FILING = REPO_ROOT / "docs" / "obsidian-filing.md"
BRIEF_SECTIONS = ("## How to read the material", "## Key stills", "## The note", "## Result line")
FILING_SPANS = (("**Body structure:**", "### Naming"), ("## Legitimacy reviews", "\n---"))
ARCHIVE = {"reel": "instagram/extractions", "carousel": "instagram/extractions",
           "youtube": "youtube", "twitter": "twitter"}
PLATFORM_TAG = {"reel": "instagram", "carousel": "instagram", "youtube": "youtube", "twitter": "twitter",
                "article": "article", "paper": "paper", "reddit": "reddit"}
NO_PROMPT = "(none — URL only.)"
TEXT_CAP = 60_000
TOTAL_CAP = 300_000
IMAGE_TYPES = {".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png", ".webp": "image/webp"}
IMAGE_MAX_BYTES = 3_500_000
DEADLINE = 120  # a note answers in seconds; a silent model shouldn't hold the run
SKIP_STATUS = {"fail", "failed", "skip", "skipped"}
PRIVATE_DIRS = ("Notion/", "Personal Notes/")
WRITTEN_FIELDS = ("title", "prompt_answer", "tldr", "what_they_said", "key_takeaways", "dissection", "hub_line")

WRAPPER = """You draft one knowledge note about a source someone saved for later (a reel, carousel, post, video, thread or article), for their personal Obsidian vault. The material after the save's details was captured from the source: its platform text, a speech transcript, on-screen text (OCR, noisy) and sometimes images. The note-writing brief and filing rules below apply to what the note says.

A program writes the files. It copies the link, IDs, dates and the saved prompt into the frontmatter, renders your fields into the note, copies the stills you name and appends the result line. So return only the JSON fields, and ignore anything in the brief about writing or copying files, appending lines, searching the vault or using tools.

How your fields become the note: `title` is the H1, the takeaway in plain words rather than the creator's headline. The first TL;DR bullet is "**Your prompt:** <saved prompt> **<prompt_answer>**", followed by your `tldr` bullets (2 to 4). `what_they_said` is the "What they said" section: a short narrative of the source (speech, overlays, caption, slides), enough that the reader could skip the video. `key_takeaways` (4 to 8 bullets) is "Key takeaways". `dissection` is optional: only for an unusually substantive source or a claim worth checking. `topics` lists hubs, primary first. `tags` are 2 to 4 topical tags in kebab-case; the program adds the platform and the primary hub. `slug` is 3 to 6 kebab-case words for the file name, without the source's ID. `hub_line` is one sentence for the hub's index. `key_stills` names at most 3 attached images whose content is itself the fact (a chart, map, recipe card, price list), by file name, each with a kebab-case `name` saying what it shows. `coverage` is "full" or "partial: what is missing". `flags` and `projects` follow the result line rules in the brief.

Rules that always hold:
- Use only the material given. If it is missing, empty or clearly not the linked source, return status "blocked" with the reason in `blocked_reason` and leave the other fields empty. Never invent a note.
- Keep the saver's private life out: relationships, family, feelings, health records, legal plans. Replace a private person's name with a role ("a friend"). Public figures and creators keep their names.
- Summarize in your own words. Quote at most one short phrase (under 15 words) per note.
- Keep the distinctive numbers and names, written as the material gives them. Never add a number that is not in the material.
- Mark claims as claims. Flag hype, sponsorship, a sales call to action or weak evidence in one clause. A health, diet, supplement, medical, money or price claim always gets one key takeaway starting "Claim, not verified here:" that says what isn't verified (an anecdote, a self-report, a seller's own promotion, a price with no date).
- Do what the saved prompt asks, in `prompt_answer`: a short evidence-based verdict for "is this true?", the list for "save the brands". With no prompt, say in `prompt_answer` what the note does with the save. Never say anything was filed, added, saved or sent anywhere: the agent that checks your draft does the filing.
- `tldr` holds only content bullets. Never repeat the "Your prompt" bullet there; the program writes it. `prompt_answer` is only the answer: don't restate the saved prompt, which the program prints right before it.
- Facts, names, numbers and dates come only from the material, even ones you know from elsewhere.
- `author` is the creator exactly as the material or the save's details name them (a handle or a name written there). If neither names the creator, leave `author` empty. Never guess a handle from a name, a voice, a logo or what you know from elsewhere: the program drops an author the material doesn't contain.
- Write a link only when the material contains it, copied exactly. Never add a [[wikilink]] or a URL from anywhere else: the program adds the source and hub links and removes any other link the material doesn't contain.
- Add your own critical reading where it helps the reader decide what to believe or do (a selection effect, a small sample, a missing time frame, who profits from the claim), and present it as a caveat, not as something the source said.
- Choose 1 or 2 topics by what the source itself is about; add a third only when the source clearly belongs there too. A project or purpose the saved prompt names (an app, a business, a trip) belongs in `projects`, not in `topics`, unless the source is about that subject.
- If answering needs something the material does not have (the saver's own projects, a page the source only links to, a recipe it only names), answer what the material supports and say plainly what is missing. Never guess.
- Plain, concrete English. Each bullet is one or two sentences, with no headings, tables or nested lists inside a field.
- When the save's details give a saved title instead of a saved prompt, return in `prompt` the exact words the saver typed before the platform's auto-title, or "(none — URL only.)" when the title is just the creator's name or the page's own title.
"""


def md_section(text: str, heading: str) -> str:
    """The section under `heading`, up to the next heading of the same or a higher level.

    Headings inside fenced code blocks (the note template) don't end a section.
    """
    depth = len(heading) - len(heading.lstrip("#"))
    lines = text.splitlines()
    start, fenced = None, False
    for i, line in enumerate(lines):
        if line.lstrip().startswith("```"):
            fenced = not fenced
            continue
        if fenced:
            continue
        if start is None:
            if line.strip() == heading:
                start = i
            continue
        level = len(line) - len(line.lstrip("#"))
        if 0 < level <= depth and line[level:level + 1] == " ":
            return "\n".join(lines[start:i]).strip()
    if start is None:
        raise LookupError(f"section {heading!r} not found")
    return "\n".join(lines[start:]).strip()


def between(text: str, start: str, end: str) -> str:
    i = text.find(start)
    if i < 0:
        raise LookupError(f"{start!r} not found")
    j = text.find(end, i + len(start))
    return text[i:j if j >= 0 else len(text)].strip()


def hubs_from_brief(brief: str) -> list[str]:
    match = re.search(r"Hubs \(`topics`[^)]*\):\s*(.+?)\.\s*(?:\n\s*\n|\Z)", brief, re.S)
    if not match:
        raise LookupError("hub list not found in the note-writer brief")
    return [h for h in re.split(r"[,\s]+", match[1]) if h]


def system_text(brief: str, filing: str) -> str:
    parts = [WRAPPER, "# Note-writing brief (excerpt)"]
    parts += [md_section(brief, heading) for heading in BRIEF_SECTIONS]
    parts.append("# Filing rules (excerpt)")
    parts += [between(filing, start, end) for start, end in FILING_SPANS]
    return "\n\n".join(parts)


def response_schema(hubs: list[str]) -> dict:
    text = {"type": "STRING"}
    fields = {
        "status": {"type": "STRING", "enum": ["written", "blocked"]},
        "blocked_reason": text,
        "prompt": text,
        "title": text,
        "slug": text,
        "author": text,
        "topics": {"type": "ARRAY", "items": {"type": "STRING", "enum": hubs}},
        "tags": {"type": "ARRAY", "items": text},
        "prompt_answer": text,
        "tldr": {"type": "ARRAY", "items": text},
        "what_they_said": text,
        "key_takeaways": {"type": "ARRAY", "items": text},
        "dissection": text,
        "key_stills": {"type": "ARRAY", "items": {"type": "OBJECT", "properties": {
            "file": text, "name": text, "shows": text}, "required": ["file", "name"]}},
        "hub_line": text,
        "projects": {"type": "ARRAY", "items": {"type": "OBJECT", "properties": {
            "repo": text, "why": text}, "required": ["repo", "why"]}},
        "coverage": text,
        "flags": {"type": "ARRAY", "items": text},
    }
    return {"type": "OBJECT", "properties": fields, "propertyOrdering": list(fields),
            "required": ["status", "title", "slug", "topics", "tags", "prompt_answer", "tldr",
                         "what_they_said", "key_takeaways", "hub_line", "coverage"]}


def label_for(path: Path, key: str) -> str:
    name = path.name
    if name.endswith(".description.txt"):
        return "caption / post text"
    if name.endswith(".ocr.txt"):
        return "on-screen text (OCR, noisy)"
    if name.endswith(".gemini.txt"):
        return "speech transcript (model transcription)"
    if name == "thread.txt":
        return "post thread text"
    if re.fullmatch(rf"{re.escape(key)}(-\d+)?\.txt", name):
        return "speech transcript (Whisper, may mishear names)"
    return "captured text"


def discover(downloads: Path, key: str) -> tuple[list[Path], list[Path]]:
    if not key or not downloads.is_dir():
        return [], []
    texts = sorted(p for p in downloads.rglob(f"{key}*.txt")
                   if p.is_file() and not p.name.endswith(".capture-error.txt"))
    texts += sorted(p for p in downloads.rglob("thread.txt") if p.parent.name == key)
    images = sorted(p for folder in ("slides", "photos") for p in downloads.rglob(f"{key}/{folder}/*")
                    if p.suffix.lower() in IMAGE_TYPES)
    return texts, images


def file_entries(item: dict, downloads: Path) -> tuple[list[tuple[Path, str]], list[Path]]:
    key = str(item.get("key", ""))
    found_texts, found_images = discover(downloads, key)
    if item.get("files"):
        texts = []
        for entry in item["files"]:
            path = Path(entry["path"] if isinstance(entry, dict) else entry)
            label = entry.get("label") if isinstance(entry, dict) else None
            texts.append((path, label or label_for(path, key)))
    else:
        texts = [(p, label_for(p, key)) for p in found_texts]
    images = [Path(p) for p in item["images"]] if "images" in item else found_images
    return texts, images


def material(texts: list[tuple[Path, str]]) -> tuple[str, list[str]]:
    """Labelled captured text, capped per file and in total. Returns the text and cut files."""
    parts, cut, total = [], [], 0
    for path, label in texts:
        try:
            body = path.read_text(encoding="utf-8", errors="replace").strip()
        except OSError:
            continue
        if not body:
            continue
        room = min(TEXT_CAP, TOTAL_CAP - total)
        if room <= 0:
            cut.append(path.name)
            continue
        if len(body) > room:
            body = body[:room] + f"\n[cut here: {len(body) - room:,} more characters not sent]"
            cut.append(path.name)
        parts.append(f"## {label} ({path.name})\n{body}")
        total += len(body)
    return "\n\n".join(parts), cut


def image_parts(images: list[Path], limit: int) -> tuple[list[dict], dict[str, Path]]:
    parts, named = [], {}
    for path in images:
        if len(named) >= limit:
            break
        mime = IMAGE_TYPES.get(path.suffix.lower())
        if not mime or not path.is_file() or path.stat().st_size > IMAGE_MAX_BYTES or path.name in named:
            continue
        parts.append({"text": f"Image: {path.name}"})
        parts.append({"inline_data": {"mime_type": mime, "data": base64.b64encode(path.read_bytes()).decode()}})
        named[path.name] = path
    return parts, named


def explicit_prompts(item: dict) -> list[str] | None:
    """The saved prompts, or None when they weren't split out and `title` holds them.

    An empty `prompts` beside a non-empty `title` counts as not split: on 2026-11-03 a
    run passed `prompts: []` with every raw saved title, and six typed prompts would
    have gone unanswered."""
    if "prompts" not in item:
        return None
    prompts = [str(p).strip() for p in item.get("prompts") or [] if str(p).strip()]
    if not prompts and str(item.get("title") or "").strip():
        return None
    return prompts


def typed_text(item: dict) -> list[str]:
    raw = item.get("typed_text") or []
    return [str(t).strip() for t in ([raw] if isinstance(raw, str) else raw) if str(t).strip()]


def save_text(item: dict, material_text: str, image_names: list[str]) -> str:
    kind = item.get("type", "")
    lines = ["# The save", f"Platform: {kind}", f"Link: {item.get('source', '')}"]
    if item.get("author"):
        lines.append(f"Author: {item['author']}")
    prompts = explicit_prompts(item)
    if prompts is None and item.get("title"):
        lines.append(f"Saved title (no separate prompt given): {json.dumps(item['title'], ensure_ascii=False)}")
    elif prompts:
        lines.append("Saved prompts, oldest first:")
        lines += [f"{i}. {json.dumps(p, ensure_ascii=False)}" for i, p in enumerate(prompts, 1)]
    else:
        lines.append(f"Saved prompt: {NO_PROMPT}")
    for text in typed_text(item):
        lines.append(f"Text typed with the save: {json.dumps(text, ensure_ascii=False)}")
    lines += ["", "# Material", material_text or "(no captured text)"]
    if image_names:
        lines += ["", "# Attached images", ", ".join(image_names)]
    return "\n".join(lines)


def request_body(system: str, schema: dict, user_text: str, images: list[dict]) -> dict:
    return {"system_instruction": {"parts": [{"text": system}]},
            "contents": [{"role": "user", "parts": [{"text": user_text}, *images]}],
            "generationConfig": {"responseMimeType": "application/json", "responseSchema": schema}}


def answer_problem(text: str, hubs: list[str]) -> str | None:
    try:
        data = json.loads(text)
    except ValueError:
        return "answer was not JSON"
    if not isinstance(data, dict):
        return "answer was not a JSON object"
    if data.get("status") == "blocked":
        return None if str(data.get("blocked_reason", "")).strip() else "blocked without a reason"
    if data.get("status") != "written":
        return "answer has no status"
    for field in ("title", "prompt_answer", "what_they_said"):
        if not str(data.get(field, "")).strip():
            return f"empty {field}"
    if not data.get("tldr") or not data.get("key_takeaways"):
        return "empty TL;DR or takeaways"
    if not [t for t in data.get("topics") or [] if t in hubs]:
        return "no known hub in topics"
    return None


def one_line(text) -> str:
    text = re.sub(r"\s+", " ", str(text)).strip()
    return re.sub(r"^(?:[-*•]|\d+[.)])\s+", "", text)


def paragraphs(text) -> str:
    text = re.sub(r"(?m)^#+\s*", "", str(text)).strip()
    return re.sub(r"\n{3,}", "\n\n", text)


def kebab(text, words: int = 6) -> str:
    return "-".join(re.findall(r"[a-z0-9]+", str(text).lower())[:words])


def yaml(value) -> str:
    return json.dumps(value, ensure_ascii=False)


NUMBER = re.compile(r"\d[\d,]*(?:\.\d+)?")


def numbers(text: str) -> set[str]:
    found = set()
    for match in NUMBER.finditer(text):
        value = match[0].replace(",", "").rstrip(".")
        if len(value.replace(".", "")) >= 2:
            found.add(value)
    return found


def unsourced_numbers(data: dict, sources: str) -> list[str]:
    written = " ".join(" ".join(map(str, v)) if isinstance(v, list) else str(v)
                       for f in WRITTEN_FIELDS if (v := data.get(f)))
    have = numbers(sources)
    return sorted(numbers(written) - have, key=lambda n: (len(n), n))


def already_filed(vault: Path | None, key: str) -> Path | None:
    if not vault or not key or not vault.is_dir():
        return None
    return next((p for p in vault.rglob(f"{key}-*.md") if not p.name.startswith("_")), None)


def author_name(author: str) -> str:
    """The name part of an author line: quotes, handles and a trailing (...) removed."""
    name = str(author).strip().strip('"').strip()
    name = re.sub(r"\s*\(.*\)\s*$", "", name)
    return re.sub(r"@[\w.]+", "", name).strip(" ,;-\u2013\u2014")


def grounded_author(author: str, sources: str) -> str:
    """Gemini's author, kept only as far as the material names it.

    Each @handle and each name (outside or inside parentheses, so "@handle (Name)" and
    "Name (with @handle)" both work) must appear in the material; an author with a part
    that doesn't is cut back to the parts that do. On 2026-11-03, 31 of 68 drafts had an
    invented or wrong author or handle."""
    author, text = one_line(author), sources.lower()
    if not author:
        return ""
    handles = re.findall(r"@[\w.]+", author)
    found = [h for h in handles
             if re.search(r"(?<![\w.])" + re.escape(h.lower()) + r"(?![\w]|\.\w)", text)]
    names = []
    for segment in re.split(r"[()]", re.sub(r"@[\w.]+", " ", author)):
        name = re.sub(r"\b(?:with|and|feat\.?|ft\.?|x)\b|&", " ", segment, flags=re.I)
        name = re.sub(r"\s+", " ", name).strip(" ,;:-/\u2013\u2014")
        if name:
            names.append(name)
    named = [n for n in names if n.lower() in text]
    if len(found) == len(handles) and len(named) == len(names):
        return author
    if found:
        return f"{found[0]} ({named[0]})" if named else found[0]
    return named[0] if named else ""


MD_LINK = re.compile(r"\[([^\]\n]*)\]\((\S+?)\)")
WIKI_LINK = re.compile(r"\[\[([^\]|\n]+)(?:\|([^\]\n]+))?\]\]")
BARE_URL = re.compile(r"(?:https?://|www\.)[^\s<>()\[\]\"']+"
                      r"|(?<![\w@.-])[a-z0-9][\w-]*\.(?:com|org|net|io|co|app|ai|dev|shop|store|me|tv|in|uk|us)\b(?:/[^\s<>()\[\]\"']*)?",
                      re.I)


def link_in(url: str, sources: str) -> bool:
    core = re.sub(r"^(?:https?://)?(?:www\.)?", "", url.rstrip(".,;:!?")).rstrip("/").lower()
    return bool(core) and core in sources.lower()


def remove_unsourced_links(data: dict, sources: str) -> int:
    """Drop links the material doesn't contain from the written fields; return how many.

    On 2026-11-03, 15 of 68 drafts had links that weren't in the source."""
    removed = 0

    def clean(value: str) -> str:
        nonlocal removed

        def wiki(m):
            nonlocal removed
            removed += 1
            return (m[2] or m[1].split("/")[-1]).strip()

        def markdown(m):
            nonlocal removed
            if link_in(m[2], sources):
                return m[0]
            removed += 1
            return m[1]

        def bare(m):
            nonlocal removed
            url = m[0].rstrip(".,;:!?")
            if link_in(url, sources):
                return m[0]
            removed += 1
            return m[0][len(url):]

        value = MD_LINK.sub(markdown, WIKI_LINK.sub(wiki, value))
        value = BARE_URL.sub(bare, value)
        value = re.sub(r"[ \t]{2,}", " ", re.sub(r"\s*\(\s*\)", "", value))
        return re.sub(r"[ \t]+([.,;:!?])", r"\1", value)

    for field in ("title", "prompt_answer", "tldr", "what_they_said", "key_takeaways", "dissection", "hub_line"):
        value = data.get(field)
        if isinstance(value, str):
            data[field] = clean(value)
        elif isinstance(value, list):
            data[field] = [clean(v) if isinstance(v, str) else v for v in value]
    return removed


def related_by_author(vault: Path | None, author: str, key: str, limit: int = 3) -> list[str]:
    if not vault or not vault.is_dir() or not author:
        return []
    handle = re.search(r"@[\w.]+", author)
    needle = (handle[0] if handle else author_name(author)).strip().lower()
    if len(needle) < 4:
        return []
    # A handle matches only as a whole handle (@chef isn't @chefjohn) and a name only as
    # the whole name: on 2026-11-03 loose matches linked unrelated notes.
    if handle:
        token = re.compile(r"(?<![\w.])" + re.escape(needle) + r"(?![\w]|\.\w)")
        same = lambda other: bool(token.search(other.lower()))
    else:
        same = lambda other: author_name(other).lower() == needle
    hits = []
    for path in sorted(vault.rglob("*.md")):
        rel = path.relative_to(vault).as_posix()
        if path.name.startswith("_") or key in path.name or rel.startswith(PRIVATE_DIRS):
            continue
        try:
            with path.open(encoding="utf-8", errors="replace") as handle_file:
                head = handle_file.read(2000)
        except OSError:
            continue
        match = re.search(r"(?m)^author:\s*(.+)$", head)
        if match and same(match[1]):
            hits.append(rel[:-3])
            if len(hits) >= limit:
                break
    return hits


def render(item: dict, data: dict, *, prompt: str, folder: str, model: str, run_date: str,
           stills: list[tuple[str, str]], related: list[str]) -> str:
    kind = item.get("type", "")
    hubs = data["topics"]
    ids = item.get("notion_page_ids") or ([item["notion_page_id"]] if item.get("notion_page_id") else [])
    added = str(item.get("added_at", ""))
    tags = [PLATFORM_TAG.get(kind, kind), hubs[0]]
    for tag in data.get("tags") or []:
        tag = kebab(tag, 5)
        if tag and tag not in tags and len(tags) < 6:
            tags.append(tag)
    front = [
        "---",
        f"type: {kind}",
        f"source: {yaml(item.get('source', ''))}",
        f"saved_url: {yaml(item.get('saved_url') or item.get('source', ''))}",
        f"media_id: {yaml(str(item.get('key', '')))}",
        f"notion_page_id: {yaml(ids[0] if len(ids) == 1 else ids)}",
        f"added_at: {yaml(added)}",
        f"date: {yaml(added[:10])}",
        f"extracted: {yaml(run_date)}",
        f"author: {yaml(item.get('author') or one_line(data.get('author', '')) or 'unknown')}",
        f"user_question: {yaml(prompt)}",
        "capture_context: Notion",
        f"capture_method: {yaml(item.get('capture_method', ''))}",
        f"drafted_by: {yaml(model + ' (Gemini API)')}",
        "status: extracted",
        f"topics: {yaml(hubs)}",
        f"tags: {yaml(tags)}",
        "---",
    ]
    answer = one_line(data["prompt_answer"]).replace("**", "")
    body = [f"# {one_line(data['title'])}", "", "## TL;DR", f"- **Your prompt:** {prompt} **{answer}**"]
    body += [f"- {one_line(b)}" for b in data["tldr"]
             if one_line(b) and not one_line(b).replace("*", "").lower().startswith("your prompt")]
    body += ["", "## What they said", paragraphs(data["what_they_said"])]
    for file_name, _ in stills:
        body += ["", f"![[{folder}/_media/{item['key']}/{file_name}]]"]
    body += ["", "## Key takeaways"] + [f"- {one_line(b)}" for b in data["key_takeaways"] if one_line(b)]
    if str(data.get("dissection", "")).strip():
        body += ["", "## Dissection", paragraphs(data["dissection"])]
    body += ["", "## Related", "- " + " · ".join(f"[[{h}/_index]]" for h in hubs)]
    body += [f"- [[{r}]]" for r in related]
    body.append(f"- Source: {item.get('source', '')}")
    return "\n".join(front) + "\n\n" + "\n".join(body) + "\n"


class Drafter:
    def __init__(self, *, key: str, models, out: Path, vault: Path | None, downloads: Path, run_date: str,
                 max_images: int = 6, redo: bool = False, opener=None, sleep=time.sleep):
        brief, filing = BRIEF.read_text(encoding="utf-8"), FILING.read_text(encoding="utf-8")
        hubs = hubs_from_brief(brief)
        present = [h for h in hubs if ((vault or out) / h / "_index.md").is_file()]
        # A vault with hub folders offers only those. The knowledge vault leaves out
        # decisions/, which stays in Benny's own vault on the Mac: on 2026-11-04 a note
        # tagged decisions had no hub index to be listed in. An empty folder offers all.
        self.hubs = present or hubs
        self.system = system_text(brief, filing)
        self.schema = response_schema(self.hubs)
        self.key, self.models, self.out, self.vault = key, tuple(models), out, vault
        self.downloads, self.run_date, self.max_images, self.redo = downloads, run_date, max_images, redo
        self.opener, self.sleep, self.skip, self.strikes = opener, sleep, set(), {}

    def build(self, item: dict) -> tuple[dict, dict[str, Path], str, list[str]]:
        texts, images = file_entries(item, self.downloads)
        text, cut = material(texts)
        parts, named = image_parts(images, self.max_images)
        user = save_text(item, text, list(named))
        return request_body(self.system, self.schema, user, parts), named, text, cut

    def draft(self, item: dict, dry_run: bool = False) -> dict:
        key = str(item.get("key", "")).strip()
        result = {"key": key, "drafted_by": "gemini"}
        if not key or not item.get("source") or item.get("type") not in PLATFORM_TAG:
            return {**result, "status": "failed", "reason": "item needs key, source and a known type"}
        if str(item.get("status", "")).lower() in SKIP_STATUS:
            return {**result, "status": "passed-over", "reason": f"capture status {item['status']}"}
        existing = already_filed(self.vault, key)
        if existing and not self.redo:
            return {**result, "status": "exists", "vault_path": existing.relative_to(self.vault).as_posix()}
        body, named, text, cut = self.build(item)
        if not text and not named:
            return {**result, "status": "blocked", "reason": "no captured text or images"}
        if dry_run:
            return {**result, "status": "dry-run", "material_chars": len(text), "images": list(named),
                    "cut": cut, "request_chars": len(json.dumps(body))}
        answer = generate(body, self.key, self.models, accept=lambda t: answer_problem(t, self.hubs),
                          opener=self.opener, sleep=self.sleep, skip=self.skip, strikes=self.strikes,
                          deadline=DEADLINE)
        result["tries"] = answer["tries"]
        if not answer["ok"]:
            return {**result, "status": "failed", "reason": "no model returned a usable draft"}
        data = json.loads(answer["text"])
        result.update(model=answer["model"], usage=answer.get("usage"))
        if data.get("status") == "blocked":
            return {**result, "status": "blocked", "reason": one_line(data.get("blocked_reason", ""))}
        return self.write(item, data, answer["model"], text, cut, named, result)

    def write(self, item, data, model, text, cut, named, result) -> dict:
        key = result["key"]
        data["topics"] = [t for t in dict.fromkeys(data.get("topics") or []) if t in self.hubs]
        details = " ".join([text, *typed_text(item), *(str(p) for p in item.get("prompts") or []),
                            str(item.get("title", "")), str(item.get("author", "")),
                            str(item.get("source", "")), str(item.get("saved_url", ""))])
        data["author"] = grounded_author(data.get("author", ""), details)
        links_removed = remove_unsourced_links(data, details)
        prompts = explicit_prompts(item)
        if prompts is None:
            guess = one_line(data.get("prompt", ""))
            title = str(item.get("title", ""))
            prompts = [guess] if guess and guess != NO_PROMPT and guess in title else []
        prompt = " | later save: ".join(prompts) or NO_PROMPT
        folder = (item.get("folder") or ARCHIVE.get(item["type"]) or data["topics"][0]).strip("/")
        slug = kebab(data.get("slug") or data["title"]) or "note"
        while slug.startswith(kebab(key) + "-"):
            slug = slug[len(kebab(key)) + 1:]
        note = self.out / folder / f"{key}-{slug}.md"
        if note.exists():
            return {**result, "status": "failed", "reason": f"{note.name} already exists; not overwritten"}
        stills = []
        for still in (data.get("key_stills") or [])[:3]:
            source = named.get(str(still.get("file", "")).strip())
            name = kebab(still.get("name", ""), 8)
            if source and name:
                stills.append((f"{name}{source.suffix.lower()}", source))
        related = related_by_author(self.vault, item.get("author") or data.get("author", ""), key)
        markdown = render(item, data, prompt=prompt, folder=folder, model=model, run_date=self.run_date,
                          stills=stills, related=related)
        note.parent.mkdir(parents=True, exist_ok=True)
        note.write_text(markdown, encoding="utf-8")
        for file_name, source in stills:
            target = self.out / folder / "_media" / key / file_name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)
        sources = " ".join([text, prompt, *typed_text(item), str(item.get("author", ""))])
        unsourced = unsourced_numbers(data, sources)
        flags = [one_line(f) for f in data.get("flags") or [] if one_line(f)]
        if unsourced:
            flags.append("check-numbers")
        coverage = one_line(data.get("coverage", "")) or "full"
        if cut:
            coverage = f"partial: material cut for length ({', '.join(cut)})"
        return {**result, "status": "written", "vault_path": note.relative_to(self.out).as_posix(),
                "title": one_line(data["title"]), "topics": data["topics"], "hub_line": one_line(data["hub_line"]),
                "projects": data.get("projects") or [], "prompt": prompt, "coverage": coverage, "flags": flags,
                "unsourced_numbers": unsourced, "links_removed": links_removed, "stills": [f for f, _ in stills]}


def load_items(path: Path) -> list[dict]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(data, dict):
        data = data.get("items", [data])
    if not isinstance(data, list) or not all(isinstance(i, dict) for i in data):
        raise ValueError("expected an item, a list of items or {\"items\": [...]}")
    return data


def pick(items: list[dict], span: str | None, only: list[str] | None) -> list[dict]:
    if span:
        start, _, end = span.partition(":")
        items = items[int(start or 0):int(end) if end else None]
    if only:
        items = [i for i in items if str(i.get("key")) in set(only)]
    return items


def status_line(row: dict) -> str:
    usage = row.get("usage") or {}
    bits = [f"{row['status']:<11}", row["key"], row.get("vault_path") or row.get("reason", "")]
    if row.get("model"):
        bits.append(f"{row['model']} in={usage.get('promptTokenCount', '?')} out={usage.get('candidatesTokenCount', '?')}")
    if row.get("unsourced_numbers"):
        bits.append(f"numbers-to-check={len(row['unsourced_numbers'])}")
    if row.get("links_removed"):
        bits.append(f"links-removed={row['links_removed']}")
    return "  ".join(str(b) for b in bits if b)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("items", type=Path, help="JSON file: one item, a list, or {\"items\": [...]}")
    parser.add_argument("--out", type=Path, required=True, help="Vault root the notes are written under")
    parser.add_argument("--results", type=Path, required=True, help="JSON lines file that gets one result per item")
    parser.add_argument("--vault", type=Path, help="Existing vault to check for filed keys and same-author notes (default: --out)")
    parser.add_argument("--range", dest="span", help="START:END slice of the items, 0-based, END excluded")
    parser.add_argument("--only", action="append", help="Draft only this key (repeatable)")
    parser.add_argument("--downloads", type=Path, default=downloads_dir(), help="Where captures are found by key (default: %(default)s)")
    parser.add_argument("--models", default=",".join(DEFAULT_MODELS), help="Comma-separated, tried in order (default: %(default)s)")
    parser.add_argument("--key-env", default=DEFAULT_KEY_ENV, help="Environment variable holding the Gemini key (default: %(default)s)")
    parser.add_argument("--max-images", type=int, default=6, help="Images attached per item (default: %(default)s)")
    parser.add_argument("--gap", type=float, default=4.0, help="Seconds between items, for per-minute limits (default: %(default)s)")
    parser.add_argument("--run-date", default=datetime.now(timezone.utc).strftime("%Y-%m-%d"))
    parser.add_argument("--redo", action="store_true", help="Draft even when a note for the key is already filed (comparisons)")
    parser.add_argument("--dry-run", action="store_true", help="Build each request and report its size; no Gemini call")
    args = parser.parse_args(argv)
    try:
        items = pick(load_items(args.items), args.span, args.only)
    except (OSError, ValueError) as exc:
        print(json.dumps({"ok": False, "error": str(exc)}))
        return 2
    key = os.environ.get(args.key_env, "").strip()
    if not key and not args.dry_run:
        print(json.dumps({"ok": False, "error": f"{args.key_env} is not set"}))
        return 2
    tier = {"tier": "not checked"}
    if not args.dry_run:
        tier = tier_check(key)
        if tier["tier"] == "billed":
            print(json.dumps({"ok": False, "error": f"billing on the key's project: {tier['detail']}"}))
            return 4
        if tier["tier"] == "unknown":
            print(f"free tier not confirmed ({tier['detail']}); a billing sign stops the run", file=sys.stderr)
    drafter = Drafter(key=key, models=[m.strip() for m in args.models.split(",") if m.strip()], out=args.out,
                      vault=args.vault or args.out, downloads=args.downloads, run_date=args.run_date,
                      max_images=args.max_images, redo=args.redo)
    counts, requests, tokens, stopped = {}, 0, [0, 0], None
    args.results.parent.mkdir(parents=True, exist_ok=True)
    for index, item in enumerate(items):
        if stopped:
            row = {"key": str(item.get("key", "")), "status": "not-sent", "reason": "Gemini stopped: billing"}
        else:
            if index and not args.dry_run:
                time.sleep(args.gap)
            try:
                row = drafter.draft(item, dry_run=args.dry_run)
            except BillingLinked as exc:
                stopped = str(exc)
                row = {"key": str(item.get("key", "")), "status": "not-sent", "reason": f"billing: {stopped}"}
        counts[row["status"]] = counts.get(row["status"], 0) + 1
        requests += len(row.get("tries") or [])
        usage = row.get("usage") or {}
        tokens[0] += usage.get("promptTokenCount") or 0
        tokens[1] += usage.get("candidatesTokenCount") or 0
        with args.results.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")
        print(status_line(row), flush=True)
    summary = {"summary": counts, "requests": requests, "tokens_in": tokens[0], "tokens_out": tokens[1],
               "tier": tier["tier"], "skipped_models": sorted(drafter.skip), "billing_stop": stopped}
    print(json.dumps(summary), flush=True)
    if stopped:
        return 4
    return 3 if counts.get("failed") else 0


if __name__ == "__main__":
    raise SystemExit(main())
