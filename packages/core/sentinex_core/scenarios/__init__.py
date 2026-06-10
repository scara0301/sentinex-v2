"""Attack scenario DSL, runner, and built-in scenario library."""

from __future__ import annotations

from importlib import resources

from .dsl import (
    DetectionSpec,
    InjectionSpec,
    MatchSpec,
    ScenarioSpec,
    parse_scenario_yaml,
    validate_scenario_yaml,
)
from .runner import ScenarioRunner

__all__ = [
    "DetectionSpec",
    "InjectionSpec",
    "MatchSpec",
    "ScenarioSpec",
    "ScenarioRunner",
    "parse_scenario_yaml",
    "validate_scenario_yaml",
    "iter_builtin_scenarios",
    "builtin_specs",
]


def iter_builtin_scenarios() -> list[tuple[str, ScenarioSpec]]:
    """Return ``(yaml_text, parsed_spec)`` for every packaged builtin scenario."""
    out: list[tuple[str, ScenarioSpec]] = []
    root = resources.files(__name__) / "builtin"
    for entry in sorted(root.iterdir(), key=lambda e: e.name):
        if entry.name.endswith((".yaml", ".yml")):
            text = entry.read_text(encoding="utf-8")
            out.append((text, parse_scenario_yaml(text)))
    return out


def builtin_specs() -> list[ScenarioSpec]:
    return [spec for _, spec in iter_builtin_scenarios()]
