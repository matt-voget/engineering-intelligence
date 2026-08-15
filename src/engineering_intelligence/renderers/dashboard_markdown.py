"""Deterministic Markdown renderer for Dashboard presentation data."""

from engineering_intelligence.presentations.dashboard import Dashboard, WorkItem


def render_dashboard_markdown(dashboard: Dashboard) -> str:
    lines = [
        "# Engineering Team Dashboard",
        "",
        f"Snapshot: `{dashboard.snapshot_name or dashboard.snapshot_id}`",
        f"Snapshot ID: `{dashboard.snapshot_id}`",
        f"Organization config: `{dashboard.organization_config_hash or 'legacy-unpinned'}`",
        f"Source config: `{dashboard.source_config_hash or 'legacy-unpinned'}`",
        f"Created: {dashboard.snapshot_created_at.isoformat()}",
        "",
        "Source freshness:",
        "",
    ]
    lines.extend(
        f"- {source.source} `{source.scope}`: {source.observed_at.isoformat()}"
        for source in dashboard.source_freshness
    )
    for team in dashboard.teams:
        lines.extend(
            [
                "",
                f"## {team.team_name} — {team.health.value.replace('_', ' ').title()}",
                "",
                f"Health coverage: {team.health_coverage.state.value}",
                *(
                    f"- {reason}"
                    for reason in team.health_coverage.reasons
                ),
                "",
            ]
        )
        if team.flags:
            lines.append("Flags:")
            lines.append("")
            for flag in team.flags:
                links = ", ".join(
                    f"[{item.label}]({item.url})" for item in flag.evidence
                )
                lines.append(
                    f"- **{flag.title}** ({flag.raised_at.isoformat()}): "
                    f"{flag.explanation} {links}"
                )
        else:
            lines.extend(["No active health flags.", ""])
        lines.extend(
            [
                "",
                "Most recently completed:",
                "",
                _item(team.most_recently_completed)
                if team.most_recently_completed
                else "- None",
                "",
                "In progress:",
                "",
                *(_item(item) for item in team.in_progress),
            ]
        )
        if not team.in_progress:
            lines.append("- None")
        lines.extend(["", "Ready for build:", ""])
        lines.extend(_item(item) for item in team.ready_for_build)
        if not team.ready_for_build:
            lines.append("- None")
    return "\n".join(lines).rstrip() + "\n"


def _item(item: WorkItem) -> str:
    target_date = item.target_date_value or (
        item.target_date.isoformat() if item.target_date else None
    )
    suffix = f" · Target Date: {target_date}" if target_date else ""
    return f"- [{item.jira_key}]({item.url}) — {item.title}{suffix}"
