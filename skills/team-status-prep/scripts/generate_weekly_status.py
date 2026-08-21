#!/usr/bin/env python3
"""Render a self-contained weekly status report from one pinned snapshot."""

from __future__ import annotations

import argparse
import html
import json
import os
import re
import subprocess
from datetime import UTC, datetime
from pathlib import Path


def esc(value: object) -> str:
    return html.escape("" if value is None else str(value), quote=True)


def run_json(args: list[str], data_dir: Path) -> dict:
    env = os.environ.copy()
    env.setdefault("UV_CACHE_DIR", "/private/tmp/engintel-uv-cache")
    result = subprocess.run(
        ["uv", "run", "engintel", *args, "--data-dir", str(data_dir), "--format", "json"],
        check=True, capture_output=True, text=True, env=env,
    )
    return json.loads(result.stdout)


def link(url: str | None, label: object) -> str:
    return f'<a href="{esc(url)}" target="_blank" rel="noreferrer">{esc(label)}</a>' if url else esc(label)


def internal_link(url: str, label: object, *, css_class: str = "") -> str:
    class_attr = f' class="{esc(css_class)}"' if css_class else ""
    return f'<a href="{esc(url)}"{class_attr}>{esc(label)}</a>'


def linked_jira_text(value: str, issue_urls: dict[str, str]) -> str:
    """Escape prose while turning known Jira keys into inline evidence links."""
    parts = re.split(r"\b([A-Z][A-Z0-9]+-\d+)\b", value)
    return "".join(
        link(issue_urls.get(part), part) if part in issue_urls else esc(part)
        for part in parts
    )


def citation(keys: list[str], *, limit: int = 6) -> str:
    unique = list(dict.fromkeys(key for key in keys if key))
    if not unique:
        return ""
    shown = unique[:limit]
    suffix = f", +{len(unique) - limit} more" if len(unique) > limit else ""
    return " (" + ", ".join(shown) + suffix + ")"


def slug(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", value.casefold()).strip("-")


def stale(node: dict, snapshot_at: datetime) -> bool:
    if (node.get("status_category") or "").casefold() == "done":
        return False
    updated = node.get("source_updated_at")
    if not updated:
        return False
    return (snapshot_at - datetime.fromisoformat(updated)).days >= 14


def hierarchy(
    node: dict,
    parent_team: str | None,
    memberships: dict[str, set[str]],
    snapshot_at: datetime,
    depth: int = 0,
) -> str:
    assignee = (node.get("direct_assignee") or {}).get("display_name") or "Unassigned"
    team = node.get("team_name") or "No team"
    badges = []
    if depth and not node.get("team_name"):
        badges.append('<span class="badge warn">No child team</span>')
    if depth and parent_team and node.get("team_name") and node["team_name"] != parent_team:
        badges.append('<span class="badge warn">Different child team</span>')
    account_id = (node.get("direct_assignee") or {}).get("jira_account_id")
    if depth and account_id and parent_team and parent_team not in memberships.get(account_id, set()):
        badges.append('<span class="badge bad">Child assignee outside parent team</span>')
    if depth and stale(node, snapshot_at):
        badges.append('<span class="badge warn">Stale 14+d</span>')
    children = "".join(
        hierarchy(child, parent_team, memberships, snapshot_at, depth + 1)
        for child in node.get("children", [])
    )
    return (
        f'<li><div class="issue-row" data-date="{date_attr(node.get("source_updated_at"))}">'
        f'<span class="depth">{depth}</span>'
        f'{link(node.get("url"), node.get("jira_key"))} '
        f'<strong>{esc(node.get("title"))}</strong> '
        f'<span class="muted">{esc(node.get("status"))} · {esc(team)} · {esc(assignee)}</span>'
        f'{"".join(badges)}</div>{f"<ul>{children}</ul>" if children else ""}</li>'
    )


def feature_card(
    feature: dict,
    item: dict,
    memberships: dict[str, set[str]],
    snapshot_at: datetime,
) -> str:
    root = feature["hierarchy"]
    target = item.get("target_date_value") or item.get("target_date")
    badges = []
    if not item.get("assignee_account_id"):
        badges.append('<span class="badge bad">No parent assignee</span>')
    if not target:
        badges.append('<span class="badge bad">No Target Date</span>')
    elif item.get("target_date"):
        try:
            days = (datetime.fromisoformat(item["target_date"]).date() - datetime.now(UTC).date()).days
            if days < 0:
                badges.append(f'<span class="badge bad">Target overdue {abs(days)}d</span>')
            elif days <= 14:
                badges.append(f'<span class="badge warn">Target in {days}d</span>')
        except ValueError:
            pass
    child_teams = {n.get("team_name") for n in flatten(root)[1:] if n.get("team_name")}
    if len(child_teams) > 1:
        badges.append('<span class="badge warn">Multiple child teams</span>')
    customers = root.get("gravitee_customers") or []
    root_account = (root.get("direct_assignee") or {}).get("jira_account_id")
    parent_team = feature.get("team_name")
    if root_account and parent_team and parent_team not in memberships.get(root_account, set()):
        badges.append('<span class="badge bad">Parent assignee outside team</span>')
    if stale(root, snapshot_at):
        badges.append('<span class="badge warn">Stale parent 14+d</span>')
    delivery = feature.get("github_delivery", {}).get("records", [])
    delivery_html = "".join(
        f'<li data-date="{date_attr(row.get("occurred_at"))}">'
        f'{link(row.get("url"), row.get("record_type"))}: {esc(row.get("title"))} '
        f'<span class="muted">{esc(row.get("actor_login") or "unknown")}'
        f'{" · " + esc(display_date(row.get("occurred_at"))) if row.get("occurred_at") else ""}</span></li>'
        for row in delivery[:20]
    ) or '<li class="muted">No linked GitHub delivery evidence.</li>'
    return f'''<article class="card searchable" data-status="{esc(item.get('status'))}" data-date="{date_attr(item.get('source_updated_at'))}">
      <header><div><span class="eyebrow">{esc(item.get('status'))} · {esc(feature.get('team_name') or 'No team')}</span>
      <h3>{link(item.get('url'), item.get('jira_key'))} — {esc(item.get('title'))}</h3></div>
      <div class="badges">{''.join(badges) or '<span class="badge good">No currently evaluable issue</span>'}</div></header>
      <p>{esc(concise_text(root.get('description_text')) or 'No Jira description was captured for this issue.')}</p>
      <p class="muted">Target Date: {esc(target or 'Missing')}{' · Customers: ' + esc(', '.join(customers)) if customers else ''} · {feature['summary']['descendant_issues']} descendants · {feature['summary']['linked_delivery_records']} linked delivery records</p>
      <details><summary>Child stories and subtasks</summary><ul class="tree">{hierarchy(root, feature.get('team_name'), memberships, snapshot_at)}</ul></details>
      <details><summary>GitHub evidence</summary><ul>{delivery_html}</ul></details>
      <details><summary>Data-quality notes</summary><ul>{''.join(f'<li>{esc(note)}</li>' for note in feature.get('data_quality_notes', [])) or '<li>None supplied.</li>'}</ul></details>
    </article>'''


def flatten(node: dict) -> list[dict]:
    return [node, *(child for child_node in node.get("children", []) for child in flatten(child_node))]


def feature_findings(
    feature: dict,
    item: dict,
    memberships: dict[str, set[str]],
    snapshot_at: datetime,
) -> list[str]:
    root = feature["hierarchy"]
    findings = []
    if not item.get("assignee_account_id"):
        findings.append("No parent assignee")
    if not (item.get("target_date_value") or item.get("target_date")):
        findings.append("No Target Date")
    parent_team = feature.get("team_name")
    root_account = (root.get("direct_assignee") or {}).get("jira_account_id")
    if root_account and parent_team and parent_team not in memberships.get(root_account, set()):
        findings.append("Parent assignee outside team")
    if stale(root, snapshot_at):
        findings.append("Stale parent 14+d")
    child_teams = {node.get("team_name") for node in flatten(root)[1:] if node.get("team_name")}
    if len(child_teams) > 1:
        findings.append("Multiple child teams")
    for node in flatten(root)[1:]:
        if not node.get("team_name"):
            findings.append("Child without team")
        elif parent_team and node["team_name"] != parent_team:
            findings.append("Different child team")
        account_id = (node.get("direct_assignee") or {}).get("jira_account_id")
        if account_id and parent_team and parent_team not in memberships.get(account_id, set()):
            findings.append("Child assignee outside parent team")
        if stale(node, snapshot_at):
            findings.append("Stale child 14+d")
    return findings


def concise_text(value: str | None, limit: int = 240) -> str:
    text = " ".join((value or "").split())
    if not text:
        return ""
    sentences = re.split(r"(?<=[.!?])\s+", text)
    result = sentences[0]
    if len(result) < 100 and len(sentences) > 1:
        result += " " + sentences[1]
    return result if len(result) <= limit else result[: limit - 1].rsplit(" ", 1)[0] + "…"


def date_attr(value: object) -> str:
    """Calendar-date attribute value driving the client-side time-range filter."""
    return esc(str(value)[:10]) if value else ""


def display_date(value: str | None) -> str:
    """Render source timestamps as compact, sortable calendar dates."""
    if not value:
        return "Unknown"
    try:
        return datetime.fromisoformat(value).date().isoformat()
    except ValueError:
        return value


def recent_team_issues(team: dict, *, limit: int = 10) -> list[dict]:
    """Return unique team-workflow issues ordered by pinned Jira update time."""
    issues: dict[str, dict] = {}
    for state in team.get("workflow", []):
        for item in state.get("items", []):
            key = item.get("jira_key")
            if key and (
                key not in issues
                or (item.get("source_updated_at") or "")
                > (issues[key].get("source_updated_at") or "")
            ):
                issues[key] = item
    return sorted(
        issues.values(), key=lambda item: item.get("source_updated_at") or "", reverse=True
    )[:limit]


TARGET_MONTH_RE = re.compile(r"^\d{4}-(0[1-9]|1[0-2])$")
DONE_COLUMN = "Done"
CHILD_STATES = ("total", "done", "in_progress", "not_started", "unknown")
# Jira status categories are the only completion signal the snapshot pins for a
# child issue; the board column applies to the parent alone.
CATEGORY_STATE = {"done": "done", "indeterminate": "in_progress", "new": "not_started"}
STATE_LABEL = {
    "done": "Done",
    "in_progress": "In progress",
    "not_started": "Not started",
    "unknown": "Unknown",
}


def descendant_records(root: dict | None) -> list[dict]:
    """Flatten a dated parent's descendants into ordered rows with their state.

    Every descendant at any depth appears once, in hierarchy order. A category
    the snapshot does not carry becomes ``unknown`` rather than being assumed
    unfinished, so it can be reported instead of quietly deflating the parent.
    """
    return [
        {
            "jira_key": node.get("jira_key"),
            "title": node.get("title"),
            "status": node.get("status"),
            "url": node.get("url"),
            "depth": node.get("depth") or 1,
            "team_name": node.get("team_name"),
            "state": CATEGORY_STATE.get(
                (node.get("status_category") or "").casefold(), "unknown"
            ),
        }
        for node in (flatten(root)[1:] if root else [])
    ]


def rollup_counts(records: list[dict]) -> dict[str, int]:
    """Tally descendant rows by state, keeping ``total`` reconciled."""
    counts = dict.fromkeys(CHILD_STATES, 0)
    counts["total"] = len(records)
    for record in records:
        counts[record["state"]] += 1
    return counts


def completion_by_target_date(team: dict, hierarchies: dict[str, dict] | None = None) -> dict:
    """Bucket configured board-workflow items by their own Target Date month.

    An item counts as wholly complete only when it sits in the workflow's Done
    column. Values that are not an exact ``YYYY-MM`` month are never treated as
    dated; they are returned verbatim so the caller can report them as hygiene.

    When ``hierarchies`` supplies a dated item's child tree, that item also
    earns partial credit equal to its share of done descendants, so a parent
    whose children are half finished reads as half complete instead of 0%.
    An item already in the Done column keeps full credit — the board is the
    authority on the parent itself — and its unfinished children are reported
    as hygiene rather than used to reduce the percentage. In-progress children
    are counted and shown but earn no credit; only a done child does.
    """
    hierarchies = hierarchies or {}
    months: dict[str, dict] = {}
    malformed: list[tuple[str, str]] = []
    rows: list[dict] = []
    missing_hierarchy: list[str] = []
    open_children_under_done: list[tuple[str, int]] = []
    dated = 0
    undated = 0
    for state in team.get("workflow", []):
        complete = state.get("name") == DONE_COLUMN
        for item in state.get("items", []):
            key = item.get("jira_key") or "Unknown issue"
            raw = item.get("target_date_value")
            if raw and not TARGET_MONTH_RE.match(raw):
                malformed.append((key, raw))
                raw = None
            if not raw:
                undated += 1
                continue
            dated += 1
            root = hierarchies.get(key)
            records = descendant_records(root)
            children = rollup_counts(records)
            if root is None:
                missing_hierarchy.append(key)
            if complete:
                credit = 1.0
                if children["total"] - children["done"]:
                    open_children_under_done.append(
                        (key, children["total"] - children["done"])
                    )
            elif children["total"]:
                credit = children["done"] / children["total"]
            else:
                credit = 0.0
            bucket = months.setdefault(
                raw,
                {
                    "total": 0,
                    "done": 0,
                    "credit": 0.0,
                    "children": dict.fromkeys(CHILD_STATES, 0),
                },
            )
            bucket["total"] += 1
            bucket["done"] += complete
            bucket["credit"] += credit
            for field in CHILD_STATES:
                bucket["children"][field] += children[field]
            rows.append({
                "jira_key": key,
                "title": item.get("title"),
                "url": item.get("url"),
                "month": raw,
                "column": state.get("name"),
                "complete": complete,
                "children": children,
                "records": records,
                "credit": credit,
            })
    return {
        "months": dict(sorted(months.items())),
        "dated": dated,
        "undated": undated,
        "malformed": malformed,
        "items": sorted(rows, key=lambda row: (row["month"], row["jira_key"])),
        "missing_hierarchy": missing_hierarchy,
        "open_children_under_done": open_children_under_done,
    }


def completion_pct(done: float, total: int) -> float:
    return round(100.0 * done / total, 1) if total else 0.0


def recent_team_pull_requests(
    team: dict,
    team_people: list[dict],
    *,
    limit: int = 10,
) -> list[dict]:
    """Union Jira-linked delivery with configured team-member authored PRs."""
    grouped: dict[str, dict] = {}
    evidence_rows = [
        (record, "jira_linked") for record in team.get("github_delivery", [])
    ]
    evidence_rows.extend(
        (record, "team_member_author")
        for person in team_people
        for record in person.get("github_contributions", [])
    )
    for record, association in evidence_rows:
        if record.get("record_type") != "pull_request":
            continue
        identity = record.get("url") or record.get("record_id")
        if not identity:
            continue
        row = grouped.setdefault(
            identity,
            {**record, "jira_items": {}, "associations": set()},
        )
        if (record.get("occurred_at") or "") > (row.get("occurred_at") or ""):
            jira_items = row["jira_items"]
            associations = row["associations"]
            row.update(record)
            row["jira_items"] = jira_items
            row["associations"] = associations
        row["associations"].add(association)
        jira_key = record.get("direct_jira_key")
        if jira_key:
            row["jira_items"][jira_key] = record.get("direct_jira_url")
    return sorted(
        grouped.values(), key=lambda row: row.get("occurred_at") or "", reverse=True
    )[:limit]


COMPLETION_RULE = (
    "Buckets board-workflow items by each item's own Target Date. Jira stores Target "
    "Date as a month, so completion is reported per month and no day-level due date or "
    "overdue count is derived. Items without a valid Target Date are excluded from the "
    "percentage."
)
ROLLUP_RULE = (
    "Rolled-up completion credits a dated parent with its share of done child issues at "
    "any depth, so a parent whose children are half finished reads as half complete "
    "rather than 0%. Only a child in a done Jira status category earns credit; children "
    "in progress are counted and shown but earn none. A parent already in the Done "
    "column keeps full credit and a parent with no children scores by its own column "
    "alone. Board-only completion counts whole items in the Done column and is kept "
    "alongside so the two readings stay separable."
)
COMPLETION_EMPTY = (
    "No item in this team's configured board workflow carries a valid Target Date, "
    "so completion against due dates is not computable for this snapshot."
)


METER_STATES = ("done", "in_progress", "not_started", "unknown")


def meter_html(counts: dict[str, int]) -> str:
    """Part-to-whole bar of child states, sized by count.

    Segments carry no text: the adjacent columns hold every number, so the bar
    is a glance-level read and the table beside it is its accessible twin.
    """
    if not counts["total"]:
        return '<span class="muted">—</span>'
    label = ", ".join(
        f"{counts[state]} {STATE_LABEL[state].lower()}"
        for state in METER_STATES if counts[state]
    )
    segments = "".join(
        f'<span class="seg {state}" style="flex:{counts[state]}"></span>'
        for state in METER_STATES if counts[state]
    )
    return f'<span class="meter" role="img" aria-label="{esc(label)}">{segments}</span>'


def completion_legend_html(counts: dict[str, int]) -> str:
    """Key the states this team actually has, so no swatch stands for nothing."""
    present = [state for state in METER_STATES if counts.get(state)]
    if not present:
        return ""
    return '<p class="legend">' + "".join(
        f'<span class="key"><span class="swatch {state}"></span>{esc(STATE_LABEL[state])}</span>'
        for state in present
    ) + "</p>"


def aggregate_children(months: dict[str, dict]) -> dict[str, int]:
    return {
        field: sum(bucket["children"][field] for bucket in months.values())
        for field in CHILD_STATES
    }


def accordion(
    title: str,
    inner: str,
    *,
    section_id: str = "",
    classes: str = "",
    open_default: bool = False,
    summary_extra: str = "",
) -> str:
    """Collapsible report section; every section except the team summary
    renders through this and starts collapsed. ``summary_extra`` stays visible
    on the collapsed summary line for at-a-glance readings."""
    id_attr = f' id="{esc(section_id)}"' if section_id else ""
    class_attr = f' class="{esc(classes)}"' if classes else ""
    open_attr = " open" if open_default else ""
    return (
        f'<section{id_attr}{class_attr}><details class="accordion"{open_attr}>'
        f'<summary><h2>{esc(title)}</h2>{summary_extra}</summary>{inner}</details></section>'
    )


def completion_table_html(completion: dict) -> str:
    """Month-level completion summary rendered in each team detail section."""
    months = completion["months"]
    if not months:
        return (
            '<section class="activity-tables">'
            f'<p class="table-note">{esc(COMPLETION_RULE)}</p>'
            f'<p class="empty">{esc(COMPLETION_EMPTY)}</p></section>'
        )
    rows = "".join(
        f"<tr><td><strong>{esc(month)}</strong></td>"
        f"<td class='num'><strong>{completion_pct(bucket['credit'], bucket['total'])}%</strong></td>"
        f"<td class='num'>{completion_pct(bucket['done'], bucket['total'])}% "
        f"<span class='muted'>({bucket['done']}/{bucket['total']})</span></td>"
        f"<td class='meter-cell'>{meter_html(bucket['children'])}</td>"
        f"<td class='num'>{bucket['children']['total'] or '—'}</td>"
        f"<td class='num'>{bucket['children']['done']}</td>"
        f"<td class='num'>{bucket['children']['in_progress']}</td>"
        f"<td class='num'>{bucket['children']['not_started']}</td></tr>"
        for month, bucket in months.items()
    )
    rows += (
        f'<tr><td>Not dated</td><td class="muted">Excluded</td>'
        f'<td class="muted">{completion["undated"]} item'
        f'{"s" if completion["undated"] != 1 else ""}</td>'
        f'<td class="muted">—</td><td class="muted">—</td><td class="muted">—</td>'
        f'<td class="muted">—</td><td class="muted">—</td></tr>'
    )
    return f'''<section class="activity-tables">
      <p class="table-note">{esc(COMPLETION_RULE)}</p>
      <p class="table-note">{esc(ROLLUP_RULE)}</p>
      {completion_legend_html(aggregate_children(months))}
      <div class="table-wrap"><table><thead><tr><th>Target Date</th><th class="num">Rolled up</th><th class="num">Board only</th><th>Child progress</th><th class="num">Children</th><th class="num">Done</th><th class="num">In progress</th><th class="num">Not started</th></tr></thead><tbody>{rows}</tbody></table></div>
      {completion_breakdown_html(completion)}</section>'''


def completion_breakdown_html(completion: dict) -> str:
    """Parent-by-parent breakdown, each expandable to its own child issues."""
    items = completion["items"]
    if not items:
        return ""
    body = []
    for month, bucket in completion["months"].items():
        month_rows = [row for row in items if row["month"] == month]
        body.append(
            f'<tr class="month-row"><th colspan="6">{esc(month)} '
            f'<span class="muted">· {completion_pct(bucket["credit"], bucket["total"])}% rolled up '
            f'across {bucket["total"]} dated item{"s" if bucket["total"] != 1 else ""}</span></th></tr>'
        )
        for row in month_rows:
            key = row["jira_key"]
            counts = row["children"]
            toggle = (
                f'<button class="toggle" data-children="{esc(key)}" aria-expanded="false">'
                f'{counts["total"]} child issue{"s" if counts["total"] != 1 else ""}</button>'
                if counts["total"] else '<span class="muted">No child issues</span>'
            )
            body.append(
                f'<tr class="parent-row"><td>{link(row["url"], key)}</td>'
                f'<td>{esc(row["title"])}</td>'
                f'<td><span class="badge column">{esc(row["column"])}</span></td>'
                f'<td class="meter-cell">{meter_html(counts)}</td>'
                f'<td class="num"><strong>{completion_pct(row["credit"], 1)}%</strong></td>'
                f'<td>{toggle}</td></tr>'
            )
            for record in row["records"]:
                body.append(
                    f'<tr class="child-row collapsed" data-parent="{esc(key)}">'
                    f'<td class="child-key">{link(record["url"], record["jira_key"])}</td>'
                    f'<td>{esc(record["title"])} '
                    f'<span class="muted">· {esc(record["team_name"] or "No team")}</span></td>'
                    f'<td class="muted">{esc(record["status"])}</td>'
                    f'<td><span class="badge state {esc(record["state"])}">'
                    f'{esc(STATE_LABEL[record["state"]])}</span></td>'
                    f'<td class="num muted">—</td><td></td></tr>'
                )
    return f'''<h3>Rollup by parent and child issue</h3>
      <p class="table-note">Each dated board item with the child issues underneath it. The percentage is the completion credit that item contributes to its month — its share of done children, or 100% once the parent itself reaches the Done column. Select a child count to list the issues.</p>
      <p class="table-note"><button class="toggle expand-all">Expand all children</button></p>
      <div class="table-wrap"><table class="breakdown"><thead><tr><th>Issue</th><th>Title</th><th>Status</th><th>Progress</th><th class="num">Contributes</th><th>Children</th></tr></thead><tbody>{''.join(body)}</tbody></table></div>'''


def team_activity_tables(team: dict, team_people: list[dict]) -> str:
    jira_assignees = {
        person.get("jira_account_id"): person.get("preferred_name") or person.get("display_name")
        for person in team.get("roster", [])
        if person.get("jira_account_id")
    }
    github_authors = {
        person.get("github_login", "").casefold(): person.get("preferred_name") or person.get("display_name")
        for person in team.get("roster", [])
        if person.get("github_login")
    }

    def jira_assignee(item: dict) -> str:
        account_id = item.get("assignee_account_id")
        if not account_id:
            return "Unassigned"
        return jira_assignees.get(account_id, "Outside configured team roster")

    def github_author(record: dict) -> str:
        login = record.get("actor_login")
        if not login:
            return "Unknown"
        display_name = github_authors.get(login.casefold())
        return f"{display_name} (@{login})" if display_name else f"@{login}"

    issues = recent_team_issues(team)
    issue_rows = "".join(
        f'''<tr data-date="{date_attr(item.get('source_updated_at'))}"><td>{link(item.get("url"), item.get("jira_key"))}</td>
        <td><time datetime="{esc(item.get('source_updated_at'))}">{esc(display_date(item.get('source_updated_at')))}</time></td>
        <td>{esc(item.get("status") or "Unknown")}</td><td>{esc(jira_assignee(item))}</td>
        <td>{esc(concise_text(item.get("title"), 180) or "No summary supplied.")}</td></tr>'''
        for item in issues
    ) or '<tr><td colspan="5" class="empty">No Jira issues were present in the configured team workflow.</td></tr>'

    pull_requests = recent_team_pull_requests(team, team_people)
    pr_rows = []
    for record in pull_requests:
        jira_items = record.get("jira_items", {})
        jira_html = ", ".join(
            link(url, key) if url else esc(key) for key, url in sorted(jira_items.items())
        ) or '<span class="muted">No Jira association</span>'
        associations = record.get("associations", set())
        if associations == {"jira_linked", "team_member_author"}:
            association_html = "Jira-linked + team-member authored"
        elif "team_member_author" in associations:
            association_html = "Team-member authored"
        else:
            association_html = "Jira-linked delivery"
        pr_label = record.get("record_id") or "Pull request"
        pr_rows.append(
            f'''<tr data-date="{date_attr(record.get('occurred_at'))}"><td>{link(record.get("url"), pr_label)}</td>
            <td><time datetime="{esc(record.get('occurred_at'))}">{esc(display_date(record.get('occurred_at')))}</time></td>
            <td>{esc(record.get("repository") or "Unknown repository")}</td>
            <td>{esc(github_author(record))}</td>
            <td>{esc(concise_text(record.get("title"), 180) or "No summary supplied.")}</td>
            <td>{jira_html}</td><td>{esc(association_html)}</td></tr>'''
        )
    pr_rows_html = "".join(pr_rows) or '<tr><td colspan="7" class="empty">No pull requests were present in the configured team evidence.</td></tr>'
    return f'''<section class="activity-tables"><h3>Recently updated Jira issues</h3>
      <p class="table-note">Latest 10 issues by Jira update date in this team's configured workflow.</p>
      <div class="table-wrap"><table><thead><tr><th>Jira</th><th>Updated</th><th>Status</th><th>Assignee</th><th>Short description</th></tr></thead><tbody>{issue_rows}</tbody></table></div>
      <h3>Recent GitHub pull requests</h3>
      <p class="table-note">Latest 10 observed pull requests linked to team Jira work or authored by a configured team engineer. Repeated evidence rows are combined.</p>
      <div class="table-wrap"><table><thead><tr><th>Pull request</th><th>Date</th><th>Repository</th><th>Author</th><th>Summary</th><th>Associated Jira</th><th>Association</th></tr></thead><tbody>{pr_rows_html}</tbody></table></div></section>'''


def recent_individual_pull_requests(person: dict, *, limit: int = 10) -> list[dict]:
    grouped: dict[str, dict] = {}
    for record in person.get("github_contributions", []):
        if record.get("record_type") != "pull_request":
            continue
        identity = record.get("url") or record.get("record_id")
        if not identity:
            continue
        row = grouped.setdefault(identity, {**record, "jira_items": {}})
        if (record.get("occurred_at") or "") > (row.get("occurred_at") or ""):
            jira_items = row["jira_items"]
            row.update(record)
            row["jira_items"] = jira_items
        jira_key = record.get("direct_jira_key")
        if jira_key:
            row["jira_items"][jira_key] = record.get("direct_jira_url")
    return sorted(
        grouped.values(), key=lambda row: row.get("occurred_at") or "", reverse=True
    )[:limit]


def individual_activity_tables(person: dict) -> str:
    display_name = person.get("preferred_name") or person["display_name"]
    jira_work = sorted(
        person.get("jira_work", []),
        key=lambda work: work.get("direct_issue_updated_at") or "",
        reverse=True,
    )[:10]
    jira_rows = []
    for work in jira_work:
        feature_html = (
            link(work.get("feature_url"), work.get("feature_key"))
            if work.get("feature_key")
            else '<span class="muted">No IBR mapping</span>'
        )
        target = work.get("target_date_value") or work.get("target_date") or "Not set"
        jira_rows.append(
            f'''<tr data-date="{date_attr(work.get('direct_issue_updated_at'))}"><td>{link(work.get("direct_issue_url"), work.get("direct_issue_key"))}</td>
            <td><time datetime="{esc(work.get('direct_issue_updated_at'))}">{esc(display_date(work.get('direct_issue_updated_at')))}</time></td>
            <td>{esc(work.get("direct_issue_status") or "Unknown")}</td><td>{esc(display_name)}</td>
            <td>{esc(target)}</td><td>{feature_html}</td>
            <td>{esc(concise_text(work.get("direct_issue_title"), 180) or "No summary supplied.")}</td></tr>'''
        )
    jira_rows_html = "".join(jira_rows) or '<tr><td colspan="7" class="empty">No Jira work was present in the configured individual evidence.</td></tr>'

    pr_rows = []
    for record in recent_individual_pull_requests(person):
        jira_items = record.get("jira_items", {})
        jira_html = ", ".join(
            link(url, key) if url else esc(key) for key, url in sorted(jira_items.items())
        ) or '<span class="muted">No Jira association</span>'
        association = (
            "Explicit Jira key"
            if jira_items
            else "Configured GitHub author identity"
        )
        pr_rows.append(
            f'''<tr data-date="{date_attr(record.get('occurred_at'))}"><td>{link(record.get("url"), record.get("record_id") or "Pull request")}</td>
            <td><time datetime="{esc(record.get('occurred_at'))}">{esc(display_date(record.get('occurred_at')))}</time></td>
            <td>{esc(record.get("repository") or "Unknown repository")}</td>
            <td>{esc(display_name)}</td><td>{esc(record.get("state") or "Unknown")}</td>
            <td>{esc(concise_text(record.get("title"), 180) or "No summary supplied.")}</td>
            <td>{jira_html}</td><td>{esc(association)}</td></tr>'''
        )
    pr_rows_html = "".join(pr_rows) or '<tr><td colspan="8" class="empty">No authored pull requests were present in the configured repository evidence.</td></tr>'
    return f'''<section class="activity-tables"><h3>Recently updated Jira issues</h3>
      <p class="table-note">Latest 10 assigned issues by Jira update date in the configured source scope.</p>
      <div class="table-wrap"><table><thead><tr><th>Jira</th><th>Updated</th><th>Status</th><th>Assignee</th><th>Target Date</th><th>IBR Feature</th><th>Short description</th></tr></thead><tbody>{jira_rows_html}</tbody></table></div>
      <h3>Recent authored GitHub pull requests</h3>
      <p class="table-note">Latest 10 pull requests matched through the individual's configured GitHub identity.</p>
      <div class="table-wrap"><table><thead><tr><th>Pull request</th><th>Date</th><th>Repository</th><th>Author</th><th>State</th><th>Summary</th><th>Associated Jira</th><th>Association</th></tr></thead><tbody>{pr_rows_html}</tbody></table></div></section>'''


WORK_CLASS_LABEL = {
    "ibr_linked": ("IBR-linked", "good"),
    "non_ibr": ("Non-IBR", "warn"),
    "unlinked": ("No Jira link", "column"),
}
WORK_BASIS_LABEL = {
    "on_ibr_board": "On IBR board",
    "descendant_of_ibr_item": "Child of IBR item",
    "explicit_jira_key": "Explicit Jira key",
    "via_pull_request": "Via pull request",
    "author_identity": "Author identity",
}
GITHUB_WORK_TABLE_LIMIT = 60


def work_class_badge(classification: str) -> str:
    label, css = WORK_CLASS_LABEL.get(classification, (classification, "column"))
    return f'<span class="badge {css}">{esc(label)}</span>'


def work_split_meter(split: dict) -> str:
    total = sum(split.values())
    if not total:
        return '<span class="muted">No records in the window.</span>'
    segments = "".join(
        f'<span class="seg {css}" style="flex-grow:{split.get(key, 0)}"></span>'
        for key, css in (("ibr_linked", "ibr"), ("non_ibr", "nonibr"), ("unlinked", "nolink"))
        if split.get(key)
    )
    return f'<span class="meter">{segments}</span>'


def work_split_text(split: dict, *, include_unlinked: bool) -> str:
    total = sum(split.values())
    parts = [
        f"{split.get('ibr_linked', 0)} IBR-linked",
        f"{split.get('non_ibr', 0)} non-IBR",
    ]
    if include_unlinked:
        parts.append(f"{split.get('unlinked', 0)} without a Jira link")
    pct = (
        f" ({round(100 * split.get('ibr_linked', 0) / total)}% IBR)" if total else ""
    )
    return " · ".join(parts) + pct


def team_work_summary_extra(work: dict | None) -> str:
    if not work:
        return '<span class="muted"> — classification unavailable</span>'
    jira = work_split_text(work["jira_split"], include_unlinked=False)
    github = work_split_text(work["github_split"], include_unlinked=True)
    return (
        f'<span class="glance"> — Jira active: {esc(jira)} · '
        f'GitHub {work["split_window_days"]}d: {esc(github)}</span>'
    )


def team_work_section(work: dict | None) -> str:
    if not work:
        return (
            '<p class="empty">The IBR-versus-non-IBR classification could not be '
            "computed for this snapshot; the team work command failed or the "
            "team-field query scope is not pinned.</p>"
        )
    jira_note = esc(work.get("jira_message") or "")
    github_note = esc(work.get("github_message") or "")
    def pull_request_links(item: dict) -> str:
        pulls = item.get("linked_pull_requests", [])
        if not pulls:
            return '<span class="muted">—</span>'
        return ", ".join(
            link(pull.get("url"), f"#{pull['record_id'].rsplit('#', 1)[-1]}")
            for pull in pulls
        )

    statuses = sorted({item.get("status") or "Unknown" for item in work.get("jira_issues", [])})
    status_filter = (
        '<span class="status-filter-group" role="group" aria-label="Filter by status">'
        '<span class="muted">Status:</span>'
        + "".join(
            f'<button type="button" class="status-chip" aria-pressed="false" data-status="{esc(status)}">{esc(status)}</button>'
            for status in statuses
        )
        + "</span>"
    ) if statuses else ""
    issue_rows = "".join(
        f'''<tr data-date="{date_attr(item.get('source_updated_at'))}" data-issue-status="{esc(item.get('status') or 'Unknown')}">
        <td>{link(item.get("url"), item.get("jira_key"))}</td>
        <td>{esc(item.get("issue_type") or "Unknown")}</td>
        <td>{esc(item.get("status") or "Unknown")}{'' if item.get("active") else ' <span class="muted">(done)</span>'}</td>
        <td>{esc(item.get("assignee_display_name") or "Unassigned")}</td>
        <td><time datetime="{esc(item.get('source_updated_at'))}">{esc(display_date(item.get('source_updated_at')))}</time></td>
        <td>{work_class_badge(item.get("classification"))}</td>
        <td>{link(item.get("ibr_parent_url"), item.get("ibr_parent_key")) if item.get("ibr_parent_key") else '<span class="muted">—</span>'}</td>
        <td>{pull_request_links(item)}</td>
        <td>{esc(concise_text(item.get("title"), 160) or "No summary supplied.")}</td></tr>'''
        for item in work.get("jira_issues", [])
    ) or '<tr><td colspan="9" class="empty">No team-field Jira issues fall inside the list window.</td></tr>'
    github_records = work.get("github_records", [])
    shown = github_records[:GITHUB_WORK_TABLE_LIMIT]
    truncation_note = (
        f"Showing the {len(shown)} most recent of {len(github_records)} records in the "
        f"{work['list_window_days']}-day window; the counts above cover all of them."
        if len(github_records) > len(shown)
        else f"All {len(github_records)} records in the {work['list_window_days']}-day window are shown."
    )
    record_rows = "".join(
        f'''<tr data-date="{date_attr(record.get('occurred_at'))}">
        <td>{link(record.get("url"), record.get("record_type").replace("_", " "))}</td>
        <td><time datetime="{esc(record.get('occurred_at'))}">{esc(display_date(record.get('occurred_at')))}</time></td>
        <td>{esc(record.get("repository"))}</td>
        <td>{esc(record.get("actor_login") or "Unknown")}</td>
        <td>{work_class_badge(record.get("classification"))}</td>
        <td>{esc(WORK_BASIS_LABEL.get(record.get("link_basis"), record.get("link_basis")))}</td>
        <td>{esc(", ".join(record.get("jira_keys", [])) or "—")}</td>
        <td>{esc(concise_text(record.get("title"), 140) or "No title supplied.")}</td></tr>'''
        for record in shown
    ) or '<tr><td colspan="8" class="empty">No GitHub records fall inside the list window.</td></tr>'
    notes = "".join(
        f"<li>{esc(note)}</li>" for note in work.get("data_quality_notes", [])
    )
    return f'''<p class="table-note">{jira_note} {github_note}</p>
      <h3>Jira issues — active, or updated in the last {work["list_window_days"]} days</h3>
      <div class="split-row">{work_split_meter(work["jira_split"])}<span>{esc(work_split_text(work["jira_split"], include_unlinked=False))} among active issues</span>{status_filter}</div>
      <div class="table-wrap"><table><thead><tr><th>Jira</th><th>Type</th><th>Status</th><th>Assignee</th><th>Updated</th><th>Classification</th><th>IBR parent</th><th>GitHub PR</th><th>Short description</th></tr></thead><tbody>{issue_rows}</tbody></table></div>
      <h3>GitHub work — last {work["list_window_days"]} days</h3>
      <div class="split-row">{work_split_meter(work["github_split"])}<span>{esc(work_split_text(work["github_split"], include_unlinked=True))} in the last {work["split_window_days"]} days</span></div>
      <p class="table-note">{esc(truncation_note)}</p>
      <div class="table-wrap"><table><thead><tr><th>Record</th><th>Date</th><th>Repository</th><th>Author</th><th>Classification</th><th>Link basis</th><th>Jira</th><th>Title</th></tr></thead><tbody>{record_rows}</tbody></table></div>
      <details><summary>Classification notes</summary><ul>{notes}</ul></details>'''


def build_cycle_time_section(view: dict | None) -> str:
    if not view:
        return '<p class="empty">Build Cycle Time is unavailable for this team.</p>'

    def status_data(durations: list[dict]) -> str:
        return esc(json.dumps({item["status"]: item["days"] for item in durations}))

    def child_rows(children: list[dict]) -> str:
        return "".join(
            f'''<tr class="cycle-child"><td style="padding-left:{16 + child.get('depth', 1) * 14}px">↳ {link(child.get("url"), child.get("jira_key"))}</td>
            <td>{esc(child.get("issue_type") or "Unknown")}</td>
            <td class="num">{f'{child["cycle_days"]:.2f} days' if child.get("cycle_days") is not None else '<span class="muted">Unavailable</span>'}</td>
            <td>{esc(display_date(child.get("period_started_at")))} → {esc(display_date(child.get("period_ended_at")))}</td>
            <td>{f'<span class="badge top-status">{esc(child.get("top_status"))}</span>' if child.get("top_status") else '<span class="muted">—</span>'}</td>
            <td>{esc(child.get("title") or "No summary supplied.")}{f'<br><span class="muted">{esc(child.get("warning"))}</span>' if child.get("warning") else ''}</td></tr>'''
            for child in children
        )

    def group_html(classification: str, heading: str) -> str:
        group = next(
            (item for item in view.get("groups", []) if item["classification"] == classification),
            {"contributions": []},
        )
        contributions = group.get("contributions", [])
        rows = "".join(
            f'''<tbody{rag_anchor_attr(item.get("rag"))} class="cycle-contribution rag-instance" data-cycle-ended="{date_attr(item.get('period_ended_at'))}" data-cycle-days="{item['cycle_days']}" data-status-durations="{status_data(item.get('status_durations', []))}">
            <tr class="cycle-parent"><td>{rag_badge(item.get("rag"))} {link(item.get("url"), item.get("jira_key"))}</td>
            <td>{esc(item.get("issue_type") or "Unknown")}</td>
            <td class="num"><strong>{item['cycle_days']:.2f} days</strong></td>
            <td>{esc(display_date(item.get("period_started_at")))} → <time datetime="{esc(item.get('period_ended_at'))}">{esc(display_date(item.get("period_ended_at")))}</time></td>
            <td>{f'<span class="badge top-status">{esc(item.get("top_status"))}</span>' if item.get("top_status") else '<span class="muted">—</span>'}</td>
            <td>{esc(item.get("title") or "No summary supplied.")}</td></tr>{child_rows(item.get("children", []))}</tbody>'''
            for item in contributions
        ) or '<tbody><tr><td colspan="6" class="empty">No qualifying completed issues.</td></tr></tbody>'
        return f'''<section class="cycle-group" data-cycle-group="{classification}"><h3>{esc(heading)}</h3>
        <div class="cycle-summary"><div class="metric"><strong class="cycle-average">—</strong>average calendar days</div><div class="metric"><strong class="cycle-sample">0</strong>qualifying issues</div><div class="metric"><strong class="cycle-top-status">—</strong>top contributing status</div></div>
        <p class="table-note">Top five contributors to the average for the selected Done-date range. Child rows show their own In Progress-to-Done cycle where complete transition evidence exists.</p>
        <div class="table-wrap"><table class="cycle-table"><thead><tr><th>Issue</th><th>Type</th><th class="num">Cycle time</th><th>Started → Done</th><th>Top status</th><th>Title</th></tr></thead>{rows}</table></div></section>'''

    notes = "".join(f'<li>{esc(note)}</li>' for note in view.get("data_quality_notes", []))
    return (
        '<p class="table-note">IBR-linked work includes Epic, Feature Request, and FDI Request parents. Non-IBR work includes every issue type assigned to the team that is neither on the IBR board nor below an IBR item. The global time filter selects issues by the date they entered Done.</p>'
        + group_html("ibr_linked", "IBR-linked parent issues")
        + group_html("non_ibr", "Non-IBR team issues — all issue types")
        + f'<details><summary>Metric definition and data notes</summary><ul>{notes}</ul></details>'
    )


def rag_anchor_attr(assessment: dict | None) -> str:
    return f' id="{esc(assessment["anchor_id"])}"' if assessment else ""


def rag_badge(assessment: dict | None) -> str:
    if not assessment:
        return ""
    level = assessment["level"]
    return (
        f'<span class="rag-badge rag-{esc(level)}" '
        f'title="{esc(assessment["explanation"])}" '
        f'aria-label="{esc(level.title())}: {esc(assessment["rule_label"])}">'
        f'<span aria-hidden="true">{esc(assessment["symbol"])}</span> '
        f'{esc(level.title())}</span>'
    )


def github_pr_metrics_section(view: dict | None) -> str:
    if not view:
        return '<p class="empty">GitHub PR metrics are unavailable for this team.</p>'

    def person(person_ref: dict | None) -> str:
        if not person_ref:
            return "Unknown"
        login = f"@{person_ref['login']}"
        return (
            f"{person_ref['display_name']} ({login})"
            if person_ref.get("display_name")
            else login
        )

    def involved(item: dict) -> str:
        author = f"Author: {person(item.get('author'))}"
        reviewers = ", ".join(person(reviewer) for reviewer in item.get("reviewers", []))
        return esc(author + " · Reviewers: " + (reviewers or "Unknown"))

    def metric_group(metric: str, heading: str, explanation: str) -> str:
        field = f"{metric}_hours"
        rag_field = f"{metric}_rag"
        rows = "".join(
            f'''<tbody{rag_anchor_attr(item.get(rag_field))} class="pr-metric-contribution rag-instance" data-pr-merged="{date_attr(item.get('merged_at'))}" data-metric-hours="{item[field]}"><tr>
            <td>{rag_badge(item.get(rag_field))} {link(item.get("url"), f'{item["repository"]}#{item["number"]}')}</td>
            <td class="num"><strong>{item[field]:.2f} hours</strong></td>
            <td>{esc(display_date(item.get("created_at")))} → {esc(display_date(item.get("first_reviewed_at")))} → <time datetime="{esc(item.get('merged_at'))}">{esc(display_date(item.get("merged_at")))}</time></td>
            <td>{involved(item)}</td><td>{esc(item.get("title") or "No title supplied.")}</td></tr></tbody>'''
            for item in view.get("contributions", [])
        ) or '<tbody><tr><td colspan="5" class="empty">No qualifying merged pull requests.</td></tr></tbody>'
        return f'''<section class="pr-metric-group" data-pr-metric="{metric}"><h3>{esc(heading)}</h3>
        <div class="cycle-summary"><div class="metric"><strong class="pr-metric-average">—</strong>average hours</div><div class="metric"><strong class="pr-metric-sample">0</strong>qualifying pull requests</div></div>
        <p class="table-note">{esc(explanation)} Top five contributors for the selected merge-date range.</p>
        <div class="table-wrap"><table class="pr-metric-table"><thead><tr><th>Pull request</th><th class="num">Time</th><th>Created → first review → merged</th><th>Involved people</th><th>Title</th></tr></thead>{rows}</table></div></section>'''

    notes = "".join(f'<li>{esc(note)}</li>' for note in view.get("data_quality_notes", []))
    repository_count = len(view.get("repositories", []))
    authors = ", ".join(f"@{login}" for login in view.get("author_logins", [])) or "No active GitHub identities configured"
    return (
        f'<p class="table-note">Repository scope: all {repository_count} configured repositories. Author scope: {esc(authors)}. The global time filter selects pull requests by merge date.</p>'
        + metric_group(
            "pickup",
            "Average pickup time",
            "Elapsed time from PR creation to the first qualifying review.",
        )
        + metric_group(
            "review",
            "Average review time",
            "Elapsed time from the first qualifying review to merge.",
        )
        + f'<details><summary>Metric definition and data notes</summary><ul>{notes}</ul></details>'
    )


def rag_status_index(team_slug: str, build_cycle: dict | None, github_pr: dict | None) -> str:
    assessments: list[tuple[dict, str]] = []
    for group in (build_cycle or {}).get("groups", []):
        for item in group.get("contributions", []):
            if item.get("rag"):
                assessments.append((item["rag"], item["jira_key"]))
    for item in (github_pr or {}).get("contributions", []):
        record = f'{item["repository"]}#{item["number"]}'
        for field in ("pickup_rag", "review_rag"):
            if item.get(field):
                assessments.append((item[field], record))
    if not assessments:
        return '<p class="muted">No RAG rules are configured for this team.</p>'
    order = {"red": 0, "amber": 1, "green": 2}
    assessments.sort(key=lambda pair: (order[pair[0]["level"]], pair[1]))
    counts = {
        level: sum(item["level"] == level for item, _record in assessments)
        for level in ("red", "amber", "green")
    }
    links = "".join(
        f'<li>{rag_badge(item)} <a href="#/teams/{esc(team_slug)}?focus={esc(item["anchor_id"])}">'
        f'{esc(record)} — {esc(item["rule_label"])}</a></li>'
        for item, record in assessments
    )
    return (
        '<div class="rag-summary" aria-label="Red amber green status summary">'
        f'<p>{counts["red"]} red · {counts["amber"]} amber · {counts["green"]} green</p>'
        f'<ul>{links}</ul></div>'
    )


def team_summaries(
    name: str,
    row: dict,
    feature_rows: list[tuple[dict, dict]],
    memberships: dict[str, set[str]],
    snapshot_at: datetime,
    metrics: dict,
) -> tuple[str, list[str], list[str]]:
    in_progress = len(row.get("in_progress", []))
    ready = len(row.get("ready_for_build", []))
    all_findings = [
        finding
        for feature, item in feature_rows
        for finding in feature_findings(feature, item, memberships, snapshot_at)
    ]
    counts = {finding: all_findings.count(finding) for finding in sorted(set(all_findings))}
    delivery_items = sum(
        bool(feature.get("github_delivery", {}).get("records"))
        for feature, _item in feature_rows
    )
    themes = []
    for feature, item in feature_rows[:4]:
        description = concise_text(feature.get("hierarchy", {}).get("description_text"))
        themes.append(f"{item['jira_key']} focuses on {description or item['title']}")
    content = (
        "; ".join(themes)
        if themes else f"No In Progress or Ready for Build IBR work is present for {name}."
    )
    if delivery_items:
        content += f" Linked GitHub delivery evidence is present for {delivery_items} of these items."
    notable_order = [
        "Stale parent 14+d", "No Target Date",
        "No parent assignee", "Parent assignee outside team", "Stale child 14+d",
        "Child without team", "Different child team", "Child assignee outside parent team",
        "Multiple child teams",
    ]
    notable = [f"{counts[label]} × {label}" for label in notable_order if counts.get(label)]
    in_progress_keys = [item["jira_key"] for item in row.get("in_progress", [])]
    ready_keys = [item["jira_key"] for item in row.get("ready_for_build", [])]
    health_items = [
        f"Pipeline: {in_progress} IBR item{'s' if in_progress != 1 else ''} In Progress"
        + citation(in_progress_keys)
        + f"; {ready} Ready for Build"
        + citation(ready_keys)
        + "."
    ]
    dashboard_flags = row.get("flags", [])
    if dashboard_flags:
        areas = sorted({flag.get("area", "other").replace("_", " ") for flag in dashboard_flags})
        flag_keys = [
            evidence.get("jira_key")
            for flag in dashboard_flags
            for evidence in flag.get("evidence", [])
            if evidence.get("jira_key")
        ]
        health_items.append(
            f"Active signals: {len(dashboard_flags)} deterministic flag{'s' if len(dashboard_flags) != 1 else ''}, covering "
            + ", ".join(areas) + citation(flag_keys) + "."
        )
    else:
        health_items.append("Active signals: no deterministic team flags are active.")
    if notable:
        hygiene_keys = [
            item["jira_key"]
            for feature, item in feature_rows
            if feature_findings(feature, item, memberships, snapshot_at)
        ]
        health_items.append("Jira health: " + "; ".join(notable) + citation(hygiene_keys) + ".")
    else:
        health_items.append("Jira health: no currently evaluable hygiene or freshness findings.")
    evaluated = [
        metric for metric in metrics.get("metrics", [])
        if metric.get("health") in {"watch", "concern", "critical", "healthy"}
    ]
    slow = [m for m in evaluated if m.get("health") in {"watch", "concern", "critical"}]
    if slow:
        metric_keys = [
            contribution.get("jira_key")
            for metric in slow
            for contribution in metric.get("contributions", [])
            if contribution.get("jira_key")
        ]
        health_items.append(
            "Flow health: thresholded metrics needing attention — "
            + ", ".join(f"{m['definition']['label']} ({m['health']})" for m in slow)
            + citation(metric_keys) + "."
        )
    elif not evaluated:
        health_items.append("Flow health: pickup, PR, and cycle-time slowness is not assessed because no versioned thresholds are configured or evaluable.")
    else:
        health_items.append("Flow health: all currently evaluable thresholded metrics are healthy.")
    cross_teams = sorted({
        node.get("team_name")
        for feature, _item in feature_rows
        for node in flatten(feature["hierarchy"])[1:]
        if node.get("team_name") and node.get("team_name") != name
    })
    cross_team_keys = [
        item["jira_key"]
        for feature, item in feature_rows
        if any(
            node.get("team_name") and node.get("team_name") != name
            for node in flatten(feature["hierarchy"])[1:]
        ) or feature.get("summary", {}).get("blocking_links", 0)
    ]
    blocking_links = sum(feature.get("summary", {}).get("blocking_links", 0) for feature, _item in feature_rows)
    if cross_teams:
        health_items.append(
            "Cross-team dependencies: active IBR hierarchies include child work assigned to "
            + ", ".join(cross_teams)
            + (f"; {blocking_links} blocking Jira relationship{'s are' if blocking_links != 1 else ' is'} also present" if blocking_links else "")
            + citation(cross_team_keys) + "."
        )
    elif blocking_links:
        health_items.append(
            f"Cross-team dependencies: {blocking_links} blocking Jira relationship{'s are' if blocking_links != 1 else ' is'} present, but no other child-work team is identified in the pinned hierarchy"
            + citation(cross_team_keys) + "."
        )
    else:
        health_items.append("Cross-team dependencies: none identified in the pinned IBR hierarchy or blocking Jira relationships.")
    return content, health_items, notable


def individual_summaries(
    person: dict,
    snapshot_at: datetime,
    team_ibr_keys: set[str],
) -> tuple[str, list[str], list[str]]:
    active = [work for work in person.get("jira_work", []) if work.get("active")]
    github = person.get("github_contributions", [])
    record_counts = {
        kind: sum(row.get("record_type") == kind for row in github)
        for kind in ("pull_request", "commit", "review")
    }
    focus = []
    for work in active[:4]:
        description = concise_text(work.get("direct_issue_description_text"))
        focus.append(f"{work['direct_issue_key']} focuses on {description or work['direct_issue_title']}")
    recent_prs = [row for row in github if row.get("record_type") == "pull_request"][:3]
    content = "; ".join(focus) if focus else "No active Jira assignment was found in the configured source scope."
    if recent_prs:
        content += " Recent linked pull-request themes include " + "; ".join(row.get("title") or row.get("record_id") for row in recent_prs) + "."
    notable = []
    blockers = person.get("blockers_and_dependencies", [])
    if blockers:
        notable.append(f"{len(blockers)} blocker or dependency relationship{'s' if len(blockers) != 1 else ''} in the pinned evidence.")
    rolled_up = sum(work.get("rolled_up_to_feature") for work in active)
    if rolled_up:
        notable.append(f"{rolled_up} active assignment{'s roll' if rolled_up != 1 else ' rolls'} up through child work to an IBR parent.")
    statuses = sorted({work.get("direct_issue_status") for work in active if work.get("direct_issue_status")})
    if statuses:
        notable.append("Active Jira statuses represented: " + ", ".join(statuses) + ".")
    missing_target = sum(not (work.get("target_date") or work.get("target_date_value")) for work in active)
    if missing_target:
        notable.append(f"{missing_target} active Jira item{'s have' if missing_target != 1 else ' has'} no Target Date.")
    approaching = 0
    overdue = 0
    for work in active:
        target = work.get("target_date")
        if not target:
            continue
        days = (datetime.fromisoformat(target).date() - snapshot_at.date()).days
        overdue += days < 0
        approaching += 0 <= days <= 14
    if overdue:
        notable.append(f"{overdue} active item{'s are' if overdue != 1 else ' is'} past Target Date.")
    if approaching:
        notable.append(f"{approaching} active item{'s have' if approaching != 1 else ' has'} a Target Date within 14 days.")
    if not active:
        notable.append("No active Jira assignment was found in the configured source scope.")
    signals = [s for s in person.get("signals", []) if s.get("signal_type") != "verified_fact"]
    active_keys = [work["direct_issue_key"] for work in active]
    health_items = [
        f"Current work: {len(active)} active Jira assignment{'s' if len(active) != 1 else ''}"
        + citation(active_keys) + ".",
        (
            f"Delivery evidence: {record_counts['pull_request']} pull request{'s' if record_counts['pull_request'] != 1 else ''}, "
            f"{record_counts['commit']} commit{'s' if record_counts['commit'] != 1 else ''}, and "
            f"{record_counts['review']} review{'s' if record_counts['review'] != 1 else ''} in the pinned source window."
        ),
    ]
    team_ibr_work = [work for work in active if work.get("feature_key") in team_ibr_keys]
    team_ibr_children = [work for work in team_ibr_work if work.get("rolled_up_to_feature")]
    team_ibr_parents = [work for work in team_ibr_work if not work.get("rolled_up_to_feature")]
    other_ibr_work = [
        work for work in active
        if work.get("feature_key") and work.get("feature_key") not in team_ibr_keys
    ]
    outside_ibr = [work for work in active if not work.get("feature_key")]
    ibr_parts = []
    if team_ibr_parents:
        ibr_parts.append(
            f"{len(team_ibr_parents)} direct parent assignment{'s' if len(team_ibr_parents) != 1 else ''}"
            + citation([work["direct_issue_key"] for work in team_ibr_parents])
        )
    if team_ibr_children:
        child_keys = [work["direct_issue_key"] for work in team_ibr_children]
        parent_keys = [work["feature_key"] for work in team_ibr_children if work.get("feature_key")]
        ibr_parts.append(
            f"{len(team_ibr_children)} child assignment{'s' if len(team_ibr_children) != 1 else ''}"
            + citation(child_keys)
            + " rolling up to current team IBR parent"
            + ("s" if len(set(parent_keys)) != 1 else "")
            + citation(parent_keys)
        )
    if ibr_parts:
        health_items.append("Relationship to team IBR work: " + "; ".join(ibr_parts) + ".")
    else:
        health_items.append("Relationship to team IBR work: no active assignment is directly on or below an In Progress/Ready for Build IBR parent for the person's report team(s).")
    differences = []
    if other_ibr_work:
        differences.append(
            f"{len(other_ibr_work)} assignment{'s' if len(other_ibr_work) != 1 else ''} linked to other or non-current IBR parent work"
            + citation([work["direct_issue_key"] for work in other_ibr_work])
            + " under " + ", ".join(dict.fromkeys(work["feature_key"] for work in other_ibr_work))
        )
    if outside_ibr:
        differences.append(
            f"{len(outside_ibr)} assignment{'s' if len(outside_ibr) != 1 else ''} outside any captured IBR parent hierarchy"
            + citation([work["direct_issue_key"] for work in outside_ibr])
        )
    health_items.append(
        "Work beyond current team IBR scope: "
        + ("; ".join(differences) + "." if differences else "none identified in active Jira assignments.")
    )
    if signals:
        signal_keys = [
            evidence.get("jira_key")
            for signal in signals
            for evidence in signal.get("evidence", [])
            if evidence.get("jira_key")
        ]
        health_items.append("Active signals: " + "; ".join(s["explanation"] for s in signals) + citation(signal_keys) + ".")
    else:
        health_items.append("Active signals: no deterministic individual process signal is active.")
    hygiene_parts = []
    if missing_target:
        missing_keys = [
            work["direct_issue_key"] for work in active
            if not (work.get("target_date") or work.get("target_date_value"))
        ]
        hygiene_parts.append(f"{missing_target} without a Target Date" + citation(missing_keys))
    if overdue:
        overdue_keys = [
            work["direct_issue_key"] for work in active if work.get("target_date")
            and (datetime.fromisoformat(work["target_date"]).date() - snapshot_at.date()).days < 0
        ]
        hygiene_parts.append(f"{overdue} past Target Date" + citation(overdue_keys))
    health_items.append("Jira health: " + ("; ".join(hygiene_parts) + "." if hygiene_parts else "no Target Date exceptions identified in active work."))
    if blockers:
        dependency_keys = [
            key for blocker in blockers
            for key in (blocker.get("source_issue_key"), blocker.get("target_issue_key")) if key
        ]
        health_items.append(
            f"Dependencies: {len(blockers)} blocker or dependency relationship{'s' if len(blockers) != 1 else ''} in the pinned evidence"
            + citation(dependency_keys) + "."
        )
    else:
        health_items.append("Dependencies: none identified in the pinned Jira relationships.")
    health_items.append("Flow health: pickup, PR, and cycle-time slowness is only reported when a versioned thresholded signal exists; none is available here.")
    return content, health_items, notable


def person_card(person: dict) -> str:
    memberships = ", ".join(m["team_name"] for m in person.get("memberships", []) if m.get("current_at_snapshot")) or "No Team"
    jira = "".join(
        f'<li data-date="{date_attr(w.get("direct_issue_updated_at"))}">{link(w.get("direct_issue_url"), w.get("direct_issue_key"))} — {esc(w.get("direct_issue_title"))} '
        f'<span class="muted">{esc(w.get("direct_issue_status"))} · {esc(w.get("relationship_type"))}</span>'
        + (f'<br><span class="rollup">IBR parent: {link(w.get("feature_url"), w.get("feature_key"))} — {esc(w.get("feature_title"))}</span>' if w.get("feature_key") else '')
        + '</li>' for w in person.get("jira_work", []) if w.get("active")
    ) or '<li class="muted">No active Jira assignment in configured scope.</li>'
    gh = "".join(
        f'<li data-date="{date_attr(row.get("occurred_at"))}">{link(row.get("url"), row.get("record_type"))}: {esc(row.get("title"))} '
        f'<span class="muted">{esc(row.get("relationship_type"))}'
        f'{" · " + esc(display_date(row.get("occurred_at"))) if row.get("occurred_at") else ""}</span></li>'
        for row in person.get("github_contributions", [])[:20]
    ) or '<li class="muted">No linked GitHub evidence in the snapshot.</li>'
    return f'''<article class="person searchable"><h3>{esc(person.get('preferred_name') or person['display_name'])}</h3>
      <p class="muted">{esc(memberships)} · Jira/GitHub identity: {esc(person.get('identity_mapping_state'))}</p>
      <details open><summary>Current Jira work</summary><ul>{jira}</ul></details>
      <details><summary>Recent linked GitHub work</summary><ul>{gh}</ul></details></article>'''


CSS = '''body{margin:0;background:#f4f6fb;color:#172033;font:15px system-ui,sans-serif;line-height:1.5}a{color:#315bd6}nav{position:sticky;top:0;z-index:2;background:#172033;padding:12px 4vw;display:flex;gap:14px;align-items:center;flex-wrap:wrap}nav a{color:white;text-decoration:none}nav input{margin-left:auto;padding:9px;border-radius:7px;border:0;min-width:240px}main{max-width:1200px;margin:auto;padding:35px 24px}h1{font-size:34px}.app-view{display:none}.app-view.active{display:block}.hero,.card,.person,.panel,.team-card{background:white;border:1px solid #dfe4ef;border-radius:14px;padding:20px;margin:14px 0;box-shadow:0 3px 12px #26334d10}.grid,.people-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(250px,1fr));gap:14px}.team-grid{display:block}.team-grid .team-card{margin:14px 0}.member-list{border-top:1px solid #e7eaf1;margin-top:16px;padding-top:12px}.member-list strong{display:block;margin-bottom:5px}.metric{background:#edf1fb;padding:16px;border-radius:10px}.metric strong{display:block;font-size:27px}.card header,.team-card header{display:flex;justify-content:space-between;gap:15px}.eyebrow{color:#65708a;text-transform:uppercase;font-size:12px;letter-spacing:.08em}.muted{color:#667087}.badge,.person-link{display:inline-block;padding:5px 8px;border-radius:20px;font-size:12px;margin:2px}.person-link{background:#edf1fb;text-decoration:none}.bad{background:#ffe3e1;color:#982d28}.warn{background:#fff0c4;color:#745100}.good{background:#dff6e8;color:#17633b}.tree,.tree ul{list-style:none;padding-left:20px}.issue-row{padding:6px;border-left:2px solid #dce3f5}.depth{display:none}.rollup{color:#425a9b}.health-list{padding-left:22px}.health-list li{margin:7px 0}.activity-tables{margin-top:24px}.activity-tables h3{margin:22px 0 2px}.table-note{color:#667087;font-size:13px;margin:0 0 8px}.table-wrap{overflow-x:auto;border:1px solid #dfe4ef;border-radius:10px}table{width:100%;border-collapse:collapse;background:white;font-size:13px}th,td{padding:9px 10px;text-align:left;vertical-align:top;border-bottom:1px solid #e7eaf1}th{background:#edf1fb;color:#39445c;white-space:nowrap}tbody tr:last-child td{border-bottom:0}td time{white-space:nowrap}.filters button,.cta{padding:8px 12px;margin:3px;border:1px solid #bcc6dc;background:white;border-radius:20px;text-decoration:none}.hidden{display:none!important}.date-hidden{display:none!important}.seg.ibr{background:#12946a}.seg.nonibr{background:#d9822b}.seg.nolink{background:#8b93a7}.badge.column{background:#edf1fb;color:#39445c}.split-row{display:flex;align-items:center;gap:12px;margin:8px 0;font-size:13px;color:#52596b;flex-wrap:wrap}.split-row .meter{max-width:260px}.status-hidden{display:none!important}.status-filter-group{margin-left:auto;display:inline-flex;align-items:center;gap:4px;flex-wrap:wrap}.status-chip{font:inherit;font-size:12px;padding:4px 10px;border-radius:20px;border:1px solid #bcc6dc;background:white;cursor:pointer;white-space:nowrap}.status-chip:hover{background:#eef2fc}.status-chip.active{background:#315bd6;border-color:#315bd6;color:white}.text-hidden{display:none!important}.table-filter{font:inherit;font-size:13px;padding:7px 10px;margin:6px 0 4px;border-radius:7px;border:1px solid #bcc6dc;background:white;min-width:220px}.table-filter:focus{outline:2px solid #315bd6;outline-offset:1px}th.sortable{cursor:pointer;user-select:none}th.sortable:hover{background:#dde6f8}th.sortable[data-dir="asc"]::after{content:" ▲";font-size:10px}th.sortable[data-dir="desc"]::after{content:" ▼";font-size:10px}details.accordion>summary .glance{font-size:13px;color:#52596b;font-weight:400}details.accordion{margin-top:0}details.accordion>summary{padding:6px 0}details.accordion>summary h2{display:inline;margin:0;font-size:24px}details.accordion[open]>summary{margin-bottom:10px}section:has(>details.accordion){background:white;border:1px solid #dfe4ef;border-radius:14px;padding:14px 20px;margin:14px 0;box-shadow:0 3px 12px #26334d10}nav .range{display:inline-flex;align-items:center;gap:8px;color:#c6cede;font-size:12px;flex-wrap:wrap}nav .range label{display:inline-flex;align-items:center;gap:5px}nav .range input[type=date]{margin-left:0;min-width:0;padding:6px;border-radius:7px;border:0;font-size:12px}#date-note{color:#ffd98a}details{margin-top:10px}summary{cursor:pointer;font-weight:600}.gap{border-left:5px solid #d89516}.attention{border-left:5px solid #cf7b20}.empty{color:#667087;font-style:italic}.breadcrumbs{margin-bottom:14px}.notable li{margin:5px 0}.completion-row{margin:6px 0 2px;display:flex;flex-wrap:wrap;gap:8px}.month-chip{display:flex;align-items:center;gap:7px;background:#f7f9fe;border:1px solid #e4e9f5;border-radius:9px;padding:6px 10px;white-space:nowrap}.month-chip strong{background:#edf1fb;padding:3px 7px;border-radius:6px}.month-chip .meter{width:84px;min-width:84px}
/* Child-state palette: validated for CVD separation and >=3:1 on the white
   table surface (done #12946a vs in-progress #2a78d6). "Not started" is the
   recessive track tone, never a warning color. */
.meter{display:inline-flex;gap:2px;height:10px;width:100%;min-width:110px;max-width:220px;border-radius:5px;overflow:hidden;background:#fff;vertical-align:middle}.meter .seg{display:block;min-width:2px}
.seg.done,.swatch.done,.badge.state.done{background:#12946a}.seg.in_progress,.swatch.in_progress,.badge.state.in_progress{background:#2a78d6}.seg.not_started,.swatch.not_started,.badge.state.not_started{background:#cbd2e0}.seg.unknown,.swatch.unknown,.badge.state.unknown{background:#8b93a7}
.legend{display:flex;flex-wrap:wrap;gap:12px;margin:8px 0 4px;font-size:12px;color:#52596b}.key{display:inline-flex;align-items:center;gap:5px}.swatch{width:11px;height:11px;border-radius:3px;display:inline-block}
.badge.state{color:#fff;font-weight:600}.badge.state.not_started{color:#39445c}.badge.column{background:#edf1fb;color:#39445c}
td.num,th.num{text-align:right;font-variant-numeric:tabular-nums;white-space:nowrap}.meter-cell{min-width:130px;width:180px}
.breakdown .month-row th{background:#e8eefb;color:#26355c;font-size:13px;padding:8px 10px}.breakdown .parent-row td{border-top:1px solid #e7eaf1}.child-row td{background:#fafbfe;font-size:12px;padding:6px 10px}.child-row .child-key{padding-left:26px}.collapsed{display:none}
/* Keys, badges and controls never wrap; the title column absorbs the slack. */
.breakdown td:first-child,.breakdown th:first-child{white-space:nowrap}.breakdown td:nth-child(2){width:99%}.badge,.toggle{white-space:nowrap}
.toggle{font:inherit;font-size:12px;color:#315bd6;background:#eef2fc;border:1px solid #d3ddf4;border-radius:20px;padding:4px 10px;cursor:pointer}.toggle:hover{background:#e2e9f9}.toggle[aria-expanded="true"]{background:#dbe4f8}.cycle-summary{display:grid;grid-template-columns:repeat(auto-fit,minmax(180px,1fr));gap:10px;margin:10px 0}.cycle-summary .metric{padding:12px}.cycle-summary .metric strong{font-size:22px}.cycle-group{margin:22px 0}.cycle-child td{background:#fafbfe;font-size:12px}.top-status{background:#e7ddff;color:#57359a}.cycle-excluded{display:none}.cycle-excluded.focused{display:table-row-group}.rag-badge{display:inline-flex;align-items:center;gap:4px;padding:3px 7px;border-radius:5px;font-size:12px;font-weight:700;white-space:nowrap}.rag-red{background:#ffe3e1;color:#982d28;border:1px solid #e9aaa5}.rag-amber{background:#fff0c4;color:#745100;border:1px solid #e5c66c}.rag-green{background:#dff6e8;color:#17633b;border:1px solid #96d5af}.rag-summary ul{columns:2;padding-left:22px}.rag-summary li{break-inside:avoid;margin:6px 0}.rag-instance:target,.rag-instance.focused{outline:3px solid #315bd6;outline-offset:-2px;scroll-margin-top:90px}@media(max-width:650px){nav input{width:100%;margin:0}.card header,.team-card header{display:block}.team-card{padding:15px}th,td{min-width:110px}th:last-child,td:last-child{min-width:220px}.rag-summary ul{columns:1}}'''
JS = '''const q=document.querySelector('#q');function activeView(){return document.querySelector('.app-view.active')||document.querySelector('[data-route="/"]')}function apply(){const v=q.value.toLowerCase(),view=activeView();if(v)view.querySelectorAll('details.accordion').forEach(d=>d.open=true);view.querySelectorAll('.searchable').forEach(e=>e.classList.toggle('hidden',!e.innerText.toLowerCase().includes(v)))}if(q)q.addEventListener('input',apply);
function route(){const raw=location.hash.startsWith('#/')?location.hash.slice(1):'/';const [path,query='']=raw.split('?');const params=new URLSearchParams(query);const views=[...document.querySelectorAll('.app-view')];const view=views.find(v=>v.dataset.route===path)||views.find(v=>v.dataset.route==='/');views.forEach(v=>v.classList.toggle('active',v===view));document.title=(view.dataset.title?view.dataset.title+' — ':'')+'Weekly Engineering Status';if(q){q.value='';apply()}document.querySelectorAll('.rag-instance.focused').forEach(e=>e.classList.remove('focused'));const focus=params.get('focus');if(focus){const target=document.getElementById(focus);if(target){const details=target.closest('details');if(details)details.open=true;target.classList.add('focused');requestAnimationFrame(()=>target.scrollIntoView({block:'center'}));return}}window.scrollTo(0,0)}
window.addEventListener('hashchange',route);route();
document.querySelectorAll('.status-filter-group').forEach(group=>{const scope=group.closest('details')||document;
group.querySelectorAll('.status-chip').forEach(chip=>chip.addEventListener('click',()=>{
chip.classList.toggle('active');chip.setAttribute('aria-pressed',chip.classList.contains('active'));
const active=new Set([...group.querySelectorAll('.status-chip.active')].map(c=>c.dataset.status));
scope.querySelectorAll('[data-issue-status]').forEach(r=>r.classList.toggle('status-hidden',active.size>0&&!active.has(r.dataset.issueStatus)));}));});
document.querySelectorAll('.table-wrap').forEach(w=>{const t=w.querySelector('table');if(!t||!t.tHead||!t.tBodies.length||t.classList.contains('cycle-table')||t.classList.contains('pr-metric-table')||t.querySelector('.child-row,.month-row'))return;
const inp=document.createElement('input');inp.type='search';inp.placeholder='Filter rows…';inp.className='table-filter';inp.setAttribute('aria-label','Filter table rows');
w.parentNode.insertBefore(inp,w);
inp.addEventListener('input',()=>{const v=inp.value.toLowerCase();
[...t.tBodies[0].rows].forEach(r=>{if(r.cells.length<=1)return;r.classList.toggle('text-hidden',Boolean(v)&&!r.innerText.toLowerCase().includes(v));});});});
document.querySelectorAll('table').forEach(t=>{if(!t.tHead||!t.tBodies.length||t.classList.contains('cycle-table')||t.classList.contains('pr-metric-table')||t.querySelector('.child-row,.month-row'))return;const body=t.tBodies[0];
[...t.tHead.rows[0].cells].forEach((th,i)=>{th.classList.add('sortable');th.addEventListener('click',()=>{
const dir=th.dataset.dir==='asc'?'desc':'asc';
[...t.tHead.rows[0].cells].forEach(c=>{c.removeAttribute('data-dir');c.removeAttribute('aria-sort')});
th.dataset.dir=dir;th.setAttribute('aria-sort',dir==='asc'?'ascending':'descending');
const rows=[...body.rows],data=rows.filter(r=>r.cells.length>1),rest=rows.filter(r=>r.cells.length<=1);
const val=r=>{const c=r.cells[i];if(!c)return'';const tm=c.querySelector('time[datetime]');if(tm)return tm.getAttribute('datetime')||'';return c.innerText.trim()};
data.sort((a,b)=>{const x=val(a),y=val(b);const nx=parseFloat(x.replace(/[%,]/g,'')),ny=parseFloat(y.replace(/[%,]/g,''));
const c=(x!==''&&y!==''&&!isNaN(nx)&&!isNaN(ny))?nx-ny:x.localeCompare(y,undefined,{numeric:true,sensitivity:'base'});
return dir==='asc'?c:-c});
data.concat(rest).forEach(r=>body.appendChild(r));});});});document.querySelectorAll('[data-filter]').forEach(b=>b.onclick=()=>{const f=b.dataset.filter,scope=b.closest('.app-view')||document;scope.querySelectorAll('[data-status]').forEach(e=>e.classList.toggle('hidden',f!=='all'&&e.dataset.status!==f));});
const dateFrom=document.querySelector('#date-from'),dateTo=document.querySelector('#date-to'),dateClear=document.querySelector('#date-clear'),dateNote=document.querySelector('#date-note');
function updateCycleMetrics(){const from=dateFrom?.value||'',to=dateTo?.value||'';document.querySelectorAll('.cycle-group').forEach(group=>{const all=[...group.querySelectorAll('.cycle-contribution')];const included=all.filter(row=>{const d=row.dataset.cycleEnded;return(!from||d>=from)&&(!to||d<=to)});const ranked=[...included].sort((a,b)=>Number(b.dataset.cycleDays)-Number(a.dataset.cycleDays));all.forEach(row=>row.classList.toggle('cycle-excluded',!ranked.slice(0,5).includes(row)));const total=included.reduce((sum,row)=>sum+Number(row.dataset.cycleDays),0);const statuses={};included.forEach(row=>{const values=JSON.parse(row.dataset.statusDurations||'{}');Object.entries(values).forEach(([status,days])=>statuses[status]=(statuses[status]||0)+Number(days));});const top=Object.entries(statuses).sort((a,b)=>b[1]-a[1]||a[0].localeCompare(b[0]))[0];group.querySelector('.cycle-average').textContent=included.length?(total/included.length).toFixed(2):'—';group.querySelector('.cycle-sample').textContent=String(included.length);group.querySelector('.cycle-top-status').textContent=top?top[0]+' ('+top[1].toFixed(2)+'d)':'—';});}
function updatePrMetrics(){const from=dateFrom?.value||'',to=dateTo?.value||'';document.querySelectorAll('.pr-metric-group').forEach(group=>{const all=[...group.querySelectorAll('.pr-metric-contribution')];const included=all.filter(row=>{const d=row.dataset.prMerged;return(!from||d>=from)&&(!to||d<=to)});const ranked=[...included].sort((a,b)=>Number(b.dataset.metricHours)-Number(a.dataset.metricHours));all.forEach(row=>row.classList.toggle('cycle-excluded',!ranked.slice(0,5).includes(row)));const total=included.reduce((sum,row)=>sum+Number(row.dataset.metricHours),0);group.querySelector('.pr-metric-average').textContent=included.length?(total/included.length).toFixed(2):'—';group.querySelector('.pr-metric-sample').textContent=String(included.length);});}
function dateApply(){if(!dateFrom)return;const from=dateFrom.value,to=dateTo.value,active=Boolean(from||to);let undated=0;document.querySelectorAll('[data-date]').forEach(e=>{const d=e.dataset.date;let hide=false;if(active){if(!d){hide=true;undated++;}else{hide=(from&&d<from)||(to&&d>to);}}e.classList.toggle('date-hidden',hide);});updateCycleMetrics();updatePrMetrics();if(dateNote)dateNote.textContent=active?(undated?undated+' undated records hidden by the time filter':'Time filter active'):'';}
if(dateFrom){dateFrom.addEventListener('change',dateApply);dateTo.addEventListener('change',dateApply);dateClear.addEventListener('click',()=>{dateFrom.value='';dateTo.value='';dateApply();});updateCycleMetrics();updatePrMetrics();}
function setChildren(key,open){document.querySelectorAll('.child-row[data-parent="'+CSS.escape(key)+'"]').forEach(r=>r.classList.toggle('collapsed',!open));document.querySelectorAll('.toggle[data-children="'+CSS.escape(key)+'"]').forEach(b=>b.setAttribute('aria-expanded',String(open)));}
document.querySelectorAll('.toggle[data-children]').forEach(b=>b.onclick=()=>setChildren(b.dataset.children,b.getAttribute('aria-expanded')!=='true'));
document.querySelectorAll('.expand-all').forEach(b=>b.onclick=()=>{const open=b.dataset.open!=='true';b.dataset.open=String(open);b.textContent=open?'Collapse all children':'Expand all children';b.closest('section').querySelectorAll('.toggle[data-children]').forEach(t=>setChildren(t.dataset.children,open));});'''


def page(title: str, body: str) -> str:
    nav_links = '<a href="#/">Engineering Status</a><a href="#/">Overview</a>'
    range_controls = (
        '<span class="range"><label>From <input type="date" id="date-from"></label>'
        '<label>To <input type="date" id="date-to"></label>'
        '<button id="date-clear" class="toggle" type="button">Clear</button>'
        '<span id="date-note" class="muted"></span></span>'
    )
    return f'''<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>{esc(title)}</title><style>{CSS}</style></head><body><nav>{nav_links}<input id="q" placeholder="Search issues, engineers, teams…">{range_controls}</nav><main id="top">{body}</main><script>{JS}</script></body></html>'''


def write_page(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--snapshot", required=True)
    parser.add_argument("--data-dir", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--source-config", type=Path, required=True)
    parser.add_argument("--teams-config", type=Path, required=True)
    parser.add_argument(
        "--summaries-json", type=Path,
        help="Agent-authored natural-language summaries grounded in the pinned evidence.",
    )
    args = parser.parse_args()
    authored_summaries = (
        json.loads(args.summaries_json.read_text(encoding="utf-8"))
        if args.summaries_json else {}
    )
    summary_snapshot = authored_summaries.get("snapshot_id")
    if summary_snapshot and summary_snapshot != args.snapshot:
        parser.error(
            f"summary snapshot {summary_snapshot} does not match requested snapshot {args.snapshot}"
        )
    dashboard = run_json([
        "dashboard", "get", args.snapshot,
        "--source-config", str(args.source_config),
        "--teams-config", str(args.teams_config),
    ], args.data_dir)
    team_rows = {row["team_name"]: row for row in dashboard["teams"]}
    report_teams = [name for name in team_rows if name.casefold() != "no team"]
    people_directory = run_json(["people", "list", "--snapshot", args.snapshot], args.data_dir)
    directory_rows = people_directory.get("people", [])
    team_members = {
        name: [row["display_name"] for row in directory_rows if name in row.get("current_teams", [])]
        for name in report_teams
    }
    selected_rows = [team_rows[name] for name in report_teams]
    team_metrics = {
        name: run_json([
            "metrics", "get", "--snapshot", args.snapshot, "--team", name,
            "--teams-config", str(args.teams_config),
        ], args.data_dir)
        for name in report_teams
    }
    team_build_cycle = {
        name: run_json([
            "metrics", "build-cycle", "--snapshot", args.snapshot, "--team", name,
            "--teams-config", str(args.teams_config),
        ], args.data_dir)
        for name in report_teams
    }
    team_github_pr_metrics = {
        name: run_json([
            "metrics", "github-pr", "--snapshot", args.snapshot, "--team", name,
            "--source-config", str(args.source_config),
            "--teams-config", str(args.teams_config),
        ], args.data_dir)
        for name in report_teams
    }
    team_details = {
        name: run_json([
            "team", "get", name, "--snapshot", args.snapshot,
            "--source-config", str(args.source_config),
            "--teams-config", str(args.teams_config),
        ], args.data_dir)
        for name in report_teams
    }
    team_work = {}
    for name in report_teams:
        if name not in team_rows:
            continue
        try:
            team_work[name] = run_json(
                ["team", "work", name, "--snapshot", args.snapshot,
                 "--teams-config", str(args.teams_config)], args.data_dir
            )
        except subprocess.CalledProcessError:
            team_work[name] = None
    items = {
        item["jira_key"]: item
        for row in selected_rows
        for item in [*row.get("in_progress", []), *row.get("ready_for_build", [])]
    }
    features = {
        key: run_json(["feature", "get", key, "--snapshot", args.snapshot], args.data_dir)
        for key in sorted(items)
    }
    people = []
    gaps = [
        "Literal GitHub Issues and their descriptions are not ingested; GitHub evidence is limited to pull requests, commits, and reviews."
    ]
    for row in directory_rows:
        name = row["person_id"]
        try:
            people.append(run_json([
                "individual", "get", name, "--snapshot", args.snapshot,
                "--teams-config", str(args.teams_config),
                "--source-config", str(args.source_config),
            ], args.data_dir))
        except subprocess.CalledProcessError:
            gaps.append(f"Individual context unavailable for {name}: command failed.")
    memberships = {
        person["jira_account_id"]: {
            team for team, names in team_members.items() if person["display_name"] in names
        }
        for person in people if person.get("jira_account_id")
    }
    snapshot_at = datetime.fromisoformat(dashboard["snapshot_created_at"])
    # Rolled-up completion needs the child tree of every dated board item, which
    # is a wider set than the active IBR parents already fetched above.
    dated_keys = sorted({
        item["jira_key"]
        for detail in team_details.values()
        for state in detail.get("workflow", [])
        for item in state.get("items", [])
        if item.get("jira_key")
        and TARGET_MONTH_RE.match(item.get("target_date_value") or "")
    })
    hierarchies = {}
    for key in dated_keys:
        if key in features:
            hierarchies[key] = features[key]["hierarchy"]
            continue
        try:
            hierarchies[key] = run_json(
                ["feature", "get", key, "--snapshot", args.snapshot], args.data_dir
            )["hierarchy"]
        except subprocess.CalledProcessError:
            gaps.append(
                f"Child hierarchy unavailable for dated item {key}: "
                "it contributes completion from its board column alone."
            )
    team_completion = {
        name: completion_by_target_date(detail, hierarchies)
        for name, detail in team_details.items()
    }
    gaps.extend(
        f'Malformed Target Date on {key} ("{raw}") in {name}: excluded from completion.'
        for name in report_teams
        for key, raw in team_completion.get(name, {}).get("malformed", [])
    )
    gaps.extend(
        f"{key} sits in the Done column in {name} with {open_children} unfinished "
        "child issue" + ("s" if open_children != 1 else "")
        + ": the parent keeps full completion credit."
        for name in report_teams
        for key, open_children in team_completion.get(name, {}).get(
            "open_children_under_done", []
        )
    )
    current_month = snapshot_at.strftime("%Y-%m")
    month_done = sum(
        completion["months"].get(current_month, {}).get("done", 0)
        for completion in team_completion.values()
    )
    month_total = sum(
        completion["months"].get(current_month, {}).get("total", 0)
        for completion in team_completion.values()
    )
    month_credit = sum(
        completion["months"].get(current_month, {}).get("credit", 0.0)
        for completion in team_completion.values()
    )

    team_cards = []
    team_detail_sections = []
    for name in report_teams:
        row = team_rows.get(name)
        if row is None:
            continue
        completion = team_completion[name]
        team_items = [*row.get("in_progress", []), *row.get("ready_for_build", [])]
        feature_rows = [(features[item["jira_key"]], item) for item in team_items]
        _content_summary, health_items, notable = team_summaries(
            name, row, feature_rows, memberships, snapshot_at, team_metrics[name]
        )
        issue_urls = {
            node["jira_key"]: node["url"]
            for feature, _item in feature_rows
            for node in flatten(feature["hierarchy"])
            if node.get("jira_key") and node.get("url")
        }
        issue_urls.update({
            evidence["jira_key"]: evidence["url"]
            for flag in row.get("flags", [])
            for evidence in flag.get("evidence", [])
            if evidence.get("jira_key") and evidence.get("url")
        })
        health_html = '<ul class="health-list">' + ''.join(
            f'<li>{linked_jira_text(item, issue_urls)}</li>' for item in health_items
        ) + '</ul>'
        team_people = [person for person in people if person["display_name"] in team_members[name]]
        activity_html = team_activity_tables(team_details[name], team_people)
        notable_html = "".join(f'<li>{esc(item)}</li>' for item in notable) or '<li>No notable hygiene findings.</li>'
        team_href = f"#/teams/{slug(name)}"
        overview_members = "".join(
            internal_link(
                f"#/people/{slug(person['display_name'])}",
                person.get("preferred_name") or person["display_name"],
                css_class="person-link",
            )
            for person in team_people
        ) or '<span class="muted">No configured engineers</span>'
        team_cards.append(f'''<article class="team-card searchable"><header><div><span class="eyebrow">Team</span><h2>{internal_link(team_href, name)}</h2></div></header><div class="member-list"><strong>People</strong>{overview_members}</div></article>''')
        detail_cards = "".join(
            feature_card(feature, item, memberships, snapshot_at)
            for feature, item in feature_rows
        ) or '<p class="empty">No In Progress or Ready for Build IBR items.</p>'
        member_links_detail = "".join(
            internal_link(
                f"#/people/{slug(person['display_name'])}",
                person.get("preferred_name") or person["display_name"], css_class="person-link",
            ) for person in team_people
        ) or '<span class="muted">No configured engineers</span>'
        team_body = (
            f'<div class="hero"><span class="eyebrow">Team detail</span><h2>{esc(name)}</h2>'
            f'<h2>Team Health</h2>{health_html}'
            f'<h3>RAG status</h3>{rag_status_index(slug(name), team_build_cycle.get(name), team_github_pr_metrics.get(name))}'
            f'<div>{member_links_detail}</div></div>'
            + accordion(
                "Build Cycle Time",
                build_cycle_time_section(team_build_cycle.get(name)),
            )
            + accordion(
                "GitHub PR Metrics",
                github_pr_metrics_section(team_github_pr_metrics.get(name)),
            )
            + accordion(
                "IBR vs non-IBR work",
                team_work_section(team_work.get(name)),
                summary_extra=team_work_summary_extra(team_work.get(name)),
            )
            + accordion("Completion by Target Date", completion_table_html(completion))
            + accordion("Recent activity", activity_html)
            + accordion(
                "Health evidence",
                f'<ul class="notable">{notable_html}</ul>',
                classes="panel attention",
            )
            + accordion(
                "IBR work and child hierarchy",
                '<div class="filters"><button data-filter="all">All</button>'
                '<button data-filter="In Progress">In Progress</button>'
                '<button data-filter="Ready for build">Ready for Build</button></div>'
                + detail_cards,
                section_id=f"team-{slug(name)}-ibr",
            )
        )
        team_detail_sections.append(
            f'<section class="app-view" data-route="/teams/{slug(name)}" '
            f'data-title="{esc(name)}"><div class="breadcrumbs">'
            f'{internal_link("#/", "Overview")} / {esc(name)}</div>{team_body}</section>'
        )

    no_team_people = [
        person
        for person in people
        if not any(person["display_name"] in names for names in team_members.values())
    ]
    no_team_links = "".join(
        internal_link(
            f"#/people/{slug(person['display_name'])}",
            person.get("preferred_name") or person["display_name"],
            css_class="person-link",
        )
        for person in no_team_people
    ) or '<span class="muted">No configured engineers</span>'
    team_cards.append(
        '<article class="team-card searchable"><header><div><span class="eyebrow">Team</span>'
        '<h2>No Team</h2></div></header><div class="member-list"><strong>People</strong>'
        f'{no_team_links}</div></article>'
    )

    individual_detail_sections = []
    for person in people:
        display = person.get("preferred_name") or person["display_name"]
        person_team_names = {
            team for team, names in team_members.items() if person["display_name"] in names
        }
        person_team_ibr_keys = {
            item["jira_key"]
            for team in person_team_names
            for item in [
                *team_rows.get(team, {}).get("in_progress", []),
                *team_rows.get(team, {}).get("ready_for_build", []),
            ]
        }
        content_summary, health_items, notable = individual_summaries(
            person, snapshot_at, person_team_ibr_keys
        )
        content_summary = authored_summaries.get("individuals", {}).get(
            person["display_name"], {}
        ).get("content_summary", content_summary)
        issue_urls = {
            work["direct_issue_key"]: work["direct_issue_url"]
            for work in person.get("jira_work", [])
            if work.get("direct_issue_key") and work.get("direct_issue_url")
        }
        issue_urls.update({
            work["feature_key"]: work["feature_url"]
            for work in person.get("jira_work", [])
            if work.get("feature_key") and work.get("feature_url")
        })
        issue_urls.update({
            blocker["target_issue_key"]: blocker["target_url"]
            for blocker in person.get("blockers_and_dependencies", [])
            if blocker.get("target_issue_key") and blocker.get("target_url")
        })
        content_html = linked_jira_text(content_summary, issue_urls)
        health_html = '<ul class="health-list">' + ''.join(
            f'<li>{linked_jira_text(item, issue_urls)}</li>' for item in health_items
        ) + '</ul>'
        activity_html = individual_activity_tables(person)
        current_teams = [team for team, names in team_members.items() if person["display_name"] in names]
        team_links = "".join(
            internal_link(f"#/teams/{slug(name)}", name, css_class="person-link")
            for name in current_teams
        ) or '<span class="muted">No report team</span>'
        notable_html = "".join(f'<li>{esc(item)}</li>' for item in notable) or '<li>No notable items in the pinned evidence.</li>'
        person_body = (
            f'<div class="hero"><span class="eyebrow">Individual work context</span><h2>{esc(display)}</h2>'
            f'<h2>What the work is about</h2><p>{content_html}</p>'
            f'<h2>Individual Health</h2>{health_html}<div>{team_links}</div></div>'
            + accordion("Recent activity", activity_html)
            + accordion(
                "Health evidence",
                f'<ul class="notable">{notable_html}</ul>',
                classes="panel attention",
            )
            + accordion("Evidence", person_card(person))
        )
        individual_detail_sections.append(
            f'<section class="app-view" data-route="/people/{slug(person["display_name"])}" '
            f'data-title="{esc(display)}"><div class="breadcrumbs">'
            f'{internal_link("#/", "Overview")} / {esc(display)}</div>{person_body}</section>'
        )

    cards = "".join(feature_card(features[key], items[key], memberships, snapshot_at) for key in sorted(features)) or '<p class="empty">No active IBR items.</p>'
    gaps_html = f"<ul>{''.join(f'<li>{esc(gap)}</li>' for gap in gaps)}</ul>" if gaps else '<p>All configured Jira hygiene fields are evaluable for this snapshot.</p>'
    overview = (
        '''<section class="app-view active" data-route="/" data-title="Overview">'''
        f'<section><h1>Teams and people</h1><p class="muted">Choose a team or person to open their page.</p><div class="team-grid">{"".join(team_cards)}</div></section>'
        + accordion("Evidence coverage", gaps_html, classes="panel gap")
        + accordion(
            "All IBR Board work",
            '<div class="filters"><button data-filter="all">All</button>'
            '<button data-filter="In Progress">In Progress</button>'
            '<button data-filter="Ready for build">Ready for Build</button></div>' + cards,
        )
        + "</section>"
    )
    body = overview + "".join(team_detail_sections) + "".join(individual_detail_sections)
    write_page(args.output, page("Weekly Engineering Status", body))
    print(json.dumps({"output": str(args.output.resolve()), "snapshot_id": dashboard["snapshot_id"], "features": len(features), "people": len(people), "team_sections": len(team_detail_sections), "individual_sections": len(people), "gaps": gaps,
        "completion": {
            "current_month": current_month,
            "current_month_done": month_done,
            "current_month_total": month_total,
            "current_month_rolled_up_pct": completion_pct(month_credit, month_total),
            "teams": {
                name: {
                    "months": {
                        month: {**bucket, "credit": round(bucket["credit"], 4)}
                        for month, bucket in completion["months"].items()
                    },
                    "dated": completion["dated"],
                    "undated": completion["undated"],
                    "malformed": completion["malformed"],
                    "missing_hierarchy": completion["missing_hierarchy"],
                    "open_children_under_done": completion["open_children_under_done"],
                }
                for name, completion in team_completion.items()
            },
        }}, indent=2))


if __name__ == "__main__":
    main()
