"""Deterministic red/amber/green metric assessments."""

import re
from typing import Literal

from pydantic import BaseModel, ConfigDict

from engineering_intelligence.config import RagConfig


class RagAssessment(BaseModel):
    model_config = ConfigDict(extra="forbid")

    level: Literal["green", "amber", "red"]
    symbol: str
    rule_id: str
    rule_label: str
    anchor_id: str
    explanation: str


def assess_rag(
    config: RagConfig,
    *,
    team_id: str,
    section: str,
    metric: str,
    value: float,
    record_key: str,
    classification: str | None = None,
) -> RagAssessment | None:
    matches = [
        rule
        for rule in config.rules
        if rule.enabled
        and rule.section == section
        and rule.metric == metric
        and (not rule.team_ids or team_id in rule.team_ids)
        and (rule.classification is None or rule.classification == classification)
    ]
    if not matches:
        return None
    rule = matches[0]
    if value >= rule.red_at:
        level, symbol = "red", config.red_symbol
    elif value >= rule.amber_at:
        level, symbol = "amber", config.amber_symbol
    else:
        level, symbol = "green", config.green_symbol
    unit = "days" if metric == "cycle_days" else "hours"
    anchor = _anchor(f"rag-{team_id}-{rule.id}-{record_key}")
    return RagAssessment(
        level=level,
        symbol=symbol,
        rule_id=rule.id,
        rule_label=rule.label,
        anchor_id=anchor,
        explanation=(
            f"{rule.label}: {value:.2f} {unit}; amber at {rule.amber_at:g}, "
            f"red at {rule.red_at:g}."
        ),
    )


def _anchor(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", value.casefold()).strip("-")
