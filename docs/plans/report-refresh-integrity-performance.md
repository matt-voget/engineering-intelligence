# Report refresh integrity and performance plan

**Status:** Approved — implementation in progress
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

1. **In progress:** Add regression tests for reused receipt entries and post-snapshot
   resume.
2. Implement idempotent snapshot adoption and complete receipt assembly.
3. Profile renderer call planning; remove duplicate queries and add bounded concurrent
   cache materialization.
4. Verify targeted and full tests; record timing evidence.
5. Run one fresh live refresh, render from its pinned snapshot, validate, and deliver
   the report.

## Exact next action

Inspect refresh orchestration, snapshot persistence, receipt assembly, and renderer
cache planning, then add failing regression tests before implementation.
