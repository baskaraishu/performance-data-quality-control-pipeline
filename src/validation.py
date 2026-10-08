"""Apply configured data-quality rules to an ingested Bronze run."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
from collections import Counter, defaultdict
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

import pandas as pd

from src.rule_config import RuleConfigError, load_rule_config

ISSUE_COLUMNS = [
    "issue_id",
    "run_id",
    "source_record_id",
    "rule_id",
    "field",
    "observed_value",
    "failure_reason",
    "severity",
    "action",
    "status",
    "detected_timestamp_utc",
]
_RUN_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")


class ValidationError(ValueError):
    """Raised when a run cannot be validated with the configured rules."""


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _is_blank(value: Any) -> bool:
    return value is None or str(value).strip() == ""


def _as_decimal(value: Any) -> Decimal | None:
    if _is_blank(value):
        return None
    try:
        parsed = Decimal(str(value).strip())
    except (InvalidOperation, ValueError):
        return None
    return parsed if parsed.is_finite() else None


def _date_is_valid(value: Any, parameters: dict[str, Any]) -> bool:
    if _is_blank(value):
        return True
    raw_value = str(value).strip()
    date_format = parameters["format"]
    try:
        parsed = datetime.strptime(raw_value, date_format).date()
    except (TypeError, ValueError):
        return False
    if parsed.strftime(date_format) != raw_value:
        return False
    return not parameters.get("first_day_of_month", False) or parsed.day == 1


def _reference_values(
    rule: dict[str, Any], references: dict[str, pd.DataFrame]
) -> set[str]:
    parameters = rule["parameters"]
    reference_file = parameters["reference_file"]
    reference_column = parameters.get("reference_key") or parameters.get(
        "reference_column"
    )
    reference_frame = references[reference_file]
    return {
        str(value).strip()
        for value in reference_frame[reference_column].tolist()
        if not _is_blank(value)
    }


def _failed_fields_for_row(
    rule: dict[str, Any],
    row: dict[str, Any],
    references: dict[str, pd.DataFrame],
    duplicate_keys: set[tuple[str, ...]],
) -> dict[str, tuple[Any, str]]:
    check = rule["check"]
    parameters = rule["parameters"]
    failed: dict[str, tuple[Any, str]] = {}

    if check == "unique_combination":
        values = tuple(str(row.get(column, "")).strip() for column in rule["columns"])
        if all(values) and values in duplicate_keys:
            fields = ", ".join(rule["columns"])
            return {
                fields: (
                    {column: row.get(column, "") for column in rule["columns"]},
                    "Combination is present in more than one source record.",
                )
            }
        return failed

    reference_values = None
    if check in {"reference_exists", "reference_column_values"}:
        reference_values = _reference_values(rule, references)

    for column in rule["columns"]:
        value = row.get(column, "")

        if check == "not_null":
            if _is_blank(value):
                failed[column] = (value, "Value is required but is blank.")
        elif check == "valid_iso_date":
            if not _date_is_valid(value, parameters):
                requirement = "a valid date"
                if parameters.get("first_day_of_month"):
                    requirement = "a valid date on the first day of the month"
                failed[column] = (value, f"Value must be {requirement} in the configured format.")
        elif check == "non_negative_integer":
            parsed = _as_decimal(value)
            if not _is_blank(value) and (
                parsed is None or parsed < 0 or parsed != parsed.to_integral_value()
            ):
                failed[column] = (value, "Value must be a whole number greater than or equal to zero.")
        elif check == "non_negative_number":
            parsed = _as_decimal(value)
            if not _is_blank(value) and (parsed is None or parsed < 0):
                failed[column] = (value, "Value must be a number greater than or equal to zero.")
        elif check == "between":
            parsed = _as_decimal(value)
            if not _is_blank(value) and (
                parsed is None
                or parsed < Decimal(str(parameters["min"]))
                or parsed > Decimal(str(parameters["max"]))
            ):
                failed[column] = (
                    value,
                    f"Value must be between {parameters['min']} and {parameters['max']} inclusive.",
                )
        elif check == "greater_than":
            parsed = _as_decimal(value)
            if not _is_blank(value) and (
                parsed is None or parsed <= Decimal(str(parameters["value"]))
            ):
                failed[column] = (
                    value,
                    f"Value must be greater than {parameters['value']}.",
                )
        elif check == "exceeds":
            parsed = _as_decimal(value)
            if not _is_blank(value) and parsed is not None and parsed > Decimal(
                str(parameters["value"])
            ):
                failed[column] = (
                    value,
                    f"Value exceeds {parameters['value']} and should be reviewed.",
                )
        elif check == "reference_exists":
            if not _is_blank(value) and str(value).strip() not in reference_values:
                failed[column] = (value, "Value was not found in the reference key.")
        elif check == "reference_column_values":
            if not _is_blank(value) and str(value).strip() not in reference_values:
                failed[column] = (value, "Value was not found in the reference values.")
        else:
            raise ValidationError(f"Unsupported validation check: {check}")

    return failed


def evaluate_records(
    frame: pd.DataFrame,
    rules: list[dict[str, Any]],
    references: dict[str, pd.DataFrame] | None = None,
    run_id: str = "test-run",
    source_file: str = "input.csv",
    detected_timestamp_utc: str | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Evaluate rules in memory; emit one issue per failed rule and source row."""
    references = references or {}
    detected_timestamp_utc = detected_timestamp_utc or datetime.now(
        timezone.utc
    ).isoformat()
    missing_columns = sorted(
        {column for rule in rules for column in rule["columns"]} - set(frame.columns)
    )
    if missing_columns:
        raise ValidationError(
            f"Source CSV is missing rule columns: {', '.join(missing_columns)}"
        )

    references_needed = {
        rule["parameters"]["reference_file"]
        for rule in rules
        if rule["check"] in {"reference_exists", "reference_column_values"}
    }
    missing_references = sorted(references_needed - references.keys())
    if missing_references:
        raise ValidationError(
            f"Reference data was not loaded: {', '.join(missing_references)}"
        )
    for rule in rules:
        if rule["check"] not in {
            "not_null",
            "valid_iso_date",
            "non_negative_integer",
            "non_negative_number",
            "between",
            "greater_than",
            "exceeds",
            "reference_exists",
            "reference_column_values",
            "unique_combination",
        }:
            raise ValidationError(
                f"Rule {rule.get('rule_id', '<unknown>')} has unsupported check "
                f"'{rule['check']}'."
            )

    duplicate_keys: set[tuple[str, ...]] = set()
    for rule in rules:
        if rule["check"] != "unique_combination":
            continue
        counts = Counter(
            tuple(str(value).strip() for value in row)
            for row in frame[rule["columns"]].itertuples(index=False, name=None)
            if all(not _is_blank(value) for value in row)
        )
        duplicate_keys.update(key for key, count in counts.items() if count > 1)

    issues: list[dict[str, Any]] = []
    failed_records: set[str] = set()
    counts_by_rule: dict[str, int] = defaultdict(int)
    counts_by_severity: dict[str, int] = defaultdict(int)
    counts_by_action: dict[str, int] = defaultdict(int)

    for source_row_number, values in enumerate(
        frame.to_dict(orient="records"), start=2
    ):
        source_record_id = f"{source_file}:{source_row_number}"
        for rule in rules:
            failed_fields = _failed_fields_for_row(
                rule, values, references, duplicate_keys
            )
            if not failed_fields:
                continue

            field_names = list(failed_fields)
            observed_values = {
                field: evidence[0] for field, evidence in failed_fields.items()
            }
            reasons = [
                f"{field}: {evidence[1]}" for field, evidence in failed_fields.items()
            ]
            issue = {
                "issue_id": f"{run_id}:{source_record_id}:{rule['rule_id']}",
                "run_id": run_id,
                "source_record_id": source_record_id,
                "rule_id": rule["rule_id"],
                "field": ";".join(field_names),
                "observed_value": json.dumps(
                    observed_values, ensure_ascii=False, sort_keys=True
                ),
                "failure_reason": " ".join(reasons),
                "severity": rule["severity"],
                "action": rule["action"],
                "status": "FAIL" if rule["action"] == "quarantine" else "WARNING",
                "detected_timestamp_utc": detected_timestamp_utc,
            }
            issues.append(issue)
            failed_records.add(source_record_id)
            counts_by_rule[rule["rule_id"]] += 1
            counts_by_severity[rule["severity"]] += 1
            counts_by_action[rule["action"]] += 1

    if counts_by_action.get("quarantine", 0):
        status = "FAIL"
    elif issues:
        status = "WARNING"
    else:
        status = "PASS"

    summary = {
        "run_id": run_id,
        "source_file": source_file,
        "input_row_count": int(len(frame)),
        "rule_count": len(rules),
        "rule_record_checks": int(len(frame) * len(rules)),
        "issue_count": len(issues),
        "records_with_failures": len(failed_records),
        "status": status,
        "failures_by_rule": dict(sorted(counts_by_rule.items())),
        "failures_by_severity": dict(sorted(counts_by_severity.items())),
        "failures_by_action": dict(sorted(counts_by_action.items())),
    }
    return issues, summary


def _load_references(
    rules: list[dict[str, Any]], project_root: Path
) -> dict[str, pd.DataFrame]:
    reference_paths = {
        rule["parameters"]["reference_file"]
        for rule in rules
        if rule["check"] in {"reference_exists", "reference_column_values"}
    }
    references: dict[str, pd.DataFrame] = {}
    for reference_file in reference_paths:
        path = Path(reference_file)
        if not path.is_absolute():
            path = project_root / path
        if not path.is_file():
            raise ValidationError(f"Reference file does not exist: {path}")
        try:
            reference_frame = pd.read_csv(
                path, dtype=str, keep_default_na=False, encoding="utf-8-sig"
            )
        except (OSError, UnicodeError, pd.errors.ParserError) as error:
            raise ValidationError(f"Could not read reference file '{path}': {error}") from error

        required_columns = {
            column
            for rule in rules
            if rule["parameters"].get("reference_file") == reference_file
            for column in (
                rule["parameters"].get("reference_key"),
                rule["parameters"].get("reference_column"),
            )
            if column
        }
        missing_columns = sorted(required_columns - set(reference_frame.columns))
        if missing_columns:
            raise ValidationError(
                f"Reference file '{path}' is missing columns: "
                f"{', '.join(missing_columns)}"
            )
        references[reference_file] = reference_frame
    return references


def validate_run(
    run_id: str,
    bronze_root: str | Path = "data/bronze",
    rules_path: str | Path = "rules/quality_rules.yml",
    output_root: str | Path = "data/silver",
    project_root: str | Path = ".",
    detected_timestamp_utc: str | None = None,
) -> tuple[dict[str, Any], Path, Path]:
    """Validate a run-specific Bronze CSV and write issue and summary outputs."""
    if not _RUN_ID_PATTERN.fullmatch(run_id):
        raise ValidationError("run_id contains unsupported characters.")

    run_directory = Path(bronze_root) / run_id
    metadata_path = run_directory / "metadata.json"
    if not metadata_path.is_file():
        raise ValidationError(f"Run metadata does not exist: {metadata_path}")
    try:
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ValidationError(f"Could not read run metadata: {error}") from error
    if metadata.get("run_id") != run_id:
        raise ValidationError("Metadata run_id does not match the requested run.")

    source_file = metadata.get("source_file")
    if not isinstance(source_file, str) or Path(source_file).name != source_file:
        raise ValidationError("Metadata has an invalid source filename.")
    bronze_file = run_directory / source_file
    if not bronze_file.is_file():
        raise ValidationError(f"Bronze CSV does not exist: {bronze_file}")
    if _sha256(bronze_file) != metadata.get("source_sha256"):
        raise ValidationError("Bronze CSV hash does not match the run metadata.")

    try:
        frame = pd.read_csv(
            bronze_file,
            dtype=str,
            keep_default_na=False,
            encoding="utf-8-sig",
        )
    except (OSError, UnicodeError, pd.errors.ParserError) as error:
        raise ValidationError(f"Could not parse Bronze CSV: {error}") from error
    if len(frame) != metadata.get("row_count"):
        raise ValidationError("Bronze CSV row count does not match run metadata.")
    if len(frame.columns) != metadata.get("column_count"):
        raise ValidationError("Bronze CSV column count does not match run metadata.")

    try:
        configuration = load_rule_config(rules_path)
    except RuleConfigError as error:
        raise ValidationError(f"Invalid quality-rule configuration: {error}") from error
    rules = configuration["rules"]
    references = _load_references(rules, Path(project_root))
    issues, summary = evaluate_records(
        frame,
        rules,
        references=references,
        run_id=run_id,
        source_file=source_file,
        detected_timestamp_utc=detected_timestamp_utc,
    )
    summary["source_sha256"] = metadata["source_sha256"]
    summary["schema_version"] = metadata["schema_version"]
    summary["detected_timestamp_utc"] = (
        issues[0]["detected_timestamp_utc"]
        if issues
        else detected_timestamp_utc or datetime.now(timezone.utc).isoformat()
    )

    output_directory = Path(output_root) / run_id
    output_directory.mkdir(parents=True, exist_ok=True)
    issues_path = output_directory / "validation_issues.csv"
    summary_path = output_directory / "validation_summary.json"
    pd.DataFrame(issues, columns=ISSUE_COLUMNS).to_csv(
        issues_path, index=False, encoding="utf-8"
    )
    summary_path.write_text(
        json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return summary, issues_path, summary_path


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Apply configured data-quality rules to a Bronze run."
    )
    parser.add_argument("--run-id", required=True, help="Existing Bronze run ID")
    parser.add_argument("--bronze-root", default="data/bronze")
    parser.add_argument("--rules", default="rules/quality_rules.yml")
    parser.add_argument("--output-root", default="data/silver")
    parser.add_argument("--project-root", default=".")
    args = parser.parse_args()

    try:
        summary, issues_path, summary_path = validate_run(
            run_id=args.run_id,
            bronze_root=args.bronze_root,
            rules_path=args.rules,
            output_root=args.output_root,
            project_root=args.project_root,
        )
    except ValidationError as error:
        parser.error(str(error))

    print(
        json.dumps(
            {
                **summary,
                "issues_path": str(issues_path),
                "summary_path": str(summary_path),
            },
            indent=2,
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()