# Weekly GitHub PR metric trends

**Status:** Completed
**Owner:** titan
**Started:** 2026-09-01
**Approved:** 2026-09-01 by Matt Voget

## Objective

Add weekly-average pickup-time and review-time charts to every team page using the
same canonical PR contributions and merge-date filters as their metric tables.

## Decisions

- Bucket qualifying merged PRs by the UTC Monday of their merge week.
- Keep pickup and review populations separate, matching their existing metric tables.
- Show arithmetic average hours and sample size at every chart point.
- Generalize the existing weekly-average SVG component to accept units and axis labels.
- Reuse the snapshot-pinned GitHub PR metric view; do not add queries or derived data
  sources.

## Acceptance criteria

1. Every team has pickup-time and review-time weekly charts.
2. Local merge-date filters update chart, summary, and contributor rows together.
3. Charts and tables consume one canonical compact PR record payload per metric.
4. The generic chart supports both cycle days and PR hours without metric-specific
   aggregation logic.
5. Empty and single-week data render accessibly.
6. Tests, Ruff, skill validation, whitespace checks, JavaScript parsing, and a cached
   report rerender pass.

## Checkpoints

1. **Completed 2026-09-01:** Generalized the shared weekly-average chart for metric
   units, short labels, axis labels, and record names. Added one canonical compact PR
   payload per pickup/review population.
2. **Completed 2026-09-01:** Wired each local merge-date filter to update the PR chart,
   average, sample count, and contributor rows from the same filtered records. Added
   payload and wiring regression tests and updated the report contract.
3. **Completed 2026-09-01:** Verified 140 tests, Ruff, whitespace checks, skill
   validation, JavaScript parsing, and a zero-query cached render containing fourteen
   PR charts plus fourteen cycle-time charts across seven teams.

## Exact next action

Deliver the updated pinned-snapshot report and reuse the generic weekly chart for the
next requested metric trend.
