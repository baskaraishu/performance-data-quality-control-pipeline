# Implementation Backlog

For the Justice Performance Data Quality and Operational Insights project.

## Working Agreement

- Work on one numbered task at a time.
- Do not start a task until the user confirms its task number.
- After each task, report files changed, checks run, and any decisions needed. Wait for confirmation before moving to the next task.
- Use `data/raw/` and `data/reference/` as the active project inputs. The former root-level copies were verified byte-identical and the redundant archive was removed during GitHub cleanup; do not overwrite the active inputs.
- Build the essential pipeline first. Tasks marked **Advanced** or **Optional** are stretch work, not prerequisites for a useful first version.
- Every task should identify the files to create or change, its inputs and outputs, important behavior and failure cases, and the command or evidence that proves it is done. Add this detail before asking the user to confirm a task.

## Current Starting Point

The workspace currently contains:

- `data/raw/performance_dirty.csv` (the intentional raw learning input; no separate clean baseline is expected)
- `data/reference/establishments.csv`
- `data/reference/kpi_targets.csv`
- Former root-level input copies were exact duplicates and have been removed; use the active files under `data/`.
- `Plan_updated.md`
- `reports/data_inventory.md`

The dirty input includes pre-calculated rates and percentages. The target file defines education attendance using attended sessions / scheduled sessions, while the input contains only `education_attendance_pct`; do not claim that value was independently recalculated.

## Phase 1: Understand and Prepare

### Task 1 - Inventory the Existing Data

**Status: Complete** - See `reports/data_inventory.md`. The intended establishment-month grain, duplicate-key exceptions, intentionally dirty input, and KPI source-field gaps are documented.

**Goal:** Establish what the supplied CSVs contain before changing or processing them.

**Work:** Record row counts, columns, data types, date range, key uniqueness, missing values, and whether KPI target definitions can be calculated from available fields.

**Done when:** A short inventory report exists, the establishment-month grain is confirmed or corrected, and any missing fields or formula mismatches are listed. Original CSVs remain unchanged.

### Task 2 - Confirm Project Scope and Assumptions

**Status: Complete** - See `docs/business_problem.md` and `docs/assumptions.md`. The dirty file is the intentional raw input; unsupported KPI formulas are explicitly caveated.

**Goal:** Decide the minimum useful version of the project and document its assumptions.

**Work:** Confirm the synthetic-data statement, target users, reporting period, primary questions, and which measures are in scope. Decide whether education attendance is a supplied percentage or will be generated from session counts.

**Done when:** `docs/business_problem.md` and `docs/assumptions.md` describe the same agreed scope and do not imply real MoJ/HMPPS data.

### Task 3 - Set Up the Beginner-Friendly Project Structure

**Status: Complete** - Created the project directories and placed the active CSVs in their raw/reference locations. Former root-level copies were verified byte-identical before their redundant archive was removed during GitHub cleanup.

**Goal:** Create a clean working structure without losing the existing input files.

**Work:** Create the agreed `data/`, `src/`, `rules/`, `tests/`, `reports/`, and `docs/` folders. Copy original inputs into the appropriate raw/reference folders only after Task 1 verifies them.

**Done when:** The expected folders exist, input copies match the originals, and the original root CSVs are untouched.

### Task 4 - Set Up Python and Dependencies

**Status: Complete** - Python 3.11.14 is managed locally with `uv`; `.venv` was created, dependencies installed from `requirements.txt`, and the documented import/pytest checks passed.

**Goal:** Make the project reproducible on the user's Mac.

**Work:** Create a virtual environment, add only the initial dependencies, and document how to activate the environment and run a basic check.

**Done when:** A fresh environment can install the dependencies and run the documented check successfully. Python version and setup commands are recorded in the README.

### Task 5 - Define the Data Dictionary and Model

**Status: Complete** - Added `docs/data_dictionary.md` and `docs/data_model.md`, linked them from the README, and verified coverage of all source columns and target KPI names. Unavailable formula inputs are documented rather than inferred.

**Goal:** Explain each field and how the tables relate.

**Work:** Document field meanings, types, required status, allowed values/ranges, KPI formulas, table grain, keys, and relationships. Keep a simple establishment-month performance fact table and dimensions only where useful.

**Done when:** `docs/data_dictionary.md` and `docs/data_model.md` agree with the actual CSV schemas. Each KPI has a formula, unit, direction, and target source where available; missing or unclear numerator/denominator fields are explicitly marked as unavailable.

## Phase 2: Make a Repeatable Data-Quality Pipeline

### Task 6 - Build Raw Ingestion and Run Metadata

**Status: Complete** - Added `src/ingestion.py` and focused tests. The supplied 362-row CSV was ingested into a run-specific Bronze folder unchanged; metadata was written and a same-run retry correctly returned `already_ingested`.

**Goal:** Read files safely and record which pipeline run processed them.

**Input:** `data/raw/performance_dirty.csv` by default. The source CSV is intentionally dirty; ingestion checks its structure but does not repair or reject semantic data-quality issues.

**Files:** Implement `src/ingestion.py`; add focused tests in `tests/test_ingestion.py`; document usage in `README.md`.

**Required behavior:**

1. Provide `ingest_csv(source_path, output_dir="data/bronze", run_id=None, schema_version="v1")` and a CLI runnable as `python -m src.ingestion`.
2. Require all 24 performance columns documented in `docs/data_dictionary.md`. Reject empty files, blank or duplicate headers, missing required columns, and rows whose field count differs from the header. Extra columns may be retained.
3. Do not parse, clean, normalize, or serialize the source records into another format. Copy the source bytes unchanged to `data/bronze/<run_id>/<source_filename>.csv`.
4. Write `data/bronze/<run_id>/metadata.json` containing `run_id`, UTC ingestion timestamp, source filename, source SHA-256, schema version, row count, column count, and Bronze file path. The JSON is run metadata only; it is not a JSON version of the input dataset.
5. Generate a UTC timestamp run ID when none is supplied. Reject unsafe run IDs that could create paths outside the Bronze folder.
6. If the same run ID is repeated for the same filename, file hash, and schema version, return `already_ingested` without creating another output. If the run ID is reused for changed input/schema or an incomplete prior output, fail clearly and do not overwrite it.
7. Report a clear error for a missing or unreadable source and for each structural CSV error. Blank data cells and invalid dates/numbers are preserved for later profiling and validation.

**Tests:** Cover byte-for-byte CSV preservation, metadata contents, missing source, missing required columns, malformed row width, a safe retry with the same run ID, and rejection when the same ID is reused with changed contents.

**Verification:** Run `python -m pytest tests/test_ingestion.py -q`; run `python -m src.ingestion --run-id <test-id>` twice and confirm the second result is `already_ingested` and the Bronze CSV matches the source.

**Done when:** The required behavior and tests above pass, and the README explains the CSV-versus-JSON output distinction and command usage.

### Task 7 - Build a First Data-Profile Report

**Status: Complete** - Added `src/profiling.py` and focused tests. The Task 6 acceptance run produced a profiling report for the 362-row input. Its generated run folder was later pruned as superseded; the profiling implementation and reproducible tests remain.

**Goal:** Show what is in the input before any cleaning or validation changes it.

**Inputs:** `data/bronze/<run_id>/performance_dirty.csv` and its sibling `metadata.json` produced by Task 6.

**Files:** Implement `src/profiling.py`; add `tests/test_profiling.py`; write a run-specific HTML report under `reports/<run_id>/profiling_report.html`.

**Required behavior:**

1. Read the Bronze CSV without modifying it and verify its run ID/source metadata.
2. Report row/column counts, column names, inferred types, missing counts and percentages, exact duplicates, duplicate establishment-month keys, category frequencies, numeric min/max/quantiles, and date coverage.
3. Report invalid or unparseable values separately from missing values; do not silently coerce or repair them.
4. Include the run ID, source filename/hash, generated timestamp, and a visible synthetic-data note.
5. Describe anomalies as profiling observations only, not confirmed errors.

**Tests:** Use a small in-test table with known missing, duplicate, category, numeric, and date values. Verify the generated profile summaries against hand-counted expected results.

**Verification:** Run `python -m pytest tests/test_profiling.py -q` and `python -m src.profiling --run-id <existing-run-id>`; confirm the HTML report exists and its counts match the source.

**Done when:** The tests pass, the report is reproducible for a run, and its metrics match the supplied dirty file without altering the Bronze CSV.

### Task 8 - Define Beginner-Level Quality Rules

**Status: Complete** - Added 18 declarative rules in `rules/quality_rules.yml`, a schema loader in `src/rule_config.py`, six configuration tests, and documented project-assumption thresholds. The loader validates configuration only; it does not yet apply rules to records.

**Goal:** Put the first agreed data-quality checks in a readable configuration file.

**Inputs:** `docs/data_dictionary.md`, `docs/assumptions.md`, the establishment reference file, and the profiling report.

**Files:** Create `rules/quality_rules.yml`; add `tests/test_quality_rules.py` for configuration structure.

**Required behavior:**

1. Define a documented YAML structure with stable `rule_id`, field(s), check name, parameters, severity, action (`flag` or `quarantine`), and plain-language description.
2. Include only the first deterministic rules: required identifiers/month, non-negative counts, percentage range, valid month/date parsing, reference ID existence, duplicate business-key detection, and the approved category lookup.
3. Treat occupancy above 100% as a review flag, not an automatic invalid value.
4. Do not add statistical anomaly detection, imputation, fuzzy matching, or automatic correction in this rule set.
5. Document rule IDs, thresholds, and their project-assumption status in `docs/assumptions.md`.

**Tests:** Check that YAML parses, rule IDs are unique, every rule has required keys, and configured fields/check names are known to the planned validation engine.

**Verification:** Run `python -m pytest tests/test_quality_rules.py -q` and load the YAML in a short Python command without errors.

**Done when:** A beginner can read each rule and tell what it checks, what happens on failure, and whether the value is an assumed threshold.

### Task 9 - Implement Validation Results

**Status: Complete** - Added `src/validation.py` and tests for all supported check types. The actual run produced 24 issue rows across 23 source records (8 quarantine actions, 16 flags); the summary status is `FAIL`. The occupancy review rule correctly flags 9 values above 100%. Bronze input remains unchanged.

**Goal:** Run the configured rules and produce traceable, understandable issue records.

**Inputs:** Bronze CSV plus metadata, establishment reference CSV, and `rules/quality_rules.yml`.

**Files:** Implement `src/validation.py`; add `tests/test_validation.py`; write issue results to `data/silver/<run_id>/validation_issues.csv` or an equivalently documented machine-readable path.

**Required behavior:**

1. Assign each source row a stable `source_record_id` from source filename and CSV row number; do not add it to or rewrite the raw file.
2. Apply each configured rule and emit one issue row per failed rule per source record. When a rule covers multiple fields, group the failing field names in `field` and store a field-to-value JSON object in `observed_value`.
3. Include `run_id`, `source_record_id`, rule ID, field, observed value, failure reason, severity, action, status (`FAIL` for quarantine action, `WARNING` for flag action), and detection timestamp.
4. Preserve blank and malformed source values in issue evidence. Do not clean values in the validator.
5. Produce a run summary with input-row count, rule checks, failures by rule/severity, and records with one or more failures.
6. Fail clearly for malformed rule configuration, unknown rule types, and unavailable reference files; do not silently skip rules.

**Tests:** For every supported check, include one passing and one failing example row. Confirm that one row failing multiple rules produces multiple issue rows but is counted as one affected record.

**Verification:** Run `python -m pytest tests/test_validation.py -q` and `python -m src.validation --run-id <existing-run-id>`; compare selected issue counts with the inventory report.

**Done when:** Rule results are repeatable, traceable to exact source rows, and separate from any data-cleaning actions.

### Task 10 - Separate Quarantined Records

**Status: Complete** - Added `src/quarantine.py` and tests. The actual run wrote the quarantine package with 355 eligible, 3 quarantined, and 4 duplicate-held rows (362 total); all 7 blocked rows and their 8 issue rows are traceable, and Bronze input remains unchanged.

**Goal:** Keep records blocked by configured rules out of accepted Silver data while preserving evidence.

**Inputs:** Bronze CSV and validation issues from the same `run_id`.

**Files:** Implement `src/quarantine.py`; add `tests/test_quarantine.py`; write `data/quarantine/<run_id>/quarantined_records.csv`, `issues.csv`, `dispositions.csv`, and a small `summary.json`.

**Required behavior:**

1. Block a source record when at least one associated issue has action `quarantine`; records with only `flag` issues remain eligible but flagged.
2. Preserve the original source columns and add `source_record_id` only to the quarantine output.
3. Preserve all issue rows for each quarantined record; do not count issue rows as separate input records.
4. Ensure every source record has one disposition: eligible, quarantined, or duplicate-held. Treat duplicate-only records with `DUP-001` as `duplicate-held`; if the record has another quarantine-action issue too, classify it as `quarantined`. Both blocked dispositions appear in `quarantined_records.csv` with the disposition shown. Duplicate candidates are not silently deleted.
5. Keep run ID and source lineage in every output and never modify Bronze input.

**Tests:** Check flag-only rows remain eligible, blocking rows are quarantined, duplicate-only rows are held, a duplicate with another blocker is quarantined, multiple issues remain attached, duplicate candidates are accounted once by record, and all dispositions reconcile to the Bronze row count.

**Verification:** Run `python -m pytest tests/test_quarantine.py -q` and inspect the run summary for the actual input.

**Done when:** Every source record is accounted for exactly once by disposition and each quarantined row is traceable to its issue details.

### Task 11 - Implement Safe, Explainable Cleaning

**Status: Complete** - Added `src/cleaning.py`, `rules/cleaning_policies.yml`, and focused tests. The actual run produced 355 eligible Silver rows, excluded 3 quarantined and 4 duplicate-held records, mapped only the three approved exact aliases, preserved three remaining missing measures, and left Bronze unchanged.

**Goal:** Produce standardized Silver data without hiding uncertainty or changing the raw evidence.

**Inputs:** Eligible records, quarantine dispositions, reference data, and explicit mappings in the cleaning-policy file.

**Files:** Implement `src/cleaning.py`; create/update `rules/cleaning_policies.yml`; add `tests/test_cleaning.py`; write `data/silver/<run_id>/performance_clean.csv`, `transformation_log.csv`, `cleaning_issues.csv`, and `cleaning_summary.json`.

**Required behavior:**

1. Parse valid numeric/date values to documented output types and preserve parse failures as issues.
2. Standardize only approved category aliases that map unambiguously to a reference value.
3. Do not impute missing operational facts, infer missing denominators, or automatically correct outliers.
4. Keep duplicate-key candidates on hold until a separate approved duplicate-resolution decision exists; do not keep the latest row by default.
5. For every transformation, log run ID, source record ID, field, original value, cleaned value, and rule/method.
6. Preserve an output lineage back to Bronze and include only rows whose Task 10 disposition is `eligible`; exclude both `quarantined` and `duplicate-held` records.

**Tests:** Verify approved category mapping, unchanged valid values, no invented values for nulls, parse-failure handling, duplicate hold behavior, and transformation-log accuracy.

**Verification:** Run `python -m pytest tests/test_cleaning.py -q`; compare the Bronze file hash before/after and confirm it is unchanged.

**Done when:** Silver output contains only eligible records, each changed value or parse failure is auditable, missing values remain missing, and unresolved issues remain flagged or quarantined.

### Task 12 - Reconcile Counts and Measures

**Status: Complete** - Added `src/reconciliation.py` and tests. The actual run reconciled all 362 records (355 eligible, 3 quarantined, 4 duplicate-held); all six selected additive measures balanced across Silver, quarantined, and held groups. The single missing population value is reported separately. Overall status is `PASS`.

**Goal:** Demonstrate where every input row and selected measure went during processing.

**Inputs:** Bronze row inventory, Silver accepted rows, quarantine/hold outputs, and transformation log for one run.

**Files:** Implement `src/reconciliation.py`; add `tests/test_reconciliation.py`; write `reports/<run_id>/reconciliation.json` and a readable summary for later reporting.

**Required behavior:**

1. Reconcile distinct source records, not issue rows: `input rows = accepted rows + quarantined rows + duplicate-held rows`.
2. Keep the disposition groups mutually exclusive so duplicate rows are not subtracted twice because they also have quality issues.
3. Compare selected additive measures (for example population and incident counts) across accepted, quarantined, and held records where values are parseable.
4. Report missing/unparseable measure values separately; never treat them as zero.
5. Report each difference with its reason and a pass/fail status. Do not add “corrected records” to a row-count equation because correction does not create a new row.

**Tests:** Use a small in-test dataset with accepted, quarantined, held, and missing-measure rows; assert exact counts and totals.

**Verification:** Run `python -m pytest tests/test_reconciliation.py -q` and confirm the real run balances without unexplained row differences.

**Done when:** All input records have one disposition, measure differences are explainable, and unexplained count differences fail the check.

### Task 13 - Calculate Data-Quality Scores

**Status: Complete** - Added `src/quality_scoring.py`, `rules/quality_dimensions.yml`, and tests. The actual run scored five dimensions, marked consistency `NOT_AVAILABLE`, kept 9 occupancy review alerts outside the score, and produced an unweighted overall score of 99.23% with denominators shown.

**Goal:** Summarize data quality in a way a manager can interpret and an analyst can reproduce.

**Inputs:** Bronze source records, validation issues, rule configuration, and source-record dispositions from the same run. All raw source records remain in the score population; quarantine disposition does not hide failures.

**Files:** Implement `src/quality_scoring.py`; create `rules/quality_dimensions.yml`; add `tests/test_quality_scoring.py`; write `reports/<run_id>/dq_scores.csv` and `dq_score_summary.json`.

**Required behavior:**

1. Calculate dimension scores for completeness, validity, uniqueness, consistency, referential integrity, and timeliness.
2. Score each source record at most once per dimension. A record is applicable when at least one rule in that dimension can be evaluated; report passed, failed, applicable, and excluded counts, score, and issue-row count.
3. Count affected records once per dimension even if several rules in that dimension fail; retain issue-level counts separately.
4. If no record has an applicable check in a dimension, report `NOT_AVAILABLE`, not 100% or zero. Consistency is currently unavailable because no consistency rules are configured.
5. Score all raw source records, including quarantined and duplicate-held records, so removing failed records cannot increase the score. Report their dispositions separately.
6. Calculate each dimension as `(applicable records - failed records) / applicable records * 100`.
7. Calculate the overall score as the unweighted average of available dimension scores; label it as a project assumption and keep dimension scores visible.
8. Configure review-only rules separately; occupancy review flags do not count as confirmed validity failures or enter the score.

**Tests:** Verify hand-calculated scores, multiple failures on one record, empty denominators, and separate issue counts.

**Verification:** Run `python -m pytest tests/test_quality_scoring.py -q` and compare a sample report score to a hand calculation.

**Done when:** Each displayed score has a visible denominator and can be reproduced from the rule-level results.

### Task 14 - Calculate Trusted KPIs

**Status: Complete** - Added `src/kpis.py`, `rules/kpi_mappings.yml`, and tests. The actual run produced typed performance Parquet plus 2,840 row-level KPI results for 355 eligible records and 8 KPIs. Recalculated metrics carry targets/variance only when inputs are valid; supplied/unclear metrics are not compared. Weighted rate aggregation uses summed numerators and denominators.

**Goal:** Produce Gold measures only where inputs and definitions support a valid calculation.

**Inputs:** Accepted Silver performance records, establishment/date/KPI references, and quality scores/issues.

**Files:** Implement `src/kpis.py`; create `rules/kpi_mappings.yml`; add `tests/test_kpis.py`; write `data/gold/<run_id>/performance.parquet`, `kpi_results.parquet`, and `kpi_summary.json`.

**Required behavior:**

1. Recalculate occupancy as population / certified normal accommodation * 100.
2. Recalculate assaults, self-harm, and security incidents per 100 as validated incident count / validated population * 100.
3. Mark education attendance and purposeful activity as supplied-only. Mark sickness and resignation rates as supplied-only because their required denominators/numerators are missing.
4. Never calculate a rate when a required value is missing, invalid, or the denominator is zero; return an unavailable value with a reason.
5. Join target, direction, unit, and KPI availability from `kpi_targets.csv` using a documented mapping. Do not silently compare mismatched or undefined measures.
6. Include a trust status and relevant quality issue/dimension reference for each KPI record.
7. Aggregate rates from summed numerator / summed denominator; do not average establishment percentages unless a valid weighting denominator exists.

**Tests:** Hand-check each recalculated formula, zero/missing denominators, supplied-only KPI handling, target mapping, aggregation across two records, and trust status.

**Verification:** Run `python -m pytest tests/test_kpis.py -q` and compare selected Gold rows to hand calculations.

**Done when:** Gold outputs contain reproducible measures, correctly marked supplied-only/unavailable KPIs, and no rate is presented as trusted when supporting data fails its rules.

## Phase 3: Test and Present the Results

### Task 15 - Add Focused Tests

**Status: Complete** - Audited the focused tests added with Tasks 6-14; all implemented core modules have deterministic normal/edge-case coverage. Added full/focused test commands to the README. The full suite contains 37 passing tests.

**Goal:** Check each pipeline behavior independently with small predictable inputs.

**Inputs:** The module behavior implemented in Tasks 6-14 and the real dirty input for occasional integration checks.

**Files:** Maintain focused tests under `tests/`; use small inline test rows or narrowly scoped pytest fixtures. Do not create a standalone generated dataset or modify the supplied dirty input.

**Required behavior:**

1. Cover expected-success and expected-failure paths for ingestion, profiling, each configured rule, quarantine, cleaning, reconciliation, quality scoring, and KPI calculations.
2. Use small cases with explicit expected values so a beginner can understand why a test passes or fails.
3. Keep tests deterministic; do not rely on current time, random values, network access, or full production-sized data.
4. Run all tests from the project root using the `.venv` environment.

**Verification:** Run `python -m pytest -q`; failures must identify the behavior and expectation clearly.

**Done when:** Each implemented core module has focused tests for normal and edge cases, and the full local test suite passes.

### Task 16 - Add One End-to-End Pipeline Test

**Status: Complete** - Added `src/pipeline.py` and `tests/test_pipeline.py`. The temporary-project success test verifies all nine stages and Gold outputs; the injected validation-stage configuration failure stops execution and leaves no Gold output. A real 362-row smoke run also completed and wrote its summary.

**Goal:** Prove the core modules work together from input to Gold output.

**Inputs:** A small CSV created inside the test with valid rows and known defects; references and rules from temporary test directories.

**Files:** Implement a small orchestration entry point in `src/pipeline.py` if one does not already exist; add `tests/test_pipeline.py`.

**Required behavior:**

1. Execute ingestion, profiling, validation, quarantine, cleaning, reconciliation, quality scoring, and KPI generation in dependency order for one run.
2. Use temporary output directories so tests never write over project data.
3. Return or persist a run summary with the run ID, row dispositions, quality-gate inputs, and Gold output paths.
4. Make a failure in a required stage stop later publication rather than silently producing partial Gold output.
5. A validation summary with status `FAIL` is a completed validation stage with recorded issues, not a stage execution crash. Include it in `quality_gate_inputs`; Task 17 will enforce release policy. Until then, completed Gold output is not automatically release-approved.

**Tests:** Include at least one valid row, one quarantined row, and one row with a recalculable KPI; assert output values, issue details, row counts, and reconciliation.

**Verification:** Run `python -m pytest tests/test_pipeline.py -q` and confirm the test leaves no files in `data/` or `reports/`.

**Done when:** One deterministic test demonstrates the full path and validates exact expected Gold rows and counts without reading the entire supplied file.

### Task 17 - Create the Data-Quality Release Gate

**Status: Complete** - Added `rules/quality_gate.yml`, `src/quality_gate.py`, tests, and pipeline integration. The real dirty run was blocked by 2 Critical issues and a 2.7624% High-affected-record rate; no Gold output was created. All 49 project tests pass.

**Goal:** Prevent a run that breaches agreed controls from being published as trusted Gold data.

**Inputs:** Validation summary, quarantine/disposition summary, quality scores, and reconciliation result from one run.

**Files:** Create `rules/quality_gate.yml`; implement `src/quality_gate.py`; add `tests/test_quality_gate.py`; write a gate result into `reports/<run_id>/quality_gate.json`.

**Required behavior:**

1. Block publication for any critical issue configured as blocking.
2. Calculate High-severity rate as distinct source records with at least one High issue divided by total input records * 100; block if it exceeds the configured maximum (initial assumption: 0.5%).
3. Block if the overall DQ score is unavailable or below the configured minimum (initial assumption: 95%).
4. Require reconciliation status `PASS`; block on any other status.
5. Return `PASSED` or `BLOCKED`, the run ID, each check result, actual value, threshold, and a plain-language reason in `reports/<run_id>/quality_gate.json`.
6. A blocked run must not create or replace Gold outputs; gate evaluation runs before KPI generation and publication.
7. Keep `flag`-only/review-only exceptions visible without treating every anomaly as a blocking data error.
8. Label all numeric limits as project assumptions, not official MoJ/HMPPS limits.

**Tests:** Test a passing run, each individual blocking condition, multiple simultaneous failures, and confirmation that blocked data is not published.

**Verification:** Run `python -m pytest tests/test_quality_gate.py -q` and the pipeline test for both pass and block cases.

**Done when:** The gate is deterministic, its thresholds are documented, and blocked runs cannot be mistaken for trusted releases.

### Task 18 - Create Human-Readable Reports

**Status: Complete** - Added `src/reporting.py` and tests. The real blocked run generated `reports/pipeline-task17-20260929/run_report.html`, combining profile findings, top rule priorities/actions, scores, reconciliation, gate checks, and Gold availability. It fills source metadata from available stage summaries when the pipeline failure summary omits it.

**Goal:** Let an analyst understand a pipeline run without inspecting source code or raw logs.

**Inputs:** Profiling summary, validation/quarantine results, quality scores, reconciliation result, and gate result.

**Files:** Implement `src/reporting.py`; add `tests/test_reporting.py`; write a consolidated `reports/<run_id>/run_report.html`. Reuse/link existing profiling, score, reconciliation, gate, and pipeline reports instead of duplicating them.

**Required behavior:**

1. Show run ID, pipeline outcome, source filename/hash, timestamps, record count, dispositions, and whether Gold/KPI outputs were published.
2. Show Critical/High issue totals and top failing rules ranked by severity/affected-record count, with fields, rule meaning, and cautious next investigation steps.
3. Show dimension scores with denominators, `NOT_AVAILABLE` dimensions, reconciliation results, and each release-gate check with actual, threshold, and reason.
4. Link to detailed profiling, DQ score, reconciliation, gate, validation, and quarantine artifacts when present.
5. Explain that validation findings are not proof of operational error; distinguish pipeline execution status from release-gate decision.
6. Clearly label all data as synthetic and note important assumptions/limitations.
7. Escape data-derived text before inserting it into HTML.
8. Generate the report from machine-readable outputs so displayed figures cannot diverge from pipeline results. Use available stage metadata when the pipeline summary lacks source fields.

**Tests:** Check required sections and counts are present, synthetic label appears, special characters are escaped, and blocked status is shown correctly.

**Verification:** Run `python -m pytest tests/test_reporting.py -q` and `python -m src.reporting --run-id <run-id>`; compare displayed counts, rule summaries, and gate checks to the machine-readable outputs.

**Done when:** A beginner can identify run outcome, release decision, top data issues, trusted/unavailable measures, reconciliation status, and next investigation without opening source code.

### Task 18.1 - Debug Initial POC Acceptance

**Status: Complete.** Persistent run `pipeline-task18-1-poc-pass-20261002` used `data/raw/performance_poc_pass.csv` (4 eligible rows; source SHA-256 `130f5d5a1ac4d7107a859135a1d17c56d40876f0f14ae89d3f4bed25fc2b81ce`) and passed validation, reconciliation, DQ scoring (100%), and all quality-gate checks. Gold Parquet contains 4 performance rows and 32 KPI rows; `reports/pipeline-task18-1-poc-pass-20261002/run_report.html` records the run. The dirty Task 17 run remains blocked with no Gold.

**Goal:** Establish reproducible evidence that the current pipeline can publish Gold for a valid synthetic POC input while still blocking the intentionally dirty input.

**Inputs:** The existing passing and blocked cases in `tests/test_pipeline.py`, current gate policy, synthetic project assumptions, and Task 18 run report.

**Files:** Update `tests/test_pipeline.py` or add a small deterministic synthetic fixture only if needed; persist the accepted POC run under its own `data/gold/<run_id>/` and `reports/<run_id>/` folders; record acceptance evidence in the README. Keep `data/raw/performance_dirty.csv` unchanged.

**Required behavior:**

1. Reproduce a deterministic synthetic input that should pass the existing configured gate without weakening thresholds or cleaning the intentionally dirty raw input.
2. Run the full pipeline with a unique POC run ID and diagnose/fix any genuine pipeline, fixture, or policy mismatch.
3. Verify the same run has pipeline status `SUCCEEDED`, gate decision `PASSED`, `release_allowed: true`, reconciliation `PASS`, and published Gold Parquet outputs.
4. Verify the dirty-input case remains `BLOCKED` and creates no Gold output.
5. Record source hash, run ID, input/output row counts, gate results, and report paths. Do not use Task 16 pre-gate Gold as passing-run evidence.

**Verification:** Run `python -m pytest tests/test_pipeline.py -q`; execute the persistent passing POC run; inspect its gate/pipeline summaries and Gold outputs; rerun the dirty blocked case or rely on the deterministic blocked test and record its no-Gold assertion.

**Done when:** Persistent, reproducible pass and block evidence is recorded and the pipeline publishes the expected Gold tables for the passing run only.

### Initial POC Acceptance (Tasks 1-18.1)

**Status: Complete.** The persistent passing run and the dirty-run block are verified. Superseded Task 6 and Task 16 run outputs were pruned and are not acceptance evidence.

Before calling the POC complete:

1. Run a persistent deterministic synthetic case through the current pipeline. Verify `quality_gate.json` says `PASSED`, `pipeline_summary.json` says `SUCCEEDED`, and Gold Parquet is published for that same run ID.
2. Verify the dirty-input case is blocked and publishes no Gold output.
3. Record the run ID, source hash, input/output row counts, gate result, and generated report path in the README and task record.

Do not use the blocked Task 17 run as Gold or call the pre-gate Task 16 snapshot gate-approved. The first POC is complete when the passing run's Gold dataset and run evidence are reproducible.

## Phase 4: Automation and Portfolio Finish

### Task 19 - Add GitHub Actions CI (Future Enhancement)

**Status: Deferred until after the initial POC is accepted.**

**Goal:** Run the repeatable code and pipeline checks whenever changes are pushed or proposed.

**Inputs:** Python 3.11 setup, `requirements.txt`, tests, and the pipeline/gate CLI from earlier tasks.

**Files:** Create `.github/workflows/data_quality.yml`; update CI documentation in `README.md`.

**Required behavior:**

1. Run on pull requests and pushes to the main development branch.
2. Use a supported Ubuntu runner and Python 3.11; install dependencies from `requirements.txt`.
3. Run `python -m pytest -q` and the pipeline on a small deterministic test input.
4. Fail the job when tests, reconciliation, or the configured data-quality gate fail.
5. Upload run reports as CI artifacts when generated; do not commit transient outputs or store secrets in workflow text.

**Verification:** Run the same commands locally, then inspect one successful CI run and one controlled failure (such as a deliberately failing test on a temporary branch/change).

**Done when:** CI passes on valid changes, fails clearly on a controlled failure, and displays test/gate output and reports.

### Task 20 - Add an Overnight Schedule (Optional)

**Status: Deferred; optional future enhancement.**

**Goal:** Run the data pipeline automatically at an agreed time after the manual run is reliable.

**Inputs:** A passing local pipeline command, chosen runtime, desired schedule/time zone, and output/log retention choice.

**Files:** Choose one route only: a documented macOS `launchd` plist under `scheduling/`, or a scheduled GitHub Actions workflow under `.github/workflows/`. Record setup/removal instructions in `docs/scheduling.md`.

**Required behavior:**

1. Call the same pipeline command used manually; do not fork pipeline logic in the scheduler.
2. Record start/end time, run ID, exit status, and log/report location.
3. Make the schedule time zone explicit and prevent overlapping runs.
4. For `launchd`, document that the Mac must be powered/awake and the user session/environment must support the job. For cloud CI, document runner and repository scheduling limitations.

**Verification:** Trigger one manual scheduled-command test, inspect the logs and output, then verify one scheduled execution if the selected environment permits it.

**Done when:** The chosen scheduler reliably invokes the pipeline and reports success/failure; otherwise leave this optional task deferred and document the blocker.

### Task 21 - Complete README and POC Evidence

**Goal:** Make the project reproducible and easy to explain to a reviewer or interviewer.

**Inputs:** Completed project outputs, tests, and documented assumptions.

**Files:** Update `README.md`; keep detailed definitions in the linked pipeline/data documents.

**Required behavior:**

1. Explain the business question, synthetic data source, architecture, setup/run commands, and directory layout.
2. Link the data dictionary, model, assumptions, quality rules, and action-plan documents.
3. Explain KPI formulas, source limitations, the data-quality gate, and reconciliation.
4. Include real generated findings from the project outputs only; label examples as synthetic and do not claim operational impact.
5. Include test/CI evidence, known limitations, and a cautious recommendation that separates evidence from interpretation.

**Verification:** Follow the README in a fresh terminal with `.venv` activated and ask a reader unfamiliar with the project to locate the inputs, run the pipeline, and explain the Gold/run evidence.

**Done when:** A new reader can run the project and distinguish synthetic results, supplied-only measures, derived measures, quality issues, and recommendations.

## Advanced Extensions (Only After the Core Project Works)

### Task 22 - Add Fuzzy Category Matching

**Goal:** Suggest corrections for category values that are close to, but not exactly, an approved reference value.

**Inputs:** Approved canonical categories, observed category variants, and the cleaning policies from Task 11.

**Files:** Add an optional matcher in `src/cleaning.py` or `src/category_matching.py`; add `tests/test_category_matching.py`; record thresholds/approved aliases in `rules/cleaning_policies.yml`.

**Required behavior:** Use RapidFuzz only to suggest a mapping; record original value, suggested value, similarity score, and decision. Auto-apply only explicitly approved high-confidence mappings. Send ambiguous/low-confidence values to review and never fuzzy-match establishment IDs.

**Verification:** Test exact matches, one approved near-match, ambiguous ties, and low-confidence rejection. Confirm transformation logs record the score and chosen action.

**Done when:** The optional matcher is explainable and repeatable, and uncertain suggestions cannot silently reach Gold.

### Task 23 - Add Drift and Robust Anomaly Monitoring

**Goal:** Flag unusual distribution changes or month-to-month movements for analyst investigation.

**Inputs:** Validated historical periods, KPI definitions, and a documented baseline window. Do not use malformed or quarantined values to set the baseline.

**Files:** Implement optional checks in `src/anomaly.py`; add `tests/test_anomaly.py`; write rule thresholds/methods to `rules/anomaly_rules.yml` and explain them in `docs/methodology.md`.

**Required behavior:** Select a method suitable for each field (for example MAD/IQR for robust univariate screening or PSI/KS for distribution comparisons). Define comparison window, minimum observations, seasonality handling, threshold, and fallback when history is insufficient. An alert is not a validation failure unless a separate deterministic rule confirms it.

**Tests:** Use fixed synthetic test sequences with a stable series, a known spike, and too little history; assert expected alert/no-alert/insufficient-history outcomes.

**Verification:** Run `python -m pytest tests/test_anomaly.py -q` and review flagged examples against the source values.

**Done when:** Alerts include method, baseline/window, score, threshold, and review reason, and do not automatically quarantine records as erroneous.

### Task 24 - Evaluate Imputation or Probabilistic Duplicate Detection

**Goal:** Evaluate whether advanced inference offers value without silently turning guesses into reported facts.

**Inputs:** A specific documented missingness or duplicate problem, approved evaluation sample, and a simple non-imputed/manual-review baseline.

**Files:** Keep experiments in a clearly labeled `experiments/` area or notebook; add `docs/imputation_evaluation.md` or `docs/duplicate_matching_evaluation.md`; add tests for any proposed production rule.

**Required behavior:**

1. State the hypothesis and evaluation metric before testing an imputer or probabilistic duplicate matcher.
2. Compare with a simple baseline and report errors/uncertainty, including any segment-level bias risk.
3. Preserve original values and write inferred values to separate fields with method, confidence, and `imputed_flag`/`suspected_duplicate` status.
4. Do not use inferred operational values in Gold KPI calculations without explicit human approval and an updated project assumption.
5. Do not auto-delete probabilistic duplicate candidates.

**Verification:** Re-run the experiment with fixed inputs/seed and obtain the same results; review examples and failure cases manually.

**Done when:** The evaluation documents whether the method is useful, its limits, and a clear decision to reject it, keep it experimental, or propose a separately confirmed implementation task.

## Initial POC Exit

The initial POC ends at Task 18.1: reproducible gate-passed Gold publication and a separate blocked dirty-run result. Confirm any future scope before starting Task 19.
