"""Deterministic Markdown renderer for the compact Team brief."""

from engineering_intelligence.presentations.dashboard import WorkItem
from engineering_intelligence.presentations.team_brief import TeamBrief


def render_team_brief_markdown(brief: TeamBrief) -> str:
    lines = [
        f"# Team brief: {brief.team_name}",
        "",
        f"- Health: {brief.health.value}",
        f"- Coverage: {brief.health_coverage.state.value}",
        f"- Active flags: {len(brief.flags)}",
        f"- Snapshot: `{brief.snapshot_name or brief.snapshot_id}`",
        f"- Snapshot ID: `{brief.snapshot_id}`",
        f"- Created: {brief.snapshot_created_at.isoformat()}",
        "",
        "## Recently completed",
        "",
        *(_work_items([brief.most_recently_completed]) if brief.most_recently_completed else ["- None"]),
        "",
        "## In progress",
        "",
        *_work_items(brief.in_progress),
        "",
        "## Ready for build",
        "",
        *_work_items(brief.ready_for_build),
        "",
        "## Concerns to probe",
        "",
    ]
    if brief.flags:
        for flag in brief.flags:
            lines.append(
                f"- **{flag.severity.value} · {flag.title}** · "
                f"{'unread' if flag.unread else 'viewed'} · "
                f"{flag.raised_at.isoformat()}"
            )
            lines.append(f"  - {flag.explanation}")
            lines.extend(f"  - [{item.label}]({item.url})" for item in flag.evidence)
    else:
        lines.append("- None")
    lines.extend(
        [
            "",
            "## Evidence coverage",
            "",
            (
                f"- Roster: {brief.roster.member_count} members; "
                f"{brief.roster.incomplete_identity_count} incomplete identities"
            ),
            (
                f"- GitHub: {brief.github.total_records} linked records across "
                f"{len(brief.github.repositories_with_linked_records)} repositories"
            ),
            f"- Metrics: {brief.metrics_availability.message}",
            "",
            "## Blocked work",
            "",
        ]
    )
    if brief.blocked_work:
        lines.extend(
            f"- [{link.source_issue_key}] — {link.relationship} "
            + (
                f"[{link.target_issue_key}]({link.target_url})"
                if link.target_issue_key and link.target_url
                else "uncollected linked issue"
            )
            for link in brief.blocked_work
        )
    else:
        lines.append("- None observed")
    lines.extend(
        [
            "",
            "## Data quality",
            "",
            *(f"- {note}" for note in brief.data_quality_notes),
        ]
    )
    return "\n".join(lines).rstrip() + "\n"


def _work_items(items: list[WorkItem]) -> list[str]:
    if not items:
        return ["- No items"]
    return [
        f"- [{item.jira_key}]({item.url}) — {item.title}"
        + (
            f" · Target Date: {item.target_date_value or item.target_date.isoformat()}"
            if item.target_date_value or item.target_date
            else ""
        )
        for item in items
    ]
