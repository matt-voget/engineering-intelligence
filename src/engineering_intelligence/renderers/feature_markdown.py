"""Deterministic Markdown renderer for Feature-detail presentation data."""

from engineering_intelligence.presentations.feature import (
    FeatureDetail,
    FeatureHierarchyNode,
)


def render_feature_markdown(feature: FeatureDetail) -> str:
    lines = [
        f"# Feature {feature.feature_key}: {feature.title}",
        "",
        f"- Jira: [{feature.feature_key}]({feature.feature_url})",
        f"- Original Jira type: {feature.original_issue_type}",
        f"- Status: {feature.status}",
        f"- Team: {feature.team_name or 'Unmapped'}",
        f"- Snapshot: `{feature.snapshot_name or feature.snapshot_id}`",
        f"- Snapshot ID: `{feature.snapshot_id}`",
        f"- Created: {feature.snapshot_created_at.isoformat()}",
        "",
        "Source freshness:",
        "",
    ]
    lines.extend(
        f"- {source.source} `{source.scope}`: {source.observed_at.isoformat()}"
        for source in feature.source_freshness
    )
    lines.extend(
        [
            "",
            "## Summary",
            "",
            f"- Total Jira issues: {feature.summary.total_issues}",
            f"- Descendants: {feature.summary.descendant_issues}",
            f"- Related people: {feature.summary.related_people}",
            f"- Jira links: {feature.summary.jira_links}",
            f"- Blocking relationships: {feature.summary.blocking_links}",
            f"- Linked delivery records: {feature.summary.linked_delivery_records}",
            "- Statuses: "
            + ", ".join(
                f"{status} ({count})"
                for status, count in feature.summary.status_counts.items()
            ),
            "",
            "## Work hierarchy",
            "",
            *_hierarchy_lines(feature.hierarchy),
            "",
            "## Contributors",
            "",
        ]
    )
    if feature.contributors:
        lines.extend(
            f"- {person.display_name} — {person.relationship_type} on "
            f"[{person.direct_issue_key}]({person.direct_issue_url})"
            + (f" · [source evidence]({person.source_url})" if person.source_url else "")
            + ("; rolled up to Feature" if person.rolled_up_to_feature else "")
            for person in feature.contributors
        )
    else:
        lines.append("- None observed")
    lines.extend(["", "## Jira links and dependencies", ""])
    if feature.jira_links:
        for link in feature.jira_links:
            target = (
                f"[{link.target_issue_key}]({link.target_url})"
                if link.target_issue_key and link.target_url
                else "uncollected linked issue"
            )
            suffix = " — blocking relationship" if link.is_blocking_relationship else ""
            lines.append(
                f"- {link.source_issue_key} {link.relationship} {target}"
                + (f" — {link.target_title}" if link.target_title else "")
                + suffix
            )
    else:
        lines.append("- None observed")
    lines.extend(["", "## Timeline", ""])
    lines.extend(
        f"- {event.occurred_at.isoformat()} — "
        f"[{event.issue_key}]({event.issue_url}) — {event.description}"
        for event in feature.timeline
    )
    lines.extend(
        [
            "",
            "## GitHub delivery",
            "",
            feature.github_delivery.message,
        ]
    )
    if feature.github_delivery.records:
        lines.append("")
        for record in feature.github_delivery.records:
            actor = f" · {record.actor_login}" if record.actor_login else ""
            rolled_up = " · rolled up from child Jira issue" if record.rolled_up_to_feature else ""
            lines.append(
                f"- [{record.record_type}: {record.title}]({record.url}) · "
                f"{record.repository} · {record.state}{actor} · direct evidence on "
                f"[{record.direct_jira_key}]({record.direct_jira_url}) · "
                f"{record.confidence}{rolled_up}"
            )
    lines.extend(
        [
            "",
            "## Data quality",
            "",
            *(f"- {note}" for note in feature.data_quality_notes),
        ]
    )
    return "\n".join(lines).rstrip() + "\n"


def _hierarchy_lines(node: FeatureHierarchyNode) -> list[str]:
    indent = "  " * node.depth
    assignee = (
        f" · {node.direct_assignee.display_name}" if node.direct_assignee else ""
    )
    line = (
        f"{indent}- [{node.jira_key}]({node.url}) · {node.issue_type} · "
        f"{node.status} · {node.title}{assignee} · "
        f"{node.rollup_people_count} related people"
    )
    return [line, *(line for child in node.children for line in _hierarchy_lines(child))]
