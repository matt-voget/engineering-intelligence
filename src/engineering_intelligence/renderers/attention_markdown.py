"""Markdown renderers for the deterministic Attention contracts."""

from engineering_intelligence.presentations.attention import AttentionFlag, AttentionInbox


def render_attention_markdown(inbox: AttentionInbox) -> str:
    lines = [
        "# Attention",
        "",
        f"- Generated: {inbox.generated_at.isoformat()}",
        f"- Collection: {inbox.collection.value}",
        f"- Unread only: {'yes' if inbox.unread_only else 'no'}",
        f"- Counts: {', '.join(f'{key}={value}' for key, value in inbox.counts.items())}",
        "",
        "| State | Flag | Team | Area | Severity | Started | Last observed | Evidence |",
        "|---|---|---|---|---|---|---|---:|",
    ]
    for flag in inbox.flags:
        state = "unread" if flag.unread else "viewed"
        lines.append(
            f"| {state} | {flag.title} | {flag.team_name} | {flag.health_area} | "
            f"{flag.severity} | {flag.condition_started_at.isoformat()} | "
            f"{flag.last_observed_at.isoformat()} | {flag.evidence_count} |"
        )
    if not inbox.flags:
        lines.append("| — | No matching flags | — | — | — | — | — | 0 |")
    if inbox.data_quality_notes:
        lines.extend(["", "## Data quality", ""])
        lines.extend(f"- {note}" for note in inbox.data_quality_notes)
    return "\n".join(lines) + "\n"


def render_attention_flag_markdown(flag: AttentionFlag) -> str:
    lines = [
        f"# {flag.title}",
        "",
        f"- Fingerprint: `{flag.fingerprint}`",
        f"- Team: {flag.team_name}",
        f"- Area: {flag.health_area}",
        f"- Severity: {flag.severity}",
        (
            f"- Signal definition: `{flag.signal_definition_key}"
            f"@{flag.signal_definition_version}`"
            if flag.signal_definition_key
            else "- Signal definition: unavailable for this historical flag"
        ),
        (
            f"- Signal evaluation: `{flag.signal_evaluation_id}`"
            if flag.signal_evaluation_id
            else "- Signal evaluation: unavailable for this historical flag"
        ),
        f"- Collection: {flag.collection.value}",
        f"- Read state: {'unread' if flag.unread else 'viewed'}",
        f"- Condition started: {flag.condition_started_at.isoformat()}",
        f"- First detected: {flag.first_detected_at.isoformat()}",
        f"- Last observed: {flag.last_observed_at.isoformat()}",
        f"- Active duration: {flag.active_duration_seconds} seconds",
        f"- Confidence: {flag.confidence}",
        "",
        flag.explanation or "No explanation was captured.",
        "",
        "## Evidence",
        "",
    ]
    lines.extend(
        f"- [{item.label}]({item.url})"
        + (f" — {item.jira_key}: {item.title}" if item.jira_key else "")
        for item in flag.evidence
    )
    if not flag.evidence:
        lines.append("- No evidence captured.")
    lines.extend(["", "## Investigation questions", ""])
    lines.extend(f"- {question}" for question in flag.investigation_questions)
    lines.extend(["", "## Lifecycle", ""])
    for occurrence in flag.occurrences:
        resolution = (
            f", resolved {occurrence.resolved_at.isoformat()}"
            if occurrence.resolved_at
            else ", active"
        )
        lines.append(
            f"- Occurrence `{occurrence.occurrence_id}` opened "
            f"{occurrence.opened_at.isoformat()}{resolution}"
        )
        lines.extend(
            f"  - {event.occurred_at.isoformat()}: {event.event_type} "
            f"({event.severity}, snapshot `{event.snapshot_id}`)"
            for event in occurrence.events
        )
    return "\n".join(lines) + "\n"
