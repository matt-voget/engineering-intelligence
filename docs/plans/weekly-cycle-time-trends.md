# Weekly cycle-time trends and shared report data

**Status:** Completed
**Owner:** titan
**Started:** 2026-09-01
**Approved:** 2026-09-01 by Matt Voget

## Objective

Add a reusable weekly-average chart for Build Cycle Time and make report components
consume a single snapshot-pinned report data layer so charts, summaries, and tables
cannot independently query or redefine the evidence.

## Decisions

- Bucket completed issues by the UTC Monday of their Done-transition week.
- Compute each point as the arithmetic average of In Progress-to-Done calendar days.
- Keep IBR-linked and non-IBR populations separate, matching the existing tables.
- Apply each table's Done-date range to its summary, trend chart, and visible rows.
- Render charts as inline SVG with no external dependency, from a reusable component.
- Treat deterministic cached query views as the canonical data layer; presentation
  components receive references from one assembled report model and do not re-query.

## Acceptance criteria

1. Each Build Cycle Time population displays a weekly average line chart and sample
   count per point.
2. Changing or clearing the local date filter updates the chart, summary, and table
   from the same filtered record collection.
3. Empty and single-week populations render legibly and accessibly.
4. Chart rendering is reusable for another metric/component without Jira-specific
   query logic.
5. Report assembly exposes one snapshot-pinned data model used by all team sections.
6. Tests cover weekly buckets, shared component data, filter wiring, and rendering.
7. Ruff, the full test suite, skill validation, and whitespace checks pass.
8. The existing pinned snapshot rerenders from cache and the updated report is sent.

## Checkpoints

1. **Completed 2026-09-01:** Introduced one assembled snapshot-pinned report model;
   team presentation components now receive their deterministic metrics, cycle data,
   GitHub metrics, details, work, and completion views through that model.
2. **Completed 2026-09-01:** Added a reusable inline-SVG weekly-average component and
   canonical compact cycle record payload. Each population's local Done-date filter
   now updates its chart, summary metrics, and contributor rows together.
3. **Completed 2026-09-01:** Added shared-model, canonical-payload, and chart-wiring
   regression tests and updated the weekly report contract.
4. **Completed 2026-09-01:** Verified 138 tests, Ruff, whitespace checks, skill
   validation, JavaScript parsing, zero-query cached rendering, and fourteen chart
   instances across seven teams.

## Exact next action

Deliver the updated cached-snapshot report for review; use the shared chart component
for the next requested week-over-week metric.
