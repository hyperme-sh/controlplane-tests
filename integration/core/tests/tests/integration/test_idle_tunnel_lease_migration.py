"""#206: idle peers retain keys/address while terminal lease IDs are cleared."""
from __future__ import annotations

import uuid

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text
from sqlalchemy.exc import IntegrityError

from spanopticon_tests.fixtures.postgres import alembic_ini_path

pytestmark = pytest.mark.integration


@pytest.mark.migration_start("0048_lab_pull_disk")
def test_idle_tunnel_migration_preserves_peers_and_tenant_scope(migration_db, monkeypatch):
    url = migration_db
    monkeypatch.setenv("DATABASE_URL", url)
    config = Config(str(alembic_ini_path()))
    command.upgrade(config, "0048_lab_pull_disk")
    engine = create_engine(url)
    tenant, other = uuid.uuid4(), uuid.uuid4()
    leases = {status: uuid.uuid4() for status in ("ended", "failed", "refunded", "active")}
    with engine.begin() as cx:
        for tid in (tenant, other):
            cx.execute(text("INSERT INTO tenants (id, name, is_active) VALUES (:id, :name, true)"), {"id": tid, "name": f"tunnel migration {tid}"})
        for status, lid in leases.items():
            cx.execute(text("""
                INSERT INTO hypermesh_leases
                  (id, tenant_id, kind, status, renter_user_id, class_id, reserved_hours, created_at, token_count, loaded_hours, purpose)
                VALUES (:id, :tenant, 'p1_hardware', :status, :renter, 'agx-large', 1, now(), 0, 0, 'renter')
            """), {"id": lid, "tenant": tenant, "status": status, "renter": uuid.uuid4()})
        cases = [(status, tenant, lid, "allocated") for status, lid in leases.items()]
        cases += [("other-tenant", other, leases["ended"], "allocated"),
                  ("revoked", tenant, leases["ended"], "revoked")]
        for name, tid, lid, status in cases:
            cx.execute(text("""
                INSERT INTO hypermesh_tunnels
                  (id, tenant_id, lease_id, device_id, status, endpoint, server_public_key,
                   server_private_key, allowed_ips, client_address, ack_applied, created_at, expires_at)
                VALUES (:id, :tenant, :lease, :device, :status, :name, 'server-public',
                        'server-private', '10.77.0.0/24', '10.77.0.2/32', true, now(), now())
            """), {"id": uuid.uuid4(), "tenant": tid, "lease": lid, "device": uuid.uuid4(),
                     "status": status, "name": name})
    command.upgrade(config, "0049_idle_tunnel_lease")
    with engine.connect() as cx:
        rows = {row.endpoint: row for row in cx.execute(text("SELECT * FROM hypermesh_tunnels"))}
    for status in ("ended", "failed", "refunded"):
        row = rows[status]
        assert row.lease_id is None
        assert row.expires_at is None
        assert row.server_private_key == "server-private"
        assert row.client_address == "10.77.0.2/32"
        assert row.ack_applied is True
        assert row.status == "allocated"
    assert rows["active"].lease_id == leases["active"]
    assert rows["other-tenant"].lease_id == leases["ended"]
    assert rows["revoked"].lease_id == leases["ended"]
    # The rollback must fail rather than delete peers or fabricate ownership.
    with pytest.raises(IntegrityError):
        command.downgrade(config, "0048_lab_pull_disk")
    with engine.begin() as cx:
        assert cx.execute(text("SELECT count(*) FROM hypermesh_tunnels")).scalar() == len(cases)
        cx.execute(text("UPDATE hypermesh_tunnels SET lease_id = :lease WHERE lease_id IS NULL"), {"lease": leases["active"]})
    command.downgrade(config, "0048_lab_pull_disk")
    command.upgrade(config, "0049_idle_tunnel_lease")
    engine.dispose()
