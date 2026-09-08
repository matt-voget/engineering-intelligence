#!/usr/bin/env python3
"""Analyze the GitHub Finder payload embedded in an Engineering Intelligence report."""

from __future__ import annotations

import argparse
import json
from collections import Counter, defaultdict
from datetime import UTC, datetime, timedelta
from html.parser import HTMLParser
from pathlib import Path
from statistics import mean

FIELDS = (
    "record_type", "repository", "identifier", "title", "url", "state", "draft",
    "author", "created", "updated", "merged", "authored", "committed", "head",
    "base", "commit_count", "review_count", "reviewers", "pull_requests",
    "jira_keys", "jira_urls", "author_teams", "reviewer_teams", "first_reviewed",
    "pickup_hours", "review_hours", "first_commit", "coding_hours", "author_person",
    "coding_basis", "coding_jira_key",
)
METRICS = {"coding": "coding_hours", "pickup": "pickup_hours", "review": "review_hours"}


class PayloadParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.capture = False
        self.parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = dict(attrs)
        if tag == "script" and values.get("id") == "github-finder-data":
            self.capture = True

    def handle_endtag(self, tag: str) -> None:
        if tag == "script" and self.capture:
            self.capture = False

    def handle_data(self, data: str) -> None:
        if self.capture:
            self.parts.append(data)


def arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("report", type=Path)
    parser.add_argument("--record-type", action="append", choices=("pull_request", "commit"))
    for name in ("repository", "state", "author", "team", "reviewer"):
        parser.add_argument(f"--{name}", action="append")
    parser.add_argument("--team-mapping", action="append", choices=("mapped", "unmapped"))
    parser.add_argument("--jira-link", action="append", choices=("linked", "unlinked"))
    parser.add_argument(
        "--timing-data", action="append",
        choices=("complete", "missing_pickup", "missing_review", "missing_both"),
    )
    parser.add_argument("--from-date")
    parser.add_argument("--to-date")
    parser.add_argument("--search")
    parser.add_argument("--exclude-thresholds", action="store_true")
    parser.add_argument("--coding-threshold", type=float, default=9.0)
    parser.add_argument("--pickup-threshold", type=float, default=176.0)
    parser.add_argument("--review-threshold", type=float, default=80.0)
    return parser.parse_args()


def load_rows(report: Path) -> list[dict]:
    parser = PayloadParser()
    parser.feed(report.read_text(encoding="utf-8"))
    if not parser.parts:
        raise ValueError("report has no github-finder-data payload")
    compact = json.loads("".join(parser.parts))
    if compact and len(compact[0]) < len(FIELDS):
        raise ValueError("report uses an older GitHub Finder payload; regenerate it")
    return [dict(zip(FIELDS, row, strict=False)) for row in compact]


def values(selected: list[str] | None) -> set[str]:
    return set(selected or [])


def record_date(row: dict) -> str:
    return row["merged"] or row["updated"] or row["committed"] or row["authored"] or row["created"] or ""


def timing_matches(row: dict, selected: set[str]) -> bool:
    if not selected:
        return True
    if row["record_type"] != "pull_request":
        return False
    pickup, review = row["pickup_hours"], row["review_hours"]
    return (
        ("complete" in selected and pickup is not None and review is not None)
        or ("missing_pickup" in selected and pickup is None)
        or ("missing_review" in selected and review is None)
        or ("missing_both" in selected and pickup is None and review is None)
    )


def filter_rows(rows: list[dict], args: argparse.Namespace) -> list[dict]:
    selected = {name: values(getattr(args, name)) for name in (
        "record_type", "repository", "state", "author", "team", "reviewer",
    )}
    mappings, jira, timing = values(args.team_mapping), values(args.jira_link), values(args.timing_data)
    query = (args.search or "").casefold()
    result = []
    for row in rows:
        if any(selected[name] and (
            row[name] not in selected[name] if name not in ("team", "reviewer")
            else not selected[name].intersection(row["author_teams" if name == "team" else "reviewers"])
        ) for name in selected):
            continue
        mapped = bool(row["author_teams"])
        if mappings and not (("mapped" in mappings and mapped) or ("unmapped" in mappings and not mapped)):
            continue
        linked = bool(row["jira_keys"])
        if jira and not (("linked" in jira and linked) or ("unlinked" in jira and not linked)):
            continue
        if not timing_matches(row, timing):
            continue
        day = record_date(row)[:10]
        if (args.from_date and day < args.from_date) or (args.to_date and day > args.to_date):
            continue
        haystack = " ".join(str(value) for value in (
            row["repository"], row["identifier"], row["title"], row["author"],
            row["author_person"], *row["author_teams"], row["state"], *row["reviewers"],
            *row["pull_requests"], *row["jira_keys"],
        )).casefold()
        if query and query not in haystack:
            continue
        result.append(row)
    return result


def monday(value: str) -> str:
    day = datetime.fromisoformat(value).astimezone(UTC)
    return (day - timedelta(days=day.weekday())).date().isoformat()


def summarize(rows: list[dict], args: argparse.Namespace) -> dict:
    thresholds = {
        "coding": args.coding_threshold,
        "pickup": args.pickup_threshold,
        "review": args.review_threshold,
    }
    charts = {}
    outliers = []
    for metric, field in METRICS.items():
        qualifying = [row for row in rows if row["record_type"] == "pull_request" and row["merged"] and row[field] is not None]
        flagged = [row for row in qualifying if row[field] > thresholds[metric]]
        plotted = [row for row in qualifying if row not in flagged] if args.exclude_thresholds else qualifying
        buckets: dict[str, list[float]] = defaultdict(list)
        for row in plotted:
            buckets[monday(row["merged"])].append(row[field])
        charts[metric] = [
            {"week": week, "average_hours": round(mean(samples), 2), "sample_size": len(samples)}
            for week, samples in sorted(buckets.items())
        ]
        outliers.extend({
            "metric": metric, "hours": round(row[field], 2), "repository": row["repository"],
            "identifier": row["identifier"], "title": row["title"], "url": row["url"],
            "coding_basis": row["coding_basis"], "coding_jira_key": row["coding_jira_key"],
        } for row in flagged)
    pulls = [row for row in rows if row["record_type"] == "pull_request"]
    merged_pulls = [row for row in pulls if row["merged"]]
    return {
        "report": str(args.report.resolve()),
        "filters": {key: value for key, value in vars(args).items() if key != "report"},
        "record_count": len(rows),
        "pull_request_count": len(pulls),
        "merged_pull_request_count": len(merged_pulls),
        "missing": {metric: sum(row[field] is None for row in pulls) for metric, field in METRICS.items()},
        "coding_basis": Counter(row["coding_basis"] for row in pulls),
        "charts": charts,
        "outliers": sorted(outliers, key=lambda row: (-row["hours"], row["metric"], row["url"])),
    }


def main() -> None:
    args = arguments()
    print(json.dumps(summarize(filter_rows(load_rows(args.report), args), args), indent=2))


if __name__ == "__main__":
    main()
