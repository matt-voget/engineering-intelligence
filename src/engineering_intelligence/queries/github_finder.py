"""Snapshot-safe organization-wide pull-request and commit finder."""

from collections import defaultdict
from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from engineering_intelligence.config import SourceConfig
from engineering_intelligence.persistence.models import (
    GitHubCommit,
    GitHubPullRequest,
    GitHubPullRequestCommit,
    GitHubPullRequestVersion,
    GitHubRepository,
    GitHubReview,
    JiraGitHubRelationship,
    JiraIssue,
    SnapshotSourceState,
)
from engineering_intelligence.presentations.github_finder import (
    GitHubFinderRecord,
    GitHubFinderView,
)
from engineering_intelligence.queries.dashboard import DashboardQuery, _as_utc
from engineering_intelligence.queries.github_pr_metrics import _measure
from engineering_intelligence.snapshots.organization import source_config_for_snapshot


class GitHubFinderQuery:
    def __init__(self, sessions: sessionmaker[Session]) -> None:
        self.sessions = sessions

    def get(self, snapshot_identifier: str, source_config: SourceConfig) -> GitHubFinderView:
        with self.sessions() as session:
            snapshot = DashboardQuery._snapshot(session, snapshot_identifier)
            source_config = source_config_for_snapshot(snapshot, source_config)
            repository_names = sorted(r.full_name for r in source_config.github.repositories)
            states = {
                state.scope.removeprefix("repository:"): state
                for state in session.scalars(select(SnapshotSourceState).where(
                    SnapshotSourceState.snapshot_id == snapshot.id,
                    SnapshotSourceState.source == "github",
                )).all()
                if state.scope.startswith("repository:")
            }
            records: list[GitHubFinderRecord] = []
            missing: list[str] = []
            for repository_name in repository_names:
                state = states.get(repository_name)
                repository = session.scalar(select(GitHubRepository).where(
                    GitHubRepository.full_name == repository_name
                ))
                if state is None or repository is None:
                    missing.append(repository_name)
                    continue
                high_water = _as_utc(state.high_water_mark)
                latest = (
                    select(
                        GitHubPullRequestVersion.pull_request_id,
                        func.max(GitHubPullRequestVersion.observed_at).label("observed_at"),
                    )
                    .where(GitHubPullRequestVersion.observed_at <= high_water)
                    .group_by(GitHubPullRequestVersion.pull_request_id)
                    .subquery()
                )
                pulls = session.execute(
                    select(GitHubPullRequest, GitHubPullRequestVersion)
                    .join(latest, latest.c.pull_request_id == GitHubPullRequest.id)
                    .join(GitHubPullRequestVersion,
                        (GitHubPullRequestVersion.pull_request_id == GitHubPullRequest.id)
                        & (GitHubPullRequestVersion.observed_at == latest.c.observed_at))
                    .where(GitHubPullRequest.repository_id == repository.id)
                ).all()
                pull_numbers = {pull.id: f"#{pull.number}" for pull, _ in pulls}
                commit_links: dict[str, list[str]] = defaultdict(list)
                if pull_numbers:
                    for pull_id, commit_sha in session.execute(select(
                        GitHubPullRequestCommit.pull_request_id,
                        GitHubPullRequestCommit.commit_sha,
                    ).where(GitHubPullRequestCommit.pull_request_id.in_(pull_numbers))):
                        commit_links[commit_sha].append(pull_numbers[pull_id])
                relationships = _relationships(
                    session,
                    [("pull_request", pull.id) for pull, _ in pulls]
                    + [("commit", sha) for sha in commit_links],
                    high_water,
                )
                commit_counts = defaultdict(int)
                for pull_id in session.scalars(select(
                    GitHubPullRequestCommit.pull_request_id
                ).where(GitHubPullRequestCommit.pull_request_id.in_(pull_numbers))).all():
                    commit_counts[pull_id] += 1
                first_commits: dict[str, datetime] = {}
                if pull_numbers:
                    linked_commits = session.execute(
                        select(GitHubPullRequestCommit.pull_request_id, GitHubCommit)
                        .join(GitHubCommit, GitHubCommit.sha == GitHubPullRequestCommit.commit_sha)
                        .where(
                            GitHubPullRequestCommit.pull_request_id.in_(pull_numbers),
                            GitHubCommit.repository_id == repository.id,
                            GitHubCommit.first_seen_at <= high_water,
                        )
                    ).all()
                    for pull_id, commit in linked_commits:
                        committed = _commit_boundary(commit)
                        if committed is not None and (pull_id not in first_commits or committed < first_commits[pull_id]):
                            first_commits[pull_id] = committed
                for pull, version in pulls:
                    reviews = list(session.scalars(select(GitHubReview).where(
                        GitHubReview.pull_request_id == pull.id,
                        GitHubReview.observed_at <= high_water,
                    )).all())
                    measurement = _measure(version, reviews)
                    created_at = _as_utc(version.source_created_at)
                    first_commit_at = first_commits.get(pull.id)
                    jira_urls = relationships.get(("pull_request", pull.id), {})
                    records.append(GitHubFinderRecord(
                        record_key=f"pull-request:{pull.id}", record_type="pull_request",
                        repository=repository_name, identifier=f"#{pull.number}",
                        title=version.title, url=pull.html_url,
                        state="merged" if version.merged_at else version.state,
                        draft=version.draft, author_login=version.author_login,
                        created_at=created_at,
                        updated_at=_as_utc(version.source_updated_at),
                        closed_at=_utc(version.closed_at), merged_at=_utc(version.merged_at),
                        head_ref=version.head_ref, base_ref=version.base_ref,
                        commit_count=commit_counts[pull.id], review_count=len(reviews),
                        reviewers=sorted({r.author_login for r in reviews if r.author_login}, key=str.casefold),
                        first_commit_at=first_commit_at,
                        first_reviewed_at=measurement[0] if measurement else None,
                        coding_hours=_coding_hours(created_at, first_commit_at),
                        pickup_hours=measurement[1] if measurement else None,
                        review_hours=measurement[2] if measurement else None,
                        jira_keys=sorted(jira_urls), jira_urls=jira_urls,
                    ))
                if commit_links:
                    commits = session.scalars(select(GitHubCommit).where(
                        GitHubCommit.repository_id == repository.id,
                        GitHubCommit.first_seen_at <= high_water,
                        GitHubCommit.sha.in_(commit_links),
                    )).all()
                    for commit in commits:
                        jira_urls = relationships.get(("commit", commit.sha), {})
                        records.append(GitHubFinderRecord(
                            record_key=f"commit:{repository_name}:{commit.sha}",
                            record_type="commit", repository=repository_name,
                            identifier=commit.sha[:8], title=commit.message.splitlines()[0],
                            url=commit.html_url, author_login=commit.author_login or commit.author_name,
                            authored_at=_utc(commit.authored_at), committed_at=_utc(commit.committed_at),
                            pull_requests=sorted(commit_links[commit.sha]),
                            jira_keys=sorted(jira_urls), jira_urls=jira_urls,
                        ))
            records.sort(key=lambda row: (
                -int((_record_date(row) or _as_utc(snapshot.created_at)).timestamp()),
                row.record_type, row.repository, row.identifier,
            ))
            notes = ["Commits are associated with ingested pull requests; the source does not crawl every default-branch commit independently."]
            if missing:
                notes.append("Missing pinned GitHub source state: " + ", ".join(missing))
            return GitHubFinderView(
                snapshot_id=snapshot.id, snapshot_name=snapshot.name or snapshot.id,
                snapshot_created_at=_as_utc(snapshot.created_at),
                source_freshness={name: _as_utc(state.high_water_mark) for name, state in states.items()},
                records=records, data_quality_notes=notes,
            )


def _record_date(record: GitHubFinderRecord) -> datetime | None:
    return record.merged_at or record.updated_at or record.committed_at or record.authored_at


def _utc(value: datetime | None) -> datetime | None:
    return _as_utc(value) if value is not None else None


def _coding_hours(created_at: datetime, first_commit_at: datetime | None) -> float | None:
    if first_commit_at is None:
        return None
    return max((created_at - first_commit_at).total_seconds() / 3600, 0.0)


def _commit_boundary(commit: GitHubCommit) -> datetime | None:
    return _utc(commit.committed_at)


def _relationships(session: Session, record_keys, high_water):
    result: dict[tuple[str, str], dict[str, str]] = defaultdict(dict)
    for record_type, record_id in record_keys:
        rows = session.execute(
            select(JiraGitHubRelationship, JiraIssue)
            .join(JiraIssue, JiraIssue.id == JiraGitHubRelationship.jira_issue_id)
            .where(
                JiraGitHubRelationship.github_record_type == record_type,
                JiraGitHubRelationship.github_record_id == record_id,
                JiraGitHubRelationship.first_seen_at <= high_water,
            )
        ).all()
        for _, issue in rows:
            result[(record_type, record_id)][issue.issue_key] = issue.web_url
    return result
