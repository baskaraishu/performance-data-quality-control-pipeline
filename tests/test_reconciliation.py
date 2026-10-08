import pandas as pd
import pytest

from src.reconciliation import ReconciliationError, reconcile_records


def test_reconcile_counts_measures_and_missing_values_across_dispositions():
    source = pd.DataFrame(
        [
            {"population": "10", "assaults": "2"},
            {"population": "", "assaults": "3"},
            {"population": "7", "assaults": "bad"},
            {"population": "5", "assaults": "1"},
        ]
    )
    silver = pd.DataFrame(
        [
            {"source_record_id": "sample.csv:2", "population": "10", "assaults": "2"},
            {"source_record_id": "sample.csv:5", "population": "5", "assaults": "1"},
        ]
    )
    blocked = pd.DataFrame(
        [
            {
                "source_record_id": "sample.csv:3",
                "disposition": "quarantined",
                "population": "",
                "assaults": "3",
            },
            {
                "source_record_id": "sample.csv:4",
                "disposition": "duplicate-held",
                "population": "7",
                "assaults": "bad",
            },
        ]
    )
    dispositions = pd.DataFrame(
        [
            {"source_record_id": "sample.csv:2", "disposition": "eligible"},
            {"source_record_id": "sample.csv:3", "disposition": "quarantined"},
            {"source_record_id": "sample.csv:4", "disposition": "duplicate-held"},
            {"source_record_id": "sample.csv:5", "disposition": "eligible"},
        ]
    )

    report = reconcile_records(source, silver, blocked, dispositions, "sample.csv")

    assert report["overall_status"] == "PASS"
    assert report["row_counts"] == {
        "eligible": 2,
        "quarantined": 1,
        "duplicate-held": 1,
    }
    assert report["checks"]["record_count_equation"]["status"] == "PASS"
    assert report["measures"]["population"]["raw"]["total"] == 22
    assert report["measures"]["population"]["difference"] == 0
    assert report["measures"]["population"]["raw"]["missing_count"] == 1
    assert report["measures"]["population"]["quarantined"]["missing_count"] == 1
    assert report["measures"]["population"]["duplicate_held"]["total"] == 7
    assert report["measures"]["assaults"]["raw"]["unparseable_count"] == 1
    assert report["measures"]["assaults"]["duplicate_held"]["unparseable_count"] == 1
    assert report["measures"]["assaults"]["difference"] == 0


def test_reconciliation_fails_for_unexplained_row_or_measure_difference():
    source = pd.DataFrame(
        [
            {"population": "10"},
            {"population": "20"},
        ]
    )
    silver = pd.DataFrame(
        [{"source_record_id": "sample.csv:2", "population": "9"}]
    )
    blocked = pd.DataFrame(
        columns=["source_record_id", "disposition", "population"]
    )
    dispositions = pd.DataFrame(
        [
            {"source_record_id": "sample.csv:2", "disposition": "eligible"},
            {"source_record_id": "sample.csv:3", "disposition": "eligible"},
        ]
    )

    report = reconcile_records(source, silver, blocked, dispositions, "sample.csv")

    assert report["checks"]["record_count_equation"]["status"] == "FAIL"
    assert report["checks"]["source_record_partition"]["status"] == "FAIL"
    assert report["measures"]["population"]["status"] == "FAIL"
    assert report["overall_status"] == "FAIL"


def test_reconciliation_rejects_duplicate_disposition_rows():
    source = pd.DataFrame([{"population": "10"}])
    silver = pd.DataFrame(
        [{"source_record_id": "sample.csv:2", "population": "10"}]
    )
    blocked = pd.DataFrame(
        columns=["source_record_id", "disposition", "population"]
    )
    dispositions = pd.DataFrame(
        [
            {"source_record_id": "sample.csv:2", "disposition": "eligible"},
            {"source_record_id": "sample.csv:2", "disposition": "eligible"},
        ]
    )

    report = reconcile_records(source, silver, blocked, dispositions, "sample.csv")

    assert report["checks"]["one_disposition_per_source_record"]["status"] == "FAIL"
    assert report["overall_status"] == "FAIL"


def test_reconciliation_uses_cleaning_issue_to_explain_parse_loss():
    source = pd.DataFrame([{"population": "not-numeric"}])
    silver = pd.DataFrame(
        [{"source_record_id": "sample.csv:2", "population": ""}]
    )
    blocked = pd.DataFrame(
        columns=["source_record_id", "disposition", "population"]
    )
    dispositions = pd.DataFrame(
        [{"source_record_id": "sample.csv:2", "disposition": "eligible"}]
    )
    cleaning_issues = pd.DataFrame([{"field": "population"}])

    report = reconcile_records(
        source,
        silver,
        blocked,
        dispositions,
        "sample.csv",
        cleaning_issues=cleaning_issues,
    )

    assert report["measures"]["population"]["status"] == "PASS"
    assert report["measures"]["population"]["cleaning_parse_issue_count"] == 1