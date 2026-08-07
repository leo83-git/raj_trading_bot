from __future__ import annotations

import json

import pytest

from execution.journal import ExecutionJournal, JournalCorruptionError
from execution.models import (
    EXIT_PRECEDENCE,
    ExecutionState,
    ExitPolicyInput,
    ExitReason,
    LegExecution,
    LegState,
    StrategyExecution,
    deterministic_key,
    evaluate_exit_policy,
)
from execution.multileg_coordinator import MultiLegCoordinator


class PaperBrokerFixture:
    """Deterministic in-memory adapter; it never reaches a live broker."""

    def __init__(self, submissions, reconciliations=None):
        self.submissions = list(submissions)
        self.reconciliations = list(reconciliations or [])
        self.events = []

    def submit(self, leg, *, compensation=False):
        self.events.append(("compensate" if compensation else "submit", leg.index))
        return self.submissions.pop(0)

    def reconcile(self, leg, *, compensation=False):
        self.events.append(("reconcile", leg.index))
        return self.reconciliations.pop(0) if self.reconciliations else None

    def cancel(self, order_id):
        self.events.append(("cancel", order_id))
        return {"status": "cancelled"}


class RaisingPaperBrokerFixture(PaperBrokerFixture):
    def submit(self, leg, *, compensation=False):
        self.events.append(("compensate" if compensation else "submit", leg.index))
        raise TimeoutError("paper broker timeout")


def _legs():
    return [
        {"symbol": "NIFTY26AUG25000CE", "action": "BUY", "quantity": 10, "price": 10.0},
        {
            "symbol": "NIFTY26AUG25100CE",
            "action": "SELL",
            "quantity": 10,
            "price": 15.0,
        },
    ]


def test_ambiguous_response_is_reconciled_before_retry(tmp_path):
    paper = PaperBrokerFixture(
        submissions=[
            None,
            {"status": "success", "quantity": 10, "price": 15.0, "order_id": "o2"},
        ],
        reconciliations=[
            {
                "status": "complete",
                "filled_quantity": 10,
                "average_price": 10.0,
                "order_id": "o1",
            }
        ],
    )
    coordinator = MultiLegCoordinator(
        paper, ExecutionJournal(tmp_path / "journal.json")
    )

    result = coordinator.execute("NIFTY", "VERTICAL", _legs(), expected_net="CREDIT")

    assert result.state is ExecutionState.COMPLETED
    assert paper.events == [("submit", 0), ("reconcile", 0), ("submit", 1)]
    assert result.legs[0].idempotency_key == deterministic_key(result.strategy_id, 0)


def test_partial_failure_is_compensated_and_flattened(tmp_path):
    paper = PaperBrokerFixture(
        submissions=[
            {"status": "success", "quantity": 10, "price": 10.0, "order_id": "o1"},
            {"status": "rejected", "quantity": 0, "order_id": "o2"},
            {
                "status": "success",
                "quantity": 10,
                "price": 10.1,
                "order_id": "flatten-1",
            },
        ]
    )
    journal = ExecutionJournal(tmp_path / "journal.json")
    result = MultiLegCoordinator(paper, journal).execute(
        "NIFTY", "VERTICAL", _legs(), expected_net="CREDIT"
    )

    assert result.state is ExecutionState.FLATTENED
    assert result.failure_reason == "leg_1_failed"
    assert result.legs[0].state is LegState.COMPENSATED
    assert paper.events[-1] == ("compensate", 0)
    assert journal.get(result.strategy_id).state is ExecutionState.FLATTENED


def test_restart_recovery_reconciles_then_compensates_known_fill(tmp_path):
    journal = ExecutionJournal(tmp_path / "journal.json")
    execution = StrategyExecution(
        strategy_id="restart-case",
        strategy_name="VERTICAL",
        underlying="NIFTY",
        expected_net="ANY",
        max_net_amount=None,
        state=ExecutionState.SUBMITTING,
        legs=[
            LegExecution(
                0,
                "NIFTY26AUG25000CE",
                "BUY",
                10,
                10.0,
                "key-0",
                LegState.FILLED,
                "o1",
                10,
                10.0,
            ),
            LegExecution(
                1, "NIFTY26AUG25100CE", "SELL", 10, 15.0, "key-1", LegState.PENDING
            ),
        ],
    )
    journal.save(execution)
    paper = PaperBrokerFixture(
        submissions=[
            {"status": "complete", "filled_quantity": 10, "average_price": 10.0}
        ],
        reconciliations=[None],
    )

    recovered = MultiLegCoordinator(paper, journal).recover_incomplete()

    assert recovered[0].state is ExecutionState.HALTED
    assert paper.events == [("reconcile", 1), ("compensate", 0)]
    assert journal.get("restart-case").state is ExecutionState.HALTED


def test_projected_debit_credit_validation_blocks_submission(tmp_path):
    paper = PaperBrokerFixture(submissions=[])
    result = MultiLegCoordinator(
        paper, ExecutionJournal(tmp_path / "journal.json")
    ).execute("NIFTY", "VERTICAL", _legs(), expected_net="DEBIT")
    assert result.state is ExecutionState.FAILED
    assert result.failure_reason == "projected_net_validation_failed"
    assert paper.events == []


def test_exit_policy_has_one_documented_precedence_order():
    assert EXIT_PRECEDENCE == (
        ExitReason.EMERGENCY_HALT,
        ExitReason.SQUARE_OFF,
        ExitReason.EXPIRY,
        ExitReason.STOP_LOSS,
        ExitReason.TRAILING_STOP,
        ExitReason.PROFIT_LADDER,
        ExitReason.TARGET,
        ExitReason.HOLD,
    )
    all_triggered = ExitPolicyInput(
        action="BUY",
        current_price=90,
        stop_loss=95,
        target=80,
        trailing_stop=96,
        profit_ladder_price=80,
        emergency_halt=True,
        square_off_due=True,
        expired=True,
    )
    assert evaluate_exit_policy(all_triggered).reason is ExitReason.EMERGENCY_HALT

    price_only = ExitPolicyInput(
        action="BUY",
        current_price=90,
        stop_loss=95,
        target=80,
        trailing_stop=96,
        profit_ladder_price=80,
    )
    assert evaluate_exit_policy(price_only).reason is ExitReason.STOP_LOSS


def test_corrupt_journal_fails_closed(tmp_path):
    path = tmp_path / "journal.json"
    path.write_text("{not-json", encoding="utf-8")

    with pytest.raises(JournalCorruptionError, match="Cannot safely recover"):
        ExecutionJournal(path).incomplete()


def test_journal_redacts_broker_credentials(tmp_path):
    journal = ExecutionJournal(tmp_path / "journal.json")
    execution = StrategyExecution(
        strategy_id="redaction-case",
        strategy_name="VERTICAL",
        underlying="NIFTY",
        expected_net="ANY",
        max_net_amount=None,
        state=ExecutionState.FAILED,
        legs=[
            LegExecution(
                0,
                "NIFTY26AUG25000CE",
                "BUY",
                10,
                10.0,
                "key-0",
                raw={"access_token": "secret-value", "status": "rejected"},
            )
        ],
    )

    journal.save(execution)
    payload = json.loads(journal.path.read_text(encoding="utf-8"))

    assert payload["redaction-case"]["legs"][0]["raw"]["access_token"] == "[REDACTED]"
    assert "secret-value" not in journal.path.read_text(encoding="utf-8")


def test_broker_exception_is_journaled_and_halted_without_escape(tmp_path):
    paper = RaisingPaperBrokerFixture(submissions=[])
    result = MultiLegCoordinator(
        paper, ExecutionJournal(tmp_path / "journal.json"), max_retries=0
    ).execute("NIFTY", "SINGLE", [_legs()[0]])

    assert result.state is ExecutionState.HALTED
    assert result.failure_reason == "leg_0_ambiguous"
