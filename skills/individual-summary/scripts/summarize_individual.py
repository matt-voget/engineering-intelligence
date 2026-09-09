#!/usr/bin/env python3
"""Extract snapshot-pinned evidence for an individual summary from an HTML report."""

from __future__ import annotations

import argparse
import json
from collections import Counter
from datetime import date
from html.parser import HTMLParser
from pathlib import Path
from statistics import mean

import yaml

GITHUB_FIELDS = (
    "record_type", "repository", "identifier", "title", "url", "state", "draft",
    "author", "created", "updated", "merged", "authored", "committed", "head",
    "base", "commit_count", "review_count", "reviewers", "pull_requests",
    "jira_keys", "jira_urls", "author_teams", "reviewer_teams", "first_reviewed",
    "pickup_hours", "review_hours", "first_commit", "coding_hours", "author_person",
    "coding_basis", "coding_jira_key",
)
DONE = {"done", "closed", "resolved"}


class ReportParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.github_capture = False
        self.github_parts: list[str] = []
        self.issue: dict | None = None
        self.cell: dict | None = None
        self.issues: list[dict] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = dict(attrs)
        if tag == "script" and values.get("id") == "github-finder-data":
            self.github_capture = True
        if tag == "tr" and "data-issue-team" in values:
            self.issue = {"attributes": values, "cells": []}
        elif tag == "td" and self.issue is not None:
            self.cell = {"text": [], "links": [], "attributes": values}
        elif tag == "a" and self.cell is not None and values.get("href"):
            self.cell["links"].append(values["href"])

    def handle_endtag(self, tag: str) -> None:
        if tag == "script" and self.github_capture:
            self.github_capture = False
        elif tag == "td" and self.cell is not None and self.issue is not None:
            self.cell["text"] = " ".join("".join(self.cell["text"]).split())
            self.issue["cells"].append(self.cell)
            self.cell = None
        elif tag == "tr" and self.issue is not None:
            if len(self.issue["cells"]) >= 15:
                self.issues.append(self.issue)
            self.issue = None

    def handle_data(self, data: str) -> None:
        if self.github_capture:
            self.github_parts.append(data)
        if self.cell is not None:
            self.cell["text"].append(data)


def arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("report", type=Path)
    parser.add_argument("person", help="Person ID, name, preferred name, or GitHub login")
    parser.add_argument("--from-date", required=True, type=date.fromisoformat)
    parser.add_argument("--to-date", required=True, type=date.fromisoformat)
    parser.add_argument(
        "--teams-config", type=Path,
        default=Path.home() / ".config/engineering-intelligence/teams.yaml",
    )
    return parser.parse_args()


def resolve_person(path: Path, identifier: str) -> dict:
    config = yaml.safe_load(path.read_text(encoding="utf-8"))
    people: dict[str, dict] = {}
    memberships: dict[str, set[str]] = {}
    for team in config.get("teams", []):
        for member in team.get("members", []):
            people.setdefault(member["id"], member)
            memberships.setdefault(member["id"], set()).add(team["name"])
    needle = identifier.casefold()
    matches = [person for person in people.values() if needle in {
        str(person.get(field) or "").casefold()
        for field in ("id", "name", "preferred_name", "github_login", "jira_account_id")
    }]
    if not matches:
        matches = [
            person for person in people.values()
            if any(
                str(person.get(field) or "").casefold().startswith(f"{needle} ")
                for field in ("name", "preferred_name")
            )
        ]
    if len(matches) != 1:
        raise ValueError(f"Person not found or ambiguous: {identifier}")
    person = dict(matches[0])
    person["teams"] = sorted(memberships[person["id"]], key=str.casefold)
    return person


def in_range(value: str | None, start: date, end: date) -> bool:
    return bool(value and start.isoformat() <= value[:10] <= end.isoformat())


def issue_rows(raw: list[dict], names: set[str], start: date, end: date) -> tuple[list[dict], list[dict]]:
    rows = []
    active = []
    for item in raw:
        attrs, cells = item["attributes"], item["cells"]
        if cells[4]["text"].casefold() not in names:
            continue
        status = attrs.get("data-issue-status") or cells[3]["text"].removesuffix(" (done)")
        row = {
            "key": cells[0]["text"], "url": next(iter(cells[0]["links"]), None),
            "team": attrs.get("data-issue-team"), "type": attrs.get("data-issue-type"),
            "status": status, "updated": attrs.get("data-date"),
            "status_started": attrs.get("data-status-started") or None,
            "status_age_days": (
                float(attrs["data-status-age-days"])
                if attrs.get("data-status-age-days") else None
            ),
            "done": attrs.get("data-cycle-ended") or None,
            "cycle_days": number(cells[6]), "in_progress_days": number(cells[7]),
            "in_review_days": number(cells[8]), "in_test_days": number(cells[9]),
            "skipped_phases": None if cells[10]["text"] == "—" else cells[10]["text"],
            "prs": cells[13]["links"], "title": cells[14]["text"],
        }
        if status.casefold() not in DONE:
            active.append(row)
        if in_range(row["updated"], start, end) or in_range(row["done"], start, end):
            rows.append(row)
    return rows, active


def number(cell: dict) -> float | None:
    value = cell["attributes"].get("data-value")
    return float(value) if value not in (None, "") else None


def pr_row(row: dict) -> dict:
    return {
        "repository": row["repository"], "identifier": row["identifier"],
        "title": row["title"], "url": row["url"], "state": row["state"],
        "created": row["created"], "updated": row["updated"],
        "first_reviewed": row["first_reviewed"], "merged": row["merged"],
        "jira_keys": row["jira_keys"],
        "coding_hours": row["coding_hours"], "pickup_hours": row["pickup_hours"],
        "review_hours": row["review_hours"], "coding_basis": row["coding_basis"],
    }


def metric(values: list[float | None]) -> dict:
    present = [value for value in values if value is not None]
    return {
        "average": round(mean(present), 2) if present else None,
        "sample_size": len(present), "missing": len(values) - len(present),
        "maximum": round(max(present), 2) if present else None,
    }


def main() -> None:
    args = arguments()
    if args.from_date > args.to_date:
        raise ValueError("--from-date must be on or before --to-date")
    parser = ReportParser()
    parser.feed(args.report.read_text(encoding="utf-8"))
    if not parser.github_parts:
        raise ValueError("report has no github-finder-data payload")
    person = resolve_person(args.teams_config, args.person)
    names = {
        str(person.get(field) or "").casefold()
        for field in ("name", "preferred_name") if person.get(field)
    }
    issues, active = issue_rows(parser.issues, names, args.from_date, args.to_date)
    compact = json.loads("".join(parser.github_parts))
    github = [dict(zip(GITHUB_FIELDS, row, strict=False)) for row in compact]
    login = (person.get("github_login") or "").casefold()
    pulls = [row for row in github if row["record_type"] == "pull_request"]
    authored = [row for row in pulls if login and (row["author"] or "").casefold() == login
                and in_range(row["merged"] or row["updated"], args.from_date, args.to_date)]
    reviewed = [row for row in pulls if login and login in {x.casefold() for x in row["reviewers"]}
                and in_range(row["merged"] or row["updated"], args.from_date, args.to_date)]
    authored_merged = [row for row in authored if row["merged"]]
    reviewed_merged = [row for row in reviewed if row["merged"]]
    completed = [row for row in issues if in_range(row["done"], args.from_date, args.to_date)]
    type_counts = Counter(row["type"] for row in issues)
    output = {
        "report": str(args.report.resolve()),
        "date_range": {"from": args.from_date.isoformat(), "to": args.to_date.isoformat()},
        "person": {key: person.get(key) for key in ("id", "name", "preferred_name", "github_login", "teams")},
        "jira": {
            "work_in_range": issues, "active_at_snapshot": active,
            "counts": {"work_in_range": len(issues), "active_at_snapshot": len(active),
                       "completed_in_range": len(completed), "by_type": type_counts},
            "cycle_days": metric([row["cycle_days"] for row in completed]),
            "phase_days": {
                "in_progress": metric([row["in_progress_days"] for row in completed]),
                "in_review": metric([row["in_review_days"] for row in completed]),
                "in_test": metric([row["in_test_days"] for row in completed]),
            },
        },
        "github": {
            "authored": [pr_row(row) for row in authored],
            "reviewed": [pr_row(row) for row in reviewed],
            "counts": {"authored_in_range": len(authored), "authored_merged": len(authored_merged),
                       "reviewed_in_range": len(reviewed), "reviewed_merged": len(reviewed_merged)},
            "coding_hours_as_author": metric([row["coding_hours"] for row in authored_merged]),
            "pickup_hours_on_reviewed_prs": metric([row["pickup_hours"] for row in reviewed_merged]),
            "review_hours_on_reviewed_prs": metric([row["review_hours"] for row in reviewed_merged]),
        },
        "metric_notes": [
            "All durations are calendar time.",
            "Coding time is reported only for merged authored PRs.",
            "Reviewer metrics describe the overall PR intervals for PRs this person reviewed; they do not attribute the interval causally to that reviewer.",
            "Jira assignment reflects the assignee at the report snapshot, not assignment history.",
        ],
    }
    print(json.dumps(output, indent=2))


if __name__ == "__main__":
    main()
