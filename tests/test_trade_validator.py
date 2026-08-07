from datetime import UTC, datetime, timedelta

from core.order_manager import OrderManager
from core.trade_validator import PortfolioGreeks, TradeIntent, TradeValidator


def valid_intent(**changes):
    values = {
        "symbol": "NIFTY26AUG25000CE",
        "action": "BUY",
        "quantity": 25,
        "entry_price": 100.0,
        "stop_loss": 90.0,
        "target": 120.0,
        "quote_timestamp": datetime.now(UTC),
        "session_open": True,
        "lot_size": 25,
        "bid_price": 99.5,
        "ask_price": 100.5,
        "available_quantity": 100,
        "expected_slippage_pct": 0.002,
        "required_margin": 2500.0,
        "available_margin": 10000.0,
        "projected_exposure_pct": 0.10,
        "implied_volatility": 20.0,
        "drawdown_pct": 0.05,
        "max_correlation": 0.4,
        "strategy_max_loss": 250.0,
        "portfolio_greeks": PortfolioGreeks(delta=10),
    }
    values.update(changes)
    return TradeIntent(**values)


def test_valid_entry_passes_every_check():
    result = TradeValidator().validate(valid_intent())
    assert result.is_valid
    assert result.status == "approved"
    assert result.errors == ()
    assert all(result.checks.values())


def test_entry_reports_execution_and_portfolio_failures():
    result = TradeValidator().validate(
        valid_intent(
            quote_timestamp=datetime.now(UTC) - timedelta(minutes=2),
            session_open=False,
            stop_loss=110.0,
            quantity=24,
            available_quantity=1,
            expected_slippage_pct=0.02,
            projected_exposure_pct=0.5,
            drawdown_pct=0.3,
            max_correlation=0.9,
            strategy_max_loss=200000,
            portfolio_greeks=PortfolioGreeks(delta=20000),
            circuit_breaker_active=True,
        )
    )
    assert not result.is_valid
    assert result.status == "halted"
    assert {
        "data_freshness",
        "session",
        "stop_target_direction",
        "quantity_and_lot",
        "liquidity",
        "slippage",
        "exposure",
        "drawdown",
        "correlation",
        "strategy_max_loss",
        "portfolio_delta",
        "circuit_breaker",
    } <= set(result.errors)


def test_sell_requires_reversed_stop_and_target():
    assert (
        TradeValidator()
        .validate(valid_intent(action="SELL", stop_loss=110, target=80))
        .is_valid
    )
    assert not TradeValidator().validate(valid_intent(action="SELL")).is_valid


def test_circuit_breakers_allow_exit_orders():
    result = TradeValidator().validate(
        valid_intent(
            is_exit=True,
            session_open=False,
            circuit_breaker_active=True,
            kill_switch_active=True,
            quote_timestamp=datetime(2000, 1, 1, tzinfo=UTC),
        )
    )
    assert result.is_valid
    assert result.status == "approved"
    assert result.checks == {"exit_allowed": True}


class BrokerSpy:
    def __init__(self):
        self.calls = []

    def place_order(self, **kwargs):
        self.calls.append(kwargs)
        return {"status": "success"}


def test_order_manager_blocks_invalid_entry_before_broker_submission():
    broker = BrokerSpy()
    manager = OrderManager(broker, db_manager=False, trade_validator=TradeValidator())
    result = manager.place_order("NIFTY", 1, "BUY", price=100, metadata={})
    assert result["status"] == "rejected"
    assert broker.calls == []


def test_order_manager_submits_explicit_exit_during_circuit_breaker():
    broker = BrokerSpy()
    manager = OrderManager(broker, db_manager=False, trade_validator=TradeValidator())
    result = manager.place_order(
        "NIFTY",
        1,
        "SELL",
        price=100,
        metadata={"is_exit": True, "circuit_breaker_active": True},
    )
    assert result["status"] == "success"
    assert len(broker.calls) == 1


def test_order_manager_fails_closed_when_validator_raises():
    class BrokenValidator:
        def validate(self, intent):
            raise RuntimeError("validator unavailable")

    broker = BrokerSpy()
    manager = OrderManager(broker, db_manager=False, trade_validator=BrokenValidator())
    result = manager.place_order("NIFTY", 1, "BUY", price=100, metadata={})
    assert result == {
        "status": "rejected",
        "reason": "trade_validation_exception",
        "errors": ["validator_exception"],
    }
    assert broker.calls == []
