"""Build a consolidated, beginner-readable HTML report for a pipeline run."""

from __future__ import annotations

import argparse
import html
import json
from pathlib import Path
from typing import Any

import pandas as pd
import yaml

from src.profiling import profile_run

SEVERITY_ORDER = {"Critical": 0, "High": 1, "Medium": 2, "Low": 3}
INVESTIGATION_GUIDANCE = {
    "REQ-001": "Confirm the establishment identifier with the source owner; do not invent an ID.",
    "REQ-002": "Confirm the reporting month for this source record before including it in a monthly comparison.",
    "REQ-003": "Request the missing population value from the source owner; do not substitute zero.",
    "REQ-004": "Confirm the reporting-period end date against the source reporting calendar.",
    "REF-001": "Verify the identifier against the establishment reference and correct it only with source evidence.",
    "DATE-001": "Ask the source owner to confirm the reporting month; do not infer a replacement date.",
    "DATE-002": "Ask the source owner to confirm the reporting-period end date.",
    "DATE-003": "Confirm the submission date with the source owner before using the timeliness result.",
    "NUM-001": "Check the original count with the data owner; retain the current value until confirmed.",
    "NUM-002": "Confirm accommodation capacity from the applicable reference/source record.",
    "NUM-003": "Confirm the staff count with the source owner; do not replace it with an estimate.",
    "RATE-001": "Check the supplied rate against its source inputs and documented definition.",
    "RATE-002": "Confirm the percentage scale and source value; do not cap it at 0 or 100 automatically.",
    "REF-002": "Confirm the region label against the establishment reference.",
    "REF-003": "Confirm the category spelling against the reference; apply only an approved exact alias.",
    "DUP-001": "Compare the duplicate monthly records and confirm the authoritative submission; do not automatically keep the latest.",
    "REVIEW-001": "Ask the data owner whether occupancy above 100% is expected; it is a review flag, not proof of error.",
}


class ReportingError(ValueError):
    """Raised when the machine-readable inputs for a run report are inconsistent."""


def _read_json(path: Path, required: bool = False) -> dict[str, Any] | None:
    if not path.is_file():
        if required:
            raise ReportingError(f"Required report input does not exist: {path}")
        return None
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ReportingError(f"Could not read report input '{path}': {error}") from error
    if not isinstance(value, dict):
        raise ReportingError(f"Report input must be a JSON object: {path}")
    return value


def _read_csv(path: Path, required: bool = False) -> pd.DataFrame:
    if not path.is_file():
        if required:
            raise ReportingError(f"Required report input does not exist: {path}")
        return pd.DataFrame()
    try:
        return pd.read_csv(path, dtype=str, keep_default_na=False, encoding="utf-8-sig")
    except (OSError, UnicodeError, pd.errors.ParserError) as error:
        raise ReportingError(f"Could not read report input '{path}': {error}") from error


def _load_rule_descriptions(project_root: Path) -> dict[str, str]:
    path = project_root / "rules/quality_rules.yml"
    if not path.is_file():
        return {}
    try:
        config = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError):
        return {}
    if not isinstance(config, dict) or not isinstance(config.get("rules"), list):
        return {}
    return {
        str(rule.get("rule_id")): str(rule.get("description", ""))
        for rule in config["rules"]
        if isinstance(rule, dict) and rule.get("rule_id")
    }


def _recommendation(rule_id: str, description: str) -> str:
    return INVESTIGATION_GUIDANCE.get(
        rule_id,
        "Review the affected source records with the data owner and document the resolution before reporting.",
    )


def _top_failures(
    issues: pd.DataFrame,
    descriptions: dict[str, str],
    limit: int = 8,
) -> list[dict[str, Any]]:
    if issues.empty or not {"rule_id", "source_record_id"}.issubset(issues.columns):
        return []
    failures = []
    for rule_id, group in issues.groupby("rule_id", sort=False):
        severity = str(group["severity"].iloc[0]) if "severity" in group else "Unknown"
        fields = sorted(
            {
                field.strip()
                for value in group.get("field", pd.Series(dtype=str)).astype(str)
                for field in value.replace(";", ",").split(",")
                if field.strip()
            }
        )
        failures.append(
            {
                "rule_id": str(rule_id),
                "severity": severity,
                "issue_count": int(len(group)),
                "affected_records": int(group["source_record_id"].astype(str).nunique()),
                "fields": fields,
                "description": descriptions.get(str(rule_id), ""),
                "next_action": _recommendation(str(rule_id), descriptions.get(str(rule_id), "")),
            }
        )
    failures.sort(
        key=lambda item: (
            SEVERITY_ORDER.get(item["severity"], 4),
            -item["affected_records"],
            item["rule_id"],
        )
    )
    return failures[:limit]


def build_run_report_data(
    run_id: str,
    project_root: str | Path = ".",
) -> dict[str, Any]:
    """Read machine-readable run outputs and prepare one combined report model."""
    root = Path(project_root).resolve()
    reports_directory = root / "reports" / run_id
    pipeline_summary = _read_json(reports_directory / "pipeline_summary.json", required=True)
    bronze_metadata = _read_json(root / "data/bronze" / run_id / "metadata.json", required=True)
    validation_summary = _read_json(root / "data/silver" / run_id / "validation_summary.json") or {}
    quarantine_summary = _read_json(root / "data/quarantine" / run_id / "summary.json") or {}
    cleaning_summary = _read_json(root / "data/silver" / run_id / "cleaning_summary.json") or {}
    dq_summary = _read_json(reports_directory / "dq_score_summary.json") or {}
    reconciliation = _read_json(reports_directory / "reconciliation.json") or {}
    gate_result = _read_json(reports_directory / "quality_gate.json")
    if gate_result is None:
        gate_result = pipeline_summary.get("quality_gate_result")

    issues = _read_csv(root / "data/silver" / run_id / "validation_issues.csv")
    profile, profile_path = profile_run(
        run_id,
        bronze_root=root / "data/bronze",
        reports_root=root / "reports",
    )
    descriptions = _load_rule_descriptions(root)
    top_failures = _top_failures(issues, descriptions)

    severity_counts = validation_summary.get("failures_by_severity", {})
    disposition_counts = quarantine_summary.get(
        "disposition_counts", pipeline_summary.get("quality_gate_inputs", {}).get("disposition_counts", {})
    )
    gate_checks = gate_result.get("checks", []) if isinstance(gate_result, dict) else []
    gate_decision = (
        gate_result.get("decision")
        if isinstance(gate_result, dict)
        else pipeline_summary.get("quality_gate_inputs", {}).get("quality_gate_decision", "NOT_EVALUATED")
    )

    gold_summary = _read_json(root / "data/gold" / run_id / "kpi_summary.json")
    gold_directory = root / "data/gold" / run_id
    if gate_decision == "BLOCKED":
        gold_status = "Not published: release gate blocked this run."
    elif gold_directory.is_dir():
        gold_status = "Gold output published."
    elif gate_decision == "NOT_EVALUATED":
        gold_status = "No release-gate decision is recorded for this run."
    else:
        gold_status = "No Gold output found for this run."

    profile_columns = profile["columns"]
    missing_total = sum(item["missing_count"] for item in profile_columns)
    unparseable_total = sum(item["unparseable_count"] for item in profile_columns)
    profile_summary = {
        "row_count": profile["row_count"],
        "column_count": profile["column_count"],
        "missing_cells": missing_total,
        "unparseable_values": unparseable_total,
        "exact_duplicate_groups": profile["duplicates"]["exact_duplicate_groups"],
        "business_key_duplicate_groups": profile["duplicates"]["business_key_duplicate_groups"],
        "date_coverage": [
            {
                "field": item["column"],
                **item["date_coverage"],
                "unparseable_count": item["unparseable_count"],
            }
            for item in profile_columns
            if item.get("date_coverage")
        ],
        "report_path": str(profile_path),
    }

    pipeline_status = pipeline_summary.get("status", "UNKNOWN")
    failed_stage = pipeline_summary.get("failed_stage")
    if gate_decision == "BLOCKED":
        outcome_label = "BLOCKED BY RELEASE GATE"
        outcome_reason = pipeline_summary.get("error", "Release gate blocked publication.")
    elif pipeline_status == "FAILED":
        outcome_label = "PIPELINE FAILED"
        outcome_reason = pipeline_summary.get("error", "A pipeline stage failed.")
    elif gate_decision == "PASSED":
        outcome_label = "RELEASE GATE PASSED"
        outcome_reason = "All configured release checks passed."
    else:
        outcome_label = "PIPELINE COMPLETED"
        outcome_reason = "No release-gate decision is recorded."

    source_file = (
        pipeline_summary.get("source_file")
        or validation_summary.get("source_file")
        or bronze_metadata.get("source_file")
        or "Not recorded"
    )
    source_hash = (
        pipeline_summary.get("source_sha256")
        or validation_summary.get("source_sha256")
        or bronze_metadata.get("source_sha256")
        or "Not recorded"
    )
    input_count = (
        pipeline_summary.get("input_row_count")
        or validation_summary.get("input_row_count")
        or bronze_metadata.get("row_count")
    )

    artifact_names = [
        "pipeline_summary.html",
        "profiling_report.html",
        "dq_score_summary.html",
        "reconciliation_summary.md",
        "quality_gate.json",
    ]
    if issues.size:
        artifact_names.append("../../data/silver/" + run_id + "/validation_issues.csv")
    if (root / "data/quarantine" / run_id / "quarantined_records.csv").is_file():
        artifact_names.append("../../data/quarantine/" + run_id + "/quarantined_records.csv")
    artifact_links = [
        {"label": Path(name).name.replace("_", " "), "href": name}
        for name in artifact_names
        if (reports_directory / name).resolve().is_file()
    ]

    return {
        "run_id": run_id,
        "outcome_label": outcome_label,
        "outcome_reason": outcome_reason,
        "pipeline_status": pipeline_status,
        "failed_stage": failed_stage,
        "validation_status": validation_summary.get(
            "status", pipeline_summary.get("quality_gate_inputs", {}).get("validation_status", "NOT_AVAILABLE")
        ),
        "source_file": source_file,
        "source_sha256": source_hash,
        "input_row_count": input_count,
        "started_at_utc": pipeline_summary.get("started_at_utc", "Not recorded"),
        "completed_at_utc": pipeline_summary.get("completed_at_utc", "Not recorded"),
        "profile": profile_summary,
        "validation": {
            "issue_count": validation_summary.get("issue_count", len(issues)),
            "records_with_failures": validation_summary.get("records_with_failures", "Not recorded"),
            "failures_by_severity": severity_counts,
        },
        "disposition_counts": disposition_counts,
        "top_failures": top_failures,
        "dimension_results": dq_summary.get("dimension_results", {}),
        "overall_dq_score_pct": dq_summary.get("overall_score_pct"),
        "review_only_issue_count": dq_summary.get("review_only_issue_count", 0),
        "reconciliation": reconciliation,
        "gate_decision": gate_decision,
        "gate_checks": gate_checks,
        "gold_status": gold_status,
        "gold_kpi_summary": gold_summary,
        "artifact_links": artifact_links,
        "synthetic_data_note": "All records, names, targets, scores, and findings in this project are synthetic and are not MoJ/HMPPS operational information.",
    }


def render_run_report_html(report: dict[str, Any]) -> str:
    """Render a consolidated operational report with escaped dynamic content."""
    def esc(value: Any) -> str:
        return html.escape(str(value))

    disposition_counts = report.get("disposition_counts", {})
    stat_cards = "".join(
        f'<div class="stat"><span>{esc(label)}</span><strong>{esc(disposition_counts.get(key, 0))}</strong></div>'
        for key, label in (
            ("eligible", "Eligible"),
            ("quarantined", "Quarantined"),
            ("duplicate-held", "Duplicate held"),
        )
    )
    stat_cards += f'<div class="stat"><span>Input records</span><strong>{esc(report.get("input_row_count", "Not recorded"))}</strong></div>'
    stat_cards += f'<div class="stat"><span>Overall DQ score</span><strong>{esc(report.get("overall_dq_score_pct") if report.get("overall_dq_score_pct") is not None else "Not available")}{"%" if report.get("overall_dq_score_pct") is not None else ""}</strong></div>'

    dimension_rows = []
    for name, result in report.get("dimension_results", {}).items():
        score = result.get("score_pct")
        dimension_rows.append(
            "<tr>"
            f"<th scope=\"row\">{esc(name.replace('_', ' ').title())}</th>"
            f"<td>{esc(result.get('status', 'NOT_AVAILABLE'))}</td>"
            f"<td>{'Not available' if score is None else esc(score) + '%'}</td>"
            f"<td>{esc(result.get('passed_records') if result.get('passed_records') is not None else 'n/a')}</td>"
            f"<td>{esc(result.get('failed_records') if result.get('failed_records') is not None else 'n/a')}</td>"
            f"<td>{esc(result.get('applicable_records', 0))}</td>"
            f"<td>{esc(result.get('excluded_records', 0))}</td>"
            f"<td>{esc(result.get('issue_count', 0))}</td>"
            "</tr>"
        )
    if not dimension_rows:
        dimension_rows.append('<tr><td colspan="8">No DQ score summary found.</td></tr>')

    failure_rows = []
    for failure in report.get("top_failures", []):
        failure_rows.append(
            "<tr>"
            f"<th scope=\"row\">{esc(failure['rule_id'])}</th>"
            f"<td>{esc(failure['severity'])}</td>"
            f"<td>{esc(failure['issue_count'])}</td>"
            f"<td>{esc(failure['affected_records'])}</td>"
            f"<td>{esc(', '.join(failure['fields']))}</td>"
            f"<td>{esc(failure['description'])}</td>"
            f"<td>{esc(failure['next_action'])}</td>"
            "</tr>"
        )
    if not failure_rows:
        failure_rows.append('<tr><td colspan="7">No validation issues are recorded.</td></tr>')

    severity_rows = "".join(
        f"<tr><th scope=\"row\">{esc(severity)}</th><td>{esc(count)}</td></tr>"
        for severity, count in sorted(
            report.get("validation", {}).get("failures_by_severity", {}).items(),
            key=lambda item: SEVERITY_ORDER.get(item[0], 4),
        )
    ) or '<tr><td colspan="2">No severity summary available.</td></tr>'

    reconciliation = report.get("reconciliation", {})
    measure_rows = []
    for name, measure in reconciliation.get("measures", {}).items():
        if measure.get("status") == "NOT_AVAILABLE":
            measure_rows.append(
                f"<tr><th scope=\"row\">{esc(name)}</th><td colspan=\"5\">Not available: {esc(measure.get('reason', ''))}</td></tr>"
            )
            continue
        measure_rows.append(
            "<tr>"
            f"<th scope=\"row\">{esc(name)}</th>"
            f"<td>{esc(measure['raw'].get('total'))}</td>"
            f"<td>{esc(measure['silver'].get('total'))}</td>"
            f"<td>{esc(measure['quarantined'].get('total'))}</td>"
            f"<td>{esc(measure['duplicate_held'].get('total'))}</td>"
            f"<td>{esc(measure.get('status'))}; missing raw {esc(measure['raw'].get('missing_count'))}, unparseable raw {esc(measure['raw'].get('unparseable_count'))}</td>"
            "</tr>"
        )
    if not measure_rows:
        measure_rows.append('<tr><td colspan="6">No reconciliation summary found.</td></tr>')

    gate_rows = []
    for check in report.get("gate_checks", []):
        gate_rows.append(
            "<tr>"
            f"<th scope=\"row\">{esc(check.get('check'))}</th>"
            f"<td>{esc(check.get('status'))}</td>"
            f"<td>{esc(check.get('actual'))}</td>"
            f"<td>{esc(check.get('threshold'))}</td>"
            f"<td>{esc(check.get('reason'))}</td>"
            "</tr>"
        )
    if not gate_rows:
        gate_rows.append('<tr><td colspan="5">No quality-gate result is recorded.</td></tr>')

    profile = report.get("profile", {})
    profile_date_rows = "".join(
        f"<tr><th scope=\"row\">{esc(item['field'])}</th><td>{esc(item['valid_count'])}</td><td>{esc(item['min'])}</td><td>{esc(item['max'])}</td><td>{esc(item['unparseable_count'])}</td></tr>"
        for item in profile.get("date_coverage", [])
    ) or '<tr><td colspan="5">No date coverage summary available.</td></tr>'

    links = "".join(
        f'<li><a href="{esc(item["href"])}">{esc(item["label"])}</a></li>'
        for item in report.get("artifact_links", [])
    ) or "<li>No component reports available.</li>"
    gate_decision = report.get("gate_decision", "NOT_EVALUATED")
    outcome = report.get("outcome_label", "PIPELINE COMPLETED")
    status_class = "blocked" if gate_decision == "BLOCKED" or outcome == "BLOCKED BY RELEASE GATE" else "passed"
    gold_status = report.get("gold_status", "Not available")
    kpi_summary = report.get("gold_kpi_summary")
    if kpi_summary:
        kpi_note = (
            f"{kpi_summary.get('kpi_result_count', 0)} KPI rows; availability: "
            + ", ".join(
                f"{name} {count}"
                for name, count in kpi_summary.get("availability_counts", {}).items()
            )
        )
    else:
        kpi_note = "No Gold KPI output is available for this run."

    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Run Report - {esc(report.get('run_id'))}</title>
<style>
:root {{ color-scheme: light; --ink:#202b33; --muted:#5b6870; --line:#d7e0e4; --surface:#f3f7f7; --teal:#176c69; --red:#9d382d; --amber:#805d12; }}
* {{ box-sizing:border-box }} body {{ margin:0; background:#fff; color:var(--ink); font:15px/1.5 -apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif }}
main {{ max-width:1240px; margin:0 auto; padding:2rem 1.1rem 3rem }} h1,h2 {{ line-height:1.2 }} h1 {{ margin:0; font-size:1.8rem }} h2 {{ margin:2rem 0 .7rem; font-size:1.2rem }}
.head {{ display:flex; flex-wrap:wrap; align-items:end; justify-content:space-between; gap:1rem; padding-bottom:1rem; border-bottom:1px solid var(--line) }} .eyebrow {{ color:var(--teal); font-size:.8rem; font-weight:700; text-transform:uppercase }}
.badge {{ padding:.4rem .7rem; font-weight:700; background:#edf3f3 }} .blocked {{ color:var(--red); background:#fbecea }} .passed {{ color:#175b48; background:#e7f4ed }}
.notice {{ margin:1rem 0; padding:.8rem 1rem; background:#fff5d6; border-left:4px solid #d2a12b }} .cards {{ display:grid; grid-template-columns:repeat(auto-fit,minmax(150px,1fr)); gap:.65rem; margin:1rem 0 }} .card {{ padding:.75rem .9rem; border:1px solid var(--line); background:var(--surface); display:flex; justify-content:space-between; gap:1rem }} .card span {{ color:var(--muted) }}
.meta {{ display:grid; grid-template-columns:repeat(auto-fit,minmax(220px,1fr)); gap:.4rem 1rem; color:var(--muted) }} code {{ overflow-wrap:anywhere }} .wrap {{ overflow-x:auto }} table {{ width:100%; border-collapse:collapse; min-width:780px }} th,td {{ padding:.55rem .6rem; border-bottom:1px solid var(--line); text-align:left; vertical-align:top }} thead th {{ background:var(--surface); font-size:.82rem }} .small {{ color:var(--muted); font-size:.88rem }}
@media(max-width:600px) {{ main {{ padding:1rem .65rem 2rem }} }}
</style></head><body><main>
<header class="head"><div><div class="eyebrow">Synthetic management information</div><h1>Pipeline Run Report</h1><p>Run <strong>{esc(report.get('run_id'))}</strong></p></div><div class="badge {status_class}">{esc(outcome)}</div></header>
<p class="notice"><strong>Analyst interpretation:</strong> {esc(report.get('outcome_reason', ''))} This is synthetic data; anomalies and KPI results are prompts for review, not evidence about real operations.</p>
<section class="cards" aria-label="Run overview"><div class="card"><span>Input records</span><strong>{esc(report.get('input_row_count', 'Not available'))}</strong></div><div class="card"><span>DQ score</span><strong>{esc(report.get('overall_dq_score_pct') if report.get('overall_dq_score_pct') is not None else 'Not available')}{'%' if report.get('overall_dq_score_pct') is not None else ''}</strong></div><div class="card"><span>Gate decision</span><strong>{esc(gate_decision)}</strong></div><div class="card"><span>Gold</span><strong>{esc(gold_status)}</strong></div>{''.join(f'<div class="card"><span>{esc(label)}</span><strong>{esc(report.get("disposition_counts", {}).get(key, 0))}</strong></div>' for key,label in [('eligible','Eligible'),('quarantined','Quarantined'),('duplicate-held','Duplicate held')])}</section>
<h2>What the data profile found</h2><p>{esc(profile.get('row_count', 'Not available'))} rows, {esc(profile.get('column_count', 'Not available'))} columns; {esc(profile.get('missing_cells', 'Not available'))} missing cells, {esc(profile.get('unparseable_values', 'Not available'))} unparseable values, {esc(profile.get('business_key_duplicate_groups', 'Not available'))} duplicate establishment-month groups.</p>
<div class="wrap"><table><thead><tr><th>Date field</th><th>Valid dates</th><th>Earliest</th><th>Latest</th><th>Unparseable</th></tr></thead><tbody>{profile_date_rows}</tbody></table></div>
<h2>Validation and investigation priorities</h2><p>Validation status: <strong>{esc(report.get('validation_status', 'Not available'))}</strong>; {esc(report.get('validation', {}).get('issue_count', 'Not available'))} rule issue rows affect {esc(report.get('validation', {}).get('records_with_failures', 'Not available'))} records.</p>
<div class="wrap"><table><thead><tr><th>Severity</th><th>Issue rows</th></tr></thead><tbody>{severity_rows}</tbody></table></div>
<div class="wrap"><table><thead><tr><th>Rule</th><th>Severity</th><th>Issue rows</th><th>Affected records</th><th>Fields</th><th>What the rule checks</th><th>Suggested next investigation</th></tr></thead><tbody>{''.join(f'<tr><th scope="row">{esc(item["rule_id"])}</th><td>{esc(item["severity"])}</td><td>{esc(item["issue_count"])}</td><td>{esc(item["affected_records"])}</td><td>{esc(", ".join(item["fields"]))}</td><td>{esc(item["description"])}</td><td>{esc(item["next_action"])}</td></tr>' for item in report.get('top_failures', [])) or '<tr><td colspan="7">No validation issues.</td></tr>'}</tbody></table></div>
<h2>Data-quality dimensions</h2><div class="wrap"><table><thead><tr><th>Dimension</th><th>Status</th><th>Score</th><th>Passed</th><th>Failed records</th><th>Applicable</th><th>Excluded</th><th>Issue rows</th></tr></thead><tbody>{''.join(f'<tr><th scope="row">{esc(name.replace("_", " ").title())}</th><td>{esc(item.get("status"))}</td><td>{"Not available" if item.get("score_pct") is None else esc(item.get("score_pct")) + "%"}</td><td>{esc(item.get("passed_records") if item.get("passed_records") is not None else "n/a")}</td><td>{esc(item.get("failed_records") if item.get("failed_records") is not None else "n/a")}</td><td>{esc(item.get("applicable_records", 0))}</td><td>{esc(item.get("excluded_records", 0))}</td><td>{esc(item.get("issue_count", 0))}</td></tr>' for name,item in report.get('dimension_results', {}).items()) or '<tr><td colspan="8">No DQ scores available.</td></tr>'}</tbody></table></div>
<h2>Reconciliation</h2><p>Overall reconciliation status: <strong>{esc(reconciliation.get('overall_status', 'Not available'))}</strong>. Row counts and additive measures are checked by disposition; missing values are not treated as zero.</p>
<div class="wrap"><table><thead><tr><th>Measure</th><th>Raw</th><th>Silver</th><th>Quarantined</th><th>Duplicate held</th><th>Difference</th><th>Status</th><th>Missing/unparseable (raw)</th></tr></thead><tbody>{''.join(f'<tr><th scope="row">{esc(name)}</th><td>{esc(item.get("raw", {}).get("total"))}</td><td>{esc(item.get("silver", {}).get("total"))}</td><td>{esc(item.get("quarantined", {}).get("total"))}</td><td>{esc(item.get("duplicate_held", {}).get("total"))}</td><td>{esc(item.get("difference"))}</td><td>{esc(item.get("status"))}</td><td>{esc(item.get("raw", {}).get("missing_count"))} / {esc(item.get("raw", {}).get("unparseable_count"))}</td></tr>' for name,item in reconciliation.get('measures', {}).items()) or '<tr><td colspan="8">No measure reconciliation.</td></tr>'}</tbody></table></div>
<h2>Release-gate checks</h2><p>Gate decision: <strong>{esc(gate_decision)}</strong></p><div class="wrap"><table><thead><tr><th>Check</th><th>Status</th><th>Actual</th><th>Threshold</th><th>Reason</th></tr></thead><tbody>{''.join(f'<tr><th scope="row">{esc(check.get("check"))}</th><td>{esc(check.get("status"))}</td><td>{esc(check.get("actual"))}</td><td>{esc(check.get("threshold"))}</td><td>{esc(check.get("reason"))}</td></tr>' for check in report.get('gate_checks', [])) or '<tr><td colspan="5">No gate result.</td></tr>'}</tbody></table></div>
<h2>KPI output</h2><p>{esc(kpi_note)}</p>
<h2>Detailed outputs</h2><ul>{links}</ul>
<h2>Run metadata</h2><section class="meta"><div><strong>Pipeline execution:</strong> {esc(report.get('pipeline_status'))}</div><div><strong>Failed stage:</strong> {esc(report.get('failed_stage') or 'None')}</div><div><strong>Source file:</strong> {esc(report.get('source_file'))}</div><div><strong>Source SHA-256:</strong> <code>{esc(report.get('source_sha256'))}</code></div><div><strong>Started (UTC):</strong> {esc(report.get('started_at_utc'))}</div><div><strong>Completed (UTC):</strong> {esc(report.get('completed_at_utc'))}</div></section>
<footer>{esc(report.get('synthetic_data_note'))} This report summarizes evidence; it does not establish source accuracy, causation, or operational impact.</footer>
</main></body></html>
"""


def generate_run_report(
    run_id: str,
    project_root: str | Path = ".",
) -> Path:
    """Generate a consolidated HTML report from machine-readable run outputs."""
    root = Path(project_root).resolve()
    reports_directory = root / "reports" / run_id
    report_data = build_run_report_data(run_id, project_root=root)
    report_path = reports_directory / "run_report.html"
    report_path.write_text(render_run_report_html(report_data), encoding="utf-8")
    return report_path


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Generate a consolidated human-readable report for a pipeline run."
    )
    parser.add_argument("--run-id", required=True, help="Existing pipeline run ID")
    parser.add_argument("--project-root", default=".")
    args = parser.parse_args()
    try:
        report_path = generate_run_report(args.run_id, project_root=args.project_root)
    except ReportingError as error:
        parser.error(str(error))
    print(json.dumps({"report_path": str(report_path)}, indent=2))


if __name__ == "__main__":
    main()