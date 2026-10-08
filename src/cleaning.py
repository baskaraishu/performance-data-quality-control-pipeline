"""Create auditable Silver data from records eligible after Task 10."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd
import yaml

from src.ingestion import EXPECTED_PERFORMANCE_COLUMNS

POLICY_KEYS = {
    "schema_version",
    "missing_values",
    "duplicate_handling",
    "integer_columns",
    "decimal_columns",
    "date_columns",
    "category_mappings",
}
TRANSFORMATION_COLUMNS = [
    "run_id",
    "source_record_id",
    "field",
    "original_value",
    "cleaned_value",
    "method",
    "rule_applied",
]
CLEANING_ISSUE_COLUMNS = [
    "issue_id",
    "run_id",
    "source_record_id",
    "field",
    "original_value",
    "reason",
    "method",
]


class CleaningError(ValueError):
    """Raised when cleaning inputs or policy are incomplete or inconsistent."""


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def validate_cleaning_policies(
    policies: Any,
    reference_frame: pd.DataFrame,
) -> dict[str, Any]:
    if not isinstance(policies, dict):
        raise CleaningError("Cleaning policy must be a YAML mapping.")
    missing_keys = POLICY_KEYS - policies.keys()
    unknown_keys = policies.keys() - POLICY_KEYS
    if missing_keys:
        raise CleaningError(
            "Cleaning policy is missing keys: " + ", ".join(sorted(missing_keys))
        )
    if unknown_keys:
        raise CleaningError(
            "Cleaning policy has unsupported keys: " + ", ".join(sorted(unknown_keys))
        )
    if policies["schema_version"] != 1:
        raise CleaningError("Cleaning policy schema_version must be 1.")
    if policies["missing_values"] != "preserve":
        raise CleaningError("Missing values must be preserved in the first version.")
    if policies["duplicate_handling"] != "use_task10_disposition":
        raise CleaningError("Duplicate handling must use Task 10 dispositions.")

    type_groups = [
        policies["integer_columns"],
        policies["decimal_columns"],
        policies["date_columns"],
    ]
    if any(not isinstance(columns, list) for columns in type_groups):
        raise CleaningError("Numeric and date column policies must be lists.")
    configured_columns = [column for group in type_groups for column in group]
    if any(not isinstance(column, str) for column in configured_columns):
        raise CleaningError("Configured type fields must be strings.")
    if len(configured_columns) != len(set(configured_columns)):
        raise CleaningError("A field cannot have more than one configured output type.")
    unknown_columns = set(configured_columns) - set(EXPECTED_PERFORMANCE_COLUMNS)
    if unknown_columns:
        raise CleaningError(
            "Cleaning policy refers to unknown fields: " + ", ".join(sorted(unknown_columns))
        )

    mappings = policies["category_mappings"]
    if not isinstance(mappings, dict):
        raise CleaningError("category_mappings must be a mapping by field name.")
    for column, values in mappings.items():
        if column not in EXPECTED_PERFORMANCE_COLUMNS:
            raise CleaningError(f"Category mapping refers to unknown field '{column}'.")
        if column not in reference_frame.columns:
            raise CleaningError(f"Reference data has no '{column}' column.")
        if not isinstance(values, dict):
            raise CleaningError(f"Mappings for '{column}' must be a mapping.")
        canonical_values = {
            str(value).strip()
            for value in reference_frame[column].tolist()
            if str(value).strip()
        }
        for alias, canonical in values.items():
            if not isinstance(alias, str) or not isinstance(canonical, str):
                raise CleaningError(f"Mappings for '{column}' must use text values.")
            if alias == canonical:
                raise CleaningError(
                    f"Mapping for '{column}' must change an alias, not repeat a canonical value."
                )
            if canonical not in canonical_values:
                raise CleaningError(
                    f"Mapping target '{canonical}' is not in the reference values for '{column}'."
                )
    return policies


def load_cleaning_policies(
    path: str | Path,
    reference_frame: pd.DataFrame,
) -> dict[str, Any]:
    policy_path = Path(path)
    try:
        with policy_path.open(encoding="utf-8") as source:
            policies = yaml.safe_load(source)
    except OSError as error:
        raise CleaningError(f"Could not read cleaning policy '{policy_path}': {error}") from error
    except yaml.YAMLError as error:
        raise CleaningError(f"Invalid YAML in '{policy_path}': {error}") from error
    return validate_cleaning_policies(policies, reference_frame)


def _format_value(value: Any) -> str:
    if value is None or value is pd.NA or pd.isna(value):
        return ""
    if isinstance(value, (pd.Timestamp, datetime, date)):
        return value.isoformat()
    if isinstance(value, float):
        return format(value, ".15g")
    return str(value)


def clean_records(
    frame: pd.DataFrame,
    policies: dict[str, Any],
    run_id: str,
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """Type valid values, apply explicit aliases, and report parse failures."""
    if "source_record_id" not in frame.columns:
        raise CleaningError("Input records must include source_record_id for lineage.")

    expected_fields = (
        set(policies["integer_columns"])
        | set(policies["decimal_columns"])
        | set(policies["date_columns"])
        | set(policies["category_mappings"])
    )
    missing_fields = sorted(expected_fields - set(frame.columns))
    if missing_fields:
        raise CleaningError(
            "Input records are missing configured fields: " + ", ".join(missing_fields)
        )

    cleaned = frame.copy(deep=True)
    transformations: list[dict[str, str]] = []
    issues: list[dict[str, str]] = []

    def log_change(
        row_index: Any,
        column: str,
        original: Any,
        new_value: Any,
        method: str,
        rule_applied: str,
    ) -> None:
        transformations.append(
            {
                "run_id": run_id,
                "source_record_id": str(cleaned.at[row_index, "source_record_id"]),
                "field": column,
                "original_value": _format_value(original),
                "cleaned_value": _format_value(new_value),
                "method": method,
                "rule_applied": rule_applied,
            }
        )

    def log_parse_issue(
        row_index: Any, column: str, original: Any, reason: str, method: str
    ) -> None:
        source_record_id = str(cleaned.at[row_index, "source_record_id"])
        issue_id = f"{run_id}:{source_record_id}:CLEAN-{column}"
        issues.append(
            {
                "issue_id": issue_id,
                "run_id": run_id,
                "source_record_id": source_record_id,
                "field": column,
                "original_value": _format_value(original),
                "reason": reason,
                "method": method,
            }
        )
        log_change(row_index, column, original, pd.NA, method, "CLEAN-PARSE")

    integer_columns = set(policies["integer_columns"])
    decimal_columns = set(policies["decimal_columns"])
    for column in [*policies["integer_columns"], *policies["decimal_columns"]]:
        original_values = cleaned[column].copy()
        parsed_values: list[Any] = []
        for index, original in original_values.items():
            raw_value = _format_value(original).strip()
            if not raw_value:
                parsed_values.append(pd.NA)
                continue
            parsed = pd.to_numeric(raw_value, errors="coerce")
            invalid = pd.isna(parsed) or not math.isfinite(float(parsed))
            if column in integer_columns and not invalid:
                invalid = float(parsed).is_integer() is False
            if invalid:
                parsed_values.append(pd.NA)
                reason = (
                    "Value could not be parsed as a finite number."
                    if column in decimal_columns
                    else "Value could not be parsed as a whole number."
                )
                log_parse_issue(index, column, original, reason, "numeric_parse")
            else:
                if column in integer_columns:
                    parsed = int(parsed)
                else:
                    parsed = float(parsed)
                parsed_values.append(parsed)
                if _format_value(original).strip() != _format_value(parsed):
                    log_change(index, column, original, parsed, "numeric_parse", "TYPE-NUMERIC")

        dtype = "Int64" if column in integer_columns else "Float64"
        cleaned[column] = pd.Series(pd.array(parsed_values, dtype=dtype), index=cleaned.index)

    for column in policies["date_columns"]:
        original_values = cleaned[column].copy()
        parsed_values: list[Any] = []
        for index, original in original_values.items():
            raw_value = _format_value(original).strip()
            if not raw_value:
                parsed_values.append(pd.NaT)
                continue
            try:
                parsed = datetime.strptime(raw_value, "%Y-%m-%d").date()
            except ValueError:
                parsed_values.append(pd.NaT)
                log_parse_issue(
                    index,
                    column,
                    original,
                    "Value is not a valid ISO date (YYYY-MM-DD).",
                    "date_parse",
                )
            else:
                parsed_values.append(pd.Timestamp(parsed))

        cleaned[column] = pd.Series(parsed_values, index=cleaned.index, dtype="datetime64[ns]")

    for column, mappings in policies["category_mappings"].items():
        for index, original in cleaned[column].items():
            alias = _format_value(original)
            if alias in mappings:
                canonical = mappings[alias]
                cleaned.at[index, column] = canonical
                log_change(
                    index,
                    column,
                    original,
                    canonical,
                    "approved_category_mapping",
                    f"CATEGORY-{column}",
                )

    transformation_frame = pd.DataFrame(
        transformations, columns=TRANSFORMATION_COLUMNS
    )
    issue_frame = pd.DataFrame(issues, columns=CLEANING_ISSUE_COLUMNS)
    return cleaned, transformation_frame, issue_frame


def clean_run(
    run_id: str,
    bronze_root: str | Path = "data/bronze",
    quarantine_root: str | Path = "data/quarantine",
    reference_path: str | Path = "data/reference/establishments.csv",
    policy_path: str | Path = "rules/cleaning_policies.yml",
    output_root: str | Path = "data/silver",
    generated_at_utc: str | None = None,
) -> tuple[dict[str, Any], Path]:
    """Create clean Silver outputs from eligible records in a completed run."""
    if not run_id or any(char not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._-" for char in run_id):
        raise CleaningError("run_id contains unsupported characters.")

    bronze_directory = Path(bronze_root) / run_id
    metadata_path = bronze_directory / "metadata.json"
    if not metadata_path.is_file():
        raise CleaningError(f"Run metadata does not exist: {metadata_path}")
    try:
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise CleaningError(f"Could not read Bronze metadata: {error}") from error
    if metadata.get("run_id") != run_id:
        raise CleaningError("Bronze metadata run_id does not match the requested run.")

    source_file = metadata.get("source_file")
    if not isinstance(source_file, str) or Path(source_file).name != source_file:
        raise CleaningError("Bronze metadata has an invalid source filename.")
    bronze_file = bronze_directory / source_file
    if not bronze_file.is_file():
        raise CleaningError(f"Bronze CSV does not exist: {bronze_file}")
    source_hash = _sha256(bronze_file)
    if source_hash != metadata.get("source_sha256"):
        raise CleaningError("Bronze CSV hash does not match its metadata.")

    quarantine_directory = Path(quarantine_root) / run_id
    dispositions_path = quarantine_directory / "dispositions.csv"
    quarantine_summary_path = quarantine_directory / "summary.json"
    if not dispositions_path.is_file() or not quarantine_summary_path.is_file():
        raise CleaningError(f"Task 10 outputs are incomplete for run '{run_id}'.")
    try:
        quarantine_summary = json.loads(
            quarantine_summary_path.read_text(encoding="utf-8")
        )
        source_frame = pd.read_csv(
            bronze_file,
            dtype=str,
            keep_default_na=False,
            encoding="utf-8-sig",
        )
        dispositions = pd.read_csv(
            dispositions_path,
            dtype=str,
            keep_default_na=False,
            encoding="utf-8",
        )
    except (OSError, json.JSONDecodeError, UnicodeError, pd.errors.ParserError) as error:
        raise CleaningError(f"Could not read Task 10 inputs: {error}") from error
    if quarantine_summary.get("run_id") != run_id:
        raise CleaningError("Quarantine summary run_id does not match.")
    if quarantine_summary.get("source_sha256") != source_hash:
        raise CleaningError("Quarantine summary refers to different Bronze data.")
    if len(source_frame) != metadata.get("row_count"):
        raise CleaningError("Bronze row count does not match its metadata.")
    if len(dispositions) != len(source_frame):
        raise CleaningError("There must be exactly one disposition for every Bronze row.")
    if set(dispositions["run_id"].astype(str)) != {run_id}:
        raise CleaningError("Disposition rows must all belong to this run.")

    expected_record_ids = {
        f"{source_file}:{row_number}"
        for row_number in range(2, len(source_frame) + 2)
    }
    disposition_ids = dispositions["source_record_id"].astype(str).tolist()
    if set(disposition_ids) != expected_record_ids or len(set(disposition_ids)) != len(disposition_ids):
        raise CleaningError("Disposition source IDs do not match Bronze source rows exactly.")
    if set(dispositions["disposition"]) - {"eligible", "quarantined", "duplicate-held"}:
        raise CleaningError("Task 10 contains an unsupported record disposition.")

    reference_file = Path(reference_path)
    if not reference_file.is_file():
        raise CleaningError(f"Establishment reference does not exist: {reference_file}")
    try:
        reference_frame = pd.read_csv(
            reference_file,
            dtype=str,
            keep_default_na=False,
            encoding="utf-8-sig",
        )
    except (OSError, UnicodeError, pd.errors.ParserError) as error:
        raise CleaningError(f"Could not read establishment reference: {error}") from error
    try:
        policies = load_cleaning_policies(policy_path, reference_frame)
    except CleaningError:
        raise

    eligible_ids = set(
        dispositions.loc[
            dispositions["disposition"] == "eligible", "source_record_id"
        ].astype(str)
    )
    source_record_ids = [
        f"{source_file}:{row_number}"
        for row_number in range(2, len(source_frame) + 2)
    ]
    eligible_positions = [
        index for index, source_record_id in enumerate(source_record_ids)
        if source_record_id in eligible_ids
    ]
    eligible_frame = source_frame.iloc[eligible_positions].copy().reset_index(drop=True)
    eligible_frame.insert(0, "source_record_id", [source_record_ids[index] for index in eligible_positions])

    if {"establishment_id", "reporting_month"}.issubset(eligible_frame.columns):
        duplicated_keys = eligible_frame.duplicated(
            ["establishment_id", "reporting_month"], keep=False
        )
        if duplicated_keys.any():
            raise CleaningError("Eligible records still contain duplicate establishment-month keys.")

    cleaned, transformations, cleaning_issues = clean_records(
        eligible_frame, policies, run_id
    )
    generated_at_utc = generated_at_utc or datetime.now(timezone.utc).isoformat()
    output_directory = Path(output_root) / run_id
    output_directory.mkdir(parents=True, exist_ok=True)
    clean_path = output_directory / "performance_clean.csv"
    transformation_path = output_directory / "transformation_log.csv"
    issues_path = output_directory / "cleaning_issues.csv"
    summary_path = output_directory / "cleaning_summary.json"

    cleaned.to_csv(clean_path, index=False, encoding="utf-8")
    transformations.to_csv(transformation_path, index=False, encoding="utf-8")
    cleaning_issues.to_csv(issues_path, index=False, encoding="utf-8")
    summary = {
        "run_id": run_id,
        "source_file": source_file,
        "source_sha256": source_hash,
        "generated_at_utc": generated_at_utc,
        "input_record_count": len(source_frame),
        "eligible_record_count": len(eligible_frame),
        "excluded_record_count": len(source_frame) - len(eligible_frame),
        "clean_record_count": len(cleaned),
        "transformation_count": len(transformations),
        "cleaning_issue_count": len(cleaning_issues),
        "outputs": {
            "performance_clean": str(clean_path),
            "transformation_log": str(transformation_path),
            "cleaning_issues": str(issues_path),
        },
    }
    summary_path.write_text(
        json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return summary, summary_path


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Create an auditable Silver dataset from Task 10 eligible records."
    )
    parser.add_argument("--run-id", required=True, help="Existing run ID")
    parser.add_argument("--bronze-root", default="data/bronze")
    parser.add_argument("--quarantine-root", default="data/quarantine")
    parser.add_argument(
        "--reference", default="data/reference/establishments.csv"
    )
    parser.add_argument("--policies", default="rules/cleaning_policies.yml")
    parser.add_argument("--output-root", default="data/silver")
    args = parser.parse_args()

    try:
        summary, summary_path = clean_run(
            run_id=args.run_id,
            bronze_root=args.bronze_root,
            quarantine_root=args.quarantine_root,
            reference_path=args.reference,
            policy_path=args.policies,
            output_root=args.output_root,
        )
    except CleaningError as error:
        parser.error(str(error))

    print(
        json.dumps(
            {**summary, "summary_path": str(summary_path)}, indent=2, sort_keys=True
        )
    )


if __name__ == "__main__":
    main()