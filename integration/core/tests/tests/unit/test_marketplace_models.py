"""Unit tests for marketplace Pydantic contracts."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

import pytest

from panopticon_models.marketplace import (
    Listing,
    ListingType,
    PriceType,
    Purchase,
    PurchaseStatus,
    Review,
)


def test_listing_free_roundtrip_json():
    lid = uuid.uuid4()
    tid = uuid.uuid4()
    sid = uuid.uuid4()
    l = Listing(
        id=lid,
        tenant_id=tid,
        seller_id=sid,
        title="My Plugin",
        description="Does things",
        type=ListingType.PLUGIN,
        price_type=PriceType.FREE,
        price_amount=None,
        metadata={"k": "v"},
    )
    raw = l.model_dump_json()
    l2 = Listing.model_validate_json(raw)
    assert l2 == l


def test_listing_paid_requires_positive_amount():
    with pytest.raises(ValueError):
        Listing(
            id=uuid.uuid4(),
            tenant_id=uuid.uuid4(),
            seller_id=uuid.uuid4(),
            title="x",
            type=ListingType.AGENT,
            price_type=PriceType.ONE_TIME,
            price_amount=None,
        )


def test_listing_free_rejects_nonzero_price():
    with pytest.raises(ValueError):
        Listing(
            id=uuid.uuid4(),
            tenant_id=uuid.uuid4(),
            seller_id=uuid.uuid4(),
            title="x",
            type=ListingType.CONFIG,
            price_type=PriceType.FREE,
            price_amount=100,
        )


def test_purchase_roundtrip():
    p = Purchase(
        id=uuid.uuid4(),
        listing_id=uuid.uuid4(),
        buyer_id=uuid.uuid4(),
        tenant_id=uuid.uuid4(),
        status=PurchaseStatus.COMPLETED,
        license_key="lic-abc",
    )
    p2 = Purchase.model_validate_json(p.model_dump_json())
    assert p2 == p


def test_review_requires_aware_datetime():
    rid = uuid.uuid4()
    tid = uuid.uuid4()
    lid = uuid.uuid4()
    rid_user = uuid.uuid4()
    with pytest.raises(ValueError):
        Review(
            id=rid,
            tenant_id=tid,
            listing_id=lid,
            reviewer_id=rid_user,
            rating=5,
            text="great",
            created_at=datetime(2024, 1, 1, 0, 0, 0),
        )

def test_review_accepts_utc():
    r = Review(
        id=uuid.uuid4(),
        tenant_id=uuid.uuid4(),
        listing_id=uuid.uuid4(),
        reviewer_id=uuid.uuid4(),
        rating=4,
        text="ok",
        created_at=datetime.now(timezone.utc),
    )
    assert r.rating == 4
