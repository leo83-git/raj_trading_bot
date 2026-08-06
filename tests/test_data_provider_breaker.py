from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

from sources.data_provider import CircuitBreaker


def test_circuit_breaker_updates_are_thread_safe():
    breaker = CircuitBreaker(max_failures=5, reset_after_seconds=60.0)

    def fail_then_succeed(index: int) -> None:
        if index % 2 == 0:
            breaker.record_failure()
        else:
            breaker.record_success()
        breaker.allow()

    with ThreadPoolExecutor(max_workers=8) as executor:
        list(executor.map(fail_then_succeed, range(100)))

    with breaker._lock:
        assert breaker.failures >= 0
        assert breaker.last_failure_time >= 0.0
