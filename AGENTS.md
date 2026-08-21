# Engineering Intelligence agent guide

## Purpose

Engineering Intelligence is a local, read-only Jira and GitHub collection and
reporting system for engineering managers. It normalizes and deduplicates source
records, creates reproducible snapshots, exposes deterministic CLI/MCP queries, and
generates one self-contained HTML status application for configured teams and people.

Treat the deterministic data layer as authoritative. Agent-written summaries are
interpretation and must stay neutral, evidence-linked, and explicit about gaps. Never
turn tickets, commits, pull requests, reviews, or lines changed into employee rankings
or performance conclusions. Private notes and 1:1 content are out of scope.

## Check setup before data or report work

Resolve the configuration directory from `ENGINTEL_CONFIG_DIR`, or default to
`~/.config/engineering-intelligence`. A usable installation has:

- `installation.json` with `schema_version: "4"`;
- existing files at its `source_config`, `teams_config`, and `data_dir` paths;
- no unresolved `CHANGE_ME` values in either YAML file; and
- a Jira email supplied by `jira.email` or the variable named by `jira.email_env`, plus
  the token variables named by `jira.token_env` and `github.token_env`, or an equivalent
  Keychain-backed scheduled setup.

Inspect these paths without printing secret values. Do not treat the checked-in
`config/*.example.yaml` files as live configuration.

If setup is missing or incomplete:

1. Verify Python 3.11+ and `uv` are available.
2. Run `uv sync --all-groups`.
3. Run `uv run engintel setup`. This creates private starter YAML and initializes the
   database outside the clone by default.
4. Run the agent-led onboarding in `docs/onboarding.md`: the user supplies only the
   Jira hostname, credentials, the IBR board choice, and their team names; the agent
   discovers boards, custom-field IDs, the GitHub organization and repositories, and
   team rosters with cross-verified GitHub identities, then proposes the complete
   configuration for confirmation. Never write a discovered value the user has not
   confirmed, and never guess anything discovery cannot verify.
5. Ask the user to provide credentials through environment variables or a supported
   credential store. Never ask the user to paste credential values into chat. Explain
   that credentials exported after the current agent process started will not update
   that process's environment; the user must exit and restart the agent from the
   credential-bearing shell before refresh or MCP use. Never write tokens into the
   repository, YAML, database, report, logs, or chat output.
6. Validate the YAML, then run `uv run engintel install --agent codex` (and/or
   `--agent claude-code` when requested). Use explicit `--source-config`,
   `--teams-config`, and `--data-dir` when paths differ from the defaults.
7. Confirm the installation manifest records the private paths and the MCP registration
   receives `ENGINTEL_DATA_DIR`, `ENGINTEL_SOURCE_CONFIG`, and
   `ENGINTEL_TEAMS_CONFIG`.
8. Tell the user to restart the agent after first-time installation so the newly
   installed skill and MCP server are available, then continue with validation or the
   requested report.

Do not block unrelated source-code work merely because a user's Jira/GitHub setup is
incomplete. Setup is required before ingestion, snapshot, query, schedule, or report
operations.

To validate a from-scratch setup without touching a live installation, run the same
flow with explicit `--config-dir` and `--data-dir` pointed at a throwaway directory;
never let a test setup overwrite live configuration, data, or agent registrations.

## Relocating the live installation to a different clone

`engintel install` refuses to replace agent skill links owned by another clone. To
promote a new clone:

1. Record the current schedule settings with `engintel schedule status` before
   anything else — `engintel uninstall` removes the owned schedule (wrapper,
   launchd/systemd definition, and `schedule.json`) along with the agent links, and
   those settings are not recoverable afterwards. Keychain entries are preserved.
2. Run `uv sync --all-groups` in the new clone, then `engintel uninstall`, then
   `engintel install` from the new clone with the same agents and the config/data
   paths recorded in the installation manifest.
3. Recreate the schedule with the previously recorded time, retention, backup
   destination, and Keychain labels.
4. Verify the manifest's `repository_root`, the skill link targets, the MCP
   registration's directory and `ENGINTEL_*` variables, the loaded schedule
   definition, and each Keychain lookup — without printing credential values.

## Configuration rules

- Keep organization-specific configuration outside the clone.
- Give exactly one Jira board the `ibr` role. The IBR board defines which tickets are
  in scope versus out of scope from an IBR perspective; `portfolio` is accepted as a
  legacy alias and normalizes to `ibr`.
- Use stable lowercase IDs for teams and people. Preserve IDs across display-name
  changes and represent secondary membership with the same person ID in multiple teams.
- Repository `team_ids` must refer to configured team IDs. Shared repositories may map
  to multiple teams; the mapping is scope, not exclusive ownership.
- Optional team classification queries use `team-field-TEAM_ID`.
- Set Jira custom-field IDs only after verifying them against the user's Jira instance.
- Never commit live URLs, account IDs, rosters, tokens, generated reports, databases,
  raw archives, receipts, exports, or backups.

## Report workflow

Use the `team-status-prep` skill for a new status report and follow its contract. Every
new report must:

1. Resolve paths from the installation manifest or `ENGINTEL_*` variables.
2. Run the complete refresh across every configured Jira board/query and GitHub
   repository; do not reuse an older snapshot.
3. Require a completed receipt for all configured scopes. Stop on authentication,
   ingestion, migration, coverage, or integrity failure.
4. Rely on normalized stable source keys for deduplication. Never merge raw exports or
   deduplicate in prose.
5. Render only the new pinned snapshot into exactly one self-contained HTML file.
6. Discover teams, people, and memberships from the snapshot rather than hard-coding a
   roster.
7. Report the output path, snapshot ID, freshness, and evidence gaps.

For questions about an existing report, reuse its pinned snapshot unless the user asks
for a fresh report. Do not mark flags viewed merely by reading them.

## Scheduled refreshes

On macOS, unattended schedules should load the Jira token, GitHub token, and optional
backup passphrase from Keychain. Use the scheduler's Jira/GitHub/backup Keychain
options; do not assume an interactive shell environment will be present. Preserve an
existing schedule's time, retention, backup destination, and owned credential labels
when upgrading it. Verify schedule status and test credential lookup without printing
the credential.

## Development workflow

- Use `uv run engintel ...` for the CLI and `uv run engintel-mcp` for the MCP server.
- Database schema changes require an Alembic migration; never rewrite an applied
  migration.
- Keep business rules in deterministic query/domain code. CLI, MCP, skills, and HTML
  rendering should consume the same contracts rather than implement competing logic.
- Preserve snapshot pinning: later refreshes must not change existing snapshot results.
- Preserve stable upsert keys for Jira issues and GitHub PRs, commits, and reviews.
- Keep Jira and GitHub interactions read-only unless the project scope is explicitly
  changed and reviewed.
- Preserve unrelated user changes and untracked files. Generated `reports/`, runtime
  data, exports, and backups are intentionally ignored.

Before handing off code changes, run:

```bash
uv run ruff check .
uv run pytest -q
git diff --check
```

When the report skill changes and Codex's skill validator is installed, also run:

```bash
uv run python ~/.codex/skills/.system/skill-creator/scripts/quick_validate.py \
  skills/team-status-prep
```

Review `git status --short` before committing and never add private configuration or
unrelated untracked artifacts.

## Primary references

- `README.md`: clone-to-first-report onboarding and user-facing operations
- `docs/onboarding.md`: agent-led discovery flow for first-time configuration
- `config/*.example.yaml`: deliberately nonfunctional configuration templates
- `skills/team-status-prep/`: current report skill, report contract, and renderer
- `src/engineering_intelligence/refresh/`: complete refresh orchestration and receipts
- `src/engineering_intelligence/queries/`: deterministic snapshot-backed views
- `src/engineering_intelligence/lifecycle.py`: agent/MCP installation manifest
- `src/engineering_intelligence/scheduler/`: owned recurring refresh installation
- `docs/architecture.md`: architectural constraints and system shape
