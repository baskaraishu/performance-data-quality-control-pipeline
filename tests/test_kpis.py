import pandas as pd
import pytest

from src.ingestion import EXPECTED_PERFORMANCE_COLUMNS
from src.kpis import (
    KPIError,
    aggregate_calculated_kpi,
    calculate_kpi_rows,
    load_kpi_mappings,
)

TARGETS = pd.DataFrame(
    [
        {"kpi_name": "Occupancy %", "direction": "Lower", "target_value": "95.0", "definition": "Population / Certified accommodation * 100"},
        {"kpi_name": "Education attendance %", "direction": "Higher", "target_value": "75.0", "definition": "Attended sessions / Scheduled sessions * 100"},
        {"kpi_name": "Purposeful activity %", "direction": "Higher", "target_value": "75.0", "definition": "Activity participation rate"},
        {"kpi_name": "Assaults per 100", "direction": "Lower", "target_value": "3.5", "definition": "Assaults / population * 100"},
        {"kpi_name": "Self-harm per 100", "direction": "Lower", "target_value": "6.0", "definition": "Self-harm incidents / population * 100"},
        {"kpi_name": "Security incidents per 100", "direction": "Lower", "target_value": "2.0", "definition": "Security incidents / population * 100"},
        {"kpi_name": "Staff sickness rate %", "direction": "Lower", "target_value": "5.0", "definition": "Sickness days / available staff days * 100"},
        {"kpi_name": "Staff resignation rate %", "direction": "Lower", "target_value": "8.0", "definition": "Resignations / average staff * 100"},
    ]
)


def load_mappings():
    return load_kpi_mappings(
        "rules/kpi_mappings.yml",
        TARGETS,
        set(EXPECTED_PERFORMANCE_COLUMNS) | {"source_record_id"},
    )


def make_record(source_record_id, population, capacity, assaults, **overrides):
    record = {
        "source_record_id": source_record_id,
        "establishment_id": "PR001",
        "establishment_name": "Synthetic One",
        "region": "North",
        "establishment_type": "Male Closed",
        "reporting_month": "2025-04-01",
        "reporting_period_end": "2025-04-30",
        "population": population,
        "certified_normal_accommodation": capacity,
        "occupancy_pct": "80",
        "assaults": assaults,
        "assaults_per_100": "2",
        "self_harm_incidents": "3",
        "self_harm_per_100": "3",
        "security_incidents": "1",
        "security_incidents_per_100": "1",
        "education_attendance_pct": "70",
        "purposeful_activity_pct": "77",
        "average_staff": "100",
        "staff_sickness_days": "40",
        "staff_sickness_rate_pct": "4",
        "staff_resignation_rate_pct": "6",
        "data_submission_date": "2025-05-05",
        "submission_delay_days": "5",
    }
    record.update(overrides)
    return record


def make_dq_summary():
    return {
        "dimension_results": {
            "completeness": {"score_pct": 99.0, "status": "SCORED"},
            "validity": {"score_pct": 98.0, "status": "SCORED"},
            "referential_integrity": {"score_pct": 97.0, "status": "SCORED"},
        }
    }


def test_kpi_formulas_targets_trust_and_supplied_only_status():
    mappings = load_mappings()
    performance = pd.DataFrame(
        [
            make_record("performance_dirty.csv:2", "100", "125", "2"),
            make_record("performance_dirty.csv:3", "50", "100", "1"),
        ]
    )
    issues = pd.DataFrame(
        [
            {
                "run_id": "kpi-test",
                "issue_id": "kpi-test:performance_dirty.csv:2:REVIEW-001",
                "source_record_id": "performance_dirty.csv:2",
                "rule_id": "REVIEW-001",
                "field": "occupancy_pct",
                "action": "flag",
            },
        ]
    )

    results = calculate_kpi_rows(
        performance,
        TARGETS,
        mappings,
        issues,
        make_dq_summary(),
        run_id="kpi-test",
    )
    row = results.loc[
        (results["source_record_id"] == "performance_dirty.csv:2")
        & (results["kpi_name"] == "Occupancy %")
    ].iloc[0]
    education = results.loc[
        (results["source_record_id"] == "performance_dirty.csv:2")
        & (results["kpi_name"] == "Education attendance %")
    ].iloc[0]
    purposeful = results.loc[
        (results["source_record_id"] == "performance_dirty.csv:2")
        & (results["kpi_name"] == "Purposeful activity %")
    ].iloc[0]
    assaults = results.loc[
        (results["source_record_id"] == "performance_dirty.csv:2")
        & (results["kpi_name"] == "Assaults per 100")
    ].iloc[0]

    assert row["actual_value"] == 80.0
    assert row["region"] == "North"
    assert row["establishment_type"] == "Male Closed"
    assert row["target_value"] == 95.0
    assert row["target_variance"] == -15.0
    assert row["comparison_status"] == "COMPARED"
    assert row["trust_status"] == "REVIEW_REQUIRED"
    assert "REVIEW-001" in row["related_issue_ids"]
    assert assaults["actual_value"] == 2.0
    assert assaults["trust_status"] == "TRUSTED"
    assert education["actual_value"] == 70.0
    assert education["availability_status"] == "SUPPLIED_ONLY"
    assert education["comparison_status"] == "NOT_COMPARABLE"
    assert pd.isna(education["target_variance"])
    assert education["trust_status"] == "REVIEW_REQUIRED"
    assert purposeful["actual_value"] == 77.0
    assert purposeful["availability_status"] == "DEFINITION_UNCLEAR"


def test_invalid_support_and_zero_denominator_do_not_produce_rates():
    mappings = load_mappings()
    performance = pd.DataFrame(
        [
            make_record("performance_dirty.csv:2", "-5", "10", "2"),
            make_record("performance_dirty.csv:3", "0", "10", "0"),
            make_record("performance_dirty.csv:4", "", "10", "1"),
        ]
    )
    issues = pd.DataFrame(
        [
            {
                "run_id": "kpi-test",
                "issue_id": "kpi-test:performance_dirty.csv:2:NUM-001",
                "source_record_id": "performance_dirty.csv:2",
                "rule_id": "NUM-001",
                "field": "population",
                "action": "flag",
            }
        ]
    )

    results = calculate_kpi_rows(
        performance,
        TARGETS,
        mappings,
        issues,
        make_dq_summary(),
        run_id="kpi-test",
    )
    occupancy = results.loc[results["kpi_name"] == "Occupancy %"].set_index(
        "source_record_id"
    )
    assaults = results.loc[results["kpi_name"] == "Assaults per 100"].set_index(
        "source_record_id"
    )

    assert pd.isna(occupancy.loc["performance_dirty.csv:2", "actual_value"])
    assert occupancy.loc["performance_dirty.csv:2", "availability_status"] == "UNAVAILABLE_INVALID_INPUT"
    assert pd.isna(assaults.loc["performance_dirty.csv:3", "actual_value"])
    assert "denominator" in assaults.loc["performance_dirty.csv:3", "trust_reason"]
    assert assaults.loc["performance_dirty.csv:4", "availability_status"] == "UNAVAILABLE_MISSING_INPUT"


def test_aggregate_ratio_uses_summed_numerator_and_denominator():
    mappings = load_mappings()
    performance = pd.DataFrame(
        [
            make_record("performance_dirty.csv:2", "100", "125", "2"),
            make_record("performance_dirty.csv:3", "50", "100", "1"),
            make_record("performance_dirty.csv:4", "-5", "100", "2"),
        ]
    )
    issues = pd.DataFrame(
        [
            {
                "run_id": "aggregate-test",
                "issue_id": "aggregate-test:performance_dirty.csv:4:NUM-001",
                "source_record_id": "performance_dirty.csv:4",
                "rule_id": "NUM-001",
                "field": "population",
                "action": "flag",
            }
        ]
    )
    results = calculate_kpi_rows(
        performance,
        TARGETS,
        mappings,
        issues,
        make_dq_summary(),
        run_id="aggregate-test",
    )

    aggregate = aggregate_calculated_kpi(results, "Occupancy %", multiplier=100)

    assert aggregate["numerator_total"] == 150.0
    assert aggregate["denominator_total"] == 225.0
    assert aggregate["actual_value"] == 66.67
    assert aggregate["actual_value"] != (80.0 + 50.0) / 2
    assert aggregate["included_records"] == 2
    assert aggregate["excluded_records"] == 1
    assert aggregate["excluded_by_availability"] == {"UNAVAILABLE_INVALID_INPUT": 1}


def test_mapping_requires_all_targets_and_supported_source_fields():
    targets = TARGETS.iloc[:-1].copy()
    with pytest.raises(KPIError, match="exactly one explicit mapping"):
        load_kpi_mappings(
            "rules/kpi_mappings.yml",
            targets,
            set(EXPECTED_PERFORMANCE_COLUMNS),
        )
