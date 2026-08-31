# Report refresh integrity and performance plan

**Status:** Completed
**Owner:** titan
**Started:** 2026-08-31
**Approved:** 2026-08-31 by Matt Voget

## Objective

Make interrupted incremental refreshes produce complete, trustworthy terminal receipts,
make post-snapshot resume idempotent, and reduce fresh-report latency by eliminating
duplicated and unnecessarily serial snapshot queries.

## Confirmed failures and baseline

- Run `c220f2c4-cffa-48dc-b61d-665e1e92628c` created snapshot
  `refresh-20260831T165135Z`, then was interrupted during flag evaluation. Resume tried
  to create the same named snapshot and failed instead of adopting the existing
  compatible snapshot.
- Run `0abaab11-e4b4-49c8-b9a7-f68501347478` resumed after 23/346 sources. Its durable
  task manifest finished 346/346, but its terminal receipt included only the 323 newly
  processed GitHub repositories and omitted all 23 reused source results, including
  all nine Jira scopes.
- The refresh completed at `17:30:50Z`, while fresh report materialization continued
  for roughly another 49 minutes and executed 168 cache misses. Expensive `people
  list`, `team work`, feature, and individual queries ran serially; team-work queries
  were issued twice per team.

## Acceptance criteria

1. A resumed completed receipt contains exactly one successful source result for every
   planned Jira and GitHub task, including reused tasks, with no missing or duplicate
   source keys.
2. If a compatible snapshot was created before interruption, resume adopts it and
   continues post-snapshot stages; it never fails only because the deterministic
   snapshot name already exists.
3. Snapshot adoption rejects a name collision whose stored snapshot provenance is not
   compatible with the run.
4. Report materialization never invokes the same snapshot/config-bound query twice in
   one render.
5. Independent team, feature, and individual views use bounded concurrency while
   preserving deterministic output order and atomic cache writes.
6. Refresh-materialized individual summaries are reused rather than recomputed by the
   renderer when they satisfy the same pinned contract.
7. Targeted interruption/resume, receipt-coverage, cache, concurrency, and deterministic
   rendering tests pass, followed by `ruff`, the full test suite, and `git diff --check`.
8. A fresh live run produces a completed 346/346 terminal receipt, a structurally valid
   self-contained report, and a measured refresh/render timing comparison.

## Checkpoints

1. **Completed 2026-08-31:** Added regression tests for reused receipt entries and
   post-snapshot resume.
2. **Completed 2026-08-31:** Persisted source receipt payloads and snapshot identity;
   resume now assembles complete receipts and adopts only provenance-compatible
   snapshots.
3. **Completed 2026-08-31:** Removed duplicate team-work queries and added deterministic
   three-worker materialization for independent team, feature, and individual views.
   Refresh-side individual cache materialization now uses the same bounded concurrency
   instead of serially computing all 16 people.
4. **Completed 2026-08-31:** Added an identity-only people directory path so report
   rendering does not recompute every person's complete work context before the
   individual views are materialized. Full-context refresh materialization remains
   bounded to three workers.
5. **Completed 2026-08-31:** Verified 135 tests, Ruff, whitespace checks, and the
   packaged skill validator. Fresh run `3baa0b0d-a684-4fd6-955f-af95321b10ea`
   completed all 346 sources (9 Jira and 337 GitHub) and pinned snapshot
   `375a27c1-dc08-4386-b3cc-f1cde79f67b5`.
6. **Completed 2026-08-31:** The pre-optimization fresh refresh baseline was 752.709
   seconds: source ingestion finished in 199.644 seconds and the serial individual
   finalizer consumed roughly 533 seconds. The optimized cold report render completed
   in 1,311 seconds, down from roughly 49 minutes, and a cache-only rerender completed
   in 4 seconds. The delivered self-contained artifact is 17,083,765 bytes and contains
   seven team sections, sixteen individual sections, and both evidence finders.

## Exact next action

Deliver the validated report and use the next fresh refresh to measure the new
concurrent individual-finalizer timing against the 533-second serial baseline.
