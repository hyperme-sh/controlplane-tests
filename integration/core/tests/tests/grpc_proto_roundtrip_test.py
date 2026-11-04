"""gRPC/proto: generation smoke, pb2 round-trip, binary vs JSON (Phase 2 Step 4)."""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import uuid
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]


def test_gen_proto_script_exits_zero() -> None:
    script = REPO_ROOT / "scripts" / "gen_proto.py"
    assert script.is_file()
    r = subprocess.run(
        [sys.executable, str(script)],
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    assert r.returncode == 0, r.stderr + r.stdout


def test_contractecho_proto_exists() -> None:
    p = REPO_ROOT / "proto" / "spanopticon" / "v1" / "contractecho.proto"
    assert p.is_file()
    assert "message UserWire" in p.read_text()


def test_shared_user_pb2_roundtrip() -> None:
    from spanopticon_models.grpc.pb2_convert import shared_user_from_pb2, shared_user_to_pb2
    from spanopticon_models.user import SharedUser

    u = SharedUser(
        id=uuid.uuid4(),
        email="a@b.com",
        display_name="Test",
        tenant_id=None,
    )
    pb = shared_user_to_pb2(u)
    raw = pb.SerializeToString()
    u2 = shared_user_from_pb2(type(pb).FromString(raw))
    assert u2.id == u.id
    assert u2.email == u.email
    assert u2.display_name == u.display_name
    assert u2.tenant_id is None


def test_listing_pb2_roundtrip() -> None:
    from spanopticon_models.grpc.pb2_convert import listing_from_pb2, listing_to_pb2
    from spanopticon_models.marketplace import Listing, ListingType, PriceType

    lid = uuid.uuid4()
    tid = uuid.uuid4()
    sid = uuid.uuid4()
    l = Listing(
        id=lid,
        tenant_id=tid,
        seller_id=sid,
        title="Item",
        description="Desc",
        type=ListingType.PLUGIN,
        price_type=PriceType.ONE_TIME,
        price_amount=1234,
        sku="SKU-1",
        metadata={"k": "v"},
    )
    pb = listing_to_pb2(l)
    l2 = listing_from_pb2(type(pb).FromString(pb.SerializeToString()))
    assert l2.id == l.id
    assert l2.title == l.title
    assert l2.price_amount == l.price_amount
    assert l2.sku == l.sku
    assert l2.metadata == l.metadata
    assert l2.tenant_id == l.tenant_id


def test_token_usage_pb2_roundtrip() -> None:
    from spanopticon_models.ai_metering import TokenUsage
    from spanopticon_models.grpc.pb2_convert import token_usage_from_pb2, token_usage_to_pb2

    uid = uuid.uuid4()
    tenant_id = uuid.uuid4()
    t = TokenUsage(
        user_id=uid,
        tenant_id=tenant_id,
        model="gpt-4",
        input_tokens=10,
        output_tokens=20,
        cost=Decimal("0.001"),
        timestamp=datetime(2025, 1, 2, 3, 4, 5, tzinfo=UTC),
    )
    pb = token_usage_to_pb2(t)
    t2 = token_usage_from_pb2(type(pb).FromString(pb.SerializeToString()))
    assert t2.user_id == t.user_id
    assert t2.tenant_id == t.tenant_id
    assert t2.cost == t.cost
    assert t2.timestamp == t.timestamp


def test_proto_not_larger_than_json_for_listing() -> None:
    from spanopticon_models.grpc.pb2_convert import listing_to_pb2
    from spanopticon_models.marketplace import Listing, ListingType, PriceType

    tid = uuid.uuid4()
    l = Listing(
        id=uuid.uuid4(),
        tenant_id=tid,
        seller_id=uuid.uuid4(),
        title="A" * 200,
        description="B" * 500,
        type=ListingType.HARDWARE,
        price_type=PriceType.ONE_TIME,
        price_amount=9999,
        sku="X" * 50,
        metadata={f"k{i}": f"v{i}" for i in range(20)},
    )
    pb = listing_to_pb2(l)
    pb_bytes = pb.SerializeToString()
    d = l.model_dump(mode="json")
    json_bytes = json.dumps(d, default=str).encode("utf-8")
    assert len(pb_bytes) <= len(json_bytes)


@pytest.mark.skipif(not shutil.which("buf"), reason="buf not on PATH")
def test_buf_lint_proto() -> None:
    r = subprocess.run(
        ["buf", "lint", str(REPO_ROOT / "proto")],
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert r.returncode == 0, r.stderr + r.stdout
