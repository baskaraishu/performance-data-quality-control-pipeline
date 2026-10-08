"""Reconcile input records and additive measures across pipeline outputs."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd

MEASURE_COLUMNS = (
    "population",
    "assaults",
    "self_harm_incidents",
    "security_incidents",
    "absconds",
    "staff_sickness_days",
)
DISPOSITIONS = ("eligible", "quarantined", "duplicate-held")


class ReconciliationError(ValueError):
    """Raised when a run's source and pipeline outputs cannot be compared."""


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _measure_values(frame: pd.DataFrame, column: str) -> dict[str, Any]:
    if column not in frame.columns:
        return {
            "available": False,
            "total": None,
            "parseable_count": 0,
            "missing_count": 0,
            "unparseable_count": 0,
            "nonblank_count": 0,
        }

    original = frame[column].fillna("").astype(str).str.strip()
    missing = original.eq("")
    numeric = pd.to_numeric(original.mask(missing), errors="coerce")
    unparseable = ~missing & numeric.isna()
    parseable = numeric.notna()
    total = numeric.sum(min_count=1)
    return {
        "available": True,
        "total": float(total) if pd.notna(total) else None,
        "parseable_count": int(parseable.sum()),
        "missing_count": int(missing.sum()),
        "unparseable_count": int(unparseable.sum()),
        "nonblank_count": int((~missing).sum()),
    }


def _cleaning_issue_counts(cleaning_issues: pd.DataFrame) -> dict[str, int]:
    if cleaning_issues.empty or "field" not in cleaning_issues.columns:
        return {}
    return {
        str(field): int(count)
        for field, count in cleaning_issues["field"].value_counts().items()
    }


def reconcile_records(
    source_frame: pd.DataFrame,
    silver_frame: pd.DataFrame,
    blocked_frame: pd.DataFrame,
    dispositions_frame: pd.DataFrame,
    source_file: str,
    cleaning_issues: pd.DataFrame | None = None,
) -> dict[str, Any]:
    """Compare source row identities, dispositions, and selected measure totals."""
    cleaning_issues = cleaning_issues if cleaning_issues is not None else pd.DataFrame()
    required_disposition_columns = {"source_record_id", "disposition"}
    if missing := required_disposition_columns - set(dispositions_frame.columns):
        raise ReconciliationError(
            "Disposition file is missing columns: " + ", ".join(sorted(missing))
        )
    if "source_record_id" not in silver_frame.columns:
        raise ReconciliationError("Silver data is missing source_record_id.")
    if not {"source_record_id", "disposition"}.issubset(blocked_frame.columns):
        raise ReconciliationError(
            "Blocked records must include source_record_id and disposition."
        )

    expected_ids = {
        f"{source_file}:{row_number}"
        for row_number in range(2, len(source_frame) + 2)
    }
    disposition_ids = dispositions_frame["source_record_id"].astype(str).tolist()
    silver_ids = silver_frame["source_record_id"].astype(str).tolist()
    blocked_ids = blocked_frame["source_record_id"].astype(str).tolist()
    disposition_values = set(dispositions_frame["disposition"].astype(str))
    unsupported = disposition_values - set(DISPOSITIONS)

    disposition_map = dict(
        zip(
            dispositions_frame["source_record_id"].astype(str),
            dispositions_frame["disposition"].astype(str),
        )
    )
    if len(disposition_map) != len(disposition_ids):
        disposition_duplicate_ids = True
    else:
        disposition_duplicate_ids = False

    expected_silver_ids = {
        source_record_id
        for source_record_id, disposition in disposition_map.items()
        if disposition == "eligible"
    }
    expected_blocked_ids = expected_ids - expected_silver_ids
    actual_silver_ids = set(silver_ids)
    actual_blocked_ids = set(blocked_ids)

    disposition_counts = {
        disposition: int(
            dispositions_frame["disposition"].astype(str).eq(disposition).sum()
        )
        for disposition in DISPOSITIONS
    }
    quarantined_frame = blocked_frame.loc[
        blocked_frame["disposition"].astype(str) == "quarantined"
    ]
    duplicate_held_frame = blocked_frame.loc[
        blocked_frame["disposition"].astype(str) == "duplicate-held"
    ]

    checks = {
        "one_disposition_per_source_record": {
            "status": "PASS"
            if set(disposition_ids) == expected_ids
            and not disposition_duplicate_ids
            and not unsupported
            else "FAIL",
            "input_count": len(source_frame),
            "disposition_rows": len(dispositions_frame),
            "unknown_dispositions": sorted(unsupported),
            "duplicate_disposition_ids": disposition_duplicate_ids,
        },
        "record_count_equation": {
            "status": "PASS"
            if len(source_frame)
            == sum(disposition_counts.values())
            == len(silver_frame) + len(blocked_frame)
            and len(silver_frame) == disposition_counts["eligible"]
            and len(blocked_frame)
            == disposition_counts["quarantined"]
            + disposition_counts["duplicate-held"]
            else "FAIL",
            "input_rows": len(source_frame),
            "eligible_rows": disposition_counts["eligible"],
            "quarantined_rows": disposition_counts["quarantined"],
            "duplicate_held_rows": disposition_counts["duplicate-held"],
            "silver_rows": len(silver_frame),
            "blocked_rows": len(blocked_frame),
            "equation": "input = eligible + quarantined + duplicate-held",
        },
        "source_record_partition": {
            "status": "PASS"
            if actual_silver_ids.isdisjoint(actual_blocked_ids)
            and actual_silver_ids == expected_silver_ids
            and actual_blocked_ids == expected_blocked_ids
            and set(disposition_ids) == expected_ids
            else "FAIL",
            "missing_silver_ids": sorted(expected_silver_ids - actual_silver_ids),
            "unexpected_silver_ids": sorted(actual_silver_ids - expected_silver_ids),
            "missing_blocked_ids": sorted(expected_blocked_ids - actual_blocked_ids),
            "unexpected_blocked_ids": sorted(actual_blocked_ids - expected_blocked_ids),
            "overlap_count": len(actual_silver_ids & actual_blocked_ids),
        },
        "blocked_row_dispositions_match": {
            "status": "PASS"
            if all(
                disposition_map.get(source_record_id)
                == disposition
                for source_record_id, disposition in zip(
                    blocked_ids,
                    blocked_frame["disposition"].astype(str).tolist(),
                )
            )
            else "FAIL",
            "blocked_rows": len(blocked_frame),
        },
    }

    parse_issue_counts = _cleaning_issue_counts(cleaning_issues)
    measure_reconciliations: dict[str, Any] = {}
    for column in MEASURE_COLUMNS:
        if column not in source_frame.columns:
            measure_reconciliations[column] = {
                "status": "NOT_AVAILABLE",
                "reason": "Measure is not present in the source schema.",
            }
            continue

        raw_measure = _measure_values(source_frame, column)
        silver_measure = _measure_values(silver_frame, column)
        quarantined_measure = _measure_values(quarantined_frame, column)
        duplicate_held_measure = _measure_values(duplicate_held_frame, column)
        output_total = None
        group_totals = [
            silver_measure["total"],
            quarantined_measure["total"],
            duplicate_held_measure["total"],
        ]
        if any(total is not None for total in group_totals):
            output_total = sum(total or 0 for total in group_totals)
        if raw_measure["total"] is None and output_total is None:
            difference = 0.0
        elif raw_measure["total"] is None or output_total is None:
            difference = None
        else:
            difference = raw_measure["total"] - output_total

        raw_nonblank = raw_measure["nonblank_count"]
        output_nonblank = sum(
            measure["nonblank_count"]
            for measure in (
                silver_measure,
                quarantined_measure,
                duplicate_held_measure,
            )
        )
        explained_parse_losses = parse_issue_counts.get(column, 0)
        unexplained_nonblank_difference = (
            raw_nonblank - output_nonblank - explained_parse_losses
        )
        totals_match = difference is not None and math.isclose(
            difference, 0.0, rel_tol=0.0, abs_tol=1e-9
        )
        value_presence_matches = unexplained_nonblank_difference == 0
        status = "PASS" if totals_match and value_presence_matches else "FAIL"
        reasons = []
        if not totals_match:
            reasons.append(
                "Raw total does not equal Silver plus blocked total for parseable values."
            )
        if not value_presence_matches:
            reasons.append(
                "Nonblank value changes are not explained by cleaning issues."
            )
        if not reasons:
            reasons.append(
                "Totals balance; missing and unparseable counts are shown separately."
            )

        measure_reconciliations[column] = {
            "status": status,
            "raw": raw_measure,
            "silver": silver_measure,
            "quarantined": quarantined_measure,
            "duplicate_held": duplicate_held_measure,
            "output_total": output_total,
            "difference": difference,
            "cleaning_parse_issue_count": explained_parse_losses,
            "unexplained_nonblank_difference": unexplained_nonblank_difference,
            "reason": " ".join(reasons),
        }

    overall_status = (
        "PASS"
        if all(check["status"] == "PASS" for check in checks.values())
        and all(
            value["status"] in {"PASS", "NOT_AVAILABLE"}
            for value in measure_reconciliations.values()
        )
        else "FAIL"
    )
    return {
        "overall_status": overall_status,
        "row_counts": disposition_counts,
        "checks": checks,
        "measures": measure_reconciliations,
        "transformation_count": None,
    }


def _read_csv(path: Path, description: str) -> pd.DataFrame:
    if not path.is_file():
        raise ReconciliationError(f"{description} does not exist: {path}")
    try:
        return pd.read_csv(path, dtype=str, keep_default_na=False, encoding="utf-8-sig")
    except (OSError, UnicodeError, pd.errors.ParserError) as error:
        raise ReconciliationError(f"Could not read {description} '{path}': {error}") from error


def _markdown_summary(report: dict[str, Any]) -> str:
    row_check = report["checks"]["record_count_equation"]
    lines = [
        "# Reconciliation Summary",
        "",
        f"- Run ID: `{report['run_id']}`",
        f"- Source file: `{report['source_file']}`",
        f"- Overall status: **{report['overall_status']}**",
        f"- Generated (UTC): `{report['generated_at_utc']}`",
        "",
        "## Record Counts",
        "",
        f"Input rows: {row_check['input_rows']}",
        f"Eligible rows: {row_check['eligible_rows']}",
        f"Quarantined rows: {row_check['quarantined_rows']}",
        f"Duplicate-held rows: {row_check['duplicate_held_rows']}",
        f"Row-count check: **{row_check['status']}**",
        "",
        "## Additive Measure Totals",
        "",
        "Missing and unparseable values are counted separately and are not treated as zero.",
        "",
        "| Measure | Raw total | Silver total | Quarantined total | Duplicate-held total | Difference | Missing (raw/silver/quarantined/held) | Unparseable (raw/silver/quarantined/held) | Check |",
        "|---|---:|---:|---:|---:|---:|---|---|---|",
    ]
    for field, measure in report["measures"].items():
        if measure["status"] == "NOT_AVAILABLE":
            lines.append(f"| {field} | n/a | n/a | n/a | n/a | n/a | n/a | NOT_AVAILABLE |")
            continue
        lines.append(
            "| {field} | {raw} | {silver} | {quarantined} | {duplicate_held} | {difference} | {missing} | {unparseable} | {status} |".format(
                field=field,
                raw=measure["raw"]["total"],
                silver=measure["silver"]["total"],
                quarantined=measure["quarantined"]["total"],
                duplicate_held=measure["duplicate_held"]["total"],
                difference=measure["difference"],
                missing="/".join(
                    str(measure[group]["missing_count"])
                    for group in ("raw", "silver", "quarantined", "duplicate_held")
                ),
                unparseable="/".join(
                    str(measure[group]["unparseable_count"])
                    for group in ("raw", "silver", "quarantined", "duplicate_held")
                ),
                status=measure["status"],
            )
        )
    lines.extend(["", "## Explanation", ""])
    for field, measure in report["measures"].items():
        if measure["status"] != "NOT_AVAILABLE":
            lines.append(f"- `{field}`: {measure['reason']}")
    lines.extend(
        [
            "",
            "This is a synthetic demonstration. Reconciliation confirms row/measure accounting; it does not prove source accuracy.",
            "",
        ]
    )
    return "\n".join(lines)


def reconcile_run(
    run_id: str,
    bronze_root: str | Path = "data/bronze",
    silver_root: str | Path = "data/silver",
    quarantine_root: str | Path = "data/quarantine",
    reports_root: str | Path = "reports",
    generated_at_utc: str | None = None,
) -> tuple[dict[str, Any], Path, Path]:
    bronze_directory = Path(bronze_root) / run_id
    metadata_path = bronze_directory / "metadata.json"
    if not metadata_path.is_file():
        raise ReconciliationError(f"Bronze metadata does not exist: {metadata_path}")
    try:
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ReconciliationError(f"Could not read Bronze metadata: {error}") from error
    if metadata.get("run_id") != run_id:
        raise ReconciliationError("Bronze metadata run_id does not match.")
    source_file = metadata.get("source_file")
    if not isinstance(source_file, str) or Path(source_file).name != source_file:
        raise ReconciliationError("Bronze metadata has an invalid source filename.")
    bronze_file = bronze_directory / source_file
    if not bronze_file.is_file() or _sha256(bronze_file) != metadata.get("source_sha256"):
        raise ReconciliationError("Bronze source is missing or its hash does not match metadata.")

    silver_directory = Path(silver_root) / run_id
    quarantine_directory = Path(quarantine_root) / run_id
    clean_summary_path = silver_directory / "cleaning_summary.json"
    quarantine_summary_path = quarantine_directory / "summary.json"
    try:
        clean_summary = json.loads(clean_summary_path.read_text(encoding="utf-8"))
        quarantine_summary = json.loads(
            quarantine_summary_path.read_text(encoding="utf-8")
        )
    except (OSError, json.JSONDecodeError) as error:
        raise ReconciliationError(f"Could not read pipeline summary: {error}") from error
    for name, summary in (
        ("cleaning", clean_summary),
        ("quarantine", quarantine_summary),
    ):
        if summary.get("run_id") != run_id:
            raise ReconciliationError(f"{name.title()} summary run_id does not match.")
        if summary.get("source_sha256") != metadata.get("source_sha256"):
            raise ReconciliationError(f"{name.title()} summary refers to different source data.")

    source_frame = _read_csv(bronze_file, "Bronze CSV")
    silver_frame = _read_csv(silver_directory / "performance_clean.csv", "Silver CSV")
    blocked_frame = _read_csv(
        quarantine_directory / "quarantined_records.csv", "Quarantined records"
    )
    dispositions_frame = _read_csv(
        quarantine_directory / "dispositions.csv", "Record dispositions"
    )
    transformations_frame = _read_csv(
        silver_directory / "transformation_log.csv", "Transformation log"
    )
    cleaning_issues_frame = _read_csv(
        silver_directory / "cleaning_issues.csv", "Cleaning issues"
    )

    if len(source_frame) != metadata.get("row_count"):
        raise ReconciliationError("Bronze row count does not match its metadata.")
    if len(silver_frame) != clean_summary.get("clean_record_count"):
        raise ReconciliationError("Silver row count does not match cleaning summary.")
    if len(blocked_frame) != quarantine_summary.get("blocked_record_count"):
        raise ReconciliationError("Blocked row count does not match quarantine summary.")
    if len(dispositions_frame) != source_frame.shape[0]:
        raise ReconciliationError("Disposition count does not match Bronze row count.")
    if len(transformations_frame) != clean_summary.get("transformation_count"):
        raise ReconciliationError("Transformation log count does not match cleaning summary.")
    if len(cleaning_issues_frame) != clean_summary.get("cleaning_issue_count"):
        raise ReconciliationError("Cleaning issue count does not match cleaning summary.")

    report = reconcile_records(
        source_frame,
        silver_frame,
        blocked_frame,
        dispositions_frame,
        source_file=source_file,
        cleaning_issues=cleaning_issues_frame,
    )
    report.update(
        {
            "run_id": run_id,
            "source_file": source_file,
            "source_sha256": metadata["source_sha256"],
            "generated_at_utc": generated_at_utc
            or datetime.now(timezone.utc).isoformat(),
            "transformation_count": len(transformations_frame),
            "cleaning_issue_count": len(cleaning_issues_frame),
        }
    )

    report_directory = Path(reports_root) / run_id
    report_directory.mkdir(parents=True, exist_ok=True)
    json_path = report_directory / "reconciliation.json"
    summary_path = report_directory / "reconciliation_summary.md"
    json_path.write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    summary_path.write_text(_markdown_summary(report), encoding="utf-8")
    return report, json_path, summary_path


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Reconcile row dispositions and additive measures for a run."
    )
    parser.add_argument("--run-id", required=True, help="Existing cleaned run ID")
    parser.add_argument("--bronze-root", default="data/bronze")
    parser.add_argument("--silver-root", default="data/silver")
    parser.add_argument("--quarantine-root", default="data/quarantine")
    parser.add_argument("--reports-root", default="reports")
    args = parser.parse_args()

    try:
        report, json_path, summary_path = reconcile_run(
            run_id=args.run_id,
            bronze_root=args.bronze_root,
            silver_root=args.silver_root,
            quarantine_root=args.quarantine_root,
            reports_root=args.reports_root,
        )
    except ReconciliationError as error:
        parser.error(str(error))

    print(
        json.dumps(
            {
                "run_id": report["run_id"],
                "overall_status": report["overall_status"],
                "row_counts": report["row_counts"],
                "reconciliation_json": str(json_path),
                "readable_summary": str(summary_path),
            },
            indent=2,
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()