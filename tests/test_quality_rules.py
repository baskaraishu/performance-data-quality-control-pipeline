from copy import deepcopy

import pytest

from src.rule_config import (
    ACTIONS,
    CHECK_PARAMETERS,
    KNOWN_FIELDS,
    SEVERITIES,
    RuleConfigError,
    load_rule_config,
    validate_rule_config,
)


def test_project_quality_rules_load_and_use_known_schema():
    config = load_rule_config()
    rules = config["rules"]
    rule_ids = [rule["rule_id"] for rule in rules]

    assert config["schema_version"] == 1
    assert len(rule_ids) == len(set(rule_ids))
    assert len(rules) >= 15
    for rule in rules:
        assert set(rule["columns"]) <= KNOWN_FIELDS
        assert rule["check"] in CHECK_PARAMETERS
        assert rule["severity"] in SEVERITIES
        assert rule["action"] in ACTIONS
        assert rule["description"].strip()


def test_rule_config_rejects_duplicate_ids():
    config = load_rule_config()
    invalid = deepcopy(config)
    invalid["rules"].append(deepcopy(invalid["rules"][0]))

    with pytest.raises(RuleConfigError, match="Duplicate rule_id"):
        validate_rule_config(invalid)


def test_rule_config_rejects_unknown_check_and_field():
    config = load_rule_config()
    invalid_check = deepcopy(config)
    invalid_check["rules"][0]["check"] = "guess_and_fix"
    with pytest.raises(RuleConfigError, match="unsupported check"):
        validate_rule_config(invalid_check)

    invalid_field = deepcopy(config)
    invalid_field["rules"][0]["columns"] = ["populaton"]
    with pytest.raises(RuleConfigError, match="unknown fields"):
        validate_rule_config(invalid_field)


def test_rule_config_rejects_missing_keys_and_bad_parameters():
    config = load_rule_config()
    missing_description = deepcopy(config)
    del missing_description["rules"][0]["description"]
    with pytest.raises(RuleConfigError, match="missing keys"):
        validate_rule_config(missing_description)

    invalid_range = deepcopy(config)
    percentage_rule = next(
        rule for rule in invalid_range["rules"] if rule["check"] == "between"
    )
    percentage_rule["parameters"] = {"min": 101, "max": 0}
    with pytest.raises(RuleConfigError, match="min greater than max"):
        validate_rule_config(invalid_range)


def test_rule_config_rejects_unsupported_action_and_severity():
    config = load_rule_config()
    invalid_action = deepcopy(config)
    invalid_action["rules"][0]["action"] = "delete"
    with pytest.raises(RuleConfigError, match="unsupported action"):
        validate_rule_config(invalid_action)

    invalid_severity = deepcopy(config)
    invalid_severity["rules"][0]["severity"] = "Urgent"
    with pytest.raises(RuleConfigError, match="unsupported severity"):
        validate_rule_config(invalid_severity)


def test_review_rules_keep_occupancy_over_100_as_flag():
    config = load_rule_config()
    rule = next(rule for rule in config["rules"] if rule["rule_id"] == "REVIEW-001")

    assert rule["check"] == "exceeds"
    assert rule["parameters"]["value"] == 100
    assert rule["action"] == "flag"