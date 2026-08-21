"""Versioned deterministic signal definitions."""

import json
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from hashlib import sha256
from typing import Any


@dataclass(frozen=True)
class SignalDefinitionSpec:
    definition_key: str
    version: str
    category: str
    scope_type: str
    area: str
    dimension: str
    title: str
    description: str
    comparison_basis: str
    parameters: dict[str, Any]
    severity_policy: dict[str, Any]
    confidence_policy: dict[str, Any]
    effective_from: datetime

    @property
    def definition_hash(self) -> str:
        payload = asdict(self)
        payload["effective_from"] = self.effective_from.isoformat()
        encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
        return sha256(encoded).hexdigest()


DASHBOARD_SIGNAL_DEFINITIONS = (
    SignalDefinitionSpec(
        definition_key="team-no-work-in-progress",
        version="1.0.0",
        category="flow_and_delivery",
        scope_type="team",
        area="work_in_flight",
        dimension="ibr_in_progress_count",
        title="No IBR items in progress",
        description="Detect a team with no IBR items in the In Progress status.",
        comparison_basis="absolute_rule",
        parameters={"measure": "ibr_in_progress_count", "operator": "equals", "value": 0},
        severity_policy={"when_triggered": "watch"},
        confidence_policy={
            "level": "high",
            "requires": ["complete_ibr_board_snapshot"],
        },
        effective_from=datetime(2026, 7, 29, tzinfo=UTC),
    ),
    SignalDefinitionSpec(
        definition_key="team-no-ready-for-build",
        version="1.0.0",
        category="flow_and_delivery",
        scope_type="team",
        area="near_term_pipeline",
        dimension="ibr_ready_for_build_count",
        title="No IBR items ready for build",
        description="Detect a team with no IBR items in the Ready for Build status.",
        comparison_basis="absolute_rule",
        parameters={
            "measure": "ibr_ready_for_build_count",
            "operator": "equals",
            "value": 0,
        },
        severity_policy={"when_triggered": "watch"},
        confidence_policy={
            "level": "high",
            "requires": ["complete_ibr_board_snapshot"],
        },
        effective_from=datetime(2026, 7, 29, tzinfo=UTC),
    ),
    SignalDefinitionSpec(
        definition_key="feature-stalled-active-work",
        version="1.0.0",
        category="flow_and_delivery",
        scope_type="feature",
        area="stalled_work",
        dimension="jira_inactive_days",
        title="Active Feature has no recent Jira activity",
        description=(
            "Detect active IBR work whose Jira record has not changed for 14 days."
        ),
        comparison_basis="absolute_rule",
        parameters={
            "measure": "jira_inactive_days",
            "operator": "greater_than_or_equal",
            "value": 14,
            "activity_source": "jira_issue_updated",
        },
        severity_policy={"when_triggered": "watch"},
        confidence_policy={
            "level": "medium",
            "limitations": ["GitHub activity is not included"],
        },
        effective_from=datetime(2026, 7, 29, tzinfo=UTC),
    ),
    SignalDefinitionSpec(
        definition_key="feature-target-date-overdue",
        version="1.0.0",
        category="flow_and_delivery",
        scope_type="feature",
        area="target_risk",
        dimension="target_days_overdue",
        title="Feature is past its target date",
        description="Detect unresolved IBR work whose configured target date has passed.",
        comparison_basis="absolute_rule",
        parameters={
            "measure": "target_days_overdue",
            "operator": "greater_than",
            "value": 0,
        },
        severity_policy={"when_triggered": "watch"},
        confidence_policy={"level": "high", "requires": ["target_date"]},
        effective_from=datetime(2026, 7, 29, tzinfo=UTC),
    ),
    SignalDefinitionSpec(
        definition_key="feature-active-ownership-gap",
        version="1.0.0",
        category="data_quality_and_visibility",
        scope_type="feature",
        area="ownership_gap",
        dimension="has_jira_assignee",
        title="Active Feature has no Jira assignee",
        description="Detect active IBR work without a current Jira assignee.",
        comparison_basis="absolute_rule",
        parameters={
            "measure": "has_jira_assignee",
            "operator": "equals",
            "value": False,
        },
        severity_policy={"when_triggered": "watch"},
        confidence_policy={"level": "high", "requires": ["jira_assignee_field"]},
        effective_from=datetime(2026, 7, 29, tzinfo=UTC),
    ),
    SignalDefinitionSpec(
        definition_key="feature-workflow-regression",
        version="1.1.0",
        category="flow_and_delivery",
        scope_type="feature",
        area="workflow_regression",
        dimension="backward_transition_count_30d",
        title="Feature moved backward in the workflow",
        description=(
            "Detect backward movement between known Ideate and Build lifecycle stages "
            "during the trailing 30 days."
        ),
        comparison_basis="absolute_rule",
        parameters={
            "measure": "backward_transition_count_30d",
            "operator": "greater_than",
            "value": 0,
            "window_days": 30,
        },
        severity_policy={"when_triggered": "watch"},
        confidence_policy={
            "level": "high",
            "requires": ["jira_status_history"],
            "applicability": "suppressed_when_status_history_is_missing",
        },
        effective_from=datetime(2026, 7, 29, tzinfo=UTC),
    ),
    SignalDefinitionSpec(
        definition_key="feature-repeated-status-cycling",
        version="1.1.0",
        category="flow_and_delivery",
        scope_type="feature",
        area="workflow_cycling",
        dimension="maximum_status_entries_90d",
        title="Feature repeatedly cycles through a status",
        description=(
            "Detect entry to the same known lifecycle status at least three times "
            "during the trailing 90 days."
        ),
        comparison_basis="absolute_rule",
        parameters={
            "measure": "maximum_status_entries_90d",
            "operator": "greater_than_or_equal",
            "value": 3,
            "window_days": 90,
        },
        severity_policy={"when_triggered": "watch"},
        confidence_policy={
            "level": "high",
            "requires": ["jira_status_history"],
            "applicability": "suppressed_when_status_history_is_missing",
        },
        effective_from=datetime(2026, 7, 29, tzinfo=UTC),
    ),
    SignalDefinitionSpec(
        definition_key="feature-stage-aging-vs-team",
        version="1.1.0",
        category="flow_and_delivery",
        scope_type="feature",
        area="stage_aging",
        dimension="current_stage_age_days",
        title="Feature stage age exceeds its team baseline",
        description=(
            "Detect a current stage age of at least 14 days and at least 1.5 times "
            "the team's trailing-90-day median for that stage."
        ),
        comparison_basis="team_historical_baseline",
        parameters={
            "measure": "current_stage_age_days",
            "minimum_days": 14,
            "baseline_multiplier": 1.5,
            "baseline_window_days": 90,
            "minimum_sample_size": 5,
        },
        severity_policy={"when_triggered": "watch"},
        confidence_policy={
            "level": "medium",
            "requires": ["jira_status_history", "minimum_baseline_sample"],
            "applicability": "suppressed_when_status_history_is_missing",
        },
        effective_from=datetime(2026, 7, 29, tzinfo=UTC),
    ),
    SignalDefinitionSpec(
        definition_key="feature-missing-transition-history",
        version="1.0.0",
        category="data_quality_and_visibility",
        scope_type="feature",
        area="transition_evidence",
        dimension="status_transition_count",
        title="Feature has no Jira status-transition history",
        description=(
            "Detect active IBR Features beyond the initial lifecycle stages that have "
            "no Jira status-transition evidence."
        ),
        comparison_basis="absolute_rule",
        parameters={
            "measure": "status_transition_count",
            "operator": "equals",
            "value": 0,
            "applicable_status_minimum_stage": "product review",
            "excludes_status": "done",
        },
        severity_policy={"when_triggered": "watch"},
        confidence_policy={
            "level": "medium",
            "limitations": ["A Jira issue may have been created directly in its current status"],
        },
        effective_from=datetime(2026, 7, 29, tzinfo=UTC),
    ),
    SignalDefinitionSpec(
        definition_key="feature-missing-timing-evidence",
        version="1.0.0",
        category="data_quality_and_visibility",
        scope_type="feature",
        area="required_evidence",
        dimension="missing_timing_field_count",
        title="Active Feature is missing required Jira timing evidence",
        description=(
            "Detect active IBR Features missing Jira created or updated timestamps "
            "required for timing and inactivity analysis."
        ),
        comparison_basis="absolute_rule",
        parameters={
            "measure": "missing_timing_field_count",
            "operator": "greater_than",
            "value": 0,
            "required_fields": ["source_created_at", "source_updated_at"],
        },
        severity_policy={"when_triggered": "watch"},
        confidence_policy={"level": "high", "requires": ["jira_issue_timestamps"]},
        effective_from=datetime(2026, 7, 29, tzinfo=UTC),
    ),
    SignalDefinitionSpec(
        definition_key="pull-request-aging-open",
        version="1.2.0",
        category="flow_and_delivery",
        scope_type="pull_request",
        area="github_pull_request_aging",
        dimension="open_age_days",
        title="Team-authored pull request is aging",
        description=(
            "Detect an open pull request authored by a configured team member that "
            "has been open for at least 14 days."
        ),
        comparison_basis="absolute_rule",
        parameters={
            "measure": "open_age_days",
            "operator": "greater_than_or_equal",
            "value": 14,
            "team_attribution": "configured_author_identity",
        },
        severity_policy={"when_triggered": "watch"},
        confidence_policy={
            "level": "high",
            "requires": ["github_pull_request_history", "configured_author_identity"],
        },
        effective_from=datetime(2026, 8, 19, tzinfo=UTC),
    ),
    SignalDefinitionSpec(
        definition_key="pull-request-missing-jira-attribution",
        version="1.2.0",
        category="data_quality_and_visibility",
        scope_type="pull_request",
        area="github_attribution",
        dimension="open_days_without_direct_jira_relationship",
        title="Open pull request has no Jira attribution",
        description=(
            "Detect an open pull request authored by a configured team member that is "
            "at least seven days old and has no direct Jira relationship."
        ),
        comparison_basis="absolute_rule",
        parameters={
            "measure": "open_days_without_direct_jira_relationship",
            "operator": "greater_than_or_equal",
            "value": 7,
            "team_attribution": "configured_author_identity",
        },
        severity_policy={"when_triggered": "watch"},
        confidence_policy={
            "level": "medium",
            "limitations": [
                "Some valid engineering work does not require a Jira relationship"
            ],
        },
        effective_from=datetime(2026, 8, 19, tzinfo=UTC),
    ),
    SignalDefinitionSpec(
        definition_key="team-review-load-concentration",
        version="1.1.0",
        category="collaboration",
        scope_type="team",
        area="github_review_concentration",
        dimension="top_reviewer_share_30d",
        title="Review load is concentrated",
        description=(
            "Detect when one reviewer submitted at least 60 percent of at least ten "
            "human reviews submitted by configured team members across all configured "
            "repositories in the trailing 30 days."
        ),
        comparison_basis="absolute_rule",
        parameters={
            "measure": "top_reviewer_share_30d",
            "operator": "greater_than_or_equal",
            "value": 0.6,
            "window_days": 30,
            "minimum_sample_size": 10,
            "exclude_authors_ending_with": "[bot]",
            "team_attribution": "configured_reviewer_identity",
        },
        severity_policy={"when_triggered": "watch"},
        confidence_policy={
            "level": "medium",
            "requires": ["github_reviews", "configured_reviewer_identity"],
            "limitations": ["Review comments outside GitHub reviews are not included"],
        },
        effective_from=datetime(2026, 8, 19, tzinfo=UTC),
    ),
)
