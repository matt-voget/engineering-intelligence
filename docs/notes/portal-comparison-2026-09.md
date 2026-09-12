# External metrics portal vs Engineering Intelligence — known deviations

Recorded 2026-09-12 from a record-level reconciliation (PRs joined on repository + number,
issues on Jira key) over 1 Jul – 11 Sep 2026 across seven engineering teams. The portal is
an internal dashboard system the operator cannot change; these notes exist so future
comparisons start from the known offsets instead of rediscovering them. Names, hosts and
account identifiers are deliberately omitted here; the operator keeps the detailed version
privately.

## Same arithmetic, different populations

On issues both systems see, build-cycle days agree within about one day. Pickup and review
hours use the same definitions on both sides (creation → first non-author review; first
review → merge; selected by merge date). Every deviation below is about *which records*
each side counts and *whom* it credits.

## Pull requests

| Cause | Size in the window | Direction | What EI does about it |
|---|---|---|---|
| The portal's GitHub feed omits six of the organization's repositories | ≈130 PRs | EI counts, portal cannot | Nothing; when comparing, drop those repositories from the EI side |
| PR names no Jira key, so the portal files it under "Unassigned" (about a third of merged PRs); EI credits the author | ≈200 PRs on the seven teams | EI team vs portal Unassigned | `metrics github-pr --attribution jira-team` reproduces the portal's rule with author fallback; `jira-team-strict` reproduces the Unassigned bucket exactly |
| Author's configured team differs from the Jira key's team (cross-team work) | ≈140 PRs | Different team on each side | Same modes; the author view remains the report contract |
| Authors not present in the configured rosters (contractors and people not officially on a team) | ≈40 PRs | Portal counts, EI cannot | Deliberately not added; the operator confirmed they are not officially on the teams |
| Busy repositories truncated by `max_pull_requests_per_repository` (coverage of the busiest repository started six weeks into the lookback under the 500 default) | ≈340 merged PRs on that repository | Portal counts, EI missed | Raise the cap for large repositories and re-sync |

## Jira issues

| Cause | Size | Direction | What EI does about it |
|---|---|---|---|
| The portal measures each IBR-linked child story's cycle; EI rolls children into the parent and measures the parent | ≈90 portal-only issues | Portal counts children, EI counts parents | `metrics build-cycle --children` adds the child population (sub-tasks excluded) |
| Issues in a team's own Jira projects with no Team field set; the portal attributes only by Team field | ≈100 issues | EI counts, portal cannot | Jira hygiene: set the Team field; nothing to change in EI |
| Throughput: the portal counts resolved issues of several types per developer week by Team field; EI counts completed tracked cycles | level gap on three teams | — | Compare the child population against the portal's throughput, not the parent view |

## Week-over-week behaviour (11 weeks)

Merged-PR volume trends correlate r 0.6–0.9 for six of seven teams despite level gaps, so
the attribution difference is a constant offset. Pickup and review weekly medians agree
only where the PR populations overlap. Cycle and completion trends correlate for four teams
once populations are mapped correctly.

## Method note

When reading the report's embedded `cycle-records` blobs, each blob precedes its team section
anchor; map a blob to the *next* `team-<id>-ibr` section, not the previous one.
