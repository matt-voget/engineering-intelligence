"""Deterministic Markdown renderers for People and Individual context."""

from engineering_intelligence.presentations.people import (
    IndividualDetail,
    PeopleDirectory,
)


def render_people_markdown(directory: PeopleDirectory) -> str:
    lines = [
        "# People directory",
        "",
        f"- Snapshot: `{directory.snapshot_name or directory.snapshot_id}`",
        f"- Snapshot ID: `{directory.snapshot_id}`",
        f"- Organization config: `{directory.organization_config_hash or 'legacy-unpinned'}`",
        f"- Source config: `{directory.source_config_hash or 'legacy-unpinned'}`",
        f"- People: {len(directory.people)}",
        "",
        "| Person | Role | Current teams | Current Features | Active context | Identity |",
        "|---|---|---|---|---|---|",
    ]
    if directory.people:
        lines.extend(
            f"| {person.display_name} | {person.role or 'Not configured'} | "
            f"{', '.join(person.current_teams) or 'None'} | "
            f"{', '.join(person.current_features) or 'None'} | "
            f"{'; '.join(person.active_context) or 'None'} | "
            f"{person.identity_mapping_state} |"
            for person in directory.people
        )
    else:
        lines.append("| No configured people |  |  |  |  |  |")
    lines.extend(
        [
            "",
            "## Data quality",
            "",
            *(f"- {note}" for note in directory.data_quality_notes),
        ]
    )
    return "\n".join(lines).rstrip() + "\n"


def render_individual_markdown(individual: IndividualDetail) -> str:
    lines = [
        f"# Individual work context: {individual.display_name}",
        "",
        f"- Snapshot: `{individual.snapshot_name or individual.snapshot_id}`",
        f"- Snapshot ID: `{individual.snapshot_id}`",
        f"- Organization config: `{individual.organization_config_hash or 'legacy-unpinned'}`",
        f"- Source config: `{individual.source_config_hash or 'legacy-unpinned'}`",
        f"- Role: {individual.role or 'Not configured'}",
        f"- Identity mapping: {individual.identity_mapping_state}",
        f"- Jira account: {individual.jira_account_id or 'Not mapped'}",
        f"- GitHub login: {individual.github_login or 'Not mapped'}",
        "",
        "## Team memberships",
        "",
    ]
    if individual.memberships:
        lines.extend(
            f"- {membership.team_name} · "
            f"{_membership_kind(membership.is_primary)} · "
            f"{membership.starts_on.isoformat()} to "
            f"{membership.ends_on.isoformat() if membership.ends_on else 'present'} · "
            f"{'current at snapshot' if membership.current_at_snapshot else 'historical'}"
            for membership in individual.memberships
        )
    else:
        lines.append("- None configured")
    lines.extend(["", "## Current work", ""])
    if individual.current_work:
        lines.extend(
            [
                "| Issue | Status | Target date | Boards | IBR Feature |",
                "|---|---|---|---|---|",
                *(
                    f"| [{item.jira_key}]({item.url}) — {item.title} | "
                    f"{item.status} | {item.target_date_value or 'Not set'} | "
                    f"{', '.join(f'[{board.board_name}]({board.board_url})' for board in item.boards) or 'Not on a collected board'} | "
                    + (
                        f"[{item.feature_key}]({item.feature_url})"
                        if item.feature_key and item.feature_url
                        else "No IBR mapping"
                    )
                    + " |"
                    for item in individual.current_work
                ),
            ]
        )
    else:
        lines.append("- No active Jira assignments observed")
    lines.extend(["", "## Jira work evidence", ""])
    if individual.jira_work:
        for item in individual.jira_work:
            feature_context = (
                f"Feature [{item.feature_key}]({item.feature_url})"
                if item.feature_key and item.feature_url
                else "outside the collected IBR Feature hierarchy"
            )
            lines.append(
                f"- [{item.direct_issue_key}]({item.direct_issue_url}) — "
                f"{item.direct_issue_title} · {item.relationship_type} · "
                f"{item.direct_issue_status} · target date "
                f"{item.target_date_value or 'not set'} · {feature_context}"
                + (" · rolled up" if item.rolled_up_to_feature else "")
            )
    else:
        lines.append("- No source-linked Jira assignments observed")
    lines.extend(["", "## GitHub contributions", ""])
    if individual.github_contributions:
        for item in individual.github_contributions:
            jira_context = (
                f"Jira [{item.direct_jira_key}]({item.direct_jira_url})"
                if item.direct_jira_key and item.direct_jira_url
                else "no explicit Jira link"
            )
            lines.append(
                f"- [{item.record_type}: {item.title}]({item.url}) · "
                f"{item.repository} · {item.state} · {jira_context}"
                + (" · rolled up" if item.rolled_up_to_feature else "")
            )
    else:
        lines.append("- No source-linked GitHub contributions observed")
    lines.extend(["", "## Blockers and dependencies", ""])
    if individual.blockers_and_dependencies:
        lines.extend(
            f"- {item.source_issue_key} {item.relationship} "
            + (
                f"[{item.target_issue_key}]({item.target_url})"
                if item.target_issue_key and item.target_url
                else "uncollected issue"
            )
            for item in individual.blockers_and_dependencies
        )
    else:
        lines.append("- None observed")
    lines.extend(["", "## Context signals", ""])
    if individual.signals:
        for signal in individual.signals:
            lines.append(
                f"- **{signal.signal_type}: {signal.title}** · {signal.confidence}"
            )
            lines.append(f"  - {signal.explanation}")
            lines.extend(
                f"  - [{evidence.label}]({evidence.url})"
                for evidence in signal.evidence
            )
    else:
        lines.append("- No deterministic signals raised")
    lines.extend(["", "## Suppressed signal rules", ""])
    if individual.suppressed_signal_rules:
        lines.extend(
            f"- **{rule.title}** (`{rule.rule_key}`): {rule.reason}"
            for rule in individual.suppressed_signal_rules
        )
    else:
        lines.append("- None")
    lines.extend(
        [
            "",
            "## Data quality and interpretation limits",
            "",
            *(f"- {note}" for note in individual.data_quality_notes),
        ]
    )
    return "\n".join(lines).rstrip() + "\n"


def _membership_kind(is_primary: bool | None) -> str:
    if is_primary is True:
        return "primary"
    if is_primary is False:
        return "secondary"
    return "primary status unknown"
