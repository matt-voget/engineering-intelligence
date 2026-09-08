---
name: engineering-intelligence-report-analysis
description: Analyze an Engineering Intelligence HTML report or answer questions about its Jira/GitHub Finder data while matching the operator-visible filters, metric definitions, outlier thresholds, and chart grouping.
---

# Engineering Intelligence report analysis

Use the generated self-contained HTML as the evidence artifact for every question about a report. Do not rerun a live query, substitute a different snapshot, or create an independent date/grouping convention when the operator is comparing the answer with the report.

## Required workflow

1. Identify the exact HTML report the operator is viewing and retain its snapshot ID.
2. Restate or infer the visible filter state. Distinguish an unselected multi-select (all values) from explicitly selected values.
3. Run `scripts/analyze_github_finder.py REPORT [FILTERS]` for GitHub Finder questions. It reads `github-finder-data` directly from the HTML and reproduces the report's date, mapping, completeness, threshold, and UTC-Monday chart rules.
4. Base every stated count, average, trend, missing-data observation, and outlier on that output. Link named examples using the URLs returned by the script.
5. State whether threshold exclusion is enabled and identify partial first/current weeks before interpreting a trend.
6. If the requested filter state is ambiguous, provide the exact filter settings used. Ask for clarification only when materially different plausible settings remain.

When diagnosing one record, inspect the embedded row first. Query the pinned local database only to explain the row's underlying commit or review boundaries, and clearly separate that diagnostic evidence from the values visible in the report.

Treat coding, pickup, and review as calendar hours. Use the metric definitions embedded in the report. Treat missing values as unavailable rather than zero. Keep analysis neutral and do not turn activity data into employee rankings or performance conclusions.

If script output and the visible report disagree, stop interpretation and resolve the mismatch in filter state, report version, grouping, or rendering logic first.

## GitHub Finder examples

Match a two-week mapped-author view with the report's default thresholds:

```bash
python scripts/analyze_github_finder.py REPORT \
  --record-type pull_request --team-mapping mapped \
  --from-date 2026-08-26 --to-date 2026-09-08
```

Add `--exclude-thresholds` only when the report checkbox is enabled. Pass repeated categorical flags such as `--team`, `--repository`, `--author`, and `--reviewer` to reproduce selected multi-select values.
