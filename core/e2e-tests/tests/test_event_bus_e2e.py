"""Multi-service + Kafka: user.created → ACL policy; payment.succeeded → tenant upgrade."""

from __future__ import annotations

import asyncio
import uuid
from types import SimpleNamespace
from unittest.mock import patch

import httpx
import pytest
from asgi_lifespan import LifespanManager
from sqlalchemy import create_engine, text

import api.core.config as api_config
from auth_service.deps import get_current_tenant, get_current_user
from auth_service.schemas import AuthMeResponse


@pytest.mark.integration
@pytest.mark.asyncio
async def test_user_created_acl_policy_and_payment_upgrades_tenant(
    migrated_postgres: str,
    db_async_url: str,
    kafka_bootstrap: str,
    kafka_topic_created: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(api_config.settings, "database_url", db_async_url)
    monkeypatch.setattr(api_config.settings, "kafka_bootstrap_servers", kafka_bootstrap)
    monkeypatch.setattr(api_config.settings, "kafka_events_topic", kafka_topic_created)

    ta = uuid.uuid4()
    ra = uuid.uuid4()
    eng = create_engine(migrated_postgres)
    with eng.begin() as cx:
        cx.execute(
            text("INSERT INTO tenants (id, name, is_active) VALUES (:id, :n, true)"),
            {"id": ta, "n": f"e2e-{ta.hex[:8]}"},
        )
        cx.execute(
            text(
                "INSERT INTO roles (id, tenant_id, name, permissions) "
                "VALUES (:id, :tid, 'member', '[]'::jsonb)"
            ),
            {"id": ra, "tid": ta},
        )

    from acl_service.app import create_app as create_acl_app
    from payment_service.app import create_app as create_payment_app
    from user_service.app import create_app as create_user_app

    user_app = create_user_app(service_name="user-service")
    acl_app = create_acl_app(service_name="acl-service")
    pay_app = create_payment_app(service_name="payment-service")

    async def _user() -> AuthMeResponse:
        return AuthMeResponse(
            sub="11111111-1111-1111-1111-111111111111",
            email="e2e@example.com",
            tenant_id=ta,
            roles=["admin"],
            scopes=[],
        )

    async def _tenant() -> uuid.UUID:
        return ta

    for app in (user_app, acl_app, pay_app):
        app.dependency_overrides[get_current_user] = _user
        app.dependency_overrides[get_current_tenant] = _tenant

    br_id = uuid.uuid4()
    with eng.begin() as cx:
        cx.execute(
            text(
                """
                INSERT INTO billing_records (
                    id, tenant_id, scope, actor, payment_type, currency, status,
                    stripe_session_id, metadata
                )
                VALUES (
                    :id, :tid, 'checkout', 'user', 'stripe_checkout', 'usd', 'pending',
                    'cs_e2e_session', '{}'::jsonb
                )
                """
            ),
            {"id": br_id, "tid": ta},
        )

    obj = SimpleNamespace(
        id="cs_e2e_session",
        metadata={"tenant_id": str(ta)},
        amount_total=1999,
        currency="usd",
        payment_intent="pi_e2e_1",
    )
    ev = SimpleNamespace(
        type="checkout.session.completed",
        data=SimpleNamespace(object=obj),
    )

    headers = {"Authorization": "Bearer test", "X-Tenant-ID": str(ta)}

    async with LifespanManager(user_app):
        async with LifespanManager(acl_app):
            async with LifespanManager(pay_app):
                transport_u = httpx.ASGITransport(app=user_app)
                transport_a = httpx.ASGITransport(app=acl_app)
                transport_p = httpx.ASGITransport(app=pay_app)
                async with httpx.AsyncClient(
                    transport=transport_u, base_url="http://user"
                ) as ucli:
                    ur = await ucli.post(
                        "/api/v1/users",
                        headers=headers,
                        json={
                            "email": f"e2e-{uuid.uuid4().hex[:8]}@example.com",
                            "first_name": "E",
                            "last_name": "Two",
                            "gender": "unspecified",
                            "is_active": True,
                        },
                    )
                    assert ur.status_code == 201, ur.text
                    uid = ur.json()["id"]

                await asyncio.sleep(4)

                async with httpx.AsyncClient(
                    transport=transport_a, base_url="http://acl"
                ) as acli:
                    pr = await acli.get("/api/v1/acl/policies", headers=headers)
                    assert pr.status_code == 200, pr.text
                    policies = pr.json()
                    assert any(
                        p.get("ptype") == "p"
                        and p.get("v0") == uid
                        and p.get("v1") == "user_profile"
                        and p.get("v2") == "read"
                        for p in policies
                    ), policies

                with patch(
                    "payment_service.interfaces.api.routes._parse_webhook_event",
                    return_value=(ev, ta),
                ):
                    async with httpx.AsyncClient(
                        transport=transport_p, base_url="http://pay"
                    ) as pcli:
                        wr = await pcli.post(
                            "/api/v1/payments/stripe/webhook",
                            content=b"{}",
                            headers={"stripe-signature": "test_sig"},
                        )
                        assert wr.status_code == 200, wr.text

                await asyncio.sleep(4)

    with eng.begin() as cx:
        cx.execute(
            text("SELECT set_config('app.current_tenant_id', :tid, true)"),
            {"tid": str(ta)},
        )
        sub_id = cx.execute(
            text("SELECT subscription_id FROM tenants WHERE id = CAST(:id AS uuid)"),
            {"id": str(ta)},
        ).scalar_one_or_none()
    assert sub_id is not None

    for app in (user_app, acl_app, pay_app):
        app.dependency_overrides.pop(get_current_user, None)
        app.dependency_overrides.pop(get_current_tenant, None)
