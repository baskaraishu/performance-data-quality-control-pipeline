import csv
import hashlib
import json

import pandas as pd
import pytest
import yaml

from src.cleaning import (
    CLEANING_ISSUE_COLUMNS,
    TRANSFORMATION_COLUMNS,
    CleaningError,
    clean_records,
    clean_run,
    load_cleaning_policies,
    validate_cleaning_policies,
)
from src.ingestion import EXPECTED_PERFORMANCE_COLUMNS


def make_policy():
    return {
        "schema_version": 1,
        "missing_values": "preserve",
        "duplicate_handling": "use_task10_disposition",
        "integer_columns": ["population", "assaults"],
        "decimal_columns": ["education_attendance_pct"],
        "date_columns": ["reporting_month", "data_submission_date"],
        "category_mappings": {"establishment_type": {"male closed": "Male Closed"}},
    }


def make_reference():
    return pd.DataFrame(
        {
            "establishment_id": ["PR001"],
            "establishment_type": ["Male Closed"],
        }
    )


def make_full_row(**updates):
    row = {column: "" for column in EXPECTED_PERFORMANCE_COLUMNS}
    row.update(
        {
            "establishment_id": "PR001",
            "establishment_name": "Synthetic A",
            "region": "North",
            "establishment_type": "Male Closed",
            "reporting_month": "2025-04-01",
            "reporting_period_end": "2025-04-30",
            "population": "10",
            "certified_normal_accommodation": "12",
            "assaults": "0",
            "data_submission_date": "2025-05-05",
        }
    )
    row.update(updates)
    return row


def test_clean_records_maps_only_approved_aliases_and_logs_parse_failures():
    frame = pd.DataFrame(
        [
            {
                **make_full_row(
                    establishment_type="male closed",
                    population="10",
                    education_attendance_pct="",
                ),
                "source_record_id": "input.csv:2",
            },
            {
                **make_full_row(
                    establishment_type="Male Closed",
                    population="not-a-number",
                    education_attendance_pct="66.5",
                    data_submission_date="not-a-date",
                ),
                "source_record_id": "input.csv:3",
            },
            {
                **make_full_row(
                    establishment_type="Male Closed",
                    population="-5",
                    education_attendance_pct="",
                ),
                "source_record_id": "input.csv:4",
            },
        ]
    )

    cleaned, transformations, issues = clean_records(frame, make_policy(), "test-run")

    assert cleaned.loc[0, "establishment_type"] == "Male Closed"
    assert cleaned.loc[0, "population"] == 10
    assert pd.isna(cleaned.loc[0, "education_attendance_pct"])
    assert cleaned.loc[1, "population"] is pd.NA or pd.isna(cleaned.loc[1, "population"])
    assert pd.isna(cleaned.loc[1, "data_submission_date"])
    assert cleaned.loc[2, "population"] == -5
    assert set(transformations.columns) == set(TRANSFORMATION_COLUMNS)
    assert set(issues.columns) == set(CLEANING_ISSUE_COLUMNS)
    assert set(issues["field"]) == {"population", "data_submission_date"}
    assert any(
        row["field"] == "establishment_type"
        and row["method"] == "approved_category_mapping"
        for row in transformations.to_dict(orient="records")
    )


def write_cleaning_run(tmp_path):
    run_id = "cleaning-test-run"
    bronze_directory = tmp_path / "bronze" / run_id
    bronze_directory.mkdir(parents=True)
    source_path = bronze_directory / "performance_dirty.csv"
    rows = [
        make_full_row(
            establishment_id="PR001",
            establishment_type="male closed",
            population="10",
        ),
        make_full_row(
            establishment_id="PR002",
            reporting_month="2025-05-01",
            establishment_type="Male Closed",
            population="12",
        ),
        make_full_row(
            establishment_id="PR003",
            reporting_month="2025-06-01",
            establishment_type="Male Closed",
            population="-3",
        ),
    ]
    with source_path.open("w", newline="", encoding="utf-8") as output:
        writer = csv.DictWriter(output, fieldnames=EXPECTED_PERFORMANCE_COLUMNS)
        writer.writeheader()
        writer.writerows(rows)
    original_hash = hashlib.sha256(source_path.read_bytes()).hexdigest()
    metadata = {
        "run_id": run_id,
        "source_file": source_path.name,
        "source_sha256": original_hash,
        "row_count": len(rows),
        "column_count": len(EXPECTED_PERFORMANCE_COLUMNS),
        "schema_version": "v1",
    }
    (bronze_directory / "metadata.json").write_text(
        json.dumps(metadata), encoding="utf-8"
    )

    quarantine_directory = tmp_path / "quarantine" / run_id
    quarantine_directory.mkdir(parents=True)
    dispositions = pd.DataFrame(
        [
            {"run_id": run_id, "source_record_id": f"{source_path.name}:2", "disposition": "eligible"},
            {"run_id": run_id, "source_record_id": f"{source_path.name}:3", "disposition": "duplicate-held"},
            {"run_id": run_id, "source_record_id": f"{source_path.name}:4", "disposition": "eligible"},
        ]
    )
    dispositions.to_csv(quarantine_directory / "dispositions.csv", index=False)
    quarantine_summary = {
        "run_id": run_id,
        "source_sha256": original_hash,
        "input_record_count": len(rows),
    }
    (quarantine_directory / "summary.json").write_text(
        json.dumps(quarantine_summary), encoding="utf-8"
    )

    reference_path = tmp_path / "establishments.csv"
    pd.DataFrame(
        {
            "establishment_id": ["PR001", "PR002", "PR003"],
            "establishment_type": ["Male Closed"] * 3,
        }
    ).to_csv(reference_path, index=False)
    policies_path = tmp_path / "cleaning_policies.yml"
    policies_path.write_text(yaml.safe_dump(make_policy()), encoding="utf-8")
    return run_id, source_path, original_hash, reference_path, policies_path


def test_clean_run_processes_only_eligible_records_and_preserves_bronze(tmp_path):
    run_id, source_path, original_hash, reference_path, policies_path = write_cleaning_run(
        tmp_path
    )

    summary, summary_path = clean_run(
        run_id,
        bronze_root=tmp_path / "bronze",
        quarantine_root=tmp_path / "quarantine",
        reference_path=reference_path,
        policy_path=policies_path,
        output_root=tmp_path / "silver",
        generated_at_utc="2026-09-29T00:00:00+00:00",
    )

    output_dir = tmp_path / "silver" / run_id
    clean = pd.read_csv(output_dir / "performance_clean.csv", dtype=str, keep_default_na=False)
    log = pd.read_csv(output_dir / "transformation_log.csv", dtype=str, keep_default_na=False)
    issues = pd.read_csv(output_dir / "cleaning_issues.csv", dtype=str, keep_default_na=False)
    saved_summary = json.loads(summary_path.read_text(encoding="utf-8"))
    assert len(clean) == 2
    assert clean["source_record_id"].tolist() == ["performance_dirty.csv:2", "performance_dirty.csv:4"]
    assert clean.loc[0, "establishment_type"] == "Male Closed"
    assert clean.loc[1, "population"] == "-3"
    assert len(issues) == 0
    assert "approved_category_mapping" in set(log["method"])
    assert summary["excluded_record_count"] == 1
    assert saved_summary["clean_record_count"] == 2
    assert hashlib.sha256(source_path.read_bytes()).hexdigest() == original_hash


def test_cleaning_policy_rejects_unapproved_reference_target():
    invalid = make_policy()
    invalid["category_mappings"]["establishment_type"]["Male-Closed"] = "Unknown Type"

    with pytest.raises(CleaningError, match="not in the reference values"):
        validate_cleaning_policies(invalid, make_reference())