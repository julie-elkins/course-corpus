#!/usr/bin/env python3
"""Tests for corpus.py.

Runs under stdlib unittest (`python3 -m unittest`) and under pytest, because the
layout satisfies both and the repository must not require a dependency install to
be verifiable.

Two classes here test an ABSENCE — TestReadOnlyInvariant and
TestNeverInfersFromMtime. Both pass trivially today and would go on passing after
the forbidden thing arrived, so each mutates INVERTED: the mutant that
distinguishes the two states is one that ADDS the write or the mtime read, not one
that removes an assertion. Noted because a reader checking these off a list cannot
tell the difference.

Every date is pinned. Nothing here calls date.today(), because a threshold test
against a moving clock passes for weeks and then fails for a reason that has
nothing to do with the code.
"""

from __future__ import annotations

import datetime as dt
import os
import re
import tempfile
import unittest

import corpus

TODAY = dt.date(2026, 10, 17)

GOOD = """---
topics: photosynthesis, energy transfer
depth: aware
audience: Y7 introductory
written: 2026-02-10
verified: 2026-09-30
---

Plants make their own food using light.
"""

NO_VERIFIED = """---
topics: osmosis
depth: practice
audience: Y10
written: 2025-01-01
verified:
---

Design an investigation.
"""

NO_HEADER = "An old lesson file nobody has annotated.\n"

BROKEN_HEADER = """---
topics: mitosis
depth: walkthrough
---

Missing audience and written.
"""

BAD_DEPTH = """---
topics: respiration
depth: deep
audience: Y11
written: 2026-01-01
---

Depth is not one of the three levels.
"""


def write_corpus(root: str, files: dict[str, str]) -> None:
    for name, text in files.items():
        path = os.path.join(root, name)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(text)


class TestCurrency(unittest.TestCase):
    """The three verdicts, and the one that must never flatter."""

    def test_recent_verification_is_current(self):
        self.assertEqual(
            corpus.currency("2026-09-30", TODAY, 365), corpus.CURRENT
        )

    def test_old_verification_is_stale(self):
        self.assertEqual(
            corpus.currency("2022-07-01", TODAY, 365), corpus.STALE
        )

    def test_absent_verification_is_unknown_and_never_current(self):
        # The probe that matters. The two above pass on almost any
        # implementation; this is the one that fails on the implementation
        # somebody writes when they treat a missing date as "fine".
        for absent in (None, "", "   "):
            verdict = corpus.currency(absent, TODAY, 365)
            self.assertEqual(verdict, corpus.UNKNOWN)
            self.assertNotEqual(verdict, corpus.CURRENT)

    def test_unparseable_verification_is_unknown(self):
        self.assertEqual(
            corpus.currency("last summer", TODAY, 365), corpus.UNKNOWN
        )

    def test_future_verification_is_unknown_not_current(self):
        # A future date is a typo. Reading it as CURRENT would make the most
        # careless entry in the corpus the most trusted one.
        self.assertEqual(
            corpus.currency("2027-01-01", TODAY, 365), corpus.UNKNOWN
        )

    def test_boundary_day_is_current(self):
        exactly = (TODAY - dt.timedelta(days=365)).isoformat()
        self.assertEqual(corpus.currency(exactly, TODAY, 365), corpus.CURRENT)
        one_more = (TODAY - dt.timedelta(days=366)).isoformat()
        self.assertEqual(corpus.currency(one_more, TODAY, 365), corpus.STALE)

    def test_threshold_is_a_parameter_not_a_constant(self):
        # Same date, two regimes, two verdicts. Pins the positive direction as
        # well as the negative one: a hardcoded comparison would make one of
        # these two assertions pass for the wrong reason.
        date = "2026-06-01"
        self.assertEqual(corpus.currency(date, TODAY, 365), corpus.CURRENT)
        self.assertEqual(corpus.currency(date, TODAY, 30), corpus.STALE)


class TestHeaderParsing(unittest.TestCase):
    """Three outcomes, kept distinct."""

    def test_usable_header_yields_chunk(self):
        loaded = corpus.load_chunk("a.md", GOOD)
        self.assertIsInstance(loaded, corpus.Chunk)
        self.assertEqual(loaded.depth, "aware")
        self.assertIn("photosynthesis", loaded.topics)
        self.assertEqual(loaded.verified, "2026-09-30")

    def test_empty_verified_field_normalises_to_none(self):
        loaded = corpus.load_chunk("b.md", NO_VERIFIED)
        self.assertIsInstance(loaded, corpus.Chunk)
        self.assertIsNone(loaded.verified)

    def test_no_header_is_a_problem_naming_that(self):
        loaded = corpus.load_chunk("c.md", NO_HEADER)
        self.assertIsInstance(loaded, corpus.Problem)
        self.assertEqual(loaded.reason, "no header block")

    def test_broken_header_is_distinct_from_missing_header(self):
        loaded = corpus.load_chunk("d.md", BROKEN_HEADER)
        self.assertIsInstance(loaded, corpus.Problem)
        self.assertIn("missing fields", loaded.reason)
        self.assertIn("audience", loaded.reason)
        self.assertNotEqual(loaded.reason, "no header block")

    def test_depth_outside_the_three_levels_is_refused(self):
        loaded = corpus.load_chunk("e.md", BAD_DEPTH)
        self.assertIsInstance(loaded, corpus.Problem)
        self.assertIn("deep", loaded.reason)

    def test_unclosed_header_is_reported(self):
        loaded = corpus.load_chunk("f.md", "---\ntopics: x\n")
        self.assertIsInstance(loaded, corpus.Problem)
        self.assertIn("never closed", loaded.reason)


class TestInventory(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        write_corpus(
            self.tmp.name,
            {
                "good.md": GOOD,
                "none.md": NO_HEADER,
                "broken.md": BROKEN_HEADER,
                "notes.pdf": "not text",
            },
        )
        self.inv = corpus.build_inventory(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_counts_split_indexed_from_invisible(self):
        self.assertEqual(self.inv.files_seen, 4)
        self.assertEqual(len(self.inv.chunks), 1)
        self.assertEqual(self.inv.unindexed, 3)

    def test_every_unindexed_file_carries_a_reason(self):
        for problem in self.inv.problems:
            self.assertTrue(problem.reason.strip())

    def test_unsupported_type_is_counted_not_silently_skipped(self):
        reasons = " ".join(p.reason for p in self.inv.problems)
        self.assertIn("unsupported file type .pdf", reasons)
        self.assertEqual(self.inv.suffix_counts[".pdf"], 1)

    def test_missing_corpus_is_a_refusal_with_guidance_not_an_exception(self):
        refused = corpus.build_inventory(
            os.path.join(self.tmp.name, "nope")
        )
        self.assertIsInstance(refused, corpus.Refusal)
        self.assertEqual(refused.error, "corpus_not_found")
        self.assertTrue(refused.guidance.strip())


class TestSearch(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        write_corpus(
            self.tmp.name,
            {"good.md": GOOD, "none.md": NO_HEADER, "stale.md": _stale_chunk()},
        )
        self.inv = corpus.build_inventory(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_topic_match_is_found(self):
        result = corpus.search(self.inv, "photosynthesis", TODAY)
        self.assertEqual(len(result.hits), 1)
        self.assertIn("photosynthesis", result.hits[0].matched_topics)

    def test_body_only_match_is_found_and_labelled(self):
        result = corpus.search(self.inv, "light", TODAY)
        self.assertEqual(len(result.hits), 1)
        self.assertEqual(result.hits[0].matched_topics, ())

    def test_no_match_is_an_explicit_empty_result(self):
        result = corpus.search(self.inv, "trigonometry", TODAY)
        self.assertEqual(result.hits, ())
        rendered = corpus.format_search(result)
        self.assertIn("No chunk matches", rendered)

    def test_unindexed_count_matches_the_inventory_exactly(self):
        # If these two disagree, one of them is lying and neither can be
        # trusted. Pinning the equality is what makes the coverage line
        # evidence rather than decoration.
        result = corpus.search(self.inv, "photosynthesis", TODAY)
        self.assertEqual(result.unindexed, self.inv.unindexed)
        self.assertEqual(result.files_seen, self.inv.files_seen)

    def test_coverage_line_appears_on_a_SUCCESSFUL_result_too(self):
        result = corpus.search(self.inv, "photosynthesis", TODAY)
        rendered = corpus.format_search(result)
        self.assertIn("Coverage:", rendered)
        self.assertIn("invisible to this query", rendered)

    def test_unknown_is_not_described_as_clean_in_the_output(self):
        rendered = corpus.format_search(
            corpus.search(self.inv, "photosynthesis", TODAY)
        )
        self.assertIn("NOT a clean bill of health", rendered)

    def test_depth_filter_excludes_other_depths(self):
        result = corpus.search(self.inv, "photosynthesis", TODAY, depth="practice")
        self.assertEqual(result.hits, ())

    def test_stale_chunk_reports_stale(self):
        result = corpus.search(self.inv, "caching", TODAY)
        self.assertEqual(len(result.hits), 1)
        self.assertEqual(result.hits[0].currency, corpus.STALE)


def _stale_chunk() -> str:
    return """---
topics: http caching
depth: walkthrough
audience: junior developers
written: 2022-06-14
verified: 2022-07-01
---

Cache-Control directives.
"""


class TestReadOnlyInvariant(unittest.TestCase):
    """Design rule 1: this module has no write surface over the corpus.

    MUTATE INVERTED. Deleting an assertion here is not the interesting mutant;
    ADDING a write call to corpus.py is, because that is the change this test
    exists to catch and the only one that distinguishes "no writes" from "writes
    nobody has noticed".
    """

    def setUp(self):
        with open(corpus.__file__, "r", encoding="utf-8") as handle:
            self.source = handle.read()

    def test_no_write_mode_open_calls(self):
        writes = re.findall(r"open\([^)]*['\"][wax]", self.source)
        self.assertEqual(writes, [], "corpus.py opened a file for writing")

    def test_no_destructive_filesystem_calls(self):
        for forbidden in (
            "os.remove",
            "os.unlink",
            "os.rename",
            "os.replace",
            "os.rmdir",
            "shutil.",
            "os.makedirs",
            "Path.write_text",
            "write_text(",
            "writelines(",
        ):
            self.assertNotIn(
                forbidden,
                self.source,
                "corpus.py contains %s" % forbidden,
            )


class TestNeverInfersFromMtime(unittest.TestCase):
    """Design rule 4: a touched file is not a checked file.

    MUTATE INVERTED, same as above: the mutant worth running adds an mtime read
    to corpus.py. One bulk copy of a corpus would otherwise set every
    modification time to the same recent instant and mark the whole thing fresh.
    """

    def test_source_does_not_read_modification_times(self):
        with open(corpus.__file__, "r", encoding="utf-8") as handle:
            source = handle.read()
        for forbidden in ("st_mtime", "getmtime", "os.stat(", "Path.stat"):
            self.assertNotIn(forbidden, source)

    def test_currency_ignores_everything_except_the_recorded_date(self):
        # Same absent date, same verdict, regardless of any file on disk.
        self.assertEqual(corpus.currency(None, TODAY, 365), corpus.UNKNOWN)


if __name__ == "__main__":
    unittest.main()
