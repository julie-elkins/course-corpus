#!/usr/bin/env python3
"""Set depth on chunks a person has judged, one worksheet at a time.

`chunk.py` writes every chunk `depth: unreviewed` because guessing would produce
a corpus that looks classified by nobody. Something then has to let a human make
those judgements without opening sixty files by hand, and this is it:

    annotate.py worksheet <corpus> depths.tsv     # emit one row per chunk
    # fill in the new_depth column
    annotate.py apply <corpus> depths.tsv         # write them back

Design rules, in force throughout this module:

1. THIS IS A SEPARATE MODULE SO THAT corpus.py STAYS READ-ONLY. The search tool
   has a test asserting it has no write surface over a corpus; putting the writer
   inside it would quietly delete that guarantee. The invariant is worth more
   than the convenience of one file.
2. IT WRITES THE `depth` LINE AND NOTHING ELSE. Never `verified` — a verification
   is a different human act with a different meaning, and a tool that set both at
   once would let "I classified this" pass as "I checked this".
3. A BLANK `new_depth` IS LEFT ALONE, never defaulted. The whole point is that an
   unmade judgement stays visibly unmade, and a default here would silently
   classify the rows a person skipped.
4. ONLY THE HEADER IS EDITED. A body line that happens to begin "depth:" is left
   untouched, because narration about depth is not metadata.
5. IT REPORTS WHAT IT DID NOT DO. Rows skipped, slugs it could not find, values
   it refused — counted and named, so a partial run cannot read as a complete one.
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import os
import re
import sys
from dataclasses import dataclass

import corpus

# Only the three real levels are settable. `unreviewed` is excluded on purpose:
# it is the absence of a judgement, and there is no reason to write it back onto
# a chunk that already carries it.
SETTABLE = corpus.DEPTHS

FIELDS = ("slug", "current_depth", "new_depth", "current_written",
          "new_written", "current_verified", "new_verified", "duration_min",
          "topics_basis", "title", "topics")

_HEADER_DEPTH = re.compile(r"^depth:.*$", re.MULTILINE)
_HEADER_WRITTEN = re.compile(r"^written:.*$", re.MULTILINE)
_HEADER_VERIFIED = re.compile(r"^verified:.*$", re.MULTILINE)

_ISO_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

# Each settable field: worksheet column, header pattern, and whether an
# empty value is a legitimate answer. `verified` is the only field where
# CLEARING it is meaningful -- "this is no longer confirmed" is a real
# statement, and the sentinel below says it explicitly rather than relying
# on a blank cell, which already means "no judgement made".
CLEAR = "none"



def delimiter_for(path: str) -> str:
    """Tab or comma, chosen by extension.

    MEASURED 2026-10-17 on macOS: nothing is registered to handle `.tsv`, so
    `open file.tsv` fails SILENTLY — no app, no error. A `.csv` opens in Excel on
    a double-click. Tab remains the default because the topics column contains
    commas, but a corpus owner who wants to edit in a spreadsheet should not have
    to know that.
    """
    return "," if path.lower().endswith(".csv") else "\t"


def sniff_delimiter(path: str) -> str:
    """Detect the delimiter of an existing worksheet from its header row.

    Detected rather than assumed from the extension, because Excel will happily
    save a file named .tsv as comma-separated. Reading the header is cheap and it
    is the only line guaranteed to contain a delimiter.
    """
    try:
        with open(path, "r", encoding="utf-8", newline="") as handle:
            header = handle.readline()
    except OSError:
        return "\t"
    if "\t" in header:
        return "\t"
    if "," in header:
        return ","
    return "\t"


@dataclass(frozen=True)
class Refusal:
    error: str
    message: str
    guidance: str


def _split_header(text: str) -> tuple[str, str] | None:
    """Return (header, rest) where header includes both fences, or None."""
    lines = text.splitlines(keepends=True)
    if not lines or lines[0].strip() != "---":
        return None
    for index in range(1, len(lines)):
        if lines[index].strip() == "---":
            return "".join(lines[: index + 1]), "".join(lines[index + 1 :])
    return None


def write_worksheet(corpus_dir: str, out_path: str) -> dict | Refusal:
    """Emit one row per chunk, with new_depth blank for a human to fill in."""
    inventory = corpus.build_inventory(corpus_dir)
    if isinstance(inventory, corpus.Refusal):
        return Refusal(inventory.error, inventory.message, inventory.guidance)

    rows = []
    for chunk in sorted(inventory.chunks, key=lambda c: c.path):
        rows.append({
            "slug": os.path.splitext(os.path.basename(chunk.path))[0],
            "current_depth": chunk.depth,
            "new_depth": "",
            "current_written": chunk.written,
            "new_written": "",
            "current_verified": chunk.verified or "",
            "new_verified": "",
            "duration_min": "" if chunk.duration_min is None else "%g" % chunk.duration_min,
            "topics_basis": chunk.topics_basis or "",
            "title": chunk.title or "",
            "topics": ", ".join(chunk.topics),
        })

    # Tab-separated, not comma: the topics column contains commas, and a CSV
    # here would need quoting that a spreadsheet round-trip tends to mangle.
    with open(out_path, "w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(
            handle, fieldnames=FIELDS, delimiter=delimiter_for(out_path)
        )
        writer.writeheader()
        writer.writerows(rows)

    unreviewed = sum(1 for c in inventory.chunks if c.depth_is_unreviewed)
    return {
        "rows": len(rows),
        "unreviewed": unreviewed,
        "out_path": out_path,
        "unindexed": inventory.unindexed,
    }


def apply_worksheet(corpus_dir: str, in_path: str) -> dict | Refusal:
    """Write the filled-in depths back into the chunk files."""
    if not os.path.isfile(in_path):
        return Refusal(
            error="worksheet_not_found",
            message="No worksheet at %r." % in_path,
            guidance="Run `annotate.py worksheet` first to generate one.",
        )
    if not os.path.isdir(corpus_dir):
        return Refusal(
            error="corpus_not_found",
            message="No directory at %r." % corpus_dir,
            guidance="Pass the directory holding the chunk files.",
        )

    with open(in_path, "r", encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle, delimiter=sniff_delimiter(in_path)))

    if not rows or "new_depth" not in (rows[0].keys() if rows else {}):
        return Refusal(
            error="worksheet_malformed",
            message="Worksheet has no new_depth column.",
            guidance=(
                "Keep the header row from the generated worksheet. Editing the "
                "column names breaks the match-up."
            ),
        )

    set_count = 0
    unchanged = 0
    blank: list[str] = []
    missing: list[str] = []
    refused: list[str] = []

    today = dt.date.today()

    for row in rows:
        slug = (row.get("slug") or "").strip()
        if not slug:
            continue

        # Each entry: column, header regex, replacement line, or a refusal
        # reason. Collected BEFORE anything is written, so a row with one bad
        # value changes nothing at all rather than landing half of its edits.
        edits: list[tuple[re.Pattern[str], str]] = []
        problems: list[str] = []

        depth = (row.get("new_depth") or "").strip().lower()
        if depth:
            if depth in SETTABLE:
                edits.append((_HEADER_DEPTH, "depth: %s" % depth))
            else:
                problems.append("depth %r not one of %s"
                                % (depth, ", ".join(SETTABLE)))

        for column, pattern, field in (
            ("new_written", _HEADER_WRITTEN, "written"),
            ("new_verified", _HEADER_VERIFIED, "verified"),
        ):
            value = (row.get(column) or "").strip()
            if not value:
                continue
            if field == "verified" and value.lower() == CLEAR:
                # Only `verified` may be cleared: withdrawing a confirmation is
                # a real statement, where an unwritten date is not.
                edits.append((pattern, "verified:"))
                continue
            if not _ISO_DATE.match(value):
                problems.append("%s %r is not YYYY-MM-DD" % (field, value))
                continue
            try:
                parsed = dt.date.fromisoformat(value)
            except ValueError:
                problems.append("%s %r is not a real date" % (field, value))
                continue
            if parsed > today:
                # A future date is a typo. Accepting one would make the most
                # careless row the most trusted, and corpus.py would then read
                # it as UNKNOWN anyway — refusing here says why.
                problems.append("%s %s is in the future" % (field, value))
                continue
            edits.append((pattern, "%s: %s" % (field, value)))

        if problems:
            refused.append("%s (%s)" % (slug, "; ".join(problems)))
            continue
        if not edits:
            # Rule 3. An unmade judgement stays unmade.
            blank.append(slug)
            continue

        path = os.path.join(corpus_dir, slug + ".md")
        if not os.path.isfile(path):
            missing.append(slug)
            continue

        with open(path, "r", encoding="utf-8") as handle:
            text = handle.read()
        split = _split_header(text)
        if split is None:
            refused.append("%s (no header block to edit)" % slug)
            continue

        header, rest = split
        new_header = header
        failed = None
        for pattern, replacement in edits:
            new_header, count = pattern.subn(replacement, new_header, count=1)
            if count != 1:
                failed = replacement.split(":")[0]
                break
        if failed:
            refused.append("%s (no %s line in header)" % (slug, failed))
            continue

        if new_header == header:
            unchanged += 1
            continue

        with open(path, "w", encoding="utf-8") as handle:
            handle.write(new_header + rest)
        set_count += 1

    return {
        "set": set_count,
        "unchanged": unchanged,
        "left_blank": blank,
        "slug_not_found": missing,
        "refused": refused,
        "rows": len(rows),
    }


def format_worksheet(result: dict) -> str:
    lines = [
        "Wrote %d row(s) to %s" % (result["rows"], result["out_path"]),
        "%d of them have no human-set depth." % result["unreviewed"],
        "",
        "new_depth:     one of %s" % ", ".join(SETTABLE),
        "new_written:   YYYY-MM-DD, the date the content was WRITTEN",
        "new_verified:  YYYY-MM-DD a human last confirmed it is still true,",
        "               or the word 'none' to withdraw a confirmation",
        "",
        "Leave a cell blank to decide it later — blanks are never defaulted,",
        "and a row with any bad value changes nothing at all.",
        "Then: annotate.py apply <corpus> %s" % result["out_path"],
    ]
    if result["unindexed"]:
        lines.append("")
        lines.append(
            "NOTE: %d file(s) in the corpus are unindexed and are NOT in this "
            "worksheet. Run `corpus.py inventory` to see which."
            % result["unindexed"]
        )
    return "\n".join(lines)


def format_apply(result: dict) -> str:
    lines = [
        "Read %d row(s)." % result["rows"],
        "Depth set:      %d" % result["set"],
        "Already correct: %d" % result["unchanged"],
        "Left blank:      %d  (no judgement made, nothing written)"
        % len(result["left_blank"]),
    ]
    # Rule 5. Both of these are printed even when zero, so a clean run says so
    # rather than leaving the reader to infer it from silence.
    lines.append("Slug not found:  %d" % len(result["slug_not_found"]))
    for slug in result["slug_not_found"]:
        lines.append("    %s" % slug)
    lines.append("Refused:         %d" % len(result["refused"]))
    for item in result["refused"]:
        lines.append("    %s" % item)
    lines.append("")
    lines.append(
        "A `verified` date was written only where you supplied one. "
        "Classifying a chunk is not the same act as checking it."
    )
    return "\n".join(lines)


def format_refusal(refusal: Refusal) -> str:
    return "\n".join(["Refused: %s" % refusal.error, refusal.message,
                      "Next: %s" % refusal.guidance])


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="annotate.py",
        description="Set depth on chunks a person has judged.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    ws = sub.add_parser("worksheet", help="emit a worksheet of chunks to judge")
    ws.add_argument("corpus", help="path to the corpus directory")
    ws.add_argument(
        "out_path",
        help="worksheet file to write; .csv opens in Excel, .tsv does not",
    )

    ap = sub.add_parser("apply", help="write filled-in depths back into chunks")
    ap.add_argument("corpus", help="path to the corpus directory")
    ap.add_argument("in_path", help="filled-in worksheet")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "worksheet":
        result = write_worksheet(args.corpus, args.out_path)
        formatter = format_worksheet
    else:
        result = apply_worksheet(args.corpus, args.in_path)
        formatter = format_apply

    if isinstance(result, Refusal):
        print(format_refusal(result), file=sys.stderr)
        return 2
    print(formatter(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
