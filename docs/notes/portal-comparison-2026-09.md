# Gravitee Operations Portal vs Engineering Intelligence — known deviations

Recorded 2026-09-12 from a record-level reconciliation (PRs joined on repository + number,
issues on Jira key) over 1 Jul – 11 Sep 2026 for Matt's teams (A2A, LLM, DevEx, Builder
Experience, Observability, Edge, Foundations). The Portal (gravitee.info) cannot be changed by
us; these notes exist so future comparisons start from the known offsets instead of
rediscovering them. Comparison page: Claude artifact "Portal vs Engineering Intelligence".

## Same arithmetic, different populations

On issues both systems see, build-cycle days agree within about one day (A2A 9.6 vs 10.0,
Edge 9.3 vs 9.3, Foundations 7.8 vs 7.4). Pickup and review hours use the same definitions
on both sides (creation → first non-author review; first review → merge; selected by merge
date). Every deviation below is about *which records* each side counts and *whom* it credits.

## Pull requests

| Cause | Size in the window | Direction | What EI does about it |
|---|---|---|---|
| Portal's Fivetran GitHub feed does not include `gravitee-gamma-module-edge`, `gravitee-gamma-edge-daemon`, `gravitee-platform-docs`, `gravitee-circleci-orb`, `gravitee-gamma-module-esm`, `gravitee-reactor-edge` | ≈130 PRs | EI counts, Portal cannot | Nothing; when comparing, drop these repositories from the EI side |
| PR has no Jira key in its title, so the Portal files it under "Unassigned" (676 of 2,081 merged PRs in the window); EI credits the author | 199 PRs on Matt's teams (LLM 57, Foundations 55, BX 32, DevEx 28, A2A 15, Obs 12) | EI team vs Portal Unassigned | `attribution.mode: jira-team` credits a PR to the team of its Jira key when one exists (Portal behaviour); author attribution stays the default |
| Author's configured team differs from the Jira key's team (cross-team work: Builder Experience authors on Foundations/Edge keys, Guillaume Cusnieux on LLM/A2A keys, Ouahid Khelifi on Edge keys) | ≈140 PRs | Different team on each side | Same `jira-team` mode reproduces the Portal's choice; the author view remains available |
| Authors absent from `teams.yaml` (konrad-krol-incubly, ctschacher, MateuszJozwiak-Incubly on DevEx work; remibaptistegio on Observability; jhaeyaert, jgiovaresco on Foundations) | 37 PRs | Portal counts, EI cannot | Add to the roster once Matt confirms team membership |
| Busy repositories truncated by `max_pull_requests_per_repository` (gravitee-api-management coverage started 12 Aug with a 500 cap) | 339 merged PRs on that repo, 61 on Matt's teams | Portal counts, EI missed | Raise the cap for large repositories and re-sync |

## Jira issues

| Cause | Size | Direction | What EI does about it |
|---|---|---|---|
| The Portal measures each IBR-linked child story's cycle; EI rolls children into the parent epic and measures the parent | 91 Portal-only issues (A2A 29, LLM 19, Foundations 17, Edge 15, Obs 8, DevEx 3) | Portal counts children, EI counts parents | Child-story build-cycle population alongside the parent view |
| Team's own project keys (GKO, FOUND, AIAM, OBS) with no Team field set; the Portal attributes only by Team field | ≈100 issues (LLM 33, DevEx 32, Foundations 24, A2A 13) | EI counts, Portal cannot | Jira hygiene: set the Team field; nothing to change in EI |
| Throughput: the Portal counts resolved Story / FR / FDI / Technical Task / bugs per developer week by Team field; EI counts completed tracked cycles | level gap on LLM, DevEx, Edge | — | Compare EI's child-story population against the Portal's throughput, not the parent view |

## Week-over-week behaviour (11 weeks from 29 Jun)

Merged-PR volume trends correlate r 0.6–0.9 for six of seven teams despite level gaps, so the
attribution difference is a constant offset. Pickup and review weekly medians agree only where
the PR populations overlap (LLM, Builder Experience, Observability review). Cycle and completion
trends correlate for Foundations (0.93 / 1.0), Edge, LLM and DevEx once populations are mapped
correctly.

## Method note

When reading the report's embedded `cycle-records` blobs, each blob precedes its team section
anchor; map a blob to the *next* `team-<id>-ibr` section, not the previous one.
