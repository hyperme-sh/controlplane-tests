"""Platform owner projections retain tenant and payer identity boundaries."""
from dataclasses import FrozenInstanceError
import uuid

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

from controlplane_db.models import ApiKey, StripeSettings, Tenant, User
from controlplane_db.payment_store import (
    UnstablePayerIdentity,
    get_stripe_settings,
    iter_webhook_secrets,
    mismatch_person_sub,
    service_user_id_for_apikey_sub,
    user_id_for_sub,
)

pytestmark = pytest.mark.integration


@pytest.fixture
def owner_store(migrated_postgres):
    engine = create_engine(migrated_postgres)
    ta, tb = uuid.uuid4(), uuid.uuid4()
    person = "payer-" + uuid.uuid4().hex
    with Session(engine) as session:
        session.add_all([Tenant(id=ta, name=str(ta)), Tenant(id=tb, name=str(tb))])
        session.flush()
        user = User(tenant_id=ta, keycloak_sub=person, email=person + "@example.com",
                    first_name="Test", last_name="Payer", gender="unknown", is_active=True)
        session.add(user)
        session.flush()
        key = ApiKey(tenant_id=ta, name="test", key_prefix=uuid.uuid4().hex[:16],
                     secret_hash="unused", created_by_sub=person)
        session.add(key)
        session.add_all([
            StripeSettings(tenant_id=ta, stripe_secret_key="sk_test_owner_a",
                           stripe_webhook_secret="whsec_owner_a"),
            StripeSettings(tenant_id=tb, stripe_secret_key="sk_test_owner_b",
                           stripe_webhook_secret=""),
        ])
        session.commit()
        user_id, key_id = user.id, key.id
    try:
        yield engine, ta, tb, person, user_id, key_id
    finally:
        with engine.begin() as connection:
            connection.execute(text("DELETE FROM tenants WHERE id IN (:ta, :tb)"),
                               {"ta": ta, "tb": tb})
        engine.dispose()


def test_credentials_are_detached_immutable_and_tenant_scoped(owner_store):
    engine, ta, tb, _, _, _ = owner_store
    a = get_stripe_settings(engine, ta)
    b = get_stripe_settings(engine, tb)
    assert a.tenant_id == ta and a.stripe_secret_key == "sk_test_owner_a"
    assert b.tenant_id == tb and b.stripe_secret_key == "sk_test_owner_b"
    assert "sk_test" not in repr(a)
    with pytest.raises(FrozenInstanceError):
        a.stripe_secret_key = "replacement"
    assert get_stripe_settings(engine, uuid.uuid4()) is None
    assert get_stripe_settings(engine, ta) == a
    secrets = iter_webhook_secrets(engine)
    assert (ta, "whsec_owner_a") in secrets
    assert all(tenant != tb for tenant, _ in secrets)


def test_person_and_key_resolution_never_cross_tenants(owner_store):
    engine, ta, tb, person, user_id, key_id = owner_store
    sub = "apikey:" + str(key_id)
    for identity in (person, str(user_id)):
        assert user_id_for_sub(engine, ta, identity) == user_id
        assert user_id_for_sub(engine, tb, identity) is None
        assert mismatch_person_sub(engine, ta, identity) == person
    assert service_user_id_for_apikey_sub(engine, ta, sub) == user_id
    assert service_user_id_for_apikey_sub(engine, tb, sub) is None
    assert mismatch_person_sub(engine, ta, sub) == person
    for bad in (sub, "apikey:invalid", "apikey:" + str(uuid.uuid4())):
        with pytest.raises(UnstablePayerIdentity, match="stable payer identity"):
            mismatch_person_sub(engine, tb, bad)
    assert user_id_for_sub(engine, ta, None) is None
    assert service_user_id_for_apikey_sub(engine, ta, "apikey:invalid") is None


def test_webhook_secret_enumeration_preserves_rls_visibility(owner_store):
    from sqlalchemy import event

    engine, ta, tb, _, _, _ = owner_store
    role = "payment_store_reader_" + uuid.uuid4().hex
    with engine.begin() as connection:
        connection.exec_driver_sql(f'CREATE ROLE "{role}" NOLOGIN NOBYPASSRLS')
        connection.exec_driver_sql(f'GRANT USAGE ON SCHEMA public TO "{role}"')
        connection.exec_driver_sql(f'GRANT SELECT ON stripe_settings TO "{role}"')
    reader = create_engine(engine.url)

    @event.listens_for(reader, "begin")
    def scoped_role(connection):
        connection.exec_driver_sql(f'SET LOCAL ROLE "{role}"')
        connection.execute(text("SELECT set_config('app.current_tenant_id', :tid, true)"),
                           {"tid": str(ta)})

    try:
        assert iter_webhook_secrets(reader) == [(ta, "whsec_owner_a")]
        assert get_stripe_settings(reader, ta).stripe_secret_key == "sk_test_owner_a"
        assert get_stripe_settings(reader, tb).stripe_secret_key == "sk_test_owner_b"
        assert iter_webhook_secrets(reader) == [(ta, "whsec_owner_a")]
    finally:
        reader.dispose()
        with engine.begin() as connection:
            connection.exec_driver_sql(f'REVOKE SELECT ON stripe_settings FROM "{role}"')
            connection.exec_driver_sql(f'REVOKE USAGE ON SCHEMA public FROM "{role}"')
            connection.exec_driver_sql(f'DROP ROLE "{role}"')
