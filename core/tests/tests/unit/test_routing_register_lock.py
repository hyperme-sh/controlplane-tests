"""Registration lock timeout is a warning, not a traceback, and retries next poll."""

from __future__ import annotations

import logging

import pytest
from pydantic import BaseModel

from controlplane_events.routing_consumer import RoutingControlPlaneConsumer


class _Payload(BaseModel):
    pass


async def _handler(_envelope, _payload: _Payload) -> None:
    return None


class _Conn:
    def __init__(self, exc: BaseException) -> None:
        self._exc = exc

    async def execute(self, *_args, **_kwargs):
        raise self._exc


class _Begin:
    def __init__(self, exc: BaseException) -> None:
        self._exc = exc

    async def __aenter__(self) -> _Conn:
        return _Conn(self._exc)

    async def __aexit__(self, *_args) -> bool:
        return False


class _Engine:
    def __init__(self, exc: BaseException) -> None:
        self._exc = exc

    def begin(self) -> _Begin:
        return _Begin(self._exc)


def _consumer() -> RoutingControlPlaneConsumer:
    return RoutingControlPlaneConsumer(
        "postgresql://localhost/controlplane",
        ["controlplane.events.v1"],
        "test-group",
        {"test.event.v1": (_Payload, _handler)},
    )


class _LockTimeout(Exception):
    sqlstate = "55P03"


@pytest.mark.asyncio
async def test_registration_lock_timeout_warns_and_skips_claim(caplog) -> None:
    consumer = _consumer()
    consumer._engine = _Engine(_LockTimeout("canceling statement due to lock timeout"))
    claimed = False

    async def _claim(*_args, **_kwargs):
        nonlocal claimed
        claimed = True
        return []

    consumer._claim = _claim
    with caplog.at_level(logging.WARNING, logger="controlplane_events.routing_consumer"):
        result = await consumer.poll_once()
    assert result == 0
    assert claimed is False
    assert consumer._registered is False
    warnings = [record for record in caplog.records if record.levelno == logging.WARNING]
    assert len(warnings) == 1
    assert warnings[0].message == "registration lock busy; retrying next poll"
    assert warnings[0].exc_info is None


@pytest.mark.asyncio
async def test_registration_other_errors_still_raise() -> None:
    consumer = _consumer()
    consumer._engine = _Engine(RuntimeError("permission denied for table controlplane_events"))
    with pytest.raises(RuntimeError, match="permission denied"):
        await consumer.poll_once()
    assert consumer._registered is False
