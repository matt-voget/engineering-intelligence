"""Markdown renderer for deterministic engineering Metrics."""

from engineering_intelligence.presentations.metrics import MetricsView


def render_metrics_markdown(view: MetricsView) -> str:
    lines = [
        "# Engineering Metrics",
        "",
        f"- Snapshot: `{view.snapshot_id}` ({view.snapshot_name or 'unnamed'})",
        f"- Date range: {view.date_from.isoformat()} through {view.date_to.isoformat()}",
        f"- Team: {view.team or 'All configured source scope'}",
        f"- Definition set: {view.definition_set_version}",
        f"- Local Jira scopes: {', '.join(view.local_source_scopes)}",
        f"- Selected scope: {view.selected_source_scope or 'all local Jira scopes'}",
        f"- Leadership comparison: {view.leadership_comparison_status}",
        "",
        "| Phase | Metric | Current | Sample | 90-day baseline | Change | Health |",
        "|---|---|---:|---:|---:|---:|---|",
    ]
    for metric in view.metrics:
        lines.append(
            f"| {metric.definition.phase.value} | {metric.definition.label} | "
            f"{_number(metric.current_value)} | {metric.sample_size} | "
            f"{_number(metric.baseline_90_day_value)} | "
            f"{_number(metric.change_from_baseline)} | {metric.health.value} |"
        )
    lines.extend(["", "## Definitions and contributing issues", ""])
    for metric in view.metrics:
        lines.extend(
            [
                f"### {metric.definition.label}",
                "",
                f"- Formula: {metric.definition.formula}",
                f"- Inclusion: {metric.definition.inclusion_rule}",
                (
                    f"- Coverage: {metric.sample_size}/{metric.candidate_issue_count} "
                    f"qualifying; excluded type={metric.excluded_issue_type_count}, "
                    f"missing completion={metric.excluded_missing_completion_count}, "
                    f"outside period={metric.excluded_outside_period_count}, "
                    f"incomplete transitions="
                    f"{metric.excluded_incomplete_transition_count}"
                ),
                (
                    f"- Leadership comparison: [{metric.definition.leadership_label}]"
                    f"({metric.definition.leadership_report_url})"
                ),
            ]
        )
        lines.extend(
            f"- [{item.jira_key}]({item.url}) — {item.title}: "
            f"{item.value} calendar days"
            for item in metric.contributions
        )
        if not metric.contributions:
            lines.append("- No qualifying contributions.")
        lines.append("")
    lines.extend(["## Data quality", ""])
    lines.extend(f"- {note}" for note in view.data_quality_notes)
    return "\n".join(lines) + "\n"


def _number(value: float | None) -> str:
    return "—" if value is None else f"{value:.2f}"
