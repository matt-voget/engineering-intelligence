# Expand report to all confirmed Jira teams

**Status:** Completed
**Owner:** titan
**Started:** 2026-09-01
**Approved:** 2026-09-01 by Matt Voget

## Objective

Expand the private installation from its original configured subset to every confirmed
Jira team and the user-confirmed people-to-team mappings, then generate a fresh report
whose team, person, finder, and metric views all use that expanded snapshot scope.

## Constraints

- Keep organization names, Jira IDs, account IDs, rosters, and JQL outside git.
- Preserve confirmed Jira/GitHub identities; never guess a GitHub login.
- Support secondary membership through the same stable person/Jira identity.
- Include confirmed teams omitted from the roster message with explicit empty rosters.
- Add and validate one pinned Jira team-field query per configured team.
- A new report requires a complete fresh refresh and source receipt.

## Acceptance criteria

1. Private team configuration contains every confirmed Jira team in deterministic order.
2. Every supplied person resolves uniquely to Jira evidence and carries exactly the
   supplied memberships; omitted teams have explicit empty rosters.
3. Existing confirmed GitHub logins are preserved and unknown logins remain null.
4. Every team has a validated `team-field-TEAM_ID` Jira query using its observed UUID.
5. Configuration validation and source coverage checks pass before refresh.
6. Fresh refresh completes every configured Jira/GitHub source and pins a new snapshot.
7. The self-contained report contains all teams and unique people and passes structural
   and repository verification before delivery.

## Completion evidence

1. Private configuration validates with 16 teams, 37 unique people, and 42
   memberships. Edge Stack and Platform Team have explicit empty rosters because the
   approved mapping supplied no members for them.
2. All 16 pinned Jira team-field queries validated and returned evidence.
3. Refresh `aedc468e-1a6f-4b5f-ae0d-f7e4fed3eca2` completed all 355 sources (18 Jira
   and 337 GitHub) and pinned snapshot `refresh-20260901T194024Z` / snapshot ID
   `7ba421f7-7083-4481-ace3-eccde071c771`.
4. The generated report contains 16 team sections, 37 individual sections, and 120
   feature views. It is self-contained with no external scripts or styles, and all
   inline JavaScript parses successfully.
5. Repository verification passed: Ruff clean and 143 tests passed.

## Follow-up

Report materialization exposed repeated full-snapshot scans in team-work and
individual/feature evidence resolution. Consolidate these into shared snapshot-backed
materializations so expanded reports do not repeat equivalent queries per route.
