"""Calculate explainable, record-based data-quality scores."""

from __future__ import annotations

import argparse
import html
import hashlib
import json
import math
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd
import yaml

from src.rule_config import RuleConfigError, load_rule_config

DIMENSIONS = (
    "completeness",
    "validity",
    "uniqueness",
    "consistency",
    "referential_integrity",
    "timeliness",
)
SCORE_COLUMNS = [
    "dimension",
    "status",
    "passed_records",
    "failed_records",
    "applicable_records",
    "excluded_records",
    "score_pct",
    "issue_count",
    "rule_ids",
]


class QualityScoringError(ValueError):
    """Raised when score inputs or dimension configuration are inconsistent."""


def render_score_summary_html(summary: dict[str, Any]) -> str:
    """Render a readable, self-contained HTML view of one score summary."""
    required = {"run_id", "dimension_results", "overall_score_pct"}
    missing = required - summary.keys()
    if missing:
        raise QualityScoringError(
            "Score summary is missing fields: " + ", ".join(sorted(missing))
        )

    def text(value: Any) -> str:
        return html.escape(str(value))

    def percentage(value: Any) -> str:
        return "Not available" if value is None else f"{float(value):.2f}%"

    dimension_rows = []
    for dimension in DIMENSIONS:
        result = summary["dimension_results"].get(dimension, {})
        score = result.get("score_pct")
        status = result.get("status", "NOT_AVAILABLE")
        label = dimension.replace("_", " ").title()
        if score is None:
            score_cell = '<span class="na">Not available</span>'
        else:
            numeric_score = max(0.0, min(100.0, float(score)))
            score_cell = (
                f'<strong>{percentage(score)}</strong>'
                f'<div class="meter" role="img" aria-label="{percentage(score)}">'
                f'<span style="width:{numeric_score:.2f}%"></span></div>'
            )
        denominator = result.get("applicable_records", 0)
        passed = result.get("passed_records")
        failed = result.get("failed_records")
        passed_failed = (
            "Not available" if passed is None or failed is None else f"{passed} / {failed}"
        )
        excluded = result.get("excluded_records", 0)
        issue_count = result.get("issue_count", 0)
        rules = ", ".join(result.get("rule_ids", [])) or "No configured rules"
        dimension_rows.append(
            "<tr>"
            f"<th scope=\"row\">{text(label)}</th>"
            f"<td><span class=\"status\">{text(status.replace('_', ' ').title())}</span></td>"
            f"<td>{score_cell}</td>"
            f"<td>{text(passed_failed)}</td>"
            f"<td>{text(denominator)}</td>"
            f"<td>{text(excluded)}</td>"
            f"<td>{text(issue_count)}</td>"
            f"<td class=\"rules\">{text(rules)}</td>"
            "</tr>"
        )

    disposition_counts = summary.get("disposition_counts", {})
    disposition_cards = "".join(
        f'<div class="stat"><span>{text(label)}</span><strong>{text(disposition_counts.get(key, 0))}</strong></div>'
        for key, label in (
            ("eligible", "Eligible"),
            ("quarantined", "Quarantined"),
            ("duplicate-held", "Duplicate held"),
        )
    )
    overall = percentage(summary.get("overall_score_pct"))
    overall_method = summary.get("overall_score_method", "Not documented")
    source_count = summary.get("source_record_count", "Not recorded")
    review_count = summary.get("review_only_issue_count", 0)
    review_records = summary.get("review_only_record_count", 0)
    generated_at = summary.get("generated_at_utc", "Not recorded")
    run_id = text(summary["run_id"])
    source_file = text(summary.get("source_file", "Not recorded"))
    source_hash = text(summary.get("source_sha256", "Not recorded"))
    population = text(summary.get("score_population", "Not recorded"))

    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Data Quality Score - {run_id}</title>
  <style>
    :root {{ color-scheme: light; --ink: #1e2b33; --muted: #56656d; --line: #d7e0e4; --surface: #f4f7f7; --teal: #176c69; --amber: #805d12; }}
    * {{ box-sizing: border-box; }}
    body {{ margin: 0; background: #fff; color: var(--ink); font: 15px/1.5 -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; }}
    main {{ max-width: 1180px; margin: 0 auto; padding: 2rem 1.25rem 3rem; }}
    h1, h2 {{ line-height: 1.2; }}
    h1 {{ margin: 0; font-size: 1.8rem; }}
    h2 {{ margin: 2rem 0 .75rem; font-size: 1.2rem; }}
    .eyebrow {{ color: var(--teal); font-size: .8rem; font-weight: 700; text-transform: uppercase; }}
    .top {{ display: flex; flex-wrap: wrap; justify-content: space-between; align-items: end; gap: 1.5rem; border-bottom: 1px solid var(--line); padding-bottom: 1.25rem; }}
    .overall {{ border-left: 4px solid var(--teal); padding: .2rem 1rem; min-width: 190px; }}
    .overall strong {{ display: block; font-size: 2rem; line-height: 1.1; }}
    .overall span {{ color: var(--muted); font-size: .9rem; }}
    .notice {{ margin: 1rem 0; padding: .8rem 1rem; background: #fff5d6; border-left: 4px solid #d2a12b; }}
    .stats {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(150px, 1fr)); gap: .7rem; margin: 1rem 0; }}
    .stat {{ display: flex; justify-content: space-between; gap: 1rem; padding: .75rem .9rem; background: var(--surface); border: 1px solid var(--line); }}
    .stat span {{ color: var(--muted); }}
    .meta {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(220px, 1fr)); gap: .5rem 1.5rem; color: var(--muted); font-size: .9rem; }}
    .meta code {{ color: var(--ink); overflow-wrap: anywhere; }}
    .table-wrap {{ overflow-x: auto; }}
    table {{ width: 100%; border-collapse: collapse; min-width: 900px; }}
    th, td {{ padding: .65rem .7rem; border-bottom: 1px solid var(--line); text-align: left; vertical-align: top; }}
    thead th {{ background: var(--surface); font-size: .82rem; }}
    tbody th {{ white-space: nowrap; }}
    .status {{ display: inline-block; padding: .15rem .4rem; background: #edf3f3; color: #27484b; font-size: .78rem; }}
    .meter {{ width: 110px; height: 5px; margin-top: .35rem; background: #e5ebed; }}
    .meter span {{ display: block; height: 100%; background: var(--teal); }}
    .na {{ color: var(--muted); font-style: italic; }}
    .rules {{ max-width: 200px; color: var(--muted); font-size: .8rem; overflow-wrap: anywhere; }}
    .review {{ padding: .8rem 1rem; border: 1px solid var(--line); }}
    footer {{ margin-top: 2rem; padding-top: 1rem; border-top: 1px solid var(--line); color: var(--muted); font-size: .85rem; }}
    @media (max-width: 600px) {{ main {{ padding: 1.25rem .75rem 2rem; }} .overall {{ width: 100%; }} }}
  </style>
</head>
<body>
  <main>
    <header class="top">
      <div><div class="eyebrow">Synthetic management information</div><h1>Data Quality Score</h1><p>Run <strong>{run_id}</strong></p></div>
      <div class="overall"><strong>{overall}</strong><span>Unweighted average of available dimensions</span></div>
    </header>
    <p class="notice"><strong>Interpret with care.</strong> This synthetic score is an overview, not a release decision. A high score does not override critical validation failures or the quality gate. Review the dimension results and issue counts below.</p>
    <section aria-label="Run record dispositions" class="stats">
      {disposition_cards}
      <div class="stat"><span>Source records</span><strong>{text(source_count)}</strong></div>
      <div class="stat"><span>Review-only alerts</span><strong>{text(review_count)}</strong></div>
      <div class="stat"><span>Records with review alerts</span><strong>{text(review_records)}</strong></div>
    </section>
    <h2>Dimension Scores</h2>
    <div class="table-wrap"><table>
      <thead><tr><th scope="col">Dimension</th><th scope="col">Status</th><th scope="col">Score</th><th scope="col">Passed / failed</th><th scope="col">Applicable</th><th scope="col">Excluded</th><th scope="col">Rule issues</th><th scope="col">Rules</th></tr></thead>
      <tbody>{''.join(dimension_rows)}</tbody>
    </table></div>
    <section class="review"><strong>Review-only alerts:</strong> {text(review_count)} issue rows across {text(review_records)} records. These are flagged for investigation and are not included as confirmed validity failures in the score.</section>
    <h2>Method</h2>
    <p>{text(overall_method)}. Each dimension uses <code>(applicable records - failed records) / applicable records * 100</code>. A record that fails several rules in one dimension counts once as a failed record; rule-issue counts remain visible. A dimension without applicable rules is shown as unavailable.</p>
    <p><strong>Scored population:</strong> {population}</p>
    <section class="meta" aria-label="Source metadata">
      <div><strong>Source file:</strong> {source_file}</div>
      <div><strong>SHA-256:</strong> <code>{source_hash}</code></div>
      <div><strong>Generated (UTC):</strong> {text(generated_at)}</div>
    </section>
    <footer>All records and targets are synthetic. This report does not represent MoJ or HMPPS operational performance and does not prove source-data accuracy.</footer>
  </main>
</body>
</html>
"""


def write_score_summary_html(
    summary: dict[str, Any],
    output_path: str | Path,
) -> Path:
    html_path = Path(output_path)
    html_path.parent.mkdir(parents=True, exist_ok=True)
    html_path.write_text(render_score_summary_html(summary), encoding="utf-8")
    return html_path


def convert_summary_file(
    summary_path: str | Path,
    output_path: str | Path | None = None,
) -> Path:
    json_path = Path(summary_path)
    try:
        summary = json.loads(json_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise QualityScoringError(f"Could not read score summary '{json_path}': {error}") from error
    html_path = Path(output_path) if output_path else json_path.with_suffix(".html")
    return write_score_summary_html(summary, html_path)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _is_blank(value: Any) -> bool:
    return value is None or str(value).strip() == ""


def validate_dimension_config(
    config: Any,
    rules: list[dict[str, Any]],
) -> dict[str, Any]:
    if not isinstance(config, dict) or config.get("schema_version") != 1:
        raise QualityScoringError("Dimension configuration must be a mapping with schema_version 1.")
    dimensions = config.get("dimensions")
    if not isinstance(dimensions, dict) or set(dimensions) != set(DIMENSIONS):
        raise QualityScoringError(
            "Dimension configuration must define exactly: " + ", ".join(DIMENSIONS)
        )

    configured_rule_ids = {rule["rule_id"] for rule in rules}
    assigned_rule_ids: set[str] = set()
    for dimension, rule_ids in dimensions.items():
        if not isinstance(rule_ids, list) or any(
            not isinstance(rule_id, str) for rule_id in rule_ids
        ):
            raise QualityScoringError(
                f"Dimension '{dimension}' must contain a list of rule IDs."
            )
        if len(rule_ids) != len(set(rule_ids)):
            raise QualityScoringError(
                f"Dimension '{dimension}' contains duplicate rule IDs."
            )
        assigned_rule_ids.update(rule_ids)

    review_only = config.get("review_only_rule_ids")
    if not isinstance(review_only, list) or any(
        not isinstance(rule_id, str) for rule_id in review_only
    ):
        raise QualityScoringError("review_only_rule_ids must be a list of rule IDs.")
    if len(review_only) != len(set(review_only)):
        raise QualityScoringError("review_only_rule_ids contains duplicate IDs.")
    review_only_ids = set(review_only)
    if assigned_rule_ids & review_only_ids:
        raise QualityScoringError("A rule cannot be both scored and review-only.")

    unknown = (assigned_rule_ids | review_only_ids) - configured_rule_ids
    missing = configured_rule_ids - (assigned_rule_ids | review_only_ids)
    if unknown:
        raise QualityScoringError(
            "Dimension configuration references unknown rules: "
            + ", ".join(sorted(unknown))
        )
    if missing:
        raise QualityScoringError(
            "Rules must be assigned to a dimension or review-only: "
            + ", ".join(sorted(missing))
        )
    return config


def load_dimension_config(
    path: str | Path,
    rules: list[dict[str, Any]],
) -> dict[str, Any]:
    config_path = Path(path)
    try:
        with config_path.open(encoding="utf-8") as source:
            config = yaml.safe_load(source)
    except OSError as error:
        raise QualityScoringError(
            f"Could not read dimension configuration '{config_path}': {error}"
        ) from error
    except yaml.YAMLError as error:
        raise QualityScoringError(
            f"Invalid YAML in '{config_path}': {error}"
        ) from error
    return validate_dimension_config(config, rules)


def _rule_applies(rule: dict[str, Any], record: dict[str, Any]) -> bool:
    values = [record.get(column, "") for column in rule["columns"]]
    if rule["check"] == "not_null":
        return True
    if rule["check"] == "unique_combination":
        return all(not _is_blank(value) for value in values)
    return any(not _is_blank(value) for value in values)


def calculate_scores(
    source_frame: pd.DataFrame,
    issues_frame: pd.DataFrame,
    rules: list[dict[str, Any]],
    dimension_config: dict[str, Any],
    source_file: str,
    run_id: str,
) -> dict[str, Any]:
    """Score each record once per applicable dimension using its failed rules."""
    dimension_config = validate_dimension_config(dimension_config, rules)
    if not {"rule_id", "source_record_id", "run_id"}.issubset(issues_frame.columns):
        raise QualityScoringError(
            "Validation issue file must include rule_id, source_record_id, and run_id."
        )

    rules_by_id = {rule["rule_id"]: rule for rule in rules}
    rule_dimensions: dict[str, set[str]] = {}
    for dimension, rule_ids in dimension_config["dimensions"].items():
        for rule_id in rule_ids:
            rule_dimensions.setdefault(rule_id, set()).add(dimension)

    source_records = source_frame.to_dict(orient="records")
    record_ids = [
        f"{source_file}:{row_number}"
        for row_number in range(2, len(source_records) + 2)
    ]
    if len(record_ids) != len(set(record_ids)):
        raise QualityScoringError("Could not generate unique source record IDs.")

    if not issues_frame.empty:
        if set(issues_frame["run_id"].astype(str)) != {run_id}:
            raise QualityScoringError(
                f"Validation issues must all belong to run '{run_id}'."
            )
        unknown_records = set(issues_frame["source_record_id"].astype(str)) - set(record_ids)
        if unknown_records:
            raise QualityScoringError(
                "Validation issues refer to unknown source records: "
                + ", ".join(sorted(unknown_records))
            )
        unknown_rules = set(issues_frame["rule_id"].astype(str)) - set(rules_by_id)
        if unknown_rules:
            raise QualityScoringError(
                "Validation issues refer to unknown rules: "
                + ", ".join(sorted(unknown_rules))
            )

    issues_by_dimension: dict[str, list[dict[str, Any]]] = {
        dimension: [] for dimension in DIMENSIONS
    }
    review_only_ids = set(dimension_config["review_only_rule_ids"])
    review_only_issues = []
    if not issues_frame.empty:
        for issue in issues_frame.to_dict(orient="records"):
            rule_id = str(issue["rule_id"])
            if rule_id in review_only_ids:
                review_only_issues.append(issue)
                continue
            for dimension in rule_dimensions.get(rule_id, set()):
                issues_by_dimension[dimension].append(issue)

    score_rows: list[dict[str, Any]] = []
    dimension_results: dict[str, dict[str, Any]] = {}
    for dimension in DIMENSIONS:
        dimension_rule_ids = dimension_config["dimensions"][dimension]
        applicable_ids: set[str] = set()
        for source_record_id, record in zip(record_ids, source_records):
            if any(
                _rule_applies(rules_by_id[rule_id], record)
                for rule_id in dimension_rule_ids
            ):
                applicable_ids.add(source_record_id)

        dimension_issues = issues_by_dimension[dimension]
        failed_ids = {
            str(issue["source_record_id"]) for issue in dimension_issues
        }
        unexpected_failures = failed_ids - applicable_ids
        if unexpected_failures:
            raise QualityScoringError(
                f"Dimension '{dimension}' has issue records outside its applicable denominator: "
                + ", ".join(sorted(unexpected_failures))
            )

        applicable_count = len(applicable_ids)
        failed_count = len(failed_ids)
        passed_count = applicable_count - failed_count
        score = (
            round(passed_count / applicable_count * 100, 2)
            if applicable_count
            else None
        )
        result = {
            "dimension": dimension,
            "status": "SCORED" if applicable_count else "NOT_AVAILABLE",
            "passed_records": passed_count if applicable_count else None,
            "failed_records": failed_count if applicable_count else None,
            "applicable_records": applicable_count,
            "excluded_records": len(source_frame) - applicable_count,
            "score_pct": score,
            "issue_count": len(dimension_issues),
            "rule_ids": dimension_rule_ids,
            "formula": "(applicable records - failed records) / applicable records * 100",
        }
        dimension_results[dimension] = result
        score_rows.append(
            {
                **result,
                "rule_ids": ",".join(dimension_rule_ids),
            }
        )

    available_scores = [
        result["score_pct"]
        for result in dimension_results.values()
        if result["score_pct"] is not None
    ]
    overall_score = (
        round(sum(available_scores) / len(available_scores), 2)
        if available_scores
        else None
    )
    score_rows.append(
        {
            "dimension": "overall",
            "status": "SCORED" if available_scores else "NOT_AVAILABLE",
            "passed_records": None,
            "failed_records": None,
            "applicable_records": len(available_scores),
            "excluded_records": len(DIMENSIONS) - len(available_scores),
            "score_pct": overall_score,
            "issue_count": None,
            "rule_ids": "Unweighted average of available dimensions; project assumption",
        }
    )

    return {
        "run_id": run_id,
        "source_file": source_file,
        "source_record_count": len(source_frame),
        "score_population": "all source records; quarantine disposition does not remove failures from DQ scores",
        "dimension_results": dimension_results,
        "overall_score_pct": overall_score,
        "overall_score_method": "Unweighted average of available dimension scores; project assumption",
        "review_only_issue_count": len(review_only_issues),
        "review_only_record_count": len(
            {str(issue["source_record_id"]) for issue in review_only_issues}
        ),
        "scores": score_rows,
    }


def score_run(
    run_id: str,
    bronze_root: str | Path = "data/bronze",
    validation_root: str | Path = "data/silver",
    quarantine_root: str | Path = "data/quarantine",
    rules_path: str | Path = "rules/quality_rules.yml",
    dimensions_path: str | Path = "rules/quality_dimensions.yml",
    reports_root: str | Path = "reports",
    generated_at_utc: str | None = None,
) -> tuple[dict[str, Any], Path, Path]:
    bronze_directory = Path(bronze_root) / run_id
    metadata_path = bronze_directory / "metadata.json"
    if not metadata_path.is_file():
        raise QualityScoringError(f"Bronze metadata does not exist: {metadata_path}")
    try:
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise QualityScoringError(f"Could not read Bronze metadata: {error}") from error
    source_file = metadata.get("source_file")
    if metadata.get("run_id") != run_id or not isinstance(source_file, str):
        raise QualityScoringError("Bronze metadata does not identify the requested run.")
    bronze_file = bronze_directory / source_file
    if not bronze_file.is_file() or _sha256(bronze_file) != metadata.get("source_sha256"):
        raise QualityScoringError("Bronze CSV is missing or its hash does not match metadata.")

    validation_directory = Path(validation_root) / run_id
    issues_path = validation_directory / "validation_issues.csv"
    validation_summary_path = validation_directory / "validation_summary.json"
    dispositions_path = Path(quarantine_root) / run_id / "dispositions.csv"
    quarantine_summary_path = Path(quarantine_root) / run_id / "summary.json"
    for path, label in (
        (issues_path, "validation issues"),
        (validation_summary_path, "validation summary"),
        (dispositions_path, "dispositions"),
        (quarantine_summary_path, "quarantine summary"),
    ):
        if not path.is_file():
            raise QualityScoringError(f"{label.title()} do not exist: {path}")

    try:
        validation_summary = json.loads(
            validation_summary_path.read_text(encoding="utf-8")
        )
        quarantine_summary = json.loads(
            quarantine_summary_path.read_text(encoding="utf-8")
        )
        source_frame = pd.read_csv(
            bronze_file, dtype=str, keep_default_na=False, encoding="utf-8-sig"
        )
        issues_frame = pd.read_csv(
            issues_path, dtype=str, keep_default_na=False, encoding="utf-8-sig"
        )
        dispositions = pd.read_csv(
            dispositions_path, dtype=str, keep_default_na=False, encoding="utf-8-sig"
        )
    except (OSError, json.JSONDecodeError, UnicodeError, pd.errors.ParserError) as error:
        raise QualityScoringError(f"Could not read scoring inputs: {error}") from error

    if validation_summary.get("run_id") != run_id or quarantine_summary.get("run_id") != run_id:
        raise QualityScoringError("Validation/quarantine summaries do not match the requested run.")
    if any(
        summary.get("source_sha256") != metadata.get("source_sha256")
        for summary in (validation_summary, quarantine_summary)
    ):
        raise QualityScoringError("Validation/quarantine summary references different source data.")
    if len(source_frame) != metadata.get("row_count"):
        raise QualityScoringError("Bronze row count does not match ingestion metadata.")
    if validation_summary.get("input_row_count") != len(source_frame):
        raise QualityScoringError("Validation summary row count does not match Bronze.")
    if len(issues_frame) != validation_summary.get("issue_count"):
        raise QualityScoringError("Issue count does not match validation summary.")
    if len(dispositions) != len(source_frame):
        raise QualityScoringError("Every source row must have one disposition.")
    if set(dispositions["run_id"].astype(str)) != {run_id}:
        raise QualityScoringError("Disposition rows must all belong to this run.")
    if dispositions["source_record_id"].duplicated().any():
        raise QualityScoringError("Source records have duplicate disposition rows.")
    expected_record_ids = {
        f"{source_file}:{row_number}"
        for row_number in range(2, len(source_frame) + 2)
    }
    if set(dispositions["source_record_id"].astype(str)) != expected_record_ids:
        raise QualityScoringError(
            "Disposition IDs do not match Bronze source-record IDs exactly."
        )
    actual_disposition_counts = Counter(dispositions["disposition"].astype(str))
    allowed_dispositions = {"eligible", "quarantined", "duplicate-held"}
    if set(actual_disposition_counts) - allowed_dispositions:
        raise QualityScoringError("Disposition file contains an unsupported disposition.")
    normalized_disposition_counts = {
        disposition: int(actual_disposition_counts.get(disposition, 0))
        for disposition in sorted(allowed_dispositions)
    }
    if normalized_disposition_counts != quarantine_summary.get("disposition_counts"):
        raise QualityScoringError(
            "Disposition counts do not match the quarantine summary."
        )

    try:
        rules = load_rule_config(rules_path)["rules"]
    except RuleConfigError as error:
        raise QualityScoringError(f"Invalid quality-rule configuration: {error}") from error
    dimension_config = load_dimension_config(dimensions_path, rules)
    result = calculate_scores(
        source_frame,
        issues_frame,
        rules,
        dimension_config,
        source_file=source_file,
        run_id=run_id,
    )
    result.update(
        {
            "source_sha256": metadata["source_sha256"],
            "schema_version": metadata["schema_version"],
            "generated_at_utc": generated_at_utc
            or datetime.now(timezone.utc).isoformat(),
            "disposition_counts": quarantine_summary["disposition_counts"],
        }
    )

    reports_directory = Path(reports_root) / run_id
    reports_directory.mkdir(parents=True, exist_ok=True)
    scores_path = reports_directory / "dq_scores.csv"
    summary_path = reports_directory / "dq_score_summary.json"
    pd.DataFrame(result["scores"], columns=SCORE_COLUMNS).to_csv(
        scores_path, index=False, encoding="utf-8"
    )
    summary_path.write_text(
        json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    write_score_summary_html(
        result, reports_directory / "dq_score_summary.html"
    )
    return result, scores_path, summary_path


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Calculate data-quality scores or render an existing score summary as HTML."
    )
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--run-id", help="Existing validated run ID")
    mode.add_argument(
        "--summary-json",
        help="Convert an existing dq_score_summary.json file to a sibling HTML report.",
    )
    parser.add_argument(
        "--html-output", help="Optional output path when using --summary-json."
    )
    parser.add_argument("--bronze-root", default="data/bronze")
    parser.add_argument("--validation-root", default="data/silver")
    parser.add_argument("--quarantine-root", default="data/quarantine")
    parser.add_argument("--rules", default="rules/quality_rules.yml")
    parser.add_argument("--dimensions", default="rules/quality_dimensions.yml")
    parser.add_argument("--reports-root", default="reports")
    args = parser.parse_args()

    if args.summary_json:
        try:
            html_path = convert_summary_file(args.summary_json, args.html_output)
        except QualityScoringError as error:
            parser.error(str(error))
        print(json.dumps({"html_path": str(html_path)}, indent=2))
        return

    try:
        result, scores_path, summary_path = score_run(
            run_id=args.run_id,
            bronze_root=args.bronze_root,
            validation_root=args.validation_root,
            quarantine_root=args.quarantine_root,
            rules_path=args.rules,
            dimensions_path=args.dimensions,
            reports_root=args.reports_root,
        )
    except QualityScoringError as error:
        parser.error(str(error))

    print(
        json.dumps(
            {
                "run_id": result["run_id"],
                "overall_score_pct": result["overall_score_pct"],
                "review_only_issue_count": result["review_only_issue_count"],
                "scores_path": str(scores_path),
                "summary_path": str(summary_path),
                "html_path": str(summary_path.with_name("dq_score_summary.html")),
            },
            indent=2,
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()