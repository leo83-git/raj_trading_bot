from __future__ import annotations

from screener.engine.p3_components import (
    CandidateRanker,
    EnsembleValidationService,
    FnoFeatureScorer,
)


def test_ensemble_validation_rejects_hold_and_neutral_market():
    service = EnsembleValidationService({"min_confidence": 0.08, "min_consensus": 0.5})
    result = service.validate(
        {"score": 0.01, "confidence": 0.01, "consensus": 0.2, "signal": "BUY"},
        market_direction="NEUTRAL",
        low_confidence=True,
    )
    assert result["rejected"] is True
    assert result["signal"] == "HOLD"
    assert "low_confidence" in result["reasons"]
    assert "neutral_market" in result["reasons"]


def test_ensemble_validation_reports_low_consensus():
    service = EnsembleValidationService({"min_confidence": 0.0, "min_consensus": 0.5})
    result = service.validate(
        {"score": 0.4, "confidence": 0.4, "consensus": 0.2, "signal": "BUY"},
        market_direction="BULLISH",
    )
    assert result["rejected"] is True
    assert "low_consensus" in result["reasons"]


def test_ensemble_validation_reports_conflicting_evidence():
    service = EnsembleValidationService(
        {"min_confidence": 0.0, "min_consensus": 0.0, "min_evidence_score": 0.25}
    )
    result = service.validate(
        {"score": 0.4, "confidence": 0.4, "consensus": 0.8, "signal": "BUY"},
        market_direction="BULLISH",
        evidence_score=0.1,
    )
    assert result["rejected"] is True
    assert "conflicting_evidence" in result["reasons"]


def test_shadow_compare_is_deterministic():
    service = EnsembleValidationService()
    compare = service.shadow_compare(
        {"signal": "BUY", "score": 0.3, "confidence": 0.4},
        {"signal": "BUY", "score": 0.2, "confidence": 0.3},
    )
    assert compare == {
        "primary_signal": "BUY",
        "shadow_signal": "BUY",
        "agreement": True,
        "primary_score": 0.3,
        "shadow_score": 0.2,
        "primary_confidence": 0.4,
        "shadow_confidence": 0.3,
    }


def test_fno_feature_scoring_keeps_missing_data_neutral():
    scorer = FnoFeatureScorer({"fno": {"min_volume": 10000, "min_price": 10}})
    result = scorer.score({"symbol": "NIFTY", "category": "fno", "features": {}})
    assert result["symbol"] == "NIFTY"
    assert result["score"] >= 0.0
    assert any(
        component["reason"] == "Missing OI" for component in result["components"]
    )
    assert result["score"] == round(
        sum(component["score"] for component in result["components"]), 4
    )


def test_candidate_ranker_uses_stable_tie_breaks():
    ranker = CandidateRanker()
    ranked = ranker.rank(
        [
            {"symbol": "B", "score": 1.0, "confidence": 0.2},
            {"symbol": "A", "score": 1.0, "confidence": 0.2},
        ]
    )
    assert [item["symbol"] for item in ranked] == ["A", "B"]
    assert [item["rank"] for item in ranked] == [1, 2]
