"""Query durable flag state as a deterministic Attention inbox."""

from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from engineering_intelligence.persistence.models import (
    FlagEvent,
    FlagEvidence,
    FlagOccurrence,
    FlagUserState,
    LogicalFlag,
    SignalDefinition,
    SignalEvaluation,
    Snapshot,
    Team,
)
from engineering_intelligence.presentations.attention import (
    AttentionCollection,
    AttentionEvent,
    AttentionEvidence,
    AttentionFlag,
    AttentionInbox,
    AttentionOccurrence,
)


class AttentionQuery:
    def __init__(self, sessions: sessionmaker[Session], *, max_flags: int = 250) -> None:
        self.sessions = sessions
        self.max_flags = max_flags

    def list(
        self,
        *,
        collection: AttentionCollection = AttentionCollection.active,
        unread_only: bool = False,
        team: str | None = None,
        now: datetime | None = None,
    ) -> AttentionInbox:
        generated_at = now or datetime.now(UTC)
        with self.sessions() as session:
            records = self._records(session, generated_at)
            counts = {
                item.value: sum(flag.collection == item for flag in records)
                for item in AttentionCollection
                if item != AttentionCollection.all
            }
            filtered = [
                flag
                for flag in records
                if (collection == AttentionCollection.all or flag.collection == collection)
                and (not unread_only or flag.unread)
                and (
                    team is None
                    or team.casefold() in {flag.team_id.casefold(), flag.team_name.casefold()}
                )
            ]
            return AttentionInbox(
                generated_at=generated_at,
                collection=collection,
                unread_only=unread_only,
                team=team,
                counts=counts,
                flags=filtered[: self.max_flags],
                data_quality_notes=(
                    ["No flag evaluations have been persisted; render a Dashboard first."]
                    if not records
                    else []
                ),
            )

    def get(
        self,
        fingerprint: str,
        *,
        now: datetime | None = None,
    ) -> AttentionFlag:
        generated_at = now or datetime.now(UTC)
        with self.sessions() as session:
            records = self._records(session, generated_at)
            match = next(
                (record for record in records if record.fingerprint == fingerprint),
                None,
            )
            if match is None:
                raise ValueError(f"Flag not found: {fingerprint}")
            return match

    def _records(self, session: Session, now: datetime) -> list[AttentionFlag]:
        logical_flags = session.scalars(
            select(LogicalFlag).order_by(
                LogicalFlag.active.desc(),
                LogicalFlag.last_seen_at.desc(),
                LogicalFlag.fingerprint,
            )
        ).all()
        team_names = {
            team.id: team.name for team in session.scalars(select(Team)).all()
        }
        records = []
        for logical in logical_flags:
            occurrences = session.scalars(
                select(FlagOccurrence)
                .where(
                    FlagOccurrence.logical_flag_fingerprint == logical.fingerprint
                )
                .order_by(FlagOccurrence.opened_at.desc())
            ).all()
            if not occurrences:
                continue
            current = next(
                (occurrence for occurrence in occurrences if occurrence.resolved_at is None),
                occurrences[0],
            )
            state = session.get(FlagUserState, logical.fingerprint)
            collection = _collection(logical, state, now)
            occurrence_models = [
                self._occurrence(session, occurrence) for occurrence in occurrences
            ]
            current_events = occurrence_models[occurrences.index(current)].events
            evidence = self._evidence(session, current.id)
            evaluation = session.scalar(
                select(SignalEvaluation)
                .where(SignalEvaluation.flag_fingerprint == logical.fingerprint)
                .order_by(SignalEvaluation.evaluated_at.desc())
                .limit(1)
            )
            definition = (
                session.get(SignalDefinition, evaluation.signal_definition_id)
                if evaluation
                else None
            )
            latest_event_at = max(
                (event.occurred_at for item in occurrence_models for event in item.events),
                default=_as_utc(logical.last_seen_at),
            )
            user_updated_at = _as_utc(state.updated_at) if state else latest_event_at
            explanation = next(
                (
                    event.explanation
                    for event in reversed(current_events)
                    if event.explanation
                ),
                None,
            )
            records.append(
                AttentionFlag(
                    fingerprint=logical.fingerprint,
                    occurrence_id=current.id,
                    collection=collection,
                    unread=state is None or state.unread_since is not None,
                    title=logical.title,
                    team_id=logical.team_id,
                    team_name=team_names.get(logical.team_id, logical.team_id),
                    health_area=logical.area,
                    severity=logical.current_severity,
                    signal_definition_key=(
                        definition.definition_key if definition else None
                    ),
                    signal_definition_version=definition.version if definition else None,
                    signal_evaluation_id=evaluation.id if evaluation else None,
                    explanation=explanation,
                    condition_started_at=_as_utc(current.opened_at),
                    first_detected_at=_as_utc(logical.first_seen_at),
                    last_observed_at=_as_utc(logical.last_seen_at),
                    last_updated_at=max(latest_event_at, user_updated_at),
                    active_duration_seconds=_duration_seconds(current, now),
                    evidence=evidence,
                    evidence_count=len(evidence),
                    affected_entities=[
                        f"team:{team_names.get(logical.team_id, logical.team_id)}",
                        *(
                            [f"feature:{evaluation.scope_id}"]
                            if evaluation and evaluation.scope_type == "feature"
                            else []
                        ),
                    ],
                    confidence=evaluation.confidence if evaluation else "high",
                    investigation_questions=_questions(logical.area),
                    viewed_at=_as_utc(state.viewed_at) if state and state.viewed_at else None,
                    understood_at=(
                        _as_utc(state.understood_at)
                        if state and state.understood_at
                        else None
                    ),
                    snoozed_until=(
                        _as_utc(state.snoozed_until)
                        if state and state.snoozed_until
                        else None
                    ),
                    severity_history=[
                        event
                        for item in occurrence_models
                        for event in item.events
                        if event.event_type in {"opened", "escalated", "deescalated"}
                    ],
                    occurrences=occurrence_models,
                )
            )
        return records

    @staticmethod
    def _occurrence(
        session: Session, occurrence: FlagOccurrence
    ) -> AttentionOccurrence:
        events = session.scalars(
            select(FlagEvent)
            .where(FlagEvent.occurrence_id == occurrence.id)
            .order_by(FlagEvent.occurred_at, FlagEvent.id)
        ).all()
        return AttentionOccurrence(
            occurrence_id=occurrence.id,
            opened_at=_as_utc(occurrence.opened_at),
            resolved_at=(
                _as_utc(occurrence.resolved_at) if occurrence.resolved_at else None
            ),
            latest_snapshot_id=occurrence.latest_snapshot_id,
            events=[
                AttentionEvent(
                    event_type=event.event_type,
                    severity=event.severity,
                    occurred_at=_as_utc(event.occurred_at),
                    snapshot_id=event.snapshot_id,
                    explanation=event.details.get("explanation"),
                )
                for event in events
            ],
        )

    @staticmethod
    def _evidence(session: Session, occurrence_id: str) -> list[AttentionEvidence]:
        rows = session.scalars(
            select(FlagEvidence)
            .join(Snapshot, Snapshot.id == FlagEvidence.snapshot_id)
            .where(FlagEvidence.occurrence_id == occurrence_id)
            .order_by(Snapshot.created_at.desc(), FlagEvidence.label, FlagEvidence.url)
        ).all()
        latest_by_url = {}
        for row in rows:
            latest_by_url.setdefault(row.url, row)
        return [
            AttentionEvidence(
                label=row.label,
                url=row.url,
                jira_key=row.jira_key,
                title=row.title,
                snapshot_id=row.snapshot_id,
            )
            for row in latest_by_url.values()
        ]


def _collection(
    logical: LogicalFlag,
    state: FlagUserState | None,
    now: datetime,
) -> AttentionCollection:
    if not logical.active:
        return AttentionCollection.resolved
    if state and state.snoozed_until and _as_utc(state.snoozed_until) > now:
        return AttentionCollection.snoozed
    if state and state.understood_at:
        return AttentionCollection.understood
    return AttentionCollection.active


def _duration_seconds(occurrence: FlagOccurrence, now: datetime) -> int:
    end = _as_utc(occurrence.resolved_at) if occurrence.resolved_at else now
    return max(0, int((end - _as_utc(occurrence.opened_at)).total_seconds()))


def _questions(area: str) -> list[str]:
    return {
        "work_in_flight": [
            "Is the team intentionally between delivery commitments?",
            "Is active work represented below the IBR level but missing from the board?",
        ],
        "near_term_pipeline": [
            "Which Feature is expected to become ready next?",
            "Is product or technical discovery blocking the near-term pipeline?",
        ],
        "stalled_work": [
            "Is work progressing somewhere that is not linked back to Jira?",
            "Is a blocker, dependency, or ownership decision preventing visible progress?",
        ],
        "target_risk": [
            "Is the target date still current and achievable?",
            "What changed in scope, dependencies, or sequencing after the date was set?",
        ],
        "ownership_gap": [
            "Who is currently coordinating this Feature?",
            "Does Jira reflect the actual ownership and contributor model?",
        ],
        "workflow_regression": [
            "What new information caused this Feature to move backward?",
            "Does the workflow change reflect rework, scope clarification, or a correction?",
        ],
        "workflow_cycling": [
            "What is causing this Feature to revisit the same stage?",
            "Would clearer entry or exit criteria reduce repeated handoffs?",
        ],
        "stage_aging": [
            "What differs from the team's recently completed work in this stage?",
            "Is the baseline comparison missing complexity, dependency, or priority context?",
        ],
    }.get(
        area,
        [
            "What changed when this condition began?",
            "Which linked evidence should be checked before drawing a conclusion?",
        ],
    )


def _as_utc(value: datetime) -> datetime:
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)
