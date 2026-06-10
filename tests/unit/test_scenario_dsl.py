import pytest

from sentinex_core.scenarios import (
    iter_builtin_scenarios,
    parse_scenario_yaml,
    validate_scenario_yaml,
)

VALID = """
version: 1
slug: my-scenario
name: My Scenario
description: test
tags: [a, b]
injections:
  - tool: "stripe.*"
    mode: merge
    payload:
      note: poisoned
detections:
  - rule_id: TEST-001
    severity: high
    category: tool_layer
    title: Something bad
    match:
      event: tool_call
      host_contains: [evil]
"""


def test_parse_valid_scenario():
    spec = parse_scenario_yaml(VALID)
    assert spec.slug == "my-scenario"
    assert spec.injections[0].tool == "stripe.*"
    assert spec.detections[0].severity == "high"
    assert spec.detections[0].match.host_contains == ["evil"]


def test_validate_rejects_invalid_yaml_syntax():
    with pytest.raises(ValueError):
        validate_scenario_yaml("foo: [unclosed")


def test_validate_rejects_non_mapping():
    with pytest.raises(ValueError, match="mapping"):
        validate_scenario_yaml("- just\n- a\n- list")


def test_validate_rejects_unknown_category():
    bad = VALID.replace("tool_layer", "made_up_category")
    with pytest.raises(ValueError, match="category"):
        validate_scenario_yaml(bad)


def test_validate_rejects_missing_detections():
    bad = """
slug: no-detections
name: No Detections
detections: []
"""
    with pytest.raises(ValueError):
        validate_scenario_yaml(bad)


def test_validate_rejects_bad_slug():
    bad = VALID.replace("my-scenario", "Bad Slug!")
    with pytest.raises(ValueError):
        validate_scenario_yaml(bad)


def test_builtin_scenarios_parse():
    builtins = iter_builtin_scenarios()
    slugs = {spec.slug for _, spec in builtins}
    assert slugs == {
        "return-path-poisoning",
        "data-exfiltration",
        "denial-of-wallet",
    }
    for _, spec in builtins:
        assert spec.detections, spec.slug
