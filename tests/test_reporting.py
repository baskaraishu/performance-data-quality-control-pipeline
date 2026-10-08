import csv
import hashlib
import json
from pathlib import Path

import pandas as pd
import pytest

from src.ingestion import EXPECTED_PERFORMANCE_COLUMNS
from src.reporting import build_run_report_data, generate_run_report, render_run_report_html


def make_report_data():
    return {
        "run_id": "learning-run",
        "outcome_label": "BLOCKED BY RELEASE GATE",
        "outcome_reason": "Gold publication was blocked.",
        "pipeline_status": "FAILED",
        "failed_stage": "quality_gate",
        "validation_status": "FAIL",
        "source_file": "synthetic.csv",
        "source_sha256": "abc123",
        "input_row_count": 100,
        "started_at_utc": "2026-09-29T10:00:00+00:00",
        "completed_at_utc": "2026-09-29T10:01:00+00:00",
        "profile": {
            "row_count": 100,
            "column_count": 24,
            "missing_cells": 2,
            "unparseable_values": 1,
            "exact_duplicate_groups": 1,
            "business_key_duplicate_groups": 2,
            "date_coverage": [],
        },
        "validation": {
            "issue_count": 3,
            "records_with_failures": 3,
            "failures_by_severity": {"Critical": 1, "High": 2},
        },
        "disposition_counts": {"eligible": 95, "quarantined": 3, "duplicate-held": 2},
        "top_failures": [
            {
                "rule_id": "REF-001",
                "severity": "Critical",
                "issue_count": 1,
                "affected_records": 1,
                "fields": ["establishment_id"],
                "description": "ID is not in the establishment reference.",
                "next_action": "Verify the ID with the source owner.",
            }
        ],
        "dimension_results": {
            "completeness": {
                "status": "SCORED",
                "score_pct": 98.0,
                "passed_records": 98,
                "failed_records": 2,
                "applicable_records": 100,
                "excluded_records": 0,
                "issue_count": 2,
            },
            "consistency": {
                "status": "NOT_AVAILABLE",
                "score_pct": None,
                "passed_records": None,
                "failed_records": None,
                "applicable_records": 0,
                "excluded_records": 100,
                "issue_count": 0,
            },
        },
        "overall_dq_score_pct": 98.0,
        "review_only_issue_count": 4,
        "reconciliation": {
            "overall_status": "PASS",
            "measures": {
                "population": {
                    "status": "PASS",
                    "raw": {"total": 1000, "missing_count": 1, "unparseable_count": 0},
                    "silver": {"total": 950, "missing_count": 0, "unparseable_count": 0},
                    "quarantined": {"total": 30, "missing_count": 1, "unparseable_count": 0},
                    "duplicate_held": {"total": 20, "missing_count": 0, "unparseable_count": 0},
                    "difference": 0,
                }
            },
        },
        "gate_decision": "BLOCKED",
        "gate_checks": [
            {
                "check": "critical_issues",
                "status": "FAIL",
                "actual": 1,
                "threshold": 0,
                "reason": "A Critical issue blocks release.",
            }
        ],
        "gold_status": "Not published: release gate blocked this run.",
        "gold_kpi_summary": None,
        "artifact_links": [{"label": "Profiling report", "href": "profiling_report.html"}],
        "synthetic_data_note": "All records and findings are synthetic.",
    }


def test_report_presents_decision_findings_and_next_investigation():
    report = render_run_report_html(make_report_data())

    assert "BLOCKED BY RELEASE GATE" in report
    assert "Validation and investigation priorities" in report
    assert "REF-001" in report
    assert "Verify the ID with the source owner" in report
    assert "Consistency" in report
    assert "Not available" in report
    assert "Reconciliation" in report
    assert "Critical" in report
    assert "Not published: release gate blocked this run" in report
    assert "Synthetic" in report


def test_report_escapes_dynamic_content():
    data = make_report_data()
    data["run_id"] = "<script>alert(1)</script>"
    data["outcome_reason"] = "<img src=x onerror=alert(1)>"
    data["top_failures"][0]["next_action"] = "<script>steal()</script>"

    report = render_run_report_html(data)

    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in report
    assert "&lt;img src=x onerror=alert(1)&gt;" in report
    assert "<script>steal()</script>" not in report


def make_minimal_report_project(tmp_path):
    root = tmp_path / "project"
    run_id = "report-test"
    bronze_dir = root / "data/bronze" / run_id
    silver_dir = root / "data/silver" / run_id
    quarantine_dir = root / "data/quarantine" / run_id
    reports_dir = root / "reports" / run_id
    for directory in (
        bronze_dir,
        silver_dir,
        quarantine_dir,
        reports_dir,
        root / "rules",
        root / "data/reference",
    ):
        directory.mkdir(parents=True, exist_ok=True)

    source_path = bronze_dir / "performance_dirty.csv"
    row = {column: "" for column in EXPECTED_PERFORMANCE_COLUMNS}
    row.update(
        {
            "establishment_id": "PR001",
            "establishment_name": "Synthetic North",
            "region": "North",
            "establishment_type": "Male Closed",
            "reporting_month": "2025-04-01",
            "reporting_period_end": "2025-04-30",
            "population": "100",
            "certified_normal_accommodation": "125",
            "assaults": "2",
            "self_harm_incidents": "3",
            "security_incidents": "1",
            "absconds": "0",
            "data_submission_date": "2025-05-05",
        }
    )
    with source_path.open("w", newline="", encoding="utf-8") as output:
        writer = csv.DictWriter(output, fieldnames=EXPECTED_PERFORMANCE_COLUMNS)
        writer.writeheader()
        writer.writerow(row)
    source_hash = hashlib.sha256(source_path.read_bytes()).hexdigest()
    metadata = {
        "run_id": run_id,
        "source_file": source_path.name,
        "source_sha256": source_hash,
        "row_count": 1,
        "column_count": len(EXPECTED_PERFORMANCE_COLUMNS),
    }
    (bronze_dir / "metadata.json").write_text(json.dumps(metadata), encoding="utf-8")

    (reports_dir / "pipeline_summary.json").write_text(
        json.dumps(
            {
                "run_id": run_id,
                "status": "FAILED",
                "failed_stage": "quality_gate",
                "error": "Gate blocked publication.",
                "started_at_utc": "2026-09-29T10:00:00+00:00",
                "completed_at_utc": "2026-09-29T10:01:00+00:00",
                "source_file": source_path.name,
                "source_sha256": source_hash,
                "input_row_count": 1,
                "quality_gate_inputs": {
                    "validation_status": "FAIL",
                    "quality_gate_decision": "BLOCKED",
                    "disposition_counts": {"eligible": 0, "quarantined": 1, "duplicate-held": 0},
                },
            }
        ),
        encoding="utf-8",
    )
    (silver_dir / "validation_summary.json").write_text(
        json.dumps(
            {
                "run_id": run_id,
                "source_file": source_path.name,
                "source_sha256": source_hash,
                "input_row_count": 1,
                "issue_count": 1,
                "records_with_failures": 1,
                "failures_by_severity": {"Critical": 1},
                "status": "FAIL",
            }
        ),
        encoding="utf-8",
    )
    pd.DataFrame(
        [
            {
                "run_id": run_id,
                "issue_id": "report-test:performance_dirty.csv:2:REF-001",
                "source_record_id": "performance_dirty.csv:2",
                "rule_id": "REF-001",
                "field": "establishment_id",
                "severity": "Critical",
                "action": "quarantine",
            }
        ]
    ).to_csv(silver_dir / "validation_issues.csv", index=False)
    (quarantine_dir / "summary.json").write_text(
        json.dumps(
            {
                "run_id": run_id,
                "source_file": source_path.name,
                "source_sha256": source_hash,
                "disposition_counts": {"eligible": 0, "quarantined": 1, "duplicate-held": 0},
            }
        ),
        encoding="utf-8",
    )
    (reports_dir / "dq_score_summary.json").write_text(
        json.dumps(
            {
                "run_id": run_id,
                "overall_score_pct": 90.0,
                "review_only_issue_count": 0,
                "dimension_results": {},
            }
        ),
        encoding="utf-8",
    )
    (reports_dir / "reconciliation.json").write_text(
        json.dumps({"overall_status": "PASS", "measures": {}}), encoding="utf-8"
    )
    (reports_dir / "quality_gate.json").write_text(
        json.dumps(
            {
                "run_id": run_id,
                "decision": "BLOCKED",
                "checks": [
                    {
                        "check": "critical_issues",
                        "status": "FAIL",
                        "actual": 1,
                        "threshold": 0,
                        "reason": "Critical issue present.",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    (root / "rules/quality_rules.yml").write_text(
        "schema_version: 1\nrules:\n  - rule_id: REF-001\n    columns: [establishment_id]\n    check: reference_exists\n    parameters: {reference_file: data/reference/establishments.csv, reference_key: establishment_id}\n    severity: Critical\n    action: quarantine\n    description: Establishment ID must be in reference.\n",
        encoding="utf-8",
    )
    pd.DataFrame(
        [{"establishment_id": "PR001"}]
    ).to_csv(root / "data/reference/establishments.csv", index=False)
    return root, run_id


def test_generate_run_report_uses_machine_readable_inputs(tmp_path):
    root, run_id = make_minimal_report_project(tmp_path)

    report_path = generate_run_report(run_id, project_root=root)
    html = report_path.read_text(encoding="utf-8")

    assert report_path.name == "run_report.html"
    assert "BLOCKED BY RELEASE GATE" in html
    assert "Critical issue present" in html
    assert "Verify the identifier against the establishment reference" in html
    assert "Not published: release gate blocked this run" in html
    assert "Source SHA-256" in html
    assert "Synthetic" in html
