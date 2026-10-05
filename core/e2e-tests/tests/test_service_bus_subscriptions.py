"""Each control-plane consumer owns its Service Bus subscription."""

from __future__ import annotations

from api.core.config import settings
from api.infrastructure.messaging.tenant_event_publisher import AzureServiceBusReceiver, ServiceBusRoutingConsumer
from controlplane_events.contracts import event_contracts, validate_event_envelope
from controlplane_events.payloads.payment_events import PaymentSucceededPayload

HYPERMESH_PAYMENT_GROUP = event_contracts()['consumer_groups']['hypermesh.payment.succeeded']


def contract_payment_consumer(receiver=None):
    async def receive(envelope, payload):
        validate_event_envelope(envelope)
    receiver = receiver or AzureServiceBusReceiver(_CONNECTION, event_contracts()['topic'], HYPERMESH_PAYMENT_GROUP)
    return ServiceBusRoutingConsumer(receiver, {'payment.succeeded.v1': (PaymentSucceededPayload, receive)})

from acl_service.infrastructure.user_created_consumer import (
    ACL_USER_CREATED_GROUP,
    build_user_created_consumer,
)
from ai_service.infrastructure.event_consumers import (
    AI_SERVICE_EVENTS_GROUP,
    build_ai_event_consumers,
)
from marketplace_service.infrastructure.user_created_consumer import (
    MARKETPLACE_USER_CREATED_GROUP,
    build_user_created_consumer as build_marketplace_consumer,
)
from user_service.infrastructure.payment_succeeded_consumer import (
    USER_PAYMENT_GROUP,
    build_payment_succeeded_consumer,
)

_CONNECTION = (
    "Endpoint=sb://example.servicebus.windows.net/;"
    "SharedAccessKeyName=k;SharedAccessKey=secret"
)


def test_five_consumers_resolve_distinct_subscriptions(monkeypatch) -> None:
    monkeypatch.setattr(settings, "azure_service_bus_connection_string", _CONNECTION)
    monkeypatch.setattr(settings, "azure_service_bus_subscription", "shared-sub")

    consumers = [
        build_user_created_consumer(object(), "postgresql://localhost/controlplane"),
        build_marketplace_consumer("postgresql://localhost/controlplane"),
        build_payment_succeeded_consumer("postgresql://localhost/controlplane"),
        build_ai_event_consumers("postgresql://localhost/controlplane"),
        contract_payment_consumer(),
    ]
    names = [consumer.subscription for consumer in consumers]
    assert names == [
        ACL_USER_CREATED_GROUP,
        MARKETPLACE_USER_CREATED_GROUP,
        USER_PAYMENT_GROUP,
        AI_SERVICE_EVENTS_GROUP,
        HYPERMESH_PAYMENT_GROUP,
    ]
    assert len(set(names)) == 5
    assert "shared-sub" not in names


async def test_payment_subscription_accepts_the_shared_wire_contract():
    import uuid
    from api.infrastructure.messaging.tenant_event_publisher import MemoryServiceBusReceiver
    from controlplane_events.envelope import ControlPlaneEventEnvelope
    receiver = MemoryServiceBusReceiver()
    tenant_id = uuid.uuid4()
    envelope = ControlPlaneEventEnvelope(
        service='payment-service', event_type='payment.succeeded.v1', tenant_id=tenant_id,
        payload={'tenant_id':str(tenant_id), 'billing_record_id':str(uuid.uuid4())},
    )
    receiver.enqueue(envelope.model_dump_json())
    assert await contract_payment_consumer(receiver).drain() == 1
    assert receiver.completed == [envelope.model_dump_json()]
    assert receiver.abandoned == []
