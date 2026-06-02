"""Circuit breaker pattern for LLM requests"""

import logging
from datetime import datetime, timedelta
from enum import Enum
from functools import wraps
from typing import Any, Callable, Dict, Optional

logger = logging.getLogger(__name__)


class CircuitState(Enum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


class CircuitBreakerConfig:
    def __init__(
        self,
        failure_threshold: int = 5,
        recovery_timeout: int = 60,
        expected_exception: type = Exception,
        name: str = "CircuitBreaker",
    ):
        self.failure_threshold = failure_threshold
        self.recovery_timeout = recovery_timeout
        self.expected_exception = expected_exception
        self.name = name


class CircuitBreaker:
    """
    Sync circuit breaker: CLOSED → OPEN after failure_threshold failures,
    OPEN → HALF_OPEN after recovery_timeout seconds, HALF_OPEN → CLOSED
    after 2 consecutive successes.
    """

    def __init__(self, config: CircuitBreakerConfig):
        self.config = config
        self.state = CircuitState.CLOSED
        self.failure_count = 0
        self.success_count = 0
        self.last_failure_time: Optional[datetime] = None
        self.opened_at: Optional[datetime] = None

    def call(self, func: Callable, *args, **kwargs) -> Any:
        if self.state == CircuitState.OPEN:
            if self._should_attempt_reset():
                self.state = CircuitState.HALF_OPEN
                self.success_count = 0
                logger.info("%s: OPEN → HALF_OPEN, attempting recovery", self.config.name)
            else:
                raise CircuitBreakerOpenException(
                    "%s: circuit is OPEN, retry in %d seconds" % (self.config.name, self._time_until_retry())
                )

        try:
            result = func(*args, **kwargs)
            self._on_success()
            return result
        except self.config.expected_exception:
            self._on_failure()
            raise

    def _on_success(self) -> None:
        self.failure_count = 0
        self.last_failure_time = None

        if self.state == CircuitState.HALF_OPEN:
            self.success_count += 1
            if self.success_count >= 2:
                self.state = CircuitState.CLOSED
                self.opened_at = None
                logger.info("%s: HALF_OPEN → CLOSED, service recovered", self.config.name)

    def _on_failure(self) -> None:
        self.failure_count += 1
        self.last_failure_time = datetime.now()

        if self.state == CircuitState.HALF_OPEN:
            self.state = CircuitState.OPEN
            self.opened_at = datetime.now()
            logger.warning("%s: HALF_OPEN → OPEN (failed during recovery)", self.config.name)
        elif self.failure_count >= self.config.failure_threshold:
            self.state = CircuitState.OPEN
            self.opened_at = datetime.now()
            logger.warning(
                "%s: CLOSED → OPEN after %d failures",
                self.config.name,
                self.failure_count,
            )

    def _should_attempt_reset(self) -> bool:
        if not self.opened_at:
            return False
        return (datetime.now() - self.opened_at).total_seconds() >= self.config.recovery_timeout

    def _time_until_retry(self) -> int:
        if not self.opened_at:
            return self.config.recovery_timeout
        elapsed = (datetime.now() - self.opened_at).total_seconds()
        return max(int(self.config.recovery_timeout - elapsed), 0)

    def get_state(self) -> Dict[str, Any]:
        return {
            "state": self.state.value,
            "failure_count": self.failure_count,
            "success_count": self.success_count,
            "last_failure_time": self.last_failure_time.isoformat() if self.last_failure_time else None,
            "opened_at": self.opened_at.isoformat() if self.opened_at else None,
            "time_until_retry": self._time_until_retry() if self.state == CircuitState.OPEN else 0,
        }


class CircuitBreakerOpenException(Exception):
    pass


def circuit_breaker(config: Optional[CircuitBreakerConfig] = None):
    """Decorator that wraps a function with circuit breaker protection."""
    if config is None:
        config = CircuitBreakerConfig()

    breaker = CircuitBreaker(config)

    def decorator(func: Callable) -> Callable:
        @wraps(func)
        def wrapper(*args, **kwargs):
            return breaker.call(func, *args, **kwargs)

        wrapper.circuit_breaker = breaker
        wrapper.get_state = breaker.get_state
        return wrapper

    return decorator
