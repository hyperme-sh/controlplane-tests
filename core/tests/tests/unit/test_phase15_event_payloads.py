"""Phase 1.5 marketplace / AI Kafka payload contracts."""

from __future__ import annotations

import uuid
from decimal import Decimal

from controlplane_events.payloads import (
    BudgetDepletedEvent,
    ListingCreatedEvent,
    PurchaseCompletedEvent,
    TokensUsedEvent,
)
from controlplane_events.topics import (
    BUDGET_DEPLETED_V1,
    LISTING_CREATED_V1,
    PURCHASE_COMPLETED_V1,
    TOKENS_USED_V1,
)


def test_listing_created_envelope_and_roundtrip():
    tid = uuid.uuid4()
    e = ListingCreatedEvent(
        listing_id=uuid.uuid4(),
        tenant_id=tid,
        seller_id=uuid.uuid4(),
        title="Listing",
        type="plugin",
    )
    env = e.wrap_envelope(service="marketplace-service", tenant_uuid=tid)
    assert env.event_type == LISTING_CREATED_V1
    assert env.service == "marketplace-service"
    e2 = ListingCreatedEvent.from_envelope_payload(env.payload)
    assert e2.title == "Listing"


def test_purchase_completed_envelope():
    tid = uuid.uuid4()
    e = PurchaseCompletedEvent(
        purchase_id=uuid.uuid4(),
        listing_id=uuid.uuid4(),
        buyer_id=uuid.uuid4(),
        tenant_id=tid,
        license_key="k1",
    )
    env = e.wrap_envelope(service="marketplace-service", tenant_uuid=tid)
    assert env.event_type == PURCHASE_COMPLETED_V1


def test_tokens_used_envelope_json():
    tid = uuid.uuid4()
    e = TokensUsedEvent(
        user_id=uuid.uuid4(),
        tenant_id=tid,
        model="claude-3",
        input_tokens=10,
        output_tokens=20,
        cost_usd=Decimal("0.001"),
    )
    payload = e.to_envelope_payload()
    e2 = TokensUsedEvent.from_envelope_payload(payload)
    assert e2.cost_usd == Decimal("0.001")
    env = e.wrap_envelope(service="ai-service", tenant_uuid=tid)
    assert env.event_type == TOKENS_USED_V1


def test_budget_depleted_envelope():
    tid = uuid.uuid4()
    e = BudgetDepletedEvent(
        budget_id=uuid.uuid4(),
        tenant_id=tid,
        user_id=uuid.uuid4(),
        spent_cents=1000,
        ceiling_cents=1000,
    )
    env = e.wrap_envelope(service="ai-service", tenant_uuid=tid)
    assert env.event_type == BUDGET_DEPLETED_V1
