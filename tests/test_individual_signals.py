from datetime import UTC, datetime

from engineering_intelligence.config import TeamsConfig
from engineering_intelligence.persistence.models import Person
from engineering_intelligence.presentations.dashboard import EvidenceLink
from engineering_intelligence.presentations.people import (
    JiraWorkRelationship,
    MembershipEvidence,
)
from engineering_intelligence.queries.individual import _context_signals

EVALUATED_AT = datetime(2026, 7, 29, 18, 0, tzinfo=UTC)


def _teams(state: str = "current_observation") -> TeamsConfig:
    return TeamsConfig.model_validate(
        {
            "teams": [
                {
                    "id": "bx",
                    "name": "Builder Experience",
                    "members": [],
                    "roster_source": {
                        "state": state,
                        "observed_on": "2026-07-29",
                    },
                }
            ]
        }
    )


def _membership() -> MembershipEvidence:
    return MembershipEvidence(
        team_id="bx",
        team_name="Builder Experience",
        starts_on=EVALUATED_AT.date(),
        ends_on=None,
        is_primary=True,
        current_at_snapshot=True,
    )


def _work(index: int) -> JiraWorkRelationship:
    feature = f"BX-{index % 3}"
    return JiraWorkRelationship(
        feature_key=feature,
        feature_title=f"Feature {feature}",
        feature_url=f"https://jira.example/{feature}",
        feature_status="In Progress",
        direct_issue_key=f"BX-{100 + index}",
        direct_issue_title=f"Work {index}",
        direct_issue_url=f"https://jira.example/BX-{100 + index}",
        direct_issue_status="In Progress",
        direct_issue_type="Task",
        relationship_type="child_issue_assignee",
        rolled_up_to_feature=True,
        active=True,
        in_flight=True,
        evidence=[
            EvidenceLink(
                label=f"BX-{100 + index}",
                url=f"https://jira.example/BX-{100 + index}",
                jira_key=f"BX-{100 + index}",
            )
        ],
    )


def test_context_signals_require_verified_roster() -> None:
    person = Person(
        id="alex",
        display_name="Alex",
        preferred_name=None,
        role=None,
        manager_person_id=None,
        jira_account_id="jira-alex",
        github_login="alex",
        active=True,
    )

    signals, suppressed = _context_signals(
        [_work(index) for index in range(5)],
        [],
        [_membership()],
        person,
        EVALUATED_AT,
        _teams("unverified"),
    )

    assert signals == []
    assert len(suppressed) == 3
    assert all("verified" in item.reason for item in suppressed)


def test_context_signals_are_neutral_and_thresholded() -> None:
    person = Person(
        id="alex",
        display_name="Alex",
        preferred_name=None,
        role=None,
        manager_person_id=None,
        jira_account_id="jira-alex",
        github_login="alex",
        active=True,
    )

    signals, suppressed = _context_signals(
        [_work(index) for index in range(5)],
        [],
        [_membership()],
        person,
        EVALUATED_AT,
        _teams(),
    )

    assert suppressed == []
    assert {signal.rule_key for signal in signals} == {
        "individual-broad-concurrent-context",
        "individual-cross-source-visibility-gap",
    }
    assert all(signal.signal_type == "investigation_prompt" for signal in signals)
    assert all("not" in signal.explanation.casefold() for signal in signals)


def test_cross_source_rules_suppress_partial_identity() -> None:
    person = Person(
        id="alex",
        display_name="Alex",
        preferred_name=None,
        role=None,
        manager_person_id=None,
        jira_account_id=None,
        github_login="alex",
        active=True,
    )

    signals, suppressed = _context_signals(
        [],
        [],
        [_membership()],
        person,
        EVALUATED_AT,
        _teams(),
    )

    assert signals == []
    assert {item.rule_key for item in suppressed} == {
        "individual-broad-concurrent-context",
        "individual-cross-source-visibility-gap",
        "individual-linked-support-context",
    }
