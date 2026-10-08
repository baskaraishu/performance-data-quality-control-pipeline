"""Load and validate the declarative quality-rule configuration."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from src.ingestion import EXPECTED_PERFORMANCE_COLUMNS

ESTABLISHMENT_REFERENCE_COLUMNS = {
    "establishment_id",
    "establishment_name",
    "region",
    "establishment_type",
    "design_capacity",
    "certified_normal_accommodation",
}
KPI_REFERENCE_COLUMNS = {"kpi_name", "direction", "target_value", "definition"}
KNOWN_FIELDS = (
    set(EXPECTED_PERFORMANCE_COLUMNS)
    | ESTABLISHMENT_REFERENCE_COLUMNS
    | KPI_REFERENCE_COLUMNS
)
SEVERITIES = {"Critical", "High", "Medium", "Low"}
ACTIONS = {"flag", "quarantine"}
CHECK_PARAMETERS = {
    "not_null": set(),
    "valid_iso_date": {"format", "first_day_of_month"},
    "non_negative_integer": set(),
    "non_negative_number": set(),
    "between": {"min", "max"},
    "greater_than": {"value"},
    "exceeds": {"value"},
    "reference_exists": {"reference_file", "reference_key"},
    "reference_column_values": {"reference_file", "reference_column"},
    "unique_combination": set(),
}
REQUIRED_PARAMETERS = {
    "not_null": set(),
    "valid_iso_date": {"format"},
    "non_negative_integer": set(),
    "non_negative_number": set(),
    "between": {"min", "max"},
    "greater_than": {"value"},
    "exceeds": {"value"},
    "reference_exists": {"reference_file", "reference_key"},
    "reference_column_values": {"reference_file", "reference_column"},
    "unique_combination": set(),
}


class RuleConfigError(ValueError):
    """Raised when quality-rule configuration is incomplete or unsupported."""


def validate_rule_config(document: Any) -> dict[str, Any]:
    """Validate rule configuration structure without applying rules to data."""
    if not isinstance(document, dict):
        raise RuleConfigError("Rule configuration must be a YAML mapping.")
    if document.get("schema_version") != 1:
        raise RuleConfigError("schema_version must be 1.")

    rules = document.get("rules")
    if not isinstance(rules, list) or not rules:
        raise RuleConfigError("rules must be a non-empty list.")

    seen_rule_ids: set[str] = set()
    required_rule_keys = {
        "rule_id",
        "columns",
        "check",
        "parameters",
        "severity",
        "action",
        "description",
    }

    for index, rule in enumerate(rules, start=1):
        location = f"rules item {index}"
        if not isinstance(rule, dict):
            raise RuleConfigError(f"{location} must be a mapping.")
        missing_keys = required_rule_keys - rule.keys()
        unknown_keys = rule.keys() - required_rule_keys
        if missing_keys:
            raise RuleConfigError(
                f"{location} is missing keys: {', '.join(sorted(missing_keys))}."
            )
        if unknown_keys:
            raise RuleConfigError(
                f"{location} has unsupported keys: {', '.join(sorted(unknown_keys))}."
            )

        rule_id = rule["rule_id"]
        if not isinstance(rule_id, str) or not rule_id.strip():
            raise RuleConfigError(f"{location} has an empty or invalid rule_id.")
        if rule_id in seen_rule_ids:
            raise RuleConfigError(f"Duplicate rule_id: {rule_id}.")
        seen_rule_ids.add(rule_id)

        columns = rule["columns"]
        if (
            not isinstance(columns, list)
            or not columns
            or any(not isinstance(column, str) for column in columns)
        ):
            raise RuleConfigError(f"Rule {rule_id} must have a non-empty columns list.")
        unknown_columns = set(columns) - KNOWN_FIELDS
        if unknown_columns:
            raise RuleConfigError(
                f"Rule {rule_id} refers to unknown fields: {', '.join(sorted(unknown_columns))}."
            )

        check = rule["check"]
        if check not in CHECK_PARAMETERS:
            raise RuleConfigError(f"Rule {rule_id} uses unsupported check '{check}'.")
        parameters = rule["parameters"]
        if not isinstance(parameters, dict):
            raise RuleConfigError(f"Rule {rule_id} parameters must be a mapping.")
        missing_parameters = REQUIRED_PARAMETERS[check] - parameters.keys()
        unknown_parameters = parameters.keys() - CHECK_PARAMETERS[check]
        if missing_parameters:
            raise RuleConfigError(
                f"Rule {rule_id} is missing parameters: "
                f"{', '.join(sorted(missing_parameters))}."
            )
        if unknown_parameters:
            raise RuleConfigError(
                f"Rule {rule_id} has unsupported parameters: "
                f"{', '.join(sorted(unknown_parameters))}."
            )
        if check == "between" and parameters["min"] > parameters["max"]:
            raise RuleConfigError(f"Rule {rule_id} has min greater than max.")
        if check == "unique_combination" and len(columns) < 2:
            raise RuleConfigError(
                f"Rule {rule_id} unique_combination requires at least two columns."
            )
        if check in {"reference_exists", "reference_column_values"}:
            reference_file = parameters["reference_file"]
            if not isinstance(reference_file, str) or not reference_file.strip():
                raise RuleConfigError(f"Rule {rule_id} requires a reference_file path.")
            reference_column = parameters.get("reference_key") or parameters.get(
                "reference_column"
            )
            if reference_column not in KNOWN_FIELDS:
                raise RuleConfigError(
                    f"Rule {rule_id} refers to unknown reference field '{reference_column}'."
                )

        if rule["severity"] not in SEVERITIES:
            raise RuleConfigError(f"Rule {rule_id} has unsupported severity.")
        if rule["action"] not in ACTIONS:
            raise RuleConfigError(f"Rule {rule_id} has unsupported action.")
        if not isinstance(rule["description"], str) or not rule["description"].strip():
            raise RuleConfigError(f"Rule {rule_id} requires a plain-language description.")

    return document


def load_rule_config(path: str | Path = "rules/quality_rules.yml") -> dict[str, Any]:
    """Read YAML and validate it against the supported rule configuration schema."""
    config_path = Path(path)
    try:
        with config_path.open(encoding="utf-8") as source:
            document = yaml.safe_load(source)
    except OSError as error:
        raise RuleConfigError(f"Could not read rule configuration '{config_path}': {error}") from error
    except yaml.YAMLError as error:
        raise RuleConfigError(f"Invalid YAML in '{config_path}': {error}") from error
    return validate_rule_config(document)