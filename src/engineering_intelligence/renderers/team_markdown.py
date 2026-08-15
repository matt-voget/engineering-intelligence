"""Deterministic Markdown renderer for Team detail."""

from engineering_intelligence.presentations.team import TeamDetail


def render_team_markdown(team: TeamDetail) -> str:
    lines = [
        f"# Team: {team.team_name}",
        "",
        f"- Health: {team.health.value}",
        f"- Health coverage: {team.health_coverage.state.value}",
        *(
            f"  - {reason}"
            for reason in team.health_coverage.reasons
        ),
        f"- Active flags: {len(team.flags)}",
        f"- Snapshot: `{team.snapshot_name or team.snapshot_id}`",
        f"- Snapshot ID: `{team.snapshot_id}`",
        f"- Organization config: `{team.organization_config_hash or 'legacy-unpinned'}`",
        f"- Source config: `{team.source_config_hash or 'legacy-unpinned'}`",
        f"- Created: {team.snapshot_created_at.isoformat()}",
        "",
        "Source freshness:",
        "",
        *(
            f"- {source.source} `{source.scope}`: {source.observed_at.isoformat()}"
            for source in team.source_freshness
        ),
        "",
        "## Active health flags",
        "",
    ]
    if team.flags:
        for flag in team.flags:
            lines.append(
                f"- **{flag.severity.value} · {flag.title}**"
                f"{' · unread' if flag.unread else ' · viewed'}"
            )
            lines.append(f"  - {flag.explanation}")
            lines.extend(f"  - [{item.label}]({item.url})" for item in flag.evidence)
    else:
        lines.append("- None")
    lines.extend(["", "## IBR workflow", ""])
    for column in team.workflow:
        marker = " ⚑" if column.attention_signal else ""
        lines.append(f"### {column.name} ({column.count}){marker}")
        lines.append("")
        if column.attention_explanation:
            lines.append(f"_Attention: {column.attention_explanation}_")
            lines.append("")
        if column.items:
            lines.extend(
                f"- [{item.jira_key}]({item.url}) — {item.title}"
                + (
                    f" · Target Date: "
                    f"{item.target_date_value or item.target_date.isoformat()}"
                    if item.target_date_value or item.target_date
                    else ""
                )
                for item in column.items
            )
        else:
            lines.append("- No items")
        lines.append("")
    lines.extend(["## People", ""])
    if team.roster:
        lines.extend(
            f"- {person.display_name} · {person.role or 'Role not configured'} · "
            f"identity {person.identity_mapping_state}"
            for person in team.roster
        )
    else:
        lines.append("- No roster members configured")
    lines.extend(["", "## GitHub delivery", "", team.github_availability.message, ""])
    if team.github_delivery:
        lines.extend(
            f"- [{record.record_type}: {record.title}]({record.url}) · "
            f"{record.state} · {record.actor_login or 'unknown actor'} · "
            f"[{record.direct_jira_key}]({record.direct_jira_url})"
            + (" · rolled up" if record.rolled_up_to_feature else "")
            for record in team.github_delivery
        )
    else:
        lines.append("- No linked records")
    lines.extend(["", "## Blocked work", ""])
    if team.blocked_work:
        lines.extend(
            f"- [{link.source_issue_key}] — {link.relationship} "
            + (
                f"[{link.target_issue_key}]({link.target_url})"
                if link.target_issue_key and link.target_url
                else "uncollected linked issue"
            )
            for link in team.blocked_work
        )
    else:
        lines.append("- None observed")
    lines.extend(
        [
            "",
            "## Metrics",
            "",
            team.metrics_availability.message,
            "",
            "## Data quality",
            "",
            *(f"- {note}" for note in team.data_quality_notes),
        ]
    )
    return "\n".join(lines).rstrip() + "\n"
