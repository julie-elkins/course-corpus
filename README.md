# course-corpus

Search what you have already taught, before you write it again.

A repeat course builder's bottleneck is not drafting. It is not knowing what they
already have. This is a small read-only tool that answers one question against a
directory of your own content: **what have I already written about this topic, at
what depth, for whom, and has anyone checked it recently?**

Stdlib Python, no dependencies, runs on the system interpreter that came with your
machine. It never writes to your content.

## Why depth and dates, and not just search

Two things separate an index you use from an archive you ignore, and both are
cheap to get wrong.

**A search that returns mentions is not useful.** Forty equally weighted hits for
one topic is not an answer. What you need back is how many times you have taught
it and at what depth, because depth is what decides whether a passage is reusable
for the course in front of you. The same topic taught at three depths produces
three unrelated pieces of text: a sentence that names it, a walkthrough that shows
how it works, and a task that makes the learner do it.

**A reuse index without a last-verified date is a liability.** It makes old content
easy to republish, including content that was true when it was written. A passage
three years out of date reads exactly as confidently as one from last week. Build
the search without the date and the main thing you have built is a faster way to
spread confident wrong answers.

So a missing verification date reports `UNKNOWN`, never `CURRENT`. A tool that
reads "nobody has checked this" as "this is fine" returns a clean verdict about
content nobody has looked at, and that failure is silent and flattering.

## The convention

One file per teachable chunk — one explanation, one worked example, one task.
Not one file per course: what gets reused is the chunk, and a search hit that lands
you in a 40-page file and leaves you scrolling has not helped.

Each file opens with a header block of five fields:

```
---
topics: photosynthesis, energy transfer
depth: aware
audience: Y7 introductory
written: 2026-02-10
verified: 2026-09-30
---

Plants make their own food...
```

`depth` is one of three values. Three rather than five on purpose: a finer scale
makes the author stop and deliberate, and anything that makes the author stop does
not get filled in.

- `aware` — names it so the learner knows it exists
- `walkthrough` — shows the learner how it works
- `practice` — makes the learner do it

`topics` is comma-separated, in whatever vocabulary you already use. Do not invent
a taxonomy; an invented one is a second thing to learn and it will not survive your
own use.

`verified` is the date a human last confirmed the content still reflects reality.
**Leave it empty if nobody has.** An honest empty field is the point — it is not a
gap to be filled in with a guess.

## Use

```
python3 corpus.py inventory ./sample-corpus
python3 corpus.py search ./sample-corpus photosynthesis
python3 corpus.py search ./sample-corpus caching --depth walkthrough
python3 corpus.py search ./sample-corpus caching --stale-after 180
```

Both examples below are recorded runs against `sample-corpus/` in this repository,
not illustrations.

```
$ python3 corpus.py inventory ./sample-corpus
Corpus: ./sample-corpus
Files seen:   7
Indexed:      5
NOT indexed:  2  (invisible to every query)

By file type:
  .md          7

Indexed by depth:
  aware        2
  walkthrough  2
  practice     1

Not indexed, with reasons:
  ./sample-corpus/broken-header.md
      header missing fields: audience, written
  ./sample-corpus/no-header-yet.md
      no header block
```

```
$ python3 corpus.py search ./sample-corpus caching
2 chunk(s) match 'caching':

  ./sample-corpus/http-caching-why-it-matters.md
      depth aware       audience: non-technical stakeholders
      written 2026-01-20   verified 2026-08-15   CURRENT

  ./sample-corpus/http-caching-headers.md
      depth walkthrough audience: junior web developers
      written 2022-06-14   verified 2022-07-01   STALE

Coverage: 5 of 7 files indexed; 2 invisible to this query.
Currency: STALE means verified over 365 days ago. UNKNOWN means nobody has
recorded a check — it is NOT a clean bill of health.
```

That coverage line prints on every result, including successful ones and including
empty ones. An empty hit list and an unreadable corpus are indistinguishable
without it, and the mistake always runs in the reassuring direction.

## Splitting an existing course into chunks

`chunk.py` turns one exported course script into one file per lesson. It reads
**one named file** and has no way to search a folder — a sweep over a documents
directory eventually indexes whatever else is in that directory, and that failure
is silent, because a correctly indexed file looks identical to one that should
never have been read.

```
python3 chunk.py ./my-course.txt ~/course-content/my-course \
  --written 2025-11-18 --audience "MLA-C01 candidates"
```

It splits at lesson headings — a line ending in a parenthesised duration, like
`Walkthrough example (5 minutes)` — and writes `duration_min`, `source` and
`title` alongside the five core fields.

Three things it refuses to do, each because the alternative is a tool that lies:

- **It never guesses `depth`.** Every chunk is written `depth: unreviewed`, which
  indexes and searches but reports `depth NOT SET BY A HUMAN` on every hit, and is
  counted on every result. Inferring depth from wording would produce a corpus
  that looks fully annotated and is not.
- **It never writes a `verified` date.** Nobody has checked the content at the
  moment it is split.
- **It will not default `--written` to today.** Today is when the split ran, not
  when the content was written. Defaulting would restate old material as new, and
  it would then read as `CURRENT` for a year.

It also **refuses to write into a git working tree** unless you pass
`--allow-in-git-repo`. Course content does not belong in a repository, and a
`.gitignore` entry is a convention where this is a check.

## Where topics come from, and why not from the title

Topics are extracted from each lesson's **body** by a general scan, and the title
is only a fallback. That ordering was forced by a measurement.

Titles were tried first and were nearly useless. On a 62-lesson course, **57 of 62
titles were structural codes** — `Domain 2 introduction`, `TS 4.3 part 2`,
`Module 1: Course introduction` — which restate to topics like `domain` and
`module`. Only 5 named their subject. A broad search then matched 56 of 62 chunks
on body text alone: the forty-equally-weighted-hits problem this tool exists to
solve, reproduced inside the tool.

Extracting from the body instead took that from **5 usable topic lines to 58 of
62**, with 4 still falling back to the title.

The scan matches by **shape, not by a word list** — a hand-built list of product
names misses whole families, misses every capitalised variant, and goes stale the
moment something is renamed. Four patterns: `Amazon X` / `AWS X`, CamelCase in one
token (`SageMaker`, `DynamoDB`), short all-caps with optional digits (`IAM`, `S3`),
and hyphenated technical terms. Candidates are frequency-ranked, because a lesson
that names something eleven times is about it and one that names it once is not,
and overlapping matches are collapsed so `VPC` and `Amazon VPC` do not spend two
of six slots on one topic.

Each chunk records **`topics_basis`** — `extracted-from-body` or
`restated-from-title` — and a search flags the second kind, because on a corpus of
coded titles those topics are worthless and the two are indistinguishable once
written into the same field.

Extraction is allowed where depth inference is not, and the distinction is the
point: extraction reports what the text says and fails in the harmless direction,
costing a noisy search hit. An inferred depth would fail in the flattering one,
producing a corpus that looks fully classified by nobody.

**It is still not finished after splitting.** Depth has to be set by a person,
once per chunk, and the scan picks up some adjectives (`cost-efficient`,
`real-time`) alongside real subjects. The tool is built to make that state visible
rather than to pretend the work has been done.

## Setting depth, once a person has judged it

Splitting leaves every chunk `depth: unreviewed`. `annotate.py` lets a human set
those without opening every file:

```
python3 annotate.py worksheet ~/course-content/my-course depths.tsv
# fill in the new_depth column
python3 annotate.py apply ~/course-content/my-course depths.tsv
```

The worksheet carries each chunk's title, duration, extracted topics and
`topics_basis` as context, and **the `new_depth` column is emitted blank.** It is
not pre-filled with a suggestion, and that is deliberate rather than lazy: a
candidate value offered alongside a judgement is not neutral. In a separate
measurement on a different tool, showing a reviewer a menu of candidates moved
agreement with those candidates from 49% to 92% — the menu was doing the deciding.
Pre-filling a depth would produce a corpus of values a person clicked past, which
is the same corpus an inferred depth would produce, arrived at more slowly.

Four things it will not do:

- **A blank row is left alone, never defaulted.** An unmade judgement stays
  visibly unmade; a default would classify every row you skipped.
- **It never touches `verified`.** Classifying a chunk is not the same act as
  checking it, and a tool that set both would let one pass for the other.
- **It edits the header only.** A body line that happens to begin `depth:` is
  narration, not metadata.
- **It reports what it did not do** — rows left blank, slugs not found, values
  refused — counted and printed even when zero, so a partial run cannot read as a
  complete one.

The writer lives in its own module so that `corpus.py` keeps having **no write
surface at all**, which is asserted by a test in each of two files. That
invariant is worth more than the convenience of one fewer file.

## Design rules

1. **Read-only.** Nothing writes to, moves or renames a file in your corpus.
   Backfilling headers is a human pass and is deliberately not automated: depth and
   verification are judgements, and a tool that guessed them would manufacture the
   false confidence this exists to prevent.
2. **An absent verification date is `UNKNOWN`, never `CURRENT`.** A future date is
   also `UNKNOWN` — it is a typo, and reading it as current would make the most
   careless entry the most trusted.
3. **Every result states what it could not see.**
4. **Verification is never inferred from file modification time.** A touched file
   is not a checked file, and one bulk copy would mark an entire corpus fresh.
5. **Stdlib only, Python 3.9+.** The audience is teachers and course builders, not
   developers; a dependency install is a wall for most of them.
6. **The staleness threshold is a parameter.** How fast content goes wrong depends
   entirely on the subject, so a single baked-in figure would be right for one
   corpus and wrong for every other. The 365-day default is a stated assumption,
   not a measurement.

## Three outcomes, kept apart

A file with a usable header is indexed. A file with no header is backfill. A file
with a broken header is a correction. The second and third need different human
action, so they are reported separately rather than both counted as "skipped".

## Tests

```
python3 -m unittest -v test_corpus
```

29 tests, no dependencies. Two test classes assert an **absence** — that the module
has no write surface, and that it never reads modification times. Both would pass
trivially today and go on passing after the forbidden thing arrived, so each was
checked by adding the thing and confirming the test fails. Three such probes were
run and all three were caught: a missing date reading as `CURRENT`, an added
write-mode `open`, and an added `os.stat` call.

## What this does not do

- It does not help with a first course. Nothing is in the corpus yet, and the
  convention is pure overhead until there is.
- It does not extract text from Word documents, slide decks or PDFs. They are
  counted and named as unsupported, never silently skipped.
- It does not replace review. It reports that nobody has checked a passage since a
  date. It cannot tell you whether the passage is correct, and a `STALE` flag on
  something still accurate is a false alarm you clear by hand.
- It does not make content good. It makes content you already had findable, which
  is a smaller and different claim.

## One warning about what you put in it

This tool exists to resurface old text for reuse, which is also the mechanism by
which material escapes that was never meant to be published. Keep unpublished
assessment content out of a corpus — exam items, answer keys, question banks. Teaching
content and assessment content have different disclosure rules, and an index does not
know the difference.

## Licence

MIT.
