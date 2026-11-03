"""Offline checks for Gemini note drafting: the queue owns the frontmatter, nothing leaks."""
import io
import json
import tempfile
import unittest
from pathlib import Path
from urllib.error import HTTPError

import gemini_api
import note_gemini as ng


class Stream(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def reply(payload, finish="STOP"):
    text = payload if isinstance(payload, str) else json.dumps(payload)
    event = {"candidates": [{"content": {"parts": [{"text": text}]}, "finishReason": finish}],
             "usageMetadata": {"promptTokenCount": 900, "candidatesTokenCount": 300}}
    return Stream(b"data: " + json.dumps(event).encode() + b"\n\n")


def per_day_429():
    body = {"error": {"code": 429, "status": "RESOURCE_EXHAUSTED", "message": "quota", "details": [
        {"@type": "type.googleapis.com/google.rpc.QuotaFailure", "violations": [
            {"quotaId": "GenerateRequestsPerDayPerProjectPerModel-FreeTier", "quotaValue": "20"}]},
        {"@type": "type.googleapis.com/google.rpc.RetryInfo", "retryDelay": "33s"}]}}
    return HTTPError("https://example.invalid", 429, "quota", {}, io.BytesIO(json.dumps(body).encode()))


def paid_429():
    body = {"error": {"code": 429, "status": "RESOURCE_EXHAUSTED", "message": "quota", "details": [
        {"@type": "type.googleapis.com/google.rpc.QuotaFailure", "violations": [
            {"quotaId": "GenerateRequestsPerMinutePerProjectPerModel", "quotaValue": "1000"}]}]}}
    return HTTPError("https://example.invalid", 429, "quota", {}, io.BytesIO(json.dumps(body).encode()))


def http_error(code):
    return HTTPError("https://example.invalid", code, "x", {}, io.BytesIO(json.dumps({"error": {"code": code}}).encode()))


def sequence(replies):
    def opener(req, timeout):
        nxt = replies.pop(0)
        if isinstance(nxt, Exception):
            raise nxt
        return nxt
    return opener


def draft_answer(**overrides):
    answer = {"status": "written", "prompt": "", "title": "Where you study matters most for some majors",
              "slug": "Abc123def45 College Choice Earnings by Major", "author": "Somebody Else",
              "topics": ["good-info", "not-a-hub", "wealth"], "tags": ["Education Data", "earnings"],
              "prompt_answer": "Summarized the **chart** and its limits.",
              "tldr": ["**Your prompt:** Is this true? **Yes.**", "- Computer science pays $115,000 to $270,000 by school.",
                       "Theater barely varies."],
              "what_they_said": "## Heading\nThe video walks through a chart.",
              "key_takeaways": ["Pick the school by major.", "About 98% of programs pass.", "Year 1999 matters."],
              "dissection": "", "key_stills": [{"file": "slide_01.jpg", "name": "Earnings Chart", "shows": "chart"}],
              "hub_line": "College earnings vary by major.", "projects": [], "coverage": "full", "flags": []}
    answer.update(overrides)
    return answer


class NoteGeminiTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.out = self.root / "vault"
        self.out.mkdir()
        cap = self.root / "downloads"
        (cap / "Abc123def45" / "slides").mkdir(parents=True)
        (cap / "Abc123def45.txt").write_text("Computer science earns $115,000 at one school and $270,000 at "
                                             "another; 98% of programs pass.", encoding="utf-8")
        (cap / "Abc123def45" / "slides" / "slide_01.jpg").write_bytes(b"\xff\xd8fakejpeg")
        self.downloads = cap
        self.item = {"key": "Abc123def45", "type": "youtube",
                     "source": "https://www.youtube.com/watch?v=Abc123def45",
                     "saved_url": "https://youtube.com/shorts/Abc123def45?si=x",
                     "notion_page_ids": ["page-id-1"], "added_at": "2026-10-30T02:50:09Z",
                     "prompts": ["Is this true?"], "author": "The Paper",
                     "capture_method": "Gemini YouTube URL input"}
        self.requests = []

    def tearDown(self):
        self.tmp.cleanup()

    def drafter(self, replies, **kwargs):
        def opener(req, timeout):
            self.requests.append(req)
            nxt = replies.pop(0)
            if isinstance(nxt, Exception):
                raise nxt
            return nxt
        return ng.Drafter(key="secret-key", models=("lite", "flash"), out=self.out, vault=self.out,
                          downloads=self.downloads, run_date="2026-11-03", opener=opener,
                          sleep=lambda s: None, **kwargs)

    def test_brief_sections_and_hubs_are_found(self):
        brief, filing = ng.BRIEF.read_text(), ng.FILING.read_text()
        system = ng.system_text(brief, filing)
        for heading in ng.BRIEF_SECTIONS:
            self.assertIn(heading, system)
        self.assertIn("## Key takeaways", system)  # the fenced template stays whole
        self.assertIn("**Body structure:**", system)
        hubs = ng.hubs_from_brief(brief)
        self.assertIn("health", hubs)
        self.assertIn("good-info", hubs)

    def test_frontmatter_comes_from_the_item_not_the_model(self):
        row = self.drafter([reply(draft_answer(prompt="Something invented"))]).draft(self.item)
        self.assertEqual(row["status"], "written")
        self.assertEqual(row["vault_path"], "youtube/Abc123def45-college-choice-earnings-by-major.md")
        note = (self.out / row["vault_path"]).read_text()
        for line in ('source: "https://www.youtube.com/watch?v=Abc123def45"',
                     'saved_url: "https://youtube.com/shorts/Abc123def45?si=x"',
                     'notion_page_id: "page-id-1"', 'added_at: "2026-10-30T02:50:09Z"', 'date: "2026-10-30"',
                     'extracted: "2026-11-03"', 'author: "The Paper"', 'user_question: "Is this true?"',
                     'topics: ["good-info", "wealth"]', 'tags: ["youtube", "good-info", "education-data", "earnings"]',
                     'drafted_by: "lite (Gemini API)"', "status: extracted"):
            self.assertIn(line, note)
        self.assertIn("- **Your prompt:** Is this true? **Summarized the chart and its limits.**", note)
        self.assertIn("- Computer science pays $115,000 to $270,000 by school.", note)
        self.assertEqual(note.count("Your prompt"), 1)
        self.assertNotIn("## Heading", note)
        self.assertIn("![[youtube/_media/Abc123def45/earnings-chart.jpg]]", note)
        self.assertTrue((self.out / "youtube/_media/Abc123def45/earnings-chart.jpg").is_file())
        self.assertIn("- [[good-info/_index]] · [[wealth/_index]]", note)
        self.assertEqual(row["unsourced_numbers"], ["1999"])
        self.assertIn("check-numbers", row["flags"])

    def test_request_carries_material_but_no_ids_dates_paths_or_key(self):
        self.drafter([reply(draft_answer())]).draft(self.item)
        body = self.requests[0].data.decode()
        self.assertIn("$270,000", body)
        self.assertIn("Is this true?", body)
        self.assertIn("slide_01.jpg", body)
        for private in ("page-id-1", "2026-10-30", str(self.root), "secret-key"):
            self.assertNotIn(private, body)
        self.assertEqual(self.requests[0].headers.get("X-goog-api-key"), "secret-key")

    def test_a_prompt_from_a_title_must_be_the_typed_words(self):
        item = {**self.item, "title": "Is this true? The Paper on YouTube"}
        del item["prompts"]
        row = self.drafter([reply(draft_answer(prompt="Is this true?"))]).draft(item)
        self.assertEqual(row["prompt"], "Is this true?")
        (self.out / row["vault_path"]).unlink()
        row = self.drafter([reply(draft_answer(prompt="Should I invest?"))]).draft(item)
        self.assertEqual(row["prompt"], ng.NO_PROMPT)

    def test_an_empty_prompts_list_beside_a_title_reads_the_title(self):
        item = {**self.item, "prompts": [], "title": "Is this true? The Paper on YouTube"}
        row = self.drafter([reply(draft_answer(prompt="Is this true?"))]).draft(item)
        self.assertEqual(row["prompt"], "Is this true?")
        self.assertIn("Is this true? The Paper on YouTube", self.requests[0].data.decode())

    def test_an_empty_prompts_list_without_a_title_is_a_bare_link(self):
        item = {**self.item, "prompts": []}
        item.pop("title", None)
        row = self.drafter([reply(draft_answer(prompt="Is this true?"))]).draft(item)
        self.assertEqual(row["prompt"], ng.NO_PROMPT)

    def test_bad_answer_is_retried_then_the_next_model_drafts(self):
        replies = [reply("not json"), reply(draft_answer(topics=["nope"])), reply(draft_answer())]
        row = self.drafter(replies).draft(self.item)
        self.assertEqual(row["status"], "written")
        self.assertEqual(row["model"], "flash")
        self.assertEqual([t["error"] for t in row["tries"]][:2], ["answer was not JSON", "no known hub in topics"])

    def test_cut_off_answer_is_not_accepted(self):
        row = self.drafter([reply(draft_answer(), finish="MAX_TOKENS"), reply(draft_answer())]).draft(self.item)
        self.assertEqual(row["status"], "written")
        self.assertEqual(row["tries"][0]["finish"], "MAX_TOKENS")

    def test_blocked_draft_writes_nothing(self):
        answer = {"status": "blocked", "blocked_reason": "the transcript is a different video"}
        row = self.drafter([reply(answer)]).draft(self.item)
        self.assertEqual(row["status"], "blocked")
        self.assertEqual(list(self.out.rglob("*.md")), [])

    def test_filed_key_is_skipped_unless_redo_and_never_overwritten(self):
        filed = self.out / "good-info" / "Abc123def45-earlier-note.md"
        filed.parent.mkdir()
        filed.write_text("---\nauthor: The Paper\n---\n")
        row = self.drafter([]).draft(self.item)
        self.assertEqual((row["status"], row["vault_path"]), ("exists", "good-info/Abc123def45-earlier-note.md"))
        row = self.drafter([reply(draft_answer())], redo=True).draft(self.item)
        self.assertEqual(row["status"], "written")
        self.assertEqual(filed.read_text(), "---\nauthor: The Paper\n---\n")
        again = self.drafter([reply(draft_answer())], redo=True).draft(self.item)
        self.assertEqual(again["status"], "failed")

    def test_same_author_notes_are_linked(self):
        other = self.out / "youtube" / "Zzz999yyy88-another.md"
        other.parent.mkdir()
        other.write_text("---\nauthor: \"The Paper (YouTube Short)\"\n---\n")
        row = self.drafter([reply(draft_answer())]).draft(self.item)
        self.assertIn("- [[youtube/Zzz999yyy88-another]]", (self.out / row["vault_path"]).read_text())

    def test_a_used_up_daily_allowance_skips_that_model_for_the_rest_of_the_run(self):
        drafter = self.drafter([per_day_429(), reply(draft_answer()), reply(draft_answer(slug="second"))], redo=True)
        first = drafter.draft(self.item)
        self.assertEqual(first["model"], "flash")
        self.assertIn("GenerateRequestsPerDayPerProjectPerModel-FreeTier limit 20", first["tries"][0]["error"])
        second = drafter.draft(self.item)
        self.assertEqual([t["model"] for t in second["tries"]], ["flash"])
        self.assertEqual(drafter.skip, {"lite"})

    def test_a_model_silent_on_two_items_is_skipped_for_the_rest_of_the_run(self):
        def silent():
            return OSError("timed out")
        replies = [silent(), silent(), reply(draft_answer(slug="one")),
                   silent(), silent(), reply(draft_answer(slug="two")),
                   reply(draft_answer(slug="three"))]
        drafter = self.drafter(replies, redo=True)
        for _ in range(3):
            row = drafter.draft(self.item)
            self.assertEqual(row["model"], "flash")
        self.assertEqual([t["model"] for t in row["tries"]], ["flash"])
        self.assertEqual(drafter.skip, {"lite"})

    def test_missing_material_and_failed_captures_are_not_sent(self):
        empty = {**self.item, "key": "Nothing0000", "files": []}
        self.assertEqual(self.drafter([]).draft(empty)["status"], "blocked")
        self.assertEqual(self.drafter([]).draft({**self.item, "status": "fail"})["status"], "passed-over")
        self.assertEqual(self.requests, [])

    def test_error_summary_keeps_the_quota_and_wait(self):
        text, wait = gemini_api.error_summary(per_day_429().read().decode())
        self.assertIn("limit 20", text)
        self.assertEqual(wait, 33.0)

    def test_command_writes_results_and_needs_the_key(self):
        items = self.root / "batch.json"
        items.write_text(json.dumps({"items": [self.item]}))
        results = self.root / "results.jsonl"
        out = io.StringIO()
        from contextlib import redirect_stdout
        from unittest.mock import patch
        with patch.dict("os.environ", {}, clear=True), redirect_stdout(out):
            self.assertEqual(ng.main([str(items), "--out", str(self.out), "--results", str(results)]), 2)
        with patch.dict("os.environ", {"GEMINI_PERSONAL_API_KEY": "secret-key"}), \
                patch("note_gemini.tier_check", return_value={"tier": "free"}), \
                patch("note_gemini.Drafter.draft", return_value={"key": "Abc123def45", "status": "written",
                                                                 "vault_path": "youtube/x.md"}), \
                redirect_stdout(out):
            self.assertEqual(ng.main([str(items), "--out", str(self.out), "--results", str(results),
                                      "--downloads", str(self.downloads)]), 0)
        self.assertEqual(json.loads(results.read_text())["status"], "written")
        self.assertNotIn("secret-key", out.getvalue())

    def test_tier_check_tells_free_from_billed_and_unknown(self):
        def check(*replies):
            return gemini_api.tier_check("k", models=("probe-a", "probe-b"), opener=sequence(list(replies)))
        self.assertEqual(check(per_day_429())["tier"], "free")
        self.assertEqual(check(reply("OK"))["tier"], "billed")
        self.assertEqual(check(http_error(402))["tier"], "billed")
        self.assertEqual(check(paid_429())["tier"], "billed")
        free = check(http_error(404), per_day_429())
        self.assertEqual((free["tier"], free["model"]), ("free", "probe-b"))
        unknown = check(http_error(404), http_error(503))
        self.assertEqual(unknown["tier"], "unknown")
        self.assertIn("probe-a: 404", unknown["detail"])

    def test_a_billing_sign_stops_drafting_and_writes_nothing(self):
        for refusal in (paid_429(), http_error(402)):
            with self.assertRaises(gemini_api.BillingLinked):
                self.drafter([refusal, reply(draft_answer())]).draft(self.item)
        self.assertEqual(list(self.out.rglob("*.md")), [])

    def test_command_stops_when_the_key_shows_billing(self):
        from contextlib import redirect_stdout
        from unittest.mock import patch
        items = self.root / "batch.json"
        items.write_text(json.dumps({"items": [self.item, {**self.item, "key": "Other000000"}]}))
        results = self.root / "results.jsonl"
        args = [str(items), "--out", str(self.out), "--results", str(results), "--gap", "0"]
        out = io.StringIO()
        with patch.dict("os.environ", {"GEMINI_PERSONAL_API_KEY": "secret-key"}), redirect_stdout(out):
            with patch("note_gemini.tier_check", return_value={"tier": "billed", "detail": "402"}):
                self.assertEqual(ng.main(args), 4)
            self.assertFalse(results.exists())
            with patch("note_gemini.tier_check", return_value={"tier": "free"}), \
                    patch("note_gemini.Drafter.draft", side_effect=gemini_api.BillingLinked("lite: 402")) as draft:
                self.assertEqual(ng.main(args), 4)
        self.assertEqual(draft.call_count, 1)
        rows = [json.loads(line) for line in results.read_text().splitlines()]
        self.assertEqual([r["status"] for r in rows], ["not-sent", "not-sent"])
        self.assertIn("billing", rows[0]["reason"])
        summary = json.loads(out.getvalue().strip().splitlines()[-1])
        self.assertEqual(summary["billing_stop"], "lite: 402")
        self.assertNotIn("secret-key", out.getvalue())

    def test_transcripts_stop_on_a_billing_sign(self):
        import youtube_gemini
        from contextlib import redirect_stdout
        from unittest.mock import patch
        with patch.dict("os.environ", {"GEMINI_PERSONAL_API_KEY": "secret-key"}), \
                patch("youtube_gemini.transcribe", side_effect=gemini_api.BillingLinked("lite: 402")), \
                redirect_stdout(io.StringIO()):
            self.assertEqual(youtube_gemini.main(["https://youtu.be/Abc123def45"]), 4)


if __name__ == "__main__":
    unittest.main()
