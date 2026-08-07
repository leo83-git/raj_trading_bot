import json
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

from core.options.chain_manager import OptionChainManager
from core.options.contract_selector import ContractSelector, SelectionCriteria
from core.options.models import ChainSource, OptionType, normalize_option_chain
from trade_quality.options_edge import OptionsStrategySelector

FIXTURE = Path(__file__).parent / "fixtures" / "options" / "nifty_chain_recorded.json"


def recorded_payload():
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def test_normalizes_recorded_nse_payload_to_typed_chain():
    chain = normalize_option_chain(recorded_payload(), "NIFTY")
    assert chain.underlying == "NIFTY"
    assert chain.spot == 25040.0
    assert chain.source is ChainSource.RECORDED
    assert len(chain.contracts) == 6
    assert chain.expiries == (date(2026, 8, 13), date(2026, 8, 20))


def test_delta_selection_uses_nearest_valid_expiry_and_liquidity():
    chain = normalize_option_chain(recorded_payload(), "NIFTY")
    result = ContractSelector().select(
        chain,
        SelectionCriteria(
            OptionType.CALL,
            target_delta=0.50,
            min_dte=1,
            max_dte=10,
            min_volume=1000,
            min_open_interest=10000,
            max_spread_pct=2.0,
        ),
        today=date(2026, 8, 8),
    )
    assert result.accepted
    assert result.method == "delta"
    assert result.contract.symbol == "NIFTY13AUG2625000CE"


def test_atm_fallback_and_structured_liquidity_rejection():
    chain = normalize_option_chain(recorded_payload(), "NIFTY")
    selector = ContractSelector()
    atm = selector.select(
        chain,
        SelectionCriteria(OptionType.PUT, min_dte=1, max_dte=10),
        today=date(2026, 8, 8),
    )
    assert atm.accepted and atm.method == "atm_fallback"
    assert atm.contract.strike == 25000

    rejected = selector.select(
        chain,
        SelectionCriteria(
            OptionType.PUT, min_dte=1, max_dte=10, min_open_interest=200000
        ),
        today=date(2026, 8, 8),
    )
    assert not rejected.accepted
    assert "open_interest_below_minimum" in rejected.reasons


def test_freshness_cache_and_synthetic_live_safety():
    manager = OptionChainManager(ttl_seconds=60)
    now = datetime(2026, 8, 8, 9, 20, tzinfo=UTC)
    chain = manager.normalize(recorded_payload(), "NIFTY", fetched_at=now)
    manager.put(chain)
    assert manager.get("NIFTY", now=now + timedelta(seconds=59)) == chain
    assert manager.get("NIFTY", now=now + timedelta(seconds=61)) is None

    payload = recorded_payload()
    payload["source"] = "synthetic"
    synthetic = manager.normalize(payload, "NIFTY", fetched_at=now)
    validation = manager.validate(synthetic, live_order=True, now=now)
    assert not validation.accepted
    assert "synthetic_or_untrusted_chain_for_live_order" in validation.reasons


def test_strategy_selector_rejects_unvalidated_or_bad_dte_and_prefers_defined_risk():
    selector = OptionsStrategySelector(
        {
            "multi_leg_enabled": True,
            "min_dte": 2,
            "max_dte": 30,
            "prefer_defined_risk": True,
        }
    )
    rejected = selector.select_strategy(
        "NIFTY", 25040, 25, 1 / 365, "SIDEWAYS", signal_validated=False
    )
    assert not rejected.validated
    assert set(rejected.rejection_reasons) == {
        "upstream_signal_not_validated",
        "dte_out_of_range",
    }

    selected = selector.select_strategy(
        "NIFTY",
        25040,
        25,
        7 / 365,
        "SIDEWAYS",
        open_interest=200000,
        theta=-15,
        skew=1.0,
    )
    assert selected.validated
    assert selected.defined_risk
    assert selected.strategy in {"IRON_BUTTERFLY", "BULL_PUT_SPREAD"}


if __name__ == "__main__":
    test_normalizes_recorded_nse_payload_to_typed_chain()
    test_delta_selection_uses_nearest_valid_expiry_and_liquidity()
    test_atm_fallback_and_structured_liquidity_rejection()
    test_freshness_cache_and_synthetic_live_safety()
    test_strategy_selector_rejects_unvalidated_or_bad_dte_and_prefers_defined_risk()
    print("5 P5 option tests passed")
