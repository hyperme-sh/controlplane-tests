"""A Keycloak identity may join multiple tenants without losing its binding."""
import uuid

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.exc import IntegrityError
from testcontainers.postgres import PostgresContainer

from controlplane_db import migrations
from spanopticon_tests.fixtures.postgres import normalize_sync_sqlalchemy_url

pytestmark = pytest.mark.integration


def test_subject_uniqueness_is_per_membership_scope():
    with PostgresContainer("postgres:16-alpine") as pg:
        url = normalize_sync_sqlalchemy_url(pg.get_connection_url())
        engine = create_engine(url)
        with engine.begin() as db:
            db.execute(text("CREATE ROLE controlplane; CREATE ROLE controlplane_app NOBYPASSRLS"))
        migrations.upgrade(url, baseline_only=True)
        subject = "existing-keycloak-operator"
        original = uuid.uuid4()

        def insert(db, tenant, *, identity=None, deleted=False):
            db.execute(text("""INSERT INTO users
                (id, tenant_id, first_name, last_name, email, gender,
                 is_active, keycloak_sub, deleted_at)
                VALUES (:id, :tenant, 'Network', 'Operator', :email, 'other',
                        true, :sub, CASE WHEN :deleted THEN now() ELSE NULL END)"""),
                {"id": identity or uuid.uuid4(), "tenant": tenant,
                 "email": f"{uuid.uuid4()}@example.invalid", "sub": subject,
                 "deleted": deleted})

        with engine.begin() as db:
            insert(db, None, identity=original)
        migrations.prepare_event_source_index(url)
        migrations.upgrade(url)
        tenants = [uuid.uuid4(), uuid.uuid4()]
        with engine.begin() as db:
            for tenant in tenants:
                db.execute(text("INSERT INTO tenants (id,name,is_active) VALUES (:id,:name,true)"),
                           {"id": tenant, "name": str(tenant)})
                insert(db, tenant, deleted=tenant == tenants[1])
            assert db.execute(text("SELECT keycloak_sub FROM users WHERE id=:id"),
                              {"id": original}).scalar_one() == subject
        for scope in [None, *tenants]:
            with pytest.raises(IntegrityError), engine.begin() as db:
                insert(db, scope)
        with engine.connect() as db:
            assert db.execute(text("SELECT count(*) FROM users WHERE keycloak_sub=:sub"),
                              {"sub": subject}).scalar_one() == 3
        engine.dispose()
