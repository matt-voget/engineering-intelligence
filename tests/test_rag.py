import pytest
from pydantic import ValidationError

from engineering_intelligence.config import RagConfig, RagRuleConfig
from engineering_intelligence.presentations.rag import assess_rag


def _rule(**overrides) -> RagRuleConfig:
    values = {
        "id": "high-cycle-time",
        "label": "High cycle time",
        "section": "build_cycle_time",
        "metric": "cycle_days",
        "amber_at": 14,
        "red_at": 30,
    }
    values.update(overrides)
    return RagRuleConfig.model_validate(values)


@pytest.mark.parametrize(
    ("value", "level", "symbol"),
    [(5, "green", "✓"), (14, "amber", "▲"), (30, "red", "!")],
)
def test_assess_rag_uses_configured_thresholds(value, level, symbol) -> None:
    assessment = assess_rag(
        RagConfig(rules=[_rule()]),
        team_id="team-a",
        section="build_cycle_time",
        metric="cycle_days",
        value=value,
        record_key="ABC-123",
        classification="ibr_linked",
    )

    assert assessment is not None
    assert assessment.level == level
    assert assessment.symbol == symbol
    assert assessment.anchor_id == "rag-team-a-high-cycle-time-abc-123"


def test_assess_rag_respects_team_and_classification_scope() -> None:
    config = RagConfig(
        rules=[_rule(team_ids=["team-b"], classification="non_ibr")]
    )

    assert (
        assess_rag(
            config,
            team_id="team-a",
            section="build_cycle_time",
            metric="cycle_days",
            value=40,
            record_key="ABC-123",
            classification="non_ibr",
        )
        is None
    )


def test_rag_rule_rejects_invalid_threshold_order() -> None:
    with pytest.raises(ValidationError, match="red_at must be greater"):
        _rule(amber_at=30, red_at=14)
