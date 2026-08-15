"""Idempotent GitHub delivery ingestion and explicit Jira-key attribution."""

import hashlib
import json
import re
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from engineering_intelligence.ingestion.archive import RawPayloadArchive
from engineering_intelligence.ingestion.github.client import GitHubClient
from engineering_intelligence.persistence.models import (
    GitHubCommit,
    GitHubPullRequest,
    GitHubPullRequestCommit,
    GitHubPullRequestVersion,
    GitHubRepository,
    GitHubReview,
    IngestionRun,
    JiraGitHubRelationship,
    JiraIssue,
    RawPayload,
)

JIRA_KEY = re.compile(r"(?<![A-Z0-9_])([A-Z][A-Z0-9_]+-\d+)(?!\d)")


class GitHubIngestionService:
    def __init__(
        self,
        sessions: sessionmaker[Session],
        archive: RawPayloadArchive,
        client: GitHubClient,
        *,
        initial_lookback_days: int = 90,
        max_pull_requests: int = 500,
        min_refresh_window_days: int = 31,
    ) -> None:
        self.sessions = sessions
        self.archive = archive
        self.client = client
        self.initial_lookback_days = initial_lookback_days
        self.max_pull_requests = max_pull_requests
        self.min_refresh_window_days = min_refresh_window_days

    def ingest_repository(
        self,
        full_name: str,
        *,
        observed_at: datetime | None = None,
    ) -> str:
        observed_at = observed_at or datetime.now(UTC)
        run_id = str(uuid4())
        with self.sessions.begin() as session:
            session.add(
                IngestionRun(
                    id=run_id,
                    source="github",
                    started_at=observed_at,
                    completed_at=None,
                    status="running",
                    request_context={"repository": full_name},
                    records_seen=0,
                    records_changed=0,
                    error=None,
                )
            )
        try:
            repository_payload = self.client.get_repository(full_name)
            with self.sessions.begin() as session:
                self._record_payload(
                    session,
                    run_id,
                    "repository",
                    str(repository_payload["id"]),
                    repository_payload,
                    observed_at,
                    {"repository": full_name},
                )
                repository = self._upsert_repository(
                    session,
                    repository_payload,
                    observed_at,
                )
                repository_id = repository.id
            updated_since = observed_at - timedelta(days=self.initial_lookback_days)
            min_updated_since = observed_at - timedelta(
                days=self.min_refresh_window_days
            )
            seen = 1
            changed = 0
            for pull in self.client.iter_pull_requests(
                full_name,
                updated_since=updated_since,
                max_records=self.max_pull_requests,
                min_updated_since=min_updated_since,
            ):
                commits = list(
                    self.client.iter_pull_request_commits(full_name, int(pull["number"]))
                )
                reviews = list(
                    self.client.iter_pull_request_reviews(full_name, int(pull["number"]))
                )
                seen += 1 + len(commits) + len(reviews)
                with self.sessions.begin() as session:
                    self._record_payload(
                        session,
                        run_id,
                        "pull_request",
                        str(pull["id"]),
                        pull,
                        observed_at,
                        {"repository": full_name, "number": pull["number"]},
                    )
                    changed += self._upsert_pull_request(
                        session,
                        repository_id,
                        full_name,
                        pull,
                        commits,
                        reviews,
                        run_id,
                        observed_at,
                    )
            with self.sessions.begin() as session:
                run = session.get(IngestionRun, run_id)
                assert run is not None
                run.status = "completed"
                run.completed_at = datetime.now(UTC)
                run.records_seen = seen
                run.records_changed = changed
            return run_id
        except Exception as error:
            with self.sessions.begin() as session:
                run = session.get(IngestionRun, run_id)
                assert run is not None
                run.status = "failed"
                run.completed_at = datetime.now(UTC)
                run.error = f"{type(error).__name__}: {error}"
            raise

    def _upsert_repository(
        self,
        session: Session,
        payload: dict[str, Any],
        observed_at: datetime,
    ) -> GitHubRepository:
        repository = session.get(GitHubRepository, int(payload["id"]))
        if repository is None:
            repository = GitHubRepository(
                id=int(payload["id"]),
                full_name=payload["full_name"],
                html_url=payload["html_url"],
                default_branch=payload.get("default_branch"),
                private=bool(payload.get("private")),
                archived=bool(payload.get("archived")),
                first_seen_at=observed_at,
                last_seen_at=observed_at,
            )
            session.add(repository)
        else:
            repository.full_name = payload["full_name"]
            repository.html_url = payload["html_url"]
            repository.default_branch = payload.get("default_branch")
            repository.private = bool(payload.get("private"))
            repository.archived = bool(payload.get("archived"))
            repository.last_seen_at = observed_at
        session.flush()
        return repository

    def _upsert_pull_request(
        self,
        session: Session,
        repository_id: int,
        full_name: str,
        payload: dict[str, Any],
        commits: list[dict[str, Any]],
        reviews: list[dict[str, Any]],
        run_id: str,
        observed_at: datetime,
    ) -> int:
        pull_id = f"{full_name}#{payload['number']}"
        version = _pull_version(payload)
        version_hash = _hash(version)
        pull = session.get(GitHubPullRequest, pull_id)
        changed = pull is None or pull.current_version_hash != version_hash
        if pull is None:
            pull = GitHubPullRequest(
                id=pull_id,
                repository_id=repository_id,
                number=int(payload["number"]),
                html_url=payload["html_url"],
                first_seen_at=observed_at,
                last_seen_at=observed_at,
                current_version_hash=version_hash,
            )
            session.add(pull)
        else:
            pull.html_url = payload["html_url"]
            pull.last_seen_at = observed_at
            pull.current_version_hash = version_hash
        if changed:
            session.add(
                GitHubPullRequestVersion(
                    id=str(uuid4()),
                    pull_request_id=pull_id,
                    observed_at=observed_at,
                    version_hash=version_hash,
                    **version,
                )
            )
        session.flush()
        self._link_jira_keys(
            session,
            "pull_request",
            pull_id,
            payload["html_url"],
            {
                "title": payload.get("title") or "",
                "body": payload.get("body") or "",
                "branch": (payload.get("head") or {}).get("ref") or "",
            },
            observed_at,
        )
        for commit in commits:
            self._record_payload(
                session,
                run_id,
                "commit",
                commit["sha"],
                commit,
                observed_at,
                {"repository": full_name, "pull_request": payload["number"]},
            )
            self._upsert_commit(
                session,
                repository_id,
                pull_id,
                commit,
                observed_at,
            )
        for review in reviews:
            self._record_payload(
                session,
                run_id,
                "review",
                str(review["id"]),
                review,
                observed_at,
                {"repository": full_name, "pull_request": payload["number"]},
            )
            self._upsert_review(session, pull_id, review, observed_at)
        return int(changed)

    def _upsert_commit(
        self,
        session: Session,
        repository_id: int,
        pull_id: str,
        payload: dict[str, Any],
        observed_at: datetime,
    ) -> None:
        commit_data = payload.get("commit") or {}
        author_data = commit_data.get("author") or {}
        committer_data = commit_data.get("committer") or {}
        commit = session.get(GitHubCommit, payload["sha"])
        if commit is None:
            commit = GitHubCommit(
                sha=payload["sha"],
                repository_id=repository_id,
                html_url=payload["html_url"],
                message=commit_data.get("message") or "",
                author_login=(payload.get("author") or {}).get("login"),
                author_name=author_data.get("name"),
                authored_at=_date(author_data.get("date")),
                committed_at=_date(committer_data.get("date")),
                first_seen_at=observed_at,
                last_seen_at=observed_at,
            )
            session.add(commit)
        else:
            commit.last_seen_at = observed_at
        session.flush()
        if session.get(GitHubPullRequestCommit, (pull_id, payload["sha"])) is None:
            session.add(
                GitHubPullRequestCommit(
                    pull_request_id=pull_id,
                    commit_sha=payload["sha"],
                )
            )
        self._link_jira_keys(
            session,
            "commit",
            payload["sha"],
            payload["html_url"],
            {"message": commit_data.get("message") or ""},
            observed_at,
        )

    @staticmethod
    def _upsert_review(
        session: Session,
        pull_id: str,
        payload: dict[str, Any],
        observed_at: datetime,
    ) -> None:
        review = session.get(GitHubReview, str(payload["id"]))
        values = {
            "pull_request_id": pull_id,
            "html_url": payload.get("html_url"),
            "state": payload.get("state") or "UNKNOWN",
            "author_login": (payload.get("user") or {}).get("login"),
            "submitted_at": _date(payload.get("submitted_at")),
            "observed_at": observed_at,
        }
        if review is None:
            session.add(GitHubReview(id=str(payload["id"]), **values))
        else:
            for field, value in values.items():
                setattr(review, field, value)

    @staticmethod
    def _link_jira_keys(
        session: Session,
        record_type: str,
        record_id: str,
        evidence_url: str,
        fields: dict[str, str],
        observed_at: datetime,
    ) -> None:
        for field, text in fields.items():
            for key in sorted(set(JIRA_KEY.findall(text.upper()))):
                issue = session.scalar(select(JiraIssue).where(JiraIssue.issue_key == key))
                if issue is None:
                    continue
                current = session.scalar(
                    select(JiraGitHubRelationship).where(
                        JiraGitHubRelationship.jira_issue_id == issue.id,
                        JiraGitHubRelationship.github_record_type == record_type,
                        JiraGitHubRelationship.github_record_id == record_id,
                        JiraGitHubRelationship.relationship_type == "explicit_jira_key",
                    )
                )
                evidence = f"{key} found in GitHub {record_type} {field}"
                if current:
                    current.last_seen_at = observed_at
                else:
                    session.add(
                        JiraGitHubRelationship(
                            id=str(uuid4()),
                            jira_issue_id=issue.id,
                            github_record_type=record_type,
                            github_record_id=record_id,
                            relationship_type="explicit_jira_key",
                            confidence="confirmed",
                            evidence=evidence,
                            evidence_url=evidence_url,
                            first_seen_at=observed_at,
                            last_seen_at=observed_at,
                        )
                    )

    def _record_payload(
        self,
        session: Session,
        run_id: str,
        record_type: str,
        record_id: str,
        payload: dict[str, Any],
        observed_at: datetime,
        request_context: dict[str, Any],
    ) -> None:
        archived = self.archive.put("github", payload)
        exists = session.scalar(
            select(RawPayload.id).where(
                RawPayload.source == "github",
                RawPayload.content_hash == archived.content_hash,
            )
        )
        if not exists:
            session.add(
                RawPayload(
                    id=str(uuid4()),
                    ingestion_run_id=run_id,
                    source="github",
                    record_type=record_type,
                    source_record_id=record_id,
                    retrieved_at=observed_at,
                    content_hash=archived.content_hash,
                    object_path=str(archived.path),
                    api_version="2022-11-28",
                    request_context=request_context,
                )
            )


def _pull_version(payload: dict[str, Any]) -> dict[str, Any]:
    return {
        "title": payload.get("title") or "",
        "body": payload.get("body"),
        "state": payload.get("state") or "unknown",
        "draft": bool(payload.get("draft")),
        "author_login": (payload.get("user") or {}).get("login"),
        "head_ref": (payload.get("head") or {}).get("ref") or "",
        "base_ref": (payload.get("base") or {}).get("ref") or "",
        "source_created_at": _date(payload["created_at"]),
        "source_updated_at": _date(payload["updated_at"]),
        "closed_at": _date(payload.get("closed_at")),
        "merged_at": _date(payload.get("merged_at")),
        "merge_commit_sha": payload.get("merge_commit_sha"),
    }


def _hash(value: dict[str, Any]) -> str:
    payload = json.dumps(value, sort_keys=True, default=str, separators=(",", ":"))
    return hashlib.sha256(payload.encode()).hexdigest()


def _date(value: str | None) -> datetime | None:
    return datetime.fromisoformat(value) if value else None
