# GitHub Finder execution plan

**Status:** Paused — refresh redesign takes priority
**Owner:** titan
**Started:** 2026-08-24

## Objective

Replace the report's GitHub Finder placeholder with a snapshot-safe, organization-wide
table of the ingested pull requests and their commits. Users can combine filters, sort
the table, and hide, restore, or reorder columns without changing underlying evidence.

GitHub Issues are explicitly out of scope. Repository configuration defines collection
scope but does not assign records to teams.

## Acceptance criteria

- A deterministic query and CLI presentation return every configured repository's pull
  requests and associated commits pinned by the selected snapshot, with stable keys and
  no duplicate rows.
- Pull-request rows expose repository, number, title, state/draft status, author,
  created/updated/closed/merged timestamps, branches, commit count, review count,
  reviewers, and linked Jira evidence where available.
- Commit rows expose repository, SHA, message, author, authored/committed timestamps,
  linked pull requests, and linked Jira evidence where available.
- `#/github-finder` provides record-type, text, repository, state, author, reviewer,
  linked-Jira, and local date filters plus a visible result count and sortable columns.
- Users can hide, restore, and reorder GitHub Finder columns independently of the Jira
  finder. These actions affect presentation only.
- The report remains one self-contained HTML file with working local hash routes and
  clickable GitHub/Jira evidence.
- Query, CLI, renderer, and contract tests pass, followed by Ruff, the full test suite,
  skill validation, and `git diff --check`.
- A fresh completed refresh supplies the final report snapshot; the regenerated report
  is structurally validated and delivered to Matt.

## Checkpoints

- [x] Confirm scope and inspect the current ingestion/query/report architecture.
  - Decision: GitHub Issues are out of scope; include pull requests and commits.
  - Finding: commits are the immutable commits associated with ingested pull requests,
    not an independent default-branch commit crawl.
- [x] Add the snapshot-safe GitHub Finder presentation, query, CLI, and tests.
  - Evidence: the live pinned snapshot returns 26,651 stable records: 6,900 pull
    requests and 19,751 associated commits across 331 repositories.
- [x] Replace the HTML placeholder with filtered, sortable PR/commit tables and reusable
  independent column management; update report tests and contract.
  - The full dataset is embedded compactly and filtered client-side; only the current
    100-row page is materialized in the DOM.
- [x] Run targeted and full verification, then commit and push the implementation.
  - Evidence: Ruff passes, all 120 tests pass, skill validation passes, JavaScript
    syntax validation passes, and `git diff --check` passes.
- [ ] Run a fresh complete refresh, regenerate and validate the report, and deliver it.

## Exact next action

Resume report generation after the incremental refresh v2 design is approved and built.
