"""Target-Date completion math for the weekly status report generator."""

import importlib.util
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
