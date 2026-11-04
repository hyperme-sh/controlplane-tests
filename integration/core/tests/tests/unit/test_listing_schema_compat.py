"""Phase 2: v1 vs v2-style compatibility for shared Listing (additive optional fields)."""

from __future__ import annotations

import uuid

from panopticon_models.marketplace import Listing, ListingType, PriceType


def test_v1_listing_payload_without_sku_validates():
    """Older payloads without ``sku`` still parse after optional deprecated field is added."""
    tid = uuid.uuid4()
    sid = uuid.uuid4()
    lid = uuid.uuid4()
    payload = {
        "id": str(lid),
        "tenant_id": str(tid),
        "seller_id": str(sid),
        "title": "Plugin",
        "description": "",
        "type": "plugin",
        "price_type": "free",
        "price_amount": None,
        "metadata": {},
    }
    obj = Listing.model_validate(payload)
    assert obj.sku is None


def test_listing_with_sku_roundtrip():
    lid = uuid.uuid4()
    tid = uuid.uuid4()
    sid = uuid.uuid4()
    l = Listing(
        id=lid,
        tenant_id=tid,
        seller_id=sid,
        title="x",
        description="",
        type=ListingType.PLUGIN,
        price_type=PriceType.FREE,
        price_amount=None,
        metadata={},
        sku="SKU-1",
    )
    l2 = Listing.model_validate_json(l.model_dump_json())
    assert l2.sku == "SKU-1"
