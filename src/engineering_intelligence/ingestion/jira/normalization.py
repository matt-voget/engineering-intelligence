"""Normalize Jira Cloud payloads into stable Phase 1 records."""

import hashlib
import json
import time
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any


def parse_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    return datetime.fromisoformat(value)


def parse_date(value: str | None) -> date | None:
    if not value:
        return None
    normalized = value.strip()
    for format_string in ("%Y-%m-%d", "%d %B %Y", "%d %b %Y"):
        try:
            parsed = time.strptime(normalized, format_string)
            return date(parsed.tm_year, parsed.tm_mon, parsed.tm_mday)
        except ValueError:
            continue
    return None


def _person(value: Any) -> tuple[str | None, str | None]:
    if not isinstance(value, dict):
        return None, None
    return value.get("accountId"), value.get("displayName")


def _team(value: Any) -> tuple[str | None, str | None]:
    if isinstance(value, dict):
        return (
            str(value.get("id")) if value.get("id") is not None else None,
            value.get("title") or value.get("name") or value.get("value"),
        )
    if isinstance(value, str):
        return value, value
    return None, None


def _option_values(value: Any) -> list[str]:
    """Preserve Jira option labels from single- or multi-select custom fields."""
    items = value if isinstance(value, list) else [value] if value else []
    results = []
    for item in items:
        if isinstance(item, dict):
            label = item.get("value") or item.get("name") or item.get("title")
        else:
            label = item
        if label is not None and str(label).strip():
            results.append(str(label).strip())
    return results


def adf_to_text(value: Any) -> str | None:
    """Flatten a Jira string or Atlassian Document Format value to readable text."""
    if isinstance(value, str):
        text = value.strip()
        return text or None
    if not isinstance(value, dict):
        return None

    def inline(node: Any) -> str:
        if not isinstance(node, dict):
            return ""
        node_type = node.get("type")
        if node_type == "text":
            return str(node.get("text") or "")
        if node_type == "hardBreak":
            return "\n"
        return "".join(inline(child) for child in node.get("content") or [])

    blocks: list[str] = []

    def visit(node: Any) -> None:
        if not isinstance(node, dict):
            return
        node_type = node.get("type")
        if node_type in {"bulletList", "orderedList"}:
            for child in node.get("content") or []:
                rendered = " ".join(inline(child).split())
                if rendered:
                    blocks.append(f"- {rendered}")
            return
        if node_type in {"paragraph", "heading", "blockquote", "codeBlock"}:
            rendered = " ".join(inline(node).split())
            if rendered:
                blocks.append(rendered)
            return
        for child in node.get("content") or []:
            visit(child)

    visit(value)
    text = "\n".join(blocks).strip()
    return text or None


@dataclass(frozen=True)
class NormalizedIssue:
    issue: dict[str, Any]
    version: dict[str, Any]
    relationships: list[dict[str, str | None]]
    version_hash: str


def normalize_issue(
    payload: dict[str, Any],
    *,
    base_url: str,
    observed_at: datetime,
    team_field_id: str | None = None,
    target_date_field_id: str | None = None,
    gravitee_customers_field_id: str | None = None,
    rank_field_id: str = "customfield_10019",
) -> NormalizedIssue:
    fields = payload.get("fields") or {}
    issue_id = str(payload["id"])
    issue_key = payload["key"]
    project = fields.get("project") or {}
    status = fields.get("status") or {}
    status_category = status.get("statusCategory") or {}
    issue_type = fields.get("issuetype") or {}
    assignee_id, assignee_name = _person(fields.get("assignee"))
    team_id, team_name = _team(fields.get(team_field_id)) if team_field_id else (None, None)
    parent = fields.get("parent") or {}
    parent_id = str(parent["id"]) if parent.get("id") is not None else None
    target_date_value = (
        str(fields.get(target_date_field_id)).strip()
        if target_date_field_id and fields.get(target_date_field_id)
        else None
    )

    version = {
        "issue_id": issue_id,
        "source_created_at": parse_datetime(fields.get("created")),
        "source_updated_at": parse_datetime(fields.get("updated")),
        "summary": fields.get("summary") or "",
        "description_text": adf_to_text(fields.get("description")),
        "issue_type_id": str(issue_type["id"]) if issue_type.get("id") is not None else None,
        "issue_type_name": issue_type.get("name"),
        "status_id": str(status["id"]) if status.get("id") is not None else None,
        "status_name": status.get("name"),
        "status_category": status_category.get("key") or status_category.get("name"),
        "assignee_account_id": assignee_id,
        "assignee_display_name": assignee_name,
        "team_id": team_id,
        "team_name": team_name,
        "parent_issue_id": parent_id,
        "rank_value": fields.get(rank_field_id),
        "target_date": parse_date(target_date_value),
        "target_date_value": target_date_value,
        "gravitee_customers": _option_values(fields.get(gravitee_customers_field_id))
        if gravitee_customers_field_id
        else [],
        "resolved_at": parse_datetime(fields.get("resolutiondate")),
        "labels": fields.get("labels") or [],
        "components": [
            {"id": str(item.get("id")), "name": item.get("name")}
            for item in fields.get("components") or []
        ],
        "fix_versions": [
            {
                "id": str(item.get("id")),
                "name": item.get("name"),
                "released": item.get("released"),
            }
            for item in fields.get("fixVersions") or []
        ],
    }
    hashable = {
        key: value.isoformat() if isinstance(value, (date, datetime)) else value
        for key, value in version.items()
    }
    version_hash = hashlib.sha256(
        json.dumps(hashable, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    issue = {
        "id": issue_id,
        "issue_key": issue_key,
        "self_url": payload.get("self") or f"{base_url.rstrip('/')}/rest/api/3/issue/{issue_key}",
        "web_url": f"{base_url.rstrip('/')}/browse/{issue_key}",
        "project_key": project.get("key") or issue_key.split("-", 1)[0],
        "created_at": parse_datetime(fields.get("created")),
        "last_source_updated_at": parse_datetime(fields.get("updated")),
        "first_seen_at": observed_at,
        "last_seen_at": observed_at,
        "current_version_hash": version_hash,
        "is_deleted": False,
    }
    relationships = _relationships(
        issue_id,
        parent_id,
        parent.get("key"),
        fields,
        base_url,
    )
    return NormalizedIssue(issue, version, relationships, version_hash)


def _relationships(
    issue_id: str,
    parent_id: str | None,
    parent_key: str | None,
    fields: dict[str, Any],
    base_url: str,
) -> list[dict[str, str | None]]:
    results: list[dict[str, str | None]] = []
    if parent_id:
        results.append(
            {
                "source_issue_id": issue_id,
                "target_issue_id": parent_id,
                "target_issue_key": parent_key,
                "target_summary": (parent.get("fields") or {}).get("summary")
                if (parent := fields.get("parent") or {})
                else None,
                "target_status": (
                    ((parent.get("fields") or {}).get("status") or {}).get("name")
                    if parent
                    else None
                ),
                "target_url": f"{base_url.rstrip('/')}/browse/{parent_key}"
                if parent_key
                else None,
                "relationship_type": "parent",
                "source_description": "Jira parent field",
            }
        )
    for subtask in fields.get("subtasks") or []:
        if subtask.get("id") is not None:
            results.append(
                {
                    "source_issue_id": issue_id,
                    "target_issue_id": str(subtask["id"]),
                    "target_issue_key": subtask.get("key"),
                    "target_summary": (subtask.get("fields") or {}).get("summary"),
                    "target_status": (
                        ((subtask.get("fields") or {}).get("status") or {}).get("name")
                    ),
                    "target_url": (
                        f"{base_url.rstrip('/')}/browse/{subtask['key']}"
                        if subtask.get("key")
                        else None
                    ),
                    "relationship_type": "child",
                    "source_description": "Jira subtask field",
                }
            )
    for link in fields.get("issuelinks") or []:
        link_type = link.get("type") or {}
        if link.get("outwardIssue"):
            target = link["outwardIssue"]
            description = link_type.get("outward") or link_type.get("name")
        elif link.get("inwardIssue"):
            target = link["inwardIssue"]
            description = link_type.get("inward") or link_type.get("name")
        else:
            continue
        if target.get("id") is not None:
            results.append(
                {
                    "source_issue_id": issue_id,
                    "target_issue_id": str(target["id"]),
                    "target_issue_key": target.get("key"),
                    "target_summary": (target.get("fields") or {}).get("summary"),
                    "target_status": (
                        ((target.get("fields") or {}).get("status") or {}).get("name")
                    ),
                    "target_url": (
                        f"{base_url.rstrip('/')}/browse/{target['key']}"
                        if target.get("key")
                        else None
                    ),
                    "relationship_type": "issue_link",
                    "source_description": description,
                }
            )
    return results
