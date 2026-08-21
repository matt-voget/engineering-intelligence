# Weekly report contract

## Scope and freshness

Use the installed source and team configuration as the only source of scope, roster,
identity, and team ordering. Run the complete refresh first and require a receipt with
`status: completed`, a new snapshot ID, every configured Jira board/query run, and
every configured GitHub repository run. A failed or missing configured source blocks
rendering. The normalized store is the deduplication boundary; never merge raw exports.

The Jira board whose configured role is `ibr` (the IBR board; `portfolio` is a legacy
alias) supplies the report workflow. If none has that role, the first configured board
is used. Named queries extend Jira coverage. GitHub repositories are organization-wide
collection scope and never imply team ownership; a GitHub record belongs in a team's
view only through a configured member's GitHub identity.

## Evidence and synthesis

Render only the receipt's new pinned snapshot. Discover teams and people from that
snapshot, including secondary memberships and people without a current team. Preserve
source freshness, empty workflow states, unmapped statuses, exact Target Date text,
flags, metrics definitions, sample sizes, exclusions, Jira hierarchy, blocking links,
and linked GitHub pull requests, commits, and reviews.
Preserve configured RAG rule IDs, thresholds, symbols, team/classification scope, and
the deterministic assessment on each metric instance. Never infer an unconfigured
threshold.

Agent-authored summaries must be neutral weekly-update prose grounded in links. Group
related work into themes and explain why it matters; do not concatenate ticket text or
turn activity counts into performance claims. Say when descriptions, thresholds,
identity mappings, roster evidence, GitHub Issues, or other requested evidence are
unavailable. Never access private notes or 1:1 content.

## Output

Create exactly one self-contained HTML single-page app with embedded CSS and JavaScript:

- `#/` is a four-part landing page: Teams, People, Issue Finder, and GitHub Finder.
  Teams and People provide compact direct links to every configured detail page.
  `#/issue-finder` provides one deduplicated table of every Jira issue pinned in the
  configured team-field query scopes, with text, date, team, status, and IBR
  classification filters. Users can add, remove, and reorder the Issue Finder's
  visible columns without changing its underlying evidence. Cycle columns measure
  calendar time from first entry into In Progress through first Done or the pinned
  snapshot boundary, break out In Progress, Code Review, and Test time, and identify
  skipped steps in the ordered delivery workflow. User-supplied Amber and Red cycle
  thresholds color and symbolize qualifying cells, while missing or incomplete
  evidence uses a distinct treatment. A row filter surfaces any issue with at least
  one Red or Amber cell. Never invent default thresholds. `#/github-finder` remains
  a routed placeholder page.
- `#/teams/TEAM` shows workflow, hierarchy, health, hygiene, metrics, Jira/GitHub
  classification when its configured query exists, Build Cycle Time split between
  IBR-linked parents and all non-IBR team-assigned issue types with status/child
  evidence, GitHub PR pickup/review time across all configured repositories scoped by
  team-member author identity with contributor and participant evidence, and member
  links. Team Health includes a red/amber/green index whose links open the owning
  section and jump to the exact assessed issue or pull request.
- `#/people/PERSON` shows neutral work context, current memberships, Jira relationships,
  delivery evidence, deterministic signals, and team links.

Keep overview rows expanded and secondary evidence on detail routes collapsed. The top
navigation identifies the application as Engineering Intelligence, embeds the supplied
logo asset, and shows the report-generation timestamp. Include clickable evidence,
explicit empty states, snapshot ID, generation time, source freshness, sortable flat
tables, table filters, and status filters. Date-range controls belong beside the table
or metric group they filter and must not apply globally. Direct hash routes must work
when the HTML file is opened locally.

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
