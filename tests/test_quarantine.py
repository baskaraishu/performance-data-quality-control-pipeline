import csv
import hashlib
import json

import pandas as pd
import pytest

from src.validation import ISSUE_COLUMNS
from src.quarantine import (
    DISPOSITION_COLUMNS,
    QuarantineError,
    build_dispositions,
    quarantine_run,
)


def issue(run_id, record_id, rule_id, action, severity="High"):
    return {
        "issue_id": f"{run_id}:{record_id}:{rule_id}",
        "run_id": run_id,
        "source_record_id": record_id,
        "rule_id": rule_id,
        "field": "population",
        "observed_value": '{"population": "-1"}',
        "failure_reason": "Test issue.",
        "severity": severity,
        "action": action,
        "status": "FAIL" if action == "quarantine" else "WARNING",
        "detected_timestamp_utc": "2026-09-29T00:00:00+00:00",
    }


def test_dispositions_separate_flagged_quarantined_and_duplicate_held_rows():
    run_id = "sample-run"
    frame = pd.DataFrame(
        [
            {"establishment_id": "PR001", "population": "10"},
            {"establishment_id": "PR002", "population": "9"},
            {"establishment_id": "PR003", "population": "-1"},
            {"establishment_id": "PR004", "population": "8"},
            {"establishment_id": "PR005", "population": "-2"},
        ]
    )
    issues = pd.DataFrame(
        [
            issue(run_id, "input.csv:3", "REVIEW", "flag"),
            issue(run_id, "input.csv:4", "REQ-001", "quarantine"),
            issue(run_id, "input.csv:5", "DUP-001", "quarantine"),
            issue(run_id, "input.csv:6", "DUP-001", "quarantine"),
            issue(run_id, "input.csv:6", "REQ-003", "quarantine"),
        ],
        columns=ISSUE_COLUMNS,
    )

    dispositions, blocked, linked_issues = build_dispositions(
        frame, issues, run_id=run_id, source_file="input.csv"
    )

    assert list(dispositions.columns) == DISPOSITION_COLUMNS
    assert dispositions["disposition"].tolist() == [
        "eligible",
        "eligible",
        "quarantined",
        "duplicate-held",
        "quarantined",
    ]
    assert dispositions.loc[1, "flag_issue_count"] == 1
    assert len(blocked) == 3
    assert len(linked_issues) == 4
    assert len(dispositions) == len(frame)
    assert dispositions["disposition"].value_counts().to_dict() == {
        "eligible": 2,
        "quarantined": 2,
        "duplicate-held": 1,
    }


def make_run(tmp_path):
    run_id = "quarantine-run"
    bronze_directory = tmp_path / "bronze" / run_id
    bronze_directory.mkdir(parents=True)
    source_path = bronze_directory / "input.csv"
    source_rows = [
        ["PR001", "10"],
        ["PR002", "9"],
        ["PR003", "-1"],
    ]
    with source_path.open("w", newline="", encoding="utf-8") as output:
        writer = csv.writer(output)
        writer.writerow(["establishment_id", "population"])
        writer.writerows(source_rows)

    metadata = {
        "run_id": run_id,
        "source_file": source_path.name,
        "source_sha256": hashlib.sha256(source_path.read_bytes()).hexdigest(),
        "row_count": len(source_rows),
        "column_count": 2,
    }
    (bronze_directory / "metadata.json").write_text(
        json.dumps(metadata), encoding="utf-8"
    )

    validation_directory = tmp_path / "silver" / run_id
    validation_directory.mkdir(parents=True)
    issues = pd.DataFrame(
        [
            issue(run_id, "input.csv:3", "REVIEW", "flag"),
            issue(run_id, "input.csv:4", "REQ-003", "quarantine"),
        ],
        columns=ISSUE_COLUMNS,
    )
    issues.to_csv(validation_directory / "validation_issues.csv", index=False)
    validation_summary = {
        "run_id": run_id,
        "source_sha256": metadata["source_sha256"],
        "input_row_count": len(source_rows),
        "issue_count": len(issues),
    }
    (validation_directory / "validation_summary.json").write_text(
        json.dumps(validation_summary), encoding="utf-8"
    )
    return run_id, source_path


def test_quarantine_run_writes_traceable_outputs_and_reconciles(tmp_path):
    run_id, source_path = make_run(tmp_path)
    original_hash = hashlib.sha256(source_path.read_bytes()).hexdigest()

    summary, summary_path = quarantine_run(
        run_id,
        bronze_root=tmp_path / "bronze",
        validation_root=tmp_path / "silver",
        output_root=tmp_path / "quarantine",
        generated_at_utc="2026-09-29T00:00:00+00:00",
    )

    output_dir = tmp_path / "quarantine" / run_id
    records = pd.read_csv(output_dir / "quarantined_records.csv", dtype=str)
    issues = pd.read_csv(output_dir / "issues.csv", dtype=str)
    dispositions = pd.read_csv(output_dir / "dispositions.csv", dtype=str)
    saved_summary = json.loads(summary_path.read_text(encoding="utf-8"))
    assert len(records) == 1
    assert records.iloc[0]["source_record_id"] == "input.csv:4"
    assert records.iloc[0]["disposition"] == "quarantined"
    assert len(issues) == 1
    assert issues.iloc[0]["source_record_id"] == "input.csv:4"
    assert dispositions["disposition"].value_counts().to_dict() == {
        "eligible": 2,
        "quarantined": 1,
    }
    assert summary["input_record_count"] == 3
    assert summary["blocked_record_count"] == 1
    assert summary["validation_issue_row_count"] == 2
    assert summary["issues_for_blocked_records"] == 1
    assert summary["records_with_flag_issues"] == 1
    assert summary["eligible_records_with_flag_issues"] == 1
    assert sum(summary["disposition_counts"].values()) == 3
    assert saved_summary["disposition_counts"] == summary["disposition_counts"]
    assert hashlib.sha256(source_path.read_bytes()).hexdigest() == original_hash


def test_quarantine_run_rejects_issues_from_another_run(tmp_path):
    run_id, _ = make_run(tmp_path)
    issue_path = tmp_path / "silver" / run_id / "validation_issues.csv"
    issues = pd.read_csv(issue_path, dtype=str)
    issues.loc[0, "run_id"] = "different-run"
    issues.to_csv(issue_path, index=False)

    with pytest.raises(QuarantineError, match="must all belong to run"):
        quarantine_run(
            run_id,
            bronze_root=tmp_path / "bronze",
            validation_root=tmp_path / "silver",
            output_root=tmp_path / "quarantine",
        )