from copy import deepcopy

import pandas as pd
import pytest

from src.quality_scoring import (
    QualityScoringError,
    calculate_scores,
    convert_summary_file,
    render_score_summary_html,
    validate_dimension_config,
)


def rule(rule_id, columns, check, parameters=None):
    return {
        "rule_id": rule_id,
        "columns": columns,
        "check": check,
        "parameters": parameters or {},
        "severity": "High",
        "action": "flag",
        "description": f"Test rule {rule_id}.",
    }


def make_rules():
    return [
        rule("REQ-POP", ["population"], "not_null"),
        rule("RATE-PCT", ["education_attendance_pct"], "between", {"min": 0, "max": 100}),
        rule("CAPACITY", ["certified_normal_accommodation"], "greater_than", {"value": 0}),
        rule("DUP", ["establishment_id", "reporting_month"], "unique_combination"),
        rule("REF", ["establishment_id"], "reference_exists", {"reference_file": "establishments.csv", "reference_key": "establishment_id"}),
        rule("DATE", ["data_submission_date"], "valid_iso_date", {"format": "%Y-%m-%d"}),
        rule("OCC-REVIEW", ["occupancy_pct"], "exceeds", {"value": 100}),
    ]


def make_dimensions():
    return {
        "schema_version": 1,
        "dimensions": {
            "completeness": ["REQ-POP"],
            "validity": ["RATE-PCT", "CAPACITY"],
            "uniqueness": ["DUP"],
            "consistency": [],
            "referential_integrity": ["REF"],
            "timeliness": ["DATE"],
        },
        "review_only_rule_ids": ["OCC-REVIEW"],
    }


def make_issue(run_id, source_record_id, rule_id):
    return {
        "run_id": run_id,
        "source_record_id": source_record_id,
        "rule_id": rule_id,
    }


def test_scores_use_applicable_denominators_and_count_records_once_per_dimension():
    run_id = "score-test"
    frame = pd.DataFrame(
        [
            {"establishment_id": "PR001", "reporting_month": "2025-04-01", "population": "10", "education_attendance_pct": "50", "certified_normal_accommodation": "12", "data_submission_date": "2025-05-01", "occupancy_pct": "90"},
            {"establishment_id": "PR002", "reporting_month": "", "population": "", "education_attendance_pct": "101", "certified_normal_accommodation": "0", "data_submission_date": "bad-date", "occupancy_pct": "80"},
            {"establishment_id": "PR001", "reporting_month": "2025-04-01", "population": "10", "education_attendance_pct": "50", "certified_normal_accommodation": "12", "data_submission_date": "2025-05-01", "occupancy_pct": "105"},
            {"establishment_id": "PR003", "reporting_month": "2025-05-01", "population": "8", "education_attendance_pct": "", "certified_normal_accommodation": "", "data_submission_date": "", "occupancy_pct": ""},
        ]
    )
    issues = pd.DataFrame(
        [
            make_issue(run_id, "input.csv:3", "REQ-POP"),
            make_issue(run_id, "input.csv:3", "RATE-PCT"),
            make_issue(run_id, "input.csv:3", "CAPACITY"),
            make_issue(run_id, "input.csv:3", "DATE"),
            make_issue(run_id, "input.csv:4", "DUP"),
            make_issue(run_id, "input.csv:2", "DUP"),
            make_issue(run_id, "input.csv:3", "REF"),
            make_issue(run_id, "input.csv:4", "DUP"),
            make_issue(run_id, "input.csv:4", "OCC-REVIEW"),
        ]
    )

    result = calculate_scores(
        frame,
        issues,
        make_rules(),
        make_dimensions(),
        source_file="input.csv",
        run_id=run_id,
    )
    dimensions = result["dimension_results"]

    assert dimensions["completeness"]["applicable_records"] == 4
    assert dimensions["completeness"]["failed_records"] == 1
    assert dimensions["completeness"]["score_pct"] == 75.0
    assert dimensions["validity"]["applicable_records"] == 3
    assert dimensions["validity"]["failed_records"] == 1
    assert dimensions["validity"]["issue_count"] == 2
    assert dimensions["validity"]["score_pct"] == 66.67
    assert dimensions["uniqueness"]["applicable_records"] == 3
    assert dimensions["uniqueness"]["failed_records"] == 2
    assert dimensions["uniqueness"]["score_pct"] == 33.33
    assert dimensions["referential_integrity"]["score_pct"] == 75.0
    assert dimensions["timeliness"]["score_pct"] == 66.67
    assert dimensions["consistency"]["status"] == "NOT_AVAILABLE"
    assert dimensions["consistency"]["score_pct"] is None
    assert result["review_only_issue_count"] == 1
    assert result["review_only_record_count"] == 1
    assert result["overall_score_pct"] == 63.33


def test_zero_applicable_records_is_not_available_not_zero_or_full_score():
    rules = [rule("OPTIONAL", ["education_attendance_pct"], "between", {"min": 0, "max": 100})]
    dimensions = make_dimensions()
    dimensions["dimensions"]["validity"] = ["OPTIONAL"]
    dimensions["dimensions"]["completeness"] = []
    dimensions["dimensions"]["uniqueness"] = []
    dimensions["dimensions"]["referential_integrity"] = []
    dimensions["dimensions"]["timeliness"] = []
    dimensions["review_only_rule_ids"] = []
    frame = pd.DataFrame([{"education_attendance_pct": ""}])

    result = calculate_scores(
        frame,
        pd.DataFrame(columns=["run_id", "source_record_id", "rule_id"]),
        rules,
        dimensions,
        source_file="input.csv",
        run_id="empty-score",
    )

    assert result["dimension_results"]["validity"]["status"] == "NOT_AVAILABLE"
    assert result["dimension_results"]["validity"]["score_pct"] is None


def test_dimension_config_requires_every_rule_to_be_scored_or_review_only():
    config = make_dimensions()
    config["review_only_rule_ids"] = []

    with pytest.raises(QualityScoringError, match="must be assigned"):
        validate_dimension_config(config, make_rules())


def test_html_report_shows_score_denominators_and_unavailable_dimension():
    result = calculate_scores(
        pd.DataFrame(
            [
                {"establishment_id": "PR001", "reporting_month": "2025-04-01", "population": "10", "education_attendance_pct": "50", "certified_normal_accommodation": "10", "data_submission_date": "2025-05-01", "occupancy_pct": "90"}
            ]
        ),
        pd.DataFrame(columns=["run_id", "source_record_id", "rule_id"]),
        make_rules(),
        make_dimensions(),
        source_file="input.csv",
        run_id="<script>alert(1)</script>",
    )

    report = render_score_summary_html(result)

    assert "Data Quality Score" in report
    assert "completeness" not in report
    assert "Completeness" in report
    assert "Applicable" in report
    assert "Not available" in report
    assert "Unweighted average of available dimension scores" in report
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in report
    assert "<script>alert(1)</script>" not in report


def test_existing_json_summary_converts_to_sibling_html(tmp_path):
    result = calculate_scores(
        pd.DataFrame(
            [
                {"establishment_id": "PR001", "reporting_month": "2025-04-01", "population": "10", "education_attendance_pct": "50", "certified_normal_accommodation": "10", "data_submission_date": "2025-05-01", "occupancy_pct": "90"}
            ]
        ),
        pd.DataFrame(columns=["run_id", "source_record_id", "rule_id"]),
        make_rules(),
        make_dimensions(),
        source_file="input.csv",
        run_id="conversion-test",
    )
    summary_path = tmp_path / "dq_score_summary.json"
    summary_path.write_text(__import__("json").dumps(result), encoding="utf-8")

    html_path = convert_summary_file(summary_path)

    assert html_path == tmp_path / "dq_score_summary.html"
    assert html_path.is_file()
    assert "conversion-test" in html_path.read_text(encoding="utf-8")