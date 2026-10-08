import csv
import json

import pytest

from src.ingestion import (
    EXPECTED_PERFORMANCE_COLUMNS,
    IngestionError,
    ingest_csv,
)


def write_csv(path, headers=None, rows=None):
    headers = headers or list(EXPECTED_PERFORMANCE_COLUMNS)
    rows = rows if rows is not None else [["example"] * len(headers)]
    with path.open("w", newline="", encoding="utf-8") as output:
        writer = csv.writer(output)
        writer.writerow(headers)
        writer.writerows(rows)


def test_ingestion_preserves_source_and_writes_metadata(tmp_path):
    source = tmp_path / "input.csv"
    output_dir = tmp_path / "bronze"
    write_csv(source)

    result = ingest_csv(source, output_dir, run_id="test-run")

    bronze_file = output_dir / "test-run" / "input.csv"
    metadata = json.loads(
        (output_dir / "test-run" / "metadata.json").read_text(encoding="utf-8")
    )
    assert bronze_file.read_bytes() == source.read_bytes()
    assert result["status"] == "ingested"
    assert metadata["row_count"] == 1
    assert metadata["column_count"] == len(EXPECTED_PERFORMANCE_COLUMNS)
    assert metadata["schema_version"] == "v1"
    assert metadata["source_sha256"] == result["source_sha256"]


def test_ingestion_reports_missing_source(tmp_path):
    with pytest.raises(IngestionError, match="does not exist"):
        ingest_csv(tmp_path / "missing.csv", tmp_path / "bronze")


def test_ingestion_reports_missing_required_columns(tmp_path):
    source = tmp_path / "input.csv"
    write_csv(source, headers=["establishment_id"], rows=[["PR001"]])

    with pytest.raises(IngestionError, match="missing required columns"):
        ingest_csv(source, tmp_path / "bronze", run_id="missing-columns")


def test_ingestion_reports_malformed_row_width(tmp_path):
    source = tmp_path / "input.csv"
    write_csv(
        source,
        rows=[["example"] * (len(EXPECTED_PERFORMANCE_COLUMNS) - 1)],
    )

    with pytest.raises(IngestionError, match="row 2 has"):
        ingest_csv(source, tmp_path / "bronze", run_id="malformed-row")


def test_same_run_id_and_input_does_not_duplicate_output(tmp_path):
    source = tmp_path / "input.csv"
    output_dir = tmp_path / "bronze"
    write_csv(source)

    first_result = ingest_csv(source, output_dir, run_id="retry-run")
    second_result = ingest_csv(source, output_dir, run_id="retry-run")

    assert first_result["status"] == "ingested"
    assert second_result["status"] == "already_ingested"
    assert len(list((output_dir / "retry-run").glob("*.csv"))) == 1


def test_run_id_cannot_be_reused_for_changed_input(tmp_path):
    source = tmp_path / "input.csv"
    output_dir = tmp_path / "bronze"
    write_csv(source)
    ingest_csv(source, output_dir, run_id="conflict-run")

    write_csv(source, rows=[["changed"] * len(EXPECTED_PERFORMANCE_COLUMNS)])
    with pytest.raises(IngestionError, match="different input or schema"):
        ingest_csv(source, output_dir, run_id="conflict-run")