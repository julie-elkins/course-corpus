---
topics: osmosis
depth: walkthrough
---

The header above is missing `audience` and `written`, so this chunk cannot be
indexed and is reported with the reason rather than silently dropped.

A broken header and a missing header need different human action: this one was
annotated and got it wrong, which is a correction. `no-header-yet.md` was never
annotated at all, which is backfill. Collapsing the two loses that difference.
