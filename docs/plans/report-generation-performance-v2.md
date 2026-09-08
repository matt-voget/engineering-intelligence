# Report generation performance v2

**Status:** Awaiting approval
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

1. Add timing/query-count instrumentation and representative parity fixtures.
2. Implement and test the shared snapshot query context and bulk team-work path.
3. Implement and test bulk feature/team-detail assembly and individual-cache reuse.
4. Add the report-bundle CLI and migrate the renderer while retaining atomic cache
   semantics.
5. Run full verification and live cold/warm benchmarks; tune only from observed data.
6. Update this plan with measured results, commit and push the completed work, and
   generate a fresh report through the optimized path.

## Exact next action

After approval, implement checkpoint 1 and commit its tests and instrumentation before
changing query behavior.
