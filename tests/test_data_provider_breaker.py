from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

from sources.data_provider import CircuitBreaker


def test_circuit_breaker_record_failure_counts_all_concurrent_updates():
    breaker = CircuitBreaker(max_failures=10, reset_after_seconds=60.0)

    with ThreadPoolExecutor(max_workers=8) as executor:
        list(executor.map(lambda _: breaker.record_failure(), range(25)))

    assert breaker.failures == 25
    assert breaker.allow() is False


def test_circuit_breaker_record_success_resets_failure_state():
    breaker = CircuitBreaker(max_failures=2, reset_after_seconds=60.0)

    breaker.record_failure()
    breaker.record_failure()
    assert breaker.allow() is False

    breaker.record_success()

    assert breaker.failures == 0
    assert breaker.last_failure_time == 0.0
    assert breaker.allow() is True


def test_circuit_breaker_allow_returns_open_and_blocked_states():
    breaker = CircuitBreaker(max_failures=2, reset_after_seconds=60.0)

    assert breaker.allow() is True

    breaker.record_failure()
    assert breaker.allow() is True

    breaker.record_failure()
    assert breaker.allow() is False
