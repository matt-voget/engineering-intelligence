# Data refresh resilience execution plan

**Status:** In progress
**Owner:** titan
**Started:** 2026-08-23

## Objective

Complete the live Engineering Intelligence refresh and make long-running source
pulls observable and resilient to transient network failures and interrupted
agent sessions.

## Acceptance criteria

- The live pull reaches a terminal receipt with every configured Jira and GitHub
  source accounted for.
- Durable progress reports the current source, completed/total sources, updated
  timestamp, and per-source record counts while a refresh is active.
- Transient GitHub transport failures are retried with bounded backoff.
- A failed GitHub repository does not discard progress from other repositories;
  failures are visible and retryable.
- An interrupted refresh can be identified as stale and resumed without
  needlessly repeating completed sources.
- Targeted tests plus the full test suite pass, and changes are committed and
  pushed.

## Checkpoints

- [x] Inspect the interrupted run and verify persisted progress/data.
  - Evidence: Jira completed 9/9 sources; GitHub completed 57 repositories.
  - Evidence: durable progress stopped at 66/347 sources at 20:31:29Z.
  - Finding: GitHub retries HTTP 429/5xx responses but not transport errors;
    refresh aborts on the first repository exception and has no resume command.
- [x] Add transport retries and repository-level failure isolation.
  - GitHub transport exceptions now use the existing bounded exponential backoff.
  - GitHub primary-rate-limit responses wait for the advertised reset time.
  - Repository failures are recorded and the remaining repositories continue.
- [x] Add stale-run detection and source-level resume behavior.
  - `engintel refresh run --resume` reuses sources marked complete in the latest
    durable progress receipt and retries the interrupted/failed source onward.
- [x] Verify targeted and full tests.
  - Evidence: Ruff passes; 9 targeted tests and all 118 tests pass.
- [ ] Resume and complete the live refresh, sending periodic checkpoints.
- [ ] Generate the terminal receipt/snapshot and report exact source totals.

## Exact next action

Commit and push the resilience changes, then run the live refresh with
`--resume` and monitor its durable progress receipt through completion.
