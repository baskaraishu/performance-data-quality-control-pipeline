"""Validate and preserve source CSV files in the Bronze layer."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

SCHEMA_VERSION = "v1"
EXPECTED_PERFORMANCE_COLUMNS = (
    "establishment_id",
    "establishment_name",
    "region",
    "establishment_type",
    "reporting_month",
    "reporting_period_end",
    "population",
    "certified_normal_accommodation",
    "occupancy_pct",
    "assaults",
    "assaults_per_100",
    "self_harm_incidents",
    "self_harm_per_100",
    "security_incidents",
    "security_incidents_per_100",
    "absconds",
    "education_attendance_pct",
    "purposeful_activity_pct",
    "average_staff",
    "staff_sickness_days",
    "staff_sickness_rate_pct",
    "staff_resignation_rate_pct",
    "data_submission_date",
    "submission_delay_days",
)
_RUN_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")


class IngestionError(ValueError):
    """Raised when a source file cannot safely be ingested."""


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _inspect_csv(source_path: Path) -> tuple[list[str], int]:
    try:
        source = source_path.open("r", newline="", encoding="utf-8-sig")
    except UnicodeDecodeError as error:
        raise IngestionError(f"Source file is not valid UTF-8: {source_path}") from error
    except OSError as error:
        raise IngestionError(f"Could not read source file '{source_path}': {error}") from error

    with source:
        reader = csv.reader(source)
        try:
            headers = next(reader)
        except StopIteration as error:
            raise IngestionError(f"Source CSV is empty: {source_path}") from error

        if not headers or any(not header.strip() for header in headers):
            raise IngestionError("CSV headers must be present and non-empty.")
        if len(headers) != len(set(headers)):
            raise IngestionError("CSV contains duplicate column headers.")

        missing_columns = [
            column for column in EXPECTED_PERFORMANCE_COLUMNS if column not in headers
        ]
        if missing_columns:
            missing = ", ".join(missing_columns)
            raise IngestionError(f"CSV is missing required columns: {missing}")

        row_count = 0
        for row_number, row in enumerate(reader, start=2):
            if len(row) != len(headers):
                raise IngestionError(
                    f"CSV row {row_number} has {len(row)} fields; "
                    f"expected {len(headers)}."
                )
            row_count += 1

    return headers, row_count


def _validate_run_id(run_id: str) -> None:
    if not _RUN_ID_PATTERN.fullmatch(run_id) or run_id in {".", ".."}:
        raise IngestionError(
            "run_id must start with a letter or number and contain only "
            "letters, numbers, dots, underscores, or hyphens."
        )


def ingest_csv(
    source_path: str | Path,
    output_dir: str | Path = "data/bronze",
    run_id: str | None = None,
    schema_version: str = SCHEMA_VERSION,
) -> dict[str, Any]:
    """Validate a performance CSV, preserve its bytes, and write run metadata.

    Reusing a run ID with the same source is safe and returns the existing run.
    Reusing it for a different source or changed contents raises an error.
    """
    source = Path(source_path)
    if not source.is_file():
        raise IngestionError(f"Source file does not exist: {source}")

    headers, row_count = _inspect_csv(source)
    source_hash = _sha256(source)
    run_id = run_id or datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    _validate_run_id(run_id)

    output_root = Path(output_dir)
    run_directory = output_root / run_id
    bronze_file = run_directory / source.name
    metadata_file = run_directory / "metadata.json"

    try:
        run_directory.mkdir(parents=True, exist_ok=False)
    except FileExistsError:
        if not bronze_file.is_file() or not metadata_file.is_file():
            raise IngestionError(
                f"Run ID '{run_id}' already exists but its output is incomplete."
            )
        try:
            existing = json.loads(metadata_file.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            raise IngestionError(
                f"Could not verify existing run '{run_id}': {error}"
            ) from error
        if (
            existing.get("source_file") != source.name
            or existing.get("source_sha256") != source_hash
            or existing.get("schema_version") != schema_version
        ):
            raise IngestionError(
                f"Run ID '{run_id}' already belongs to different input or schema."
            )
        return {**existing, "status": "already_ingested"}

    try:
        shutil.copyfile(source, bronze_file)
        metadata = {
            "run_id": run_id,
            "ingestion_timestamp_utc": datetime.now(timezone.utc).isoformat(),
            "source_file": source.name,
            "source_sha256": source_hash,
            "schema_version": schema_version,
            "row_count": row_count,
            "column_count": len(headers),
            "bronze_file": str(bronze_file),
        }
        metadata_file.write_text(
            json.dumps(metadata, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
    except OSError as error:
        shutil.rmtree(run_directory, ignore_errors=True)
        raise IngestionError(f"Could not write Bronze output: {error}") from error

    return {**metadata, "status": "ingested"}


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Validate and preserve a performance CSV in the Bronze layer."
    )
    parser.add_argument(
        "--input",
        default="data/raw/performance_dirty.csv",
        help="Path to the source CSV (default: %(default)s)",
    )
    parser.add_argument(
        "--output-dir",
        default="data/bronze",
        help="Bronze output directory (default: %(default)s)",
    )
    parser.add_argument(
        "--run-id",
        help="Optional run ID. Reuse it to safely retry the same input.",
    )
    args = parser.parse_args()

    try:
        result = ingest_csv(args.input, args.output_dir, args.run_id)
    except IngestionError as error:
        parser.error(str(error))
    print(json.dumps(result, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()