---
name: team-status-prep
description: Refresh and deduplicate every configured Jira and GitHub source, create a pinned snapshot, and generate one self-contained evidence-linked HTML report covering configured teams and individuals. Use for weekly engineering status, leadership and portfolio reviews, team health, Jira hygiene, delivery flow, or current team and engineer work.
---

# Generate the Engineering Status Report

Read [references/weekly-report.md](references/weekly-report.md) completely and follow
it exactly. Use `scripts/generate_weekly_status.py` only after the refresh succeeds.
The required result is one portable HTML single-page app with an overview route and
one client-side route for every team and individual; do not create a companion report
directory.

Use the installed Engineering Intelligence MCP tools when available. Fall back to
`uv run engintel` from `ENGINTEL_REPO` or the repository resolved as `../..` from
this skill. Use `ENGINTEL_DATA_DIR` for the persistent runtime.

## Refresh before rendering

- Always run the complete refresh workflow when generating a report. Do not reuse a
  cached or earlier snapshot for a new report.
- Require a completed receipt for every configured Jira scope and GitHub repository.
- Rely on the ingestion layer's stable source keys to deduplicate Jira issues, pull
  requests, commits, and reviews. Never concatenate exports or deduplicate in prose.
- Render only the new snapshot named in that receipt. Never mix live and snapshot data.
- For questions about an already-generated report, reuse its snapshot instead of
  refreshing unless the user asks for a new report.

Resolve `DATA_DIR`, `SOURCE_CONFIG`, and `TEAMS_CONFIG` from environment variables
`ENGINTEL_DATA_DIR`, `ENGINTEL_SOURCE_CONFIG`, and `ENGINTEL_TEAMS_CONFIG` when set.
Otherwise read `~/.config/engineering-intelligence/installation.json` (or
`ENGINTEL_CONFIG_DIR/installation.json`). Stop with setup instructions if the files
cannot be resolved; never fall back to the checked-in starter templates.

Current-status CLI command:

```bash
uv run engintel refresh run \
  --source-config SOURCE_CONFIG \
  --teams-config TEAMS_CONFIG \
  --data-dir DATA_DIR
```

Stop on authentication, ingestion, migration, coverage, receipt, or integrity failure.
Do not render from partial, stale, or remembered data.

## Build the report

1. Call MCP `get_dashboard(snapshot)` to establish health and active flags.
2. Discover teams and people from the new snapshot; never use a hard-coded roster.
3. Call MCP `get_team_brief(snapshot, team)` for the requested team.
4. Call MCP `get_metrics(snapshot, team=TEAM)` when flow context is requested.
5. Call MCP `get_feature(snapshot, issue_key)` only for Features needing explanation.
6. Call MCP `list_attention` or `get_flag` only when flag lifecycle detail is needed.
7. Call MCP `get_team(snapshot, team)` only when the compact brief lacks evidence
   required by a follow-up; its complete GitHub delivery list can be large.

Use equivalent CLI commands when MCP is unavailable:

```bash
uv run engintel dashboard get SNAPSHOT --data-dir DATA_DIR --format json
uv run engintel team brief TEAM --snapshot SNAPSHOT \
  --data-dir DATA_DIR --format json
uv run engintel metrics get --snapshot SNAPSHOT --team TEAM \
  --data-dir DATA_DIR --format json
```

Do not mark flags viewed merely because they were fetched.

## Preserve report content

Use this fixed order:

1. **Health and coverage** — health state, coverage reliability, source freshness,
   and active flags.
2. **Recently completed** — the most recently closed IBR item, including Jira key,
   title, link, and Target Date when present.
3. **In progress** — every IBR item in progress, preserving Jira key, title, link,
   and Target Date when present.
4. **Ready for build** — every IBR item ready for build, including an explicit empty
   state and Target Date on each item when present.
5. **Concerns to probe** — flag timestamp, severity, unread/viewed state, explanation,
   and direct Jira, GitHub, or metric evidence.
6. **Meeting questions** — at most five neutral questions grounded in the evidence.

Preserve configured empty workflow states and unmapped statuses. An empty state is
only a concern when the deterministic presentation flags it.
Preserve `target_date_value` exactly as entered in Jira, including month-only values;
do not invent a day or reinterpret missing values.

## Analysis boundary

- Treat deterministic views as facts and label agent synthesis as interpretation.
- Do not invent health scores, thresholds, ownership, completion dates, or repository
  scope.
- Do not infer employee performance from tickets, commits, pull requests, reviews,
  or lines changed.
- Treat roster, identity, and source-coverage warnings as blockers on stronger claims.
- Describe comparisons using the saved metric definition, window, baseline, sample
  size, and exclusions.
- Keep all issue and metric references clickable.
- Do not access or store private notes or 1:1 content.

End with the output path, snapshot identifier, source freshness, and a short list of
evidence gaps that limit the report.
