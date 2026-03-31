"""Unit tests for controlplane_events envelope parsing and tenant checks."""

from __future__ import annotations

import uuid

import pytest

from controlplane_events.envelope import ControlPlaneEventEnvelope
from controlplane_events.envelope_parse import (
    extract_payload_tenant_id,
    parse_envelope_bytes,
    payload_matches_envelope_tenant,
    tenant_ids_consistent,
)
from controlplane_events.payloads import PaymentSucceededPayload, UserCreatedEvent


def test_parse_envelope_bytes_roundtrip():
    tid = uuid.uuid4()
    env = ControlPlaneEventEnvelope(
        event_type="user.created.v1",
        service="user-service",
        tenant_id=tid,
        payload={"user_id": str(uuid.uuid4()), "email": "a@b.com", "tenant_id": str(tid)},
    )
    raw = env.model_dump_json().encode("utf-8")
    parsed = parse_envelope_bytes(raw)
    assert parsed is not None
    assert parsed.event_type == "user.created.v1"
    assert parsed.tenant_id == tid


def test_parse_envelope_bytes_invalid_returns_none():
    assert parse_envelope_bytes(None) is None
    assert parse_envelope_bytes(b"") is None
    assert parse_envelope_bytes(b"not json") is None


def test_tenant_ids_consistent_both_match():
    tid = uuid.uuid4()
    env = ControlPlaneEventEnvelope(
        event_type="x",
        service="s",
        tenant_id=tid,
        payload={},
    )
    assert tenant_ids_consistent(env, tid) is True


def test_tenant_ids_consistent_both_mismatch():
    tid = uuid.uuid4()
    env = ControlPlaneEventEnvelope(
        event_type="x",
        service="s",
        tenant_id=tid,
        payload={},
    )
    assert tenant_ids_consistent(env, uuid.uuid4()) is False


def test_tenant_ids_consistent_envelope_none_payload_some_rejected():
    env = ControlPlaneEventEnvelope(
        event_type="x",
        service="s",
        tenant_id=None,
        payload={},
    )
    assert tenant_ids_consistent(env, uuid.uuid4()) is False


def test_tenant_ids_consistent_envelope_only_ok():
    tid = uuid.uuid4()
    env = ControlPlaneEventEnvelope(
        event_type="x",
        service="s",
        tenant_id=tid,
        payload={},
    )
    assert tenant_ids_consistent(env, None) is True


def test_payload_matches_payment_succeeded():
    tid = uuid.uuid4()
    env = ControlPlaneEventEnvelope(
        event_type="payment.succeeded.v1",
        service="payment-service",
        tenant_id=tid,
        payload={},
    )
    p = PaymentSucceededPayload(
        tenant_id=tid,
        billing_record_id=uuid.uuid4(),
    )
    assert payload_matches_envelope_tenant(env, p) is True


def test_payload_mismatch_payment_succeeded():
    tid = uuid.uuid4()
    env = ControlPlaneEventEnvelope(
        event_type="payment.succeeded.v1",
        service="payment-service",
        tenant_id=tid,
        payload={},
    )
    p = PaymentSucceededPayload(
        tenant_id=uuid.uuid4(),
        billing_record_id=uuid.uuid4(),
    )
    assert payload_matches_envelope_tenant(env, p) is False


def test_user_created_extract_and_match():
    tid = uuid.uuid4()
    uc = UserCreatedEvent(
        user_id=uuid.uuid4(),
        email="u@example.com",
        tenant_id=str(tid),
    )
    assert extract_payload_tenant_id(uc) == tid
    env = ControlPlaneEventEnvelope(
        event_type="user.created.v1",
        service="user-service",
        tenant_id=tid,
        payload=uc.to_envelope_payload(),
    )
    assert payload_matches_envelope_tenant(env, uc) is True


@pytest.mark.asyncio
async def test_routing_consumer_dispatches():
    from controlplane_events.routing_consumer import RoutingControlPlaneConsumer

    tid = uuid.uuid4()
    calls: list[tuple] = []

    async def on_payment(env: ControlPlaneEventEnvelope, p: PaymentSucceededPayload) -> None:
        calls.append((env.event_type, p.tenant_id))

    # Consumer is not started — call handle_message directly
    consumer = RoutingControlPlaneConsumer(
        "localhost:9092",
        ["controlplane.events.v1"],
        "test-group",
        {"payment.succeeded.v1": (PaymentSucceededPayload, on_payment)},
    )
    env = ControlPlaneEventEnvelope(
        event_type="payment.succeeded.v1",
        service="payment-service",
        tenant_id=tid,
        payload=PaymentSucceededPayload(
            tenant_id=tid,
            billing_record_id=uuid.uuid4(),
        ).to_envelope_payload(),
    )
    await consumer.handle_message(
        key=None,
        value=env.model_dump_json().encode("utf-8"),
        headers={},
    )
    assert len(calls) == 1
    assert calls[0][0] == "payment.succeeded.v1"
    assert calls[0][1] == tid


@pytest.mark.asyncio
async def test_routing_consumer_skips_tenant_mismatch():
    from controlplane_events.routing_consumer import RoutingControlPlaneConsumer

    tid = uuid.uuid4()
    calls: list = []

    async def on_payment(_env: ControlPlaneEventEnvelope, _p: PaymentSucceededPayload) -> None:
        calls.append(1)

    consumer = RoutingControlPlaneConsumer(
        "localhost:9092",
        ["controlplane.events.v1"],
        "test-group",
        {"payment.succeeded.v1": (PaymentSucceededPayload, on_payment)},
    )
    env = ControlPlaneEventEnvelope(
        event_type="payment.succeeded.v1",
        service="payment-service",
        tenant_id=tid,
        payload=PaymentSucceededPayload(
            tenant_id=uuid.uuid4(),
            billing_record_id=uuid.uuid4(),
        ).to_envelope_payload(),
    )
    await consumer.handle_message(
        key=None,
        value=env.model_dump_json().encode("utf-8"),
        headers={},
    )
    assert calls == []
