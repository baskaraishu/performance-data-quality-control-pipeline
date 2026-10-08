# ⚖️ Justice Performance Data Quality and Operational Insights

A beginner-friendly portfolio project that profiles synthetic monthly performance data, checks its quality, and prepares trusted Gold performance and KPI outputs. The data is synthetic and does not represent confidential MoJ or HMPPS information.

[![Python 3.11](https://img.shields.io/badge/Python-3.11-3776AB?logo=python&logoColor=white)](https://www.python.org/downloads/release/python-3110/)
[![POC scope: Pipeline to Gold](https://img.shields.io/badge/POC-Pipeline%20to%20Gold-27856A)](#run-the-end-to-end-pipeline)

[🛠️ Setup](#development-setup-macos) · [🧪 Tests](#run-tests) · [🚀 Run the pipeline](#run-the-end-to-end-pipeline) · [📚 Project docs](#project-documents)

## Project Structure

```text
.
|-- data/
|   |-- raw/
|   |   |-- performance_dirty.csv
|   |   `-- performance_poc_pass.csv
|   |-- reference/
|   |   |-- establishments.csv
|   |   `-- kpi_targets.csv
|   |-- bronze/       # Generated per-run input snapshots; Git-ignored
|   |-- silver/       # Generated cleaned/validation outputs; Git-ignored
|   |-- quarantine/   # Generated dispositions and held records; Git-ignored
|   `-- gold/         # Generated Gold outputs; Git-ignored
|-- docs/
|   |-- assumptions.md
|   |-- business_problem.md
|   |-- data_dictionary.md
|   `-- data_model.md
|-- reports/
|   |-- data_inventory.md
|   `-- <run_id>/     # Generated run reports; Git-ignored
|-- rules/
|   |-- cleaning_policies.yml
|   |-- kpi_mappings.yml
|   |-- quality_dimensions.yml
|   |-- quality_gate.yml
|   `-- quality_rules.yml
|-- src/
|   |-- cleaning.py
|   |-- ingestion.py
|   |-- kpis.py
|   |-- pipeline.py
|   |-- profiling.py
|   |-- quality_gate.py
|   |-- quality_scoring.py
|   |-- quarantine.py
|   |-- reconciliation.py
|   |-- reporting.py
|   |-- rule_config.py
|   `-- validation.py
|-- tests/
|   `-- test_*.py
|-- .gitignore
|-- .python-version
|-- Implementation_Tasks.md
|-- Plan_updated.md
|-- README.md
`-- requirements.txt
```

Bronze, Silver, quarantine, Gold, and per-run report contents are produced by pipeline runs and excluded from Git. The empty `.github/workflows/` directory is omitted; no CI workflow is committed yet.

## Development Setup (macOS)

The project uses Python 3.11. The steps below use `uv` to install/manage that Python version locally and create an isolated virtual environment. This does not replace macOS's system Python.

### 1. Install `uv`

If `uv` is not already installed, install it with Homebrew:

```sh
brew install uv
```

### 2. Create and activate the project environment

Run these commands from the project root:

```sh
uv python install 3.11
uv venv --python 3.11 --seed .venv
source .venv/bin/activate
```

Activate `.venv` again whenever you open a new terminal for this project:

```sh
source .venv/bin/activate
```

### 3. Install project dependencies

```sh
python -m pip install -r requirements.txt
```

### 4. Verify the setup

```sh
python -c "import duckdb, pandas, pandera, pyarrow, yaml; print('Core project dependencies imported successfully')"
python -m pytest --version
```

At this stage, the ingestion step is available. The import check confirms the core libraries are available and pytest can run.

## Run Tests

With `.venv` active, run the full deterministic unit-test suite from the project root:

```sh
python -m pytest -q
```

To run one module's focused tests, pass its test file, for example:

```sh
python -m pytest tests/test_kpis.py -q
```

## Run Initial Ingestion

From the project root, with `.venv` active:

```sh
python -m src.ingestion
```

This validates the required performance-data columns, then copies the input unchanged into a timestamped folder under `data/bronze/` and writes `metadata.json` with the run ID, source hash, schema version, timestamp, and row count. To safely retry a run, provide the same explicit run ID:

```sh
python -m src.ingestion --run-id my-test-run
```

Reusing that ID with the same file does not create a duplicate. Reusing it with changed file contents returns an error instead of overwriting the earlier run.

## Profile an Ingested Run

After ingestion, create the run's HTML profile with:

```sh
python -m src.profiling --run-id <run-id>
```

The report is written to `reports/<run-id>/profiling_report.html`. It summarizes column types, missing and unparseable values, duplicates, categories, numeric ranges/quantiles, and date coverage. Profiling does not clean or modify the Bronze input.

## Validate an Ingested Run

Apply the configured rules to the same Bronze run with:

```sh
python -m src.validation --run-id <run-id>
```

The validator writes `data/silver/<run-id>/validation_issues.csv` and `validation_summary.json`. Each issue is traceable to a source row and rule. A run is `FAIL` when any rule requests quarantine, `WARNING` when it has only flag issues, and `PASS` when no rule fails. Validation records findings but does not clean or rewrite Bronze data.

## Separate Blocked Records

After validation, classify each source row with:

```sh
python -m src.quarantine --run-id <run-id>
```

The quarantine package is written to `data/quarantine/<run-id>/`. `dispositions.csv` has exactly one row per source record. `quarantined_records.csv` contains every blocked row, including duplicate-held rows, and labels its disposition. `issues.csv` contains every issue attached to those blocked rows. Flag-only rows remain eligible and are counted in the disposition ledger; their full issue detail remains in the validation output. `summary.json` keeps record counts separate from issue-row counts.

## Create Clean Silver Data

After Task 10 has produced dispositions, create the clean Silver output with:

```sh
python -m src.cleaning --run-id <run-id>
```

Only `eligible` rows are included. Numeric and date fields are parsed without repairing business-invalid values; missing values remain blank. Only exact aliases listed in `rules/cleaning_policies.yml` are standardized. The run writes `performance_clean.csv`, `transformation_log.csv`, `cleaning_issues.csv`, and `cleaning_summary.json` under `data/silver/<run-id>/`.

## Reconcile a Pipeline Run

After cleaning and quarantine have completed, reconcile row dispositions and selected additive totals with:

```sh
python -m src.reconciliation --run-id <run-id>
```

The command writes `reports/<run-id>/reconciliation.json` and `reconciliation_summary.md`. It checks that every Bronze row appears exactly once as eligible, quarantined, or duplicate-held, and compares additive measures across those groups. Missing and unparseable values are reported separately, not counted as zero.

## Calculate Data-Quality Scores

Calculate dimension scores after validation and dispositions are available:

```sh
python -m src.quality_scoring --run-id <run-id>
```

This writes `reports/<run-id>/dq_scores.csv`, `dq_score_summary.json`, and a readable `dq_score_summary.html`. To convert an existing JSON summary without recalculating scores:

```sh
python -m src.quality_scoring --summary-json reports/<run-id>/dq_score_summary.json
```

The HTML is written beside the JSON file. Scores use applicable source records as denominators, count each failed record once per dimension, and show excluded counts. The overall score is an unweighted project assumption; it does not override a blocking validation/release-gate failure. Dimensions without configured rules are shown as `NOT_AVAILABLE`.

## Calculate Trusted KPIs

After validation, cleaning, quarantine, and scoring, create the Gold KPI outputs with:

```sh
python -m src.kpis --run-id <run-id>
```

The command writes `data/gold/<run-id>/performance.parquet`, `kpi_results.parquet`, and `kpi_summary.json`. The KPI results include the actual/target, comparison status, availability, trust status, source fields, related validation issue IDs, and relevant DQ dimensions. The explicit formula and availability mapping is in `rules/kpi_mappings.yml`. Supplied-only or unclear measures are not compared to targets. For rate rollups, use summed numerator / summed denominator via the `aggregate_calculated_kpi` helper, not an average of row percentages.

## Run the End-to-End Pipeline

Run all required stages from the project root with:

```sh
python -m src.pipeline --run-id <unique-run-id>
```

The orchestrator writes `pipeline_summary.json` and a readable `pipeline_summary.html` under `reports/<run-id>/`, and publishes Gold files only after all required stage commands complete. To convert an existing JSON summary without rerunning the pipeline:

```sh
python -m src.pipeline --summary-json reports/<run-id>/pipeline_summary.json
```

A validation result of `FAIL` means data issues were recorded and dispositions applied; it is distinct from a stage execution failure. Release approval is determined by the quality-gate decision, not by pipeline execution status alone.

## Create a Consolidated Analyst Report

After pipeline stages have produced their machine-readable outputs, create one beginner-readable report with:

```sh
python -m src.reporting --run-id <run-id>
```

This writes `reports/<run-id>/run_report.html`, combining the run outcome, profile, row dispositions, highest-priority validation rules and investigation steps, DQ dimension scores, reconciliation, release-gate checks, Gold/KPI availability, and links to detailed reports. It labels unavailable results rather than implying they passed.

The pipeline applies `rules/quality_gate.yml` after reconciliation and scoring but before KPI generation or Gold publication. It writes `reports/<run-id>/quality_gate.json`. The initial project policy blocks any Critical issue, a High-severity affected-record rate above 0.5%, an overall DQ score below 95%, or a failed reconciliation. These thresholds are demonstration assumptions, not official MoJ/HMPPS limits.

To evaluate the gate separately for an existing run:

```sh
python -m src.quality_gate --run-id <run-id>
```

A blocked decision prevents a new Gold output. Validation can report issues without crashing; the gate decides whether the run may publish.

## Data Locations

- `data/raw/`: supplied raw learning input
- `data/reference/`: establishment and KPI reference files
- `data/bronze/`: immutable ingested source snapshots
- `data/silver/`: validation, cleaning, and reconciliation-ready outputs
- `data/gold/`: trusted analytical outputs created in later tasks
- `data/quarantine/`: records held for data-quality review

Generated run outputs under `data/bronze/`, `data/silver/`, `data/quarantine/`, `data/gold/`, and per-run `reports/` folders are excluded from Git. Recreate them by running the documented pipeline commands.

## Project Documents

- `Plan_updated.md`: beginner project plan
- `Implementation_Tasks.md`: task-by-task implementation backlog
- `docs/business_problem.md`: business problem and first-version scope
- `docs/assumptions.md`: agreed synthetic-data and KPI assumptions
- `docs/data_dictionary.md`: source fields, rules, and KPI definitions
- `docs/data_model.md`: table grain, keys, and relationships
- `rules/quality_rules.yml`: initial configurable data-quality checks and project-assumption thresholds
- `rules/quality_dimensions.yml`: mapping from validation rules to quality-score dimensions
- `rules/kpi_mappings.yml`: documented formula, source, target-comparison, and trust mapping for each KPI
- `rules/quality_gate.yml`: release controls and project-assumption thresholds
- `reports/data_inventory.md`: initial read-only data inventory

Task 18.1's persistent POC run is `pipeline-task18-1-poc-pass-20261002`: four synthetic PR001 establishment-month rows passed the gate and published Gold. The unchanged dirty Task 17 run remains blocked and has no Gold. Superseded Task 6 and pre-gate Task 16 generated run folders were removed during cleanup. The initial POC ends with reproducible Gold publication and its run report. See Task 18.1 in `Implementation_Tasks.md`.

To rerun the POC fixture, use a new run ID because completed Gold runs are not overwritten:

```sh
python -m src.pipeline --run-id <unique-run-id> --input data/raw/performance_poc_pass.csv
```
