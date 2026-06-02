import asyncio

import pytest

from app.services.resilient_client import ResilientClient, RetryPolicy


def test_retry_policy_retries_until_exhausted():
    attempts = []

    async def failing_operation():
        attempts.append(1)
        raise RuntimeError("boom")

    with pytest.raises(RuntimeError, match="boom"):
        asyncio.run(RetryPolicy(retries=2, base_delay=0.01).run(failing_operation))

    assert len(attempts) == 2


def test_resilient_client_success_through_limiter():
    client = ResilientClient(rate_limit=5, per_seconds=1, fail_max=2, reset_timeout=5, retries=1, base_delay=0.01)

    async def ok_operation():
        return "ok"

    assert asyncio.run(client.call("test_ok", ok_operation)) == "ok"


def test_resilient_client_opens_circuit_after_failures():
    client = ResilientClient(rate_limit=1, per_seconds=1, fail_max=1, reset_timeout=5, retries=1, base_delay=0.01)

    async def failing_operation():
        raise RuntimeError("boom")

    with pytest.raises(RuntimeError, match="boom"):
        asyncio.run(client.call("test_fail", failing_operation))

    with pytest.raises(RuntimeError, match="Circuit breaker is OPEN"):
        asyncio.run(client.call("test_fail", failing_operation))
