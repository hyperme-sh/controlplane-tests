"""Each control-plane consumer owns its Service Bus subscription."""

from __future__ import annotations

from api.core.config import settings
from hypermesh_service.payment_succeeded import (
    HYPERMESH_PAYMENT_GROUP,
    resolve_payment_consumer,
)

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
        resolve_payment_consumer(
            store=object(),
            service_bus_connection_string=_CONNECTION,
            database_url="postgresql://localhost/controlplane",
        ),
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
