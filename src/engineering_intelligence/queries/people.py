"""Build the People directory from persisted identities and Individual contracts."""

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from engineering_intelligence.config import TeamsConfig
from engineering_intelligence.persistence.models import Person, Snapshot, SnapshotSourceState
from engineering_intelligence.presentations.dashboard import SourceFreshness
from engineering_intelligence.presentations.people import (
    PeopleDirectory,
    PersonDirectoryRow,
)
from engineering_intelligence.queries.individual import IndividualQuery, _as_utc


class PeopleQuery:
    def __init__(
        self,
        sessions: sessionmaker[Session],
        *,
        max_people: int = 250,
        max_feature_nodes: int = 250,
    ) -> None:
        self.sessions = sessions
        self.max_people = max_people
        self.individual_query = IndividualQuery(
            sessions,
            max_feature_nodes=max_feature_nodes,
        )

    def get(self, snapshot_identifier: str) -> PeopleDirectory:
        with self.sessions() as session:
            snapshot = _snapshot(session, snapshot_identifier)
            saved_config = (
                TeamsConfig.model_validate(snapshot.organization_config)
                if snapshot.organization_config is not None
                else None
            )
            states = session.scalars(
                select(SnapshotSourceState)
                .where(SnapshotSourceState.snapshot_id == snapshot.id)
                .order_by(SnapshotSourceState.source, SnapshotSourceState.scope)
            ).all()
            people_query = select(Person)
            if saved_config is None:
                people_query = people_query.where(Person.active.is_(True))
            else:
                accountable_ids = {
                    member.id
                    for team in saved_config.teams
                    for member in team.members
                    if member.active
                    and member.starts_on <= snapshot.created_at.date()
                    and (
                        member.ends_on is None
                        or member.ends_on >= snapshot.created_at.date()
                    )
                }
                people_query = people_query.where(Person.id.in_(accountable_ids))
            people = session.scalars(
                people_query
                .order_by(Person.display_name, Person.id)
                .limit(self.max_people + 1)
            ).all()
        if len(people) > self.max_people:
            raise ValueError(f"People directory exceeds the {self.max_people}-person limit")
        rows = []
        individual_query = (
            IndividualQuery(
                self.sessions,
                max_feature_nodes=self.individual_query.feature_query.max_nodes,
                teams_config=saved_config,
            )
            if saved_config is not None
            else self.individual_query
        )
        for person in people:
            individual = individual_query.get(snapshot_identifier, person.id)
            active_work = [item for item in individual.jira_work if item.active]
            rows.append(
                PersonDirectoryRow(
                    person_id=person.id,
                    display_name=person.display_name,
                    preferred_name=person.preferred_name,
                    role=person.role,
                    current_teams=[
                        membership.team_name
                        for membership in individual.memberships
                        if membership.current_at_snapshot
                    ],
                    current_features=sorted(
                        {
                            item.feature_key
                            for item in active_work
                            if item.feature_key is not None
                        }
                    ),
                    active_context=sorted(
                        {
                            f"{item.direct_issue_key}: {item.direct_issue_title}"
                            for item in active_work
                        }
                    ),
                    identity_mapping_state=individual.identity_mapping_state,
                    jira_account_id=person.jira_account_id,
                    github_login=person.github_login,
                )
            )
        notes = []
        if not rows:
            notes.append(
                "No people are configured; apply explicit roster and identity configuration."
            )
        if any(row.identity_mapping_state != "complete" for row in rows):
            notes.append("Some people have partial or missing Jira/GitHub identity mappings.")
        return PeopleDirectory(
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
                for state in states
            ],
            people=rows,
            data_quality_notes=notes,
        )


def _snapshot(session: Session, identifier: str) -> Snapshot:
    snapshot = session.get(Snapshot, identifier)
    if snapshot is None:
        snapshot = session.scalar(select(Snapshot).where(Snapshot.name == identifier))
    if snapshot is None:
        raise ValueError(f"Snapshot not found: {identifier}")
    return snapshot
