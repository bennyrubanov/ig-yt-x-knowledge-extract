"""Offline positive/negative controls for the batch-only filing gate."""
import contextlib
import io
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import note_check as nc


class NoteCheckTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = (Path(self.tmp.name) / "knowledge").resolve()
        self.root.mkdir()
        self.path = "instagram/extractions/Abc-test.md"
        self.note = self.root / self.path
        self.note.parent.mkdir(parents=True)
        self.hub = self.root / "cooking/_index.md"
        self.hub.parent.mkdir()
        self.hub.write_text(f"# Cooking\n- [[{self.path[:-3]}|Useful recipe]]\n")
        self.props = {"type": "reel", "source": "https://example.com/post/Abc", "saved_url": "https://example.com/post/Abc?x=y",
                      "media_id": "Abc", "notion_page_id": ["page-one", "page-two"], "added_at": "2026-11-09T13:02:01Z",
                      "date": "2026-11-09", "extracted": "2026-11-10", "capture_context": "Notion",
                      "user_question": "Keep this: bowls & sauces?", "status": "reviewed", "topics": ["cooking"], "tags": ["cooking", "3d-food", "食べ物"]}
        self.body = "# Useful recipe\n\n## TL;DR\n- **Your prompt:** Keep this: bowls & sauces? **Kept the supplied details.**\n\n## Related\n- [[cooking/_index]]\n"
        self.write()

    def write(self, props=None, body=None):
        front = "\n".join(f"{key}: {json.dumps(value, ensure_ascii=False)}" for key, value in (props or self.props).items())
        self.note.write_text(f"---\n{front}\n---\n\n{self.body if body is None else body}")

    def check(self, **kwargs):
        return nc.check(self.root, {"vault_path": self.path, "key": "Abc"}, reviewed=True, hubs=True, **kwargs)

    def test_valid_note_is_read_only_and_checks_retained_audio(self):
        audio = self.root / "music-producing/_audio/Abc.m4a"
        audio.parent.mkdir(parents=True)
        audio.write_bytes(b"synthetic audio fixture")
        self.write(body=self.body + "\n![[music-producing/_audio/Abc.m4a]]\n")
        before = self.note.read_bytes(), self.hub.read_bytes(), audio.read_bytes()
        self.assertEqual(self.check()["errors"], [])
        self.assertEqual(before, (self.note.read_bytes(), self.hub.read_bytes(), audio.read_bytes()))

    def test_duplicate_keys_and_invalid_yaml_fail(self):
        self.note.write_text(self.note.read_text().replace("---\n\n#", 'tags: ["duplicate"]\n---\n\n#'))
        self.assertIn("unique", " ".join(self.check()["errors"]))
        self.note.write_text("---\ntitle: a: bad scalar\n---\n# Note\n")
        self.assertIn("invalid YAML", " ".join(self.check()["errors"]))

    def test_yaml_object_tags_cannot_execute_code(self):
        self.note.write_text('---\nx: !!python/object/apply:os.system ["echo unsafe"]\n---\n# Note\n')
        self.assertIn("invalid YAML", " ".join(self.check()["errors"]))

    def test_flat_typed_properties_and_tags(self):
        self.write({**self.props, "tags": ["1984", "bad tag"], "aliases": "wrong type", "extra": {"nested": True}})
        errors = " ".join(self.check()["errors"])
        self.assertIn("invalid tag", errors)
        self.assertIn("aliases must be a list", errors)
        self.assertIn("keep Obsidian properties flat", errors)
        self.assertTrue(nc.valid_tag("3d-modeling"))
        self.assertTrue(nc.valid_tag("📚/reading"))
        self.assertFalse(nc.valid_tag("1984"))

    def test_missing_retained_embed_is_an_error(self):
        self.write(body=self.body + "\n![[instagram/extractions/_media/Abc/card.jpg|600]]\n")
        self.assertIn("unresolved wiki link", " ".join(self.check()["errors"]))

    def test_markdown_internal_links_and_escaped_table_alias_work(self):
        target = self.root / "cooking/Sauce notes.md"
        target.write_text("# Sauce notes\n")
        self.write(body=self.body + "\n[Details](cooking/Sauce%20notes.md#Details)\n| Ref |\n| [[cooking/Sauce notes\\|Sauces]] |\n")
        self.assertEqual(self.check()["errors"], [])
        self.hub.write_text(f"# Cooking\n- [Recipe]({self.path})\n")
        self.assertEqual(self.check()["errors"], [])

    def test_markdown_folder_relative_image_is_supported(self):
        self.path = "cooking/Abc-test.md"
        self.note = self.root / self.path
        image = self.root / "cooking/assets/photo.png"
        image.parent.mkdir()
        image.write_bytes(b"fixture")
        self.hub.write_text(f"# Cooking\n- [[{self.path[:-3]}]]\n")
        self.write(body=self.body + "\n![](assets/photo.png)\n")
        self.assertEqual(self.check()["errors"], [])

    def test_markdown_folder_relative_hub_backlink_is_supported(self):
        self.path = "cooking/notes/Abc-test.md"
        self.note = self.root / self.path
        self.note.parent.mkdir()
        self.hub.write_text("# Cooking\n- [Recipe](notes/Abc-test.md)\n")
        self.write()
        self.assertEqual(self.check()["errors"], [])
        self.assertEqual(self.check()["warnings"], [])
        other = self.root / "notes/Abc-test.md"
        other.parent.mkdir()
        other.write_text("# Other same-named note\n")
        self.assertEqual(len(self.check()["warnings"]), 1)

    def test_private_and_outside_links_are_rejected(self):
        self.write(body=self.body + "\n[[Private Notes/never-read]]\n[Outside](../../../outside.md)\n")
        errors = " ".join(self.check()["errors"])
        self.assertIn("private or mirror path", errors)
        self.assertIn("leaves the explicitly supplied vault", errors)

    def test_extension_probe_never_inspects_a_sibling_outside_root(self):
        outside = self.root.with_name(self.root.name + ".md")
        outside.write_text("synthetic outside file")
        original = Path.is_file
        probes = []
        def guarded(path):
            self.assertTrue(path.is_relative_to(self.root), str(path))
            probes.append(path)
            return original(path)
        with patch.object(Path, "is_file", guarded):
            nc.targets(self.root, self.note, ".")
            nc.targets(self.root, self.note, str(self.root))
        self.assertNotIn(outside, probes)

    def test_symlink_escape_is_rejected_without_reading_target(self):
        outside = Path(self.tmp.name) / "outside.md"
        outside.write_text("synthetic outside fixture")
        (self.root / "escape.md").symlink_to(outside)
        self.write(body=self.body + "\n![[escape.md]]\n")
        self.assertIn("leaves the explicitly supplied vault", " ".join(self.check()["errors"]))

    def test_quoted_property_links_are_checked(self):
        self.write({**self.props, "related": "[[Private Notes/never-read]]"})
        self.assertIn("private or mirror path", " ".join(self.check()["errors"]))

    def test_examples_comments_and_external_urls_are_not_followed(self):
        self.write(body=self.body + '\n```md\n![[missing.jpg]]\n```\n`[[missing.md]]`\n<!-- [[missing.md]] -->\n%% [[missing.md]] %%\n[Source](https://example.com/elsewhere)\n')
        self.assertEqual(self.check()["errors"], [])

    def test_fence_info_text_does_not_close_an_example(self):
        self.write(body=self.body + '\n```text\n```python\n![[missing.jpg]]\n```\n``literal [[missing.jpg]] and `code` ``\n')
        self.assertEqual(self.check()["errors"], [])

    def test_review_and_saved_prompt_gate(self):
        self.write({**self.props, "status": "extracted"}, body="# Note\n## TL;DR\n- Generic answer\n")
        errors = " ".join(self.check()["errors"])
        self.assertIn("status must be reviewed", errors)
        self.assertIn("Your prompt", errors)

    def test_prompt_prefix_accepts_plain_or_bold_styling(self):
        self.write(body=self.body.replace("**Your prompt:**", "Your prompt:"))
        self.assertEqual(self.check()["errors"], [])
        self.write(body=self.body.replace("**Your prompt:**", "**Your prompt**:"))
        self.assertEqual(self.check()["errors"], [])

    def test_wrong_source_identity_and_missing_provenance(self):
        self.write({**self.props, "media_id": "wrong", "notion_page_id": 123, "added_at": "not-a-date"})
        errors = " ".join(self.check()["errors"])
        self.assertIn("differs from the batch record", errors)
        self.assertIn("list of text IDs", errors)
        self.assertIn("ISO date/time", errors)

    def test_hub_requires_real_backlink_but_does_not_follow_unrelated_links(self):
        self.hub.write_text("# Cooking\n- [[Private Notes/never-read]]\n")
        self.assertIn("has no backlink", " ".join(self.check()["errors"]))
        self.hub.write_text(f"# Cooking\n- [[Private Notes/never-read]]\n- [[{self.path[:-3]}]]\n")
        self.assertEqual(self.check()["errors"], [])

    def test_hub_is_read_once_per_batch(self):
        cache = {}
        with patch.object(nc, "read_text", wraps=nc.read_text) as read:
            self.check(hub_cache=cache)
            self.check(hub_cache=cache)
        self.assertEqual([call.args[0] for call in read.call_args_list].count(self.hub), 1)

    def test_missing_short_wikilink_requires_manual_resolution(self):
        self.write(body=self.body + "\n[[Unresolved short name]]\n")
        self.assertEqual(len(self.check()["warnings"]), 1)
        self.assertEqual(self.check()["errors"], [])

    def test_intentional_future_note_links_warn_instead_of_blocking(self):
        self.write(body=self.body + "\n[[cooking/Future note.md#Details]]\n[Future](cooking/Another%20note.md)\n")
        self.assertEqual(len(self.check()["warnings"]), 2)
        self.assertEqual(self.check()["errors"], [])

    def test_heading_and_block_links_keep_their_file_target(self):
        self.write(body=self.body + "\n[[#TL;DR]]\n[[cooking/_index#^recipe-list|Recipes]]\n")
        self.assertEqual(self.check()["errors"], [])

    def test_valid_short_hub_backlink_warns_instead_of_failing(self):
        self.hub.write_text("# Cooking\n- [[Abc-test|Recipe]]\n")
        result = self.check()
        self.assertEqual(result["errors"], [])
        self.assertEqual(len(result["warnings"]), 1)

    def test_existing_prunable_media_is_not_a_permanent_deliverable(self):
        media = self.root / "downloads/clip.m4a"
        media.parent.mkdir()
        media.write_bytes(b"fixture")
        self.write(body=self.body + "\n[Audio](downloads/clip.m4a)\n")
        self.assertIn("prunable downloads", " ".join(self.check()["errors"]))

    def test_malformed_url_does_not_crash_checker(self):
        self.write({**self.props, "source": "https://["})
        self.assertIn("source must", " ".join(self.check()["errors"]))

    def test_yaml_native_dates_and_datetimes_are_supported(self):
        self.note.write_text(self.note.read_text().replace('date: "2026-11-09"', 'date: 2026-11-09')
                             .replace('added_at: "2026-11-09T13:02:01Z"', 'added_at: 2026-11-09T13:02:01Z'))
        self.assertEqual(self.check()["errors"], [])

    def test_rejected_private_note_name_is_not_copied_into_report(self):
        with patch.object(Path, "read_text", side_effect=AssertionError("must not read")):
            result = nc.check(self.root, {"vault_path": "Private Notes/Sensitive-title.md"})
        self.assertNotIn("Sensitive-title", json.dumps(result))
        self.assertTrue(result["errors"])

    def test_rejected_topic_names_are_not_copied_into_report(self):
        self.write({**self.props, "topics": ["Private Notes/Sensitive-topic", "../../Outside-topic"]})
        result = self.check()
        report = json.dumps(result)
        self.assertNotIn("Sensitive-topic", report)
        self.assertNotIn("Outside-topic", report)
        self.assertTrue(result["errors"])

    def test_cli_checks_jsonl_and_counts_blocked_rows_separately(self):
        results = Path(self.tmp.name) / "review.jsonl"
        results.write_text(json.dumps({"key": "Abc", "vault_path": self.path}) + '\n{"key":"other","status":"blocked"}\n')
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = nc.main(["--vault", str(self.root), "--results", str(results), "--require-reviewed", "--require-hub-links"])
        report = json.loads(out.getvalue())
        self.assertEqual((code, report["checked"], report["skipped_without_note"]), (0, 1, 1))

    def test_cli_cannot_succeed_by_checking_zero_files(self):
        results = Path(self.tmp.name) / "empty.jsonl"
        results.write_text('{"status":"blocked"}\n')
        with contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(nc.main(["--vault", str(self.root), "--results", str(results)]), 2)

    def test_empty_vault_variable_cannot_fall_back_to_current_directory(self):
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as error:
            nc.main(["--vault", "", "--note", self.path])
        self.assertEqual(error.exception.code, 2)

    def test_real_dispatcher_has_red_and_green_attachment_controls(self):
        script = Path(__file__).resolve().parents[1] / "scripts/igx.py"
        cmd = [sys.executable, str(script), "note-check", "--vault", str(self.root), "--note", self.path,
               "--require-reviewed", "--require-hub-links"]
        self.write(body=self.body + "\n![[cooking/card.png]]\n")
        bad = subprocess.run(cmd, capture_output=True, text=True)
        self.assertEqual(bad.returncode, 1)
        self.assertGreater(json.loads(bad.stdout)["errors"], 0)
        (self.root / "cooking/card.png").write_bytes(b"synthetic fixture")
        good = subprocess.run(cmd, capture_output=True, text=True)
        self.assertEqual(good.returncode, 0, good.stdout + good.stderr)
        self.assertEqual(json.loads(good.stdout)["checked"], 1)

    def test_private_vault_and_private_results_rejected_before_content_read(self):
        private = Path(self.tmp.name) / "Private Notes"
        with patch.object(Path, "read_text", side_effect=AssertionError("must not read")), contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(nc.main(["--vault", str(private), "--note", "Journal.md"]), 2)
            self.assertEqual(nc.main(["--vault", str(self.root), "--results", str(private / "results.jsonl")]), 2)


if __name__ == "__main__":
    unittest.main()
