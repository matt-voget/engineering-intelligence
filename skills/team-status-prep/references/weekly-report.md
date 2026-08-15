# Weekly report contract

## Scope and freshness

Use the installed source and team configuration as the only source of scope, roster,
identity, and team ordering. Run the complete refresh first and require a receipt with
`status: completed`, a new snapshot ID, every configured Jira board/query run, and
every configured GitHub repository run. A failed or missing configured source blocks
rendering. The normalized store is the deduplication boundary; never merge raw exports.

The Jira board whose configured role is `ibr` (the IBR board; `portfolio` is a legacy alias) supplies the report workflow. If
none has that role, the first configured board is used. Named queries and repositories
extend coverage but do not imply ownership beyond their explicit configuration.

## Evidence and synthesis

Render only the receipt's new pinned snapshot. Discover teams and people from that
snapshot, including secondary memberships and people without a current team. Preserve
source freshness, empty workflow states, unmapped statuses, exact Target Date text,
flags, metrics definitions, sample sizes, exclusions, Jira hierarchy, blocking links,
and linked GitHub pull requests, commits, and reviews.

Agent-authored summaries must be neutral weekly-update prose grounded in links. Group
related work into themes and explain why it matters; do not concatenate ticket text or
turn activity counts into performance claims. Say when descriptions, thresholds,
identity mappings, roster evidence, GitHub Issues, or other requested evidence are
unavailable. Never access private notes or 1:1 content.

## Output

Create exactly one self-contained HTML single-page app with embedded CSS and JavaScript:

- `#/` is an overview with one full-width row per configured team. The linked team name
  is the only route control, and every current member links to their person page.
- `#/teams/TEAM` shows workflow, hierarchy, health, hygiene, metrics, Jira/GitHub
  classification when its configured query exists, and member links.
- `#/people/PERSON` shows neutral work context, current memberships, Jira relationships,
  delivery evidence, deterministic signals, and team links.

Keep overview rows expanded and secondary evidence on detail routes collapsed. Include
clickable evidence, explicit empty states, snapshot ID, generation time, source
freshness, search, sortable flat tables, table filters, status filters, and a global
date range filter. Direct hash routes must work when the HTML file is opened locally.

Run the renderer with the same installed configuration used by refresh:

```bash
python skills/team-status-prep/scripts/generate_weekly_status.py \
  --snapshot SNAPSHOT_ID \
  --data-dir DATA_DIR \
  --source-config SOURCE_CONFIG \
  --teams-config TEAMS_CONFIG \
  --summaries-json SUMMARIES_JSON \
  --output reports/weekly-status-YYYY-MM-DD.html
```

End with the output path, snapshot identifier, source freshness, and evidence gaps.
