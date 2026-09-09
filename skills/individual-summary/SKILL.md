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

Look for evidence that materially changes the summary, including long overall or phase cycle times, assigned issues that have remained in their current status for an unusually long time, threshold-exceeding PR timing, missing timing data, little completed work, no active assigned Jira work, many concurrent active issues, skipped phases, repeated work on one issue type, or a strong concentration in one work theme. These are prompts for context, not conclusions about the person. For current-status lag, use the status-started date and status-age value; do not substitute the issue's last-updated date.

Use judgment. A low count in a short or partial range, support work, leave, cross-team work, large initiatives, inherited PRs, dependency waits, batching, and missing links can all explain the data. Mention a possible explanation only when the linked evidence supports it, and label it as a possibility. Do not call a mix “unbalanced” from a tiny sample or compare people unless the operator explicitly asks for a team-level comparison and the comparison is appropriate.

## Output shape

Use short Markdown headings and compact bullets so the result scans well in chat. Aim for four parts:

- **Work summary:** one or two sentences on the main themes, followed by three to six representative Jira/PR bullets. Each bullet includes a linked key or PR, its status, and its relevant date.
- **Flow metrics:** separate Jira, authored-PR, and reviewed-PR bullets. Always show units, sample sizes, and missing counts.
- **What stands out:** one to three observations tied to evidence. For lagging Jira work, show the current status, the date it entered that status, and calendar days there.
- **Scope:** the exact date range, report snapshot/freshness, and material gaps in one compact line.

Prefer “The report shows…” and “A useful follow-up is…” over causal or evaluative claims.
