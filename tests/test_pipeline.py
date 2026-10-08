import json
import shutil
from pathlib import Path

import pandas as pd
import pytest
import yaml

from src.ingestion import EXPECTED_PERFORMANCE_COLUMNS
from src.pipeline import (
    PipelineError,
    convert_pipeline_summary_file,
    render_pipeline_summary_html,
    run_pipeline,
)

TARGETS = [
    ("Occupancy %", "Lower", 95.0, "Population / Certified accommodation * 100"),
    ("Education attendance %", "Higher", 75.0, "Attended sessions / Scheduled sessions * 100"),
    ("Purposeful activity %", "Higher", 75.0, "Activity participation rate"),
    ("Assaults per 100", "Lower", 3.5, "Assaults / population * 100"),
    ("Self-harm per 100", "Lower", 6.0, "Self-harm incidents / population * 100"),
    ("Security incidents per 100", "Lower", 2.0, "Security incidents / population * 100"),
    ("Staff sickness rate %", "Lower", 5.0, "Sickness days / available staff days * 100"),
    ("Staff resignation rate %", "Lower", 8.0, "Resignations / average staff * 100"),
]


def make_row(establishment_id, reporting_month, reporting_period_end):
    row = {column: "" for column in EXPECTED_PERFORMANCE_COLUMNS}
    row.update(
        {
            "establishment_id": establishment_id,
            "establishment_name": "Synthetic One",
            "region": "North",
            "establishment_type": "Male Closed",
            "reporting_month": reporting_month,
            "reporting_period_end": reporting_period_end,
            "population": "100",
            "certified_normal_accommodation": "125",
            "occupancy_pct": "80",
            "assaults": "2",
            "assaults_per_100": "2",
            "self_harm_incidents": "3",
            "self_harm_per_100": "3",
            "security_incidents": "1",
            "security_incidents_per_100": "1",
            "absconds": "0",
            "education_attendance_pct": "70",
            "purposeful_activity_pct": "75",
            "average_staff": "50",
            "staff_sickness_days": "10",
            "staff_sickness_rate_pct": "4",
            "staff_resignation_rate_pct": "5",
            "data_submission_date": "2025-05-05",
            "submission_delay_days": "5",
        }
    )
    return row


def make_temporary_project(tmp_path, invalid_rules=False, dirty_input=False):
    project = tmp_path / "project"
    (project / "data/raw").mkdir(parents=True)
    (project / "data/reference").mkdir(parents=True)
    (project / "rules").mkdir(parents=True)

    source_path = project / "data/raw/performance_dirty.csv"
    if dirty_input:
        rows = [
            make_row("PR001", "2025-04-01", "2025-04-30"),
            make_row("PR001", "2025-05-01", "2025-05-31"),
            make_row("PR999", "2025-06-01", "2025-06-30"),
            make_row("PR001", "2025-04-01", "2025-04-30"),
        ]
    else:
        months = [
            ("2025-04-01", "2025-04-30"),
            ("2025-05-01", "2025-05-31"),
            ("2025-06-01", "2025-06-30"),
            ("2025-07-01", "2025-07-31"),
        ]
        rows = [make_row("PR001", month, period_end) for month, period_end in months]
        rows[0]["occupancy_pct"] = "101"
    pd.DataFrame(rows, columns=EXPECTED_PERFORMANCE_COLUMNS).to_csv(
        source_path, index=False
    )

    pd.DataFrame(
        [
            {
                "establishment_id": "PR001",
                "establishment_name": "Synthetic One",
                "region": "North",
                "establishment_type": "Male Closed",
                "design_capacity": 130,
                "certified_normal_accommodation": 125,
            }
        ]
    ).to_csv(project / "data/reference/establishments.csv", index=False)
    pd.DataFrame(
        TARGETS,
        columns=["kpi_name", "direction", "target_value", "definition"],
    ).to_csv(project / "data/reference/kpi_targets.csv", index=False)

    repository_root = Path(__file__).resolve().parents[1]
    for filename in (
        "quality_rules.yml",
        "cleaning_policies.yml",
        "quality_dimensions.yml",
        "quality_gate.yml",
        "kpi_mappings.yml",
    ):
        shutil.copyfile(
            repository_root / "rules" / filename,
            project / "rules" / filename,
        )

    if invalid_rules:
        rules_path = project / "rules/quality_rules.yml"
        config = yaml.safe_load(rules_path.read_text(encoding="utf-8"))
        config["rules"][0]["check"] = "not_a_real_check"
        rules_path.write_text(yaml.safe_dump(config), encoding="utf-8")

    return project


def test_end_to_end_pipeline_writes_gold_and_run_summary_in_temp_project(tmp_path):
    project = make_temporary_project(tmp_path)

    summary = run_pipeline("pipeline-e2e", project_root=project)

    assert summary["status"] == "SUCCEEDED"
    assert [stage["stage"] for stage in summary["stages"]] == [
        "ingestion",
        "profiling",
        "validation",
        "quarantine",
        "cleaning",
        "reconciliation",
        "quality_scoring",
        "quality_gate",
        "kpi_generation",
        "gold_publish",
    ]
    assert summary["quality_gate_inputs"]["reconciliation_status"] == "PASS"
    assert summary["quality_gate_inputs"]["disposition_counts"] == {
        "eligible": 4,
        "quarantined": 0,
        "duplicate-held": 0,
    }
    assert summary["quality_gate_inputs"]["quality_gate_decision"] == "PASSED"
    assert summary["quality_gate_result"]["decision"] == "PASSED"

    run_directory = project / "data/gold/pipeline-e2e"
    performance = pd.read_parquet(run_directory / "performance.parquet")
    kpi_results = pd.read_parquet(run_directory / "kpi_results.parquet")
    saved_summary = json.loads(
        (project / "reports/pipeline-e2e/pipeline_summary.json").read_text(
            encoding="utf-8"
        )
    )
    assert len(performance) == 4
    assert len(kpi_results) == 32
    assert set(kpi_results["source_record_id"]) == {
        "performance_dirty.csv:2",
        "performance_dirty.csv:3",
        "performance_dirty.csv:4",
        "performance_dirty.csv:5",
    }
    assert saved_summary["status"] == "SUCCEEDED"
    assert (project / "reports/pipeline-e2e/pipeline_summary.html").is_file()
    assert not (project / "data/gold/.staging/pipeline-e2e").exists()


def test_pipeline_failure_stops_before_gold_publication(tmp_path):
    project = make_temporary_project(tmp_path, invalid_rules=True)

    with pytest.raises(PipelineError, match="failed during validation"):
        run_pipeline("pipeline-fail", project_root=project)

    failure_summary = json.loads(
        (project / "reports/pipeline-fail/pipeline_summary.json").read_text(
            encoding="utf-8"
        )
    )
    assert failure_summary["status"] == "FAILED"
    assert failure_summary["failed_stage"] == "validation"
    assert failure_summary["gold_published"] is False
    assert (project / "reports/pipeline-fail/pipeline_summary.html").is_file()
    assert not (project / "data/gold/pipeline-fail").exists()
    assert not (project / "data/gold/.staging/pipeline-fail").exists()


def test_quality_gate_blocks_dirty_run_before_gold_publication(tmp_path):
    project = make_temporary_project(tmp_path, dirty_input=True)

    with pytest.raises(PipelineError, match="Quality gate blocked Gold publication"):
        run_pipeline("pipeline-gate-block", project_root=project)

    gate_result = json.loads(
        (project / "reports/pipeline-gate-block/quality_gate.json").read_text(
            encoding="utf-8"
        )
    )
    pipeline_summary = json.loads(
        (project / "reports/pipeline-gate-block/pipeline_summary.json").read_text(
            encoding="utf-8"
        )
    )
    assert gate_result["decision"] == "BLOCKED"
    assert any(
        check["check"] == "critical_issues" and check["status"] == "FAIL"
        for check in gate_result["checks"]
    )
    assert pipeline_summary["failed_stage"] == "quality_gate"
    assert pipeline_summary["gold_published"] is False
    assert not (project / "data/gold/pipeline-gate-block").exists()
    assert not (project / "data/gold/.staging/pipeline-gate-block").exists()


def test_pipeline_summary_html_handles_failure_and_escapes_dynamic_text(tmp_path):
    failure = {
        "run_id": "<script>alert(1)</script>",
        "status": "FAILED",
        "failed_stage": "validation",
        "error": "<img src=x onerror=alert(1)>",
        "completed_stages": [
            {"stage": "ingestion", "status": "SUCCEEDED", "outputs": ["data/file.csv"]}
        ],
        "quality_gate_inputs": {
            "validation_status": "FAIL",
            "validation_failures_by_severity": {"Critical": 1},
            "disposition_counts": {"eligible": 2, "quarantined": 1, "duplicate-held": 0},
            "reconciliation_status": "NOT_RUN",
            "overall_dq_score_pct": 90,
        },
        "input_row_count": 3,
        "source_file": "<raw>.csv",
        "source_sha256": "abc123",
        "started_at_utc": "2026-09-29T00:00:00+00:00",
        "completed_at_utc": "2026-09-29T00:01:00+00:00",
        "gold_output_files": [],
    }
    report = render_pipeline_summary_html(failure)
    summary_path = tmp_path / "pipeline_summary.json"
    summary_path.write_text(json.dumps(failure), encoding="utf-8")

    html_path = convert_pipeline_summary_file(summary_path)
    html_report = html_path.read_text(encoding="utf-8")

    assert "FAILED" in report
    assert "validation" in report
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in report
    assert "<script>alert(1)</script>" not in report
    assert "&lt;img src=x onerror=alert(1)&gt;" in html_report
    assert html_path == tmp_path / "pipeline_summary.html"
