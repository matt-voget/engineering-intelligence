"""Build a reproducible team-health Dashboard from an IBR snapshot."""

from collections import Counter, defaultdict
from datetime import UTC, datetime, timedelta
from statistics import median
from urllib.parse import quote

from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from engineering_intelligence.config import GitHubConfig, TeamsConfig
from engineering_intelligence.persistence.models import (
    BoardMembershipObservation,
    GitHubPullRequest,
    GitHubPullRequestVersion,
    GitHubRepository,
    GitHubReview,
    JiraGitHubRelationship,
    JiraIssue,
    JiraIssueVersion,
    JiraStatusTransition,
    Snapshot,
    SnapshotSourceState,
)
from engineering_intelligence.presentations.dashboard import (
    Dashboard,
    EvidenceLink,
    HealthCoverage,
    HealthCoverageState,
    HealthFlag,
    HealthState,
    Severity,
    SignalEvaluationInput,
    SourceFreshness,
    TeamDashboardRow,
    WorkItem,
)
from engineering_intelligence.snapshots.organization import (
    github_config_for_snapshot,
    ibr_board_id_for_snapshot,
    organization_config_for_snapshot,
)

MAXIMUM_IBR_SOURCE_AGE = timedelta(hours=24)
ACTIVE_DELIVERY_STATUSES = {
    "in progress",
    "in code review",
    "ready for test",
    "in testing",
    "ready for doc",
    "ready for docs",
}
WORKFLOW_STAGE_ORDER = {
    "idea": 0,
    "ideation": 1,
    "product review": 2,
    "ready for build": 3,
    "in progress": 4,
    "in code review": 5,
    "ready for test": 6,
    "in testing": 7,
    "ready for docs": 8,
    "done": 9,
}


class DashboardQuery:
    def __init__(self, sessions: sessionmaker[Session], *, jira_base_url: str) -> None:
        self.sessions = sessions
        self.jira_base_url = jira_base_url.rstrip("/")

    def get(
        self,
        snapshot_identifier: str,
        teams_config: TeamsConfig,
        *,
        ibr_board_id: int | None = None,
        github_config: GitHubConfig | None = None,
    ) -> Dashboard:
        with self.sessions() as session:
            snapshot = self._snapshot(session, snapshot_identifier)
            ibr_board_id = ibr_board_id or ibr_board_id_for_snapshot(snapshot)
            teams_config = organization_config_for_snapshot(snapshot, teams_config)
            github_config = github_config_for_snapshot(snapshot, github_config)
            source_states = session.scalars(
                select(SnapshotSourceState)
                .where(SnapshotSourceState.snapshot_id == snapshot.id)
                .order_by(SnapshotSourceState.source, SnapshotSourceState.scope)
            ).all()
            ibr_state = next(
                (state for state in source_states if state.scope == f"board:{ibr_board_id}"),
                None,
            )
            versions = (
                self._ibr_versions(session, ibr_state)
                if ibr_state is not None and ibr_state.ingestion_run_id is not None
                else []
            )
            board_record_count = len(versions)
            transitions_by_issue = self._transitions(
                session,
                {issue.id for issue, _version in versions},
                (
                    _as_utc(ibr_state.high_water_mark)
                    if ibr_state is not None
                    else _as_utc(snapshot.created_at)
                ),
            )
            grouped: dict[str, list[tuple[JiraIssue, JiraIssueVersion]]] = defaultdict(list)
            for issue, version in versions:
                if version.team_name:
                    grouped[version.team_name.casefold()].append((issue, version))
            github_signals = self._github_signals(
                session,
                source_states,
                github_config,
                teams_config,
                _as_utc(snapshot.created_at),
                (
                    _as_utc(ibr_state.high_water_mark)
                    if ibr_state is not None
                    else _as_utc(snapshot.created_at)
                ),
            )

            rows = []
            for team in teams_config.teams:
                aliases = {team.name.casefold(), *(alias.casefold() for alias in team.aliases)}
                team_records = [record for alias in aliases for record in grouped.get(alias, [])]
                rows.append(
                    self._team_row(
                        team.id,
                        team.name,
                        team_records,
                        _as_utc(snapshot.created_at),
                        transitions_by_issue,
                        self._health_coverage(
                            snapshot_created_at=_as_utc(snapshot.created_at),
                            source_state=ibr_state,
                            board_record_count=board_record_count,
                            team_records=team_records,
                            required_scope=f"board:{ibr_board_id}",
                        ),
                        github_signals.get(team.id, ([], [])),
                    )
                )

            return Dashboard(
                snapshot_id=snapshot.id,
                snapshot_name=snapshot.name,
                snapshot_created_at=_as_utc(snapshot.created_at),
                organization_config_hash=snapshot.organization_config_hash,
                source_config_hash=snapshot.source_config_hash,
                source_freshness=[
                    SourceFreshness(
                        source=state.source,
                        scope=state.scope,
                        observed_at=_as_utc(state.high_water_mark),
                        ingestion_run_id=state.ingestion_run_id,
                    )
                    for state in source_states
                ],
                teams=rows,
            )

    @staticmethod
    def _snapshot(session: Session, identifier: str) -> Snapshot:
        snapshot = session.get(Snapshot, identifier)
        if snapshot is None:
            snapshot = session.scalar(select(Snapshot).where(Snapshot.name == identifier))
        if snapshot is None:
            raise ValueError(f"Snapshot not found: {identifier}")
        return snapshot

    @staticmethod
    def _ibr_versions(
        session: Session,
        state: SnapshotSourceState,
    ) -> list[tuple[JiraIssue, JiraIssueVersion]]:
        issue_ids = session.scalars(
            select(BoardMembershipObservation.issue_id).where(
                BoardMembershipObservation.ingestion_run_id == state.ingestion_run_id
            )
        ).all()
        records: list[tuple[JiraIssue, JiraIssueVersion]] = []
        for issue_id in issue_ids:
            issue = session.get(JiraIssue, issue_id)
            version = session.scalar(
                select(JiraIssueVersion)
                .where(
                    JiraIssueVersion.issue_id == issue_id,
                    JiraIssueVersion.observed_at <= state.high_water_mark,
                )
                .order_by(JiraIssueVersion.observed_at.desc())
                .limit(1)
            )
            if issue is not None and version is not None:
                records.append((issue, version))
        return records

    def _team_row(
        self,
        team_id: str,
        team_name: str,
        records: list[tuple[JiraIssue, JiraIssueVersion]],
        raised_at: datetime,
        transitions_by_issue: dict[str, list[JiraStatusTransition]],
        health_coverage: HealthCoverage,
        github_signals: tuple[list[HealthFlag], list[SignalEvaluationInput]],
    ) -> TeamDashboardRow:
        work_items = [self._work_item(issue, version) for issue, version in records]
        in_progress = sorted(
            (item for item in work_items if item.status.casefold() == "in progress"),
            key=lambda item: item.jira_key,
        )
        ready_for_build = sorted(
            (item for item in work_items if item.status.casefold() == "ready for build"),
            key=lambda item: item.jira_key,
        )
        completed = sorted(
            (
                item
                for item in work_items
                if item.status.casefold() == "done" and item.completed_at is not None
            ),
            key=lambda item: item.completed_at.isoformat() if item.completed_at else "",
            reverse=True,
        )
        flags: list[HealthFlag] = []
        team_evaluations: list[SignalEvaluationInput] = []
        feature_evaluations: list[SignalEvaluationInput] = []
        history_evaluations: list[SignalEvaluationInput] = []
        data_quality_evaluations: list[SignalEvaluationInput] = []
        github_evaluations: list[SignalEvaluationInput] = []
        if health_coverage.state == HealthCoverageState.reliable:
            flags = self._health_flags(
                team_id,
                team_name,
                in_progress,
                ready_for_build,
                raised_at,
            )
            feature_flags, feature_evaluations = self._feature_signals(
                team_id,
                work_items,
                raised_at,
            )
            history_flags, history_evaluations = self._history_signals(
                team_id,
                work_items,
                transitions_by_issue,
                raised_at,
            )
            data_quality_flags, data_quality_evaluations = self._data_quality_signals(
                team_id,
                work_items,
                transitions_by_issue,
                raised_at,
            )
            flags.extend(feature_flags)
            flags.extend(history_flags)
            flags.extend(data_quality_flags)
            github_flags, github_evaluations = github_signals
            flags.extend(github_flags)
            team_evaluations = self._team_evaluations(
                team_id,
                in_progress,
                ready_for_build,
                flags,
            )
        health = self._health_state(flags, health_coverage)
        return TeamDashboardRow(
            team_id=team_id,
            team_name=team_name,
            health=health,
            health_coverage=health_coverage,
            flags=flags,
            most_recently_completed=completed[0] if completed else None,
            in_progress=in_progress,
            ready_for_build=ready_for_build,
            signal_evaluation_inputs=[
                *team_evaluations,
                *feature_evaluations,
                *history_evaluations,
                *data_quality_evaluations,
                *github_evaluations,
            ],
        )

    @staticmethod
    def _github_signals(
        session: Session,
        source_states: list[SnapshotSourceState],
        github_config: GitHubConfig | None,
        teams_config: TeamsConfig,
        evaluated_at: datetime,
        jira_high_water: datetime,
    ) -> dict[str, tuple[list[HealthFlag], list[SignalEvaluationInput]]]:
        """Evaluate bounded GitHub signals from records valid at the snapshot."""
        results = {team.id: ([], []) for team in teams_config.teams}
        if github_config is None:
            return results
        states = {
            state.scope.removeprefix("repository:"): state
            for state in source_states
            if state.source == "github" and state.scope.startswith("repository:")
        }
        teams_by_login: dict[str, set[str]] = defaultdict(set)
        for team in teams_config.teams:
            for member in team.members:
                if member.active and member.github_login:
                    teams_by_login[member.github_login.casefold()].add(team.id)
        linked_prs_by_team: dict[
            str, list[tuple[GitHubRepository, GitHubPullRequest, GitHubPullRequestVersion]]
        ] = defaultdict(list)
        reviews_by_team: dict[str, list[tuple[GitHubReview, GitHubPullRequest]]] = defaultdict(list)

        for repository_config in github_config.repositories:
            state = states.get(repository_config.full_name)
            if state is None or state.ingestion_run_id is None:
                continue
            repository = session.scalar(
                select(GitHubRepository).where(
                    GitHubRepository.full_name == repository_config.full_name
                )
            )
            if repository is None:
                continue
            high_water = _as_utc(state.high_water_mark)
            latest_versions = (
                select(
                    GitHubPullRequestVersion.pull_request_id,
                    func.max(GitHubPullRequestVersion.observed_at).label("observed_at"),
                )
                .where(GitHubPullRequestVersion.observed_at <= high_water)
                .group_by(GitHubPullRequestVersion.pull_request_id)
                .subquery()
            )
            pr_rows = session.execute(
                select(GitHubPullRequest, GitHubPullRequestVersion)
                .join(
                    latest_versions,
                    latest_versions.c.pull_request_id == GitHubPullRequest.id,
                )
                .join(
                    GitHubPullRequestVersion,
                    (GitHubPullRequestVersion.pull_request_id == GitHubPullRequest.id)
                    & (GitHubPullRequestVersion.observed_at == latest_versions.c.observed_at),
                )
                .where(GitHubPullRequest.repository_id == repository.id)
            ).all()
            prs = {pr.id: (pr, version) for pr, version in pr_rows}
            if not prs:
                continue
            relationships = session.scalars(
                select(JiraGitHubRelationship).where(
                    JiraGitHubRelationship.github_record_type == "pull_request",
                    JiraGitHubRelationship.github_record_id.in_(prs),
                    JiraGitHubRelationship.first_seen_at <= high_water,
                )
            ).all()
            linked_pr_ids: set[str] = set()
            for relationship in relationships:
                linked_pr_ids.add(relationship.github_record_id)

            for pr_id, (pr, version) in prs.items():
                if version.state.casefold() != "open":
                    continue
                author = (version.author_login or "").casefold()
                author_teams = teams_by_login.get(author, set())
                if pr_id in linked_pr_ids:
                    for team_id in author_teams:
                        linked_prs_by_team[team_id].append((repository, pr, version))
                elif author and not author.endswith("[bot]"):
                    for team_id in author_teams:
                        age_days = max(
                            0,
                            (evaluated_at.date() - _as_utc(version.source_created_at).date()).days,
                        )
                        triggered = age_days >= 7
                        fingerprint = (
                            f"{team_id}:github-attribution:{repository.full_name}#{pr.number}"
                        )
                        flags, evaluations = results.setdefault(team_id, ([], []))
                        if triggered:
                            flags.append(
                                HealthFlag(
                                    fingerprint=fingerprint,
                                    area="github_attribution",
                                    severity=Severity.watch,
                                    title=f"{repository.full_name}#{pr.number} has no Jira attribution",
                                    explanation=(
                                        f"The open PR is {age_days} days old and has no direct "
                                        "Jira relationship. Its author is a confirmed team member."
                                    ),
                                    raised_at=evaluated_at,
                                    evidence=[
                                        EvidenceLink(
                                            label=f"Open PR #{pr.number} in GitHub",
                                            url=pr.html_url,
                                            title=version.title,
                                        )
                                    ],
                                )
                            )
                        evaluations.append(
                            SignalEvaluationInput(
                                definition_key="pull-request-missing-jira-attribution",
                                definition_version="2.0.0",
                                scope_type="pull_request",
                                scope_id=f"{repository.full_name}#{pr.number}",
                                subject_id=pr.id,
                                dimension="open_days_without_direct_jira_relationship",
                                condition_met=triggered,
                                severity="watch" if triggered else None,
                                confidence="medium",
                                current_value={"days": age_days, "jira_relationship_count": 0},
                                sample_size=1,
                                flag_fingerprint=fingerprint if triggered else None,
                                details={"team_id": team_id, "repository": repository.full_name},
                            )
                        )

            reviews = session.scalars(
                select(GitHubReview).where(
                    GitHubReview.pull_request_id.in_(prs),
                    GitHubReview.observed_at <= high_water,
                )
            ).all()
            for review in reviews:
                reviewer = (review.author_login or "").casefold()
                for team_id in teams_by_login.get(reviewer, set()):
                    reviews_by_team[team_id].append((review, prs[review.pull_request_id][0]))

        for team_id, linked_prs in linked_prs_by_team.items():
            flags, evaluations = results[team_id]
            for repository, pr, version in linked_prs:
                age_days = max(
                    0,
                    (evaluated_at.date() - _as_utc(version.source_created_at).date()).days,
                )
                triggered = age_days >= 14
                fingerprint = f"{team_id}:github-aging-pr:{repository.full_name}#{pr.number}"
                evidence = [
                    EvidenceLink(
                        label=f"Open PR #{pr.number} in GitHub",
                        url=pr.html_url,
                        title=version.title,
                    )
                ]
                if triggered:
                    flags.append(
                        HealthFlag(
                            fingerprint=fingerprint,
                            area="github_pull_request_aging",
                            severity=Severity.watch,
                            title=f"{repository.full_name}#{pr.number} is aging",
                            explanation=(
                                f"This Jira-attributed open PR has been open for {age_days} days."
                            ),
                            raised_at=evaluated_at,
                            evidence=evidence,
                        )
                    )
                evaluations.append(
                    SignalEvaluationInput(
                        definition_key="pull-request-aging-open",
                        definition_version="1.0.0",
                        scope_type="pull_request",
                        scope_id=f"{repository.full_name}#{pr.number}",
                        subject_id=pr.id,
                        dimension="open_age_days",
                        condition_met=triggered,
                        severity="watch" if triggered else None,
                        confidence="high",
                        current_value={"days": age_days},
                        sample_size=1,
                        flag_fingerprint=fingerprint if triggered else None,
                        details={"team_id": team_id, "repository": repository.full_name},
                    )
                )

        for team_id, (flags, evaluations) in results.items():
            recent = [
                (review, pr)
                for review, pr in reviews_by_team.get(team_id, [])
                if review.submitted_at is not None
                and _as_utc(review.submitted_at) >= evaluated_at - timedelta(days=30)
                and review.author_login
                and not review.author_login.casefold().endswith("[bot]")
            ]
            counts = Counter(review.author_login for review, _pr in recent)
            top_reviewer, top_count = counts.most_common(1)[0] if counts else (None, 0)
            share = top_count / len(recent) if recent else 0.0
            triggered = len(recent) >= 10 and share >= 0.6
            fingerprint = f"{team_id}:github-review-concentration"
            if triggered:
                evidence = []
                seen_urls: set[str] = set()
                for review, pr in recent:
                    if review.author_login != top_reviewer or pr.html_url in seen_urls:
                        continue
                    seen_urls.add(pr.html_url)
                    evidence.append(
                        EvidenceLink(
                            label=f"Review by @{top_reviewer}",
                            url=review.html_url or pr.html_url,
                        )
                    )
                    if len(evidence) == 5:
                        break
                flags.append(
                    HealthFlag(
                        fingerprint=fingerprint,
                        area="github_review_concentration",
                        severity=Severity.watch,
                        title="Review load is concentrated",
                        explanation=(
                            f"@{top_reviewer} submitted {top_count} of {len(recent)} "
                            f"reviews ({share:.0%}) on Jira-attributed team PRs in the "
                            "last 30 days."
                        ),
                        raised_at=evaluated_at,
                        evidence=evidence,
                    )
                )
            evaluations.append(
                SignalEvaluationInput(
                    definition_key="team-review-load-concentration",
                    definition_version="1.0.0",
                    scope_type="team",
                    scope_id=team_id,
                    dimension="top_reviewer_share_30d",
                    condition_met=triggered,
                    severity="watch" if triggered else None,
                    confidence="medium",
                    current_value={
                        "review_count": len(recent),
                        "top_reviewer": top_reviewer,
                        "top_reviewer_count": top_count,
                        "top_reviewer_share": round(share, 4),
                    },
                    sample_size=len(recent),
                    flag_fingerprint=fingerprint if triggered else None,
                    details={
                        "team_id": team_id,
                        "window_days": 30,
                        "minimum_sample_size": 10,
                    },
                )
            )
        return results

    @staticmethod
    def _health_state(
        flags: list[HealthFlag],
        coverage: HealthCoverage,
    ) -> HealthState:
        if coverage.state != HealthCoverageState.reliable:
            return HealthState.unknown
        severities = {flag.severity for flag in flags}
        if Severity.critical in severities:
            return HealthState.critical
        if Severity.concern in severities:
            return HealthState.concern
        if Severity.watch in severities:
            return HealthState.watch
        return HealthState.healthy

    @staticmethod
    def _health_coverage(
        *,
        snapshot_created_at: datetime,
        source_state: SnapshotSourceState | None,
        board_record_count: int,
        team_records: list[tuple[JiraIssue, JiraIssueVersion]],
        required_scope: str = "board:2168",
    ) -> HealthCoverage:
        if source_state is None or source_state.ingestion_run_id is None:
            return HealthCoverage(
                state=HealthCoverageState.insufficient,
                reasons=["The snapshot has no successful IBR board ingestion."],
                required_source="jira",
                required_scope=required_scope,
                observed_at=(
                    _as_utc(source_state.high_water_mark) if source_state is not None else None
                ),
                maximum_age_seconds=int(MAXIMUM_IBR_SOURCE_AGE.total_seconds()),
                age_at_snapshot_seconds=None,
                board_record_count=0,
                team_record_count=0,
            )
        observed_at = _as_utc(source_state.high_water_mark)
        age = max(snapshot_created_at - observed_at, timedelta())
        reasons: list[str] = []
        state = HealthCoverageState.reliable
        if age > MAXIMUM_IBR_SOURCE_AGE:
            state = HealthCoverageState.stale
            reasons.append(
                "The IBR source was more than 24 hours old when this snapshot was created."
            )
        elif board_record_count == 0:
            state = HealthCoverageState.insufficient
            reasons.append("The IBR source returned no board records.")
        elif any(version.status_name is None for _issue, version in team_records):
            state = HealthCoverageState.incomplete
            reasons.append("One or more mapped IBR records has no Jira status.")
        return HealthCoverage(
            state=state,
            reasons=reasons,
            required_source=source_state.source,
            required_scope=source_state.scope,
            observed_at=observed_at,
            maximum_age_seconds=int(MAXIMUM_IBR_SOURCE_AGE.total_seconds()),
            age_at_snapshot_seconds=int(age.total_seconds()),
            board_record_count=board_record_count,
            team_record_count=len(team_records),
        )

    @staticmethod
    def _transitions(
        session: Session,
        issue_ids: set[str],
        high_water: datetime,
    ) -> dict[str, list[JiraStatusTransition]]:
        if not issue_ids:
            return {}
        rows = session.scalars(
            select(JiraStatusTransition)
            .where(
                JiraStatusTransition.issue_id.in_(issue_ids),
                JiraStatusTransition.changed_at <= high_water,
                JiraStatusTransition.first_seen_at <= high_water,
            )
            .order_by(
                JiraStatusTransition.issue_id,
                JiraStatusTransition.changed_at,
                JiraStatusTransition.changelog_id,
                JiraStatusTransition.item_index,
            )
        ).all()
        grouped: dict[str, list[JiraStatusTransition]] = defaultdict(list)
        for row in rows:
            grouped[row.issue_id].append(row)
        return grouped

    @staticmethod
    def _work_item(issue: JiraIssue, version: JiraIssueVersion) -> WorkItem:
        return WorkItem(
            jira_id=issue.id,
            jira_key=issue.issue_key,
            title=version.summary,
            status=version.status_name or "Unknown",
            url=issue.web_url,
            completed_at=version.resolved_at or version.source_updated_at,
            assignee_account_id=version.assignee_account_id,
            source_created_at=version.source_created_at or issue.created_at,
            source_updated_at=version.source_updated_at,
            target_date=version.target_date,
            target_date_value=version.target_date_value,
        )

    @staticmethod
    def _team_evaluations(
        team_id: str,
        in_progress: list[WorkItem],
        ready_for_build: list[WorkItem],
        flags: list[HealthFlag],
    ) -> list[SignalEvaluationInput]:
        by_area = {flag.area: flag for flag in flags}
        definitions = (
            (
                "team-no-work-in-progress",
                "work_in_flight",
                "ibr_in_progress_count",
                len(in_progress),
            ),
            (
                "team-no-ready-for-build",
                "near_term_pipeline",
                "ibr_ready_for_build_count",
                len(ready_for_build),
            ),
        )
        return [
            SignalEvaluationInput(
                definition_key=definition_key,
                definition_version="1.0.0",
                scope_type="team",
                scope_id=team_id,
                dimension=dimension,
                condition_met=value == 0,
                severity="watch" if value == 0 else None,
                confidence="high",
                current_value={"count": value},
                sample_size=value,
                flag_fingerprint=(by_area[area].fingerprint if value == 0 else None),
                details={"comparison_basis": "absolute_rule"},
            )
            for definition_key, area, dimension, value in definitions
        ]

    @staticmethod
    def _feature_signals(
        team_id: str,
        work_items: list[WorkItem],
        evaluated_at: datetime,
    ) -> tuple[list[HealthFlag], list[SignalEvaluationInput]]:
        flags: list[HealthFlag] = []
        evaluations: list[SignalEvaluationInput] = []
        for item in work_items:
            active = item.status.casefold() in ACTIVE_DELIVERY_STATUSES
            evidence = [
                EvidenceLink(
                    label=f"Open {item.jira_key} in Jira",
                    url=item.url,
                    jira_key=item.jira_key,
                    title=item.title,
                )
            ]
            if active and item.source_updated_at is not None:
                inactive_days = max(
                    0,
                    (evaluated_at.date() - _as_utc(item.source_updated_at).date()).days,
                )
                triggered = inactive_days >= 14
                fingerprint = f"{team_id}:stalled-work:{item.jira_key}"
                if triggered:
                    flags.append(
                        HealthFlag(
                            fingerprint=fingerprint,
                            area="stalled_work",
                            severity=Severity.watch,
                            title=f"{item.jira_key} has no recent Jira activity",
                            explanation=(
                                f"{item.jira_key} is active but has not changed in Jira "
                                f"for {inactive_days} days. GitHub activity is not yet "
                                "included in this rule."
                            ),
                            raised_at=evaluated_at,
                            evidence=evidence,
                        )
                    )
                evaluations.append(
                    SignalEvaluationInput(
                        definition_key="feature-stalled-active-work",
                        definition_version="1.0.0",
                        scope_type="feature",
                        scope_id=item.jira_key,
                        subject_id=item.jira_id,
                        dimension="jira_inactive_days",
                        condition_met=triggered,
                        severity="watch" if triggered else None,
                        confidence="medium",
                        current_value={"days": inactive_days},
                        sample_size=1,
                        flag_fingerprint=fingerprint if triggered else None,
                        details={
                            "team_id": team_id,
                            "jira_key": item.jira_key,
                            "activity_source": "jira_issue_updated",
                            "limitations": ["GitHub activity is not included"],
                        },
                    )
                )
            if active:
                assigned = item.assignee_account_id is not None
                fingerprint = f"{team_id}:ownership-gap:{item.jira_key}"
                if not assigned:
                    flags.append(
                        HealthFlag(
                            fingerprint=fingerprint,
                            area="ownership_gap",
                            severity=Severity.watch,
                            title=f"{item.jira_key} has no Jira assignee",
                            explanation=(
                                f"{item.jira_key} is active but has no current Jira assignee."
                            ),
                            raised_at=evaluated_at,
                            evidence=evidence,
                        )
                    )
                evaluations.append(
                    SignalEvaluationInput(
                        definition_key="feature-active-ownership-gap",
                        definition_version="1.0.0",
                        scope_type="feature",
                        scope_id=item.jira_key,
                        subject_id=item.jira_id,
                        dimension="has_jira_assignee",
                        condition_met=not assigned,
                        severity="watch" if not assigned else None,
                        confidence="high",
                        current_value={"assigned": assigned},
                        sample_size=1,
                        flag_fingerprint=fingerprint if not assigned else None,
                        details={"team_id": team_id, "jira_key": item.jira_key},
                    )
                )
            unresolved = item.status.casefold() != "done"
            if unresolved and item.target_date is not None:
                days_overdue = (evaluated_at.date() - item.target_date).days
                triggered = days_overdue > 0
                fingerprint = f"{team_id}:target-risk:{item.jira_key}"
                if triggered:
                    flags.append(
                        HealthFlag(
                            fingerprint=fingerprint,
                            area="target_risk",
                            severity=Severity.watch,
                            title=f"{item.jira_key} is past its target date",
                            explanation=(
                                f"{item.jira_key} is unresolved and its target date "
                                f"passed {days_overdue} days ago."
                            ),
                            raised_at=evaluated_at,
                            evidence=evidence,
                        )
                    )
                evaluations.append(
                    SignalEvaluationInput(
                        definition_key="feature-target-date-overdue",
                        definition_version="1.0.0",
                        scope_type="feature",
                        scope_id=item.jira_key,
                        subject_id=item.jira_id,
                        dimension="target_days_overdue",
                        condition_met=triggered,
                        severity="watch" if triggered else None,
                        confidence="high",
                        current_value={
                            "days": days_overdue,
                            "target_date": item.target_date.isoformat(),
                        },
                        sample_size=1,
                        flag_fingerprint=fingerprint if triggered else None,
                        details={"team_id": team_id, "jira_key": item.jira_key},
                    )
                )
        return flags, evaluations

    @staticmethod
    def _data_quality_signals(
        team_id: str,
        work_items: list[WorkItem],
        transitions_by_issue: dict[str, list[JiraStatusTransition]],
        evaluated_at: datetime,
    ) -> tuple[list[HealthFlag], list[SignalEvaluationInput]]:
        flags: list[HealthFlag] = []
        evaluations: list[SignalEvaluationInput] = []
        for item in work_items:
            status = _canonical_status(item.status)
            stage = WORKFLOW_STAGE_ORDER.get(status)
            transitions = transitions_by_issue.get(item.jira_id, [])
            evidence = [
                EvidenceLink(
                    label=f"Open {item.jira_key} in Jira",
                    url=item.url,
                    jira_key=item.jira_key,
                    title=item.title,
                )
            ]
            transition_rule_applies = (
                stage is not None
                and stage >= WORKFLOW_STAGE_ORDER["product review"]
                and status != "done"
            )
            if transition_rule_applies:
                missing_transitions = not transitions
                fingerprint = f"{team_id}:data-quality-transition:{item.jira_key}"
                if missing_transitions:
                    flags.append(
                        HealthFlag(
                            fingerprint=fingerprint,
                            area="transition_evidence",
                            severity=Severity.watch,
                            title=f"{item.jira_key} has no Jira transition history",
                            explanation=(
                                f"{item.jira_key} is in {item.status}, but no Jira "
                                "status-transition evidence is available. Flow and "
                                "stage-history judgments are suppressed for this Feature."
                            ),
                            raised_at=evaluated_at,
                            evidence=evidence,
                        )
                    )
                evaluations.append(
                    SignalEvaluationInput(
                        definition_key="feature-missing-transition-history",
                        definition_version="1.0.0",
                        scope_type="feature",
                        scope_id=item.jira_key,
                        subject_id=item.jira_id,
                        dimension="status_transition_count",
                        condition_met=missing_transitions,
                        severity="watch" if missing_transitions else None,
                        confidence="medium",
                        current_value={
                            "count": len(transitions),
                            "current_status": status,
                        },
                        sample_size=len(transitions),
                        flag_fingerprint=fingerprint if missing_transitions else None,
                        details={
                            "team_id": team_id,
                            "jira_key": item.jira_key,
                            "limitations": [
                                "The issue may have been created directly in its current status"
                            ],
                        },
                    )
                )

            if item.status.casefold() in ACTIVE_DELIVERY_STATUSES:
                missing_fields = [
                    field
                    for field, value in (
                        ("source_created_at", item.source_created_at),
                        ("source_updated_at", item.source_updated_at),
                    )
                    if value is None
                ]
                fingerprint = f"{team_id}:data-quality-timing:{item.jira_key}"
                if missing_fields:
                    flags.append(
                        HealthFlag(
                            fingerprint=fingerprint,
                            area="required_evidence",
                            severity=Severity.watch,
                            title=f"{item.jira_key} is missing Jira timing evidence",
                            explanation=(
                                f"{item.jira_key} is active but lacks "
                                f"{', '.join(missing_fields)}. Timing and inactivity "
                                "judgments requiring those fields are suppressed."
                            ),
                            raised_at=evaluated_at,
                            evidence=evidence,
                        )
                    )
                evaluations.append(
                    SignalEvaluationInput(
                        definition_key="feature-missing-timing-evidence",
                        definition_version="1.0.0",
                        scope_type="feature",
                        scope_id=item.jira_key,
                        subject_id=item.jira_id,
                        dimension="missing_timing_field_count",
                        condition_met=bool(missing_fields),
                        severity="watch" if missing_fields else None,
                        confidence="high",
                        current_value={
                            "count": len(missing_fields),
                            "missing_fields": missing_fields,
                        },
                        sample_size=2,
                        flag_fingerprint=fingerprint if missing_fields else None,
                        details={"team_id": team_id, "jira_key": item.jira_key},
                    )
                )
        return flags, evaluations

    @staticmethod
    def _history_signals(
        team_id: str,
        work_items: list[WorkItem],
        transitions_by_issue: dict[str, list[JiraStatusTransition]],
        evaluated_at: datetime,
    ) -> tuple[list[HealthFlag], list[SignalEvaluationInput]]:
        flags: list[HealthFlag] = []
        evaluations: list[SignalEvaluationInput] = []
        baselines = _team_stage_baselines(
            work_items,
            transitions_by_issue,
            evaluated_at,
        )
        for item in work_items:
            transitions = transitions_by_issue.get(item.jira_id, [])
            if not transitions:
                continue
            evidence = [
                EvidenceLink(
                    label=f"Open {item.jira_key} in Jira",
                    url=item.url,
                    jira_key=item.jira_key,
                    title=item.title,
                )
            ]
            recent_30 = [
                transition
                for transition in transitions
                if _as_utc(transition.changed_at) >= evaluated_at - timedelta(days=30)
            ]
            regressions = [
                transition for transition in recent_30 if _is_workflow_regression(transition)
            ]
            regression_triggered = bool(regressions)
            regression_fingerprint = f"{team_id}:workflow-regression:{item.jira_key}"
            if regression_triggered:
                latest = regressions[-1]
                flags.append(
                    HealthFlag(
                        fingerprint=regression_fingerprint,
                        area="workflow_regression",
                        severity=Severity.watch,
                        title=f"{item.jira_key} moved backward in the workflow",
                        explanation=(
                            f"{item.jira_key} had {len(regressions)} backward lifecycle "
                            "transition(s) in the last 30 days; the latest moved from "
                            f"{latest.from_status_name or 'Unknown'} to "
                            f"{latest.to_status_name or 'Unknown'}."
                        ),
                        raised_at=evaluated_at,
                        evidence=evidence,
                    )
                )
            evaluations.append(
                SignalEvaluationInput(
                    definition_key="feature-workflow-regression",
                    definition_version="1.1.0",
                    scope_type="feature",
                    scope_id=item.jira_key,
                    subject_id=item.jira_id,
                    dimension="backward_transition_count_30d",
                    condition_met=regression_triggered,
                    severity="watch" if regression_triggered else None,
                    confidence="high",
                    current_value={
                        "count": len(regressions),
                        "latest_transition": (
                            {
                                "from": regressions[-1].from_status_name,
                                "to": regressions[-1].to_status_name,
                                "changed_at": _as_utc(regressions[-1].changed_at).isoformat(),
                            }
                            if regressions
                            else None
                        ),
                    },
                    sample_size=len(recent_30),
                    flag_fingerprint=(regression_fingerprint if regression_triggered else None),
                    details={"team_id": team_id, "jira_key": item.jira_key},
                )
            )

            recent_90 = [
                transition
                for transition in transitions
                if _as_utc(transition.changed_at) >= evaluated_at - timedelta(days=90)
            ]
            entries = Counter(
                _canonical_status(transition.to_status_name)
                for transition in recent_90
                if _canonical_status(transition.to_status_name) in WORKFLOW_STAGE_ORDER
            )
            repeated_status, maximum_entries = (
                max(entries.items(), key=lambda pair: (pair[1], pair[0])) if entries else (None, 0)
            )
            cycling_triggered = maximum_entries >= 3
            cycling_fingerprint = f"{team_id}:workflow-cycling:{item.jira_key}"
            if cycling_triggered:
                flags.append(
                    HealthFlag(
                        fingerprint=cycling_fingerprint,
                        area="workflow_cycling",
                        severity=Severity.watch,
                        title=f"{item.jira_key} repeatedly cycles through a status",
                        explanation=(
                            f"{item.jira_key} entered {repeated_status} "
                            f"{maximum_entries} times in the last 90 days."
                        ),
                        raised_at=evaluated_at,
                        evidence=evidence,
                    )
                )
            evaluations.append(
                SignalEvaluationInput(
                    definition_key="feature-repeated-status-cycling",
                    definition_version="1.1.0",
                    scope_type="feature",
                    scope_id=item.jira_key,
                    subject_id=item.jira_id,
                    dimension="maximum_status_entries_90d",
                    condition_met=cycling_triggered,
                    severity="watch" if cycling_triggered else None,
                    confidence="high",
                    current_value={
                        "maximum_entries": maximum_entries,
                        "status": repeated_status,
                    },
                    sample_size=len(recent_90),
                    flag_fingerprint=(cycling_fingerprint if cycling_triggered else None),
                    details={"team_id": team_id, "jira_key": item.jira_key},
                )
            )

            current_status = _canonical_status(item.status)
            baseline_values = [
                duration
                for issue_id, duration in baselines.get(current_status, [])
                if issue_id != item.jira_id
            ]
            if (
                item.status.casefold() in ACTIVE_DELIVERY_STATUSES
                and item.source_created_at is not None
                and len(baseline_values) >= 5
            ):
                stage_started = _current_stage_started_at(
                    item,
                    transitions,
                )
                current_days = max(
                    0.0,
                    (evaluated_at - stage_started).total_seconds() / 86400,
                )
                baseline_median = float(median(baseline_values))
                threshold = max(14.0, baseline_median * 1.5)
                aging_triggered = current_days >= threshold
                aging_fingerprint = f"{team_id}:stage-aging:{item.jira_key}"
                if aging_triggered:
                    flags.append(
                        HealthFlag(
                            fingerprint=aging_fingerprint,
                            area="stage_aging",
                            severity=Severity.watch,
                            title=f"{item.jira_key} is aging in {item.status}",
                            explanation=(
                                f"{item.jira_key} has spent {current_days:.1f} days in "
                                f"{item.status}, above the {threshold:.1f}-day threshold "
                                f"derived from the team's {baseline_median:.1f}-day "
                                f"median ({len(baseline_values)} samples)."
                            ),
                            raised_at=evaluated_at,
                            evidence=evidence,
                        )
                    )
                evaluations.append(
                    SignalEvaluationInput(
                        definition_key="feature-stage-aging-vs-team",
                        definition_version="1.1.0",
                        scope_type="feature",
                        scope_id=item.jira_key,
                        subject_id=item.jira_id,
                        dimension="current_stage_age_days",
                        condition_met=aging_triggered,
                        severity="watch" if aging_triggered else None,
                        confidence="medium",
                        current_value={
                            "days": round(current_days, 2),
                            "status": current_status,
                            "threshold_days": round(threshold, 2),
                        },
                        baseline={
                            "median_days": round(baseline_median, 2),
                            "window_days": 90,
                            "multiplier": 1.5,
                        },
                        sample_size=len(baseline_values),
                        flag_fingerprint=(aging_fingerprint if aging_triggered else None),
                        details={"team_id": team_id, "jira_key": item.jira_key},
                    )
                )
        return flags, evaluations

    def _health_flags(
        self,
        team_id: str,
        team_name: str,
        in_progress: list[WorkItem],
        ready_for_build: list[WorkItem],
        raised_at: datetime,
    ) -> list[HealthFlag]:
        board_url = f"{self.jira_base_url}/issues/"
        team_query_url = f"{self.jira_base_url}/issues/?jql=" + quote(
            f'"Team" = "{team_name}" ORDER BY Rank ASC'
        )
        evidence = [
            EvidenceLink(label=f"Open {team_name} work in Jira", url=team_query_url),
            EvidenceLink(label="Open IBR work in Jira", url=board_url),
        ]
        flags = []
        if not in_progress:
            flags.append(
                HealthFlag(
                    fingerprint=f"{team_id}:work-in-flight:none",
                    area="work_in_flight",
                    severity=Severity.watch,
                    title="No IBR items in progress",
                    explanation=(
                        "The snapshot contains no IBR items in the In Progress column "
                        "for this team."
                    ),
                    raised_at=raised_at,
                    evidence=evidence,
                )
            )
        if not ready_for_build:
            flags.append(
                HealthFlag(
                    fingerprint=f"{team_id}:near-term-pipeline:none",
                    area="near_term_pipeline",
                    severity=Severity.watch,
                    title="No IBR items ready for build",
                    explanation=(
                        "The snapshot contains no IBR items in the Ready for Build "
                        "column for this team."
                    ),
                    raised_at=raised_at,
                    evidence=evidence,
                )
            )
        return flags


def _as_utc(value: datetime) -> datetime:
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


def _canonical_status(status: str | None) -> str:
    value = (status or "").strip().casefold()
    return "ready for docs" if value == "ready for doc" else value


def _is_workflow_regression(transition: JiraStatusTransition) -> bool:
    previous = WORKFLOW_STAGE_ORDER.get(_canonical_status(transition.from_status_name))
    current = WORKFLOW_STAGE_ORDER.get(_canonical_status(transition.to_status_name))
    return previous is not None and current is not None and current < previous


def _current_stage_started_at(
    item: WorkItem,
    transitions: list[JiraStatusTransition],
) -> datetime:
    current_status = _canonical_status(item.status)
    matching = [
        transition
        for transition in transitions
        if _canonical_status(transition.to_status_name) == current_status
    ]
    if matching:
        return _as_utc(matching[-1].changed_at)
    assert item.source_created_at is not None
    return _as_utc(item.source_created_at)


def _team_stage_baselines(
    work_items: list[WorkItem],
    transitions_by_issue: dict[str, list[JiraStatusTransition]],
    evaluated_at: datetime,
) -> dict[str, list[tuple[str, float]]]:
    baseline_start = evaluated_at - timedelta(days=90)
    durations: dict[str, list[tuple[str, float]]] = defaultdict(list)
    for item in work_items:
        transitions = transitions_by_issue.get(item.jira_id, [])
        if not transitions or item.source_created_at is None:
            continue
        status = _canonical_status(transitions[0].from_status_name)
        started = _as_utc(item.source_created_at)
        for transition in transitions:
            ended = _as_utc(transition.changed_at)
            if (
                status in WORKFLOW_STAGE_ORDER
                and baseline_start <= ended <= evaluated_at
                and ended >= started
            ):
                durations[status].append((item.jira_id, (ended - started).total_seconds() / 86400))
            status = _canonical_status(transition.to_status_name)
            started = ended
    return durations
