from sentinex_core.scoring import RiskScoreEngine


def test_empty_engine_scores_zero():
    engine = RiskScoreEngine()
    assert engine.current_score() == 0.0
    assert engine.finding_count == 0


def test_single_critical_infrastructure_caps_at_100():
    engine = RiskScoreEngine()
    score, delta = engine.add_finding("critical", "infrastructure", "INFRA-001")
    assert score == 100.0
    assert delta == 100.0


def test_score_is_monotonic_per_severity():
    low = RiskScoreEngine()
    low.add_finding("low", "llm_layer", "L-1")
    high = RiskScoreEngine()
    high.add_finding("high", "llm_layer", "H-1")
    assert high.current_score() > low.current_score()


def test_top_drivers_ordered_by_weight():
    engine = RiskScoreEngine()
    engine.add_finding("low", "llm_layer", "LOW-1")
    engine.add_finding("critical", "tool_layer", "CRIT-1")
    engine.add_finding("medium", "llm_layer", "MED-1")
    assert engine.top_drivers[0] == "CRIT-1"


def test_one_shot_matches_streaming():
    findings = [
        ("high", "tool_layer", "A"),
        ("medium", "business_logic", "B"),
        ("info", "llm_layer", "C"),
    ]
    engine = RiskScoreEngine()
    for sev, cat, rule in findings:
        engine.add_finding(sev, cat, rule)
    assert RiskScoreEngine.compute_from_findings(findings) == engine.current_score()
