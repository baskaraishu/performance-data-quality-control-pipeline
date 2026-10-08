# Data Dictionary

## How to Read This Dictionary

This dictionary describes the supplied CSV schemas and the rules intended for the project. It does not claim that the synthetic values or thresholds are official. A rule describes what the pipeline should check; it does not mean every current raw row passes.

- **Required** means the field is expected for a usable monthly performance row. A missing required value should be reported, not invented.
- **Conditional** means the field is required only when its measure or check is being calculated.
- **Supplied** means a value is present in the input but its underlying formula cannot currently be verified.
- Dates are stored as text in CSV and must be parsed and checked before use.

## Performance Input: `data/raw/performance_dirty.csv`

Intended business grain: one establishment per reporting month. The raw file can contain multiple rows for a key so that duplicate handling can be demonstrated. Preserve a source-row identifier during ingestion to distinguish such rows.

| Field | Expected type | Meaning | Requirement and validation |
|---|---|---|---|
| `establishment_id` | String | Synthetic identifier for an establishment. | Required; must match `data/reference/establishments.csv`. |
| `establishment_name` | String | Synthetic establishment display name. | Required; compare with the reference record for the ID. |
| `region` | String | Synthetic region label. | Required; compare with the reference record. |
| `establishment_type` | String/category | Synthetic establishment category. | Required; compare with the reference. Reference values: Male Closed, Male Category B, Male Category C, Female Closed, Young Offender. |
| `reporting_month` | Date (month start) | Month represented by the record. | Required; valid ISO date on the first day of a month. Business key component. |
| `reporting_period_end` | Date | Last calendar day covered by the reporting month. | Required; valid date and must agree with `reporting_month`. |
| `population` | Integer | Population count for the reporting period. | Required for occupancy and incident rates; integer >= 0. Missing, negative, or zero-denominator cases must be flagged. |
| `certified_normal_accommodation` | Integer | Certified normal accommodation capacity for the period. | Required for occupancy; integer > 0 to calculate a rate. Check against the applicable establishment reference, but retain the period value. |
| `occupancy_pct` | Decimal percentage | Supplied occupancy percentage. | Supplied/recalculable; compare with population / certified normal accommodation * 100 when both inputs are valid. Values above 100 are review items, not automatically invalid. |
| `assaults` | Integer | Assault incident count for the period. | Required for the incident measure; integer >= 0. |
| `assaults_per_100` | Decimal rate | Supplied assaults per 100 people. | Supplied/recalculable; compare with assaults / population * 100 when inputs are valid. |
| `self_harm_incidents` | Integer | Self-harm incident count for the period. | Required for the incident measure; integer >= 0. |
| `self_harm_per_100` | Decimal rate | Supplied self-harm incidents per 100 people. | Supplied/recalculable; compare with self_harm_incidents / population * 100 when inputs are valid. |
| `security_incidents` | Integer | Security incident count for the period. | Required for the incident measure; integer >= 0. |
| `security_incidents_per_100` | Decimal rate | Supplied security incidents per 100 people. | Supplied/recalculable; compare with security_incidents / population * 100 when inputs are valid. |
| `absconds` | Integer | Abscond count for the period. | Expected integer >= 0. No target KPI is defined for this field in the current target file. |
| `education_attendance_pct` | Decimal percentage | Supplied education attendance percentage. | Optional/conditional; if present, expected 0-100. Source session counts are absent, so it cannot be independently recalculated or correctly weighted. |
| `purposeful_activity_pct` | Decimal percentage | Supplied purposeful-activity percentage. | Optional/conditional; if present, expected 0-100. The underlying numerator and denominator need clarification. |
| `average_staff` | Integer/count | Average staff count for the period. | Conditional; if present, expected integer > 0. This alone is not the available-staff-days denominator. |
| `staff_sickness_days` | Integer | Staff sickness days for the period. | Conditional; if present, integer >= 0. Available staff days are absent, so the target rate cannot be independently recalculated. |
| `staff_sickness_rate_pct` | Decimal percentage | Supplied staff sickness rate. | Supplied only; if present, expected 0-100. Do not independently validate its formula without available staff days. |
| `staff_resignation_rate_pct` | Decimal percentage | Supplied staff resignation rate. | Supplied only; if present, expected 0-100. Resignation counts are absent, so the target rate cannot be independently recalculated. |
| `data_submission_date` | Date | Date the synthetic monthly data was submitted. | Required for timeliness; valid date. |
| `submission_delay_days` | Integer days | Supplied delay in days. | Supplied/recalculable; compare with data_submission_date minus reporting_period_end. Negative values can mean early submission and are not automatically invalid. |

### Performance Record Keys

- **Raw source key:** no unique row ID is supplied. Ingestion should assign a traceable `source_record_id` without changing the original CSV.
- **Business key:** `establishment_id + reporting_month`.
- **Curated key:** the same business key after duplicate issues have been resolved under an explicitly documented rule. Do not discard raw duplicate candidates before review.

## Establishment Reference: `data/reference/establishments.csv`

One row per synthetic establishment; 30 rows were present during the inventory.

| Field | Expected type | Meaning | Validation |
|---|---|---|---|
| `establishment_id` | String | Stable synthetic establishment identifier. | Required and unique; primary business key for this reference. |
| `establishment_name` | String | Synthetic display name. | Required. |
| `region` | String/category | Synthetic region. | Required. Observed values: North, South, Midlands, Wales, Scotland. |
| `establishment_type` | String/category | Synthetic establishment category. | Required; use the category values listed above. |
| `design_capacity` | Integer | Synthetic design capacity. | Required; integer >= 0. |
| `certified_normal_accommodation` | Integer | Reference certified accommodation value. | Required; integer > 0. Compare with the period-level value; do not overwrite historical fact values without an approved rule. |

## KPI Target Reference: `data/reference/kpi_targets.csv`

One row per synthetic KPI target. The file has no target effective dates or establishment-specific targets; for the first version, targets are treated as the same across establishments and months. These are demonstration targets, not official targets.

| Field | Expected type | Meaning | Validation |
|---|---|---|---|
| `kpi_name` | String | Display name of a KPI. | Required and unique after agreed name normalization. |
| `direction` | Category | Whether lower or higher values are preferred. | Required; currently `Lower` or `Higher`. |
| `target_value` | Decimal | Synthetic target value in the KPI's stated unit. | Required; numeric. Interpret only with its KPI and unit. |
| `definition` | String | Formula or description supplied for the target. | Required; document gaps rather than assuming missing inputs. |

## KPI Definitions and Source Availability

| KPI | Numerator / input | Denominator | Unit | Direction | Target | Can it be independently recalculated now? |
|---|---|---|---|---|---:|---|
| Occupancy % | Population | Certified normal accommodation | Percent | Lower | 95.0 | Yes, when both source values are valid and denominator > 0. |
| Education attendance % | Attended sessions | Scheduled sessions | Percent | Higher | 75.0 | No; only the percentage is supplied. |
| Purposeful activity % | Not defined in the target file | Not defined | Percent (assumed from field name) | Higher | 75.0 | No; retain as supplied and clarify definition. |
| Assaults per 100 | Assault count | Population | Incidents per 100 people | Lower | 3.5 | Yes, when source values are valid and population > 0. |
| Self-harm per 100 | Self-harm incident count | Population | Incidents per 100 people | Lower | 6.0 | Yes, when source values are valid and population > 0. |
| Security incidents per 100 | Security incident count | Population | Incidents per 100 people | Lower | 2.0 | Yes, when source values are valid and population > 0. |
| Staff sickness rate % | Sickness days | Available staff days | Percent | Lower | 5.0 | No; available staff days are absent. |
| Staff resignation rate % | Resignations | Average staff | Percent | Lower | 8.0 | No; resignation counts are absent. |

For an unavailable numerator or denominator, report the KPI as unavailable or supplied-only; do not infer a value. A KPI target comparison is meaningful only when the actual value, unit, direction, target period, and data-quality status are known.
