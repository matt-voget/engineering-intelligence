"""Target-Date completion math for the weekly status report generator."""

import importlib.util
import json
import subprocess
import threading
import time
from datetime import UTC, datetime
from pathlib import Path

import pytest

SCRIPT = (
    Path(__file__).parents[1]
    / "skills/team-status-prep/scripts/generate_weekly_status.py"
)


def load_generator():
    spec = importlib.util.spec_from_file_location("generate_weekly_status", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def generator():
    return load_generator()


def team(*columns: tuple[str, list[tuple[str, str | None]]]) -> dict:
    return {
        "workflow": [
            {
                "name": name,
                "items": [
                    {"jira_key": key, "target_date_value": value} for key, value in items
                ],
            }
            for name, items in columns
        ]
    }


def hierarchy(key: str, *children: str) -> dict:
    """Build a parent node whose children carry the given status categories."""
    return {
        "jira_key": key,
        "status_category": "indeterminate",
        "children": [
            {
                "jira_key": f"{key}-C{index}",
                "title": f"Child {index} of {key}",
                "status": category.title(),
                "url": f"https://jira.example/{key}-C{index}",
                "status_category": category,
                "children": [],
            }
            for index, category in enumerate(children, start=1)
        ],
    }


def counts(**overrides: int) -> dict:
    base = {"total": 0, "done": 0, "in_progress": 0, "not_started": 0, "unknown": 0}
    return {**base, **overrides}


def test_run_json_materializes_then_reuses_snapshot_cache(generator, tmp_path, monkeypatch):
    source = tmp_path / "sources.yaml"
    teams = tmp_path / "teams.yaml"
    source.write_text("github: {}", encoding="utf-8")
    teams.write_text("teams: []", encoding="utf-8")
    generator.configure_query_cache(tmp_path / "cache", source, teams)
    calls = []

    def fake_run(command, **kwargs):
        calls.append(command)
        return subprocess.CompletedProcess(command, 0, '{"value": 42}', "")

    monkeypatch.setattr(generator.subprocess, "run", fake_run)
    assert generator.run_json(["example", "get"], tmp_path) == {"value": 42}
    assert generator.run_json(["example", "get"], tmp_path) == {"value": 42}
    assert len(calls) == 1
    assert generator._query_cache_stats == {"hits": 1, "misses": 1}

    summary = generator.write_materialization_summary(tmp_path / "cache")
    persisted = json.loads(
        (tmp_path / "cache" / "materialization.json").read_text(encoding="utf-8")
    )
    assert summary == persisted
    assert summary["cache"] == {"hits": 1, "misses": 1}
    assert summary["views"]["example get"]["count"] == 2
    assert summary["views"]["example get"]["hits"] == 1
    assert summary["views"]["example get"]["misses"] == 1


def test_run_json_fails_loudly_on_corrupt_cache(generator, tmp_path, monkeypatch):
    source = tmp_path / "sources.yaml"
    teams = tmp_path / "teams.yaml"
    source.write_text("github: {}", encoding="utf-8")
    teams.write_text("teams: []", encoding="utf-8")
    generator.configure_query_cache(tmp_path / "cache", source, teams)
    cache_path = generator._query_cache_path(["example", "get"])
    cache_path.parent.mkdir(parents=True)
    cache_path.write_text("not json", encoding="utf-8")
    monkeypatch.setattr(
        generator.subprocess, "run",
        lambda *args, **kwargs: pytest.fail("corrupt cache must not be silently recomputed"),
    )
    with pytest.raises(RuntimeError, match="Invalid report cache entry"):
        generator.run_json(["example", "get"], tmp_path)


def test_run_json_many_is_bounded_and_preserves_request_order(
    generator, tmp_path, monkeypatch
):
    active = 0
    max_active = 0
    lock = threading.Lock()

    def fake_run_json(args, _data_dir):
        nonlocal active, max_active
        with lock:
            active += 1
            max_active = max(max_active, active)
        time.sleep(0.02)
        with lock:
            active -= 1
        return {"value": args[0]}

    monkeypatch.setattr(generator, "run_json", fake_run_json)
    monkeypatch.setattr(generator, "REPORT_QUERY_WORKERS", 2)

    result = generator.run_json_many([[str(index)] for index in range(5)], tmp_path)

    assert result == [{"value": str(index)} for index in range(5)]
    assert max_active == 2


def test_team_work_cache_revision_is_selective(generator):
    assert generator._query_cache_version(["dashboard", "get"]) == "1"
    assert generator._query_cache_version(["team", "work", "A2A"]) == "1:2"
    assert generator._query_cache_version(["github", "finder"]) == "1:2"


def test_page_includes_snapshot_provenance(generator, monkeypatch):
    monkeypatch.setattr(generator, "logo_data_uri", lambda: "data:image/png;base64,test")
    html = generator.page(
        "Engineering Intelligence",
        "<section>Report</section>",
        datetime(2026, 8, 30, 9, 23, tzinfo=UTC),
        "181106e0-3dde-4150-a467-2b4d0e326709",
    )
    assert "Report generated" in html
    assert "2026-08-30 09:23 UTC" in html
    assert "Snapshot 181106e0-3dde-4150-a467-2b4d0e326709" in html
    assert "6.1.0" in html
    assert "<script src=" not in html


def test_report_model_reuses_the_same_snapshot_pinned_views(generator):
    dashboard = {"snapshot_id": "snapshot-1"}
    people = [{"person_id": "person-1"}]
    metrics = {"Team": {"metric": "value"}}
    build_cycle = {"Team": {"groups": []}}
    github = {"Team": {"contributions": []}}
    details = {"Team": {"workflow": []}}
    work = {"Team": {"jira_issues": []}}
    completion = {"Team": {"months": {}}}

    model = generator.assemble_report_model(
        snapshot_id="snapshot-1",
        dashboard=dashboard,
        people=people,
        team_names=["Team"],
        metrics=metrics,
        build_cycle=build_cycle,
        github_pr_metrics=github,
        details=details,
        work=work,
        completion=completion,
    )

    assert model["dashboard"] is dashboard
    assert model["people"] is people
    assert model["teams"]["Team"]["build_cycle"] is build_cycle["Team"]
    assert model["teams"]["Team"]["work"] is work["Team"]


def test_classified_team_delivery_preserves_linked_and_unlinked_pull_requests(generator):
    rows = generator.classified_team_delivery({
        "jira_issues": [{"jira_key": "ENG-1", "url": "https://jira/ENG-1"}],
        "github_records": [
            {
                "record_type": "pull_request",
                "record_id": "acme/api#1",
                "jira_keys": ["ENG-1"],
            },
            {
                "record_type": "pull_request",
                "record_id": "acme/api#2",
                "jira_keys": [],
            },
            {"record_type": "commit", "record_id": "abc", "jira_keys": ["ENG-1"]},
        ],
    })

    assert [(row["record_id"], row["direct_jira_key"]) for row in rows] == [
        ("acme/api#1", "ENG-1"),
        ("acme/api#2", None),
    ]
    assert rows[0]["direct_jira_url"] == "https://jira/ENG-1"


def test_build_cycle_chart_and_table_share_one_canonical_record_payload(generator):
    html = generator.build_cycle_time_section(
        {
            "groups": [
                {
                    "classification": "ibr_linked",
                    "contributions": [
                        {
                            "jira_key": "ENG-1",
                            "title": "Deliver reusable chart",
                            "url": "https://jira.example/ENG-1",
                            "issue_type": "Epic",
                            "cycle_days": 6.0,
                            "period_started_at": "2026-08-24T00:00:00Z",
                            "period_ended_at": "2026-08-31T00:00:00Z",
                            "top_status": "In Progress",
                            "status_durations": [
                                {"status": "In Progress", "days": 6.0}
                            ],
                            "children": [],
                            "rag": None,
                        }
                    ],
                },
                {"classification": "non_ibr", "contributions": []},
            ],
            "data_quality_notes": [],
        }
    )

    assert 'data-cycle-key="ENG-1"' in html
    assert 'class="cycle-records"' in html
    assert '[["ENG-1","2026-08-31T00:00:00Z",6.0,{"In Progress":6.0}]]' in html
    assert "Weekly average cycle time" in html
    assert "Grouped by the UTC Monday" in html


def test_reusable_weekly_chart_is_wired_to_filtered_cycle_records(generator):
    assert "function weeklyAverage(records,dateOf,valueOf)" in generator.JS
    assert "function renderWeeklyAverageChart(container,records,options)" in generator.JS
    assert "renderWeeklyAverageChart(group.querySelector('.weekly-chart-canvas'),included" in generator.JS
    assert "unit:'calendar days'" in generator.JS


def test_github_metric_charts_share_canonical_pr_payloads(generator):
    html = generator.github_pr_metrics_section(
        {
            "repositories": ["example/repository"],
            "author_logins": ["engineer"],
            "contributions": [
                {
                    "repository": "example/repository",
                    "number": 42,
                    "title": "Reduce latency",
                    "url": "https://github.example/example/repository/pull/42",
                    "author": {"login": "engineer", "display_name": "Engineer"},
                    "reviewers": [],
                    "created_at": "2026-08-24T00:00:00Z",
                    "first_reviewed_at": "2026-08-25T00:00:00Z",
                    "merged_at": "2026-08-27T00:00:00Z",
                    "pickup_hours": 24.0,
                    "review_hours": 48.0,
                    "pickup_rag": None,
                    "review_rag": None,
                }
            ],
            "data_quality_notes": [],
        }
    )

    assert html.count('class="pr-metric-records"') == 2
    assert html.count('data-pr-key="example/repository#42"') == 2
    assert '[["example/repository#42","2026-08-27T00:00:00Z",24.0]]' in html
    assert '[["example/repository#42","2026-08-27T00:00:00Z",48.0]]' in html
    assert "Weekly average pickup time" in html
    assert "Weekly average review time" in html


def test_reusable_weekly_chart_is_wired_to_filtered_pr_records(generator):
    assert "group.querySelector('.pr-metric-records')" in generator.JS
    assert "date:record=>record.merged" in generator.JS
    assert "value:record=>record.hours" in generator.JS
    assert "unit:'hours'" in generator.JS


def test_done_column_is_the_numerator_per_month(generator):
    result = generator.completion_by_target_date(
        team(
            ("In Progress", [("FOUND-1", "2026-08"), ("FOUND-2", "2026-07")]),
            ("Done", [("FOUND-3", "2026-08"), ("FOUND-4", "2026-09")]),
        )
    )
    months = result["months"]
    assert {month: bucket["done"] for month, bucket in months.items()} == {
        "2026-07": 0,
        "2026-08": 1,
        "2026-09": 1,
    }
    assert {month: bucket["total"] for month, bucket in months.items()} == {
        "2026-07": 1,
        "2026-08": 2,
        "2026-09": 1,
    }
    assert result["dated"] == 4
    assert result["undated"] == 0


def test_months_are_returned_in_ascending_order(generator):
    result = generator.completion_by_target_date(
        team(
            (
                "Idea",
                [("A-1", "2026-09"), ("A-2", "2026-07"), ("A-3", "2026-08")],
            ),
        )
    )
    assert list(result["months"]) == ["2026-07", "2026-08", "2026-09"]


def test_undated_items_are_excluded_not_counted_incomplete(generator):
    result = generator.completion_by_target_date(
        team(
            ("In Progress", [("BX-1", None), ("BX-2", None)]),
            ("Done", [("BX-3", "2026-08")]),
        )
    )
    assert result["months"]["2026-08"]["total"] == 1
    assert result["months"]["2026-08"]["done"] == 1
    assert result["undated"] == 2
    assert generator.completion_pct(1, 1) == 100.0


def test_malformed_values_are_flagged_and_never_dated(generator):
    result = generator.completion_by_target_date(
        team(
            (
                "In Progress",
                [
                    ("ES-129", "2026_10"),
                    ("OBS-13", "2026_08"),
                    ("APIM-1", "APIM-14539"),
                    ("ES-130", "2026-13"),
                ],
            ),
        )
    )
    assert result["months"] == {}
    assert result["dated"] == 0
    # Malformed values are excluded from the percentage, so they land in the
    # undated bucket and dated + undated still reconciles to the item count.
    assert result["undated"] == 4
    assert result["malformed"] == [
        ("ES-129", "2026_10"),
        ("OBS-13", "2026_08"),
        ("APIM-1", "APIM-14539"),
        ("ES-130", "2026-13"),
    ]


def test_unmapped_status_counts_toward_total_but_never_done(generator):
    result = generator.completion_by_target_date(
        team(("Unmapped status", [("LLM-1", "2026-08")])),
    )
    assert result["months"]["2026-08"] == {
        "total": 1,
        "done": 0,
        "credit": 0.0,
        "children": counts(),
    }


def test_team_without_board_items_renders_empty_state(generator):
    result = generator.completion_by_target_date(team())
    assert result["months"] == {}
    assert generator.completion_pct(0, 0) == 0.0
    assert "not computable" in generator.completion_table_html(result)


def test_rendered_table_excludes_malformed_values(generator):
    result = generator.completion_by_target_date(
        team(
            ("Done", [("OBS-1", "2026-08")]),
            ("In Progress", [("OBS-13", "2026_08")]),
        )
    )
    html = generator.completion_table_html(result)
    assert "2026_08" not in html
    assert "OBS-13" not in html
    assert "100.0%" in html


def test_children_partially_complete_the_in_progress_parent(generator):
    result = generator.completion_by_target_date(
        team(("In Progress", [("FOUND-72", "2026-07")])),
        {
            "FOUND-72": hierarchy(
                "FOUND-72",
                "done", "done", "done", "done", "done",
                "indeterminate", "indeterminate",
                "new", "new",
            )
        },
    )
    bucket = result["months"]["2026-07"]
    assert bucket["done"] == 0
    assert bucket["children"] == counts(
        total=9, done=5, in_progress=2, not_started=2
    )
    # Five of nine children are done, so the parent is 55.6% complete rather
    # than the 0% its own board column would report.
    assert generator.completion_pct(bucket["credit"], bucket["total"]) == 55.6


def test_in_progress_children_are_counted_but_earn_no_credit(generator):
    result = generator.completion_by_target_date(
        team(("In Progress", [("AIAM-130", "2026-08")])),
        {"AIAM-130": hierarchy("AIAM-130", "indeterminate", "indeterminate")},
    )
    bucket = result["months"]["2026-08"]
    assert bucket["children"] == counts(total=2, in_progress=2)
    assert bucket["credit"] == 0.0


def test_parent_in_done_column_keeps_full_credit(generator):
    result = generator.completion_by_target_date(
        team(("Done", [("APIM-14755", "2026-08")])),
        {"APIM-14755": hierarchy("APIM-14755", "done", "new")},
    )
    bucket = result["months"]["2026-08"]
    assert bucket["credit"] == 1.0
    assert bucket["done"] == 1
    # The unfinished child under a Done parent is hygiene, not a deduction.
    assert result["open_children_under_done"] == [("APIM-14755", 1)]


def test_childless_parent_scores_by_its_own_column(generator):
    result = generator.completion_by_target_date(
        team(
            ("In Progress", [("AIAM-52", "2026-08")]),
            ("Done", [("AIAM-70", "2026-08")]),
        ),
        {"AIAM-52": hierarchy("AIAM-52"), "AIAM-70": hierarchy("AIAM-70")},
    )
    bucket = result["months"]["2026-08"]
    assert bucket["children"]["total"] == 0
    assert bucket["credit"] == 1.0
    assert generator.completion_pct(bucket["credit"], bucket["total"]) == 50.0


def test_descendants_are_counted_at_every_depth(generator):
    grandchild = {"jira_key": "X-3", "status_category": "done", "children": []}
    child = {"jira_key": "X-2", "status_category": "new", "children": [grandchild]}
    root = {"jira_key": "X-1", "status_category": "indeterminate", "children": [child]}
    result = generator.completion_by_target_date(
        team(("In Progress", [("X-1", "2026-08")])), {"X-1": root}
    )
    bucket = result["months"]["2026-08"]
    assert bucket["children"] == counts(total=2, done=1, not_started=1)
    assert bucket["credit"] == 0.5


def test_unknown_child_category_is_reported_not_assumed_unfinished(generator):
    root = {
        "jira_key": "Y-1",
        "status_category": "indeterminate",
        "children": [{"jira_key": "Y-2", "status_category": None, "children": []}],
    }
    result = generator.completion_by_target_date(
        team(("In Progress", [("Y-1", "2026-08")])), {"Y-1": root}
    )
    assert result["months"]["2026-08"]["children"] == counts(total=1, unknown=1)


def test_missing_hierarchy_falls_back_to_the_board_column(generator):
    result = generator.completion_by_target_date(
        team(("In Progress", [("Z-1", "2026-08")])), {}
    )
    assert result["missing_hierarchy"] == ["Z-1"]
    assert result["months"]["2026-08"]["credit"] == 0.0


def test_rollup_is_visible_in_rendered_table(generator):
    completion = generator.completion_by_target_date(
        team(("In Progress", [("FOUND-72", "2026-07")])),
        {"FOUND-72": hierarchy("FOUND-72", "done", "indeterminate", "new", "new")},
    )
    table = generator.completion_table_html(completion)
    assert "25.0%" in table
    # The board-only reading stays visible next to the rolled-up one.
    assert "0/1" in table
    assert "FOUND-72" in table


def test_breakdown_lists_every_child_issue_under_its_parent(generator):
    completion = generator.completion_by_target_date(
        team(("In Progress", [("FOUND-72", "2026-07")])),
        {"FOUND-72": hierarchy("FOUND-72", "done", "indeterminate", "new")},
    )
    html = generator.completion_breakdown_html(completion)
    for index in (1, 2, 3):
        assert f"FOUND-72-C{index}" in html
    # Each child is labelled by state, so the meter is never the only channel.
    assert "Done</span>" in html
    assert "In progress</span>" in html
    assert "Not started</span>" in html
    # Children start collapsed behind a labelled, accessible toggle.
    assert html.count('class="child-row collapsed"') == 3
    assert 'data-children="FOUND-72"' in html
    assert 'aria-expanded="false"' in html
    assert "3 child issues" in html


def test_breakdown_groups_parents_under_their_target_month(generator):
    completion = generator.completion_by_target_date(
        team(
            ("In Progress", [("A-1", "2026-07"), ("B-1", "2026-08")]),
            ("Done", [("C-1", "2026-08")]),
        ),
        {"A-1": hierarchy("A-1", "done"), "B-1": hierarchy("B-1", "new")},
    )
    html = generator.completion_breakdown_html(completion)
    assert html.index("2026-07") < html.index("A-1") < html.index("2026-08")
    assert "1 dated item<" in html
    assert "2 dated items<" in html
    # A parent with no hierarchy still gets a row, with an explicit empty state.
    assert "No child issues" in html


def test_meter_omits_empty_states_and_describes_itself(generator):
    counts = generator.rollup_counts(
        [{"state": "done"}, {"state": "done"}, {"state": "in_progress"}]
    )
    html = generator.meter_html(counts)
    assert 'aria-label="2 done, 1 in progress"' in html
    assert 'class="seg done" style="flex:2"' in html
    assert 'class="seg in_progress" style="flex:1"' in html
    # Zero-count states never render a segment, which would eat a 2px gap.
    assert "not_started" not in html
    assert generator.meter_html(generator.rollup_counts([])) == '<span class="muted">—</span>'


def test_descendant_records_carry_state_and_depth(generator):
    grandchild = {
        "jira_key": "X-3", "title": "Deep", "status": "Done",
        "status_category": "done", "url": "u3", "depth": 2, "children": [],
    }
    child = {
        "jira_key": "X-2", "title": "Mid", "status": "To Do",
        "status_category": "new", "url": "u2", "depth": 1, "children": [grandchild],
    }
    records = generator.descendant_records(
        {"jira_key": "X-1", "status_category": "indeterminate", "children": [child]}
    )
    assert [(r["jira_key"], r["state"], r["depth"]) for r in records] == [
        ("X-2", "not_started", 1),
        ("X-3", "done", 2),
    ]
    assert generator.rollup_counts(records) == counts(total=2, done=1, not_started=1)


def test_legend_keys_only_the_states_present(generator):
    completion = generator.completion_by_target_date(
        team(("In Progress", [("K-1", "2026-08")])),
        {"K-1": hierarchy("K-1", "done", "new")},
    )
    legend = generator.completion_legend_html(
        generator.aggregate_children(completion["months"])
    )
    assert "Done" in legend and "Not started" in legend
    # No child is in progress or unknown here, so neither earns a swatch.
    assert "In progress" not in legend
    assert "Unknown" not in legend
    assert generator.completion_legend_html(counts()) == ""


def test_github_finder_embeds_compact_paged_records_and_controls(generator):
    html = generator.github_finder_section({
        "records": [{
            "record_type": "pull_request", "repository": "acme/api",
            "identifier": "#42", "title": "Ship finder", "url": "https://github/pr/42",
            "state": "merged", "draft": False, "author_login": "octocat",
            "created_at": "2026-08-01T00:00:00Z", "updated_at": "2026-08-02T00:00:00Z",
            "merged_at": "2026-08-03T00:00:00Z", "authored_at": None,
            "committed_at": None, "head_ref": "finder", "base_ref": "main",
            "commit_count": 2, "review_count": 1, "reviewers": ["reviewer"],
            "first_reviewed_at": "2026-08-01T12:00:00Z",
            "first_commit_at": "2026-07-31T12:00:00Z", "coding_hours": 12.0,
            "pickup_hours": 12.0, "review_hours": 36.0,
            "pull_requests": [], "jira_keys": ["ENG-1"],
            "jira_urls": {"ENG-1": "https://jira/ENG-1"},
        }],
        "data_quality_notes": ["Pinned evidence."],
    }, {"people": [
        {"github_login": "octocat", "current_teams": ["A2A"]},
        {"github_login": "reviewer", "current_teams": ["Foundations"]},
    ]}, ["A2A", "Foundations", "Team with no records"])
    assert 'class="github-finder-table"' in html
    assert 'data-gh-multi="repository"' in html
    assert 'data-gh-multi="reviewer"' in html
    assert 'data-gh-multi="team"' in html
    assert "Team with no records" in html
    assert "reviewerTeam" not in html
    assert 'class="column-manager github-column-manager"' in html
    assert 'id="github-finder-data"' in html
    assert "Ship finder" in html and "ENG-1" in html
    assert "pickupHours" in generator.JS and "PR pickup time" in generator.JS
    assert "reviewHours" in generator.JS and "PR review time" in generator.JS
    assert "codingHours" in generator.JS and "PR coding time" in generator.JS
    assert "A2A" in html and "Foundations" in html
    assert html.count("data-github-finder-chart=") == 3
    assert html.count("data-gh-chart-average") == 3
    assert html.count("data-gh-chart-trend") == 3
    assert html.count("data-multi-all") == 9
    assert html.count("data-multi-none") == 9
    assert "data-gh-outlier-toggle" in html and "data-gh-outlier-rows" in html
    assert 'data-gh-multi="timing"' in html
    assert html.count("data-gh-outlier-threshold=") == 3
    assert "How GitHub timing metrics are computed" in html
    assert 'data-gh-multi="teamMapping"' in html
    assert "Mapped to a Jira team" in html and "Not mapped to a Jira team" in html
    assert "Mapped person" in generator.JS and "Mapped team(s)" in generator.JS
    assert "github-filter-card" in html
    assert "data-gh-filter-chips" in html
    assert "data-gh-search" in html


def test_issue_finder_embeds_completed_cycle_boundary_for_filtered_chart(generator):
    html = generator.issue_finder_section(
        [
            {
                "jira_key": "ENG-1",
                "team_name": "Team",
                "title": "Ship trend chart",
                "url": "https://jira.example/ENG-1",
                "status": "Done",
                "status_category": "done",
                "issue_type": "Story",
                "assignee_display_name": "Engineer",
                "source_updated_at": "2026-08-31T12:00:00Z",
                "classification": "non_ibr",
                "active": False,
                "cycle_started_at": "2026-08-24T09:00:00Z",
                "cycle_ended_at": "2026-08-28T09:00:00Z",
                "total_cycle_days": 4.0,
                "in_progress_cycle_days": 2.0,
                "in_review_cycle_days": 1.0,
                "in_test_cycle_days": 1.0,
                "skipped_phases": [],
                "linked_pull_requests": [],
            }
        ]
    )

    assert 'data-cycle-ended="2026-08-28"' in html
    assert 'data-total-cycle-days="4.0"' in html
    assert "data-issue-finder-chart" in html
    assert "only issues that entered Done" in html
    assert "data-issue-outlier-toggle" in html
    assert 'class="multi-filter" data-finder-multi="issueTeam"' in html
    assert 'class="multi-filter" data-finder-multi="issueStatus"' in html
    assert 'class="multi-filter" data-finder-multi="issueType"' in html
    assert 'class="multi-filter" data-finder-multi="issueSkippedPhases"' in html
    assert 'data-issue-skipped-phases="No skipped phases"' in html
    assert 'data-issue-type="Story"' in html
    assert "All statuses" in html and "All types" in html
    assert html.count("data-multi-all") == 4
    assert html.count("data-multi-none") == 4
    assert html.index("data-issue-finder-text") < html.index("data-issue-finder-chart")
    assert "data-issue-search" in html
    assert html.index("data-issue-date-from") < html.index("data-issue-finder-chart")
    assert html.index("weekly-chart-canvas") < html.index("data-issue-outlier-toggle")
    assert "data-issue-outlier-rows" in html


def test_issue_finder_uses_full_configured_team_list(generator):
    html = generator.issue_finder_section([], ["Team A", "Team B"])
    assert "Team A" in html and "Team B" in html


def test_finder_charts_use_filtered_populations(generator):
    assert "new MutationObserver" in generator.JS
    assert "row.dataset.cycleEnded" in generator.JS
    assert "row.dataset.cycleEnded&&row.dataset.totalCycleDays!==''" in generator.JS
    assert "extremeHighOutliers(records" in generator.JS
    assert "echarts.init" in generator.JS
    assert "triggerOn:'mousemove|click'" in generator.JS
    assert "multiValues(control)" in generator.JS
    assert "syncIssueMultiLabels" in generator.JS
    assert "values.join(', ')" in generator.JS
    assert "deferFinderSearch" in generator.JS
    assert "input.addEventListener('keydown'" in generator.JS
    assert "renderGithubFinderChart" in generator.JS
    assert generator.JS.index("function formatHours") < generator.JS.index("function renderGithubFinderChart")
    assert "const records=filtered.filter(row=>row.type==='pull_request'" in generator.JS
    assert "finderCharts();draw()" in generator.JS
