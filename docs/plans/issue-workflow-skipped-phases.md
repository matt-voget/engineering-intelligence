# Jira workflow-aware skipped phases

## Goal

Make skipped-phase reporting depend on the live Jira workflow statuses assigned to each
project and issue type, preserve that evidence in snapshots, and add an Issue Finder
multi-select for skipped phases.

## Acceptance criteria

- Each Jira source run records the status set Jira exposes for every project/issue-type
  pair represented by that run.
- Snapshot-pinned team-work queries use the workflow observation from the source run
  pinned by the snapshot. A phase absent from that workflow is never reported as skipped.
- Existing snapshots without workflow observations remain readable through the legacy
  phase sequence.
- Issue Finder offers an All/None multi-select whose values are the skipped phase names;
  selecting values keeps rows that skipped at least one selected phase.
- Tests cover workflow-specific phase applicability, observation persistence, snapshot
  lookup, and report filtering.
- A fresh complete refresh and report demonstrate the behavior against live Jira.

## Checkpoints

1. **Complete:** Audited 21 live Jira projects, their project/issue-type status sets,
   workflow-scheme assignments, and 50 live workflow definitions. Confirmed the current
   fixed six-phase assumption produces false skipped-phase labels for workflow families
   that omit review, test, or documentation phases.
2. **Next:** Add snapshot-safe Jira workflow observations and consume them in build-cycle
   calculation.
3. Add the Issue Finder skipped-phase multi-select and renderer tests.
4. Run repository validation, complete a fresh refresh, generate the report, inspect the
   live result, commit, push, and deliver it.
