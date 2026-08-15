"""Materialized Individual views for fast agent-facing reads."""

import os
from pathlib import Path
from tempfile import NamedTemporaryFile

from sqlalchemy.orm import Session, sessionmaker

from engineering_intelligence.config import TeamsConfig
from engineering_intelligence.presentations.people import IndividualDetail
from engineering_intelligence.queries.individual import IndividualQuery


def cache_individual(root: Path, individual: IndividualDetail) -> Path:
    path = _snapshot_dir(root, individual.snapshot_id) / f"{individual.person_id}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    with NamedTemporaryFile(
        "w", encoding="utf-8", dir=path.parent, delete=False
    ) as stream:
        stream.write(individual.model_dump_json(indent=2))
        stream.write("\n")
        temporary = Path(stream.name)
    os.replace(temporary, path)
    return path


def load_cached_individual(
    root: Path,
    snapshot_id: str,
    person_identifier: str,
) -> IndividualDetail | None:
    directory = _snapshot_dir(root, snapshot_id)
    direct = directory / f"{person_identifier}.json"
    candidates = [direct] if direct.is_file() else sorted(directory.glob("*.json"))
    normalized = person_identifier.casefold()
    for path in candidates:
        try:
            individual = IndividualDetail.model_validate_json(path.read_text())
        except (OSError, ValueError):
            continue
        identities = {
            individual.person_id,
            individual.display_name,
            individual.preferred_name or "",
            individual.jira_account_id or "",
            individual.github_login or "",
        }
        if normalized in {identity.casefold() for identity in identities if identity}:
            return individual
    return None


def materialize_individuals(
    root: Path,
    snapshot_id: str,
    sessions: sessionmaker[Session],
    teams_config: TeamsConfig,
) -> int:
    query = IndividualQuery(sessions, teams_config=teams_config)
    person_ids = sorted(
        {member.id for team in teams_config.teams for member in team.members if member.active}
    )
    for person_id in person_ids:
        cache_individual(root, query.get(snapshot_id, person_id))
    return len(person_ids)


def _snapshot_dir(root: Path, snapshot_id: str) -> Path:
    return root / "cache" / "individual" / snapshot_id
