"""Apply explicit people and effective-dated team configuration idempotently."""

from datetime import date
from uuid import NAMESPACE_URL, uuid5

from pydantic import BaseModel, ConfigDict
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from engineering_intelligence.config import TeamsConfig
from engineering_intelligence.persistence.models import (
    Person,
    Team,
    TeamAlias,
    TeamMembership,
)

ALIAS_START = date(1970, 1, 1)


class OrganizationLoadResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    teams: int
    aliases: int
    people: int
    memberships: int


class OrganizationService:
    def __init__(self, sessions: sessionmaker[Session]) -> None:
        self.sessions = sessions

    def apply(self, config: TeamsConfig) -> OrganizationLoadResult:
        self._validate_people(config)
        people_seen: set[str] = set()
        alias_count = 0
        membership_count = 0
        with self.sessions.begin() as session:
            for team_config in config.teams:
                team = session.get(Team, team_config.id)
                if team is None:
                    team = Team(id=team_config.id, name=team_config.name, active=True)
                    session.add(team)
                    session.flush()
                else:
                    team.name = team_config.name
                    team.active = True
                for alias in team_config.aliases:
                    self._upsert_alias(session, team_config.id, alias)
                    alias_count += 1
                for member in team_config.members:
                    self._upsert_person(session, member)
                    people_seen.add(member.id)
                    self._upsert_membership(session, team_config.id, member)
                    membership_count += 1
        return OrganizationLoadResult(
            teams=len(config.teams),
            aliases=alias_count,
            people=len(people_seen),
            memberships=membership_count,
        )

    @staticmethod
    def _upsert_alias(session: Session, team_id: str, alias: str) -> None:
        current = session.scalar(
            select(TeamAlias).where(
                TeamAlias.team_id == team_id,
                TeamAlias.alias == alias,
                TeamAlias.starts_on == ALIAS_START,
            )
        )
        if current is None:
            session.add(
                TeamAlias(
                    id=_stable_id("alias", team_id, alias, ALIAS_START.isoformat()),
                    team_id=team_id,
                    alias=alias,
                    starts_on=ALIAS_START,
                    ends_on=None,
                )
            )

    @staticmethod
    def _upsert_person(session: Session, member: object) -> None:
        person = session.get(Person, member.id)
        values = {
            "display_name": member.name,
            "preferred_name": member.preferred_name,
            "role": member.role,
            "manager_person_id": member.manager_person_id,
            "jira_account_id": member.jira_account_id,
            "github_login": member.github_login,
            "active": member.active,
        }
        if person is None:
            session.add(Person(id=member.id, **values))
        else:
            for key, value in values.items():
                setattr(person, key, value)

    @staticmethod
    def _upsert_membership(session: Session, team_id: str, member: object) -> None:
        current = session.scalar(
            select(TeamMembership).where(
                TeamMembership.team_id == team_id,
                TeamMembership.person_id == member.id,
                TeamMembership.starts_on == member.starts_on,
            )
        )
        if current is None:
            session.add(
                TeamMembership(
                    id=_stable_id(
                        "membership",
                        team_id,
                        member.id,
                        member.starts_on.isoformat(),
                    ),
                    team_id=team_id,
                    person_id=member.id,
                    starts_on=member.starts_on,
                    ends_on=member.ends_on,
                    is_primary=member.is_primary,
                )
            )
        else:
            current.ends_on = member.ends_on
            current.is_primary = member.is_primary

    @staticmethod
    def _validate_people(config: TeamsConfig) -> None:
        definitions: dict[str, tuple[str, str | None, str | None]] = {}
        for team in config.teams:
            for member in team.members:
                identity = (member.name, member.jira_account_id, member.github_login)
                prior = definitions.setdefault(member.id, identity)
                if prior != identity:
                    raise ValueError(
                        f"Person {member.id} has conflicting identity fields across teams"
                    )
                if member.ends_on and member.ends_on < member.starts_on:
                    raise ValueError(
                        f"Membership for {member.id} ends before it starts"
                    )


def _stable_id(*parts: str) -> str:
    return str(uuid5(NAMESPACE_URL, ":".join(parts)))
