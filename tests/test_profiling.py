import csv
import hashlib
import json

import pytest

from src.profiling import ProfilingError, profile_run


def make_bronze_run(tmp_path, rows):
    run_id = "profile-test-run"
    run_directory = tmp_path / "bronze" / run_id
    run_directory.mkdir(parents=True)
    csv_path = run_directory / "performance_dirty.csv"
    headers = [
        "establishment_id",
        "reporting_month",
        "population",
        "region",
        "data_submission_date",
    ]
    with csv_path.open("w", newline="", encoding="utf-8") as output:
        writer = csv.writer(output)
        writer.writerow(headers)
        writer.writerows(rows)

    metadata = {
        "run_id": run_id,
        "source_file": csv_path.name,
        "source_sha256": hashlib.sha256(csv_path.read_bytes()).hexdigest(),
        "row_count": len(rows),
        "column_count": len(headers),
    }
    (run_directory / "metadata.json").write_text(
        json.dumps(metadata), encoding="utf-8"
    )
    return run_id, csv_path


def test_profile_summarizes_known_values_and_writes_escaped_html(tmp_path):
    run_id, csv_path = make_bronze_run(
        tmp_path,
        [
            ["PR001", "2025-04-01", "10", "North<&", "2025-05-01"],
            ["PR001", "2025-04-01", "10", "North<&", "2025-05-01"],
            ["PR001", "2025-04-01", "bad-number", "South", "not-a-date"],
            ["PR002", "", "5", "South", "2025-06-01"],
        ],
    )
    original_bytes = csv_path.read_bytes()

    profile, report_path = profile_run(
        run_id,
        bronze_root=tmp_path / "bronze",
        reports_root=tmp_path / "reports",
    )

    columns = {item["column"]: item for item in profile["columns"]}
    assert profile["row_count"] == 4
    assert profile["column_count"] == 5
    assert profile["duplicates"]["exact_duplicate_extra_rows"] == 1
    assert profile["duplicates"]["business_key_duplicate_groups"] == 1
    assert profile["duplicates"]["business_key_duplicate_extra_rows"] == 2
    assert columns["population"]["missing_count"] == 0
    assert columns["population"]["unparseable_count"] == 1
    assert columns["population"]["numeric_summary"]["min"] == 5
    assert columns["population"]["numeric_summary"]["max"] == 10
    assert columns["reporting_month"]["missing_count"] == 1
    assert columns["data_submission_date"]["unparseable_count"] == 1
    assert columns["region"]["inferred_type"] == "category/text"
    assert columns["region"]["unparseable_count"] == 0
    assert columns["region"]["category_frequencies"][0] == {
        "value": "North<&",
        "count": 2,
    }
    assert report_path.is_file()
    assert "Synthetic demonstration data" in report_path.read_text(encoding="utf-8")
    assert "North&lt;&amp;" in report_path.read_text(encoding="utf-8")
    assert "North<&" not in report_path.read_text(encoding="utf-8")
    assert csv_path.read_bytes() == original_bytes


def test_profile_rejects_bronze_hash_mismatch(tmp_path):
    run_id, csv_path = make_bronze_run(tmp_path, [["PR001", "2025-04-01", "10", "North", "2025-05-01"]])
    with csv_path.open("a", encoding="utf-8") as output:
        output.write("PR002,2025-05-01,12,South,2025-06-01\n")

    with pytest.raises(ProfilingError, match="hash does not match"):
        profile_run(
            run_id,
            bronze_root=tmp_path / "bronze",
            reports_root=tmp_path / "reports",
        )