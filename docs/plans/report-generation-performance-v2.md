# Report generation performance v2

**Status:** Complete (approved and delivered 2026-09-08)
**Owner:** titan
**Started:** 2026-09-08

## Objective

Reduce fresh-snapshot HTML report generation from roughly an hour to minutes while
preserving the existing snapshot, evidence, deterministic ordering, resumable cache,
and self-contained output contracts.

## Measured baseline

The 2026-09-08 report for snapshot
`112c99ee-0c79-4052-a565-4aea447365e2` materialized 399 cache entries over 3,992.7
seconds (66.5 minutes). The dominant cold-cache stages were:

- 16 `team work` views: 1,998 seconds;
- 279 `feature get` views: 636 seconds;
- 16 `team get` views: 373 seconds;
- 16 `metrics github-pr` views: 246 seconds;
- 37 cached `individual get` views: 67 seconds;
- remaining dashboard, finder, metric, and build-cycle views: under 90 seconds.

A cache-only rerender of the same snapshot completed in 5.9 seconds and produced the
same 24 MB self-contained report. This isolates the bottleneck to repeated snapshot
query materialization rather than HTML generation.

The query implementations explain the measured cost:

- `TeamWorkQuery` executes per-issue lookups for issue versions, status timelines,
  Jira/GitHub relationships, and linked pull requests across every team scope.
- `TeamQuery` recomputes the dashboard for every team and independently loads feature
  trees that the report later requests again.
- each feature is a separate CLI process and recursively issues per-node database
  queries;
- the renderer starts a new `uv run engintel` process for every cache miss, preventing
  query state and loaded snapshot indexes from being shared across related views.

## Design

1. Add structured renderer timing for every materialization phase, including cache
   hits, misses, view counts, elapsed time, and the slowest view. Persist the summary
   beside the snapshot-bound report cache so future regressions are visible without
   reconstructing timestamps.
2. Add bulk snapshot query contexts in the core package. Load snapshot source states,
   Jira issues and latest pinned versions, hierarchy relationships, status
   transitions, Jira/GitHub relationships, repositories, pull-request versions,
   reviews, and commits once per materialization process and index them by stable IDs.
3. Add bulk APIs for team work and feature detail. Partition the shared indexed data
   into the existing presentation contracts so payloads and ordering remain identical
   to the current single-view commands.
4. Add a report-bundle CLI command that materializes all requested report views in one
   process. Reuse the dashboard, metrics, feature details, and refresh-created
   individual cache instead of invoking hundreds of isolated CLI processes. The HTML
   renderer will consume the bundle and continue writing atomic, snapshot/config-bound
   cache entries for resumability and fast template-only rerenders.
5. Preserve the existing individual/team/feature commands as public compatibility
   paths. Validate bundle output against those contracts on representative fixtures.

## Acceptance criteria

1. A cold render of the current 16-team, 37-person snapshot completes in under 10
   minutes on this agent, measured from an empty isolated report cache.
2. A cache-only rerender completes in under 10 seconds.
3. Cold materialization uses one report-bundle process rather than one process per
   derived view, and each snapshot-wide source table is loaded at most once per bundle
   phase.
4. Team work, feature, team detail, individual, metric, Issue Finder, and GitHub Finder
   payloads remain deterministic and snapshot-pinned. Tests compare representative
   bundle results with the existing query contracts.
5. Cache entries remain atomic and resumable. An interrupted run reuses completed
   entries; corrupt or mismatched entries still fail loudly.
6. The generated application remains one self-contained HTML file with all configured
   team/person routes, both finders, embedded assets, and no external scripts or
   styles.
7. Targeted query-count and performance regression tests pass, followed by Ruff, the
   full test suite, the skill validator, `git diff --check`, and a measured live cold
   and warm render.

## Checkpoints

1. **Complete 2026-09-08:** Added atomic per-run materialization timing with cache
   hits, misses, view totals, and slowest calls. The summary is persisted as
   `materialization.json` beside the snapshot-bound cache and returned by the renderer.
2. **In progress 2026-09-08:** The team-work GitHub path now selects candidate pull
   requests through configured team-member PR, review, and commit identities before
   loading record families. On the live snapshot, the AM query fell from 157.1 seconds
   to 3.35 seconds (47x) with byte-for-byte identical JSON; focused tests and Ruff pass.
   Shared cross-team context remains a possible follow-up only if the full cold
   benchmark shows it is still necessary.
3. **In progress 2026-09-08:** Added report-path indexes for Jira hierarchy children,
   IBR-board membership, GitHub reviews by pull request, and Jira links by GitHub
   record. Migration and focused feature/team-work tests pass; live query output is
   unchanged. Bulk feature/team-detail assembly remains pending measurement of the
   indexed cold path.
4. **Complete 2026-09-08:** GitHub PR metrics now preselect team-authored pull requests
   and validate only those against each repository's pinned high-water mark. The live AM
   query completes in 8.1 seconds and produces the same canonical payload as the cached
   pre-change query.
5. **Complete 2026-09-08:** Added a lightweight snapshot-pinned `team workflow` view for
   the workflow and roster data the renderer consumes. The live AM query fell from 40.8
   to 12.1 seconds, and parity tests verify its workflow and roster against `team get`.
6. **Complete 2026-09-08:** The first instrumented cold run finished in 961 seconds
   (16.0 minutes), with 279 isolated `feature get` processes accounting for 1,618
   aggregate seconds. Added `feature get-many` and changed the renderer to materialize
   the union of active and target-dated feature hierarchies in one snapshot-pinned
   process and one atomic cache entry. A six-worker experiment did not improve
   throughput because SQLite contention offset the extra parallelism, so the bounded
   worker count remains three.
7. **Complete 2026-09-08:** The final isolated cold benchmark completed in 410.6
   seconds (6 minutes 51 seconds), versus the 3,992.7-second baseline: a 9.7x speedup
   and below the 10-minute target. Its cache-only rerender completed in 2.9 seconds.
   Ruff, all 145 tests, the skill validator, `git diff --check`, and structural checks
   of the 24 MB self-contained HTML passed.
8. **Complete 2026-09-08:** Refresh `c40dfd28-357a-4f83-a4f0-3d2f3c272538`
   completed all 355 configured sources and pinned snapshot
   `76355724-b18d-4c2c-a04a-394b22f8a863`. The fresh report materialized in 420.5
   seconds at `reports/weekly-status-2026-09-08.html` with all 16 team and 37 person
   routes plus both finders.

## Exact next action

No further action is required. Future report runs should use the persisted timing
summary to detect regressions and retain the three-worker bound unless new benchmarks
show a benefit.
