#!/usr/bin/env python3
"""Tests for annotate.py.

The tests that matter guard four things, each of which fails silently if broken:
a blank judgement must not be defaulted, a `verified` date must never be touched
by a depth edit, body text must not be mistaken for header metadata, and
corpus.py must still have no write surface after a writer joined the repository.

Runs under stdlib unittest and under pytest. All dates pinned.
"""

from __future__ import annotations

import csv
import os
import re
import tempfile
import unittest

import annotate
import corpus

CHUNK = """---
topics: sagemaker, s3
topics_basis: extracted-from-body
depth: unreviewed
audience: MLA-C01 candidates
written: 2025-11-18
verified:
duration_min: 6
source: MLA.txt
title: TS 4.3 part 2
---

Narration mentioning monitoring.
And a body line that says depth: practice as prose, not metadata.
"""


class _Corpus(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = os.path.join(self.tmp.name, "chunks")
        os.makedirs(self.dir)
        for slug in ("ts-4-3-part-2", "ts-1-1-part-1"):
            with open(os.path.join(self.dir, slug + ".md"), "w",
                      encoding="utf-8") as handle:
                handle.write(CHUNK)
        self.sheet = os.path.join(self.tmp.name, "depths.tsv")

    def tearDown(self):
        self.tmp.cleanup()

    def read(self, slug: str) -> str:
        with open(os.path.join(self.dir, slug + ".md"), encoding="utf-8") as h:
            return h.read()

    def write_sheet(self, rows: list[dict]) -> None:
        with open(self.sheet, "w", encoding="utf-8", newline="") as handle:
            handle.write("\t".join(annotate.FIELDS) + "\n")
            for row in rows:
                handle.write("\t".join(row.get(f, "") for f in annotate.FIELDS) + "\n")


class TestWorksheet(_Corpus):
    def test_one_row_per_indexed_chunk(self):
        result = annotate.write_worksheet(self.dir, self.sheet)
        self.assertEqual(result["rows"], 2)
        self.assertEqual(result["unreviewed"], 2)

    def test_new_depth_column_is_emitted_blank(self):
        annotate.write_worksheet(self.dir, self.sheet)
        with open(self.sheet, encoding="utf-8") as handle:
            body = handle.read()
        self.assertIn("new_depth", body.splitlines()[0])
        for line in body.splitlines()[1:]:
            cells = line.split("\t")
            self.assertEqual(cells[annotate.FIELDS.index("new_depth")], "")

    def test_unindexed_files_are_reported_not_hidden(self):
        with open(os.path.join(self.dir, "stray.md"), "w",
                  encoding="utf-8") as handle:
            handle.write("no header here\n")
        result = annotate.write_worksheet(self.dir, self.sheet)
        self.assertEqual(result["rows"], 2)
        self.assertEqual(result["unindexed"], 1)
        self.assertIn("NOT in this worksheet", annotate.format_worksheet(result))

    def test_missing_corpus_refuses_with_guidance(self):
        refused = annotate.write_worksheet(
            os.path.join(self.tmp.name, "nope"), self.sheet
        )
        self.assertIsInstance(refused, annotate.Refusal)
        self.assertTrue(refused.guidance.strip())


class TestApply(_Corpus):
    def test_sets_a_valid_depth(self):
        self.write_sheet([{"slug": "ts-4-3-part-2", "new_depth": "practice"}])
        result = annotate.apply_worksheet(self.dir, self.sheet)
        self.assertEqual(result["set"], 1)
        self.assertIn("depth: practice", self.read("ts-4-3-part-2"))

    def test_blank_new_depth_is_left_alone_never_defaulted(self):
        # Rule 3. A default here would classify every row a person skipped, and
        # the corpus would then look fully judged.
        self.write_sheet([{"slug": "ts-4-3-part-2", "new_depth": ""}])
        result = annotate.apply_worksheet(self.dir, self.sheet)
        self.assertEqual(result["set"], 0)
        self.assertEqual(result["left_blank"], ["ts-4-3-part-2"])
        self.assertIn("depth: unreviewed", self.read("ts-4-3-part-2"))

    def test_verified_is_never_touched(self):
        # Classifying is not checking. A tool that set both would let one pass
        # for the other, which is the failure the whole repository is about.
        self.write_sheet([{"slug": "ts-4-3-part-2", "new_depth": "aware"}])
        annotate.apply_worksheet(self.dir, self.sheet)
        text = self.read("ts-4-3-part-2")
        self.assertIn("verified:\n", text)
        self.assertNotIn("verified: 20", text)
        chunk = corpus.load_chunk("x.md", text)
        self.assertIsNone(chunk.verified)
        self.assertEqual(
            corpus.currency(chunk.verified, corpus.dt.date(2026, 10, 17), 365),
            corpus.UNKNOWN,
        )

    def test_body_prose_beginning_depth_is_not_edited(self):
        # Rule 4. The fixture body contains "depth: practice" as narration.
        self.write_sheet([{"slug": "ts-4-3-part-2", "new_depth": "aware"}])
        annotate.apply_worksheet(self.dir, self.sheet)
        text = self.read("ts-4-3-part-2")
        self.assertEqual(len(re.findall(r"^depth: aware$", text, re.M)), 1)
        self.assertIn("says depth: practice as prose", text)

    def test_invalid_depth_is_refused_and_named(self):
        self.write_sheet([{"slug": "ts-4-3-part-2", "new_depth": "deep"}])
        result = annotate.apply_worksheet(self.dir, self.sheet)
        self.assertEqual(result["set"], 0)
        self.assertEqual(len(result["refused"]), 1)
        self.assertIn("deep", result["refused"][0])
        self.assertIn("depth: unreviewed", self.read("ts-4-3-part-2"))

    def test_unreviewed_cannot_be_written_back(self):
        self.write_sheet([{"slug": "ts-4-3-part-2", "new_depth": "unreviewed"}])
        result = annotate.apply_worksheet(self.dir, self.sheet)
        self.assertEqual(result["set"], 0)
        self.assertEqual(len(result["refused"]), 1)

    def test_unknown_slug_is_reported_not_silently_dropped(self):
        self.write_sheet([{"slug": "does-not-exist", "new_depth": "aware"}])
        result = annotate.apply_worksheet(self.dir, self.sheet)
        self.assertEqual(result["slug_not_found"], ["does-not-exist"])

    def test_counts_are_printed_even_when_zero(self):
        self.write_sheet([{"slug": "ts-4-3-part-2", "new_depth": "aware"}])
        rendered = annotate.format_apply(
            annotate.apply_worksheet(self.dir, self.sheet)
        )
        self.assertIn("Slug not found:  0", rendered)
        self.assertIn("Refused:         0", rendered)

    def test_applying_twice_is_idempotent(self):
        self.write_sheet([{"slug": "ts-4-3-part-2", "new_depth": "aware"}])
        annotate.apply_worksheet(self.dir, self.sheet)
        second = annotate.apply_worksheet(self.dir, self.sheet)
        self.assertEqual(second["set"], 0)
        self.assertEqual(second["unchanged"], 1)

    def test_round_trip_makes_the_chunk_searchable_at_its_depth(self):
        annotate.write_worksheet(self.dir, self.sheet)
        with open(self.sheet, encoding="utf-8") as handle:
            lines = handle.read().splitlines()
        header, rows = lines[0], lines[1:]
        index = annotate.FIELDS.index("new_depth")
        fixed = []
        for row in rows:
            cells = row.split("\t")
            cells[index] = "walkthrough"
            fixed.append("\t".join(cells))
        with open(self.sheet, "w", encoding="utf-8") as handle:
            handle.write("\n".join([header] + fixed) + "\n")

        annotate.apply_worksheet(self.dir, self.sheet)
        inv = corpus.build_inventory(self.dir)
        result = corpus.search(inv, "sagemaker", corpus.dt.date(2026, 10, 17))
        self.assertEqual(result.unreviewed_depth, 0)
        self.assertTrue(result.hits)
        narrowed = corpus.search(
            inv, "sagemaker", corpus.dt.date(2026, 10, 17), depth="aware"
        )
        self.assertEqual(narrowed.hits, ())

    def test_missing_worksheet_refuses(self):
        refused = annotate.apply_worksheet(
            self.dir, os.path.join(self.tmp.name, "nope.tsv")
        )
        self.assertIsInstance(refused, annotate.Refusal)
        self.assertEqual(refused.error, "worksheet_not_found")


class TestDelimiterHandling(_Corpus):
    """Chosen by extension on write, DETECTED on read.

    MEASURED 2026-10-17 on macOS: nothing is registered to handle `.tsv`, so
    `open file.tsv` does nothing at all — no app, no error message. That silent
    failure is what this is for.
    """

    def test_csv_extension_produces_commas(self):
        out = os.path.join(self.tmp.name, "depths.csv")
        annotate.write_worksheet(self.dir, out)
        with open(out, encoding="utf-8") as handle:
            header = handle.readline()
        self.assertIn(",", header)
        self.assertNotIn("\t", header)

    def test_tsv_extension_still_produces_tabs(self):
        annotate.write_worksheet(self.dir, self.sheet)
        with open(self.sheet, encoding="utf-8") as handle:
            self.assertIn("\t", handle.readline())

    def test_commas_inside_topics_survive_a_csv_round_trip(self):
        # The reason tab was the original default: the topics column contains
        # commas. If quoting were wrong, the columns would shift and new_depth
        # would be read from the wrong cell — which would set depths against the
        # wrong chunks and look entirely successful.
        out = os.path.join(self.tmp.name, "depths.csv")
        annotate.write_worksheet(self.dir, out)
        with open(out, encoding="utf-8", newline="") as handle:
            rows = list(csv.DictReader(handle))
        self.assertEqual(rows[0]["topics"], "sagemaker, s3")
        self.assertEqual(rows[0]["current_depth"], "unreviewed")

    def test_apply_reads_a_csv_worksheet(self):
        out = os.path.join(self.tmp.name, "depths.csv")
        annotate.write_worksheet(self.dir, out)
        with open(out, encoding="utf-8", newline="") as handle:
            rows = list(csv.DictReader(handle))
        for row in rows:
            row["new_depth"] = "practice"
        with open(out, "w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=annotate.FIELDS)
            writer.writeheader()
            writer.writerows(rows)

        result = annotate.apply_worksheet(self.dir, out)
        self.assertEqual(result["set"], 2)
        self.assertIn("depth: practice", self.read("ts-4-3-part-2"))

    def test_a_tsv_named_file_saved_as_csv_is_still_read(self):
        # Excel will save a file called .tsv with commas. Sniffing the header
        # rather than trusting the extension is what makes that survive instead
        # of being refused as malformed.
        annotate.write_worksheet(self.dir, self.sheet)
        with open(self.sheet, encoding="utf-8", newline="") as handle:
            rows = list(csv.DictReader(handle, delimiter="\t"))
        for row in rows:
            row["new_depth"] = "aware"
        with open(self.sheet, "w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=annotate.FIELDS)
            writer.writeheader()
            writer.writerows(rows)

        self.assertEqual(annotate.sniff_delimiter(self.sheet), ",")
        result = annotate.apply_worksheet(self.dir, self.sheet)
        self.assertEqual(result["set"], 2)


class TestCorpusStillHasNoWriteSurface(unittest.TestCase):
    """A writer joined the repository; corpus.py must still not be one.

    MUTATE INVERTED: the mutant worth running moves a write into corpus.py. This
    duplicates the invariant in test_corpus.py deliberately — that one guards the
    module, this one guards against the refactor that would "tidy" annotate.py's
    writer into corpus.py and delete the guarantee as a side effect.
    """

    def test_corpus_module_opens_nothing_for_writing(self):
        with open(corpus.__file__, encoding="utf-8") as handle:
            source = handle.read()
        self.assertEqual(re.findall(r"open\([^)]*['\"][wax]", source), [])

    def test_annotate_is_the_only_writer_and_says_so(self):
        with open(annotate.__file__, encoding="utf-8") as handle:
            source = handle.read()
        self.assertIn("corpus.py STAYS READ-ONLY", source)
        self.assertTrue(re.findall(r"open\([^)]*['\"]w", source))


if __name__ == "__main__":
    unittest.main()
