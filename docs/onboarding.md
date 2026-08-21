# Agent-led onboarding

This is the playbook an agent follows to configure Engineering Intelligence for a
new user. The design goal: **the user supplies credentials, their Jira hostname, the
IBR board choice, and their team names — the agent discovers everything else and
proposes it for confirmation.** Never guess; every discovered value is verified
against the live instance and confirmed by the user before it is written.

All Jira and GitHub calls below are read-only. Never print credential values,
and never ask the user to paste a credential into chat.

## What the user provides

1. **Jira**: the instance hostname exported as the variable named by
   `jira.host_env` (default `ATLASSIAN_HOST`), their Atlassian account email,
   and an API token exported as the variable named by `jira.token_env` (default
   `ATLASSIAN_API_TOKEN`). A configured `jira.base_url` remains the fallback.
2. **GitHub**: a personal access token with read access to the organization's
   repositories, exported as the variable named by `github.token_env` (default
   `GITHUB_PAT`).
3. **The IBR board**: chosen from a discovered list (step 2 below).
4. **Team names**: the names of the teams to report on, matching the team names
   used in Jira.

Everything else — custom-field IDs, the repository list, team rosters, Jira
account IDs, and GitHub logins — is discovered and confirmed, not typed in.

## Step 1 — Verify both connections

- Jira: `GET {base_url}/rest/api/3/myself` with basic auth (email + token).
  A 200 confirms credentials and returns the user's own `accountId`.
- GitHub: `GET {api_url}/user` with the token. A 200 confirms the token; note
  the `X-OAuth-Scopes` response header to confirm repository read access.

Stop with a clear message if either fails; nothing below can be discovered
without working credentials.

## Step 2 — Discover boards and ask for the IBR board

- List boards with `GET {base_url}/rest/agile/1.0/board?startAt=N&maxResults=50`
  (paginate until `isLast`).
- Present the board names to the user and ask **which one is the IBR board** —
  the board that defines which tickets are in scope versus out of scope from an
  IBR perspective. This is a judgment only the user can make; never infer it
  from board names.
- Record it with `role: ibr` (exactly one board may hold this role; `portfolio`
  is accepted as a legacy alias). Additional boards the user wants collected
  get descriptive roles such as `team`, `work-tracking`, `support`, `roadmap`,
  or `quality`.

## Step 3 — Auto-discover custom fields

- Fetch all fields with `GET {base_url}/rest/api/3/field` and match candidates
  by name: the team field (commonly `Team`, type `team`), the target-date field
  (commonly `Target Date` or `Target date`), and any organization-specific
  fields such as `Gravitee Customers`.
- **Verify before writing**: fetch a handful of issues from the chosen IBR board
  (`GET {base_url}/rest/agile/1.0/board/{id}/issue?maxResults=10&fields=<candidates>`)
  and confirm the candidate fields actually carry values of the expected shape
  on real issues. A name match alone is not verification.
- Set `team_field_id`, `target_date_field_id`, and (when applicable)
  `gravitee_customers_field_id` to the verified `customfield_*` IDs. Leave a
  field `null` and tell the user when no candidate verifies — never guess an ID.

## Step 4 — Auto-discover GitHub organization and repositories

- Enumerate the token's organizations with `GET {api_url}/user/orgs`. If there
  is more than one, ask which to use.
- List that organization's repositories with
  `GET {api_url}/orgs/{org}/repos?sort=pushed&per_page=100` (paginate) and keep
  those pushed within `initial_lookback_days` (default 90).
- Propose the filtered list to the user for confirmation — they may drop
  repositories that are noise or add ones the filter missed. Repository
  Repository selection only bounds collection scope. Team ownership is derived
  from confirmed member `github_login` identities in step 5.

## Step 5 — Discover team rosters and cross-verify GitHub identities

For each team name the user supplied:

1. **Find the team's Jira members.** Using the verified team field from step 3,
   search recent issues with
   `GET {base_url}/rest/api/3/search/jql?jql=<team-field> = "<name>" AND updated >= -90d`
   and collect the distinct assignees and reporters (`accountId`,
   `displayName`). Where the instance exposes Atlassian Teams, the team's
   member list may be read directly instead; issue evidence remains the
   fallback that always works.
2. **Cross-verify GitHub logins.** Collect author logins and display names from
   recent pull requests and commits in the confirmed repositories
   (`GET {api_url}/repos/{org}/{repo}/pulls?state=all&per_page=100`, commit
   authors from `GET .../commits`), plus organization members
   (`GET {api_url}/orgs/{org}/members`). Match Jira people to GitHub logins by
   display-name similarity and by co-occurrence — a GitHub login whose PRs
   reference the Jira keys assigned to that person is strong evidence.
3. **Confirm with the user.** Present one proposal table per team: display
   name, Jira account ID, proposed GitHub login, and the evidence for the
   match. Unmatched people keep `github_login: null` with an explicit note.
   **No mapping is written until the user confirms it** — a wrong identity
   mapping silently misattributes delivery evidence.
4. Confirm repository coverage independently of team ownership. Repositories
   determine what GitHub data is collected; an active member's confirmed
   `github_login` determines which team receives that person's PR, commit, and
   review evidence. Jira keys classify the evidence but do not assign its team.

## Step 6 — Derive per-team classification queries

The IBR-versus-non-IBR work view reads a dedicated snapshot scope per team: a
Jira query whose id is exactly `team-field-<team-id>`. Without these queries
the report renders that view's Jira half empty, so derive one for every
configured team instead of leaving `jira.queries` empty:

- `id`: `team-field-<team-id>` (for example `team-field-devex`);
- `jql`: the verified team field matched to the team, bounded by an update
  window — for example `cf[10001] = "<team-uuid>" AND updated >= -92d`.

When the team field is the Atlassian team type, JQL that matches the display
name can silently return zero issues; match by the team's UUID, taken from the
field values observed on real issues in step 3 or step 5. Run each query once
and confirm it returns issues before writing it — a query that returns nothing
for an active team is a configuration error, not a valid empty scope.

## Step 7 — Propose the final configuration

Present the complete picture in one place before writing anything:

- teams that map to Jira team names, each with its confirmed members and their
  Jira account IDs and GitHub usernames;
- the IBR board and any additional boards with their roles;
- the verified custom-field IDs;
- the derived `team-field-<team-id>` classification queries;
- the confirmed repository collection list;

On approval, write `sources.yaml` and `teams.yaml` (stable lowercase IDs;
membership `starts_on` set to the confirmation date with
`starts_on_basis: first_verified_observation`; `roster_source.source` recording
that the roster came from agent discovery plus user confirmation). Validate
both files, run `engintel install`, and remind the user to restart the agent so
the skill and MCP server load.

## Boundaries

- Discovery output is a proposal; the user's confirmation is what makes it
  configuration.
- Never write a credential anywhere; only the environment-variable names are
  recorded.
- Identity mappings without user confirmation must not be written, even when
  the evidence looks conclusive.
