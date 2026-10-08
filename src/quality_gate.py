"""Evaluate release readiness from validation, DQ scoring, and reconciliation."""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd
import yaml

REQUIRED_CONFIG_KEYS = {
    "schema_version",
    "block_on_any_critical_issue",
    "maximum_high_severity_record_rate_pct",
    "minimum_overall_dq_score_pct",
    "require_reconciliation_pass",
}


class QualityGateError(ValueError):
    """Raised when the gate cannot safely evaluate its inputs or policy."""


def validate_gate_config(config: Any) -> dict[str, Any]:
    if not isinstance(config, dict):
        raise QualityGateError("Quality-gate configuration must be a YAML mapping.")
    missing = REQUIRED_CONFIG_KEYS - config.keys()
    unknown = config.keys() - REQUIRED_CONFIG_KEYS
    if missing:
        raise QualityGateError(
            "Quality-gate configuration is missing keys: "
            + ", ".join(sorted(missing))
        )
    if unknown:
        raise QualityGateError(
            "Quality-gate configuration has unsupported keys: "
            + ", ".join(sorted(unknown))
        )
    if config["schema_version"] != 1:
        raise QualityGateError("Quality-gate schema_version must be 1.")
    for key in ("block_on_any_critical_issue", "require_reconciliation_pass"):
        if not isinstance(config[key], bool):
            raise QualityGateError(f"{key} must be true or false.")
    for key in (
        "maximum_high_severity_record_rate_pct",
        "minimum_overall_dq_score_pct",
    ):
        value = config[key]
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise QualityGateError(f"{key} must be a number.")
        if not 0 <= value <= 100:
            raise QualityGateError(f"{key} must be between 0 and 100.")
    return config


def load_gate_config(path: str | Path = "rules/quality_gate.yml") -> dict[str, Any]:
    config_path = Path(path)
    try:
        with config_path.open(encoding="utf-8") as source:
            config = yaml.safe_load(source)
    except OSError as error:
        raise QualityGateError(
            f"Could not read quality-gate config '{config_path}': {error}"
        ) from error
    except yaml.YAMLError as error:
        raise QualityGateError(
            f"Invalid YAML in quality-gate config '{config_path}': {error}"
        ) from error
    return validate_gate_config(config)


def evaluate_quality_gate(
    run_id: str,
    validation_summary: dict[str, Any],
    validation_issues: pd.DataFrame,
    dq_summary: dict[str, Any],
    reconciliation: dict[str, Any],
    config: dict[str, Any],
    evaluated_at_utc: str | None = None,
) -> dict[str, Any]:
    """Return a deterministic PASSED/BLOCKED decision with evidence per check."""
    config = validate_gate_config(config)
    for label, summary in (
        ("validation", validation_summary),
        ("DQ score", dq_summary),
        ("reconciliation", reconciliation),
    ):
        if summary.get("run_id") != run_id:
            raise QualityGateError(f"{label} summary run_id does not match '{run_id}'.")

    required_issue_columns = {"run_id", "source_record_id", "severity"}
    missing_columns = required_issue_columns - set(validation_issues.columns)
    if missing_columns:
        raise QualityGateError(
            "Validation issues are missing columns: "
            + ", ".join(sorted(missing_columns))
        )
    if not validation_issues.empty and set(validation_issues["run_id"].astype(str)) != {run_id}:
        raise QualityGateError("Validation issues contain records from another run.")

    input_count = validation_summary.get("input_row_count")
    if not isinstance(input_count, int) or input_count < 0:
        raise QualityGateError("Validation summary has an invalid input_row_count.")
    if len(validation_issues) != validation_summary.get("issue_count"):
        raise QualityGateError("Validation issue count does not match its summary.")

    critical_issue_count = int(
        validation_issues["severity"].astype(str).eq("Critical").sum()
    )
    critical_blocked = (
        bool(config["block_on_any_critical_issue"]) and critical_issue_count > 0
    )
    checks = [
        {
            "check": "critical_issues",
            "status": "FAIL" if critical_blocked else "PASS",
            "actual": critical_issue_count,
            "threshold": 0 if config["block_on_any_critical_issue"] else "not blocking",
            "reason": (
                f"Found {critical_issue_count} Critical validation issue rows."
                if critical_blocked
                else "No blocking Critical validation issues were found."
            ),
        }
    ]

    high_issues = validation_issues.loc[
        validation_issues["severity"].astype(str) == "High"
    ]
    high_affected_records = int(high_issues["source_record_id"].astype(str).nunique())
    if input_count == 0:
        high_rate = None
        high_rate_passes = False
        high_reason = "Cannot calculate a High-severity rate for a run with no source records."
    else:
        high_rate = high_affected_records / input_count * 100
        high_rate_passes = high_rate <= config["maximum_high_severity_record_rate_pct"]
        high_reason = (
            f"{high_affected_records} of {input_count} source records have at least one High issue."
        )
    checks.append(
        {
            "check": "high_severity_record_rate_pct",
            "status": "PASS" if high_rate_passes else "FAIL",
            "actual": round(high_rate, 4) if high_rate is not None else None,
            "threshold": config["maximum_high_severity_record_rate_pct"],
            "reason": high_reason,
        }
    )

    overall_score = dq_summary.get("overall_score_pct")
    minimum_score = config["minimum_overall_dq_score_pct"]
    score_passes = (
        isinstance(overall_score, (int, float))
        and not isinstance(overall_score, bool)
        and overall_score >= minimum_score
    )
    checks.append(
        {
            "check": "minimum_overall_dq_score_pct",
            "status": "PASS" if score_passes else "FAIL",
            "actual": overall_score,
            "threshold": minimum_score,
            "reason": (
                f"Overall DQ score is {overall_score}%."
                if score_passes
                else "Overall DQ score is unavailable or below the configured minimum."
            ),
        }
    )

    reconciliation_status = reconciliation.get("overall_status")
    reconciliation_passes = (
        reconciliation_status == "PASS"
        if config["require_reconciliation_pass"]
        else True
    )
    checks.append(
        {
            "check": "reconciliation",
            "status": "PASS" if reconciliation_passes else "FAIL",
            "actual": reconciliation_status,
            "threshold": "PASS" if config["require_reconciliation_pass"] else "not required",
            "reason": (
                "Reconciliation passed."
                if reconciliation_passes
                else "Reconciliation did not pass; Gold publication is blocked."
            ),
        }
    )

    decision = "PASSED" if all(check["status"] == "PASS" for check in checks) else "BLOCKED"
    return {
        "run_id": run_id,
        "decision": decision,
        "release_allowed": decision == "PASSED",
        "evaluated_at_utc": evaluated_at_utc or datetime.now(timezone.utc).isoformat(),
        "input_record_count": input_count,
        "flag_only_issue_count": int(
            validation_issues.get("action", pd.Series(dtype=str)).astype(str).eq("flag").sum()
        ),
        "checks": checks,
        "policy": config,
        "policy_note": "Numeric thresholds are project assumptions, not official MoJ/HMPPS standards.",
    }


def run_quality_gate(
    run_id: str,
    validation_root: str | Path = "data/silver",
    reports_root: str | Path = "reports",
    config_path: str | Path = "rules/quality_gate.yml",
    evaluated_at_utc: str | None = None,
) -> tuple[dict[str, Any], Path]:
    validation_directory = Path(validation_root) / run_id
    report_directory = Path(reports_root) / run_id
    validation_summary_path = validation_directory / "validation_summary.json"
    issues_path = validation_directory / "validation_issues.csv"
    dq_summary_path = report_directory / "dq_score_summary.json"
    reconciliation_path = report_directory / "reconciliation.json"
    for path in (validation_summary_path, issues_path, dq_summary_path, reconciliation_path):
        if not path.is_file():
            raise QualityGateError(f"Required quality-gate input does not exist: {path}")
    try:
        validation_summary = json.loads(validation_summary_path.read_text(encoding="utf-8"))
        dq_summary = json.loads(dq_summary_path.read_text(encoding="utf-8"))
        reconciliation = json.loads(reconciliation_path.read_text(encoding="utf-8"))
        issues = pd.read_csv(issues_path, dtype=str, keep_default_na=False, encoding="utf-8-sig")
    except (OSError, json.JSONDecodeError, UnicodeError, pd.errors.ParserError) as error:
        raise QualityGateError(f"Could not read quality-gate inputs: {error}") from error

    config = load_gate_config(config_path)
    result = evaluate_quality_gate(
        run_id,
        validation_summary,
        issues,
        dq_summary,
        reconciliation,
        config,
        evaluated_at_utc=evaluated_at_utc,
    )
    result["source_sha256"] = validation_summary.get("source_sha256")
    report_directory.mkdir(parents=True, exist_ok=True)
    output_path = report_directory / "quality_gate.json"
    output_path.write_text(
        json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return result, output_path


def main() -> None:
    parser = argparse.ArgumentParser(description="Evaluate whether a run may publish Gold outputs.")
    parser.add_argument("--run-id", required=True, help="Existing scored and reconciled run ID")
    parser.add_argument("--validation-root", default="data/silver")
    parser.add_argument("--reports-root", default="reports")
    parser.add_argument("--config", default="rules/quality_gate.yml")
    args = parser.parse_args()
    try:
        result, output_path = run_quality_gate(
            args.run_id,
            validation_root=args.validation_root,
            reports_root=args.reports_root,
            config_path=args.config,
        )
    except QualityGateError as error:
        parser.error(str(error))
    print(json.dumps({**result, "gate_result_path": str(output_path)}, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
