"""Postgres-backed host registration through HTTP, across independent transactions."""

from __future__ import annotations

import asyncio
import base64
import hashlib
import importlib.util
import secrets
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from fastapi import FastAPI
from sqlalchemy import create_engine, func, select, text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from api.core.config import settings
from api.infrastructure.security.tenant_session_jwt import mint_tenant_session_jwt
from controlplane_db.models import (
    Group,
    Organization,
    Role,
    Tenant,
    User,
    UserGroupMembership,
)
from hypermesh_service.models import (
    HostOrganizationBinding,
    HypermeshDevice,
    HypermeshHardwareClass,
    HypermeshHostRegistration,
)
from user_service.models import OperatorBan
from hypermesh_service import host_registration as registration

pytestmark = pytest.mark.integration
BASE = "/api/v1/hypermesh/host-registrations"


@pytest.fixture
async def plane(async_engine, monkeypatch):
    maker = async_sessionmaker(async_engine, expire_on_commit=False, autoflush=False)
    monkeypatch.setattr(registration, "get_session_maker", lambda: maker)
    monkeypatch.setattr(settings, "api_key_exchange_jwt_secret", "registration-tests-secret-with-at-least-32-chars")
    monkeypatch.setattr(settings, "azure_service_bus_connection_string", None)
    app = FastAPI()
    app.include_router(registration.router, prefix="/api/v1")
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="https://plane.test") as client:
        yield client, maker


async def account(maker, *, tenant_id=None, membership_subject=True):
    tenant_id = tenant_id or uuid.uuid4()
    subject = str(uuid.uuid4())
    org_id, member_id, role_id, group_id = (uuid.uuid4() for _ in range(4))
    email = f"{subject}@example.com"
    async with maker() as s:
        if await s.get(Tenant, tenant_id) is None:
            s.add(Tenant(id=tenant_id, name=f"registration-{tenant_id}", is_active=True))
            await s.flush()
            s.add(HypermeshHardwareClass(
                id=uuid.uuid4(), tenant_id=tenant_id, class_id="agx-large",
                vendor="NVIDIA", sku="AGX", module="Orin", memory_gb=64,
                public_sku=False, path_b_suite_id="core", hourly_rate_cents=0,
            ))
        s.add(Organization(id=org_id, tenant_id=tenant_id, legal_name=f"Host {org_id}"))
        s.add(Role(id=role_id, tenant_id=tenant_id, name=f"role-{role_id}", permissions=[]))
        await s.flush()
        s.add(User(id=member_id, tenant_id=tenant_id, keycloak_sub=subject if membership_subject else None,
                   email=email, first_name="Host", last_name="Owner", gender="unspecified", is_active=True))
        # Multiple accounts in one tenant share the existing host group.
        group = await s.scalar(select(Group).where(Group.tenant_id == tenant_id, Group.name == "host"))
        if group:
            group_id = group.id
        else:
            s.add(Group(id=group_id, tenant_id=tenant_id, name="host", role_id=role_id))
        s.add(HostOrganizationBinding(tenant_id=tenant_id, keycloak_sub=subject, organization_id=org_id))
        await s.flush()
        s.add(UserGroupMembership(id=uuid.uuid4(), tenant_id=tenant_id, user_id=member_id, group_id=group_id))
        await s.commit()
    return {"tenant_id": tenant_id, "subject": subject, "org_id": org_id,
            "member_id": member_id, "email": email}


def headers(person, client="hypermesh-native"):
    token = mint_tenant_session_jwt(
        claims={"sub": person["subject"], "email": person["email"], "azp": client},
        tenant_id=person["tenant_id"],
    )
    return {"Authorization": f"Bearer {token}", "X-Tenant-ID": str(person["tenant_id"])}


def payload():
    secret = "hm_dev_" + secrets.token_urlsafe(24)
    return {
        "request_id": str(uuid.uuid4()), "host_name": "host-" + secrets.token_hex(6),
        "class_id": "agx-large", "wg_public_key": base64.b64encode(secrets.token_bytes(32)).decode(),
        "device_secret_hash": hashlib.sha256(secret.encode()).hexdigest(),
        "credential_prefix": secret.removeprefix("hm_dev_")[:8],
        "hardware": {"profile": "jetson-agx", "architecture": "arm64", "cpu": "ARM",
                     "gpu": "NVIDIA", "system_memory_bytes": 64 * 2**30, "model_memory_bytes": 32 * 2**30},
    }


async def pending(client, person, body):
    res = await client.post(BASE, headers=headers(person), json=body)
    assert res.status_code == 200, res.text
    assert res.json()["state"] == "pending"
    return f'{BASE}/{body["request_id"]}'


async def approved(client, person, body):
    path = await pending(client, person, body)
    res = await client.post(path + "/decision", headers=headers(person, "controlplane-frontend"),
                            json={"decision": "approve"})
    assert res.status_code == 200, res.text
    return path


async def test_concurrent_complete_and_response_loss_create_one_device_and_event(plane):
    client, maker = plane
    person = await account(maker)
    body = payload()
    path = await approved(client, person, body)
    responses = await asyncio.gather(*[
        client.post(path + "/complete", headers=headers(person)) for _ in range(6)
    ])
    assert all(r.status_code == 200 for r in responses), [r.text for r in responses]
    device_ids = {r.json()["device_id"] for r in responses}
    assert len(device_ids) == 1
    async with maker() as s:
        device = await s.get(HypermeshDevice, uuid.UUID(next(iter(device_ids))))
        assert device.host_org_id == person["org_id"]
        assert device.device_secret_hash == body["device_secret_hash"]
        assert device.wg_public_key == body["wg_public_key"]
        assert device.enrolled and device.sell_state == "paused"
        assert device.path_b_state == "never-run" and device.path_a["intake_result"] == "pending"
        assert not device.lab and device.enroll_token_hash is None
        count = await s.scalar(text("SELECT count(*) FROM controlplane_events WHERE envelope->>'correlation_id' = :id"),
                               {"id": body["request_id"]})
        assert count == 1
    # A fresh HTTP client models process/response loss and reauthentication.
    async with httpx.AsyncClient(transport=client._transport, base_url="https://plane.test") as restarted:
        retried = await restarted.post(BASE, headers=headers(person), json=body)
        assert retried.status_code == 200
        assert retried.json()["device_id"] in device_ids
        assert retried.json()["state"] == "completed"
        assert body["device_secret_hash"] not in retried.text
        assert body["wg_public_key"] not in retried.text
        assert retried.headers["cache-control"] == "no-store"


async def test_identity_and_client_binding_cannot_be_overridden(plane):
    client, maker = plane
    person = await account(maker)
    other = await account(maker, tenant_id=person["tenant_id"])
    body = payload()
    path = await pending(client, person, body)
    for action in ("", "/complete", "/decision"):
        method = client.get if not action else client.post
        kwargs = {"json": {"decision": "approve"}} if action == "/decision" else {}
        client_id = "controlplane-frontend" if action == "/decision" else "hypermesh-native"
        res = await method(path + action, headers=headers(other, client_id), **kwargs)
        assert res.status_code == 404
    res = await client.post(path + "/decision", headers=headers(person), json={"decision": "approve"})
    assert res.status_code == 403
    res = await client.post(path + "/complete", headers=headers(person, "controlplane-frontend"))
    assert res.status_code == 403
    res = await client.post(BASE, headers=headers(person), json={**body, "host_org_id": str(other["org_id"])})
    assert res.status_code == 422
    res = await client.get(path, headers={"X-Tenant-ID": str(person["tenant_id"])})
    assert res.status_code == 401
    res = await client.get(path, headers={**headers(person), "X-Tenant-ID": str(uuid.uuid4())})
    assert res.status_code in (400, 403)


async def test_request_is_immutable_and_unapproved_completion_fails(plane):
    client, maker = plane
    person = await account(maker)
    body = payload()
    path = await pending(client, person, body)
    assert (await client.post(path + "/complete", headers=headers(person))).status_code == 409
    for change in ({"host_name": "changed"}, {"wg_public_key": payload()["wg_public_key"]},
                   {"device_secret_hash": "ab" * 32}):
        res = await client.post(BASE, headers=headers(person), json={**body, **change})
        assert res.status_code == 409
    async with maker() as s:
        assert await s.scalar(select(func.count()).select_from(HypermeshDevice).where(
            HypermeshDevice.tenant_id == person["tenant_id"])) == 0


@pytest.mark.parametrize("terminal", ["denied", "expired"])
async def test_terminal_request_cannot_complete_or_be_reapproved(plane, terminal):
    client, maker = plane
    person = await account(maker)
    body = payload()
    path = await pending(client, person, body)
    if terminal == "denied":
        res = await client.post(path + "/decision", headers=headers(person, "controlplane-frontend"),
                                json={"decision": "deny"})
        assert res.status_code == 200
    else:
        async with maker() as s:
            row = await s.get(HypermeshHostRegistration, uuid.UUID(body["request_id"]))
            row.expires_at = datetime.now(UTC) - timedelta(seconds=1)
            await s.commit()
    assert (await client.get(path, headers=headers(person))).json()["state"] == terminal
    assert (await client.post(path + "/complete", headers=headers(person))).status_code == 409
    res = await client.post(path + "/decision", headers=headers(person, "controlplane-frontend"),
                            json={"decision": "approve"})
    assert res.status_code == 409


async def test_archived_device_cannot_be_revived_or_reuse_key(plane):
    client, maker = plane
    person = await account(maker)
    body = payload()
    path = await approved(client, person, body)
    first = await client.post(path + "/complete", headers=headers(person))
    assert first.status_code == 200
    async with maker() as s:
        device = await s.get(HypermeshDevice, uuid.UUID(first.json()["device_id"]))
        device.archived_at = datetime.now(UTC)
        device.enrolled = False
        device.device_secret_hash = None
        await s.commit()
    assert (await client.post(path + "/complete", headers=headers(person))).status_code == 410
    new = {**payload(), "host_name": body["host_name"], "wg_public_key": body["wg_public_key"]}
    new_path = await approved(client, person, new)
    assert (await client.post(new_path + "/complete", headers=headers(person))).status_code == 409
    fresh = {**payload(), "host_name": body["host_name"]}
    fresh_path = await approved(client, person, fresh)
    result = await client.post(fresh_path + "/complete", headers=headers(person))
    assert result.status_code == 200
    assert result.json()["device_id"] != first.json()["device_id"]


async def test_global_key_conflict_rolls_back_even_across_tenants(plane):
    client, maker = plane
    first, second = await account(maker), await account(maker)
    a = payload()
    b = {**payload(), "wg_public_key": a["wg_public_key"]}
    paths = [await approved(client, first, a), await approved(client, second, b)]
    replies = await asyncio.gather(*[
        client.post(path + "/complete", headers=headers(person))
        for path, person in zip(paths, (first, second))
    ])
    assert sorted(r.status_code for r in replies) == [200, 409]
    async with maker() as s:
        assert await s.scalar(select(func.count()).select_from(HypermeshDevice).where(
            HypermeshDevice.wg_public_key == a["wg_public_key"])) == 1
        rows = (await s.scalars(select(HypermeshHostRegistration).where(
            HypermeshHostRegistration.id.in_([uuid.UUID(a["request_id"]), uuid.UUID(b["request_id"])])))).all()
        assert sorted(row.state for row in rows) == ["approved", "completed"]


async def test_commit_failure_returns_retryable_error_without_partial_device(plane, monkeypatch):
    client, maker = plane
    person = await account(maker)
    body = payload()
    path = await approved(client, person, body)
    async def fail_commit(self):
        raise SQLAlchemyError("simulated commit failure")
    with monkeypatch.context() as patch:
        patch.setattr(AsyncSession, "commit", fail_commit)
        result = await client.post(path + "/complete", headers=headers(person))
        assert result.status_code == 503
    async with maker() as s:
        row = await s.get(HypermeshHostRegistration, uuid.UUID(body["request_id"]))
        assert row.state == "approved" and row.device_id is None
        assert await s.scalar(select(func.count()).select_from(HypermeshDevice).where(
            HypermeshDevice.tenant_id == person["tenant_id"])) == 0
    assert (await client.post(path + "/complete", headers=headers(person))).status_code == 200


async def test_membership_and_bans_are_rechecked_after_approval(plane):
    client, maker = plane
    person = await account(maker, membership_subject=False)
    body = payload()
    path = await approved(client, person, body)
    async with maker() as s:
        member = await s.get(User, person["member_id"])
        member.is_active = False
        await s.commit()
    assert (await client.post(path + "/complete", headers=headers(person))).status_code == 403
    async with maker() as s:
        member = await s.get(User, person["member_id"])
        member.is_active = True
        s.add(OperatorBan(id=uuid.uuid4(), tenant_id=person["tenant_id"], subject_kind="user",
                          subject_id=person["member_id"], reason="test ban", created_by="test",
                          created_at=datetime.now(UTC)))
        await s.commit()
    assert (await client.post(path + "/complete", headers=headers(person))).status_code == 403


def test_registration_migration_existing_schema_and_forced_rls(migrated_postgres):
    engine = create_engine(migrated_postgres)
    path = Path(__file__).resolve().parents[3] / "core/lib/controlplane-db/src/controlplane_db/alembic/versions/20261001_0052_host_registrations.py"
    spec = importlib.util.spec_from_file_location("registration_migration", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    with engine.connect() as cx:
        transaction = cx.begin()
        try:
            with Operations.context(MigrationContext.configure(cx)):
                # The subsequent onboarding table references registrations.
                successor_path = path.with_name("20261001_0053_onboarding_runs.py")
                successor_spec = importlib.util.spec_from_file_location("onboarding_migration", successor_path)
                successor = importlib.util.module_from_spec(successor_spec)
                successor_spec.loader.exec_module(successor)
                successor.downgrade()
                module.downgrade()
                module.upgrade()
                module.upgrade()  # current-metadata bootstrap is safe, too
                successor.upgrade()
                successor.upgrade()
            row = cx.execute(text("SELECT relrowsecurity, relforcerowsecurity FROM pg_class "
                                  "WHERE oid = 'hypermesh_host_registrations'::regclass")).one()
            assert row == (True, True)
            assert cx.scalar(text("SELECT count(*) FROM pg_policy "
                                  "WHERE polrelid = 'hypermesh_host_registrations'::regclass")) == 4
            tid, org, rid = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
            cx.execute(text("INSERT INTO tenants (id, name, is_active) VALUES (:id, :name, true)"),
                       {"id": tid, "name": f"rls-{tid}"})
            cx.execute(text("INSERT INTO organizations (id, tenant_id, legal_name) VALUES (:id, :tid, :name)"),
                       {"id": org, "tid": tid, "name": f"org-{org}"})
            cx.execute(text("INSERT INTO hypermesh_host_registrations "
                            "(id, tenant_id, owner_sub, host_org_id, request, state, created_at, expires_at) "
                            "VALUES (:id, :tid, 'owner', :org, '{}'::jsonb, 'pending', now(), now())"),
                       {"id": rid, "tid": tid, "org": org})
            role = "registration_rls_" + uuid.uuid4().hex
            cx.execute(text(f'CREATE ROLE "{role}" NOLOGIN'))
            cx.execute(text(f'GRANT SELECT ON hypermesh_host_registrations TO "{role}"'))
            cx.execute(text(f'SET LOCAL ROLE "{role}"'))
            cx.execute(text("SELECT set_config('app.current_tenant_id', '', true)"))
            assert cx.scalar(text("SELECT count(*) FROM hypermesh_host_registrations")) == 0
            cx.execute(text("SELECT set_config('app.current_tenant_id', :tid, true)"), {"tid": str(tid)})
            assert cx.scalar(text("SELECT count(*) FROM hypermesh_host_registrations")) == 1
            cx.execute(text("SELECT set_config('app.current_tenant_id', :tid, true)"), {"tid": str(uuid.uuid4())})
            assert cx.scalar(text("SELECT count(*) FROM hypermesh_host_registrations")) == 0
        finally:
            transaction.rollback()
    engine.dispose()


async def test_service_bus_outbox_retries_after_send_failure_and_restart(plane, monkeypatch):
    import json
    from api.infrastructure.messaging.tenant_event_publisher import (
        MemoryServiceBusSender, ServiceBusTenantEventPublisher,
    )
    from hypermesh_service.registration_events import forward_registration_events

    client, maker = plane
    monkeypatch.setattr(settings, "azure_service_bus_connection_string", "configured")
    person = await account(maker)
    body = payload()
    path = await approved(client, person, body)
    result = await client.post(path + "/complete", headers=headers(person))
    assert result.status_code == 200
    async with maker() as s:
        row = await s.get(HypermeshHostRegistration, uuid.UUID(body["request_id"]))
        assert row.enrollment_event["correlation_id"] == body["request_id"]
        assert row.event_forwarded_at is None

    class UncertainSender(MemoryServiceBusSender):
        async def send_json(self, **kwargs):
            await super().send_json(**kwargs)
            raise OSError("acknowledgment lost")

    failing = UncertainSender()
    publisher = ServiceBusTenantEventPublisher(failing, "controlplane.events.v1", "hypermesh-service")
    with pytest.raises(OSError):
        await forward_registration_events(maker, publisher, person["tenant_id"])
    async with maker() as s:
        row = await s.get(HypermeshHostRegistration, uuid.UUID(body["request_id"]))
        assert row.state == "completed" and row.event_forwarded_at is None

    sender = MemoryServiceBusSender()
    restarted = ServiceBusTenantEventPublisher(sender, "controlplane.events.v1", "hypermesh-service")
    counts = await asyncio.gather(*[
        forward_registration_events(maker, restarted, person["tenant_id"]) for _ in range(3)
    ])
    assert sum(counts) == 1 and len(sender.sent) == 1
    delivered = json.loads(sender.sent[0]["body"])
    uncertain = json.loads(failing.sent[0]["body"])
    assert delivered["event_type"] == "hypermesh.device.enrolled.v1"
    assert delivered["correlation_id"] == uncertain["correlation_id"] == body["request_id"]
    assert delivered["payload"]["device_id"] == result.json()["device_id"]
    assert body["device_secret_hash"] not in sender.sent[0]["body"]
    assert await forward_registration_events(maker, restarted, person["tenant_id"]) == 0


async def test_postgres_mode_does_not_queue_a_second_delivery(plane):
    from hypermesh_service.registration_events import forward_registration_events
    client, maker = plane
    person = await account(maker)
    body = payload()
    path = await approved(client, person, body)
    assert (await client.post(path + "/complete", headers=headers(person))).status_code == 200

    class RefusePublisher:
        async def publish(self, **kwargs):
            pytest.fail("Postgres enrollment event must not be published twice")

    assert await forward_registration_events(maker, RefusePublisher(), person["tenant_id"]) == 0


async def test_onboarding_certificate_is_atomic_immutable_and_owner_bound(plane, monkeypatch):
    from hypermesh_service.models import HypermeshCatalogEntry, HypermeshOnboardingRun
    from hypermesh_service import onboarding_certification as cert

    reg_client, maker = plane
    monkeypatch.setattr(cert, "get_session_maker", lambda: maker)
    person = await account(maker)
    body = payload()
    secret = "hm_dev_" + secrets.token_urlsafe(24)
    body["device_secret_hash"] = hashlib.sha256(secret.encode()).hexdigest()
    body["credential_prefix"] = secret[7:15]
    path = await approved(reg_client, person, body)
    completed = await reg_client.post(path + "/complete", headers=headers(person))
    assert completed.status_code == 200
    device_id = uuid.UUID(completed.json()["device_id"])
    program = {"suite_id":"pack-v1", "catalog_id":"model", "file_hash":"sha256:"+"a"*64,
               "context_tokens":4096, "decode_tok_s":10.0, "thermal_c":None}
    async def program_contract(*args, **kwargs):
        assert kwargs["tenant_id"] == person["tenant_id"]
        return program
    monkeypatch.setattr(cert, "certification_program", program_contract)
    async with maker() as session:
        device = await session.get(HypermeshDevice, device_id)
        device.path_a = {**device.path_a, "intake_result":"accepted"}
        session.add(HypermeshCatalogEntry(
            id=uuid.uuid4(), tenant_id=person["tenant_id"], catalog_id="model",
            artifact_name="reviewed", class_id="agx-large", file_hash=program["file_hash"],
            image_digest="registry.test/runtime@sha256:"+"b"*64,
            runtime="nvidia", format="gguf", advertised_context=4096, size_bytes=1234,
            onboarding_source_ref="bartowski/model@"+"a"*40+"/model.gguf",
            certified=True, per_1k_token_cents=0, loaded_hour_min_cents=0,
        ))
        await session.commit()
    app = FastAPI()
    app.include_router(cert.router, prefix="/api/v1")
    hdr = {"Authorization":"Bearer "+secret,"X-Tenant-ID":str(person["tenant_id"])}
    query = {"protocol_version":1, "request_id":body["request_id"],
             "device_id":str(device_id),"run_id":str(uuid.uuid4()),"nonce":secrets.token_hex(32)}
    prefix = "/api/v1/hypermesh/agent/onboarding/certification"
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url="https://plane.test") as client:
        plans = await asyncio.gather(*[client.post(prefix+"/plan",headers=hdr,json=query) for _ in range(3)])
        assert all(r.status_code==200 for r in plans), [r.text for r in plans]
        digest = plans[0].json()["manifest_sha256"]
        assert {r.json()["manifest_sha256"] for r in plans} == {digest}
        query["manifest_sha256"] = digest
        evidence = {"host_image_hash":"sha256:"+"c"*64, "file_hash":program["file_hash"],
            "image_digest":"registry.test/runtime@sha256:"+"b"*64,
            "sample_ms":100,"tokens_per_second":10.0,"thermal_c":None,
            "context_tokens":4096,"prompt_tokens":4080,"generated_tokens":16,
            "context_elapsed_ms":100,"cleanup_complete":True,"tunnel_verified":True}
        results = await asyncio.gather(*[client.post(prefix+"/result",headers=hdr,
            json={**query,"evidence":evidence}) for _ in range(3)])
        assert all(r.status_code==200 and r.json()["state"]=="passed" for r in results), [r.text for r in results]
        changed = await client.post(prefix+"/result",headers=hdr,json={**query,"evidence":{**evidence,"sample_ms":101}})
        assert changed.status_code==409
        approval={"run_id":query["run_id"],"manifest_sha256":digest}
        other = await account(maker, tenant_id=person["tenant_id"])
        denied = await client.post("/api/v1/hypermesh/host-onboarding/approve-activation",
                                   headers=headers(other),json=approval)
        assert denied.status_code==403
        assert (await client.post("/api/v1/hypermesh/host-onboarding/approve-activation",
                                  headers=headers(person),json=approval)).status_code==200
        permitted = await client.post(prefix+"/activation",headers=hdr,json=query)
        assert permitted.status_code==200, permitted.text
        assert permitted.json()["activation_allowed"] and not permitted.json()["selling_allowed"]
        async with maker() as session:
            device = await session.get(HypermeshDevice,device_id)
            assert device.path_b_state=="green" and device.sell_state=="paused"
            run = await session.get(HypermeshOnboardingRun,uuid.UUID(query["run_id"]))
            assert run.evidence==evidence and run.approved_by==person["subject"]
            device.schedule_hold=True
            await session.commit()
        assert (await client.post(prefix+"/activation",headers=hdr,json=query)).status_code==409

        # Exercise FORCE RLS using a non-owner role, not the superuser fixture.
        async with maker() as session:
            role = "onboarding_rls_" + uuid.uuid4().hex
            await session.execute(text(f'CREATE ROLE "{role}" NOLOGIN'))
            await session.execute(text(f'GRANT SELECT ON hypermesh_onboarding_runs TO "{role}"'))
            await session.execute(text(f'SET LOCAL ROLE "{role}"'))
            await session.execute(text("SELECT set_config('app.current_tenant_id', '', true)"))
            assert await session.scalar(select(func.count()).select_from(HypermeshOnboardingRun)) == 0
            await session.execute(text("SELECT set_config('app.current_tenant_id', :tid, true)"),
                                  {"tid": str(person["tenant_id"])})
            assert await session.scalar(select(func.count()).select_from(HypermeshOnboardingRun)) == 1
            await session.execute(text("SELECT set_config('app.current_tenant_id', :tid, true)"),
                                  {"tid": str(uuid.uuid4())})
            assert await session.scalar(select(func.count()).select_from(HypermeshOnboardingRun)) == 0
            await session.rollback()
