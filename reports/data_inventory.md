# Existing Data Inventory

## Purpose

This is the read-only inventory for Implementation Task 1. It records what was present in the workspace on 28 September 2026. No CSV files were changed.

## Files Found

| File | Rows | Columns | Observations |
|---|---:|---:|---|
| `performance_baseline.csv` | Not expected | - | The user confirmed that the dirty file intentionally replaces a separate clean baseline for the learning exercise. |
| `performance_dirty.csv` | 362 | 24 | Intentional raw learning input; contains missing values and data-quality issues. |
| `establishments.csv` | 30 | 6 | Establishment reference table; no missing values were found. |
| `kpi_targets.csv` | 8 | 4 | KPI name, direction, target value, and definition. |

The observed fields in `performance_dirty.csv` are:

```text
establishment_id, establishment_name, region, establishment_type,
reporting_month, reporting_period_end, population,
certified_normal_accommodation, occupancy_pct, assaults,
assaults_per_100, self_harm_incidents, self_harm_per_100,
security_incidents, security_incidents_per_100, absconds,
education_attendance_pct, purposeful_activity_pct, average_staff,
staff_sickness_days, staff_sickness_rate_pct,
staff_resignation_rate_pct, data_submission_date, submission_delay_days
```

## Grain and Coverage

The intended grain is one row per `establishment_id` and `reporting_month` (one establishment's monthly performance record). This is supported by the available columns and by the 30 establishment reference records.

The dirty file spans valid reporting months from April 2025 through March 2026, with reporting-period ends from 30 April 2025 through 31 March 2026. Submission dates range from 2 May 2025 through 8 April 2026.

The intended grain is not currently satisfied by every raw row:

- Two establishment-month keys occur twice: PR017 / 2025-04-01 and PR018 / 2025-05-01.
- PR013 has an invalid reporting month, `2025-14-01`.
- PR999 does not appear in `establishments.csv`.
- The file has 362 rows; it should not be assumed to contain 362 distinct valid establishment-month records.

## Missingness and Data Types

Four cells are blank, one in each of these fields:

- `population`
- `education_attendance_pct`
- `purposeful_activity_pct`
- `average_staff`

Identifiers and descriptive fields are text. Count fields are integer-like, and rates/percentages are numeric. Date fields are represented as ISO-style text in the CSV and should be parsed explicitly. `reporting_month` includes at least one malformed date value, so it cannot safely be treated as a valid date column without validation.

## Confirmed Exceptions

The dirty file contains these representative rule failures:

- Negative population: -50
- Negative assaults: -4
- Education attendance above 100%: 143
- Negative staff resignation rate: -7
- Invalid reporting month: `2025-14-01`
- Unknown establishment ID: PR999
- Two duplicated establishment-month keys

There are also nine occupancy values above 100%. Occupancy above capacity may be operationally possible, so confirm the business rule before treating these as invalid. Do not automatically quarantine solely because occupancy exceeds 100%.

## KPI Formula and Field Availability

The file contains pre-calculated occupancy and incident rates. Formula checks against the corresponding source columns found:

| Supplied rate | Formula checked | Mismatching rows |
|---|---|---:|
| `occupancy_pct` | population / certified normal accommodation * 100 | 2 |
| `assaults_per_100` | assaults / population * 100 | 5 |
| `self_harm_per_100` | self-harm incidents / population * 100 | 2 |
| `security_incidents_per_100` | security incidents / population * 100 | 2 |

The differences include records with invalid or missing source values. Recalculate or flag these measures during later validation; do not assume the supplied rates are trusted.

The target definitions also reveal source-field gaps:

| KPI target | Required source data | Available? | Inventory decision |
|---|---|---|---|
| Occupancy % | Population and certified normal accommodation | Yes | Recalculable, subject to validation. |
| Education attendance % | Attended sessions and scheduled sessions | No | Only a supplied percentage exists; cannot independently recalculate or weight it correctly. |
| Purposeful activity % | Definition/denominator for participation rate | Unclear | Definition needs agreement before interpreting or aggregating. |
| Assaults per 100 | Assault count and population | Yes | Recalculable, subject to validation. |
| Self-harm per 100 | Self-harm incident count and population | Yes | Recalculable, subject to validation. |
| Security incidents per 100 | Security incident count and population | Yes | Recalculable, subject to validation. |
| Staff sickness rate % | Sickness days and available staff days | Partial | Sickness days and average staff exist, but available staff days are not supplied. Do not treat average staff as the denominator without an agreed method. |
| Staff resignation rate % | Resignations and average staff | Partial | Average staff and a pre-calculated rate exist; the resignation count is absent, so the rate cannot be independently recalculated. |

`education_attendance_pct`, `staff_sickness_rate_pct`, and `staff_resignation_rate_pct` should be treated as supplied measures until the missing source data and calculation rules are resolved. The target file's wording alone does not establish that the supplied percentages use the same definitions.

## Decisions and Follow-Up

1. Do not expect or reconstruct `performance_baseline.csv`; use the supplied dirty file as the intentional raw input. Create small, separate known-good fixtures for tests when needed.
2. Treat education attendance, purposeful activity, sickness, and resignation rates as supplied values unless their source components and definitions are added.
3. In Task 5, document the establishment-month key and the handling of multiple submissions for the same month.
4. Occupancy above 100% is not automatically invalid; review it according to the documented business rule.

## Reproducibility Evidence

Read-time SHA-256 hashes for the source files that were present:

```text
performance_dirty.csv  226b926c4add91aa80541aa9d16352ce15a4e3c9df15bf9abb863cb148ebb401
establishments.csv     3de60ba2a763fd40b82d8029e50ac877da2e16711390f8cbbc5036f349a5eb90
kpi_targets.csv        78edeba57df6c2fac0d8f0a9584c153134aa25bd17f6c1368bd8eb35940a1ece
```