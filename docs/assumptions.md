# Project Assumptions and Decisions

These are explicit project assumptions for the first implementation. They are demonstration choices, not official Ministry of Justice thresholds, policies, or definitions. Review them if better source information becomes available.

## Data and Reporting Period

- `performance_dirty.csv` is intentionally the project's raw learning dataset. It replaces a separate clean baseline for this exercise; no `performance_baseline.csv` is expected.
- Do not reverse-engineer or create a supposedly clean version by editing the dirty input. Create small known-good or invalid example rows inside the tests that need predictable input; no separate generator task or dataset is required.
- Establishment names, identifiers, values, and results are synthetic. No record should be described as a real establishment or real operational outcome.
- The intended record grain is one row per establishment per reporting month.
- The valid reporting period represented by the data is April 2025 through March 2026. Malformed dates, duplicates, and unknown IDs are deliberate or observed input issues, not additional valid reporting periods.
- The 30-row establishment reference is the authority for valid establishment IDs in this demonstration. An input ID absent from it is flagged for review.

## Duplicate and Data-Quality Handling

- A repeated establishment-month key is a duplicate candidate. Preserve its source evidence and flag/quarantine the affected records until a documented handling rule is applied; do not assume the latest submission is automatically correct.
- Missing values remain missing unless an approved, clearly labeled method is introduced. The first version will not impute operational facts.
- Invalid values are not silently corrected. The pipeline records the value, rule, and reason, and routes the issue according to its severity.
- An anomaly indicates that a value needs investigation; it does not by itself prove a data error.
- Occupancy above 100% is possible in principle and is not automatically invalid. Flag it for review only if the defined business rule requires it.
- Quality thresholds and score weights are project assumptions and must be visibly labeled as such.

## Initial Configured Rule Assumptions

The first rules are in `rules/quality_rules.yml`. They are demonstration checks, not official MoJ/HMPPS standards:

- Count fields are expected to be whole numbers greater than or equal to zero.
- Supplied percentages for education, purposeful activity, sickness, and resignation are expected to be between 0 and 100 when present.
- Occupancy above 100 is a review flag only; it is not automatically quarantined because values above capacity can occur.
- Establishment IDs, region labels, and establishment types are checked against `data/reference/establishments.csv`.
- Missing/invalid identity or reporting-period fields and duplicate establishment-month keys are configured for quarantine; measure-level issues are generally flagged so unrelated measures can still be assessed.
- A rule's severity and action are project choices. They must be revisited if definitions or source-owner guidance change.

## Initial Cleaning Policies

- Only the observed exact `establishment_type` aliases `male closed`, `MALE CLOSED`, and `Male-Closed` map to the reference value `Male Closed`.
- No fuzzy matching, broad case conversion, imputation, outlier correction, or automatic duplicate selection is enabled in the first cleaning pass.
- Silver output includes only rows with Task 10 disposition `eligible`; both `quarantined` and `duplicate-held` rows stay out of it.
- Values that cannot be parsed are written as missing in the typed Silver CSV and preserved with their original text in `cleaning_issues.csv` and `transformation_log.csv`. The Bronze file remains the unchanged source of truth.

## Data-Quality Score Calculation

- A dimension score is `(applicable source records - failed source records) / applicable source records * 100`.
- A source record that fails multiple rules in one dimension counts once in that dimension's failed-record numerator; rule issue counts are reported separately.
- A record is applicable when at least one rule in that dimension can be checked for it. Missing optional values do not become zeros; records with no applicable checks are counted as excluded for that dimension.
- Score all raw source records, including quarantined and duplicate-held records, so blocking bad rows cannot make the remaining score appear better. Report disposition counts separately.
- Consistency is `not available` until consistency rules are configured. Do not display it as 100% or zero.
- The overall score is the unweighted average of available dimension scores. This is a project assumption and does not replace the data-quality release gate; any critical blocking failure can still block release even when the score is high.
- Occupancy review flags are reported separately and are not treated as confirmed validity failures in the score.

## Release-Gate Thresholds

The release policy is configured in `rules/quality_gate.yml`. These are demonstration assumptions, not official MoJ/HMPPS limits:

- Block if any Critical validation issue is present.
- Block if the percentage of distinct source records with at least one High issue exceeds 0.5% of all input records.
- Block if the overall DQ score is unavailable or below 95%.
- Block unless reconciliation status is `PASS`.
- A passing gate is necessary for Gold publication, but it does not prove source accuracy or replace analyst review.

## KPI Definitions and Availability

- Occupancy percentage is recalculated as population divided by certified normal accommodation, multiplied by 100, when both inputs are valid and the denominator is greater than zero.
- Assaults, self-harm, and security incidents per 100 people are recalculated as the incident count divided by valid population, multiplied by 100.
- Education attendance is retained as a supplied percentage. Attended-session and scheduled-session counts are not present, so the percentage cannot be independently recalculated or correctly weighted across establishments.
- Purposeful activity is retained as a supplied percentage for description only until its denominator and business definition are clear.
- Staff sickness and resignation percentages are retained as supplied values but are not independently verified: available staff days and resignation counts are absent. Do not compare these as validated target results in the first version.
- A missing or zero denominator produces an unavailable KPI and a quality issue, not an invented rate.
- Target values in `kpi_targets.csv` are synthetic project targets. They are not official MoJ/HMPPS targets.

## Interpretation

- The analysis is descriptive and exception-focused. It does not establish causation, prove accuracy against an independent source, or prescribe operational action.
- Recommendations should be framed as investigation or data-improvement actions unless independently supported evidence is available.