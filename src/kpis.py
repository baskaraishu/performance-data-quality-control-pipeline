"""Calculate supported KPIs and produce traceable Gold datasets."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd
import yaml

from src.ingestion import EXPECTED_PERFORMANCE_COLUMNS
from src.quality_scoring import QualityScoringError

KPI_RESULT_COLUMNS = [
    "run_id",
    "source_record_id",
    "establishment_id",
    "region",
    "establishment_type",
    "reporting_month",
    "kpi_name",
    "actual_value",
    "numerator_value",
    "denominator_value",
    "target_value",
    "target_direction",
    "target_variance",
    "unit",
    "availability_status",
    "comparison_status",
    "trust_status",
    "trust_reason",
    "formula",
    "relevant_dq_dimensions",
    "relevant_dq_scores",
    "related_issue_ids",
]
KPI_AVAILABILITY = {"CALCULATED", "SUPPLIED_ONLY", "DEFINITION_UNCLEAR"}
DQ_DIMENSIONS = {
    "completeness",
    "validity",
    "uniqueness",
    "consistency",
    "referential_integrity",
    "timeliness",
}
REVIEW_ONLY_RULE_IDS = {"REVIEW-001"}
INTEGER_COLUMNS = {
    "population",
    "certified_normal_accommodation",
    "assaults",
    "self_harm_incidents",
    "security_incidents",
    "absconds",
    "average_staff",
    "staff_sickness_days",
    "submission_delay_days",
}
DECIMAL_COLUMNS = {
    "occupancy_pct",
    "assaults_per_100",
    "self_harm_per_100",
    "security_incidents_per_100",
    "education_attendance_pct",
    "purposeful_activity_pct",
    "staff_sickness_rate_pct",
    "staff_resignation_rate_pct",
}
DATE_COLUMNS = {
    "reporting_month",
    "reporting_period_end",
    "data_submission_date",
}


class KPIError(ValueError):
    """Raised when KPI mappings or run inputs are invalid."""


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _number(value: Any) -> float | None:
    if value is None or value is pd.NA or str(value).strip() == "":
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if math.isfinite(parsed) else None


def _issue_fields(issue: dict[str, Any]) -> set[str]:
    raw_fields = str(issue.get("field", ""))
    return {
        field.strip()
        for field in raw_fields.replace(";", ",").split(",")
        if field.strip()
    }


def _validate_mapping_document(
    document: Any,
    targets: pd.DataFrame,
    source_fields: set[str],
) -> dict[str, Any]:
    if not isinstance(document, dict) or document.get("schema_version") != 1:
        raise KPIError("KPI mapping must be a YAML mapping with schema_version 1.")
    mappings = document.get("kpis")
    if not isinstance(mappings, list) or not mappings:
        raise KPIError("KPI mapping must contain a non-empty kpis list.")
    context_fields = document.get("context_fields")
    if not isinstance(context_fields, list) or set(context_fields) - source_fields:
        raise KPIError("KPI context_fields must refer to available performance columns.")

    target_names = targets["kpi_name"].astype(str).tolist()
    mapped_names = [mapping.get("kpi_name") for mapping in mappings]
    if len(mapped_names) != len(set(mapped_names)):
        raise KPIError("KPI mappings contain duplicate target names.")
    if set(mapped_names) != set(target_names):
        raise KPIError("Each target KPI must have exactly one explicit mapping.")

    for mapping in mappings:
        name = mapping["kpi_name"]
        method = mapping.get("method")
        availability = mapping.get("availability")
        if method not in {"ratio", "supplied"}:
            raise KPIError(f"KPI '{name}' has an unsupported method.")
        if availability not in KPI_AVAILABILITY:
            raise KPIError(f"KPI '{name}' has an unsupported availability status.")
        if not isinstance(mapping.get("target_comparable"), bool):
            raise KPIError(f"KPI '{name}' must define target_comparable as true/false.")
        if not isinstance(mapping.get("unit"), str) or not mapping["unit"].strip():
            raise KPIError(f"KPI '{name}' must define a unit.")
        if not isinstance(mapping.get("quality_dimensions"), list) or set(
            mapping["quality_dimensions"]
        ) - DQ_DIMENSIONS:
            raise KPIError(f"KPI '{name}' refers to unsupported DQ dimensions.")
        if mapping.get("output_field") not in source_fields:
            raise KPIError(f"KPI '{name}' output_field is missing from the Silver schema.")

        if method == "ratio":
            if availability != "CALCULATED":
                raise KPIError(f"Ratio KPI '{name}' must have CALCULATED availability.")
            required = {
                mapping.get("numerator_field"),
                mapping.get("denominator_field"),
            }
            if None in required or required - source_fields:
                raise KPIError(f"KPI '{name}' has missing ratio input fields.")
            multiplier = _number(mapping.get("multiplier"))
            if multiplier is None or multiplier <= 0:
                raise KPIError(f"KPI '{name}' must have a positive multiplier.")
            if not mapping.get("target_comparable"):
                raise KPIError(f"Calculated KPI '{name}' should be target-comparable.")
        elif mapping.get("supplied_field") not in source_fields:
            raise KPIError(f"Supplied KPI '{name}' has a missing supplied_field.")
    return document


def load_kpi_mappings(
    mapping_path: str | Path,
    targets: pd.DataFrame,
    source_fields: set[str],
) -> dict[str, Any]:
    path = Path(mapping_path)
    try:
        with path.open(encoding="utf-8") as source:
            document = yaml.safe_load(source)
    except OSError as error:
        raise KPIError(f"Could not read KPI mapping '{path}': {error}") from error
    except yaml.YAMLError as error:
        raise KPIError(f"Invalid YAML in KPI mapping '{path}': {error}") from error
    return _validate_mapping_document(document, targets, source_fields)


def cast_performance_types(frame: pd.DataFrame) -> pd.DataFrame:
    """Convert Silver CSV values to documented nullable types without imputing."""
    typed = frame.copy()
    for column in INTEGER_COLUMNS & set(typed.columns):
        numeric = pd.to_numeric(typed[column].replace("", pd.NA), errors="coerce")
        typed[column] = numeric.astype("Int64")
    for column in DECIMAL_COLUMNS & set(typed.columns):
        typed[column] = pd.to_numeric(
            typed[column].replace("", pd.NA), errors="coerce"
        ).astype("Float64")
    for column in DATE_COLUMNS & set(typed.columns):
        typed[column] = pd.to_datetime(typed[column].replace("", pd.NA), errors="coerce")
    return typed


def calculate_kpi_rows(
    performance: pd.DataFrame,
    targets: pd.DataFrame,
    mappings: dict[str, Any],
    validation_issues: pd.DataFrame,
    dq_score_summary: dict[str, Any],
    run_id: str,
) -> pd.DataFrame:
    """Create one establishment/month/KPI result with target and trust metadata."""
    if not {"source_record_id", "establishment_id", "reporting_month"}.issubset(
        performance.columns
    ):
        raise KPIError("Silver performance data is missing required lineage/key fields.")
    target_rows = targets.set_index("kpi_name").to_dict(orient="index")
    issues_by_record: dict[str, list[dict[str, Any]]] = {}
    if not validation_issues.empty:
        for issue in validation_issues.to_dict(orient="records"):
            issues_by_record.setdefault(str(issue["source_record_id"]), []).append(issue)

    dimension_scores = dq_score_summary.get("dimension_results", {})
    context_fields = set(mappings["context_fields"])
    results: list[dict[str, Any]] = []

    for record in performance.to_dict(orient="records"):
        source_record_id = str(record["source_record_id"])
        record_issues = issues_by_record.get(source_record_id, [])
        for mapping in mappings["kpis"]:
            target = target_rows[mapping["kpi_name"]]
            source_fields = (
                [mapping["numerator_field"], mapping["denominator_field"]]
                if mapping["method"] == "ratio"
                else [mapping["supplied_field"]]
            )
            support_fields = set(source_fields)
            relevant_fields = support_fields | context_fields | {
                mapping["output_field"]
            }
            relevant_issues = [
                issue
                for issue in record_issues
                if _issue_fields(issue) & relevant_fields
            ]
            support_issues = [
                issue
                for issue in record_issues
                if _issue_fields(issue) & support_fields
                and str(issue.get("rule_id", "")) not in REVIEW_ONLY_RULE_IDS
            ]
            context_issues = [
                issue
                for issue in relevant_issues
                if _issue_fields(issue) & context_fields
            ]

            numerator_value = None
            denominator_value = None
            actual_value = None
            availability_status = mapping["availability"]
            trust_status = "REVIEW_REQUIRED"
            reason = mapping.get("reason", "")

            if mapping["method"] == "ratio":
                numerator = _number(record.get(mapping["numerator_field"]))
                denominator = _number(record.get(mapping["denominator_field"]))
                missing_fields = [
                    field
                    for field in source_fields
                    if record.get(field) is None or str(record.get(field)).strip() == ""
                ]
                if missing_fields:
                    availability_status = "UNAVAILABLE_MISSING_INPUT"
                    reason = "Required input is missing: " + ", ".join(missing_fields)
                elif numerator is None or denominator is None:
                    availability_status = "UNAVAILABLE_INVALID_INPUT"
                    reason = "A required input is not a finite number."
                elif denominator <= 0:
                    availability_status = "UNAVAILABLE_INVALID_INPUT"
                    reason = "The denominator must be greater than zero."
                elif support_issues:
                    availability_status = "UNAVAILABLE_INVALID_INPUT"
                    reason = "A required input has a related validation issue."
                else:
                    numerator_value = numerator
                    denominator_value = denominator
                    actual_value = round(
                        numerator / denominator * float(mapping["multiplier"]), 2
                    )
                    availability_status = "CALCULATED"
                    reason = mapping["formula"]

                if availability_status.startswith("UNAVAILABLE"):
                    trust_status = "UNAVAILABLE"
                elif relevant_issues:
                    trust_status = "REVIEW_REQUIRED"
                else:
                    trust_status = "TRUSTED"
            else:
                supplied = _number(record.get(mapping["supplied_field"]))
                supplied_field = mapping["supplied_field"]
                if record.get(supplied_field) is None or str(
                    record.get(supplied_field)
                ).strip() == "":
                    availability_status = "UNAVAILABLE_MISSING_INPUT"
                    reason = f"Supplied value '{supplied_field}' is missing."
                    trust_status = "UNAVAILABLE"
                elif supplied is None or support_issues:
                    availability_status = "UNAVAILABLE_INVALID_INPUT"
                    reason = f"Supplied value '{supplied_field}' is invalid."
                    trust_status = "UNAVAILABLE"
                else:
                    actual_value = supplied
                    trust_status = "REVIEW_REQUIRED"

            comparable = bool(mapping["target_comparable"])
            target_value = _number(target.get("target_value"))
            if not comparable:
                comparison_status = "NOT_COMPARABLE"
                target_variance = None
            elif actual_value is None or target_value is None:
                comparison_status = "UNAVAILABLE"
                target_variance = None
            else:
                comparison_status = "COMPARED"
                target_variance = round(actual_value - target_value, 2)

            linked_issue_ids = sorted(
                str(issue.get("issue_id", ""))
                for issue in relevant_issues
                if issue.get("issue_id")
            )
            relevant_scores = {
                dimension: {
                    "score_pct": dimension_scores.get(dimension, {}).get("score_pct"),
                    "status": dimension_scores.get(dimension, {}).get(
                        "status", "NOT_AVAILABLE"
                    ),
                }
                for dimension in mapping["quality_dimensions"]
            }
            results.append(
                {
                    "run_id": run_id,
                    "source_record_id": source_record_id,
                    "establishment_id": record.get("establishment_id"),
                    "region": record.get("region"),
                    "establishment_type": record.get("establishment_type"),
                    "reporting_month": record.get("reporting_month"),
                    "kpi_name": mapping["kpi_name"],
                    "actual_value": actual_value,
                    "numerator_value": numerator_value,
                    "denominator_value": denominator_value,
                    "target_value": target_value,
                    "target_direction": target.get("direction"),
                    "target_variance": target_variance,
                    "unit": mapping["unit"],
                    "availability_status": availability_status,
                    "comparison_status": comparison_status,
                    "trust_status": trust_status,
                    "trust_reason": reason,
                    "formula": mapping.get("formula", "Supplied value; formula not independently verified."),
                    "relevant_dq_dimensions": ",".join(mapping["quality_dimensions"]),
                    "relevant_dq_scores": json.dumps(
                        relevant_scores, sort_keys=True, separators=(",", ":")
                    ),
                    "related_issue_ids": json.dumps(
                        linked_issue_ids, separators=(",", ":")
                    ),
                }
            )

    return pd.DataFrame(results, columns=KPI_RESULT_COLUMNS)


def aggregate_calculated_kpi(
    kpi_results: pd.DataFrame,
    kpi_name: str,
    multiplier: float,
) -> dict[str, Any]:
    """Aggregate a ratio KPI from summed numerators and denominators."""
    all_rows = kpi_results.loc[kpi_results["kpi_name"] == kpi_name]
    selected = all_rows.loc[all_rows["availability_status"] == "CALCULATED"]
    usable = selected.dropna(subset=["numerator_value", "denominator_value"])
    denominator_total = pd.to_numeric(usable["denominator_value"], errors="coerce").sum()
    numerator_total = pd.to_numeric(usable["numerator_value"], errors="coerce").sum()
    if usable.empty or denominator_total <= 0:
        return {
            "kpi_name": kpi_name,
            "status": "UNAVAILABLE",
            "actual_value": None,
            "numerator_total": None,
            "denominator_total": None,
            "included_records": 0,
            "excluded_records": len(all_rows),
            "excluded_by_availability": all_rows["availability_status"]
            .value_counts()
            .to_dict(),
        }
    return {
        "kpi_name": kpi_name,
        "status": "CALCULATED",
        "actual_value": round(float(numerator_total) / float(denominator_total) * multiplier, 2),
        "numerator_total": float(numerator_total),
        "denominator_total": float(denominator_total),
        "included_records": int(len(usable)),
        "excluded_records": int(len(all_rows) - len(usable)),
        "excluded_by_availability": all_rows.loc[
            all_rows["availability_status"] != "CALCULATED", "availability_status"
        ].value_counts().to_dict(),
    }


def _cast_performance_types(frame: pd.DataFrame) -> pd.DataFrame:
    typed = frame.copy()
    for column in INTEGER_COLUMNS & set(typed.columns):
        typed[column] = pd.to_numeric(
            typed[column].replace("", pd.NA), errors="coerce"
        ).astype("Int64")
    for column in DECIMAL_COLUMNS & set(typed.columns):
        typed[column] = pd.to_numeric(
            typed[column].replace("", pd.NA), errors="coerce"
        ).astype("Float64")
    for column in DATE_COLUMNS & set(typed.columns):
        typed[column] = pd.to_datetime(
            typed[column].replace("", pd.NA), errors="coerce"
        )
    return typed


def calculate_kpis_from_files(
    performance_path: str | Path,
    targets_path: str | Path,
    mapping_path: str | Path,
    issues_path: str | Path,
    dq_summary_path: str | Path,
    run_id: str,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    try:
        performance = pd.read_csv(
            performance_path, dtype=str, keep_default_na=False, encoding="utf-8-sig"
        )
        targets = pd.read_csv(
            targets_path, dtype=str, keep_default_na=False, encoding="utf-8-sig"
        )
        issues = pd.read_csv(
            issues_path, dtype=str, keep_default_na=False, encoding="utf-8-sig"
        )
        dq_summary = json.loads(Path(dq_summary_path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeError, pd.errors.ParserError) as error:
        raise KPIError(f"Could not read KPI inputs: {error}") from error

    if dq_summary.get("run_id") != run_id:
        raise KPIError("DQ score summary run_id does not match the KPI run.")
    try:
        mappings = load_kpi_mappings(mapping_path, targets, set(performance.columns))
    except (OSError, yaml.YAMLError) as error:
        raise KPIError(f"Could not read KPI mappings: {error}") from error
    except KPIError:
        raise

    typed_performance = _cast_performance_types(performance)
    kpi_rows = calculate_kpi_rows(
        performance,
        targets,
        mappings,
        issues,
        dq_summary,
        run_id,
    )
    return typed_performance, kpi_rows


def calculate_kpis_for_run(
    run_id: str,
    silver_root: str | Path = "data/silver",
    targets_path: str | Path = "data/reference/kpi_targets.csv",
    mapping_path: str | Path = "rules/kpi_mappings.yml",
    validation_root: str | Path = "data/silver",
    reports_root: str | Path = "reports",
    gold_root: str | Path = "data/gold",
) -> tuple[dict[str, Any], Path, Path]:
    silver_directory = Path(silver_root) / run_id
    cleaning_summary_path = silver_directory / "cleaning_summary.json"
    issues_path = Path(validation_root) / run_id / "validation_issues.csv"
    dq_summary_path = Path(reports_root) / run_id / "dq_score_summary.json"
    for path, description in (
        (cleaning_summary_path, "cleaning summary"),
        (issues_path, "validation issues"),
        (dq_summary_path, "DQ score summary"),
        (Path(targets_path), "KPI targets"),
        (Path(mapping_path), "KPI mappings"),
    ):
        if not path.is_file():
            raise KPIError(f"{description.title()} file does not exist: {path}")
    try:
        cleaning_summary = json.loads(cleaning_summary_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise KPIError(f"Could not read cleaning summary: {error}") from error
    if cleaning_summary.get("run_id") != run_id:
        raise KPIError("Cleaning summary run_id does not match.")

    performance_path = silver_directory / "performance_clean.csv"
    if not performance_path.is_file():
        raise KPIError(f"Clean Silver performance file does not exist: {performance_path}")
    if not {"source_sha256", "source_file"}.issubset(cleaning_summary):
        raise KPIError("Cleaning summary is missing source lineage metadata.")

    performance, kpi_results = calculate_kpis_from_files(
        performance_path=performance_path,
        targets_path=targets_path,
        mapping_path=mapping_path,
        issues_path=issues_path,
        dq_summary_path=dq_summary_path,
        run_id=run_id,
    )
    if len(performance) != cleaning_summary.get("clean_record_count"):
        raise KPIError("Silver performance row count does not match cleaning summary.")
    if len(kpi_results) != len(performance) * len(pd.read_csv(targets_path)):
        raise KPIError("KPI results do not contain one row per performance record and target.")

    gold_directory = Path(gold_root) / run_id
    gold_directory.mkdir(parents=True, exist_ok=True)
    performance_path_out = gold_directory / "performance.parquet"
    kpi_path_out = gold_directory / "kpi_results.parquet"
    performance.to_parquet(performance_path_out, index=False)
    kpi_results.to_parquet(kpi_path_out, index=False)
    summary = {
        "run_id": run_id,
        "source_file": cleaning_summary["source_file"],
        "source_sha256": cleaning_summary["source_sha256"],
        "performance_record_count": len(performance),
        "kpi_result_count": len(kpi_results),
        "kpi_names": sorted(kpi_results["kpi_name"].unique().tolist()),
        "availability_counts": kpi_results["availability_status"].value_counts().to_dict(),
        "trust_status_counts": kpi_results["trust_status"].value_counts().to_dict(),
        "outputs": {
            "performance": str(performance_path_out),
            "kpi_results": str(kpi_path_out),
        },
    }
    summary_path = gold_directory / "kpi_summary.json"
    summary_path.write_text(
        json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return summary, performance_path_out, kpi_path_out


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Calculate trusted KPIs and write Gold Parquet outputs."
    )
    parser.add_argument("--run-id", required=True, help="Existing cleaned and scored run ID")
    parser.add_argument("--silver-root", default="data/silver")
    parser.add_argument("--targets", default="data/reference/kpi_targets.csv")
    parser.add_argument("--mappings", default="rules/kpi_mappings.yml")
    parser.add_argument("--validation-root", default="data/silver")
    parser.add_argument("--reports-root", default="reports")
    parser.add_argument("--gold-root", default="data/gold")
    args = parser.parse_args()
    try:
        summary, performance_path, kpi_path = calculate_kpis_for_run(
            run_id=args.run_id,
            silver_root=args.silver_root,
            targets_path=args.targets,
            mapping_path=args.mappings,
            validation_root=args.validation_root,
            reports_root=args.reports_root,
            gold_root=args.gold_root,
        )
    except KPIError as error:
        parser.error(str(error))
    print(
        json.dumps(
            {
                **summary,
                "performance_path": str(performance_path),
                "kpi_results_path": str(kpi_path),
            },
            indent=2,
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()