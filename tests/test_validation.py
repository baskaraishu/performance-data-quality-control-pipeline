import csv
import hashlib
import json

import pandas as pd
import pytest

from src.rule_config import validate_rule_config
from src.validation import (
    ISSUE_COLUMNS,
    ValidationError,
    evaluate_records,
    validate_run,
)


def make_rule(rule_id, columns, check, parameters=None, action="flag"):
    return {
        "rule_id": rule_id,
        "columns": columns,
        "check": check,
        "parameters": parameters or {},
        "severity": "High",
        "action": action,
        "description": f"Test rule {rule_id}.",
    }


def full_config_rules():
    return [
        make_rule("NULL", ["establishment_id"], "not_null", action="quarantine"),
        make_rule(
            "DATE", ["reporting_month"], "valid_iso_date", {"format": "%Y-%m-%d"}
        ),
        make_rule("INTEGER", ["population"], "non_negative_integer"),
        make_rule("NUMBER", ["assaults_per_100"], "non_negative_number"),
        make_rule(
            "BETWEEN",
            ["education_attendance_pct"],
            "between",
            {"min": 0, "max": 100},
        ),
        make_rule(
            "GREATER",
            ["certified_normal_accommodation"],
            "greater_than",
            {"value": 0},
        ),
        make_rule("EXCEEDS", ["occupancy_pct"], "exceeds", {"value": 100}),
        make_rule(
            "REF_KEY",
            ["establishment_id"],
            "reference_exists",
            {"reference_file": "establishments.csv", "reference_key": "establishment_id"},
        ),
        make_rule(
            "REF_VALUE",
            ["region"],
            "reference_column_values",
            {"reference_file": "establishments.csv", "reference_column": "region"},
        ),
        make_rule(
            "UNIQUE",
            ["establishment_id", "reporting_month"],
            "unique_combination",
            action="quarantine",
        ),
    ]


def test_evaluator_applies_each_supported_check_and_groups_per_rule_record():
    configuration = validate_rule_config(
        {"schema_version": 1, "rules": full_config_rules()}
    )
    frame = pd.DataFrame(
        [
            {
                "establishment_id": "PR001",
                "reporting_month": "2025-04-01",
                "population": "10",
                "assaults_per_100": "0.5",
                "education_attendance_pct": "50",
                "certified_normal_accommodation": "100",
                "occupancy_pct": "100",
                "region": "North",
            },
            {
                "establishment_id": "UNKNOWN",
                "reporting_month": "2025-14-01",
                "population": "-2",
                "assaults_per_100": "bad-rate",
                "education_attendance_pct": "101",
                "certified_normal_accommodation": "0",
                "occupancy_pct": "101",
                "region": "West",
            },
            {
                "establishment_id": "PR001",
                "reporting_month": "2025-04-01",
                "population": "5",
                "assaults_per_100": "1.0",
                "education_attendance_pct": "25",
                "certified_normal_accommodation": "50",
                "occupancy_pct": "98",
                "region": "North",
            },
            {
                "establishment_id": "",
                "reporting_month": "",
                "population": "",
                "assaults_per_100": "",
                "education_attendance_pct": "",
                "certified_normal_accommodation": "",
                "occupancy_pct": "",
                "region": "",
            },
        ]
    )
    references = {
        "establishments.csv": pd.DataFrame(
            {"establishment_id": ["PR001"], "region": ["North"]}
        )
    }

    issues, summary = evaluate_records(
        frame,
        configuration["rules"],
        references=references,
        run_id="unit-run",
        source_file="sample.csv",
        detected_timestamp_utc="2026-09-29T00:00:00+00:00",
    )

    row_two_issues = [issue for issue in issues if issue["source_record_id"] == "sample.csv:3"]
    failed_rules = {issue["rule_id"] for issue in row_two_issues}
    assert failed_rules == {
        "DATE",
        "INTEGER",
        "NUMBER",
        "BETWEEN",
        "GREATER",
        "EXCEEDS",
        "REF_KEY",
        "REF_VALUE",
    }
    assert any(
        issue["rule_id"] == "NULL" and issue["source_record_id"] == "sample.csv:5"
        for issue in issues
    )
    assert len([issue for issue in issues if issue["rule_id"] == "UNIQUE"]) == 2
    assert summary["rule_count"] == 10
    assert summary["input_row_count"] == 4
    assert summary["records_with_failures"] == 4
    assert summary["issue_count"] == len(issues)
    assert summary["status"] == "FAIL"
    assert all(set(issue) == set(ISSUE_COLUMNS) for issue in issues)
    assert next(issue for issue in row_two_issues if issue["rule_id"] == "NUMBER")[
        "observed_value"
    ] == '{"assaults_per_100": "bad-rate"}'


def write_run(tmp_path, rows, headers):
    run_id = "integration-run"
    run_directory = tmp_path / "bronze" / run_id
    run_directory.mkdir(parents=True)
    source_path = run_directory / "performance_dirty.csv"
    with source_path.open("w", newline="", encoding="utf-8") as output:
        writer = csv.writer(output)
        writer.writerow(headers)
        writer.writerows(rows)
    metadata = {
        "run_id": run_id,
        "source_file": source_path.name,
        "source_sha256": hashlib.sha256(source_path.read_bytes()).hexdigest(),
        "schema_version": "v1",
        "row_count": len(rows),
        "column_count": len(headers),
    }
    (run_directory / "metadata.json").write_text(
        json.dumps(metadata), encoding="utf-8"
    )
    return run_id, source_path


def write_reference_and_rules(tmp_path, rules):
    reference_path = tmp_path / "reference.csv"
    with reference_path.open("w", newline="", encoding="utf-8") as output:
        writer = csv.writer(output)
        writer.writerow(["establishment_id", "region"])
        writer.writerow(["PR001", "North"])
    rules_path = tmp_path / "rules.yml"
    import yaml

    rules_path.write_text(
        yaml.safe_dump({"schema_version": 1, "rules": rules}), encoding="utf-8"
    )
    return reference_path, rules_path


def test_validate_run_writes_traceable_issues_and_summary(tmp_path):
    headers = ["establishment_id", "reporting_month", "population", "region"]
    run_id, source_path = write_run(
        tmp_path,
        [["UNKNOWN", "2025-14-01", "-1", "West"]],
        headers,
    )
    rules = [
        make_rule("NULL", ["establishment_id"], "not_null"),
        make_rule(
            "DATE", ["reporting_month"], "valid_iso_date", {"format": "%Y-%m-%d"}
        ),
        make_rule("INTEGER", ["population"], "non_negative_integer"),
        make_rule(
            "REF",
            ["establishment_id"],
            "reference_exists",
            {"reference_file": "reference.csv", "reference_key": "establishment_id"},
        ),
    ]
    _, rules_path = write_reference_and_rules(tmp_path, rules)
    original_hash = hashlib.sha256(source_path.read_bytes()).hexdigest()

    summary, issues_path, summary_path = validate_run(
        run_id,
        bronze_root=tmp_path / "bronze",
        rules_path=rules_path,
        output_root=tmp_path / "silver",
        project_root=tmp_path,
        detected_timestamp_utc="2026-09-29T00:00:00+00:00",
    )

    issues = pd.read_csv(issues_path, dtype=str, keep_default_na=False)
    saved_summary = json.loads(summary_path.read_text(encoding="utf-8"))
    assert len(issues) == 3
    assert set(issues["source_record_id"]) == {"performance_dirty.csv:2"}
    assert summary["records_with_failures"] == 1
    assert saved_summary["status"] == "WARNING"
    assert hashlib.sha256(source_path.read_bytes()).hexdigest() == original_hash


def test_validate_run_fails_clearly_when_reference_file_is_unavailable(tmp_path):
    headers = ["establishment_id"]
    run_id, _ = write_run(tmp_path, [["PR001"]], headers)
    rules = [
        make_rule(
            "REF",
            ["establishment_id"],
            "reference_exists",
            {"reference_file": "missing.csv", "reference_key": "establishment_id"},
        )
    ]
    _, rules_path = write_reference_and_rules(tmp_path, rules)

    with pytest.raises(ValidationError, match="Reference file does not exist"):
        validate_run(
            run_id,
            bronze_root=tmp_path / "bronze",
            rules_path=rules_path,
            output_root=tmp_path / "silver",
            project_root=tmp_path,
        )


def test_validate_run_fails_clearly_for_unknown_rule_type(tmp_path):
    headers = ["establishment_id"]
    run_id, _ = write_run(tmp_path, [["PR001"]], headers)
    invalid_rule = make_rule("BAD", ["establishment_id"], "guess_and_fix")
    _, rules_path = write_reference_and_rules(tmp_path, [invalid_rule])

    with pytest.raises(ValidationError, match="Invalid quality-rule configuration.*unsupported check"):
        validate_run(
            run_id,
            bronze_root=tmp_path / "bronze",
            rules_path=rules_path,
            output_root=tmp_path / "silver",
            project_root=tmp_path,
        )