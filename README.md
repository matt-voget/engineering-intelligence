# Engineering Intelligence

Engineering Intelligence is a local, read-only Jira and GitHub data pipeline for
engineering managers. It refreshes configured sources, deduplicates them into a local
database, pins reproducible snapshots, and generates one self-contained HTML status
app with an overview plus pages for every configured team and person.

No Jira or GitHub credentials are committed, and generated data and reports remain on
the user's machine.

This repository is intended for internal, clone-based use. Install and run it from a
working clone; the Python wheel is not a supported distribution because setup,
migrations, and report generation use repository-owned files.

## From clone to first report

Prerequisites:

- Python 3.11 or newer
- [`uv`](https://docs.astral.sh/uv/getting-started/installation/)
- Read access to the Jira boards/JQL and GitHub repositories being configured
- Jira email and API token; GitHub personal access token
- Optional: Codex CLI or Claude Code to invoke the report skill conversationally

## Set up with Claude Code

Clone the repository and install its dependencies:

```bash
git clone https://github.com/matt-voget/engineering-intelligence.git
cd engineering-intelligence
uv sync --all-groups
```

Export the credentials in this terminal before starting Claude Code. These are the
default variable names in the starter configuration; if you later change the names in
`sources.yaml`, export the renamed variables instead:

```bash
export ATLASSIAN_EMAIL='manager@example.com'
export ATLASSIAN_API_TOKEN='...'
export ATLASSIAN_HOST='example.atlassian.net'
export GITHUB_PAT='...'
claude
```

Do not paste tokens into the Claude conversation. If Claude Code was already running
when the variables were exported, exit and restart it from the same terminal so its
MCP processes inherit the credentials.

Then ask:

```text
Set up Engineering Intelligence for my teams. Follow CLAUDE.md, inspect the current
setup safely, and guide me through every value you cannot determine without guessing.
Never display or store credential values.
```

Claude will create private starter configuration outside the clone and run the
agent-led onboarding in [`docs/onboarding.md`](docs/onboarding.md): you supply only
your Jira hostname and credentials, pick your IBR board from a discovered list, and name
your teams — the agent discovers the custom fields, GitHub organization and
repositories, and team rosters (cross-verifying each member's GitHub username), then
proposes the complete configuration for your confirmation before writing it. It then
validates the result and runs `engintel install --agent claude-code`. Restart Claude
Code once after installation so the newly registered MCP server and report skill are
available. Then ask:

```text
Use $team-status-prep to generate my current engineering status report.
```

The remaining sections document the same setup flow command by command and provide a
CLI-only alternative.

Install dependencies and generate private starter configuration:

```bash
git clone https://github.com/matt-voget/engineering-intelligence.git
cd engineering-intelligence
uv sync --all-groups

CONFIG_DIR="$HOME/.config/engineering-intelligence"
DATA_DIR="$HOME/.local/share/engineering-intelligence"

uv run engintel setup \
  --config-dir "$CONFIG_DIR" \
  --data-dir "$DATA_DIR"
```

`setup` creates these files outside the clone by default:

```text
~/.config/engineering-intelligence/sources.yaml
~/.config/engineering-intelligence/teams.yaml
~/.local/share/engineering-intelligence/engineering-intelligence.db
```

Edit both YAML files and replace every `CHANGE_ME` value (or let an agent discover
and propose them via [`docs/onboarding.md`](docs/onboarding.md)). The source file
defines:

- Jira hostname or fallback URL, the IBR board and other boards, optional JQL scopes, and optional custom
  fields
- GitHub repositories and their explicit team mappings
- Environment-variable names used for credentials

The teams file defines stable team/person IDs, aliases, membership dates, and Jira and
GitHub identities. Secondary memberships are represented by putting the same stable
person ID in more than one team. Keep the configuration private if identity mappings
are sensitive.

Export credentials and the Jira hostname in your shell; the exact variable names come
from `sources.yaml`:

```bash
export ATLASSIAN_EMAIL='manager@example.com'
export ATLASSIAN_API_TOKEN='...'
export ATLASSIAN_HOST='example.atlassian.net'
export GITHUB_PAT='...'
```

Install the report skill for one or both supported agents. This validates the YAML,
initializes storage, links the skill, registers the local read-only MCP server, and
records the data/config paths in an installation manifest:

```bash
uv run engintel install --agent codex \
  --config-dir "$CONFIG_DIR" \
  --data-dir "$DATA_DIR"
# or substitute: --agent claude-code
# or pass --agent twice to install both
```

The selected agent CLI must be installed and available on `PATH` so `install` can
register the local MCP server. Agent integration is optional; the CLI-only workflow
below does not require Codex or Claude Code.

Then ask the agent:

```text
Use $team-status-prep to generate my current engineering status report.
```

Each report run applies the roster, refreshes every configured Jira and GitHub source,
upserts records by stable source identifiers, creates a source- and organization-pinned
snapshot, and renders exactly one portable HTML file. Authentication, source, coverage,
or integrity failures stop the run rather than producing a stale or partial report.

## Verify setup without an agent

The generated paths can also be supplied explicitly. If you start a new shell, define
them again:

```bash
CONFIG_DIR="$HOME/.config/engineering-intelligence"
DATA_DIR="$HOME/.local/share/engineering-intelligence"

uv run engintel refresh run \
  --source-config "$CONFIG_DIR/sources.yaml" \
  --teams-config "$CONFIG_DIR/teams.yaml" \
  --data-dir "$DATA_DIR"

uv run engintel refresh latest --data-dir "$DATA_DIR"
```

The refresh receipt contains the new snapshot ID and one result for every configured
scope. To render manually, run:

```bash
uv run python skills/team-status-prep/scripts/generate_weekly_status.py \
  --snapshot SNAPSHOT_ID \
  --data-dir "$DATA_DIR" \
  --source-config "$CONFIG_DIR/sources.yaml" \
  --teams-config "$CONFIG_DIR/teams.yaml" \
  --output reports/weekly-status.html
```

The HTML has hash routes (`#/`, `#/teams/...`, and `#/people/...`) and can be opened
directly from disk. It has no remote CSS or JavaScript dependency.

Because every command accepts explicit `--config-dir`/`--source-config`/
`--teams-config`/`--data-dir` paths, the full setup-to-refresh flow can also be
exercised against a throwaway directory to validate a clone or configuration change
end to end without touching a live installation, its data, or its schedule.

## Configuration conventions

- Give exactly one Jira board the `ibr` role. The IBR board defines which tickets are
  in scope versus out of scope from an IBR perspective (`portfolio` is accepted as a
  legacy alias). Installation and refresh reject missing or duplicate IBR boards.
- Use stable lowercase IDs for teams and people. Do not reuse IDs after renames.
- Repository `team_ids` must refer to explicit team IDs in `teams.yaml`; shared repos
  may list multiple teams.
- If team-wide Jira classification is desired, add a named query with ID
  `team-field-TEAM_ID`. Empty or absent optional classification scopes do not change
  the IBR workflow.
- Set optional Jira custom-field IDs only after verifying them in your Jira instance.
- Tokens stay in environment variables. They are never written to YAML, SQLite,
  snapshots, reports, or installation manifests.
- `ATLASSIAN_HOST` accepts either an Atlassian hostname or a full HTTPS URL and
  overrides `jira.base_url`; the configured URL remains the fallback.

The checked-in files in [`config/`](config/) are intentionally nonfunctional starter
templates. Never put an organization's live roster, URLs, account IDs, or secrets in
those files.

`ENGINTEL_CONFIG_DIR` and `ENGINTEL_DATA_DIR` may be used instead of repeatedly
passing the corresponding command options. Credential variable names remain defined
by the private `sources.yaml` file.

## Troubleshooting setup

- If validation mentions `CHANGE_ME`, finish replacing placeholders in both private
  YAML files. The checked-in examples are not live configuration.
- Jira authentication requires both the configured email and token variables; GitHub
  authentication requires the configured token variable. Check that they are exported
  in the shell running the command without printing their values.
- If agent installation reports that `codex` or `claude` is missing, install the
  selected CLI or use the manual CLI workflow above.
- `uv run engintel doctor` verifies that the local package and CLI can start. A
  successful complete refresh is the end-to-end validation of configuration,
  credentials, source coverage, and storage.

## Maintenance and removal

After pulling a newer version, refresh owned skill links and apply database migrations:

```bash
uv sync --all-groups
uv run engintel upgrade
```

Remove agent integration without deleting data or configuration:

```bash
uv run engintel uninstall
```

`uninstall` removes every recorded agent link **and any owned refresh schedule**,
including its wrapper script, launchd/systemd definition, and `schedule.json`. If you
intend to reinstall afterwards, record the output of `uv run engintel schedule status`
first so the schedule can be recreated with the same time, retention, backup
destination, and Keychain labels. Keychain entries themselves are not deleted.

## Moving the installation to a different clone

`install` refuses to replace agent skill links owned by another clone. To promote a
new clone to be the live instance while keeping existing configuration, data, and
credentials:

1. In the new clone, run `uv sync --all-groups`.
2. Record the current schedule settings: `uv run engintel schedule status`.
3. Release the old clone's links: `uv run engintel uninstall` (this also removes the
   owned schedule; see above).
4. From the new clone, run `uv run engintel install` with the same `--agent`,
   `--source-config`, `--teams-config`, and `--data-dir` values recorded in the
   installation manifest.
5. Recreate the schedule with `uv run engintel schedule install`, passing the
   previously recorded time, backup, and Keychain options.
6. Verify: the manifest's `repository_root` points at the new clone, the agent skill
   links resolve into it, the MCP registration's command directory and `ENGINTEL_*`
   variables are correct, the schedule definition is loaded, and each Keychain lookup
   succeeds without printing the credential.

On macOS, unattended refreshes should read both source tokens and any backup
passphrase from Keychain. After creating the Keychain entries, install the schedule
with `--jira-keychain-service`/`--jira-keychain-account`,
`--github-keychain-service`/`--github-keychain-account`, and the backup
`--keychain-service`/`--keychain-account` options. The generated wrapper exports the
Jira token under the variable configured by `jira.token_env`.

Basic diagnostics and tests:

```bash
uv run engintel doctor
uv run pytest
uv run ruff check .
```

## Data and privacy model

The application reads Jira and GitHub; it does not transition issues, post comments,
or modify repositories. Normalized history, raw payload archives, refresh receipts,
snapshots, exports, backups, and reports are local artifacts excluded by `.gitignore`.
Evidence is work context, not an employee-performance score. Private notes and 1:1
content are outside the system's scope.

The current implementation and design constraints are in
[`docs/architecture.md`](docs/architecture.md). The installed
report contract is [`skills/team-status-prep/SKILL.md`](skills/team-status-prep/SKILL.md).
