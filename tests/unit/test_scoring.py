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


def test_weak_confidence_dampens_score():
    strong = RiskScoreEngine()
    strong.add_finding("high", "tool_layer", "X", "strong")
    weak = RiskScoreEngine()
    weak.add_finding("high", "tool_layer", "X", "weak")
    assert weak.current_score() < strong.current_score()


def test_confidence_still_monotonic():
    engine = RiskScoreEngine()
    for severity, category, rule_id, confidence in [
        ("critical", "infrastructure", "A", "strong"),
        ("low", "memory_state", "B", "weak"),
        ("low", "memory_state", "C", "weak"),
        ("high", "tool_layer", "D", "weak"),
    ]:
        _, delta = engine.add_finding(severity, category, rule_id, confidence)
        assert delta >= 0


def test_compute_from_findings_accepts_3_or_4_tuples():
    findings = [
        ("high", "tool_layer", "A"),
        ("high", "tool_layer", "B", "weak"),
    ]
    score = RiskScoreEngine.compute_from_findings(findings)

    engine = RiskScoreEngine()
    engine.add_finding("high", "tool_layer", "A")
    engine.add_finding("high", "tool_layer", "B", "weak")
    assert score == engine.current_score()
