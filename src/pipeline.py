"""Run the required data pipeline stages in dependency order."""

from __future__ import annotations

import argparse
import html
import json
import os
import re
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from src.cleaning import clean_run
from src.ingestion import ingest_csv
from src.kpis import calculate_kpis_for_run
from src.profiling import profile_run
from src.quality_gate import run_quality_gate
from src.quality_scoring import score_run
from src.quarantine import quarantine_run
from src.reconciliation import reconcile_run
from src.validation import validate_run

_RUN_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")


class PipelineError(RuntimeError):
    """Raised when a required pipeline stage fails."""


def render_pipeline_summary_html(summary: dict[str, Any]) -> str:
    """Render a self-contained HTML report for a successful or failed run."""
    if not isinstance(summary, dict) or not summary.get("run_id"):
        raise PipelineError("Pipeline summary must contain a run_id.")

    def escape(value: Any) -> str:
        return html.escape(str(value))

    status = str(summary.get("status", "UNKNOWN"))
    status_class = "success" if status == "SUCCEEDED" else "failure"
    stages = summary.get("stages", summary.get("completed_stages", []))
    stage_rows = []
    for stage in stages:
        outputs = stage.get("outputs", [])
        outputs_html = "<br>".join(escape(output) for output in outputs) or "-"
        stage_rows.append(
            "<tr>"
            f"<th scope=\"row\">{escape(stage.get('stage', 'Unknown'))}</th>"
            f"<td><span class=\"stage-status\">{escape(stage.get('status', 'SUCCEEDED'))}</span></td>"
            f"<td class=\"outputs\">{outputs_html}</td>"
            "</tr>"
        )

    failed_stage = summary.get("failed_stage")
    if failed_stage and not any(
        stage.get("stage") == failed_stage for stage in stages
    ):
        stage_rows.append(
            "<tr class=\"failed-row\">"
            f"<th scope=\"row\">{escape(failed_stage)}</th>"
            "<td><span class=\"stage-status failure\">FAILED</span></td>"
            f"<td class=\"outputs\">{escape(summary.get('error', 'Stage failed'))}</td>"
            "</tr>"
        )

    gate_inputs = summary.get("quality_gate_inputs", {})
    gate_result = summary.get("quality_gate_result", {})
    gate_decision = gate_result.get(
        "decision", gate_inputs.get("quality_gate_decision", "NOT_EVALUATED")
    )
    gate_check_rows = "".join(
        "<tr>"
        f"<th scope=\"row\">{escape(check.get('check', 'Unknown'))}</th>"
        f"<td>{escape(check.get('status', 'UNKNOWN'))}</td>"
        f"<td>{escape(check.get('actual', 'Not available'))}</td>"
        f"<td>{escape(check.get('threshold', 'Not available'))}</td>"
        f"<td class=\"outputs\">{escape(check.get('reason', ''))}</td>"
        "</tr>"
        for check in gate_result.get("checks", [])
    ) or '<tr><td colspan="5">No gate checks recorded.</td></tr>'
    dispositions = gate_inputs.get("disposition_counts", {})
    disposition_cards = "".join(
        f'<div class="stat"><span>{escape(label)}</span><strong>{escape(dispositions.get(key, 0))}</strong></div>'
        for key, label in (
            ("eligible", "Eligible"),
            ("quarantined", "Quarantined"),
            ("duplicate-held", "Duplicate held"),
        )
    )
    failures = gate_inputs.get("validation_failures_by_severity", {})
    failure_rows = "".join(
        f"<tr><th scope=\"row\">{escape(severity)}</th><td>{escape(count)}</td></tr>"
        for severity, count in sorted(failures.items())
    ) or '<tr><td colspan="2">No validation summary available</td></tr>'
    gold_files = summary.get("gold_output_files", [])
    gold_file_list = "".join(f"<li><code>{escape(name)}</code></li>" for name in gold_files)
    gold_path = summary.get("gold_output_path")
    if gold_path:
        gold_file_list = f"<li>Output folder: <code>{escape(gold_path)}</code></li>" + gold_file_list
    if not gold_file_list:
        gold_file_list = "<li>Gold output was not published.</li>"

    validation_status = gate_inputs.get("validation_status", "Not available")
    reconciliation_status = gate_inputs.get("reconciliation_status", "Not available")
    overall_score = gate_inputs.get("overall_dq_score_pct")
    overall_score_text = "Not available" if overall_score is None else f"{escape(overall_score)}%"
    input_count = summary.get("input_row_count", "Not recorded")
    source_file = summary.get("source_file", "Not recorded")
    source_hash = summary.get("source_sha256", "Not recorded")
    started = summary.get("started_at_utc", "Not recorded")
    completed = summary.get("completed_at_utc", "Not recorded")
    if gate_decision == "BLOCKED":
        validation_note = "The release gate blocked Gold publication. Review each failed gate check below."
    elif validation_status == "FAIL":
        validation_note = "Validation found rule failures; the release gate allowed publication under the configured policy. Review the recorded issues."
    else:
        validation_note = ""

    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Pipeline Run - {escape(summary['run_id'])}</title>
  <style>
    :root {{ color-scheme: light; --ink: #1d2b34; --muted: #596971; --line: #d7e0e4; --surface: #f3f7f7; --teal: #176c69; --red: #9d382d; --amber: #805d12; }}
    * {{ box-sizing: border-box; }}
    body {{ margin: 0; background: #fff; color: var(--ink); font: 15px/1.5 -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; }}
    main {{ max-width: 1100px; margin: 0 auto; padding: 2rem 1.2rem 3rem; }}
    h1, h2 {{ line-height: 1.2; }} h1 {{ margin: 0; font-size: 1.8rem; }} h2 {{ margin: 2rem 0 .75rem; font-size: 1.2rem; }}
    .top {{ display: flex; flex-wrap: wrap; align-items: end; justify-content: space-between; gap: 1rem; border-bottom: 1px solid var(--line); padding-bottom: 1.25rem; }}
    .eyebrow {{ color: var(--teal); font-size: .8rem; font-weight: 700; text-transform: uppercase; }}
    .run-status {{ display: inline-block; padding: .35rem .65rem; font-weight: 700; }} .success {{ color: #175b48; background: #e7f4ed; }} .failure {{ color: var(--red); background: #fbecea; }}
    .notice {{ margin: 1rem 0; padding: .8rem 1rem; background: #fff5d6; border-left: 4px solid #d2a12b; }}
    .stats {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(150px, 1fr)); gap: .7rem; margin: 1rem 0; }}
    .stat {{ display: flex; justify-content: space-between; gap: 1rem; padding: .75rem .9rem; border: 1px solid var(--line); background: var(--surface); }} .stat span {{ color: var(--muted); }}
    .meta {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(230px, 1fr)); gap: .6rem 1rem; color: var(--muted); }} .meta code {{ color: var(--ink); overflow-wrap: anywhere; }}
    table {{ border-collapse: collapse; width: 100%; }} th, td {{ padding: .65rem .7rem; border-bottom: 1px solid var(--line); text-align: left; vertical-align: top; }} thead th {{ background: var(--surface); font-size: .82rem; }}
    .outputs {{ color: var(--muted); font-size: .85rem; overflow-wrap: anywhere; }} .gate {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(180px, 1fr)); gap: .75rem; }} .gate div {{ padding: .8rem; border: 1px solid var(--line); }}
    ul {{ margin: .4rem 0; }} footer {{ margin-top: 2rem; padding-top: 1rem; border-top: 1px solid var(--line); color: var(--muted); font-size: .85rem; }}
    @media (max-width: 600px) {{ main {{ padding: 1.2rem .7rem 2rem; }} }}
  </style>
</head>
<body>
  <main>
    <header class="top">
      <div><div class="eyebrow">Synthetic performance data</div><h1>Pipeline Run Summary</h1><p>Run <strong>{escape(summary['run_id'])}</strong></p></div>
      <div class="run-status {status_class}">{escape(status)}</div>
    </header>
    <p class="notice"><strong>Synthetic demonstration.</strong> This report summarizes a local portfolio pipeline and does not represent MoJ/HMPPS operational data. A successful pipeline run is not, by itself, a release approval.</p>
    <section class="stats" aria-label="Run overview">
      <div class="stat"><span>Input records</span><strong>{escape(input_count)}</strong></div>
      <div class="stat"><span>Data-quality score</span><strong>{overall_score_text}</strong></div>
      <div class="stat"><span>Validation status</span><strong>{escape(validation_status)}</strong></div>
      <div class="stat"><span>Reconciliation</span><strong>{escape(reconciliation_status)}</strong></div>
    <div class="stat"><span>Release gate</span><strong>{escape(gate_decision)}</strong></div>
      {disposition_cards}
    </section>
    {f'<p class="notice">{escape(validation_note)}</p>' if validation_note else ''}
    <h2>Pipeline Stages</h2>
    <table><thead><tr><th scope="col">Stage</th><th scope="col">Status</th><th scope="col">Outputs</th></tr></thead><tbody>{''.join(stage_rows) or '<tr><td colspan="3">No completed stages recorded.</td></tr>'}</tbody></table>
    <h2>Validation Findings</h2>
    <table><thead><tr><th scope="col">Severity</th><th scope="col">Issue count</th></tr></thead><tbody>{failure_rows}</tbody></table>
    <h2>Release Gate Checks</h2>
    <table><thead><tr><th scope="col">Check</th><th scope="col">Status</th><th scope="col">Actual</th><th scope="col">Threshold</th><th scope="col">Reason</th></tr></thead><tbody>{gate_check_rows}</tbody></table>
    <h2>Gold Outputs</h2>
    <ul>{gold_file_list}</ul>
    <h2>Run Metadata</h2>
    <section class="meta">
      <div><strong>Source:</strong> {escape(source_file)}</div>
      <div><strong>Source SHA-256:</strong> <code>{escape(source_hash)}</code></div>
      <div><strong>Started (UTC):</strong> {escape(started)}</div>
      <div><strong>Completed (UTC):</strong> {escape(completed)}</div>
    </section>
    <footer>Validation findings and the quality gate are distinct: quarantined rows can be removed while the run continues for analysis, but release approval must be determined by the configured gate.</footer>
  </main>
</body>
</html>
"""


def write_pipeline_summary_html(
    summary: dict[str, Any],
    output_path: str | Path,
) -> Path:
    html_path = Path(output_path)
    html_path.parent.mkdir(parents=True, exist_ok=True)
    html_path.write_text(render_pipeline_summary_html(summary), encoding="utf-8")
    return html_path


def convert_pipeline_summary_file(
    summary_path: str | Path,
    output_path: str | Path | None = None,
) -> Path:
    json_path = Path(summary_path)
    try:
        summary = json.loads(json_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise PipelineError(
            f"Could not read pipeline summary '{json_path}': {error}"
        ) from error
    html_path = Path(output_path) if output_path else json_path.with_suffix(".html")
    return write_pipeline_summary_html(summary, html_path)


def run_pipeline(
    run_id: str,
    project_root: str | Path = ".",
    source_path: str | Path | None = None,
) -> dict[str, Any]:
    """Run ingestion through KPI generation and publish Gold only on success.

    Intermediate outputs are run-specific. Gold is written to a staging
    directory and atomically renamed into its final location only after all
    required stages have completed.
    """
    if not _RUN_ID_PATTERN.fullmatch(run_id):
        raise PipelineError("run_id contains unsupported characters.")

    root = Path(project_root).resolve()
    source = Path(source_path) if source_path is not None else root / "data/raw/performance_dirty.csv"
    if not source.is_absolute():
        source = root / source

    bronze_root = root / "data/bronze"
    silver_root = root / "data/silver"
    quarantine_root = root / "data/quarantine"
    reports_root = root / "reports"
    gold_root = root / "data/gold"
    final_gold_directory = gold_root / run_id
    staging_root = gold_root / ".staging"
    staging_run_directory = staging_root / run_id
    pipeline_summary_path = reports_root / run_id / "pipeline_summary.json"

    if final_gold_directory.exists():
        raise PipelineError(
            f"Gold output already exists for run '{run_id}'; refusing to overwrite it."
        )
    if staging_run_directory.exists():
        raise PipelineError(
            f"A staged Gold output already exists for run '{run_id}'; inspect it before retrying."
        )

    rules_path = root / "rules/quality_rules.yml"
    cleaning_policy_path = root / "rules/cleaning_policies.yml"
    dimensions_path = root / "rules/quality_dimensions.yml"
    quality_gate_path = root / "rules/quality_gate.yml"
    kpi_mapping_path = root / "rules/kpi_mappings.yml"
    establishment_reference = root / "data/reference/establishments.csv"
    kpi_targets_path = root / "data/reference/kpi_targets.csv"

    stages: list[dict[str, Any]] = []
    active_stage = "preflight"
    started_at = datetime.now(timezone.utc).isoformat()
    gate_result: dict[str, Any] | None = None
    gate_result_path: Path | None = None
    quality_gate_inputs: dict[str, Any] | None = None

    def record_stage(name: str, outputs: list[str]) -> None:
        stages.append(
            {
                "stage": name,
                "status": "SUCCEEDED",
                "outputs": outputs,
            }
        )

    def write_summary(summary: dict[str, Any]) -> None:
        pipeline_summary_path.parent.mkdir(parents=True, exist_ok=True)
        html_path = pipeline_summary_path.with_suffix(".html")
        summary["html_report_path"] = str(html_path)
        temporary_path = pipeline_summary_path.with_suffix(".json.tmp")
        temporary_html_path = html_path.with_suffix(".html.tmp")
        temporary_path.write_text(
            json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        temporary_html_path.write_text(
            render_pipeline_summary_html(summary), encoding="utf-8"
        )
        os.replace(temporary_path, pipeline_summary_path)
        os.replace(temporary_html_path, html_path)

    try:
        required_inputs = (
            source,
            rules_path,
            cleaning_policy_path,
            dimensions_path,
            quality_gate_path,
            kpi_mapping_path,
            establishment_reference,
            kpi_targets_path,
        )
        missing_inputs = [str(path) for path in required_inputs if not path.is_file()]
        if missing_inputs:
            raise PipelineError(
                "Required pipeline inputs do not exist: " + ", ".join(missing_inputs)
            )

        active_stage = "ingestion"
        ingestion = ingest_csv(source, output_dir=bronze_root, run_id=run_id)
        record_stage("ingestion", [ingestion["bronze_file"]])

        active_stage = "profiling"
        profile, profile_path = profile_run(
            run_id,
            bronze_root=bronze_root,
            reports_root=reports_root,
        )
        record_stage(
            "profiling",
            [str(profile_path)],
        )

        active_stage = "validation"
        validation_summary, issues_path, validation_summary_path = validate_run(
            run_id,
            bronze_root=bronze_root,
            rules_path=rules_path,
            output_root=silver_root,
            project_root=root,
        )
        record_stage(
            "validation",
            [str(issues_path), str(validation_summary_path)],
        )

        active_stage = "quarantine"
        quarantine_summary, quarantine_summary_path = quarantine_run(
            run_id,
            bronze_root=bronze_root,
            validation_root=silver_root,
            output_root=quarantine_root,
        )
        record_stage(
            "quarantine",
            [
                str(quarantine_root / run_id / "dispositions.csv"),
                str(quarantine_root / run_id / "quarantined_records.csv"),
                str(quarantine_root / run_id / "issues.csv"),
                str(quarantine_summary_path),
            ],
        )

        active_stage = "cleaning"
        cleaning_summary, cleaning_summary_path = clean_run(
            run_id,
            bronze_root=bronze_root,
            quarantine_root=quarantine_root,
            reference_path=establishment_reference,
            policy_path=cleaning_policy_path,
            output_root=silver_root,
        )
        record_stage(
            "cleaning",
            [
                str(silver_root / run_id / "performance_clean.csv"),
                str(silver_root / run_id / "transformation_log.csv"),
                str(silver_root / run_id / "cleaning_issues.csv"),
                str(cleaning_summary_path),
            ],
        )

        active_stage = "reconciliation"
        reconciliation, reconciliation_path, reconciliation_summary_path = reconcile_run(
            run_id,
            bronze_root=bronze_root,
            silver_root=silver_root,
            quarantine_root=quarantine_root,
            reports_root=reports_root,
        )
        record_stage(
            "reconciliation",
            [str(reconciliation_path), str(reconciliation_summary_path)],
        )

        active_stage = "quality_scoring"
        score_summary, scores_path, score_summary_path = score_run(
            run_id,
            bronze_root=bronze_root,
            validation_root=silver_root,
            quarantine_root=quarantine_root,
            rules_path=rules_path,
            dimensions_path=dimensions_path,
            reports_root=reports_root,
        )
        record_stage(
            "quality_scoring",
            [str(scores_path), str(score_summary_path)],
        )

        active_stage = "quality_gate"
        gate_result, gate_result_path = run_quality_gate(
            run_id,
            validation_root=silver_root,
            reports_root=reports_root,
            config_path=quality_gate_path,
        )
        quality_gate_inputs = {
            "validation_status": validation_summary["status"],
            "validation_failures_by_severity": validation_summary[
                "failures_by_severity"
            ],
            "disposition_counts": quarantine_summary["disposition_counts"],
            "reconciliation_status": reconciliation["overall_status"],
            "overall_dq_score_pct": score_summary["overall_score_pct"],
            "quality_gate_decision": gate_result["decision"],
        }
        stages.append(
            {
                "stage": "quality_gate",
                "status": gate_result["decision"],
                "outputs": [str(gate_result_path)],
            }
        )
        if gate_result["decision"] == "BLOCKED":
            failed_checks = [
                check["check"]
                for check in gate_result["checks"]
                if check["status"] == "FAIL"
            ]
            raise PipelineError(
                "Quality gate blocked Gold publication: " + ", ".join(failed_checks)
            )

        active_stage = "kpi_generation"
        staging_root.mkdir(parents=True, exist_ok=True)
        kpi_summary, staged_performance_path, staged_kpi_path = calculate_kpis_for_run(
            run_id,
            silver_root=silver_root,
            targets_path=kpi_targets_path,
            mapping_path=kpi_mapping_path,
            validation_root=silver_root,
            reports_root=reports_root,
            gold_root=staging_root,
        )
        record_stage(
            "kpi_generation",
            [str(staged_performance_path), str(staged_kpi_path)],
        )

        summary = {
            "run_id": run_id,
            "status": "SUCCEEDED",
            "started_at_utc": started_at,
            "completed_at_utc": datetime.now(timezone.utc).isoformat(),
            "source_file": ingestion["source_file"],
            "source_sha256": ingestion["source_sha256"],
            "input_row_count": profile["row_count"],
            "stages": stages,
            "quality_gate_inputs": quality_gate_inputs,
            "quality_gate_result_path": str(gate_result_path),
            "quality_gate_result": gate_result,
            "gold_output_path": str(final_gold_directory),
            "gold_output_files": ["performance.parquet", "kpi_results.parquet", "kpi_summary.json"],
        }

        if not staging_run_directory.is_dir():
            raise PipelineError("KPI generation did not create its staged Gold directory.")
        gold_root.mkdir(parents=True, exist_ok=True)
        os.replace(staging_run_directory, final_gold_directory)
        for stage in stages:
            if stage["stage"] == "kpi_generation":
                stage["outputs"] = [
                    str(final_gold_directory / Path(output).name)
                    for output in stage["outputs"]
                ]
        try:
            staging_root.rmdir()
        except OSError:
            pass
        summary["stages"].append(
            {
                "stage": "gold_publish",
                "status": "SUCCEEDED",
                "outputs": [str(final_gold_directory)],
            }
        )
        summary["completed_at_utc"] = datetime.now(timezone.utc).isoformat()
        write_summary(summary)
        return summary
    except Exception as error:
        if staging_run_directory.exists():
            shutil.rmtree(staging_run_directory)
        failure_summary = {
            "run_id": run_id,
            "status": "FAILED",
            "started_at_utc": started_at,
            "completed_at_utc": datetime.now(timezone.utc).isoformat(),
            "failed_stage": active_stage,
            "error_type": type(error).__name__,
            "error": str(error),
            "completed_stages": stages,
            "gold_published": final_gold_directory.exists(),
        }
        if quality_gate_inputs is not None:
            failure_summary["quality_gate_inputs"] = quality_gate_inputs
        if gate_result is not None:
            failure_summary["quality_gate_result"] = gate_result
        if gate_result_path is not None:
            failure_summary["quality_gate_result_path"] = str(gate_result_path)
        try:
            write_summary(failure_summary)
        except OSError:
            pass
        if isinstance(error, PipelineError):
            raise
        raise PipelineError(
            f"Pipeline failed during {active_stage}: {error}"
        ) from error


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Run the pipeline or convert a pipeline summary JSON to HTML."
    )
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--run-id", help="Unique run ID")
    mode.add_argument(
        "--summary-json", help="Convert an existing pipeline summary JSON to HTML."
    )
    parser.add_argument("--html-output", help="Optional HTML path with --summary-json")
    parser.add_argument("--project-root", default=".")
    parser.add_argument("--input", help="Optional source CSV; defaults to data/raw/performance_dirty.csv")
    args = parser.parse_args()

    if args.summary_json:
        try:
            html_path = convert_pipeline_summary_file(
                args.summary_json, output_path=args.html_output
            )
        except PipelineError as error:
            parser.error(str(error))
        print(json.dumps({"html_report_path": str(html_path)}, indent=2))
        return

    try:
        summary = run_pipeline(
            run_id=args.run_id,
            project_root=args.project_root,
            source_path=args.input,
        )
    except PipelineError as error:
        parser.error(str(error))
    print(json.dumps(summary, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()