import asyncio
import datetime
import logging
import random
from typing import Any, Awaitable, Callable, Dict

from aiobreaker import CircuitBreaker, CircuitBreakerError
from aiolimiter import AsyncLimiter
from opentelemetry import metrics, trace

logger = logging.getLogger(__name__)

tracer = trace.get_tracer("resilient-client")
meter = metrics.get_meter("resilient-client")

request_counter = meter.create_counter(
    "resilient_client_requests",
    description="Number of resilient client requests",
)
failure_counter = meter.create_counter(
    "resilient_client_failures",
    description="Number of resilient client failures",
)

ResilientFn = Callable[[], Awaitable[Any]]


class RetryPolicy:
    def __init__(self, retries: int = 3, base_delay: float = 0.2):
        self.retries = retries
        self.base_delay = base_delay

    async def run(self, fn: ResilientFn) -> Any:
        last_exc: Exception | None = None

        for attempt in range(self.retries):
            try:
                return await fn()
            except Exception as exc:
                last_exc = exc
                delay = self.base_delay * (2 ** attempt)
                delay += random.uniform(0, delay * 0.1)
                logger.debug(
                    "Retry attempt %d/%d failed (%.3fs backoff): %s",
                    attempt + 1,
                    self.retries,
                    delay,
                    exc,
                )
                await asyncio.sleep(delay)

        if last_exc is None:
            raise RuntimeError("Retry exhausted but no exception was captured")
        raise last_exc


class ResilientClient:
    def __init__(
        self,
        rate_limit: int,
        per_seconds: float,
        fail_max: int = 5,
        reset_timeout: float = 30.0,
        retries: int = 3,
        base_delay: float = 0.2,
    ):
        self.limiter = AsyncLimiter(rate_limit, per_seconds)
        self.circuit_breaker = CircuitBreaker(
            fail_max=fail_max,
            timeout_duration=datetime.timedelta(seconds=reset_timeout),
            name="resilient_client",
        )
        self.retry = RetryPolicy(retries=retries, base_delay=base_delay)

    async def call(self, operation_name: str, fn: ResilientFn) -> Any:
        with tracer.start_as_current_span(operation_name) as span:
            request_counter.add(1, {"op": operation_name})

            async def guarded_call() -> Any:
                async with self.limiter:
                    return await fn()

            # Retry is the inner layer: transient failures are retried before
            # the circuit breaker counts a failure. The circuit breaker wraps
            # the entire retry sequence and only opens after all retries fail.
            async def retriable() -> Any:
                return await self.retry.run(guarded_call)

            try:
                result = await self.circuit_breaker.call_async(retriable)
                span.set_attribute("status", "success")
                logger.debug("Resilient call succeeded: %s", operation_name)
                return result

            except CircuitBreakerError as exc:
                failure_counter.add(1, {"op": operation_name, "reason": "circuit_open"})
                span.record_exception(exc)
                span.set_attribute("status", "failure")
                logger.exception("Resilient call failed — circuit breaker open: %s", operation_name)
                if exc.__cause__ is not None:
                    raise exc.__cause__ from exc
                raise RuntimeError("Circuit breaker is OPEN") from exc

            except Exception as exc:
                failure_counter.add(1, {"op": operation_name})
                span.record_exception(exc)
                span.set_attribute("status", "failure")
                logger.exception("Resilient call failed: %s", operation_name)
                raise
