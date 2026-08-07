"""Phase P3 screening and ensemble helpers.

This module isolates eligibility, prefiltering, enrichment, explainable
F&O scoring, ranking, and ensemble validation so the legacy entry points can
remain as thin wrappers.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

from quant_utils.logger import get_logger

log = get_logger("screener.engine.p3")


INDEX_SYMBOLS = {
    "NIFTY",
    "BANKNIFTY",
    "FINNIFTY",
    "MIDCPNIFTY",
    "NIFTYNXT50",
    "SENSEX",
    "BANKEX",
    "SENSEX50",
}


@dataclass(slots=True)
class ExplainableComponent:
    name: str
    score: float
    weight: float
    value: Any = None
    reason: str = ""


@dataclass(slots=True)
class CandidateAssessment:
    symbol: str
    category: str
    eligible: bool
    confidence: float
    score: float
    signal: str
    reason: str
    components: list[ExplainableComponent] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        if value is None:
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def _neutral_component(name: str, weight: float, reason: str) -> ExplainableComponent:
    return ExplainableComponent(
        name=name, score=0.0, weight=weight, value=None, reason=reason
    )


class EnsembleValidationService:
    """Validate and compare ensemble signals without mutating execution logic."""

    def __init__(self, config: dict[str, Any] | None = None):
        self.config = config or {}
        self.min_confidence = _safe_float(self.config.get("min_confidence"), 0.08)
        self.min_consensus = _safe_float(self.config.get("min_consensus"), 0.5)
        self.max_conflict_gap = _safe_float(self.config.get("max_conflict_gap"), 0.25)

    def validate(
        self,
        ensemble_result: dict[str, Any],
        *,
        market_direction: str | None = None,
        model_confidence: float | None = None,
        evidence_score: float | None = None,
        stale: bool = False,
        low_confidence: bool = False,
    ) -> dict[str, Any]:
        signal = str(ensemble_result.get("signal", "HOLD")).upper()
        consensus = _safe_float(ensemble_result.get("consensus"), 0.0)
        confidence = _safe_float(ensemble_result.get("confidence"), 0.0)
        score = _safe_float(ensemble_result.get("score"), 0.0)
        market_direction = str(market_direction or "NEUTRAL").upper()

        rejected = False
        reasons: list[str] = []
        if stale:
            rejected = True
            reasons.append("stale_evidence")
        if low_confidence or confidence < self.min_confidence:
            rejected = True
            reasons.append("low_confidence")
        if consensus < self.min_consensus:
            rejected = True
            reasons.append("low_consensus")
        if market_direction == "NEUTRAL" and signal in {"BUY", "SELL"}:
            reasons.append("neutral_market")
        if (
            evidence_score is not None
            and abs(evidence_score) < self.max_conflict_gap
            and signal != "HOLD"
        ):
            reasons.append("conflicting_evidence")
            rejected = True

        validated_signal = "HOLD" if rejected else signal
        return {
            "signal": validated_signal,
            "score": score,
            "confidence": confidence,
            "consensus": consensus,
            "rejected": rejected,
            "reasons": reasons,
            "model_confidence": _safe_float(model_confidence, confidence),
        }

    def shadow_compare(
        self,
        primary: dict[str, Any],
        shadow: dict[str, Any],
    ) -> dict[str, Any]:
        primary_signal = str(primary.get("signal", "HOLD")).upper()
        shadow_signal = str(shadow.get("signal", "HOLD")).upper()
        return {
            "primary_signal": primary_signal,
            "shadow_signal": shadow_signal,
            "agreement": primary_signal == shadow_signal,
            "primary_score": _safe_float(primary.get("score"), 0.0),
            "shadow_score": _safe_float(shadow.get("score"), 0.0),
            "primary_confidence": _safe_float(primary.get("confidence"), 0.0),
            "shadow_confidence": _safe_float(shadow.get("confidence"), 0.0),
        }


class FnoFeatureScorer:
    """Explainable F&O feature scoring with neutral handling for missing data."""

    def __init__(self, config: dict[str, Any] | None = None):
        self.config = config or {}
        self.thresholds = self.config.get("fno", {})
        self.min_volume = _safe_float(self.thresholds.get("min_volume"), 10000)
        self.min_price = _safe_float(self.thresholds.get("min_price"), 10)
        self.category_thresholds = self.config.get("p3_thresholds", {})

    def score(self, stock: dict[str, Any]) -> dict[str, Any]:
        symbol = str(stock.get("symbol", "")).upper().strip()
        category = str(stock.get("category", "intraday")).lower()
        features = (
            stock.get("features") if isinstance(stock.get("features"), dict) else stock
        )
        components: list[ExplainableComponent] = []

        index_bonus = 0.0
        if symbol in INDEX_SYMBOLS:
            index_bonus = 0.12
            components.append(
                ExplainableComponent(
                    "index", index_bonus, 0.12, symbol, "Index eligibility bonus"
                )
            )
        else:
            components.append(_neutral_component("index", 0.12, "Not an index"))

        oi = _safe_float(features.get("open_interest"))
        pcr = _safe_float(features.get("pcr"), 0.0)
        iv = _safe_float(features.get("iv"), 0.0)
        spread = _safe_float(features.get("spread_pct"), 0.0)
        liquidity = _safe_float(features.get("liquidity_score"), 0.0)
        volume = _safe_float(features.get("relative_volume"), 0.0)
        momentum = _safe_float(features.get("momentum_score"), 0.0)
        trend = str(features.get("trend", "SIDEWAYS")).upper()
        dte = features.get("dte")

        components.extend(
            [
                ExplainableComponent(
                    "oi",
                    min(0.14, oi / 1_000_000 * 0.14) if oi else 0.0,
                    0.14,
                    oi,
                    "Open interest strength" if oi else "Missing OI",
                ),
                ExplainableComponent(
                    "iv",
                    max(0.0, 0.10 - abs(iv - 0.5) * 0.10) if iv else 0.0,
                    0.10,
                    iv,
                    "Implied volatility quality" if iv else "Missing IV",
                ),
                ExplainableComponent(
                    "pcr",
                    0.10 if 0.8 <= pcr <= 1.2 else 0.03 if pcr else 0.0,
                    0.10,
                    pcr,
                    "PCR balance" if pcr else "Missing PCR",
                ),
                ExplainableComponent(
                    "spread",
                    max(0.0, 0.10 - spread * 0.02) if spread else 0.0,
                    0.10,
                    spread,
                    "Tight spread" if spread else "Missing spread",
                ),
                ExplainableComponent(
                    "liquidity",
                    min(0.12, liquidity * 0.12) if liquidity else 0.0,
                    0.12,
                    liquidity,
                    "Liquidity support" if liquidity else "Missing liquidity",
                ),
                ExplainableComponent(
                    "volume",
                    min(0.12, volume * 0.12) if volume else 0.0,
                    0.12,
                    volume,
                    "Relative volume" if volume else "Missing volume",
                ),
                ExplainableComponent(
                    "momentum",
                    min(0.10, abs(momentum) * 0.10) if momentum else 0.0,
                    0.10,
                    momentum,
                    "Momentum strength" if momentum else "Missing momentum",
                ),
                ExplainableComponent(
                    "trend",
                    (
                        0.10
                        if trend == "UPTREND"
                        else 0.03 if trend == "SIDEWAYS" else 0.0
                    ),
                    0.10,
                    trend,
                    "Trend alignment" if trend else "Missing trend",
                ),
                ExplainableComponent(
                    "dte",
                    (
                        0.10
                        if isinstance(dte, (int, float)) and 7 <= float(dte) <= 45
                        else 0.04 if dte is not None else 0.0
                    ),
                    0.10,
                    dte,
                    "Healthy DTE" if dte is not None else "Missing DTE",
                ),
            ]
        )

        raw_score = sum(component.score for component in components)
        if category == "fno":
            raw_score += index_bonus
        confidence = min(0.95, max(0.0, raw_score))
        signal = "BUY" if raw_score >= 0.55 else "SELL" if raw_score <= 0.25 else "HOLD"
        return {
            "symbol": symbol,
            "category": category,
            "score": round(raw_score, 4),
            "confidence": round(confidence, 4),
            "signal": signal,
            "components": [asdict(component) for component in components],
        }


class CandidateRanker:
    """Rank candidates by score with stable tie-breaking."""

    def rank(self, candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
        ranked = sorted(
            candidates,
            key=lambda item: (
                _safe_float(item.get("score"), 0.0),
                _safe_float(item.get("confidence"), 0.0),
                str(item.get("symbol", "")),
            ),
            reverse=True,
        )
        for index, candidate in enumerate(ranked, start=1):
            candidate["rank"] = index
        return ranked
