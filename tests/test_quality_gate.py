import json
from pathlib import Path

import pandas as pd
import pytest

from src.quality_gate import (
    QualityGateError,
    evaluate_quality_gate,
    load_gate_config,
    run_quality_gate,
)

RUN_ID = "gate-test"
CONFIG = load_gate_config("rules/quality_gate.yml")


def make_inputs(issues=None, score=95.0, reconciliation_status="PASS", input_count=200):
    issues = issues or [
        {
            "run_id": RUN_ID,
            "source_record_id": "input.csv:2",
            "severity": "High",
            "action": "flag",
        }
    ]
    validation_summary = {
        "run_id": RUN_ID,
        "input_row_count": input_count,
        "issue_count": len(issues),
        "failures_by_severity": {
            severity: sum(issue["severity"] == severity for issue in issues)
            for severity in ("Critical", "High", "Medium", "Low")
        },
    }
    validation_issues = pd.DataFrame(
        issues, columns=["run_id", "source_record_id", "severity", "action"]
    )
    dq_summary = {"run_id": RUN_ID, "overall_score_pct": score}
    reconciliation = {"run_id": RUN_ID, "overall_status": reconciliation_status}
    return validation_summary, validation_issues, dq_summary, reconciliation


def test_gate_passes_at_inclusive_high_rate_and_score_thresholds():
    result = evaluate_quality_gate(RUN_ID, *make_inputs(), CONFIG)

    checks = {check["check"]: check for check in result["checks"]}
    assert result["decision"] == "PASSED"
    assert result["release_allowed"] is True
    assert checks["high_severity_record_rate_pct"]["actual"] == 0.5
    assert checks["high_severity_record_rate_pct"]["status"] == "PASS"
    assert checks["minimum_overall_dq_score_pct"]["actual"] == 95.0
    assert checks["minimum_overall_dq_score_pct"]["status"] == "PASS"


def test_any_critical_issue_blocks_even_with_high_score():
    issues = [
        {
            "run_id": RUN_ID,
            "source_record_id": "input.csv:2",
            "severity": "Critical",
            "action": "quarantine",
        }
    ]
    result = evaluate_quality_gate(RUN_ID, *make_inputs(issues=issues, score=99.9), CONFIG)
    critical = next(check for check in result["checks"] if check["check"] == "critical_issues")

    assert result["decision"] == "BLOCKED"
    assert result["release_allowed"] is False
    assert critical["actual"] == 1
    assert critical["status"] == "FAIL"


def test_high_failure_rate_above_threshold_blocks():
    issues = [
        {
            "run_id": RUN_ID,
            "source_record_id": f"input.csv:{row_number}",
            "severity": "High",
            "action": "flag",
        }
        for row_number in (2, 3)
    ]
    result = evaluate_quality_gate(RUN_ID, *make_inputs(issues=issues), CONFIG)

    high_check = next(
        check for check in result["checks"]
        if check["check"] == "high_severity_record_rate_pct"
    )
    assert high_check["actual"] == 1.0
    assert high_check["status"] == "FAIL"
    assert result["decision"] == "BLOCKED"


def test_low_score_blocks():
    result = evaluate_quality_gate(RUN_ID, *make_inputs(score=94.99), CONFIG)
    score_check = next(
        check for check in result["checks"]
        if check["check"] == "minimum_overall_dq_score_pct"
    )

    assert score_check["status"] == "FAIL"
    assert result["decision"] == "BLOCKED"


def test_reconciliation_failure_blocks():
    result = evaluate_quality_gate(
        RUN_ID, *make_inputs(reconciliation_status="FAIL"), CONFIG
    )
    reconciliation_check = next(
        check for check in result["checks"] if check["check"] == "reconciliation"
    )

    assert reconciliation_check["status"] == "FAIL"
    assert result["decision"] == "BLOCKED"


def test_gate_reports_multiple_blocking_reasons_and_keeps_flag_only_issues_visible():
    issues = [
        {
            "run_id": RUN_ID,
            "source_record_id": "input.csv:2",
            "severity": "Critical",
            "action": "quarantine",
        },
        {
            "run_id": RUN_ID,
            "source_record_id": "input.csv:3",
            "severity": "High",
            "action": "flag",
        },
        {
            "run_id": RUN_ID,
            "source_record_id": "input.csv:4",
            "severity": "High",
            "action": "flag",
        },
    ]
    result = evaluate_quality_gate(
        RUN_ID,
        *make_inputs(issues=issues, score=90, reconciliation_status="FAIL"),
        CONFIG,
    )
    failed_checks = [check["check"] for check in result["checks"] if check["status"] == "FAIL"]

    assert result["decision"] == "BLOCKED"
    assert set(failed_checks) == {
        "critical_issues",
        "high_severity_record_rate_pct",
        "minimum_overall_dq_score_pct",
        "reconciliation",
    }
    assert result["flag_only_issue_count"] == 2
    assert all(check["reason"] for check in result["checks"])


def test_unavailable_score_blocks_closed():
    result = evaluate_quality_gate(RUN_ID, *make_inputs(score=None), CONFIG)
    score_check = next(
        check for check in result["checks"]
        if check["check"] == "minimum_overall_dq_score_pct"
    )

    assert score_check["status"] == "FAIL"
    assert result["decision"] == "BLOCKED"


def test_gate_run_writes_json_decision(tmp_path):
    validation_dir = tmp_path / "silver" / RUN_ID
    reports_dir = tmp_path / "reports" / RUN_ID
    validation_dir.mkdir(parents=True)
    reports_dir.mkdir(parents=True)
    validation_summary, issues, dq_summary, reconciliation = make_inputs()
    validation_summary.update({"source_sha256": "abc"})
    dq_summary.update({"source_sha256": "abc"})
    reconciliation.update({"source_sha256": "abc"})
    validation_summary_path = validation_dir / "validation_summary.json"
    validation_summary_path.write_text(json.dumps(validation_summary), encoding="utf-8")
    issues.to_csv(validation_dir / "validation_issues.csv", index=False)
    (reports_dir / "dq_score_summary.json").write_text(
        json.dumps(dq_summary), encoding="utf-8"
    )
    (reports_dir / "reconciliation.json").write_text(
        json.dumps(reconciliation), encoding="utf-8"
    )

    result, output_path = run_quality_gate(
        RUN_ID,
        validation_root=tmp_path / "silver",
        reports_root=tmp_path / "reports",
        config_path=Path("rules/quality_gate.yml"),
        evaluated_at_utc="2026-09-29T00:00:00+00:00",
    )

    saved = json.loads(output_path.read_text(encoding="utf-8"))
    assert output_path.name == "quality_gate.json"
    assert result["decision"] == saved["decision"] == "PASSED"
    assert saved["evaluated_at_utc"] == "2026-09-29T00:00:00+00:00"
