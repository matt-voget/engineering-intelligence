"""Deduplicate, re-open, resolve, and track user state for health flags."""

from datetime import UTC, datetime
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from engineering_intelligence.flags.definitions import (
    DASHBOARD_SIGNAL_DEFINITIONS,
    SignalDefinitionSpec,
)
from engineering_intelligence.persistence.models import (
    FlagEvent,
    FlagEvidence,
    FlagOccurrence,
    FlagUserState,
    LogicalFlag,
    SignalDefinition,
    SignalEvaluation,
)
from engineering_intelligence.presentations.dashboard import (
    Dashboard,
    HealthFlag,
    SignalEvaluationInput,
)


class FlagService:
    def __init__(self, sessions: sessionmaker[Session]) -> None:
        self.sessions = sessions

    def record_dashboard(self, dashboard: Dashboard) -> Dashboard:
        """Record one idempotent lifecycle evaluation for a Dashboard snapshot."""
        evaluated_at = dashboard.snapshot_created_at
        current = {
            flag.fingerprint: (team.team_id, flag)
            for team in dashboard.teams
            for flag in team.flags
        }
        evaluable_team_ids = [
            team.team_id
            for team in dashboard.teams
            if team.health.value != "unknown"
        ]
        with self.sessions.begin() as session:
            self._record_evaluations(session, dashboard)
            for team_id, flag in current.values():
                self._record_active(
                    session,
                    team_id,
                    flag,
                    dashboard.snapshot_id,
                    evaluated_at,
                )
            previous = session.scalars(
                select(LogicalFlag).where(
                    LogicalFlag.team_id.in_(evaluable_team_ids),
                    LogicalFlag.active.is_(True),
                )
            ).all()
            for logical in previous:
                if logical.fingerprint not in current:
                    self._resolve(
                        session,
                        logical,
                        dashboard.snapshot_id,
                        evaluated_at,
                    )
            session.flush()
            for _team_id, flag in current.values():
                state = session.get(FlagUserState, flag.fingerprint)
                occurrence = self._active_occurrence(session, flag.fingerprint)
                flag.occurrence_id = occurrence.id
                flag.unread = state is None or state.unread_since is not None
        return dashboard

    def _record_evaluations(self, session: Session, dashboard: Dashboard) -> None:
        definitions = {
            (spec.definition_key, spec.version): self._definition(
                session,
                spec,
                dashboard.snapshot_created_at,
            )
            for spec in DASHBOARD_SIGNAL_DEFINITIONS
        }
        specs = {
            (spec.definition_key, spec.version): spec
            for spec in DASHBOARD_SIGNAL_DEFINITIONS
        }
        for team in dashboard.teams:
            inputs = (
                team.signal_evaluation_inputs
                if team.signal_evaluation_inputs
                else self._legacy_team_inputs(team)
            )
            flags_by_fingerprint = {flag.fingerprint: flag for flag in team.flags}
            for item in inputs:
                identity = (item.definition_key, item.definition_version)
                spec = specs.get(identity)
                if spec is None:
                    raise ValueError(
                        "Unknown signal definition: "
                        f"{item.definition_key}@{item.definition_version}"
                    )
                if item.scope_type != spec.scope_type or item.dimension != spec.dimension:
                    raise ValueError(
                        f"Signal input does not match definition {spec.definition_key}"
                    )
                flag = (
                    flags_by_fingerprint.get(item.flag_fingerprint)
                    if item.flag_fingerprint
                    else None
                )
                if item.condition_met != (flag is not None):
                    raise ValueError(
                        "Dashboard flag output does not match signal rule "
                        f"{spec.definition_key} for {item.scope_type}:{item.scope_id}"
                    )
                definition = definitions[identity]
                existing = session.scalar(
                    select(SignalEvaluation).where(
                        SignalEvaluation.snapshot_id == dashboard.snapshot_id,
                        SignalEvaluation.signal_definition_id == definition.id,
                        SignalEvaluation.scope_type == item.scope_type,
                        SignalEvaluation.scope_id == item.scope_id,
                        SignalEvaluation.subject_id == item.subject_id,
                        SignalEvaluation.dimension == item.dimension,
                    )
                )
                immutable_values = {
                    "condition_met": item.condition_met,
                    "severity": item.severity,
                    "confidence": item.confidence,
                    "current_value": item.current_value,
                    "baseline": item.baseline,
                    "sample_size": item.sample_size,
                    "flag_fingerprint": item.flag_fingerprint,
                }
                if existing is not None:
                    if any(
                        getattr(existing, field) != expected
                        for field, expected in immutable_values.items()
                    ):
                        raise ValueError(
                            "Signal evaluation is immutable and differs for "
                            f"{spec.definition_key}, {team.team_id}, "
                            f"{dashboard.snapshot_id}"
                        )
                    if flag:
                        flag.signal_definition_key = spec.definition_key
                        flag.signal_definition_version = spec.version
                        flag.signal_evaluation_id = existing.id
                        flag.confidence = item.confidence
                    continue
                evaluation = SignalEvaluation(
                    id=str(uuid4()),
                    signal_definition_id=definition.id,
                    snapshot_id=dashboard.snapshot_id,
                    scope_type=item.scope_type,
                    scope_id=item.scope_id,
                    subject_id=item.subject_id,
                    dimension=item.dimension,
                    evaluated_at=dashboard.snapshot_created_at,
                    details={
                        "comparison_basis": spec.comparison_basis,
                        "explanation": flag.explanation if flag else None,
                        "presentation_severity": flag.severity.value if flag else None,
                        **item.details,
                    },
                    **immutable_values,
                )
                session.add(evaluation)
                if flag:
                    flag.signal_definition_key = spec.definition_key
                    flag.signal_definition_version = spec.version
                    flag.signal_evaluation_id = evaluation.id
                    flag.confidence = item.confidence

    @staticmethod
    def _legacy_team_inputs(team) -> list[SignalEvaluationInput]:
        flags_by_area = {flag.area: flag for flag in team.flags}
        values = (
            (
                "team-no-work-in-progress",
                "work_in_flight",
                "ibr_in_progress_count",
                len(team.in_progress),
            ),
            (
                "team-no-ready-for-build",
                "near_term_pipeline",
                "ibr_ready_for_build_count",
                len(team.ready_for_build),
            ),
        )
        return [
            SignalEvaluationInput(
                definition_key=definition_key,
                definition_version="1.0.0",
                scope_type="team",
                scope_id=team.team_id,
                dimension=dimension,
                condition_met=value == 0,
                severity="watch" if value == 0 else None,
                confidence="high",
                current_value={"count": value},
                sample_size=value,
                flag_fingerprint=(
                    flags_by_area[area].fingerprint if value == 0 else None
                ),
                details={"comparison_basis": "absolute_rule"},
            )
            for definition_key, area, dimension, value in values
        ]

    @staticmethod
    def _definition(
        session: Session,
        spec: SignalDefinitionSpec,
        created_at: datetime,
    ) -> SignalDefinition:
        definition = session.scalar(
            select(SignalDefinition).where(
                SignalDefinition.definition_key == spec.definition_key,
                SignalDefinition.version == spec.version,
            )
        )
        if definition is not None:
            if definition.definition_hash != spec.definition_hash:
                raise ValueError(
                    "Signal definition changed without a version bump: "
                    f"{spec.definition_key}@{spec.version}"
                )
            return definition
        definition = SignalDefinition(
            id=str(uuid4()),
            definition_key=spec.definition_key,
            version=spec.version,
            category=spec.category,
            scope_type=spec.scope_type,
            area=spec.area,
            title=spec.title,
            description=spec.description,
            comparison_basis=spec.comparison_basis,
            parameters=spec.parameters,
            severity_policy=spec.severity_policy,
            confidence_policy=spec.confidence_policy,
            definition_hash=spec.definition_hash,
            effective_from=spec.effective_from,
            created_at=created_at,
        )
        session.add(definition)
        session.flush()
        return definition

    def mark_viewed(self, fingerprint: str, *, viewed_at: datetime | None = None) -> None:
        viewed_at = viewed_at or datetime.now(UTC)
        with self.sessions.begin() as session:
            logical = session.get(LogicalFlag, fingerprint)
            if logical is None:
                raise ValueError(f"Flag not found: {fingerprint}")
            state = session.get(FlagUserState, fingerprint)
            if state is None:
                state = FlagUserState(
                    logical_flag_fingerprint=fingerprint,
                    unread_since=None,
                    viewed_at=viewed_at,
                    understood_at=None,
                    snoozed_until=None,
                    updated_at=viewed_at,
                )
                session.add(state)
            else:
                state.unread_since = None
                state.viewed_at = viewed_at
                state.updated_at = viewed_at

    def _record_active(
        self,
        session: Session,
        team_id: str,
        flag: HealthFlag,
        snapshot_id: str,
        evaluated_at: datetime,
    ) -> None:
        logical = session.get(LogicalFlag, flag.fingerprint)
        prior_snapshot_event = session.scalar(
            select(FlagEvent.id).where(
                FlagEvent.logical_flag_fingerprint == flag.fingerprint,
                FlagEvent.snapshot_id == snapshot_id,
            )
        )
        if logical is not None and logical.active and prior_snapshot_event:
            occurrence = self._active_occurrence(session, flag.fingerprint)
            self._evidence_once(session, occurrence, snapshot_id, flag)
            return
        if logical is None:
            logical = LogicalFlag(
                fingerprint=flag.fingerprint,
                team_id=team_id,
                area=flag.area,
                title=flag.title,
                active=True,
                current_severity=flag.severity.value,
                first_seen_at=evaluated_at,
                last_seen_at=evaluated_at,
            )
            session.add(logical)
            session.flush()
            occurrence = self._open_occurrence(
                session, logical, snapshot_id, evaluated_at, flag
            )
            self._reset_unread(session, logical.fingerprint, evaluated_at)
        elif not logical.active:
            logical.active = True
            logical.last_seen_at = evaluated_at
            logical.current_severity = flag.severity.value
            occurrence = self._open_occurrence(
                session, logical, snapshot_id, evaluated_at, flag
            )
            self._reset_unread(session, logical.fingerprint, evaluated_at)
        else:
            occurrence = self._active_occurrence(session, flag.fingerprint)
            prior_severity = logical.current_severity
            logical.last_seen_at = max(_as_utc(logical.last_seen_at), evaluated_at)
            logical.title = flag.title
            occurrence.latest_snapshot_id = snapshot_id
            event_type = (
                "observed"
                if prior_severity == flag.severity.value
                else "escalated"
                if _severity_rank(flag.severity.value) > _severity_rank(prior_severity)
                else "deescalated"
            )
            logical.current_severity = flag.severity.value
            self._event_once(
                session,
                logical,
                occurrence,
                snapshot_id,
                event_type,
                evaluated_at,
                flag,
            )
        self._evidence_once(session, occurrence, snapshot_id, flag)

    def _open_occurrence(
        self,
        session: Session,
        logical: LogicalFlag,
        snapshot_id: str,
        evaluated_at: datetime,
        flag: HealthFlag,
    ) -> FlagOccurrence:
        occurrence = FlagOccurrence(
            id=str(uuid4()),
            logical_flag_fingerprint=logical.fingerprint,
            opened_at=evaluated_at,
            resolved_at=None,
            latest_snapshot_id=snapshot_id,
        )
        session.add(occurrence)
        session.flush()
        self._event_once(
            session,
            logical,
            occurrence,
            snapshot_id,
            "opened",
            evaluated_at,
            flag,
        )
        return occurrence

    def _resolve(
        self,
        session: Session,
        logical: LogicalFlag,
        snapshot_id: str,
        evaluated_at: datetime,
    ) -> None:
        occurrence = self._active_occurrence(session, logical.fingerprint)
        if _as_utc(occurrence.opened_at) > evaluated_at:
            return
        logical.active = False
        logical.last_seen_at = evaluated_at
        occurrence.resolved_at = evaluated_at
        occurrence.latest_snapshot_id = snapshot_id
        self._event_once(
            session,
            logical,
            occurrence,
            snapshot_id,
            "resolved",
            evaluated_at,
            None,
        )

    @staticmethod
    def _active_occurrence(session: Session, fingerprint: str) -> FlagOccurrence:
        occurrence = session.scalar(
            select(FlagOccurrence)
            .where(
                FlagOccurrence.logical_flag_fingerprint == fingerprint,
                FlagOccurrence.resolved_at.is_(None),
            )
            .order_by(FlagOccurrence.opened_at.desc())
            .limit(1)
        )
        if occurrence is None:
            raise RuntimeError(f"Active flag has no open occurrence: {fingerprint}")
        return occurrence

    @staticmethod
    def _event_once(
        session: Session,
        logical: LogicalFlag,
        occurrence: FlagOccurrence,
        snapshot_id: str,
        event_type: str,
        evaluated_at: datetime,
        flag: HealthFlag | None,
    ) -> None:
        exists = session.scalar(
            select(FlagEvent.id).where(
                FlagEvent.logical_flag_fingerprint == logical.fingerprint,
                FlagEvent.snapshot_id == snapshot_id,
                FlagEvent.event_type == event_type,
            )
        )
        if exists:
            return
        session.add(
            FlagEvent(
                id=str(uuid4()),
                logical_flag_fingerprint=logical.fingerprint,
                occurrence_id=occurrence.id,
                snapshot_id=snapshot_id,
                event_type=event_type,
                severity=flag.severity.value if flag else logical.current_severity,
                occurred_at=evaluated_at,
                details={"explanation": flag.explanation if flag else None},
            )
        )

    @staticmethod
    def _evidence_once(
        session: Session,
        occurrence: FlagOccurrence,
        snapshot_id: str,
        flag: HealthFlag,
    ) -> None:
        for evidence in flag.evidence:
            exists = session.scalar(
                select(FlagEvidence.id).where(
                    FlagEvidence.occurrence_id == occurrence.id,
                    FlagEvidence.snapshot_id == snapshot_id,
                    FlagEvidence.url == evidence.url,
                )
            )
            if not exists:
                session.add(
                    FlagEvidence(
                        id=str(uuid4()),
                        occurrence_id=occurrence.id,
                        snapshot_id=snapshot_id,
                        label=evidence.label,
                        url=evidence.url,
                        jira_key=evidence.jira_key,
                        title=evidence.title,
                    )
                )

    @staticmethod
    def _reset_unread(
        session: Session,
        fingerprint: str,
        evaluated_at: datetime,
    ) -> None:
        state = session.get(FlagUserState, fingerprint)
        if state is None:
            session.add(
                FlagUserState(
                    logical_flag_fingerprint=fingerprint,
                    unread_since=evaluated_at,
                    viewed_at=None,
                    understood_at=None,
                    snoozed_until=None,
                    updated_at=evaluated_at,
                )
            )
        else:
            state.unread_since = evaluated_at
            state.viewed_at = None
            state.understood_at = None
            state.snoozed_until = None
            state.updated_at = evaluated_at


def _severity_rank(severity: str) -> int:
    return {"info": 0, "watch": 1, "concern": 2, "critical": 3}[severity]


def _as_utc(value: datetime) -> datetime:
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)
