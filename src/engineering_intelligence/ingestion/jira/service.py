"""Idempotent Jira board ingestion orchestration."""

from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from sqlalchemy import delete, select
from sqlalchemy.orm import Session, sessionmaker

from engineering_intelligence.ingestion.archive import RawPayloadArchive
from engineering_intelligence.ingestion.jira.client import JiraClient
from engineering_intelligence.ingestion.jira.normalization import normalize_issue
from engineering_intelligence.persistence.models import (
    Board,
    BoardColumn,
    BoardMembershipObservation,
    IngestionRun,
    JiraIssue,
    JiraIssueVersion,
    JiraRelationship,
    JiraScopeObservation,
    JiraStatusTransition,
    RawPayload,
)


class JiraIngestionService:
    def __init__(
        self,
        sessions: sessionmaker[Session],
        archive: RawPayloadArchive,
        client: JiraClient,
        *,
        base_url: str,
        team_field_id: str | None = None,
        target_date_field_id: str | None = None,
        gravitee_customers_field_id: str | None = None,
        hierarchy_max_depth: int = 10,
        hierarchy_batch_size: int = 40,
    ) -> None:
        self.sessions = sessions
        self.archive = archive
        self.client = client
        self.base_url = base_url
        self.team_field_id = team_field_id
        self.target_date_field_id = target_date_field_id
        self.gravitee_customers_field_id = gravitee_customers_field_id
        self.hierarchy_max_depth = hierarchy_max_depth
        self.hierarchy_batch_size = hierarchy_batch_size

    def ingest_board(self, board_id: int, *, observed_at: datetime | None = None) -> str:
        observed_at = observed_at or datetime.now(UTC)
        run_id = str(uuid4())
        with self.sessions.begin() as session:
            session.add(
                IngestionRun(
                    id=run_id,
                    source="jira",
                    started_at=observed_at,
                    completed_at=None,
                    status="running",
                    request_context={"board_id": board_id},
                    records_seen=0,
                    records_changed=0,
                    error=None,
                )
            )

        try:
            configuration = self.client.get_board_configuration(board_id)
            with self.sessions.begin() as session:
                self._record_payload(
                    session,
                    run_id,
                    "board_configuration",
                    str(board_id),
                    configuration,
                    observed_at,
                    {"board_id": board_id},
                )
                self._upsert_board(session, configuration, observed_at)

            seen = 0
            changed = 0
            seen_issue_ids: set[str] = set()
            frontier_keys: list[str] = []
            requested_fields = self._requested_fields(configuration)
            for payload in self.client.iter_board_issues(
                board_id,
                fields=requested_fields,
            ):
                seen += 1
                seen_issue_ids.add(str(payload["id"]))
                frontier_keys.append(payload["key"])
                with self.sessions.begin() as session:
                    self._record_payload(
                        session,
                        run_id,
                        "issue",
                        str(payload["id"]),
                        payload,
                        observed_at,
                        {"board_id": board_id},
                    )
                    changed += self._upsert_issue(session, payload, observed_at)
                    # Make the issue visible to SQLite before inserting the
                    # board-membership row that references it.
                    session.flush()
                    session.add(
                        BoardMembershipObservation(
                            id=str(uuid4()),
                            board_id=board_id,
                            issue_id=str(payload["id"]),
                            observed_at=observed_at,
                            ingestion_run_id=run_id,
                        )
                    )
            hierarchy_seen, hierarchy_changed = self._ingest_hierarchy(
                run_id,
                board_id,
                observed_at,
                requested_fields,
                frontier_keys,
                seen_issue_ids,
            )
            seen += hierarchy_seen
            changed += hierarchy_changed
            changelog_seen, changelog_changed = self._ingest_status_changelogs(
                run_id,
                board_id,
                observed_at,
                seen_issue_ids,
            )
            seen += changelog_seen
            changed += changelog_changed
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

    def ingest_query(
        self,
        scope_id: str,
        jql: str,
        *,
        observed_at: datetime | None = None,
    ) -> str:
        """Archive and normalize one explicitly configured named JQL scope."""
        observed_at = observed_at or datetime.now(UTC)
        run_id = str(uuid4())
        with self.sessions.begin() as session:
            session.add(
                IngestionRun(
                    id=run_id,
                    source="jira",
                    started_at=observed_at,
                    completed_at=None,
                    status="running",
                    request_context={"query_id": scope_id, "jql": jql},
                    records_seen=0,
                    records_changed=0,
                    error=None,
                )
            )
        try:
            seen = 0
            changed = 0
            issue_ids: set[str] = set()
            for payload in self.client.iter_jql_issues(
                jql,
                fields=self._requested_fields({}),
            ):
                issue_id = str(payload["id"])
                if issue_id in issue_ids:
                    continue
                issue_ids.add(issue_id)
                seen += 1
                with self.sessions.begin() as session:
                    self._record_payload(
                        session,
                        run_id,
                        "query_issue",
                        issue_id,
                        payload,
                        observed_at,
                        {"query_id": scope_id},
                    )
                    changed += self._upsert_issue(session, payload, observed_at)
                    session.flush()
                    session.add(
                        JiraScopeObservation(
                            id=str(uuid4()),
                            scope_id=scope_id,
                            issue_id=issue_id,
                            observed_at=observed_at,
                            ingestion_run_id=run_id,
                        )
                    )
            transition_seen, transition_changed = self._ingest_status_changelogs(
                run_id,
                None,
                observed_at,
                issue_ids,
                scope_id=scope_id,
            )
            seen += transition_seen
            changed += transition_changed
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

    def _ingest_status_changelogs(
        self,
        run_id: str,
        board_id: int | None,
        observed_at: datetime,
        issue_ids: set[str],
        *,
        scope_id: str | None = None,
    ) -> tuple[int, int]:
        seen = 0
        changed = 0
        for issue_log in self.client.iter_issue_changelogs(
            sorted(issue_ids),
            field_ids=["status"],
        ):
            issue_id = str(issue_log["issueId"])
            with self.sessions.begin() as session:
                self._record_payload(
                    session,
                    run_id,
                    "status_changelog",
                    issue_id,
                    issue_log,
                    observed_at,
                    {
                        "board_id": board_id,
                        "query_id": scope_id,
                        "field_ids": ["status"],
                    },
                )
                for history in issue_log.get("changeHistories") or []:
                    changed_at = _changelog_datetime(history.get("created"))
                    author = history.get("author") or {}
                    for item_index, item in enumerate(history.get("items") or []):
                        if (item.get("fieldId") or item.get("field") or "").casefold() != (
                            "status"
                        ):
                            continue
                        seen += 1
                        current = session.scalar(
                            select(JiraStatusTransition).where(
                                JiraStatusTransition.issue_id == issue_id,
                                JiraStatusTransition.changelog_id == str(history["id"]),
                                JiraStatusTransition.item_index == item_index,
                            )
                        )
                        if current:
                            current.last_seen_at = observed_at
                            continue
                        session.add(
                            JiraStatusTransition(
                                id=str(uuid4()),
                                issue_id=issue_id,
                                changelog_id=str(history["id"]),
                                item_index=item_index,
                                changed_at=changed_at,
                                author_account_id=author.get("accountId"),
                                author_display_name=author.get("displayName"),
                                from_status_id=_optional_string(item.get("from")),
                                from_status_name=item.get("fromString"),
                                to_status_id=_optional_string(item.get("to")),
                                to_status_name=item.get("toString"),
                                first_seen_at=observed_at,
                                last_seen_at=observed_at,
                            )
                        )
                        changed += 1
        return seen, changed

    def _ingest_hierarchy(
        self,
        run_id: str,
        board_id: int,
        observed_at: datetime,
        requested_fields: list[str],
        frontier_keys: list[str],
        seen_issue_ids: set[str],
    ) -> tuple[int, int]:
        hierarchy_seen = 0
        hierarchy_changed = 0
        for depth in range(1, self.hierarchy_max_depth + 1):
            next_frontier: list[str] = []
            for start in range(0, len(frontier_keys), self.hierarchy_batch_size):
                parent_keys = frontier_keys[start : start + self.hierarchy_batch_size]
                for payload in self.client.iter_child_issues(
                    parent_keys,
                    fields=requested_fields,
                ):
                    issue_id = str(payload["id"])
                    if issue_id in seen_issue_ids:
                        continue
                    seen_issue_ids.add(issue_id)
                    next_frontier.append(payload["key"])
                    hierarchy_seen += 1
                    with self.sessions.begin() as session:
                        self._record_payload(
                            session,
                            run_id,
                            "hierarchy_issue",
                            issue_id,
                            payload,
                            observed_at,
                            {
                                "board_id": board_id,
                                "hierarchy_depth": depth,
                                "parent_keys": parent_keys,
                            },
                        )
                        hierarchy_changed += self._upsert_issue(
                            session,
                            payload,
                            observed_at,
                        )
            if not next_frontier:
                break
            frontier_keys = next_frontier
        return hierarchy_seen, hierarchy_changed

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
        archived = self.archive.put("jira", payload)
        exists = session.scalar(
            select(RawPayload.id).where(
                RawPayload.source == "jira",
                RawPayload.content_hash == archived.content_hash,
            )
        )
        if not exists:
            session.add(
                RawPayload(
                    id=str(uuid4()),
                    ingestion_run_id=run_id,
                    source="jira",
                    record_type=record_type,
                    source_record_id=record_id,
                    retrieved_at=observed_at,
                    content_hash=archived.content_hash,
                    object_path=str(archived.path),
                    api_version="3/agile-1.0",
                    request_context=request_context,
                )
            )

    def _upsert_board(
        self,
        session: Session,
        payload: dict[str, Any],
        observed_at: datetime,
    ) -> None:
        board_id = int(payload["id"])
        board = session.get(Board, board_id)
        values = {
            "name": payload["name"],
            "board_type": payload.get("type") or "unknown",
            "filter_id": str((payload.get("filter") or {}).get("id") or "") or None,
            "source_url": f"{self.base_url.rstrip('/')}/jira/software/c/boards/{board_id}",
            "observed_at": observed_at,
        }
        if board:
            for key, value in values.items():
                setattr(board, key, value)
        else:
            session.add(Board(id=board_id, **values))
        session.execute(delete(BoardColumn).where(BoardColumn.board_id == board_id))
        for position, column in enumerate(
            (payload.get("columnConfig") or {}).get("columns") or []
        ):
            statuses = column.get("statuses") or [None]
            for status in statuses:
                session.add(
                    BoardColumn(
                        board_id=board_id,
                        name=column["name"],
                        position=position,
                        status_id=str(status["id"]) if status else None,
                    )
                )

    def _upsert_issue(
        self,
        session: Session,
        payload: dict[str, Any],
        observed_at: datetime,
    ) -> int:
        normalized = normalize_issue(
            payload,
            base_url=self.base_url,
            observed_at=observed_at,
            team_field_id=self.team_field_id,
            target_date_field_id=self.target_date_field_id,
            gravitee_customers_field_id=self.gravitee_customers_field_id,
        )
        issue = session.get(JiraIssue, normalized.issue["id"])
        changed = issue is None or issue.current_version_hash != normalized.version_hash
        if issue is None:
            issue = JiraIssue(**normalized.issue)
            session.add(issue)
        else:
            issue.issue_key = normalized.issue["issue_key"]
            issue.self_url = normalized.issue["self_url"]
            issue.web_url = normalized.issue["web_url"]
            issue.project_key = normalized.issue["project_key"]
            issue.last_seen_at = observed_at
            issue.last_source_updated_at = normalized.issue["last_source_updated_at"]
            issue.current_version_hash = normalized.version_hash
            issue.is_deleted = False
        version_exists = session.scalar(
            select(JiraIssueVersion.id).where(
                JiraIssueVersion.issue_id == normalized.issue["id"],
                JiraIssueVersion.version_hash == normalized.version_hash,
            )
        )
        if changed and version_exists is None:
            session.add(
                JiraIssueVersion(
                    id=str(uuid4()),
                    observed_at=observed_at,
                    version_hash=normalized.version_hash,
                    **normalized.version,
                )
            )
        for relationship in normalized.relationships:
            current = session.scalar(
                select(JiraRelationship).where(
                    JiraRelationship.source_issue_id == relationship["source_issue_id"],
                    JiraRelationship.target_issue_id == relationship["target_issue_id"],
                    JiraRelationship.relationship_type == relationship["relationship_type"],
                )
            )
            if current:
                current.last_seen_at = observed_at
                current.active = True
                current.source_description = relationship["source_description"]
                # Relationship metadata is first-observed evidence until a
                # versioned relationship-history model is introduced.
                current.target_issue_key = (
                    current.target_issue_key or relationship["target_issue_key"]
                )
                current.target_summary = (
                    current.target_summary or relationship["target_summary"]
                )
                current.target_status = (
                    current.target_status or relationship["target_status"]
                )
                current.target_url = current.target_url or relationship["target_url"]
            else:
                session.add(
                    JiraRelationship(
                        id=str(uuid4()),
                        first_seen_at=observed_at,
                        last_seen_at=observed_at,
                        active=True,
                        **relationship,
                    )
                )
        return int(changed)

    def _requested_fields(self, configuration: dict[str, Any]) -> list[str]:
        fields = [
            "summary",
            "description",
            "issuetype",
            "status",
            "assignee",
            "project",
            "created",
            "updated",
            "resolutiondate",
            "parent",
            "subtasks",
            "issuelinks",
            "labels",
            "components",
            "fixVersions",
        ]
        rank_id = (configuration.get("ranking") or {}).get("rankCustomFieldId")
        for field_id in (
            rank_id,
            self.team_field_id,
            self.target_date_field_id,
            self.gravitee_customers_field_id,
        ):
            if field_id is None:
                continue
            field = str(field_id)
            fields.append(field if field.startswith("customfield_") else f"customfield_{field}")
        return fields


def _optional_string(value: Any) -> str | None:
    return str(value) if value is not None else None


def _changelog_datetime(value: str | float | None) -> datetime:
    if isinstance(value, (int, float)):
        # Jira's bulk API documentation shows epoch seconds, while live Cloud
        # tenants may return epoch milliseconds.
        epoch_seconds = value / 1000 if value > 100_000_000_000 else value
        return datetime.fromtimestamp(epoch_seconds, UTC)
    if isinstance(value, str):
        parsed = datetime.fromisoformat(value)
        return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)
    raise ValueError("Jira changelog is missing a created timestamp")
