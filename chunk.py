#!/usr/bin/env python3
"""Split one exported course script into per-lesson chunks.

Reads ONE named file and writes one chunk file per lesson, with the header block
pre-filled as far as can be done honestly. Everything that needs a human
judgement is left visibly unset rather than guessed.

Design rules, in force throughout this module:

1. ONE NAMED INPUT FILE, never a directory sweep. The module has no discovery
   path at all. A sweep over a documents folder would eventually index whatever
   else is in that folder, and the failure is silent: a correctly indexed file
   looks identical to one that should never have been read.
2. DEPTH IS NEVER GUESSED. A chunk is written with `depth: unreviewed`, which
   indexes and searches but reports as unreviewed on every hit. Inferring a depth
   from wording would manufacture exactly the confidence this tool exists to
   deny, and the resulting corpus would look fully annotated.
3. `verified:` IS ALWAYS EMPTY. Nobody has checked this content at the moment it
   is chunked, and a date here would be the one lie that matters.
4. `written:` IS REQUIRED FROM THE CALLER. Today's date is when the split ran,
   not when the content was written, and defaulting to it would silently restate
   old material as new — which would then read as CURRENT for a year.
5. REFUSES TO WRITE INTO A GIT WORKING TREE unless explicitly overridden. Course
   content does not belong in a repository, and the single worst outcome
   available here is a commit that publishes unreleased material. A `.gitignore`
   entry is a convention; this is a check.
6. No part of a chunk's body is ever printed. The output is counts and titles.
"""

from __future__ import annotations

import argparse
import os
import re
import sys
from dataclasses import dataclass

# OBSERVED 2026-10-17 on a 3,557-line exported exam-prep script: every lesson
# boundary in that document is a heading line ending in a parenthesised duration
# ("Walkthrough example (5 minutes)"), 62 of them, and no other line shape marks
# a lesson. Slide markers exist but cover only 22 lines of the document, so they
# cannot carry the split.
LESSON_HEADING = re.compile(
    r"^(?P<title>.{3,120}?)\s*\(\s*(?P<mins>\d+(?:\.\d+)?)\s*(?:minutes?|mins?|min)\s*\)\s*$"
)

# Written into every chunk. corpus.py indexes this and flags it on each hit.
UNREVIEWED = "unreviewed"

# A second boundary shape. MEASURED 2026-10-17 across four exam-prep scripts:
# the parenthesised-duration heading exists in ONE of them (62 lessons) and in
# none of the other three, which would make the splitter refuse them entirely.
# `Task Statement` is the only boundary present in all four, and it is also the
# unit the exam guide uses -- so it is what makes a cross-course search
# comparable rather than merely possible.
TASK_STATEMENT = re.compile(r"^Task Statement\b.*$")

# MEASURED 2026-10-17: every script opens with a contents list where task
# statements sit on CONSECUTIVE lines, then repeats them later as real section
# headings. The gap distribution is cleanly bimodal -- 1 line apart in the list,
# 5 or more apart for real sections (DEA gaps: 1,1,1,1,6,32,37...; DOP:
# 1x8,5,8,12...; DVA: 1x5,16,21,24...). So this threshold separates the two
# populations rather than trading them off, and any value from 2 to 5 gives the
# same answer. 3 is the smallest that removes every consecutive-run entry.
#
# Splitting on the list entries would produce one-line chunks carrying a real
# task statement's title and none of its content -- which would then read as
# genuine coverage of that task statement.
MIN_SECTION_LINES = 3

_SLUG_STRIP = re.compile(r"[^a-z0-9]+")


@dataclass(frozen=True)
class Lesson:
    """One lesson's heading, duration and body line range."""

    title: str
    duration_min: float
    start_line: int
    body: str


@dataclass(frozen=True)
class Refusal:
    error: str
    message: str
    guidance: str


def slugify(title: str, taken: set[str]) -> str:
    """Make a filename from a title, without colliding with one already used."""
    base = _SLUG_STRIP.sub("-", title.lower()).strip("-") or "lesson"
    base = base[:70].strip("-")
    slug = base
    suffix = 2
    while slug in taken:
        slug = "%s-%d" % (base, suffix)
        suffix += 1
    taken.add(slug)
    return slug


def find_task_statements(text: str) -> tuple[tuple[Lesson, ...], int]:
    """Split text at Task Statement headings, skipping contents-list entries.

    Returns the same Lesson shape as find_lessons, with duration_min 0.0 because
    these documents state no per-section duration. 0.0 is rendered as an EMPTY
    duration field rather than as a zero, because "not stated" and "zero
    minutes" are different claims.
    """
    lines = text.splitlines()
    marks = [i for i, line in enumerate(lines) if TASK_STATEMENT.match(line)]
    if not marks:
        return (), len(lines)

    real: list[int] = []
    for position, index in enumerate(marks):
        following = marks[position + 1] if position + 1 < len(marks) else len(lines)
        if following - index >= MIN_SECTION_LINES:
            real.append(index)
    if not real:
        return (), len(lines)

    sections: list[Lesson] = []
    for position, index in enumerate(real):
        end = real[position + 1] if position + 1 < len(real) else len(lines)
        sections.append(
            Lesson(
                title=lines[index].strip(),
                duration_min=0.0,
                start_line=index + 1,
                body="\n".join(lines[index + 1 : end]).strip("\n"),
            )
        )
    return tuple(sections), real[0]


def find_lessons(text: str) -> tuple[tuple[Lesson, ...], int]:
    """Split text at lesson headings.

    Returns the lessons and the count of lines appearing BEFORE the first
    heading. That preamble is reported rather than attached to lesson one: it is
    usually front matter, and silently folding it into the first chunk would
    misattribute it.
    """
    lines = text.splitlines()
    marks: list[tuple[int, str, float]] = []
    for index, line in enumerate(lines):
        match = LESSON_HEADING.match(line)
        if match:
            marks.append(
                (index, match.group("title").strip(), float(match.group("mins")))
            )

    if not marks:
        return (), len(lines)

    lessons: list[Lesson] = []
    for position, (index, title, mins) in enumerate(marks):
        end = marks[position + 1][0] if position + 1 < len(marks) else len(lines)
        body = "\n".join(lines[index + 1 : end]).strip("\n")
        lessons.append(
            Lesson(
                title=title,
                duration_min=mins,
                start_line=index + 1,
                body=body,
            )
        )
    return tuple(lessons), marks[0][0]


def derive_topics(title: str) -> str:
    """Derive a topics line from the lesson title.

    This is a RESTATEMENT of the title, not a judgement about content, which is
    why it is allowed where depth is not. Stopwords are dropped so that a title
    like "What is machine learning?" yields "machine learning" rather than four
    useless terms. The author is expected to correct it; the README says so.
    """
    # Shares _NOT_A_TOPIC rather than keeping a second list. MEASURED
    # 2026-10-17: with two separate stoplists, the title "TS 2.2 part 1"
    # derived to the topic "ts" — the scan already excluded "ts" and the title
    # path did not, so one code path filtered a term the other emitted. Two
    # lists of the same thing drift, and the drift shows up as a junk topic.
    stop = _NOT_A_TOPIC | {
        "an", "are", "as", "at", "by", "from", "into", "is", "of", "on", "to",
        "introduction", "intro", "overview", "course", "lesson",
    }
    words = [w for w in _SLUG_STRIP.sub(" ", title.lower()).split() if w]
    kept = [w for w in words if w not in stop and not w.isdigit()]
    return ", ".join(kept[:6]) if kept else title.lower()


# A GENERAL SCAN, not a word list. A hand-built list of service names misses
# whole families and every capitalised variant, and it goes stale the moment a
# product is renamed. These four patterns match by SHAPE instead:
#   "Amazon X" / "AWS X"  — the product-naming convention
#   CamelCase in one token — SageMaker, CloudWatch, DynamoDB
#   short all-caps        — S3, IAM, ETL, KMS
#   hyphenated technical terms — hyperparameter-tuning, cross-validation
_NAMED = (
    re.compile(r"\b(?:Amazon|AWS)\s+[A-Z][A-Za-z0-9]*(?:\s+[A-Z][A-Za-z0-9]*){0,2}"),
    re.compile(r"\b[A-Z][a-z]+[A-Z][A-Za-z0-9]*\b"),
    # Two alternatives, because requiring two or more capitals missed S3 —
    # MEASURED 2026-10-17, "s3" absent from a body that named it. A single
    # capital followed by digits is a real naming shape (S3, M5), and matching
    # a lone capital with no digits would pull in every "I" and "A".
    re.compile(r"\b(?:[A-Z]{2,6}\d{0,2}|[A-Z]\d{1,2})\b"),
    re.compile(r"\b[a-z]{4,}-[a-z]{4,}(?:-[a-z]{4,})?\b"),
)

# Shapes the scan matches that are never a topic. Deliberately tiny: this is a
# list of SCAN ARTEFACTS, not a list of domain vocabulary. Growing it with real
# subject terms would turn the scan back into a hand-list.
_NOT_A_TOPIC = {
    "aws", "amazon", "i", "ts", "x", "a", "the", "and", "or", "if", "in", "it",
    "slide", "x-slide", "module", "domain", "part", "ok", "so", "now", "next",
    "first", "second", "third", "finally", "here", "this", "that", "you",
    "your", "we", "us", "let", "also", "but", "for", "with", "when", "what",
    "how", "why", "tbd", "faq", "q", "version", "free", "paid", "minutes",
}


def extract_topics(body: str, limit: int = 6) -> list[str]:
    """Pull candidate topics out of a lesson body by scanning for named things.

    Frequency-ranked, because a lesson that says SageMaker eleven times is about
    SageMaker and one that says it once is not. Ties break on first appearance,
    so the ordering is stable across runs rather than depending on dict order.

    This is EXTRACTION, not classification — the same reason title restatement
    was allowed where depth inference was not. It reports what the text says, and
    it can be wrong in the harmless direction (a noisy topic costs a bad search
    hit) rather than the flattering one (a guessed depth costs a false verdict).
    """
    counts: dict[str, int] = {}
    order: dict[str, int] = {}
    for pattern in _NAMED:
        for match in pattern.finditer(body):
            term = match.group(0).strip()
            key = term.lower()
            if key in _NOT_A_TOPIC or len(key) < 2:
                continue
            if key.isdigit():
                continue
            counts[key] = counts.get(key, 0) + 1
            order.setdefault(key, match.start())

    ranked = sorted(counts, key=lambda k: (-counts[k], order[k]))

    # Drop near-duplicates that the overlapping patterns produce: "Amazon VPC"
    # and "VPC" both match, and emitting both spends two of six slots on one
    # topic. The higher-frequency form wins — it is the one the author actually
    # uses, and for search the shorter form matches a superset of what the
    # longer one would.
    kept: list[str] = []
    for candidate in ranked:
        if any(candidate in k or k in candidate for k in kept):
            continue
        kept.append(candidate)
        if len(kept) == limit:
            break
    return kept


def topics_for(lesson: Lesson, limit: int = 6) -> tuple[str, str]:
    """Return (topics_line, basis). Body extraction first, title as fallback.

    MEASURED 2026-10-17: on a 62-lesson exam-prep script, 57 of 62 titles were
    structural codes ("TS 4.3 part 2"), so title restatement produced topics like
    "domain" and "module" and was useless for 92% of the corpus. The body is
    where the subject actually is. `basis` is recorded in the chunk so a later
    reader can tell which chunks have derived topics worth trusting.
    """
    extracted = extract_topics(lesson.body, limit=limit)
    if extracted:
        return ", ".join(extracted), "extracted-from-body"
    fallback = derive_topics(lesson.title)
    return fallback, "restated-from-title"


def render_chunk(
    lesson: Lesson, written: str, audience: str, source: str,
    split_by: str = "lesson-duration",
) -> str:
    """Build one chunk file's text. `verified` is deliberately empty (rule 3)."""
    topics_line, basis = topics_for(lesson)
    header = [
        "---",
        "topics: %s" % topics_line,
        "topics_basis: %s" % basis,
        "depth: %s" % UNREVIEWED,
        "audience: %s" % audience,
        "written: %s" % written,
        "verified:",
        # An unstated duration is EMPTY, never 0. "Not stated" and "zero
        # minutes" are different claims, and a zero would skew any total.
        "duration_min: %s" % (
            "" if not lesson.duration_min
            else int(lesson.duration_min)
            if lesson.duration_min == int(lesson.duration_min)
            else lesson.duration_min
        ),
        "split_by: %s" % split_by,
        "source: %s" % source,
        "title: %s" % lesson.title,
        "---",
        "",
    ]
    return "\n".join(header) + lesson.body + "\n"


def inside_git_work_tree(path: str) -> str | None:
    """Return the repository root if `path` sits inside one, else None.

    Walks up looking for `.git`, which may be a directory OR a file — a worktree
    records it as a file, so an `isdir` check here would miss live work.
    """
    current = os.path.abspath(path)
    while True:
        if os.path.exists(os.path.join(current, ".git")):
            return current
        parent = os.path.dirname(current)
        if parent == current:
            return None
        current = parent


def chunk_file(
    source_path: str,
    out_dir: str,
    written: str,
    audience: str,
    allow_in_git_repo: bool = False,
    dry_run: bool = False,
) -> dict | Refusal:
    """Split one named file into per-lesson chunks under out_dir."""
    if not os.path.isfile(source_path):
        return Refusal(
            error="source_not_found",
            message="No file at %r." % source_path,
            guidance=(
                "Export the document to plain text first, then pass the path to "
                "that single file. This tool reads one named file and never "
                "searches a folder."
            ),
        )

    # A YEAR is accepted here where `verified` demands a full date: nothing
    # computes with `written`, it is displayed. Demanding precision nobody
    # has would get it invented, which is worse than coarse truth.
    if not re.match(r"^\d{4}(-\d{2}(-\d{2})?)?$", written):
        return Refusal(
            error="written_date_invalid",
            message="--written must be YYYY, YYYY-MM or YYYY-MM-DD, got %r."
            % written,
            guidance=(
                "Pass the date the SOURCE content was written, not today's "
                "date. Today's date would restate old material as new, and it "
                "would then read as CURRENT for a year."
            ),
        )

    repo_root = inside_git_work_tree(out_dir if os.path.isdir(out_dir) else os.path.dirname(os.path.abspath(out_dir)))
    if repo_root and not allow_in_git_repo:
        return Refusal(
            error="destination_inside_git_repo",
            message="%s is inside the git repository at %s." % (out_dir, repo_root),
            guidance=(
                "Choose a destination outside any repository, e.g. "
                "~/course-corpus-content/. Course content does not belong in "
                "version control here, and a .gitignore entry is a convention "
                "rather than a check. Pass --allow-in-git-repo only if you are "
                "certain the content is publishable."
            ),
        )

    try:
        with open(source_path, "r", encoding="utf-8-sig") as handle:
            text = handle.read()
    except (OSError, UnicodeDecodeError) as exc:
        return Refusal(
            error="source_unreadable",
            message="Could not read %r: %s" % (source_path, exc),
            guidance="Check the file is plain text, not .docx or .pdf.",
        )

    lessons, preamble_lines = find_lessons(text)
    split_by = "lesson-duration"
    if not lessons:
        # Fall back to the only boundary shared by every script measured. The
        # fallback is RECORDED in each chunk rather than inferred later: a
        # task-statement chunk is far coarser than a lesson, and a cross-course
        # search that hid the difference would compare unlike things.
        lessons, preamble_lines = find_task_statements(text)
        split_by = "task-statement"
    if not lessons:
        return Refusal(
            error="no_lesson_boundaries",
            message=(
                "Found no boundaries in %s. This splitter looks for a heading "
                "line ending in a parenthesised duration, then for lines "
                "beginning 'Task Statement'." % source_path
            ),
            guidance=(
                "Check the export kept its headings, or split this document by "
                "hand. Writing one chunk for the whole document would produce a "
                "file too large for a search hit to be useful."
            ),
        )

    source_name = os.path.basename(source_path)
    taken: set[str] = set()
    written_files: list[tuple[str, float]] = []

    if not dry_run:
        os.makedirs(out_dir, exist_ok=True)

    for lesson in lessons:
        slug = slugify(lesson.title, taken)
        path = os.path.join(out_dir, slug + ".md")
        if not dry_run:
            with open(path, "w", encoding="utf-8") as handle:
                handle.write(
                    render_chunk(
                        lesson, written, audience, source_name, split_by
                    )
                )
        written_files.append((path, lesson.duration_min))

    return {
        "source": source_path,
        "out_dir": out_dir,
        "lessons": len(lessons),
        "split_by": split_by,
        "total_minutes": round(sum(l.duration_min for l in lessons), 1),
        "preamble_lines_not_chunked": preamble_lines,
        "files": written_files,
        "dry_run": dry_run,
    }


def format_result(result: dict) -> str:
    lines = [
        "%s %d lesson(s) from %s"
        % ("Would write" if result["dry_run"] else "Wrote", result["lessons"],
           result["source"]),
        "Destination: %s" % result["out_dir"],
        "Split at: %s boundaries" % result["split_by"],
        "Stated duration: %s" % (
            "%s minutes total" % result["total_minutes"]
            if result["total_minutes"]
            else "not stated in this document"
        ),
    ]
    if result["preamble_lines_not_chunked"]:
        lines.append(
            "NOT chunked: %d line(s) before the first lesson heading. Front "
            "matter is reported rather than folded into lesson one."
            % result["preamble_lines_not_chunked"]
        )
    lines.append("")
    lines.append(
        "Every chunk was written with depth: %s and an EMPTY verified date."
        % UNREVIEWED
    )
    lines.append(
        "Neither was guessed. Until a human sets them, a search will find these "
        "chunks and report them as unreviewed and UNKNOWN."
    )
    return "\n".join(lines)


def format_refusal(refusal: Refusal) -> str:
    return "\n".join(
        ["Refused: %s" % refusal.error, refusal.message,
         "Next: %s" % refusal.guidance]
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="chunk.py",
        description=(
            "Split ONE exported course script into per-lesson chunks. Reads one "
            "named file; it cannot search a folder."
        ),
    )
    parser.add_argument("source", help="path to one exported plain-text script")
    parser.add_argument("out_dir", help="destination directory for the chunks")
    parser.add_argument(
        "--written",
        required=True,
        metavar="YYYY-MM-DD",
        help="date the SOURCE content was written (not today)",
    )
    parser.add_argument(
        "--audience",
        required=True,
        help="who the content was written for, e.g. 'MLA-C01 candidates'",
    )
    parser.add_argument(
        "--allow-in-git-repo",
        action="store_true",
        help="permit writing inside a git working tree (default: refuse)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="report what would be written without writing it",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    result = chunk_file(
        source_path=args.source,
        out_dir=args.out_dir,
        written=args.written,
        audience=args.audience,
        allow_in_git_repo=args.allow_in_git_repo,
        dry_run=args.dry_run,
    )
    if isinstance(result, Refusal):
        print(format_refusal(result), file=sys.stderr)
        return 2
    print(format_result(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
