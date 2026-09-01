# Finder weekly metric trends

**Status:** Completed
**Owner:** titan
**Started:** 2026-09-01
**Approved:** 2026-09-01 by Matt Voget

## Objective

Add filter-aware weekly Build Cycle Time to Issue Finder and weekly PR pickup/review
time to GitHub Finder, using each finder's canonical records after all active filters.

## Decisions

- Issue Finder charts include only issues with a completed In Progress-to-Done cycle
  and bucket by the UTC Monday of the Done transition.
- GitHub Finder charts include qualifying pull-request records with a merge timestamp
  and the corresponding pickup/review measurement, bucketed by merge week.
- Every finder facet—including text, dates, teams, status, classification, attention,
  repository, authors/reviewers, linked Jira, and record type—affects its charts.
- Pagination, sorting, and visible-column choices do not change the filtered population.
- Extend the canonical team-work cycle contract with start/end boundaries. Use a
  query-family cache revision so only affected team-work views rematerialize.

## Acceptance criteria

1. Issue Finder shows a weekly average completed Build Cycle Time chart.
2. GitHub Finder shows weekly average PR pickup and review charts.
3. Chart sample sizes and averages reconcile to all filtered rows, not only the page.
4. Missing/running cycle measurements and ineligible GitHub records are excluded.
5. All charts reuse the generic weekly-average component and canonical finder data.
6. Tests cover cycle boundaries, cache revisioning, finder payloads, and filter events.
7. Ruff, full tests, skill validation, JavaScript parsing, and cached rendering pass.

## Checkpoints

1. **Completed 2026-09-01:** Extended deterministic workflow-cycle metrics and team
   work records with In Progress start and Done end boundaries. Added query-family
   cache revisioning so only the seven affected team-work views rematerialized.
2. **Completed 2026-09-01:** Added the Issue Finder completed-cycle chart and GitHub
   Finder pickup/review charts. Issue row-class observation covers facets, attention,
   text, and local dates; GitHub charts consume the full post-filter collection before
   paging and sorting presentation.
3. **Completed 2026-09-01:** Added cycle-boundary, cache-revision, finder-payload, and
   filter-wiring tests and updated the report contract.
4. **Completed 2026-09-01:** Verified 143 tests, Ruff, whitespace checks, skill
   validation, JavaScript parsing, and a self-contained 17,354,041-byte render with
   156 cache hits, seven intentional misses, and thirty-one weekly charts total.

## Exact next action

Deliver the updated pinned-snapshot report; subsequent rerenders reuse all newly
materialized cycle-boundary views.
