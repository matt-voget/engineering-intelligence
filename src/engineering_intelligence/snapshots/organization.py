"""Canonical, snapshot-pinned organization configuration."""

import hashlib
import json

from engineering_intelligence.config import (
    IBR_BOARD_ROLE,
    GitHubConfig,
    SourceConfig,
    TeamsConfig,
)
from engineering_intelligence.persistence.models import Snapshot


def _canonical_config(config: SourceConfig | TeamsConfig) -> tuple[dict, str]:
    payload = config.model_dump(mode="json")
    encoded = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    return payload, hashlib.sha256(encoded).hexdigest()


def canonical_organization_config(config: TeamsConfig) -> tuple[dict, str]:
    return _canonical_config(config)


def canonical_source_config(config: SourceConfig) -> tuple[dict, str]:
    return _canonical_config(config)


def organization_config_for_snapshot(
    snapshot: Snapshot,
    fallback: TeamsConfig,
) -> TeamsConfig:
    if snapshot.organization_config is None:
        return fallback
    return TeamsConfig.model_validate(snapshot.organization_config)


def source_config_for_snapshot(
    snapshot: Snapshot,
    fallback: SourceConfig,
) -> SourceConfig:
    if snapshot.source_config is None:
        return fallback
    return SourceConfig.model_validate(snapshot.source_config)


def github_config_for_snapshot(
    snapshot: Snapshot,
    fallback: GitHubConfig | None,
) -> GitHubConfig | None:
    if snapshot.source_config is None:
        return fallback
    return SourceConfig.model_validate(snapshot.source_config).github


def ibr_board_id_for_snapshot(snapshot: Snapshot) -> int | None:
    """Resolve the report's IBR board from pinned source configuration.

    The IBR board defines which tickets are in scope versus out of scope from an
    IBR perspective. Legacy pinned configurations using the ``portfolio`` role
    are normalized to ``ibr`` by the configuration model. Returns ``None`` when
    the snapshot predates pinned source configuration, so callers fall through
    to their explicit missing-IBR-board handling instead of guessing a board.
    """
    if snapshot.source_config is None:
        return None
    boards = SourceConfig.model_validate(snapshot.source_config).jira.boards
    ibr = next((board for board in boards if board.role == IBR_BOARD_ROLE), None)
    return (ibr or boards[0]).id
