#!/usr/bin/env python3
"""Tests for chunk.py.

The four tests that matter here all guard against the splitter inventing
information it does not have: a depth, a verification date, a written date, or
permission to write course content into a repository. Each of those failures
produces output that looks completely correct.

Runs under stdlib unittest and under pytest. All dates pinned.
"""

from __future__ import annotations

import os
import subprocess
import tempfile
import unittest

import chunk
import corpus

SCRIPT = """Front matter before any lesson heading.
More front matter.

What is machine learning? (5 minutes)
Narration for the first lesson.
Second line of narration.

Machine Learning Lifecycle (4 minutes)
Narration for the second lesson.

TS 4.3 part 2 (6.5 minutes)
Narration for the third lesson.
"""

NO_HEADINGS = "Just prose.\nNo parenthesised durations anywhere.\n"


class TestLessonSplitting(unittest.TestCase):
    def test_finds_every_lesson_and_its_duration(self):
        lessons, preamble = chunk.find_lessons(SCRIPT)
        self.assertEqual(len(lessons), 3)
        self.assertEqual([l.title for l in lessons],
                         ["What is machine learning?",
                          "Machine Learning Lifecycle",
                          "TS 4.3 part 2"])
        self.assertEqual([l.duration_min for l in lessons], [5.0, 4.0, 6.5])

    def test_preamble_is_reported_not_folded_into_lesson_one(self):
        lessons, preamble = chunk.find_lessons(SCRIPT)
        self.assertEqual(preamble, 3)
        self.assertNotIn("Front matter", lessons[0].body)

    def test_body_stops_at_the_next_heading(self):
        lessons, _ = chunk.find_lessons(SCRIPT)
        self.assertIn("first lesson", lessons[0].body)
        self.assertNotIn("second lesson", lessons[0].body)

    def test_no_headings_yields_no_lessons(self):
        lessons, preamble = chunk.find_lessons(NO_HEADINGS)
        self.assertEqual(lessons, ())
        self.assertEqual(preamble, 2)

    def test_slug_collision_does_not_overwrite(self):
        taken: set[str] = set()
        first = chunk.slugify("Domain 1 introduction", taken)
        second = chunk.slugify("Domain 1 introduction", taken)
        self.assertNotEqual(first, second)
        self.assertEqual(first, "domain-1-introduction")
        self.assertEqual(second, "domain-1-introduction-2")


class TestNeverInventsMetadata(unittest.TestCase):
    """The splitter must leave judgements visibly unmade."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.src = os.path.join(self.tmp.name, "course.txt")
        with open(self.src, "w", encoding="utf-8") as handle:
            handle.write(SCRIPT)
        self.out = os.path.join(self.tmp.name, "out")

    def tearDown(self):
        self.tmp.cleanup()

    def _run(self, **kw):
        args = dict(
            source_path=self.src,
            out_dir=self.out,
            written="2026-03-02",
            audience="MLA-C01 candidates",
        )
        args.update(kw)
        return chunk.chunk_file(**args)

    def test_every_chunk_is_written_unreviewed(self):
        result = self._run()
        self.assertNotIsInstance(result, chunk.Refusal)
        for path, _ in result["files"]:
            with open(path, encoding="utf-8") as handle:
                self.assertIn("depth: unreviewed", handle.read())

    def test_every_chunk_has_an_empty_verified_field(self):
        result = self._run()
        for path, _ in result["files"]:
            with open(path, encoding="utf-8") as handle:
                text = handle.read()
            self.assertIn("verified:\n", text)
            # The precise failure guarded against: a date appearing here would
            # read as CURRENT for a year on content nobody has checked.
            self.assertNotIn("verified: 20", text)

    def test_unreviewed_chunks_still_index_and_are_flagged(self):
        # Writing them unreviewed is only defensible if they remain findable.
        # A splitter that produced unindexable files would be worse than one
        # that guessed, because the content would vanish entirely.
        self._run()
        inv = corpus.build_inventory(self.out)
        self.assertEqual(len(inv.chunks), 3)
        self.assertEqual(inv.unindexed, 0)
        result = corpus.search(inv, "machine", corpus.dt.date(2026, 10, 17))
        self.assertEqual(result.unreviewed_depth, len(result.hits))
        rendered = corpus.format_search(result)
        self.assertIn("NOT SET BY A HUMAN", rendered)
        self.assertIn("no human-set depth", rendered)

    def test_duration_and_source_survive_the_round_trip(self):
        self._run()
        inv = corpus.build_inventory(self.out)
        by_title = {c.title: c for c in inv.chunks}
        self.assertEqual(by_title["TS 4.3 part 2"].duration_min, 6.5)
        self.assertEqual(by_title["TS 4.3 part 2"].source, "course.txt")

    def test_written_date_must_be_supplied_and_well_formed(self):
        for bad in ("today", "2026-3-2", "", "03/02/2026"):
            refused = self._run(written=bad)
            self.assertIsInstance(refused, chunk.Refusal)
            self.assertEqual(refused.error, "written_date_invalid")
            self.assertTrue(refused.guidance.strip())

    def test_missing_source_refuses_with_guidance(self):
        refused = self._run(source_path=os.path.join(self.tmp.name, "nope.txt"))
        self.assertIsInstance(refused, chunk.Refusal)
        self.assertEqual(refused.error, "source_not_found")

    def test_document_with_no_lesson_boundaries_refuses(self):
        flat = os.path.join(self.tmp.name, "flat.txt")
        with open(flat, "w", encoding="utf-8") as handle:
            handle.write(NO_HEADINGS)
        refused = self._run(source_path=flat)
        self.assertIsInstance(refused, chunk.Refusal)
        self.assertEqual(refused.error, "no_lesson_boundaries")

    def test_dry_run_writes_nothing(self):
        result = self._run(dry_run=True)
        self.assertEqual(result["lessons"], 3)
        self.assertFalse(os.path.exists(self.out))


class TestRefusesToWriteIntoAGitRepo(unittest.TestCase):
    """Design rule 5. The worst outcome available is a commit that publishes
    unreleased course material, so the destination is checked rather than left
    to a .gitignore convention.

    MUTATE INVERTED: the interesting mutant removes the check from chunk.py, not
    an assertion from here.
    """

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        subprocess.run(
            ["git", "init", "-q", self.tmp.name],
            check=True,
            capture_output=True,
        )
        self.src = os.path.join(self.tmp.name, "course.txt")
        with open(self.src, "w", encoding="utf-8") as handle:
            handle.write(SCRIPT)

    def tearDown(self):
        self.tmp.cleanup()

    def test_destination_inside_a_repo_is_refused(self):
        refused = chunk.chunk_file(
            source_path=self.src,
            out_dir=os.path.join(self.tmp.name, "chunks"),
            written="2026-03-02",
            audience="MLA-C01 candidates",
        )
        self.assertIsInstance(refused, chunk.Refusal)
        self.assertEqual(refused.error, "destination_inside_git_repo")
        self.assertIn("outside any repository", refused.guidance)

    def test_nothing_was_written_by_the_refused_run(self):
        chunk.chunk_file(
            source_path=self.src,
            out_dir=os.path.join(self.tmp.name, "chunks"),
            written="2026-03-02",
            audience="MLA-C01 candidates",
        )
        # A refusal that had already created the directory would leave content
        # inside the repository regardless of the refusal.
        self.assertFalse(os.path.exists(os.path.join(self.tmp.name, "chunks")))

    def test_override_flag_permits_it(self):
        result = chunk.chunk_file(
            source_path=self.src,
            out_dir=os.path.join(self.tmp.name, "chunks"),
            written="2026-03-02",
            audience="MLA-C01 candidates",
            allow_in_git_repo=True,
        )
        self.assertNotIsInstance(result, chunk.Refusal)
        self.assertEqual(result["lessons"], 3)

    def test_a_git_worktree_file_is_detected_not_just_a_directory(self):
        # `.git` is a FILE in a worktree, so an isdir() check would miss live
        # work and write content straight into it.
        plain = tempfile.TemporaryDirectory()
        try:
            marker = os.path.join(plain.name, ".git")
            with open(marker, "w", encoding="utf-8") as handle:
                handle.write("gitdir: /somewhere/else\n")
            self.assertEqual(
                chunk.inside_git_work_tree(plain.name),
                os.path.abspath(plain.name),
            )
        finally:
            plain.cleanup()


class TestDerivedTopics(unittest.TestCase):
    def test_topics_restate_the_title_without_stopwords(self):
        self.assertEqual(
            chunk.derive_topics("What is machine learning?"),
            "machine, learning",
        )

    def test_title_of_only_stopwords_falls_back_to_the_title(self):
        self.assertEqual(chunk.derive_topics("Introduction"), "introduction")


if __name__ == "__main__":
    unittest.main()
