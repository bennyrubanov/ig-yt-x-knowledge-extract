# Obsidian source-note quality gate

Use this when filing a new or changed source note, after the agent's content review
and hub updates, before marking its queue rows `noted`. It applies to the current
batch's knowledge notes, not to a vault migration or a search of unrelated notes.

Benny requested the [kepano/obsidian-skills](https://github.com/kepano/obsidian-skills)
review on 2026-11-10. The reviewed upstream version is
[`3ccff533`](https://github.com/kepano/obsidian-skills/tree/3ccff5338ea700537839b21900aa5358a0402c98).
The useful practices below are incorporated into this provider-neutral flow; no
plugin or provider-specific skill installation is needed.

## Write portable notes

- Keep YAML at the beginning, with unique property names and flat values. Quote
  arbitrary text, URLs, prompts and IDs; JSON-quoted strings are valid YAML. `tags`,
  `topics`, `aliases` and `cssclasses` use lists. Keep `date` and `extracted` as
  `YYYY-MM-DD` and preserve the original saved timestamp in `added_at`. Do not replace
  source/save metadata with a model's guesses. The existing text-or-list
  `notion_page_id` contract stays compatible; no bulk property conversion.
- Tags have no `#` prefix or spaces and cannot consist only of numbers. A digit
  prefix such as `3d-modeling` is valid. Use `tags`, `aliases`, `cssclasses`, not their
  deprecated singular forms.
- Prefer vault-relative internal links such as
  `[[instagram/extractions/Abc-useful-note|Useful note]]`, retaining the source ID in
  the filename. Markdown internal links are also supported; URL-encode spaces.
  Full paths avoid ambiguous short names. In a Markdown table, escape a wikilink's
  alias separator: `[[cooking/sauce-notes\|Sauces]]`.
- Copy useful images/audio into the existing permanent media folders, then embed
  them using `![[...]]` (including the file extension). Never point a retained-media
  link at prunable `downloads/`, a temporary path or the source's expiring CDN URL.
  Preserve identity/provenance and unresolved track names; an embed doesn't confer
  reuse rights. Keep the current music and project-handoff rules.
- Keep the existing title, TL;DR, source discussion, takeaways and related-hub
  structure. The first TL;DR bullet answers the exact saved prompt when present.
  Keep source claims distinct from verified evidence and proposed applications.
  Callouts are optional for a genuinely useful qualification, not a new template
  requirement. Each relevant hub gets a short backlink to the source note.

These rules are checked against official [properties](https://help.obsidian.md/properties),
[tags](https://help.obsidian.md/tags), [internal links](https://help.obsidian.md/links)
and [embeds](https://help.obsidian.md/embeds) documentation. In particular, the
upstream skill's stricter wording about digit prefixes and Markdown links is not
copied into our policy.

## Check once per ready batch

Install the small parser dependency once in the same Python environment as `igx`:

```bash
python -m pip install -r requirements-filing.txt
```

After human/agent review, permanent attachment copies and hub updates:

```bash
python scripts/igx.py note-check \
  --vault "$KNOWLEDGE_ROOT" \
  --results "$RUN_DIR/review.jsonl" \
  --require-reviewed --require-hub-links > "$RUN_DIR/note-check.json"
```

`KNOWLEDGE_ROOT` is the explicitly authorized `Obsidian/` knowledge root on the Mac
or the `cloud-notes` checkout in cloud. `review.jsonl` is **this batch's** list of
records with `vault_path` and, where available, the source `key`. JSON arrays and
`{"items": [...]}` also work. For one changed note, use repeatable
`--note instagram/extractions/Abc-useful-note.md` instead of `--results`.

The checker is read-only and offline. It reads only those notes, their explicitly
linked file targets, and the named hubs; it does not recursively discover files,
open Obsidian, fetch links, change notes or mark Notion. It refuses paths outside
the supplied root, private/mirror paths and symlink escapes. A blocked record
without a note is counted separately; checking zero notes cannot report success.

- Exit **0**: no structural errors. Review unresolved-note/ambiguity warnings with
  the existing targeted lookup rules; use an unambiguous path or record an intentional
  future-note reference. Obsidian permits unresolved links. Short names may also
  resolve elsewhere in the vault, which this batch-only check doesn't scan.
- Exit **1**: fix the listed properties, missing files, source-ID mismatch, prompt
  structure or required hub/permanent-media target before filing. Ordinary unresolved
  note references are warnings rather than a ban on Obsidian's linking workflow.
  Rerun after changes.
- Exit **2**: fix input, explicit scope or the parser installation first.

Save the small JSON report with the batch's run evidence. Do not repeat the check
without a change/failure to investigate. It does not verify factual accuracy,
source completeness, audio decoding, privacy terms, anchor/block targets or the
appearance in Obsidian. Those content/privacy/media checks still belong to the
reviewer. A passing report alone never makes a source `noted`.

## Keep optional features optional

No new Bases, Canvas, vault folders, property migration, settings or plugins are
introduced just to file saved sources. Existing indexes remain the navigation.
The current renderer already creates notes deterministically, so a second Knap
templating runtime is unnecessary. Defuddle is not an automatic capture fallback;
the existing source-access and metering rules still apply.

The Obsidian CLI requires a running local app and defaults to its focused vault.
It is not part of cloud filing. If separately authorized for a Mac render check,
target the exact public knowledge vault and exact note path; never use its default
vault, whole-vault searches or developer/eval commands. At the normal authorized
Mac import, check representative notes in reading view when a new formatting
construct is introduced. Report that separately from the offline file check.
