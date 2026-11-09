"""Offline checks for an explicitly named batch of Obsidian source notes.

No vault discovery, recursive scan, writes, Obsidian process or network calls.
This checks file delivery and syntax, not the truth of a source or a human review.
"""
from __future__ import annotations

import argparse
import json
import os
import posixpath
import re
import sys
import unicodedata
from datetime import date, datetime
from pathlib import Path
from urllib.parse import unquote, urlsplit

try:
    import yaml
except ImportError:
    yaml = None

PRIVATE_PARTS = {"private notes", "personal notes", "notion"}
MAX_BYTES = 2_000_000
NO_NOTE = {"blocked", "failed", "fail", "passed-over", "skipped", "skip"}
MEDIA_EXTENSIONS = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg", ".pdf", ".mp3", ".m4a",
                    ".wav", ".ogg", ".flac", ".mp4", ".webm", ".mov", ".avif", ".bmp"}


def scoped(root: Path, value: str, base: Path | None = None) -> Path:
    """Reject private/out-of-root targets before inspecting their contents."""
    path = Path(value)
    if any(p.casefold() in PRIVATE_PARTS for p in root.parts + path.parts):
        raise ValueError("private or mirror path is outside the filing scope")
    root = root.resolve()
    path = path if path.is_absolute() else (base or root) / path
    path = Path(os.path.abspath(path))
    if not path.is_relative_to(root):
        raise ValueError("path leaves the explicitly supplied vault root")
    path = path.resolve()
    if any(p.casefold() in PRIVATE_PARTS for p in path.parts):
        raise ValueError("private or mirror path is outside the filing scope")
    if not path.is_relative_to(root):
        raise ValueError("path leaves the explicitly supplied vault root")
    return path


def read_text(path: Path) -> str:
    if path.stat().st_size > MAX_BYTES:
        raise ValueError("file exceeds the 2 MB source-note check limit")
    return path.read_text(encoding="utf-8-sig")


def external(target: str) -> bool:
    return bool(re.match(r"^[a-zA-Z][a-zA-Z0-9+.-]*:", target))


def frontmatter(text: str) -> tuple[dict, str]:
    if yaml is None:
        raise ValueError("PyYAML is required; install requirements-filing.txt")
    match = re.match(r"\A---\r?\n(.*?)\r?\n---(?:\r?\n|\Z)", text, re.S)
    if not match:
        raise ValueError("missing or unclosed YAML frontmatter")

    class UniqueLoader(yaml.SafeLoader):
        pass

    def mapping(loader, node, deep=False):
        keys = set()
        for key, _ in node.value:
            name = loader.construct_object(key, deep=deep)
            if not isinstance(name, str) or name in keys:
                raise ValueError("frontmatter property names must be unique strings")
            keys.add(name)
        return yaml.SafeLoader.construct_mapping(loader, node, deep=deep)

    UniqueLoader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, mapping)
    try:
        props = yaml.load(match[1], Loader=UniqueLoader)
    except yaml.YAMLError as exc:
        raise ValueError("invalid YAML frontmatter") from exc
    if not isinstance(props, dict):
        raise ValueError("frontmatter must be a property mapping")
    return props, text[match.end():]


def prose(text: str) -> str:
    """Ignore example code and hidden comments when checking actual links."""
    lines, fence = [], None
    for line in text.splitlines():
        marker = re.match(r"^\s{0,3}(`{3,}|~{3,})(.*)$", line)
        if marker:
            token = marker[1]
            if fence is None:
                fence = token
            elif token[0] == fence[0] and len(token) >= len(fence) and not marker[2].strip():
                fence = None
            continue
        if fence is None:
            lines.append(line)
    text = re.sub(r"<!--.*?-->|%%.*?%%", "", "\n".join(lines), flags=re.S)
    return re.sub(r"(?<!`)(`+)(?!`)(.*?)(?<!`)\1(?!`)", "", text)


def links(text: str):
    text = prose(text)
    for match in re.finditer(r"!?\[\[([^\]\n]+)\]\]", text):
        yield match[1].replace(r"\|", "|").split("|", 1)[0].strip(), "wiki", match[0].startswith("!")
    for match in re.finditer(r"!?\[[^\]\n]*\]\((<[^>\n]+>|[^\s)]+)(?:\s+[^)]*)?\)", text):
        yield match[1].strip("<>"), "markdown", match[0].startswith("!")


def targets(root: Path, note: Path, target: str, kind: str = "wiki") -> list[Path]:
    target = unquote(target.split("#", 1)[0])
    if not target:
        return [note]
    if "\\" in target:
        raise ValueError("use forward slashes in vault links")
    if target.startswith(("./", "../")):
        bases = [note.parent]
    elif kind == "markdown":
        bases = list(dict.fromkeys([note.parent, root]))
    else:
        bases = [root] if "/" in target else [note.parent, root]
    found = []
    for base in bases:
        candidate = scoped(root, target, base)
        variants = [candidate]
        if candidate.name:
            variants.append(candidate.with_name(candidate.name + ".md"))
        for variant in variants:
            try:
                path = scoped(root, str(variant))
            except ValueError:
                continue  # an extension probe is never permission to leave root
            if path.is_file() and path not in found:
                found.append(path)
    return found


def valid_tag(value: str) -> bool:
    return (bool(value) and not value.isdecimal() and not any(not p for p in value.split("/"))
            and all(not c.isspace() and not unicodedata.category(c).startswith("C")
                    and (ord(c) > 127 or c.isalnum() or c in "_-/") for c in value))


def hub_backlink(text: str, hub: Path, note: Path, root: Path) -> tuple[bool, bool]:
    """Compare link destinations, without inspecting unrelated hub targets."""
    expected = note.relative_to(root).as_posix().removesuffix(".md")
    for target, kind, _ in links(text):
        if kind == "markdown" and external(target):
            continue
        target = unquote(target.split("#", 1)[0]).removesuffix(".md")
        relative = posixpath.normpath(hub.parent.relative_to(root).as_posix() + "/" + target)
        if target.startswith(("./", "../")):
            candidates = [relative]
        elif kind == "markdown":
            candidates = [relative, posixpath.normpath(target)]
        else:
            candidates = [posixpath.normpath(target)]
        if expected in candidates:
            return True, len(targets(root, hub, target, kind)) > 1
        if target == note.stem:
            return True, True
    return False, False


def check(root: Path, record: dict, *, reviewed=False, hubs=False, hub_cache=None) -> dict:
    label = str(record.get("vault_path", ""))
    result = {"vault_path": "(out-of-scope or invalid path)", "errors": [], "warnings": []}
    errors, warnings = result["errors"], result["warnings"]
    try:
        if any(p.casefold() in PRIVATE_PARTS for p in root.parts):
            raise ValueError("private or mirror path is outside the filing scope")
        root = root.resolve()
        note = scoped(root, label)
        if not label or note.suffix.lower() != ".md":
            raise ValueError("vault_path must name a Markdown source note")
        result["vault_path"] = note.relative_to(root).as_posix()
        props, body = frontmatter(read_text(note))
    except (OSError, ValueError) as exc:
        errors.append(str(exc) if isinstance(exc, ValueError) else "source note cannot be read")
        return result
    for name in ("type", "source", "media_id"):
        if not isinstance(props.get(name), str) or not props[name].strip():
            errors.append(f"{name} must be non-empty text")
    if record.get("key") and props.get("media_id") != str(record["key"]):
        errors.append("media_id differs from the batch record key")
    try:
        source = urlsplit(str(props.get("source", "")))
        source_ok = source.scheme in {"http", "https"} and bool(source.netloc) and not any(c.isspace() for c in source.netloc)
    except ValueError:
        source_ok = False
    if not source_ok:
        errors.append("source must be an http(s) URL")
    for name in ("tags", "topics", "aliases", "cssclasses"):
        if name not in props and name not in {"tags", "topics"}:
            continue
        value = props.get(name)
        if not isinstance(value, list) or any(not isinstance(v, str) or not v.strip() for v in value):
            errors.append(f"{name} must be a list of non-empty strings")
        elif name in {"tags", "topics"} and not value:
            errors.append(f"{name} must not be empty")
    if isinstance(props.get("tags"), list) and any(not isinstance(t, str) or not valid_tag(t) for t in props["tags"]):
        errors.append("tags contain an invalid tag (no #, spaces, empty segments or numbers-only tags)")
    for name, value in props.items():
        if isinstance(value, dict) or isinstance(value, list) and any(isinstance(v, (dict, list)) for v in value):
            errors.append(f"{name} is nested; keep Obsidian properties flat")
    for old, current in {"tag": "tags", "alias": "aliases", "cssclass": "cssclasses"}.items():
        if old in props:
            errors.append(f"deprecated {old} property; use {current}")
    if reviewed and props.get("status") != "reviewed":
        errors.append("status must be reviewed after the agent's content review")
    if props.get("capture_context") == "Notion" or props.get("notion_page_id"):
        for name in ("saved_url", "added_at", "notion_page_id", "user_question"):
            if not props.get(name):
                errors.append(f"Notion source is missing {name}")
            elif name != "notion_page_id" and not (isinstance(props[name], str) or name == "added_at" and isinstance(props[name], datetime)):
                errors.append(f"{name} must preserve a text value")
        ids = props.get("notion_page_id")
        if ids and not (isinstance(ids, str) or isinstance(ids, list) and all(isinstance(v, str) and v for v in ids)):
            errors.append("notion_page_id must preserve a text ID or list of text IDs")
    for name in ("date", "extracted"):
        if name in props:
            try:
                value = str(props[name])
                if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
                    raise ValueError()
                date.fromisoformat(value)
            except ValueError:
                errors.append(f"{name} must be YYYY-MM-DD")
    if props.get("added_at"):
        try:
            value = str(props["added_at"])
            if not re.match(r"^\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}", value):
                raise ValueError()
            datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            errors.append("added_at must preserve an ISO date/time")
    actual = prose(body)
    if not re.search(r"(?m)^# \S", actual):
        errors.append("missing note title")
    prompt = props.get("user_question", "")
    if prompt and not str(prompt).startswith("(none"):
        tldr = re.search(r"(?ms)^## TL;DR\s*\n(.*?)(?=^## |\Z)", actual)
        first = tldr[1].replace("**", "").replace("__", "") if tldr else ""
        if not re.match(r"\s*[-*]\s+Your prompt:", first):
            errors.append("first TL;DR bullet must answer Your prompt")
    property_links = [v for value in props.values() for v in (value if isinstance(value, list) else [value])
                      if isinstance(v, str) and v.startswith("[[") and v.endswith("]]")]
    required_hubs = {f"{t}/_index" for t in props.get("topics", []) if isinstance(t, str)} if hubs and isinstance(props.get("topics"), list) else set()
    for target, kind, embedded in links(body + "\n" + "\n".join(property_links)):
        if kind == "markdown" and external(target):
            continue  # external URLs are not fetched
        try:
            found = targets(root, note, target, kind)
            if not found:
                message = f"unresolved {kind} link: {target}"
                path = unquote(target.split("#", 1)[0])
                required = (embedded or Path(path).suffix.lower() in MEDIA_EXTENSIONS
                            or any(p in {"_media", "_audio"} for p in Path(path).parts)
                            or path.removesuffix(".md") in required_hubs)
                (errors if required else warnings).append(message)
            if (embedded or Path(target.split("#", 1)[0]).suffix.lower() in MEDIA_EXTENSIONS) and "downloads" in {p.casefold() for p in Path(unquote(target)).parts}:
                errors.append("retained media must not link to prunable downloads")
            elif len(found) > 1:
                warnings.append(f"ambiguous link; verify resolution or use an unambiguous path: {target}")
        except ValueError as exc:
            errors.append(f"link rejected: {exc}")
    if hubs and isinstance(props.get("topics"), list):
        hub_cache = {} if hub_cache is None else hub_cache
        for topic in props["topics"]:
            if not isinstance(topic, str):
                continue
            try:
                hub = scoped(root, f"{topic}/_index.md")
            except ValueError:
                errors.append("hub path rejected: outside the filing scope")
                continue
            hub_label = hub.relative_to(root).as_posix()
            try:
                if hub not in hub_cache:
                    hub_cache[hub] = read_text(hub)
                linked, short = hub_backlink(hub_cache[hub], hub, note, root)
                if not linked:
                    errors.append(f"hub {hub_label} has no backlink to this note")
                elif short:
                    warnings.append(f"hub {hub_label} uses a short or ambiguous backlink; verify resolution or use an unambiguous path")
            except (OSError, ValueError):
                errors.append(f"hub {hub_label} cannot be checked within scope")
    return result


def records(path: Path) -> list[dict]:
    if any(p.casefold() in PRIVATE_PARTS for p in path.parts):
        raise ValueError("results path is outside the filing scope")
    if any(p.casefold() in PRIVATE_PARTS for p in path.resolve().parts):
        raise ValueError("results path is outside the filing scope")
    text = path.read_text(encoding="utf-8")
    if path.suffix == ".jsonl":
        rows = [json.loads(line) for line in text.splitlines() if line.strip()]
    else:
        rows = json.loads(text)
        rows = rows.get("items", [rows]) if isinstance(rows, dict) else rows
    if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
        raise ValueError("results must contain record objects")
    return rows


def explicit_root(value: str) -> Path:
    if not value.strip():
        raise argparse.ArgumentTypeError("--vault must not be empty or inferred from a default")
    return Path(value)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--vault", type=explicit_root, required=True, help="Explicit knowledge root; never a default/focused vault")
    parser.add_argument("--results", type=Path, help="This batch's JSON or JSONL records with vault_path and optional key")
    parser.add_argument("--note", action="append", default=[], help="One exact vault-relative .md path; repeatable")
    parser.add_argument("--require-reviewed", action="store_true")
    parser.add_argument("--require-hub-links", action="store_true")
    args = parser.parse_args(argv)
    try:
        if yaml is None:
            raise ValueError("Install the offline checker dependency: python -m pip install -r requirements-filing.txt")
        if any(p.casefold() in PRIVATE_PARTS for p in args.vault.parts):
            raise ValueError("private or mirror vault is outside the filing scope")
        root = args.vault.resolve()
        root = scoped(root, str(root))
        if not root.is_dir():
            raise ValueError("the explicit vault root must exist")
        rows = records(args.results) if args.results else []
        rows += [{"vault_path": path} for path in args.note]
        skipped = [r for r in rows if not r.get("vault_path") and r.get("status") in NO_NOTE]
        ready = [r for r in rows if r not in skipped]
        if not ready or any(not r.get("vault_path") for r in ready):
            raise ValueError("name at least one source note; every non-blocked record needs vault_path")
        hub_cache = {}
        results = [check(root, r, reviewed=args.require_reviewed, hubs=args.require_hub_links, hub_cache=hub_cache) for r in ready]
    except (OSError, ValueError) as exc:
        print(f"note-check: {exc}", file=sys.stderr)
        return 2
    report = {"checked": len(results), "skipped_without_note": len(skipped),
              "errors": sum(len(r["errors"]) for r in results),
              "warnings": sum(len(r["warnings"]) for r in results), "notes": results}
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 1 if report["errors"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
