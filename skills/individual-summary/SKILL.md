---
name: individual-summary
description: Produce a short, evidence-linked summary of one configured individual's Jira and GitHub work for a requested time range, including flow metrics and notable anomalies.
---

# Individual summary

Use this skill when an operator asks what one person has been working on or requests an individual work summary. Keep the result short, neutral, and useful for a manager preparing a conversation. Do not score, rank, or infer effort, productivity, or performance from activity data.

## Evidence workflow

1. Use a newly generated Engineering Intelligence report unless the operator identifies an existing report. Retain its snapshot ID and freshness. Never combine records from different reports or snapshots.
2. Resolve the person through the configured team roster. Run `scripts/summarize_individual.py REPORT PERSON --from-date YYYY-MM-DD --to-date YYYY-MM-DD`, supplying `--teams-config` when it is not available at the default configured path.
3. Base every count, metric, ticket, pull request, and anomaly on the helper output and the report links it returns. Missing measurements are unavailable, not zero.
4. Describe the main themes of work from ticket and PR titles. Link the small number of examples that best support each theme; do not dump every record.
5. Report calendar-time metrics with sample sizes:
   - issue cycle time for assigned issues that entered Done in the range, including phase times when present;
   - coding time for merged PRs authored by the person;
   - pickup and review time for merged PRs reviewed by the person.
6. Be precise about reviewer attribution: the report measures PR creation to its first review and first review to merge. Filtering to a reviewer shows PRs that person reviewed; it does not prove that person caused either interval or that their review was the first one.

The helper treats Jira work in the range as assigned issues updated in the range or completed in the range. It separately returns all currently active assigned issues so “no active Jira tickets” can be assessed at the report snapshot.

## What to notice

Look for evidence that materially changes the summary, including long overall or phase cycle times, threshold-exceeding PR timing, missing timing data, little completed work, no active assigned Jira work, many concurrent active issues, skipped phases, repeated work on one issue type, or a strong concentration in one work theme. These are prompts for context, not conclusions about the person.

Use judgment. A low count in a short or partial range, support work, leave, cross-team work, large initiatives, inherited PRs, dependency waits, batching, and missing links can all explain the data. Mention a possible explanation only when the linked evidence supports it, and label it as a possibility. Do not call a mix “unbalanced” from a tiny sample or compare people unless the operator explicitly asks for a team-level comparison and the comparison is appropriate.

## Output shape

Aim for four compact parts:

- one or two sentences on what the person worked on, with Jira and PR links;
- a metrics line with sample sizes and missing-data counts;
- one to three notable observations, each tied to evidence;
- a short scope note naming the date range, report snapshot, and material gaps.

Prefer “The report shows…” and “A useful follow-up is…” over causal or evaluative claims.

