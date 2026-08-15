from pathlib import Path

import pytest
from sqlalchemy import func, select

from engineering_intelligence.config import TeamsConfig
from engineering_intelligence.organization import OrganizationService
from engineering_intelligence.persistence.database import (
    create_sqlite_engine,
    session_factory,
    upgrade_database,
)
from engineering_intelligence.persistence.models import (
    Person,
    Team,
    TeamAlias,
    TeamMembership,
)


def organization_config() -> TeamsConfig:
    return TeamsConfig.model_validate(
        {
            "schema_version": "1",
            "teams": [
                {
                    "id": "a2a",
                    "name": "A2A",
                    "aliases": ["Agent to Agent"],
                    "members": [
                        {
                            "id": "alex",
                            "name": "Alex Kim",
                            "role": "Engineer",
                            "jira_account_id": "jira-alex",
                            "github_login": "alex",
                            "starts_on": "2026-01-01",
                            "is_primary": True,
                        }
                    ],
                },
                {
                    "id": "llm",
                    "name": "LLM",
                    "aliases": [],
                    "members": [
                        {
                            "id": "alex",
                            "name": "Alex Kim",
                            "role": "Engineer",
                            "jira_account_id": "jira-alex",
                            "github_login": "alex",
                            "starts_on": "2026-06-01",
                            "is_primary": False,
                            "ends_on": "2026-09-01",
                        }
                    ],
                },
            ],
        }
    )


def test_organization_apply_is_effective_dated_and_idempotent(tmp_path: Path) -> None:
    database = tmp_path / "engintel.db"
    upgrade_database(database)
    sessions = session_factory(create_sqlite_engine(database))
    service = OrganizationService(sessions)

    first = service.apply(organization_config())
    second = service.apply(organization_config())

    assert first == second
    assert first.model_dump() == {
        "teams": 2,
        "aliases": 1,
        "people": 1,
        "memberships": 2,
    }
    with sessions() as session:
        assert session.scalar(select(func.count()).select_from(Team)) == 2
        assert session.scalar(select(func.count()).select_from(TeamAlias)) == 1
        assert session.scalar(select(func.count()).select_from(Person)) == 1
        assert session.scalar(select(func.count()).select_from(TeamMembership)) == 2
        memberships = session.scalars(
            select(TeamMembership).order_by(TeamMembership.starts_on)
        ).all()
        assert memberships[0].is_primary is True
        assert memberships[1].is_primary is False


def test_conflicting_person_identity_is_rejected(tmp_path: Path) -> None:
    config = organization_config()
    config.teams[1].members[0].name = "Different Name"
    database = tmp_path / "engintel.db"
    upgrade_database(database)
    service = OrganizationService(session_factory(create_sqlite_engine(database)))

    with pytest.raises(ValueError, match="conflicting"):
        service.apply(config)


def test_unknown_primary_status_updates_membership_without_duplicate(
    tmp_path: Path,
) -> None:
    config = organization_config()
    database = tmp_path / "engintel.db"
    upgrade_database(database)
    sessions = session_factory(create_sqlite_engine(database))
    service = OrganizationService(sessions)

    service.apply(config)
    config.teams[0].members[0].is_primary = None
    service.apply(config)

    with sessions() as session:
        memberships = session.scalars(
            select(TeamMembership).where(TeamMembership.team_id == "a2a")
        ).all()
        assert len(memberships) == 1
        assert memberships[0].is_primary is None
