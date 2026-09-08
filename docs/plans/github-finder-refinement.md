# GitHub Finder refinement

## Goal

Make the GitHub Finder consistent with the Issue Finder while preserving snapshot-pinned attribution and metrics.

## Acceptance criteria

- Every categorical GitHub filter is a multi-select with All/None actions and visible selected values.
- The Team filter attributes a row only through the author's configured GitHub username.
- Jira and GitHub finders receive the same full configured team list, including teams with no matching rows.
- Pull requests expose coding time from the earliest pinned linked commit to PR creation.
- Coding, pickup, and review charts are stacked vertically and show the latest weekly average and trend.
- One top-level outlier checkbox controls chart outliers and an expandable linked detail table explains them.
- Free-text filtering remains button/Enter triggered.
- Query, renderer, lint, full tests, diff checks, and skill validation pass.

## Checkpoints

1. Extend the deterministic GitHub Finder contract and snapshot-safe query with coding time.
2. Unify team inputs and rebuild GitHub filter controls.
3. Add the coding column, stacked charts, KPI summaries, and shared outlier details.
4. Update focused tests, run the full validation suite, generate the pinned report, then commit and push to `main`.
