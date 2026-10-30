"""Postgres events: free agent listing purchase → entitlement → metered inference.

The HTTP purchase writes the business row. This test then calls the same consumer
handler directly so CI stays deterministic.
"""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, patch

import httpx
import pytest
from asgi_lifespan import LifespanManager
from sqlalchemy import create_engine, text

import api.core.config as api_config
from api.common.event_types import PURCHASE_COMPLETED_V1
from auth_service.deps import get_current_tenant, get_current_user
from auth_service.schemas import AuthMeResponse
from controlplane_events.envelope import ControlPlaneEventEnvelope
from controlplane_events.payloads.purchase_completed import PurchaseCompletedEvent

from ai_service.infrastructure.event_consumers import build_ai_event_consumers


@pytest.mark.integration
@pytest.mark.phase2_step2
@pytest.mark.asyncio
async def test_free_agent_listing_purchase_entitles_inference(
    migrated_postgres: str,
    db_async_url: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(api_config.settings, "database_url", db_async_url)
    monkeypatch.setattr(api_config.settings, "azure_service_bus_connection_string", None)
    monkeypatch.setattr(api_config.settings, "ai_upstream_base_url", "http://test-llm.invalid/v1")
    monkeypatch.setattr(api_config.settings, "acl_service_base_url", None)
    monkeypatch.setattr(api_config.settings, "ai_rate_limit_per_minute", 500)

    from ai_service.interfaces.api import routes as ai_routes
    from marketplace_service.interfaces.api import routes as mkt_routes

    async def _acl_allow(*_a: object, **_k: object) -> bool:
        return True

    monkeypatch.setattr(mkt_routes, "enforce_rbac_http", _acl_allow)
    monkeypatch.setattr(mkt_routes, "enforce_acl_http", _acl_allow)
    monkeypatch.setattr(ai_routes, "enforce_rbac_http", _acl_allow)
    monkeypatch.setattr(ai_routes, "enforce_acl_http", _acl_allow)

    ta = uuid.uuid4()
    actor_id = uuid.UUID("11111111-1111-1111-1111-111111111111")
    eng = create_engine(migrated_postgres)
    with eng.begin() as cx:
        cx.execute(
            text("INSERT INTO tenants (id, name, is_active) VALUES (:id, :n, true)"),
            {"id": ta, "n": f"e2e-mkt-{ta.hex[:8]}"},
        )
        cx.execute(
            text(
                "INSERT INTO users (id, tenant_id, first_name, last_name, email, gender, is_active) "
                "VALUES (:id, :tid, 'E', 'Two', 'e2e-mkt@test.com', 'unspecified', true)"
            ),
            {"id": actor_id, "tid": ta},
        )

    from ai_service.app import create_app as create_ai_app
    from marketplace_service.app import create_app as create_mkt_app

    ai_app = create_ai_app(service_name="ai-service")
    mkt_app = create_mkt_app(service_name="marketplace-service")

    async def _user() -> AuthMeResponse:
        return AuthMeResponse(
            sub=str(actor_id),
            email="e2e-mkt@test.com",
            tenant_id=ta,
            roles=[],
            scopes=[],
        )

    async def _tenant() -> uuid.UUID:
        return ta

    for app in (mkt_app, ai_app):
        app.dependency_overrides[get_current_user] = _user
        app.dependency_overrides[get_current_tenant] = _tenant

    headers = {"Authorization": "Bearer test-token", "X-Tenant-ID": str(ta)}

    async with LifespanManager(ai_app):
        async with LifespanManager(mkt_app):
            transport_m = httpx.ASGITransport(app=mkt_app)
            transport_a = httpx.ASGITransport(app=ai_app)
            async with httpx.AsyncClient(transport=transport_m, base_url="http://mkt") as mcli:
                lr = await mcli.post(
                    "/api/v1/marketplace/listings",
                    headers=headers,
                    json={
                        "title": "Test agent",
                        "description": "",
                        "listing_type": "agent",
                        "price_type": "free",
                        "metadata": {},
                        "is_public": False,
                    },
                )
                assert lr.status_code == 201, lr.text
                lid = lr.json()["id"]
                pr = await mcli.post(
                    f"/api/v1/marketplace/listings/{lid}/purchase",
                    headers=headers,
                    json={},
                )
                assert pr.status_code == 201, pr.text
                pur = pr.json()

            cons = build_ai_event_consumers(db_async_url)
            env = ControlPlaneEventEnvelope(
                event_type=PURCHASE_COMPLETED_V1,
                service="marketplace-service",
                tenant_id=ta,
                payload=PurchaseCompletedEvent(
                    purchase_id=uuid.UUID(str(pur["id"])),
                    listing_id=uuid.UUID(str(lid)),
                    buyer_id=actor_id,
                    tenant_id=ta,
                    license_key=pur["license_key"],
                ).to_envelope_payload(),
            )
            await cons.handle_message(
                key=None,
                value=env.model_dump_json().encode("utf-8"),
                headers={},
            )

            with patch(
                "ai_service.interfaces.api.routes.chat_completion",
                new=AsyncMock(return_value=("ok-from-e2e", 3, 4)),
            ):
                async with httpx.AsyncClient(transport=transport_a, base_url="http://ai") as acli:
                    ir = await acli.post(
                        "/api/v1/ai/inference",
                        headers=headers,
                        json={
                            "prompt": "hello",
                            "model": "test-model",
                            "billing_mode": "none",
                            "purchase_id": pur["id"],
                            "license_key": pur["license_key"],
                        },
                    )
                    assert ir.status_code == 200, ir.text
                    assert ir.json()["text"] == "ok-from-e2e"

    for app in (mkt_app, ai_app):
        app.dependency_overrides.pop(get_current_user, None)
        app.dependency_overrides.pop(get_current_tenant, None)
