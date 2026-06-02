from datetime import datetime, timedelta

from app.services.circuit_breaker import CircuitBreaker, CircuitBreakerConfig, CircuitBreakerOpenException, CircuitState


class ExplodingService:
    def __init__(self, failures: int):
        self.failures = failures
        self.calls = 0

    def call(self):
        self.calls += 1
        if self.calls <= self.failures:
            raise RuntimeError("service down")
        return "ok"


def test_circuit_breaker_opens_after_threshold():
    breaker = CircuitBreaker(CircuitBreakerConfig(failure_threshold=2, recovery_timeout=60))
    service = ExplodingService(failures=5)

    try:
        breaker.call(service.call)
    except RuntimeError:
        pass

    try:
        breaker.call(service.call)
    except RuntimeError:
        pass

    try:
        breaker.call(service.call)
    except CircuitBreakerOpenException:
        assert breaker.get_state()["state"] == "open"
        return

    raise AssertionError("Expected circuit breaker to open")


def test_circuit_breaker_recovers_after_half_open_success():
    breaker = CircuitBreaker(CircuitBreakerConfig(failure_threshold=1, recovery_timeout=1))
    service = ExplodingService(failures=0)

    try:
        breaker.call(service.call)
    except RuntimeError:
        pass

    breaker.state = CircuitState.HALF_OPEN
    breaker.opened_at = datetime.now() - timedelta(seconds=2)
    breaker.success_count = 0

    first_result = breaker.call(service.call)
    assert first_result == "ok"
    assert breaker.get_state()["state"] == "half_open"

    second_result = breaker.call(service.call)
    assert second_result == "ok"
    assert breaker.get_state()["state"] == "closed"
