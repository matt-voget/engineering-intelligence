# Expand report to all confirmed Jira teams

**Status:** Approved — implementation in progress
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

## Checkpoints

1. **In progress:** Resolve the approved names to unique Jira identities and construct
   private configuration updates.
2. Validate every new team query and private configuration.
3. Run a full incremental refresh and render the new snapshot.
4. Verify, deliver, and record final coverage/timing evidence.

## Exact next action

Resolve ambiguous cross-team names from existing Jira evidence, preserving known
identities and leaving unconfirmed GitHub mappings empty.
