# Engineering Leadership Cockpit

## Agent-Native Technical Architecture Proposal

Status: Proposed  
Date: July 28, 2026

Deployment decision: Local stack

## 1. Architectural objective

Build a personal, agent-native engineering-intelligence system that:

- Persistently collects and preserves Jira and GitHub data.
- Produces reproducible metrics, flags, health states, and relationships.
- Exposes the same deterministic domain operations through CLI and MCP.
- Lets Codex, Claude Code, and other autonomous agents perform analysis.
- Renders approved presentations consistently across agents.
- Supports historical queries, stable snapshots, evidence, and backups.

The external agent is the conversational interface. The system does not include a built-in chat experience.

## 2. Recommended shape

```text
External agent session
    │
    ├── Skill / prompt
    │
    ├── MCP tools ──────────────┐
    │                           │
    └── CLI commands ───────────┤
                                ▼
                       Deterministic core
                  ┌─────────────┼─────────────┐
                  │             │             │
               Queries       Metrics        Flags
                  │             │             │
                  └─────────────┼─────────────┘
                                │
                       Snapshot service
                                │
                     Persistent data store
                         │             │
                    Jira ingest    GitHub ingest
                         │             │
                         └──── backups ┘

Deterministic core → presentation-data schemas → HTML / Markdown / JSON renderers
```

## 3. Core architectural rule

MCP, CLI, skills, prompts, and renderers must not contain independent business logic.

All authoritative behavior lives in one deterministic domain package:

- Source normalization
- Hierarchy construction
- Attribution
- Metrics
- Flags
- Health
- Snapshots
- Evidence
- Presentation-data assembly

Both MCP and CLI call this package directly. This prevents agent- or interface-specific results.

## 4. Recommended implementation stack

### 4.1 Primary runtime

Use Python as the initial single runtime.

Recommended libraries:

- `uv` for environment and dependency management
- Pydantic for versioned schemas and configuration validation
- SQLAlchemy or SQLModel for persistence
- Alembic for schema migrations
- Typer for the CLI
- FastMCP or the current official Python MCP SDK for the MCP server
- httpx for Jira and GitHub APIs
- Polars for metric and historical calculations
- Jinja2 for deterministic HTML and Markdown rendering
- pytest for unit, integration, and golden-render tests

Reasons:

- Strong fit for ingestion, historical processing, metrics, and scripting
- Straightforward CLI and MCP support
- Easy for autonomous coding agents to invoke and extend
- Avoids splitting business logic across Python and TypeScript in the initial release

The architecture should not expose Python-specific objects through public interfaces. Pydantic JSON schemas form the stable boundary.

### 4.2 Persistence

Start with SQLite in WAL mode for the personal, local-first release.

Use:

- Normalized relational tables for current and historical queries
- Immutable event tables for source changes and flag lifecycle
- Raw source payload storage for auditability and reprocessing
- Content hashes for deduplication

SQLite is appropriate for a single owner and modest Jira/GitHub volume. Keep repository interfaces narrow enough to migrate to PostgreSQL if the system later becomes remote, multi-process, or multi-user.

### 4.3 Raw payload archive

Store raw Jira and GitHub payloads as compressed, content-addressed objects outside the main database.

Each ingestion record stores:

- Source
- Source record ID
- Retrieval timestamp
- Content hash
- Object location
- API version
- Cursor or request context

This allows re-normalization without repeatedly querying external systems.

### 4.4 Backups

Back up:

- SQLite database
- Raw payload archive
- Versioned configuration
- Encryption metadata required for recovery

Requirements:

- Encrypted
- Stored separately from the primary working directory
- Point-in-time or frequent snapshot recovery
- Retention policy
- Periodic automated restore verification
- Backup status queryable through CLI and MCP

The exact remote backup target should remain configurable. A local-only copy is not sufficient as the sole backup.

## 5. Repository structure

```text
engineering-leadership-cockpit/
├── pyproject.toml
├── alembic.ini
├── config/
│   ├── teams.yaml
│   ├── sources.yaml
│   ├── workflows.yaml
│   ├── metrics.yaml
│   └── flags.yaml
├── src/engintel/
│   ├── cli/
│   ├── mcp/
│   ├── config/
│   ├── domain/
│   ├── ingestion/
│   │   ├── jira/
│   │   └── github/
│   ├── normalization/
│   ├── attribution/
│   ├── metrics/
│   ├── flags/
│   ├── snapshots/
│   ├── queries/
│   ├── presentations/
│   ├── renderers/
│   ├── persistence/
│   └── security/
├── schemas/
├── skills/
│   └── team-status-prep/
│       ├── SKILL.md
│       ├── references/
│       └── scripts/
├── prompts/
├── templates/
│   ├── html/
│   └── markdown/
├── tests/
│   ├── unit/
│   ├── integration/
│   ├── contract/
│   ├── golden/
│   └── restore/
└── var/
    ├── database/
    ├── raw/
    ├── rendered/
    └── backups/
```

Runtime data paths should be configurable and excluded from source control.

## 6. Domain model

### 6.1 Organization

- `Team`
- `TeamAlias`
- `Person`
- `PersonIdentity`
- `TeamMembership`
- `Repository`
- `RepositoryOwnership`

Team membership and ownership are effective-dated.

### 6.2 Jira work

- `JiraIssue`
- `JiraIssueVersion`
- `JiraStatusEvent`
- `JiraAssignmentEvent`
- `JiraRelationship`
- `Board`
- `BoardMembershipObservation`
- `WorkflowMapping`

`JiraRelationship` supports:

- Parent
- Child
- Subtask
- Blocks
- Is blocked by
- Relates to
- Other source relationship types

Every qualifying IBR item creates a `Feature` projection. The projection references the original Jira issue and preserves its source issue type.

### 6.3 GitHub delivery

- `GitHubRepository`
- `PullRequest`
- `PullRequestVersion`
- `Review`
- `Commit`
- `CheckRun`
- `Release`
- `JiraGitHubRelationship`

Relationships retain:

- Relationship type
- Direct or inferred
- Confidence
- Evidence
- Created and applicable dates

### 6.4 Person-to-work attribution

- `WorkContribution`

Fields include:

- Person
- Work item
- Relationship type
- Direct or rolled up
- Source system
- Source record
- Effective dates
- Confidence
- Evidence

Supported types include:

- High-level owner
- Jira assignee
- Jira contributor
- Child-issue assignee
- Subtask assignee
- Pull-request author
- Commit author
- Reviewer
- Approver
- Manually confirmed relationship

### 6.5 Metrics

- `MetricDefinition`
- `MetricDefinitionVersion`
- `MetricEvaluation`
- `MetricContribution`

An evaluation stores:

- Metric
- Scope
- Snapshot
- Value
- Units
- Time range
- Baseline
- Threshold
- Definition version
- Included and excluded records
- Data-quality state

### 6.6 Flags

- `SignalDefinition`
- `SignalEvaluation`
- `LogicalFlag`
- `FlagOccurrence`
- `FlagEvent`
- `FlagEvidence`
- `FlagUserState`

`SignalDefinition` stores an immutable versioned rule contract. `SignalEvaluation`
records every per-snapshot result, including rules whose condition is false.
`LogicalFlag` is the lifecycle projection for triggered evaluations and owns the
stable fingerprint. `FlagOccurrence` captures each open-to-resolved interval.
`FlagEvent` is immutable.

Per-Feature rules use the Jira issue key as scope, the immutable Jira issue ID as
subject, and a rule-specific dimension. Applicability is explicit: a rule does not
create a clear result for a Feature when required evidence is absent. Missing
required evidence is handled by a separate data-quality rule rather than silently
treated as false.

History-based Feature rules read only transitions visible at the snapshot high-water
mark. Workflow order is a versioned normalized lifecycle. Aging baselines use
completed stage intervals ending in the trailing 90 days, exclude the Feature being
evaluated, and require at least five peer samples. A missing minimum sample suppresses
the aging evaluation rather than producing a low-confidence clear or triggered result.

User state remains separate:

- Viewed
- Unread
- Understood
- Snoozed

### 6.7 Snapshots

- `Snapshot`
- `SnapshotSourceState`
- `SnapshotRuleVersion`
- `SnapshotMetricVersion`

Snapshots may be implemented as consistent high-water marks over immutable events rather than full database copies.

## 7. Ingestion architecture

### 7.1 Jira

Initial scope:

- One configured portfolio board
- Any configured team, work-tracking, support, or quality boards
- Jira projects and boards added through configuration

Collect:

- Current issue fields
- Changelog and status history
- Assignment history
- Parent, child, subtask, and linked issue relationships
- Comments only when explicitly required by a query or analysis policy
- Board membership and rank
- Team, exact Target Date text (`customfield_11513`), safely parsed full target date,
  fix version, labels, and components

Use:

- Incremental synchronization based on updated timestamps and cursors
- Periodic reconciliation for missed changes and deletions
- Rate-limit-aware bounded requests
- Idempotent upserts
- Raw-payload hashing

### 7.2 GitHub

Collect:

- Repositories
- Pull requests and versions
- Reviews and requested changes
- Commits
- Check runs
- Releases and tags

Use webhooks when practical, with periodic reconciliation as the correctness mechanism.

### 7.3 Source deletions

Source deletion must not erase history. Mark records as absent or deleted in current projections while retaining prior observations and raw payloads.

## 8. Deterministic processing pipeline

```text
Collect raw payload
→ Persist raw payload
→ Normalize source records
→ Append immutable events
→ Update current projections
→ Rebuild affected hierarchy and attribution
→ Evaluate affected metrics
→ Evaluate affected flag rules
→ Update logical flags and occurrences
→ Make a new consistent snapshot available
```

Every stage is idempotent.

Processing should be dependency-aware so a changed Jira child issue only recalculates affected Features, people, metrics, and flags.

## 9. Query and presentation services

The deterministic core should expose domain operations rather than low-level table access:

- `create_snapshot`
- `get_snapshot`
- `refresh_sources`
- `evaluate_signals`
- `get_dashboard`
- `get_team_brief`
- `get_team`
- `get_feature`
- `get_people`
- `get_individual`
- `get_metrics`
- `explain_metric`
- `compare_periods`
- `list_attention`
- `get_flag`
- `mark_flag_viewed`
- `mark_flag_unread`
- `snooze_flag`
- `mark_flag_understood`
- `search_evidence`
- `render_presentation`

Each response uses a common envelope:

```json
{
  "schema_version": "1.0",
  "snapshot_id": "opaque-id",
  "snapshot_created_at": "timestamp",
  "source_freshness": {},
  "rule_versions": {},
  "metric_versions": {},
  "data_quality": {},
  "data": {}
}
```

## 10. CLI

The CLI is both a human fallback and a universal agent interface.

Example shape:

```text
engintel sync
engintel snapshot create
engintel dashboard get --snapshot <id> --json
engintel team brief "TEAM" --snapshot <id> --format json
engintel team get "TEAM" --snapshot <id> --format json
engintel feature get BX-148 --snapshot <id> --json
engintel person get "Jordan Lee" --snapshot <id> --json
engintel metric get build-cycle --team BX --snapshot <id> --json
engintel flags list --state active --unread --json
engintel flags view <flag-id>
engintel render dashboard --snapshot <id> --format html
engintel backup status --json
```

Requirements:

- JSON output by default for agent-facing commands
- Optional concise human-readable output
- Stable exit codes
- Bounded result sizes
- Explicit pagination
- No ANSI decoration in JSON mode
- Idempotent read commands

## 11. MCP server

MCP tools wrap the same service operations as the CLI.

Tool descriptions must:

- State whether the operation is deterministic
- Declare required identifiers
- Explain snapshot behavior
- Describe pagination and bounds
- Identify side effects

Flag viewed, unread, snoozed, and understood operations modify only local system state. Jira and GitHub remain read-only.

MCP resources may expose:

- Metric definitions
- Flag definitions
- Presentation schemas
- Team configuration
- Current source freshness

## 12. Skills and prompts

Skills orchestrate complete workflows without owning authoritative logic.

### 12.1 Dashboard skill

1. Check freshness.
2. Synchronize when policy requires.
3. Create or reuse a snapshot.
4. Call `get_dashboard`.
5. Invoke the deterministic Dashboard renderer.
6. Present the snapshot ID and freshness.
7. Offer focused follow-up operations.

### 12.2 Team-status-prep skill

1. Resolve team identity.
2. Refresh and create a snapshot only for explicitly current status.
3. Query Dashboard, Team, metric, Feature, and Attention presentations in order.
4. Render health, completed, in-progress, ready-for-build, concerns, and questions
   using the fixed meeting contract.
5. Preserve empty states, evidence links, freshness, and analysis boundaries.

### 12.3 Individual-context-prep skill

1. Resolve person and identities.
2. Verify roster provenance and identity completeness at the snapshot.
3. Query direct and rolled-up contributions, blockers, and evidence.
4. Preserve evaluated and suppressed context-signal rules.
5. Render the fixed conversation-preparation contract without scoring.

### 12.4 Prompt policy

Prompts may guide:

- Summarization
- Comparative explanation
- Pattern identification
- Investigation questions

Prompts must not define metric formulas, health outcomes, severity, attribution rules, or required presentation fields.

## 13. Renderers

Initial formats:

- Interactive HTML
- Markdown
- Structured JSON

All formats consume the same presentation-data schema.

HTML supports richer drill-down. Markdown provides a portable fallback for agents that cannot display interactive artifacts. JSON supports agent reasoning and third-party renderers.

Use golden tests to verify:

- Required fields
- Ordering
- Jira keys and links
- Empty states
- Flag severity
- Snapshot metadata
- Equivalent content across formats

## 14. Session behavior

Recommended agent-session sequence:

1. Agent invokes a skill or CLI/MCP operation.
2. System evaluates freshness.
3. System syncs only when required or requested.
4. System creates a stable snapshot.
5. Agent presents a deterministic rendering.
6. Follow-up questions reuse the snapshot.
7. Agent performs focused analysis through bounded tools.
8. Agent explicitly refreshes and creates a new snapshot if the user requests current data.

The agent should state when an answer uses a different snapshot from the prior output.

## 15. Security

- Keep Jira and GitHub credentials outside source control.
- Prefer OS keychain or a dedicated encrypted secret store.
- Give agent credentials read-only domain access by default.
- Scope credentials to configured teams and sources.
- Individually identify and revoke credentials.
- Audit MCP and CLI mutations to local flag state.
- Redact secrets and sensitive raw payload fields from logs.
- Avoid allowing arbitrary SQL or filesystem access through MCP.
- Validate all renderer inputs against versioned schemas.

## 16. Testing strategy

### Unit tests

- Metric formulas
- Flag fingerprints
- Severity and hysteresis
- Attribution
- Hierarchy roll-up
- Snapshot boundaries

### Contract tests

- CLI and MCP equivalence
- Schema compatibility
- Pagination
- Error semantics

### Integration tests

- Jira fixture ingestion
- GitHub fixture ingestion
- Reconciliation
- Deletion and rename behavior

### Golden tests

- Dashboard
- Team
- Feature
- People
- Individual
- Metrics
- Attention
- Configuration

### Recovery tests

- Backup creation
- Restore into an empty environment
- Integrity verification
- Snapshot and historical-query reproduction after restore

## 17. Phased implementation

### Phase 0: Foundation

- Python project and packaging
- Configuration schemas
- SQLite schema and migrations
- Secret handling
- CLI skeleton
- Fixture-based tests

### Phase 1: Vertical slice

- Jira IBR board ingestion
- Raw payload archive
- Jira hierarchy
- Teams and people mappings
- Snapshot creation
- Dashboard query
- Dashboard JSON and Markdown rendering
- Manual encrypted backup

Exit criterion: An external agent can run one command or MCP workflow and present a reproducible Dashboard from a named snapshot.

### Phase 2: GitHub and attribution

- GitHub ingestion
- Jira-to-GitHub linking
- Direct and rolled-up contribution model
- Feature and Individual queries
- Feature and Individual renderers

### Phase 3: Metrics and flags

- Versioned independent metric and signal definitions
- Immutable per-snapshot evaluation records, including non-triggering evaluations
- Team and Feature flow, load, dependency, and visibility signals
- Health derivation from the highest-severity active flag
- GitHub collaboration signals after repository scope is confirmed
- Safeguarded individual-context signals after identity coverage is reliable
- Attention and Metrics renderers

### Phase 4: Agent interfaces

- MCP server
- Cross-agent skills
- Analysis prompts
- CLI/MCP contract tests
- Context-bounded query operations

### Phase 5: Operational hardening

- Scheduled incremental synchronization
- Reconciliation
- Automated encrypted backups
- Restore verification
- Performance and observability
- HTML renderers

## 18. Key architecture decisions to validate

Before implementation, validate:

1. Local-only versus remotely reachable MCP server
2. Backup destination and recovery objective
3. Jira authentication method for the running service
4. GitHub organization and repository scope
5. Whether SQLite is sufficient for expected history volume
6. Preferred skill packaging for Codex and Claude Code
7. Whether initial renderers need HTML, or Markdown plus JSON is enough for the first vertical slice

The approved default is:

- Local stack
- Python
- SQLite
- MCP plus CLI
- Markdown and JSON first
- Interactive HTML after the deterministic data contracts stabilize

## 19. Private repository and release model

All solution components live in one private GitHub repository:

```text
Source code
Database migrations
Configuration schemas and templates
MCP server
CLI
Codex adapter
Claude Code adapter
Skills
Prompts
Renderers
Install, upgrade, repair, validation, and uninstall commands
Export and import commands
Tests and documentation
```

Runtime data, raw payloads, secrets, rendered output, and backups are excluded from Git.

Every installation records:

- Repository origin
- Installed Git commit
- Application version
- Database migration revision
- Configuration-schema version
- Installed agent adapters
- Created files and links
- Modified configuration entries

Tags may identify stable releases, while the installed commit remains the authoritative version identifier.

## 20. Portability

The local stack should minimize host assumptions.

Recommended approach:

- Support macOS and Linux initially.
- Use `uv` to install a pinned Python runtime and dependencies where practical.
- Use platform-standard user data and configuration directories, resolved at runtime.
- Avoid absolute paths in committed configuration.
- Keep runtime paths overridable through one documented environment variable or config file.
- Provide shell-independent Python entry points for lifecycle operations.
- Avoid requiring Docker for the initial release.

Docker may be offered later, but it should not be required for agent integration with host CLI and MCP configuration.

## 21. Agent adapters

Maintain an agent-neutral core plus thin adapters:

```text
adapters/
├── codex/
│   ├── skills/
│   ├── mcp-template/
│   └── adapter.py
└── claude-code/
    ├── skills/
    ├── mcp-template/
    └── adapter.py
```

The adapter interface supports:

- Detect
- Install
- Validate
- Repair
- Upgrade
- Uninstall

Adapters discover the supported agent's current configuration conventions at install time. They must not assume that all versions use the same path or file format.

Each adapter records exact mutations in the installation manifest. Removal uses that manifest rather than broad directory deletion.

Agent-neutral workflows should be authored once and transformed or wrapped into agent-specific skill packaging. Adapter-specific files only handle discovery, metadata, and invocation conventions.

## 22. Lifecycle CLI

Provide one top-level lifecycle command:

```text
engintel install
engintel validate
engintel repair
engintel upgrade
engintel uninstall
engintel version
```

Suggested behaviors:

### Install

1. Detect repository and version.
2. Check prerequisites.
3. Resolve local config, data, cache, log, and backup paths.
4. Install the Python package and `engintel` entry point.
5. Initialize configuration from templates.
6. Run database migrations.
7. Detect Codex CLI and Claude Code.
8. Ask which detected adapters to install when not explicitly specified.
9. Install MCP and skill adapters.
10. Write the installation manifest.
11. Run validation and a smoke test.

Supported non-interactive form:

```text
engintel install --agents codex,claude-code --yes
```

### Validate

Validate:

- CLI availability
- Database integrity
- Migration state
- Configuration
- Jira and GitHub authentication
- MCP startup
- Agent discovery
- Skill presence
- Write access to runtime paths
- Latest backup state

Return structured JSON with `--json`.

### Repair

Reapply missing generated files and adapter entries without overwriting user-managed configuration.

### Upgrade

Recommended command:

```text
engintel upgrade --from-checkout
```

Flow:

1. Require a clean or explicitly accepted repository state.
2. Identify current and target commits.
3. Check supported upgrade path.
4. Create a pre-upgrade backup.
5. Stage generated files.
6. Apply database migrations transactionally where possible.
7. Update agent adapters.
8. Run validation and smoke tests.
9. Atomically update the installation manifest.

Fetching or pulling Git changes should be an explicit separate step unless the user chooses an `--update-repository` option. This avoids an installer silently changing a working tree.

### Uninstall

Default:

```text
engintel uninstall --agents codex,claude-code
```

This removes only agent registrations, generated adapter files, and installed entry points recorded in the manifest.

Persistent data, backups, exports, secrets, and the repository checkout remain.

Destructive removal requires explicit targets:

```text
engintel uninstall --purge-data --purge-secrets
```

The command must preview the resolved paths and request confirmation before purge.

## 23. Installation manifest

Store a versioned manifest outside the repository in the local configuration directory.

Example fields:

```json
{
  "manifest_version": "1",
  "installed_commit": "git-sha",
  "application_version": "0.1.0",
  "database_revision": "revision-id",
  "repository_path": "/resolved/path",
  "runtime_paths": {},
  "agents": {
    "codex": {
      "installed": true,
      "created_paths": [],
      "modified_entries": []
    },
    "claude-code": {
      "installed": true,
      "created_paths": [],
      "modified_entries": []
    }
  }
}
```

Never store credentials in this manifest.

## 24. Export and import architecture

### 24.1 Portable bundle

Use a directory or compressed archive with:

```text
engintel-export/
├── manifest.json
├── README.md
├── checksums.sha256
├── config/
│   ├── teams.yaml
│   ├── workflows.yaml
│   ├── metrics.yaml
│   └── flags.yaml
├── data/
│   ├── teams.ndjson
│   ├── people.ndjson
│   ├── jira-issues.ndjson
│   ├── jira-events.ndjson
│   ├── github.ndjson
│   ├── metrics.ndjson
│   ├── flags.ndjson
│   └── snapshots.ndjson
├── tables/
│   └── selected-human-readable.csv
└── reports/
    └── export-summary.md
```

The manifest records:

- Export ID and timestamp
- Source installation version
- Schema and migration versions
- Included datasets and time ranges
- Record counts
- Checksums
- Whether raw payloads are included
- Whether the archive is encrypted

### 24.2 Export CLI

```text
engintel export
engintel export --output /path/to/export
engintel export --format bundle
engintel export --include-raw
```

If `--output` is omitted, prompt:

```text
Where should the export be stored?
```

For autonomous agents, the skill must relay this question to the user rather than select an arbitrary location.

Do not overwrite an existing destination without explicit confirmation or `--overwrite`.

### 24.3 Import CLI

```text
engintel import
engintel import --input /path/to/export
engintel import --dry-run --input /path/to/export
```

Import sequence:

1. Resolve the user-selected source.
2. Verify checksums.
3. Validate versions.
4. Produce a dry-run plan.
5. Report duplicates, conflicts, migrations, and expected record counts.
6. Create a pre-import backup.
7. Import immutable events idempotently.
8. Rebuild projections, metrics, flags, and snapshots as needed.
9. Produce JSON and Markdown reports.

### 24.4 Secrets

Standard export excludes:

- Jira tokens
- GitHub tokens
- Agent credentials
- Backup credentials
- Local absolute paths

If secret portability is later required, implement a separate encrypted secret bundle with explicit opt-in and password or key handling.

## 25. Lifecycle testing

Add automated tests for:

- Clean install
- Repeated install
- Codex-only install
- Claude-Code-only install
- Both adapters together
- Repair after one generated file is removed
- Upgrade across each supported migration boundary
- Failed upgrade rollback
- Adapter-only uninstall
- Default uninstall preserving data
- Explicit purge path confirmation
- Export into a user-selected path
- Import dry run
- Import into an empty installation
- Duplicate import idempotency
- Export/import round trip
- Backup and restore after upgrade
