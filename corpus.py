#!/usr/bin/env python3
"""Search a course corpus for what you have already written.

A corpus here is a directory of plain-text content chunks, each opening with a
short header block. This module answers one question against it: what have I
already written about this topic, at what depth, for whom, and has anyone checked
it recently?

Design rules, in force throughout this module:

1. READ-ONLY. Nothing here writes to, moves or renames a file in the corpus.
   Backfilling header blocks is a human pass and is deliberately not automated:
   the depth and the verification date are judgements, and a tool that guessed
   them would manufacture exactly the false confidence this module exists to
   prevent.
2. An ABSENT verification date reports UNKNOWN and NEVER CURRENT. A system that
   reads "nobody has checked this" as "this is fine" returns a clean verdict for
   content nobody has looked at, which is worse than carrying no dates at all,
   because the failure is silent and it flatters.
3. Every result states what it could NOT see. An empty hit list and an unreadable
   corpus are indistinguishable without that count, and the mistake always runs
   in the reassuring direction.
4. Verification is NEVER inferred from filesystem mtime. A touched file is not a
   checked file, and one bulk copy would otherwise mark an entire corpus fresh.
5. Stdlib only, and targeting Python 3.9 so it runs on an untouched macOS system
   interpreter. The audience is teachers and course builders, not developers; a
   dependency install is a wall for most of them.
6. The staleness threshold is a PARAMETER, not a constant. How fast content goes
   wrong depends entirely on the subject, so a single baked-in figure would be
   right for one corpus and wrong for every other.
7. A refusal is a returned result carrying guidance, not a traceback. The caller
   may be a person or another program; either way it should be told the cheapest
   correction rather than handed a stack.
"""

from __future__ import annotations

import argparse
import datetime as dt
import os
import re
import sys
from dataclasses import dataclass, field

# FIELD CONVENTION (this repository defines it; see README). Three levels, not
# five. A finer scale makes the author stop and deliberate, and anything that
# makes the author stop does not get filled in — which costs the whole index.
DEPTHS = ("aware", "walkthrough", "practice")

# Fields a header must carry to count as indexed. `verified` is deliberately NOT
# required: its absence is meaningful (rule 2) and demanding it would push
# authors into inventing a date to satisfy the parser.
REQUIRED_FIELDS = ("topics", "depth", "audience", "written")

# Extensions read as text. Anything else is counted and named as unsupported
# rather than silently skipped (rule 3).
TEXT_SUFFIXES = (".md", ".markdown", ".txt")

# MODELLING ASSUMPTION: one year. Long enough that a stable subject is not
# flagged on every line, short enough to catch a product that ships quarterly.
# To be wrong, a corpus would have to go stale substantially faster or slower
# than annually — which most do, hence --stale-after.
DEFAULT_STALE_AFTER_DAYS = 365

_FENCE = re.compile(r"^---\s*$")
_FIELD = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*)\s*:\s*(.*)$")

CURRENT = "CURRENT"
STALE = "STALE"
UNKNOWN = "UNKNOWN"


@dataclass(frozen=True)
class Chunk:
    """One indexed content chunk. Frozen: a record two readers share."""

    path: str
    topics: tuple[str, ...]
    depth: str
    audience: str
    written: str
    verified: str | None
    body: str


@dataclass(frozen=True)
class Problem:
    """A file the index could not use, and the reason it could not."""

    path: str
    reason: str


@dataclass(frozen=True)
class Inventory:
    """What the corpus contains, including what could not be read."""

    root: str
    chunks: tuple[Chunk, ...]
    problems: tuple[Problem, ...]
    suffix_counts: dict[str, int] = field(default_factory=dict)

    @property
    def files_seen(self) -> int:
        return len(self.chunks) + len(self.problems)

    @property
    def unindexed(self) -> int:
        """Files present in the corpus and invisible to any query."""
        return len(self.problems)


@dataclass(frozen=True)
class Hit:
    """One matching chunk, with the verdict that decides whether to reuse it."""

    chunk: Chunk
    currency: str
    matched_topics: tuple[str, ...]


@dataclass(frozen=True)
class SearchResult:
    """Hits, plus the coverage statement that stops an empty result lying."""

    query: str
    hits: tuple[Hit, ...]
    unindexed: int
    files_seen: int
    stale_after_days: int


@dataclass(frozen=True)
class Refusal:
    """A refusal. `guidance` names the cheapest correction, not the rule."""

    error: str
    message: str
    guidance: str


def parse_header(text: str) -> tuple[dict[str, str] | None, str, str | None]:
    """Split a chunk into its header fields and body.

    Returns (fields, body, problem). `fields` is None when there is no header
    block at all; `problem` is set when a header exists but cannot be used. The
    three outcomes are kept distinct ON PURPOSE: "no header", "broken header"
    and "usable header" need different human action, and collapsing the first
    two loses the distinction between content nobody has annotated yet and
    content somebody annotated wrongly.
    """
    lines = text.splitlines()
    if not lines or not _FENCE.match(lines[0]):
        return None, text, None

    fields: dict[str, str] = {}
    for index, line in enumerate(lines[1:], start=1):
        if _FENCE.match(line):
            body = "\n".join(lines[index + 1 :])
            missing = [f for f in REQUIRED_FIELDS if not fields.get(f)]
            if missing:
                return None, body, "header missing fields: " + ", ".join(missing)
            return fields, body, None
        if not line.strip():
            continue
        match = _FIELD.match(line)
        if match:
            fields[match.group(1).lower()] = match.group(2).strip()

    return None, text, "header block opened but never closed"


def load_chunk(path: str, text: str) -> Chunk | Problem:
    """Build a Chunk from file text, or report why it is unusable."""
    fields, body, problem = parse_header(text)
    if problem is not None:
        return Problem(path=path, reason=problem)
    if fields is None:
        return Problem(path=path, reason="no header block")

    depth = fields["depth"].lower()
    if depth not in DEPTHS:
        return Problem(
            path=path,
            reason="depth %r is not one of %s" % (depth, ", ".join(DEPTHS)),
        )

    topics = tuple(t.strip() for t in fields["topics"].split(",") if t.strip())
    if not topics:
        return Problem(path=path, reason="topics field is empty")

    # An empty `verified:` and an absent one mean the same thing, and that thing
    # is UNKNOWN. Normalising both to None here is what makes rule 2 hold at
    # every later call site rather than at each one separately.
    verified = fields.get("verified", "").strip() or None

    return Chunk(
        path=path,
        topics=topics,
        depth=depth,
        audience=fields["audience"],
        written=fields["written"],
        verified=verified,
        body=body,
    )


def build_inventory(root: str) -> Inventory | Refusal:
    """Walk a corpus directory and index every text file in it."""
    if not os.path.isdir(root):
        return Refusal(
            error="corpus_not_found",
            message="No directory at %r." % root,
            guidance=(
                "Pass the path to the directory holding your content files, "
                "e.g. corpus.py inventory ./sample-corpus"
            ),
        )

    chunks: list[Chunk] = []
    problems: list[Problem] = []
    suffix_counts: dict[str, int] = {}

    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if not d.startswith(".")]
        for name in sorted(filenames):
            if name.startswith("."):
                continue
            path = os.path.join(dirpath, name)
            suffix = os.path.splitext(name)[1].lower() or "(none)"
            suffix_counts[suffix] = suffix_counts.get(suffix, 0) + 1

            if suffix not in TEXT_SUFFIXES:
                problems.append(
                    Problem(path=path, reason="unsupported file type %s" % suffix)
                )
                continue
            try:
                with open(path, "r", encoding="utf-8") as handle:
                    text = handle.read()
            except (OSError, UnicodeDecodeError) as exc:
                problems.append(Problem(path=path, reason="unreadable: %s" % exc))
                continue

            loaded = load_chunk(path, text)
            if isinstance(loaded, Problem):
                problems.append(loaded)
            else:
                chunks.append(loaded)

    return Inventory(
        root=root,
        chunks=tuple(chunks),
        problems=tuple(problems),
        suffix_counts=suffix_counts,
    )


def currency(
    verified: str | None, today: dt.date, stale_after_days: int
) -> str:
    """Return CURRENT, STALE or UNKNOWN for one verification date.

    UNKNOWN covers both "no date given" and "date unparseable". Neither is
    evidence of freshness, and rule 2 forbids resolving either toward CURRENT.
    """
    if not verified:
        return UNKNOWN
    try:
        checked = dt.date.fromisoformat(verified)
    except ValueError:
        return UNKNOWN
    if checked > today:
        # A future date is a typo, not a stronger guarantee. Reading it as
        # CURRENT would make the single most careless entry the most trusted.
        return UNKNOWN
    return CURRENT if (today - checked).days <= stale_after_days else STALE


def search(
    inventory: Inventory,
    query: str,
    today: dt.date,
    stale_after_days: int = DEFAULT_STALE_AFTER_DAYS,
    depth: str | None = None,
) -> SearchResult:
    """Find chunks matching a topic, optionally filtered to one depth."""
    needle = query.strip().lower()
    hits: list[Hit] = []

    for chunk in inventory.chunks:
        if depth is not None and chunk.depth != depth:
            continue
        matched = tuple(t for t in chunk.topics if needle in t.lower())
        if not matched and needle not in chunk.body.lower():
            continue
        hits.append(
            Hit(
                chunk=chunk,
                currency=currency(chunk.verified, today, stale_after_days),
                matched_topics=matched,
            )
        )

    # Topic matches first, then shallowest depth: a hit whose header names the
    # topic is about it, where a body match may only mention it in passing.
    hits.sort(key=lambda h: (not h.matched_topics, DEPTHS.index(h.chunk.depth)))

    return SearchResult(
        query=query,
        hits=tuple(hits),
        unindexed=inventory.unindexed,
        files_seen=inventory.files_seen,
        stale_after_days=stale_after_days,
    )


def format_inventory(inv: Inventory) -> str:
    lines = [
        "Corpus: %s" % inv.root,
        "Files seen:   %d" % inv.files_seen,
        "Indexed:      %d" % len(inv.chunks),
        "NOT indexed:  %d  (invisible to every query)" % inv.unindexed,
        "",
    ]
    if inv.suffix_counts:
        lines.append("By file type:")
        for suffix, count in sorted(inv.suffix_counts.items()):
            lines.append("  %-12s %d" % (suffix, count))
        lines.append("")
    if inv.chunks:
        by_depth: dict[str, int] = {}
        for chunk in inv.chunks:
            by_depth[chunk.depth] = by_depth.get(chunk.depth, 0) + 1
        lines.append("Indexed by depth:")
        for level in DEPTHS:
            lines.append("  %-12s %d" % (level, by_depth.get(level, 0)))
        lines.append("")
    if inv.problems:
        lines.append("Not indexed, with reasons:")
        for problem in inv.problems:
            lines.append("  %s" % problem.path)
            lines.append("      %s" % problem.reason)
    else:
        lines.append("Every file in the corpus is indexed.")
    return "\n".join(lines)


def format_search(result: SearchResult) -> str:
    lines = []
    if not result.hits:
        lines.append(
            "No chunk matches %r. Searched %d indexed files."
            % (result.query, result.files_seen - result.unindexed)
        )
    else:
        lines.append(
            "%d chunk(s) match %r:" % (len(result.hits), result.query)
        )
        lines.append("")
        for hit in result.hits:
            chunk = hit.chunk
            where = "topic" if hit.matched_topics else "body only"
            lines.append("  %s" % chunk.path)
            lines.append(
                "      depth %-11s audience: %s" % (chunk.depth, chunk.audience)
            )
            lines.append(
                "      written %s   verified %s   %s"
                % (chunk.written, chunk.verified or "never", hit.currency)
            )
            lines.append("      matched on %s" % where)
            lines.append("")

    # Rule 3. This line is printed on EVERY result, including a successful one:
    # the number only protects the reader if they never have to ask for it.
    lines.append(
        "Coverage: %d of %d files indexed; %d invisible to this query."
        % (
            result.files_seen - result.unindexed,
            result.files_seen,
            result.unindexed,
        )
    )
    if result.unindexed:
        lines.append(
            "         Run `inventory` to see which files and why. An empty or "
            "thin result may mean content exists that this cannot see."
        )
    lines.append(
        "Currency: STALE means verified over %d days ago. UNKNOWN means nobody "
        "has recorded a check — it is NOT a clean bill of health."
        % result.stale_after_days
    )
    return "\n".join(lines)


def format_refusal(refusal: Refusal) -> str:
    return "\n".join(
        [
            "Refused: %s" % refusal.error,
            refusal.message,
            "Next: %s" % refusal.guidance,
        ]
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="corpus.py",
        description="Search a course corpus for content you have already written.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    inv = sub.add_parser(
        "inventory", help="count what is in the corpus and what cannot be read"
    )
    inv.add_argument("corpus", help="path to the corpus directory")

    find = sub.add_parser("search", help="find chunks about a topic")
    find.add_argument("corpus", help="path to the corpus directory")
    find.add_argument("query", help="topic to search for")
    find.add_argument(
        "--depth",
        choices=DEPTHS,
        default=None,
        help="only return chunks at this depth",
    )
    find.add_argument(
        "--stale-after",
        type=int,
        default=DEFAULT_STALE_AFTER_DAYS,
        metavar="DAYS",
        help=(
            "days after which a verified chunk reports STALE "
            "(default: %d)" % DEFAULT_STALE_AFTER_DAYS
        ),
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    inventory = build_inventory(args.corpus)
    if isinstance(inventory, Refusal):
        print(format_refusal(inventory), file=sys.stderr)
        return 2

    if args.command == "inventory":
        print(format_inventory(inventory))
        return 0

    result = search(
        inventory,
        args.query,
        today=dt.date.today(),
        stale_after_days=args.stale_after,
        depth=args.depth,
    )
    print(format_search(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
