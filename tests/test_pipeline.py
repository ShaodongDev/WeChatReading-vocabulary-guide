"""Offline checks using invented highlights, sentences, and pronunciation data."""
import contextlib
import copy
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import types
import unittest
from unittest import mock
import urllib.error
import zipfile
from xml.etree import ElementTree as ET

SCRIPTS = Path(__file__).resolve().parents[1] / "skills/weread-vocab/scripts"
sys.path.insert(0, str(SCRIPTS))
import common
import fetch
import ipa
import epub_context
import merge_batches
import build as epub_build


def run_main(module, *args):
    output = io.StringIO()
    with mock.patch.object(sys, "argv", [module.__file__, *map(str, args)]), contextlib.redirect_stdout(output):
        module.main()
    return json.loads(output.getvalue())


class PipelineTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="weread-offline-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.cache = self.root / "cache"
        for patcher in (mock.patch.object(common, "CACHE", str(self.cache)),
                        mock.patch.object(ipa, "CACHE", str(self.cache)),
                        mock.patch.object(ipa, "INDEX", str(self.cache / "oald9_index.json")),
                        mock.patch.dict(os.environ, {"WEREAD_VOCAB_CHROME": "none"})):
            patcher.start()
            self.addCleanup(patcher.stop)
        self.wd = Path(common.workdir("fixture"))
        self.quotation = "The resilient team learns from setbacks and shares new lessons each day."
        self.bookmarks = {
            "book": {"title": "Synthetic Learning Book", "author": "Example Author"},
            "chapters": [{"chapterUid": 1, "chapterIdx": 1, "title": "Working Together"},
                         {"chapterUid": 2, "chapterIdx": 2, "title": "Changing Plans"}],
            "updated": [
                {"bookmarkId": "q0", "chapterUid": 1, "range": "0-74", "markText": self.quotation},
                {"bookmarkId": "h1", "chapterUid": 1, "range": "4-13", "markText": "resilient"},
                {"bookmarkId": "h2", "chapterUid": 2, "range": "40-45", "markText": "adapt"},
            ],
        }
        self.reviews = {"hasMore": 0, "reviews": [{"review": {
            "reviewId": "n1", "chapterUid": 1, "range": "4-13",
            "content": "A note with <tags> & symbols.", "abstract": "resilient"}}]}
        # These entries are authored fixtures, not extracted dictionary content.
        common.save(ipa.INDEX, {"source": "synthetic test data", "links": {}, "prons": {
            "resilient": [["resilient adjective", "rɪˈzɪliənt"]],
            "adapt": [["adapt verb", "əˈdæpt"]],
            "steady": [["steady adjective", "ˈstedi"]],
        }})

    def fetch_book(self, *options):
        def response(name, **params):
            if name == "/book/bookmarklist":
                self.assertEqual(params, {"bookId": "fixture"})
                return copy.deepcopy(self.bookmarks)
            if name == "/review/list/mine":
                self.assertEqual(params["bookid"], "fixture")
                return copy.deepcopy(self.reviews)
            self.fail("Unexpected API operation: " + name)
        with mock.patch.object(fetch, "api", side_effect=response), \
                mock.patch.object(fetch, "epub_candidates", return_value=[]):
            return run_main(fetch, "--book-id", "fixture", *options)

    def write_pending_sentences(self):
        for path in sorted((self.wd / "batches").glob("batch_*.json")):
            batch = common.load(str(path), [])
            sentences = {str(t["id"]): f"The team learned to **{t['term']}** as its plans changed over time."
                         for t in batch}
            common.save(str(path).replace("batch_", "out_"), sentences)
            common.save(str(path).replace("batch_", "unsure_"), [])
        return run_main(merge_batches, self.wd)

    def source_epub(self):
        path = self.root / "source.epub"
        with zipfile.ZipFile(path, "w") as archive:
            archive.writestr("mimetype", "application/epub+zip")
            archive.writestr("META-INF/container.xml", '''<container xmlns="urn:oasis:names:tc:opendocument:xmlns:container"><rootfiles><rootfile full-path="content.opf"/></rootfiles></container>''')
            archive.writestr("content.opf", '''<package xmlns="http://www.idpf.org/2007/opf"><manifest><item id="body" href="body.xhtml" media-type="application/xhtml+xml"/></manifest><spine><itemref idref="body"/></spine></package>''')
            archive.writestr("body.xhtml", f"<html><body><h1>Working Together</h1><p>{self.quotation}</p></body></html>")
        return path

    def test_full_epub_keeps_ipa_notes_chapters_and_sentence_provenance(self):
        if not shutil.which("pandoc"):
            self.skipTest("Pandoc is required for EPUB generation")
        summary = self.fetch_book()
        self.assertEqual((summary["terms"], summary["notes"], summary["highlights"]), (2, 1, 3))
        matched = run_main(epub_context, self.wd, self.source_epub())
        self.assertEqual(matched["from_book"], 1)
        self.assertEqual(matched["terms_needing_sentences"], 1)
        self.assertEqual(self.write_pending_sentences()["still_pending"], 0)
        self.assertFalse(run_main(ipa, self.wd)["unresolved"])
        exported = run_main(epub_build, self.wd, "--out-dir", self.root / "exports", "--md")
        self.assertEqual((exported["cards"], exported["quotes"], exported["notes"]), (2, 1, 1))
        self.assertEqual((exported["sentences_from_book"], exported["sentences_written"]), (1, 1))
        with zipfile.ZipFile(exported["epub"]) as archive:
            self.assertEqual(archive.read("mimetype"), b"application/epub+zip")
            pages = "\n".join(archive.read(name).decode() for name in archive.namelist() if name.endswith(".xhtml"))
            for text in ["Working Together", "Changing Plans", "rɪˈzɪliənt", "əˈdæpt", "from the book", "not quotes", "My note:"]:
                self.assertIn(text, pages)
            self.assertIn("&lt;tags&gt;", pages)
            self.assertNotIn("<tags>", pages)
            self.assertNotIn("Chinese translation", pages)
            self.assertIn('class="ipa"', pages)
            opf = ET.fromstring(archive.read("EPUB/content.opf"))
            title = opf.find(".//{http://purl.org/dc/elements/1.1/}title")
            self.assertIn("Vocabulary in Context", title.text)
        self.assertTrue(Path(exported["md"]).is_file())

    def test_incremental_fetch_only_batches_new_terms_and_keeps_existing_sentences(self):
        self.fetch_book()
        self.write_pending_sentences()
        run_main(ipa, self.wd)
        previous = common.load(str(self.wd / "sentences.json"))
        same = self.fetch_book()
        self.assertEqual((same["new_terms"], same["new_highlights"], same["terms_needing_sentences"]), (0, 0, 0))
        self.bookmarks["updated"].append({"bookmarkId": "h3", "chapterUid": 2, "range": "60-66", "markText": "steady"})
        updated = self.fetch_book()
        self.assertEqual((updated["new_terms"], updated["terms_needing_sentences"]), (1, 1))
        batch = common.load(updated["batches"][0])
        self.assertEqual([t["bookmarkId"] for t in batch], ["h3"])
        self.assertEqual(previous, common.load(str(self.wd / "sentences.json")))

    def test_without_notes_does_not_request_or_preserve_personal_thoughts(self):
        result = self.fetch_book("--without-notes")
        self.assertEqual(result["notes"], 0)
        self.assertFalse(common.load(str(self.wd / "book.json"))["notes"])

    def test_batches_are_bounded_even_with_many_words_in_one_chapter(self):
        common.save(str(self.wd / "terms.json"), [
            {"bookmarkId": f"synthetic-{i}", "chapter": "One Chapter", "term": "sample"} for i in range(241)])
        pending, paths = common.write_batches(str(self.wd))
        self.assertEqual(len(pending), 241)
        self.assertEqual([len(common.load(path)) for path in paths], [120, 120, 1])

    def test_pagination_stops_when_cursors_repeat(self):
        with mock.patch.object(fetch, "api", return_value={"hasMore": 1, "books": [{"sort": 12}]}):
            with self.assertRaisesRegex(SystemExit, "pagination did not advance"):
                fetch.all_notebooks()
        with mock.patch.object(fetch, "api", return_value={"hasMore": 1, "reviews": [], "synckey": 0}):
            with self.assertRaisesRegex(SystemExit, "pagination did not advance"):
                fetch.all_my_reviews("fixture")

    def test_heteronym_choice_is_required_before_build(self):
        self.fetch_book()
        self.write_pending_sentences()
        index = common.load(ipa.INDEX)
        index["prons"]["resilient"] = [["resilient noun", "rɪˈzɪliənt"], ["resilient verb", "ˈtest"]]
        common.save(ipa.INDEX, index)
        self.assertTrue(run_main(ipa, self.wd)["unresolved"])
        with self.assertRaisesRegex(SystemExit, "Pronunciation choices"):
            run_main(epub_build, self.wd, "--out-dir", self.root / "exports")
        common.save(str(self.wd / "ipa_overrides.json"), {"terms": {"h1": "/rɪˈzɪliənt/"}})
        self.assertFalse(run_main(ipa, self.wd)["unresolved"])

    def test_personal_global_fixes_stay_outside_installed_package(self):
        common.save(str(self.cache / "ipa_global.json"), {"adapt": "ˈloʊkl"})
        self.assertEqual(ipa.Resolver({}).resolve("adapt")[0], "ˈloʊkl")
        self.assertEqual(ipa.Resolver({"adapt": "əˈdæpt"}).resolve("adapt")[0], "əˈdæpt")

    def test_incompatible_dictionary_does_not_replace_an_existing_index(self):
        previous = Path(ipa.INDEX).read_bytes()
        reader = types.SimpleNamespace(MDX=lambda path: types.SimpleNamespace(items=lambda: []))
        with mock.patch.object(ipa, "find_oald", return_value="synthetic-dictionary.mdx"), \
                mock.patch.object(ipa, "load_readmdict", return_value=reader):
            with self.assertRaisesRegex(SystemExit, "No OALD9 American"):
                ipa.build_index()
        self.assertEqual(previous, Path(ipa.INDEX).read_bytes())

    def test_book_id_cannot_escape_cache(self):
        with self.assertRaisesRegex(SystemExit, "Invalid book ID"):
            common.workdir("../outside")

    def test_setup_reuses_existing_index_without_creating_dependency_environment(self):
        setup_env = os.environ.copy()
        setup_env["WEREAD_VOCAB_HOME"] = str(self.cache)
        setup_env.pop("WEREAD_VOCAB_OALD", None)
        setup_env.pop("WEREAD_API_KEY", None)
        result = subprocess.run(["bash", str(SCRIPTS / "setup_env.sh")], env=setup_env,
                                capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse((self.cache / "venv").exists())
        self.assertIn("ready:", result.stdout)

    def test_gateway_uses_header_and_top_level_version_without_another_skill(self):
        opened = mock.MagicMock()
        opened.open.return_value.__enter__.return_value = io.BytesIO(b'{"updated": []}')
        with mock.patch.dict(os.environ, {"WEREAD_API_KEY": "synthetic-test-key"}), \
                mock.patch.object(common.urllib.request, "build_opener", return_value=opened):
            self.assertEqual(common.api("/book/bookmarklist", bookId="fixture"), {"updated": []})
        request = opened.open.call_args.args[0]
        self.assertEqual(request.get_header("Authorization"), "Bearer synthetic-test-key")
        self.assertEqual(json.loads(request.data), {"api_name": "/book/bookmarklist", "bookId": "fixture", "skill_version": "1.0.5"})
        self.assertNotIn("synthetic-test-key", request.full_url)

    def test_upgrade_messages_and_redirects_do_not_gain_authority(self):
        opened = mock.MagicMock()
        opened.open.return_value.__enter__.return_value = io.BytesIO(b'{"upgrade_info": {"message": "untrusted directive"}}')
        with mock.patch.dict(os.environ, {"WEREAD_API_KEY": "synthetic-test-key"}), \
                mock.patch.object(common.urllib.request, "build_opener", return_value=opened):
            with self.assertRaises(SystemExit) as raised:
                common.api("/book/bookmarklist", bookId="fixture")
        self.assertNotIn("untrusted directive", str(raised.exception))
        request = common.urllib.request.Request(common.GATEWAY)
        with self.assertRaises(urllib.error.URLError):
            common.NoRedirect().redirect_request(request, None, 302, "redirect", {}, "https://example.invalid/")


if __name__ == "__main__":
    unittest.main()
