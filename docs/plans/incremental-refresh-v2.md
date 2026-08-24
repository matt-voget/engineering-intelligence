# Incremental refresh v2 design

**Status:** Approved — implementation in progress
**Owner:** titan
**Started:** 2026-08-24
**Approved:** 2026-08-24 by Matt Voget

## Objective

Replace the current source-sequential refresh with a durable, incremental, observable
pipeline that safely reuses normalized Jira and GitHub data, respects provider limits,
uses bounded concurrency, fails explicitly, and exposes progress that any agent or
human-facing client can consume. The design is channel-agnostic; Telegram, a terminal,
an IDE, or a scheduled service are all consumers of the same event stream and state.

## Confirmed current-state problems

- Jira lists every issue in every scope and bulk-fetches status changelogs for every
  listed issue on every run, even when the issue's source `updated` value is unchanged.
- GitHub lists PRs in the lookback window, then fetches every PR's commits and reviews
  again. The PR list already provides `updated_at`, but it is not used to skip unchanged
  detail.
- All 346 configured sources run sequentially. One large or rate-limited repository
  blocks every source behind it.
- Progress changes only at stage/source boundaries. There is no item/page counter,
  request count, rate-limit state, or heartbeat during a long source or CPU-heavy stage.
- The refresh catches `Exception`, not interruption/termination. The paused process
  ignored SIGINT, required SIGTERM, and left its progress receipt falsely `running`.
- Resume infers completed work from the latest event list. It does not validate a
  durable execution-plan identity or include reused source receipts in the terminal
  receipt.
- A detached shell process has no owned, generic supervision contract. If it vanishes,
  consumers must notice stale timestamps themselves.

## Design principles

1. **Normalized data is the cache.** Reuse it only when provider metadata proves a
   record unchanged; never use cache age alone as proof.
2. **Freshness and reconciliation are separate.** Incremental runs fetch changes;
   scheduled reconciliation runs verify membership, deletions, and a bounded historical
   overlap.
3. **One durable run model.** CLI, skill, IDE, scheduler, and chat integrations consume
   the same state and JSONL events.
4. **Provider-aware concurrency.** Parallelism is bounded by one shared limiter per
   provider, not scattered sleeps in workers.
5. **No silent terminal state.** Every normal exit, exception, cancellation, or handled
   signal produces a terminal state and non-zero exit when incomplete. Unhandleable
   death is exposed as an expired lease on the next status/read operation.
6. **Snapshot only from a complete plan.** Every planned source must have a compatible
   successful source result before snapshot creation.

## Proposed architecture

```text
refresh plan
  -> durable run + source/task manifest
  -> Jira planner ---------------------> bounded Jira workers ---+
  -> GitHub planner -------------------> bounded GitHub workers --+-> writer/checkpoints
  -> complete-source coverage gate
  -> snapshot -> signals -> individual cache -> optional backup
  -> terminal receipt

state/events.jsonl <--- all stages, workers, limiter waits, heartbeats, terminal state
```

### 1. Durable run and task manifest

Create a database-backed (or atomically persisted) run manifest before network work:

- run ID, config hashes, mode, started/updated/lease-expiry timestamps;
- status: `planned|running|cancelling|cancelled|failed|stale|completed`;
- every configured source and its state, attempt, last cursor/checkpoint, counters,
  previous compatible ingestion run, and final ingestion run;
- planned source count and config identity.

`--resume RUN_ID` resumes only a compatible manifest. A plain new run never silently
borrows an unrelated latest event list. Reused source results appear explicitly in the
terminal receipt.

### 2. Jira incremental strategy

For each board/query scope:

1. Enumerate current scope membership using minimal fields: ID, key, `updated`, parent,
   and any fields required to discover hierarchy.
2. Compare each issue's provider `updated` value with normalized
   `last_source_updated_at`.
3. Normalize full list payloads for new/changed issues only. If a minimal listing cannot
   supply all report fields, batch-fetch full detail only for that delta.
4. Bulk-fetch changelogs only for new/changed issues. Changelog rows remain idempotent.
5. Record unchanged issues as reused in the new scope observation so snapshot membership
   remains current.
6. Reconcile issues missing from the current scope and preserve existing deletion/
   membership semantics.

Run hierarchy discovery breadth-first in configured batches. Deduplicate issue IDs
across overlapping Jira scopes within the run so one changed issue is fetched and
normalized once, then attached to every observed scope.

### 3. GitHub incremental strategy

For each configured repository:

1. List PRs newest-first with an overlap from the last successful source high-water
   mark. Keep the configured periodic re-verification window as a safety net.
2. Compare list `updated_at` and the normalized current PR version. When unchanged:
   reuse the PR version, commit links, commits, reviews, and Jira relationships without
   new detail calls.
3. For new or changed open PRs, fetch commits and reviews and apply idempotently.
4. For merged PRs, treat commits as immutable. Re-fetch details only when the PR's
   provider `updated_at` advances or during periodic reconciliation.
5. Preserve the bounded historical scan for late edits, force-pushes before merge, and
   review changes. Record exactly which window/mode supplied coverage.

Phase 1 stays on REST and removes redundant N+2 detail calls. GraphQL batching is a
later measured optimization only if REST request counts remain the bottleneck.

### 4. Rate limiting and retries

Add one concurrency-safe limiter per provider:

- inspect `Retry-After` and provider remaining/reset headers on every response;
- proactively pause dispatch before exhaustion, not after a failed request;
- use bounded exponential backoff with jitter for transport errors and retryable 5xx;
- cap attempts and wait duration, then fail the task explicitly;
- publish `rate_limit_wait` events with provider, reason, resume time, remaining budget,
  and affected worker count;
- never hold a database write transaction while sleeping or making a network request.

Conservative defaults: Jira 2 concurrent requests, GitHub 4 repository workers. Make
them configurable within hard safety bounds. Adapt downward on secondary limits.

### 5. Fetch/apply concurrency

- Workers perform network reads and normalization outside write transactions.
- A bounded writer queue applies short, atomic batches to SQLite, avoiding concurrent
  writer contention.
- Sources complete independently; a failed source does not stop unrelated workers, but
  the coverage gate blocks snapshot creation and returns a failed terminal run.
- Queue bounds provide backpressure so large repositories cannot exhaust memory.

### 6. Generic progress and lifecycle contract

Emit newline-delimited JSON events to stdout immediately and persist them atomically:

```json
{
  "schema_version": "2",
  "run_id": "...",
  "at": "2026-08-24T16:30:00Z",
  "stage": "jira_ingest",
  "source": "jira:query:team-field-a2a",
  "status": "running",
  "unit": "issues",
  "processed": 123,
  "total": 234,
  "new": 4,
  "updated": 19,
  "unchanged": 100,
  "requests": 8,
  "message": "Ingesting Jira updates — 123/234 issues checked; 19 updated"
}
```

Required event kinds: `run_started`, `stage_started`, `source_started`, `progress`,
`rate_limit_wait`, `retrying`, `heartbeat`, `source_completed`, `source_failed`,
`stage_completed`, `run_cancelled`, `run_failed`, and `run_completed`.

- Emit progress at page/batch boundaries and at least every 15 seconds while active.
- A lightweight heartbeat thread updates the run lease during long network waits and
  CPU stages.
- `engintel refresh status RUN_ID --format json` returns state and detects an expired
  lease. It atomically marks an orphaned `running` run `stale` and exits non-zero.
- `engintel refresh watch RUN_ID --format jsonl` tails persisted events; consumers turn
  these into terminal, IDE, web, Slack, Telegram, or other UX.
- Human output is derived from structured counters; messages are not parsed as state.

### 7. Cancellation, failure, and supervision

- Register SIGINT/SIGTERM handlers. Stop dispatching new work, let the current atomic
  write finish, write `cancelled` state/receipt, release the lock, and exit non-zero.
- Wrap the top-level run in `try/finally`; cancellation is handled separately from
  ordinary exceptions. Do not rely on `except Exception` for lifecycle correctness.
- SIGKILL, OOM, host loss, and agent death cannot be handled in-process. The durable
  lease makes them observable. Any later `status`, `run`, or `resume` command performs
  stale-run reconciliation first.
- Provide `engintel refresh resume RUN_ID`; it retries failed/stale/incomplete tasks and
  reuses compatible completed tasks.
- Scheduled/background operation uses the existing owned launchd/systemd path, enhanced
  with restart policy and exit-status logging. Interactive agents run synchronously or
  use `start` + `watch`; ad-hoc detached shell commands are not the supported contract.
- Notifications are adapters outside the refresh core. They consume events/terminal
  state; the core never assumes Telegram.

## Modes

- `incremental` (default): provider-metadata delta plus safety overlap.
- `reconcile`: full membership and configured historical-window verification; scheduled
  periodically (for example weekly), not for every report.
- `full`: explicit recovery/audit mode that re-reads all configured bounded history.

Every mode still creates a complete snapshot across every configured source. “Reuse”
means an explicitly verified compatible source result, never a partial snapshot.

## Acceptance criteria

- An unchanged second refresh performs no Jira changelog fetches for unchanged issues
  and no GitHub commit/review calls for unchanged PRs.
- A changed Jira ticket, changed/open PR, new PR, new review, and newly merged PR are all
  detected within the configured overlap.
- Periodic reconciliation detects membership removal and late historical changes.
- No provider request is dispatched while the shared limiter says the budget is
  exhausted; retry/wait state is visible in events.
- Configured workers demonstrate bounded parallelism without SQLite lock failures or
  unbounded memory growth.
- Progress includes human-readable item counters at least every 15 seconds and can be
  consumed entirely through JSONL/state without Telegram.
- SIGINT and SIGTERM produce `cancelled` terminal receipts and non-zero exit. Simulated
  process death becomes `stale` after lease expiry and is detectable/resumable.
- A failed source prevents snapshot creation, produces a failed receipt, and preserves
  other completed source checkpoints.
- Resume validates run/config compatibility and produces a terminal receipt containing
  both reused and newly completed source results.
- Benchmarks on the live 346-source configuration record API requests, elapsed time,
  records checked/new/updated/reused, rate-limit waits, and peak memory. Incremental
  performance targets will be set from the first baseline rather than invented.
- Migration, query, refresh, interruption, rate-limit, concurrency, CLI, scheduler,
  and end-to-end snapshot integrity tests pass.

## Delivery checkpoints

1. Capture a request/timing baseline from the paused receipt and targeted fixture runs.
2. **Completed 2026-08-24:** Add durable run/task state, lifecycle handling, lease,
   events, status/watch/resume (`8d1b112` plus the following checkpoint).
3. **Completed 2026-08-24:** Jira board/query membership uses minimal fields, compares
   provider `updated` timestamps, batch-hydrates only new/changed issues, preserves
   observations for reused issues, and fetches changelogs only for the delta.
   Checked/new/updated/reused counters are persisted and emitted in refresh events.
4. **Completed 2026-08-24:** Unchanged PRs reuse normalized commit/review detail,
   GitHub delta counters are persisted, rate-limit/retry waits emit generic events,
   and shared provider request limiters enforce Jira 2 / GitHub 4 defaults.
5. **In progress:** GitHub repositories run through four bounded workers with serialized
   durable progress and aggregated failures. Explicit incremental/reconcile/full modes
   are persisted and resume-compatible; reconcile/full re-read unchanged details and
   changelogs idempotently. Jira cross-scope planning remains before Jira sources can
   safely run concurrently.
6. Update the skill and scheduler to use the generic run/watch contract.
7. Run fault injection and live before/after benchmarks; tune safe defaults.
8. Resume the report refresh through the new path, render, validate, and deliver.

## Approved decisions

1. Approve `incremental` as the report default, with `reconcile` scheduled periodically
   and `full` reserved for audit/recovery.
2. Approve conservative initial concurrency of Jira 2 / GitHub 4, adjusted only from
   observed rate-limit and stability data.
3. Approve durable run/task state plus structured JSONL as the generic contract, with
   channel notifications implemented as optional adapters.
