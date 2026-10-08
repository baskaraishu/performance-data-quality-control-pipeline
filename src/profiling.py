"""Profile Bronze CSV data and render a self-contained HTML report."""

from __future__ import annotations

import argparse
import hashlib
import html
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd

DATE_COLUMNS = {
    "reporting_month",
    "reporting_period_end",
    "data_submission_date",
}
NUMERIC_COLUMNS = {
    "population",
    "certified_normal_accommodation",
    "occupancy_pct",
    "assaults",
    "assaults_per_100",
    "self_harm_incidents",
    "self_harm_per_100",
    "security_incidents",
    "security_incidents_per_100",
    "absconds",
    "education_attendance_pct",
    "purposeful_activity_pct",
    "average_staff",
    "staff_sickness_days",
    "staff_sickness_rate_pct",
    "staff_resignation_rate_pct",
    "submission_delay_days",
}
KEY_COLUMNS = ("establishment_id", "reporting_month")
_RUN_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")


class ProfilingError(ValueError):
    """Raised when a Bronze run is missing or inconsistent."""


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _missing_mask(values: pd.Series) -> pd.Series:
    return values.fillna("").astype(str).str.strip().eq("")


def _profile_numeric(values: pd.Series) -> dict[str, Any]:
    missing = _missing_mask(values)
    non_missing = values[~missing].astype(str).str.strip()
    parsed = pd.to_numeric(non_missing, errors="coerce")
    valid = parsed.dropna()
    invalid_values = non_missing[parsed.isna()]
    inferred_type = "integer" if len(valid) and (valid % 1 == 0).all() else "number"
    if len(invalid_values) and len(valid):
        inferred_type += " with unparseable values"
    elif not len(valid) and len(non_missing):
        inferred_type = "text (no numeric values parsed)"

    stats = None
    if len(valid):
        quantiles = valid.quantile([0.25, 0.5, 0.75])
        stats = {
            "min": float(valid.min()),
            "max": float(valid.max()),
            "quantiles": {
                "25%": float(quantiles.loc[0.25]),
                "50%": float(quantiles.loc[0.5]),
                "75%": float(quantiles.loc[0.75]),
            },
        }

    return {
        "inferred_type": inferred_type,
        "unparseable_count": int(len(invalid_values)),
        "unparseable_examples": invalid_values.value_counts().head(5).to_dict(),
        "numeric_summary": stats,
    }


def _profile_date(values: pd.Series) -> dict[str, Any]:
    missing = _missing_mask(values)
    non_missing = values[~missing].astype(str).str.strip()
    parsed = pd.to_datetime(non_missing, format="ISO8601", errors="coerce")
    valid = parsed.dropna()
    invalid_values = non_missing[parsed.isna()]

    date_coverage = None
    if len(valid):
        date_coverage = {
            "valid_count": int(len(valid)),
            "min": valid.min().date().isoformat(),
            "max": valid.max().date().isoformat(),
        }

    inferred_type = "date"
    if len(invalid_values) and len(valid):
        inferred_type = "date with unparseable values"
    elif not len(valid) and len(non_missing):
        inferred_type = "text (no dates parsed)"

    return {
        "inferred_type": inferred_type,
        "unparseable_count": int(len(invalid_values)),
        "unparseable_examples": invalid_values.value_counts().head(5).to_dict(),
        "date_coverage": date_coverage,
    }


def _profile_text(values: pd.Series) -> dict[str, Any]:
    missing = _missing_mask(values)
    non_missing = values[~missing].astype(str).str.strip()
    numeric = pd.to_numeric(non_missing, errors="coerce")
    dates = pd.to_datetime(non_missing, format="ISO8601", errors="coerce")

    if len(non_missing) and numeric.notna().all():
        inferred_type = "integer" if (numeric % 1 == 0).all() else "number"
    elif len(non_missing) and dates.notna().all():
        inferred_type = "date"
    elif numeric.notna().any():
        inferred_type = "mixed numeric/text"
    elif dates.notna().any():
        inferred_type = "mixed date/text"
    else:
        inferred_type = "category/text"

    return {
        "inferred_type": inferred_type,
        "unparseable_count": 0,
        "unparseable_examples": {},
        "category_frequencies": [
            {"value": str(value), "count": int(count)}
            for value, count in non_missing.value_counts().items()
        ],
    }


def _column_profile(column: str, values: pd.Series, row_count: int) -> dict[str, Any]:
    missing = _missing_mask(values)
    missing_count = int(missing.sum())
    non_missing_count = row_count - missing_count

    if column in DATE_COLUMNS:
        details = _profile_date(values)
    elif column in NUMERIC_COLUMNS:
        details = _profile_numeric(values)
    else:
        details = _profile_text(values)

    profile = {
        "column": column,
        "inferred_type": details["inferred_type"],
        "missing_count": missing_count,
        "missing_pct": (missing_count / row_count * 100) if row_count else 0.0,
        "non_missing_count": non_missing_count,
        "distinct_non_missing": int(values[~missing].astype(str).str.strip().nunique()),
        "unparseable_count": details["unparseable_count"],
        "unparseable_examples": details["unparseable_examples"],
    }
    if "numeric_summary" in details:
        profile["numeric_summary"] = details["numeric_summary"]
    if "date_coverage" in details:
        profile["date_coverage"] = details["date_coverage"]
    if "category_frequencies" in details:
        profile["category_frequencies"] = details["category_frequencies"]
    return profile


def _duplicate_summary(frame: pd.DataFrame) -> dict[str, Any]:
    if frame.empty:
        return {
            "exact_duplicate_groups": 0,
            "exact_duplicate_extra_rows": 0,
            "business_key_duplicate_groups": 0,
            "business_key_duplicate_extra_rows": 0,
            "business_key_examples": [],
        }

    exact_counts = frame.value_counts(dropna=False)
    exact_duplicate_groups = exact_counts[exact_counts > 1]

    if not set(KEY_COLUMNS).issubset(frame.columns):
        business_groups = pd.Series(dtype="int64")
    else:
        valid_keys = frame.loc[
            ~_missing_mask(frame[KEY_COLUMNS[0]])
            & ~_missing_mask(frame[KEY_COLUMNS[1]]),
            list(KEY_COLUMNS),
        ]
        business_groups = valid_keys.value_counts()
        business_groups = business_groups[business_groups > 1]

    examples = []
    for key, count in business_groups.head(10).items():
        if not isinstance(key, tuple):
            key = (key,)
        examples.append(
            {
                KEY_COLUMNS[index]: str(value)
                for index, value in enumerate(key)
            }
            | {"row_count": int(count)}
        )

    return {
        "exact_duplicate_groups": int(len(exact_duplicate_groups)),
        "exact_duplicate_extra_rows": int((exact_duplicate_groups - 1).sum()),
        "business_key_duplicate_groups": int(len(business_groups)),
        "business_key_duplicate_extra_rows": int((business_groups - 1).sum()),
        "business_key_examples": examples,
    }


def _table(headers: list[str], rows: list[list[Any]]) -> str:
    header_html = "".join(f"<th>{html.escape(str(value))}</th>" for value in headers)
    body_html = "".join(
        "<tr>" + "".join(f"<td>{html.escape(str(value))}</td>" for value in row) + "</tr>"
        for row in rows
    )
    if not rows:
        body_html = f'<tr><td colspan="{len(headers)}">None</td></tr>'
    return f"<table><thead><tr>{header_html}</tr></thead><tbody>{body_html}</tbody></table>"


def render_html_report(profile: dict[str, Any]) -> str:
    """Render a self-contained HTML report with escaped, data-derived content."""
    columns = []
    for item in profile["columns"]:
        columns.append(
            [
                item["column"],
                item["inferred_type"],
                item["missing_count"],
                f'{item["missing_pct"]:.2f}%',
                item["unparseable_count"],
                item["distinct_non_missing"],
            ]
        )

    numeric_rows = []
    category_rows = []
    date_rows = []
    for item in profile["columns"]:
        summary = item.get("numeric_summary")
        if summary:
            numeric_rows.append(
                [
                    item["column"],
                    summary["min"],
                    summary["max"],
                    summary["quantiles"]["25%"],
                    summary["quantiles"]["50%"],
                    summary["quantiles"]["75%"],
                ]
            )
        for category in item.get("category_frequencies", []):
            category_rows.append([item["column"], category["value"], category["count"]])
        coverage = item.get("date_coverage")
        if coverage:
            date_rows.append(
                [item["column"], coverage["valid_count"], coverage["min"], coverage["max"]]
            )

    errors = []
    for item in profile["columns"]:
        if item["unparseable_count"]:
            examples = ", ".join(
                f"{value} ({count})"
                for value, count in item["unparseable_examples"].items()
            )
            errors.append([item["column"], item["unparseable_count"], examples])

    duplicates = profile["duplicates"]
    duplicate_rows = [
        ["Exact duplicate groups", duplicates["exact_duplicate_groups"]],
        ["Extra exact-duplicate rows", duplicates["exact_duplicate_extra_rows"]],
        ["Duplicate establishment-month groups", duplicates["business_key_duplicate_groups"]],
        ["Extra duplicate-key rows", duplicates["business_key_duplicate_extra_rows"]],
    ]
    duplicate_examples = [
        [example.get("establishment_id", ""), example.get("reporting_month", ""), example["row_count"]]
        for example in duplicates["business_key_examples"]
    ]

    report_timestamp = profile["generated_at_utc"]
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Data Profile - {html.escape(profile['run_id'])}</title>
  <style>
    body {{ font: 15px/1.5 -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; color: #20262e; margin: 2rem auto; padding: 0 1rem; max-width: 1100px; }}
    h1, h2 {{ color: #183a4a; }}
    .notice {{ padding: .75rem 1rem; background: #fff5d6; border-left: 4px solid #d6a72c; }}
    .meta {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(220px, 1fr)); gap: .5rem 1.5rem; }}
    table {{ border-collapse: collapse; width: 100%; margin: .75rem 0 1.5rem; }}
    th, td {{ border-bottom: 1px solid #d8dee3; padding: .45rem .6rem; text-align: left; vertical-align: top; }}
    th {{ background: #edf3f4; }}
    code {{ overflow-wrap: anywhere; }}
  </style>
</head>
<body>
  <h1>Data Profiling Report</h1>
  <p class="notice"><strong>Synthetic demonstration data.</strong> These records are not MoJ or HMPPS operational data. Profiling observations are not confirmed data errors.</p>
  <section class="meta">
    <div><strong>Run ID:</strong> {html.escape(profile['run_id'])}</div>
    <div><strong>Source file:</strong> {html.escape(profile['source_file'])}</div>
    <div><strong>Source SHA-256:</strong> <code>{html.escape(profile['source_sha256'])}</code></div>
    <div><strong>Generated (UTC):</strong> {html.escape(report_timestamp)}</div>
    <div><strong>Rows:</strong> {profile['row_count']}</div>
    <div><strong>Columns:</strong> {profile['column_count']}</div>
  </section>
  <h2>Column Profile</h2>
  {_table(['Column', 'Inferred type', 'Missing', 'Missing %', 'Unparseable', 'Distinct non-missing'], columns)}
  <h2>Exact and Business-Key Duplicates</h2>
  {_table(['Check', 'Count'], duplicate_rows)}
  <h3>Duplicate establishment-month examples</h3>
  {_table(['Establishment ID', 'Reporting month', 'Rows'], duplicate_examples)}
  <h2>Numeric Summaries</h2>
  {_table(['Column', 'Min', 'Max', '25th percentile', 'Median', '75th percentile'], numeric_rows)}
  <h2>Date Coverage</h2>
  {_table(['Column', 'Valid dates', 'Earliest', 'Latest'], date_rows)}
  <h2>Category Frequencies</h2>
  {_table(['Column', 'Category', 'Count'], category_rows)}
  <h2>Unparseable Values</h2>
  {_table(['Column', 'Count', 'Examples'], errors)}
</body>
</html>
"""


def profile_run(
    run_id: str,
    bronze_root: str | Path = "data/bronze",
    reports_root: str | Path = "reports",
) -> tuple[dict[str, Any], Path]:
    """Profile one Bronze run and write its HTML report."""
    if not _RUN_ID_PATTERN.fullmatch(run_id):
        raise ProfilingError("run_id contains unsupported characters.")

    run_directory = Path(bronze_root) / run_id
    metadata_path = run_directory / "metadata.json"
    if not metadata_path.is_file():
        raise ProfilingError(f"Run metadata does not exist: {metadata_path}")

    try:
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ProfilingError(f"Could not read run metadata: {error}") from error

    if metadata.get("run_id") != run_id:
        raise ProfilingError("Metadata run_id does not match the requested run.")
    source_file = metadata.get("source_file")
    if not isinstance(source_file, str) or Path(source_file).name != source_file:
        raise ProfilingError("Metadata has an invalid source filename.")
    bronze_file = run_directory / source_file
    if not bronze_file.is_file():
        raise ProfilingError(f"Bronze CSV does not exist: {bronze_file}")

    source_hash = _sha256(bronze_file)
    if metadata.get("source_sha256") != source_hash:
        raise ProfilingError("Bronze CSV hash does not match the run metadata.")

    try:
        frame = pd.read_csv(
            bronze_file,
            dtype=str,
            keep_default_na=False,
            encoding="utf-8-sig",
        )
    except (OSError, UnicodeError, pd.errors.ParserError) as error:
        raise ProfilingError(f"Could not parse Bronze CSV: {error}") from error

    if len(frame) != metadata.get("row_count"):
        raise ProfilingError("Bronze CSV row count does not match run metadata.")
    if len(frame.columns) != metadata.get("column_count"):
        raise ProfilingError("Bronze CSV column count does not match run metadata.")

    columns = [
        _column_profile(column, frame[column], len(frame)) for column in frame.columns
    ]
    profile = {
        "run_id": run_id,
        "source_file": source_file,
        "source_sha256": source_hash,
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "row_count": int(len(frame)),
        "column_count": int(len(frame.columns)),
        "columns": columns,
        "duplicates": _duplicate_summary(frame),
    }

    report_directory = Path(reports_root) / run_id
    report_directory.mkdir(parents=True, exist_ok=True)
    report_path = report_directory / "profiling_report.html"
    report_path.write_text(render_html_report(profile), encoding="utf-8")
    return profile, report_path


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Profile an ingested Bronze CSV and write an HTML report."
    )
    parser.add_argument("--run-id", required=True, help="Existing Bronze run ID")
    parser.add_argument("--bronze-root", default="data/bronze")
    parser.add_argument("--reports-root", default="reports")
    args = parser.parse_args()

    try:
        profile, report_path = profile_run(
            args.run_id,
            bronze_root=args.bronze_root,
            reports_root=args.reports_root,
        )
    except ProfilingError as error:
        parser.error(str(error))

    print(
        json.dumps(
            {
                "run_id": profile["run_id"],
                "row_count": profile["row_count"],
                "column_count": profile["column_count"],
                "report_path": str(report_path),
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()