"""Separate blocked records while retaining source and issue evidence."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd

from src.validation import ISSUE_COLUMNS

DUPLICATE_RULE_IDS = {"DUP-001"}
DISPOSITION_COLUMNS = [
    "run_id",
    "source_record_id",
    "disposition",
    "issue_count",
    "flag_issue_count",
    "quarantine_issue_count",
]
_RUN_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")


class QuarantineError(ValueError):
    """Raised when source rows and validation results cannot be reconciled."""


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def build_dispositions(
    source_frame: pd.DataFrame,
    issues: pd.DataFrame,
    run_id: str,
    source_file: str,
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """Return one disposition per source row plus blocked rows and linked issues.

    Duplicate-only records are held as ``duplicate-held``. A different
    quarantine-action issue takes precedence and classifies the row as
    ``quarantined``. Both dispositions are excluded from accepted Silver data.
    """
    missing_issue_columns = sorted(set(ISSUE_COLUMNS) - set(issues.columns))
    if missing_issue_columns:
        raise QuarantineError(
            "Validation issues are missing columns: "
            + ", ".join(missing_issue_columns)
        )

    expected_record_ids = {
        f"{source_file}:{row_number}"
        for row_number in range(2, len(source_frame) + 2)
    }
    if not issues.empty:
        issue_run_ids = set(issues["run_id"].astype(str))
        if issue_run_ids != {run_id}:
            raise QuarantineError(
                f"Validation issues must all belong to run '{run_id}'."
            )
        unknown_actions = set(issues["action"].astype(str)) - {"flag", "quarantine"}
        if unknown_actions:
            raise QuarantineError(
                "Validation issues have unsupported actions: "
                + ", ".join(sorted(unknown_actions))
            )
        orphan_ids = set(issues["source_record_id"].astype(str)) - expected_record_ids
        if orphan_ids:
            raise QuarantineError(
                "Validation issues refer to unknown source records: "
                + ", ".join(sorted(orphan_ids))
            )

    issues_by_record: dict[str, list[dict[str, Any]]] = defaultdict(list)
    if not issues.empty:
        for issue in issues.to_dict(orient="records"):
            issues_by_record[str(issue["source_record_id"])].append(issue)

    dispositions: list[dict[str, Any]] = []
    blocked_rows: list[dict[str, Any]] = []
    row_issues: list[dict[str, Any]] = []

    for row_number, source_row in enumerate(
        source_frame.to_dict(orient="records"), start=2
    ):
        source_record_id = f"{source_file}:{row_number}"
        associated_issues = issues_by_record.get(source_record_id, [])
        blocking_issues = [
            issue for issue in associated_issues if issue["action"] == "quarantine"
        ]
        non_duplicate_blockers = [
            issue
            for issue in blocking_issues
            if issue["rule_id"] not in DUPLICATE_RULE_IDS
        ]
        has_duplicate_issue = any(
            issue["rule_id"] in DUPLICATE_RULE_IDS for issue in associated_issues
        )

        if non_duplicate_blockers:
            disposition = "quarantined"
        elif has_duplicate_issue:
            disposition = "duplicate-held"
        elif blocking_issues:
            disposition = "quarantined"
        else:
            disposition = "eligible"

        disposition_row = {
            "run_id": run_id,
            "source_record_id": source_record_id,
            "disposition": disposition,
            "issue_count": len(associated_issues),
            "flag_issue_count": sum(
                issue["action"] == "flag" for issue in associated_issues
            ),
            "quarantine_issue_count": len(blocking_issues),
        }
        dispositions.append(disposition_row)

        if disposition != "eligible":
            blocked_rows.append(
                {
                    **source_row,
                    "source_record_id": source_record_id,
                    "disposition": disposition,
                }
            )
            for issue in associated_issues:
                row_issues.append({**issue, "disposition": disposition})

    dispositions_frame = pd.DataFrame(dispositions, columns=DISPOSITION_COLUMNS)
    blocked_columns = [*source_frame.columns, "source_record_id", "disposition"]
    blocked_frame = pd.DataFrame(blocked_rows, columns=blocked_columns)
    linked_issue_columns = [*issues.columns, "disposition"]
    linked_issues_frame = pd.DataFrame(row_issues, columns=linked_issue_columns)
    return dispositions_frame, blocked_frame, linked_issues_frame


def quarantine_run(
    run_id: str,
    bronze_root: str | Path = "data/bronze",
    validation_root: str | Path = "data/silver",
    output_root: str | Path = "data/quarantine",
    generated_at_utc: str | None = None,
) -> tuple[dict[str, Any], Path]:
    """Classify source rows and write quarantine evidence for one validation run."""
    if not _RUN_ID_PATTERN.fullmatch(run_id):
        raise QuarantineError("run_id contains unsupported characters.")

    run_directory = Path(bronze_root) / run_id
    metadata_path = run_directory / "metadata.json"
    if not metadata_path.is_file():
        raise QuarantineError(f"Run metadata does not exist: {metadata_path}")
    try:
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise QuarantineError(f"Could not read run metadata: {error}") from error
    if metadata.get("run_id") != run_id:
        raise QuarantineError("Metadata run_id does not match the requested run.")

    source_file = metadata.get("source_file")
    if not isinstance(source_file, str) or Path(source_file).name != source_file:
        raise QuarantineError("Metadata has an invalid source filename.")
    bronze_file = run_directory / source_file
    if not bronze_file.is_file():
        raise QuarantineError(f"Bronze CSV does not exist: {bronze_file}")
    source_hash = _sha256(bronze_file)
    if source_hash != metadata.get("source_sha256"):
        raise QuarantineError("Bronze CSV hash does not match the run metadata.")

    validation_directory = Path(validation_root) / run_id
    validation_summary_path = validation_directory / "validation_summary.json"
    issues_path = validation_directory / "validation_issues.csv"
    if not validation_summary_path.is_file() or not issues_path.is_file():
        raise QuarantineError(
            f"Validation outputs are incomplete for run '{run_id}'."
        )
    try:
        validation_summary = json.loads(
            validation_summary_path.read_text(encoding="utf-8")
        )
    except (OSError, json.JSONDecodeError) as error:
        raise QuarantineError(f"Could not read validation summary: {error}") from error
    if validation_summary.get("run_id") != run_id:
        raise QuarantineError("Validation summary run_id does not match.")
    if validation_summary.get("source_sha256") != source_hash:
        raise QuarantineError("Validation summary refers to different source data.")

    try:
        source_frame = pd.read_csv(
            bronze_file,
            dtype=str,
            keep_default_na=False,
            encoding="utf-8-sig",
        )
        issues_frame = pd.read_csv(
            issues_path,
            dtype=str,
            keep_default_na=False,
            encoding="utf-8-sig",
        )
    except (OSError, UnicodeError, pd.errors.ParserError) as error:
        raise QuarantineError(f"Could not parse run CSV: {error}") from error

    if len(source_frame) != metadata.get("row_count"):
        raise QuarantineError("Bronze CSV row count does not match run metadata.")
    if validation_summary.get("input_row_count") != len(source_frame):
        raise QuarantineError("Validation summary row count does not match Bronze.")
    if len(issues_frame) != validation_summary.get("issue_count"):
        raise QuarantineError("Validation issue count does not match its summary.")

    dispositions, blocked_records, linked_issues = build_dispositions(
        source_frame,
        issues_frame,
        run_id=run_id,
        source_file=source_file,
    )
    disposition_counts = Counter(dispositions["disposition"].tolist())
    if sum(disposition_counts.values()) != len(source_frame):
        raise QuarantineError("Source records did not reconcile to one disposition each.")

    generated_at_utc = generated_at_utc or datetime.now(timezone.utc).isoformat()
    output_directory = Path(output_root) / run_id
    output_directory.mkdir(parents=True, exist_ok=True)
    quarantined_path = output_directory / "quarantined_records.csv"
    linked_issues_path = output_directory / "issues.csv"
    dispositions_path = output_directory / "dispositions.csv"
    summary_path = output_directory / "summary.json"

    blocked_records.to_csv(quarantined_path, index=False, encoding="utf-8")
    linked_issues.to_csv(linked_issues_path, index=False, encoding="utf-8")
    dispositions.to_csv(dispositions_path, index=False, encoding="utf-8")

    issue_action_counts = Counter(issues_frame.get("action", pd.Series(dtype=str)))
    records_with_flag_issues = int(
        dispositions["flag_issue_count"].gt(0).sum()
    )
    eligible_flagged_records = int(
        (
            dispositions["disposition"].eq("eligible")
            & dispositions["flag_issue_count"].gt(0)
        ).sum()
    )
    summary = {
        "run_id": run_id,
        "source_file": source_file,
        "source_sha256": source_hash,
        "generated_at_utc": generated_at_utc,
        "input_record_count": len(source_frame),
        "disposition_counts": {
            disposition: int(disposition_counts.get(disposition, 0))
            for disposition in ("eligible", "quarantined", "duplicate-held")
        },
        "eligible_record_count": int(disposition_counts.get("eligible", 0)),
        "quarantined_record_count": int(disposition_counts.get("quarantined", 0)),
        "duplicate_held_record_count": int(
            disposition_counts.get("duplicate-held", 0)
        ),
        "blocked_record_count": len(blocked_records),
        "validation_issue_row_count": len(issues_frame),
        "issues_for_blocked_records": len(linked_issues),
        "records_with_flag_issues": records_with_flag_issues,
        "eligible_records_with_flag_issues": eligible_flagged_records,
        "flag_issue_row_count": int(issue_action_counts.get("flag", 0)),
        "quarantine_action_issue_row_count": int(
            issue_action_counts.get("quarantine", 0)
        ),
        "outputs": {
            "quarantined_records": str(quarantined_path),
            "issues": str(linked_issues_path),
            "dispositions": str(dispositions_path),
        },
    }
    summary_path.write_text(
        json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return summary, summary_path


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Separate blocked source records and preserve issue evidence."
    )
    parser.add_argument("--run-id", required=True, help="Existing validated run ID")
    parser.add_argument("--bronze-root", default="data/bronze")
    parser.add_argument("--validation-root", default="data/silver")
    parser.add_argument("--output-root", default="data/quarantine")
    args = parser.parse_args()

    try:
        summary, summary_path = quarantine_run(
            run_id=args.run_id,
            bronze_root=args.bronze_root,
            validation_root=args.validation_root,
            output_root=args.output_root,
        )
    except QuarantineError as error:
        parser.error(str(error))

    print(
        json.dumps(
            {**summary, "summary_path": str(summary_path)}, indent=2, sort_keys=True
        )
    )


if __name__ == "__main__":
    main()