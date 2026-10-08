# Justice Performance Data Quality and Operational Insights Pipeline

## Beginner-Friendly Project Plan (with Modern Data Cleaning)

## 1. What This Project Is

This is a portfolio project for a Ministry of Justice-style Performance Analyst interview.

You will show how to turn messy operational data into trusted performance insights.

Important: this project uses synthetic (fake but realistic) data inspired by public concepts. It does not use confidential MoJ or HMPPS data.

---

## 2. What You Will Build

The initial POC ends at Task 18.1. It proves the controlled pipeline and release gate with a persistent passing Gold run and a blocked dirty run, then documents the Gold outputs and run evidence.

```text
Synthetic source
   -> Ingestion and profiling
   -> Validation, quarantine, and cleaning
   -> Reconciliation and DQ scoring
   -> Quality gate
          -> PASSED: KPI generation and Gold publication
          -> BLOCKED: report findings; do not publish Gold
   -> Human-readable run report
   -> Gate-passed Gold dataset
```

By the end of the POC, you should be able to demonstrate:

1. A deterministic synthetic run passes the gate and publishes Gold outputs.
2. The intentionally dirty run is blocked and produces no Gold output.
3. The run report distinguishes pipeline execution, gate decision, DQ evidence, and Gold availability.
4. The gate-passed Gold dataset is recorded with its run ID, source hash, and publication evidence.
5. The repository contains reproducible code, tests, rules, and documentation without credentials.

The initial POC stops at Gold publication and its run report. Any downstream consumption is outside this plan.

---

## 3. Who This Plan Is For

This version is designed for beginners:

- Plain language first
- Clear order of work
- Small deliverables at each step
- Optional advanced techniques after core pipeline works

Rule for success: build a simple working pipeline first, then add advanced methods.

---

## 4. Skills You Will Demonstrate

- Python
- SQL (DuckDB)
- Data profiling
- Data validation
- Data cleaning
- Reconciliation and controls
- KPI design
- Testing with pytest
- CI with GitHub Actions
- Data governance and auditability

---

## 5. Project Structure

Create this folder layout:

```text
justice-performance-data-quality/
|
|-- README.md
|
|-- data/
|   |-- raw/
|   |-- reference/
|   |-- bronze/
|   |-- silver/
|   |-- gold/
|   `-- quarantine/
|
|-- src/
|   |-- ingestion.py
|   |-- profiling.py
|   |-- validation.py
|   |-- cleaning.py
|   |-- reconciliation.py
|   |-- kpis.py
|   `-- reporting.py
|
|-- rules/
|   |-- quality_rules.yml
|   `-- cleaning_policies.yml
|
|-- tests/
|   |-- test_ingestion.py
|   |-- test_validation.py
|   |-- test_cleaning.py
|   |-- test_reconciliation.py
|   `-- test_kpis.py
|
|-- reports/
|   |-- profiling_report.html
|   |-- data_quality_report.html
|   `-- reconciliation_report.html
|
|-- docs/
|   |-- business_problem.md
|   |-- data_model.md
|   |-- data_dictionary.md
|   |-- methodology.md
|   |-- assumptions.md
|   `-- data_quality_action_plan.md
|
|-- .github/
|   `-- workflows/
|       `-- data_quality.yml
|
|-- requirements.txt
`-- LICENSE

```

Why bronze/silver/gold?

- Bronze: raw as received
- Silver: cleaned and standardized
- Gold: trusted model for KPI and BI

This is a modern and interview-strong pattern.

---

## 6. Technology Stack

### Core

- Python 3.11+
- pandas
- duckdb
- pyarrow
- pyyaml
- pytest
- pandera

### Recommended modern additions

- scikit-learn (advanced imputation and anomaly options)
- rapidfuzz (fuzzy matching for category/entity cleanup)
- evidently or custom drift checks (data drift monitoring)

### Version control and automation

- Git and GitHub
- GitHub Actions

---

## 7. Business Problem (Step 1)

Create `docs/business_problem.md` with:

1. Context
2. Problem statement
3. Users and decisions
4. Objectives
5. Assumptions
6. Limitations

Use this central question:

Can managers trust the performance data enough to make operational decisions?

---

## 8. Data Design (Steps 2-4)

## 8.1 Define Synthetic Dataset

Suggested fields:

- establishment_id
- establishment_name
- region
- establishment_type
- reporting_month
- population
- certified_normal_accommodation
- assaults
- self_harm_incidents
- security_incidents
- staff_sickness_days
- education_attendance_pct
- purposeful_activity_pct
- absconds
- staff_resignation_rate
- data_submission_date

Target size: 50,000 to 100,000 rows.

## 8.2 Data Model

Use a simple star schema:

- DimEstablishment
- DimDate
- FactPerformance
- FactDataQuality

Document in `docs/data_model.md`:

- table grain
- primary keys
- foreign keys
- relationships

## 8.3 Data Dictionary

Create `docs/data_dictionary.md` with:

- definition
- datatype
- required or optional
- allowed range/format
- quality rule reference

---

## 9. Use the Supplied Learning Data

The supplied `performance_dirty.csv` is intentionally the raw learning input. A separate clean baseline is not expected. Do not create a supposedly clean baseline by editing the dirty file.

Use the supplied reference inputs:

- `data/reference/establishments.csv`
- `data/reference/kpi_targets.csv`

Keep the supplied raw file unchanged. When a test needs predictable input, create a small example inside that test rather than a separate dataset-generation step.

The dirty input includes known issue types such as:

- missing values
- duplicates
- invalid ranges
- inconsistent categories
- bad dates
- bad reference IDs
- impossible cross-field relationships
- temporal spikes

Important: inventory the actual supplied defects before building rules. Do not assume the issue rates or examples in this plan match the file exactly.

This makes tests reproducible.

---

## 10. Pipeline Build Order (Core)

Use this order. Do not skip.

1. Ingestion and profiling
2. Validation and quarantine
3. Cleaning and standardization
4. Reconciliation and DQ scoring
5. Quality gate
6. KPI calculation and Gold publication only for a passing run
7. Human-readable pipeline/run reports
8. Unit and integration tests, including gate-passed and gate-blocked cases
9. Human-readable run report with gate decision and Gold availability

---

## 11. Ingestion (Step 9)

Create `src/ingestion.py`.

Minimum behavior:

1. Check all required files exist.
2. Validate expected schema and data types.
3. Attach run metadata:
   - run_id
   - ingestion_timestamp
   - source_file
   - row_count
4. Write bronze data exactly as received.

Modern best practice to include now:

- idempotent runs (same input and same run_id should not duplicate outputs)
- schema version field (example: schema_version = v1)

---

## 12. Profiling (Step 10)

Create `src/profiling.py` and output `reports/profiling_report.html`.

Profile:

- row/column counts
- type summary
- missingness percent
- duplicates
- min/max and quantiles
- category frequencies
- date coverage
- simple outlier screening

Goal: understand data before cleaning.

---

## 13. Validation Framework (Steps 11-20)

Use rule-driven validation from YAML, not hard-coded checks.

Create `rules/quality_rules.yml`.

Example:

```yaml
establishment_id:
  - rule: not_null
  - rule: reference_exists
    reference_table: establishments

reporting_month:
  - rule: valid_month

population:
  - rule: not_null
  - rule: greater_than_or_equal
    value: 0

education_attendance_pct:
  - rule: between
    min: 0
    max: 100

establishment_type:
  - rule: allowed_values
    values: ["Male Closed", "Male Open", "Female", "YOI"]

```

Validation output should include:

- status: PASS / WARNING / FAIL
- severity: Critical / High / Medium / Low
- rule_id
- record_id
- field
- observed_value
- reason
- detected_timestamp

Write failed records to `data/quarantine/`.

---

## 14. State-of-the-Art Data Cleaning Techniques (Beginner Friendly)

Use this in two levels.

## Level A: Must-have techniques

1. Standardization and canonicalization
   - lowercase + trim + controlled title case
   - map known aliases to canonical values
2. Rule-based range repair
   - impossible values to null + quarantine reason
3. Deterministic deduplication
   - key = establishment_id + reporting_month
   - keep latest submission by submission_date
4. Type-safe parsing
   - strict date parsing and explicit numeric coercion
5. Audit trail
   - store original_value, cleaned_value, rule_applied

## Level B: Modern advanced techniques (after Level A works)

1. Fuzzy category resolution
   - use RapidFuzz to map near-miss labels to canonical set
   - example: "Male-closed" -> "Male Closed"
2. Smart missing-data imputation
   - use segmented median or iterative imputer for non-critical metrics
   - always tag imputed fields with `imputed_flag`
3. Drift detection between periods
   - monitor PSI or KS statistics for key fields
   - alert if distribution shifts unexpectedly
4. Robust anomaly detection
   - combine IQR + MAD + time-based change thresholds
   - optional Isolation Forest for multivariate anomaly scoring
5. Probabilistic duplicate detection for near-duplicates
   - similarity on name + region + month when keys are unreliable
   - mark as `suspected_duplicate` for review, do not auto-delete

Use advanced methods carefully:

- never hide uncertainty
- keep explainability
- keep reproducible logic

---

## 15. Cleaning Implementation (Step 21)

Create `src/cleaning.py`.

Processing pattern:

1. Read bronze and validation results.
2. Apply deterministic fixes.
3. Apply approved advanced fixes.
4. Write silver outputs.
5. Write transformation log:
   - run_id
   - record_id
   - column
   - original_value
   - cleaned_value
   - method
   - confidence (if applicable)

Hard rule:

No destructive overwrite of raw data.

---

## 16. Reconciliation and Control Totals (Step 22)

Create `src/reconciliation.py` and `reports/reconciliation_report.html`.

Required controls:

```text
raw_count
- duplicate_removed_count
- quarantined_count
+ corrected_count
= final_silver_count

```

Also reconcile key metrics totals (example: population).

Every difference must be explainable in report text.

---

## 17. Data Quality Score and Action Plan (Step 23)

Build score dimensions:

- completeness
- validity
- uniqueness
- consistency
- referential integrity
- timeliness

Example weights (project assumption):

- completeness 20
- validity 20
- consistency 20
- uniqueness 15
- referential integrity 15
- timeliness 10

Create issue log fields:

- issue_id
- detected_date
- dataset
- field
- rule_id
- severity
- impact
- root_cause
- action
- owner
- due_date
- status

Create `docs/data_quality_action_plan.md`.

---

## 18. KPI Layer and Performance Analysis (Step 24)

Create `src/kpis.py`.

Core KPI examples:

- occupancy_pct = population / certified_normal_accommodation \* 100
- assaults_per_100 = assaults / population \* 100
- education_attendance_pct
- staff_sickness_rate
- target_variance = actual - target

Critical principle:

If data quality for a metric is below threshold, show KPI with warning label. Do not present weak-quality KPIs as definitive.

---

## 19. Testing and CI (Core Tests In Scope; Hosted CI Future)

Unit and integration tests, including gate-passed Gold publication and gate-blocked no-publication cases, are part of the initial POC. GitHub Actions hosting and artifact publishing are future enhancements.

## 19.1 Automated tests

Use pytest for:

- validation rule behavior
- cleaning behavior
- dedupe logic
- reconciliation math
- KPI formula correctness

Also add one integration test:

`dirty input -> full pipeline -> expected gold outputs`

## 19.2 Data quality gate

Use stronger release policy than only "critical = 0".

Recommended gate:

1. Critical failures = 0
2. High failure rate <= 0.5 percent
3. Overall DQ score >= 95
4. Reconciliation difference = 0 for protected totals

If any fail, block release to gold.

## 20.3 GitHub Actions

Workflow steps:

1. Install dependencies
2. Run unit tests
3. Run integration test
4. Run validation pipeline
5. Generate reports
6. Enforce quality gate
7. Publish artifacts

---

## 20. Suggested Timeline (Beginner Realistic)

Week 1:

- business problem
- data model and dictionary
- inventory the supplied dirty dataset and agree the initial quality rules

Week 2:

- ingestion
- profiling
- rule engine
- quarantine

Week 3:

- cleaning
- reconciliation
- KPI layer

Week 4:

- complete quality-gate and reporting acceptance
- run a persistent deterministic synthetic case that passes the gate and publishes Gold
- run the dirty case and verify it is blocked without Gold publication
- record the passing Gold run ID, source hash, row counts, and gate decision
- prepare the README and GitHub repository for future enhancements

---

## 21. Initial POC Definition of Done

The initial POC is complete when all are true:

1. A deterministic synthetic run completes end-to-end with `quality_gate.json` decision `PASSED` and publishes Gold outputs.
2. The dirty-input run is shown as `BLOCKED`, and no Gold output is created for it.
3. Validation, quarantine, reconciliation, DQ scoring, gate checks, and Gold availability are traceable in the run artifacts and Task 18 report.
4. KPI Gold rows carry availability/trust context; unsupported or supplied-only measures are not presented as recalculated or definitive.
5. The gate-passed Gold outputs and their run ID, source hash, row counts, and gate decision are recorded in run artifacts.
6. The README explains setup, tests, assumptions, gate behavior, and how to reproduce the Gold output.

---

## 22. What to Say in Interview

Use this simple story:

1. Problem: decisions were being made on potentially unreliable data.
2. Method: built a governed quality pipeline with controls and quarantine.
3. Evidence: reconciliation, quality score, automated tests, and the release-gate pass/block behavior.
4. Insight: separated real performance issues from data-quality noise.
5. Action: provided exception-focused recommendations.
6. Impact: improved trust in management information.

Key line:

I treated data quality as an assurance function for decision-making, not just data cleaning.

---

## 23. Initial POC Deliverables Checklist

```text
[ ] docs/business_problem.md
[ ] docs/data_model.md
[ ] docs/data_dictionary.md
[ ] data/raw/performance_dirty.csv
[ ] rules/quality_rules.yml
[ ] rules/cleaning_policies.yml
[ ] src/ingestion.py
[ ] src/profiling.py
[ ] src/validation.py
[ ] src/cleaning.py
[ ] src/reconciliation.py
[ ] src/kpis.py
[ ] reports/profiling_report.html
[ ] reports/data_quality_report.html
[ ] reports/reconciliation_report.html
[ ] tests and integration test
[ ] Gate-passed persistent synthetic run with Gold outputs
[ ] Dirty run blocked by the quality gate with no Gold publication
[ ] Task 18 run report showing gate decision and Gold availability
[ ] README with methods, assumptions, limitations, and import instructions

```

Work beyond the Gold-output POC requires a separately agreed scope and is not specified in this plan.