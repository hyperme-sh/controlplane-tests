from __future__ import annotations

import json
import uuid

import pytest
from pydantic import ValidationError
from solders.keypair import Keypair

from spanopticon_models import (
    ACLPolicy,
    CryptoBudget,
    PaymentIntent,
    SharedGroup,
    SharedOrganization,
    SharedTenant,
    SharedUser,
    SociACLAttributes,
)
from spanopticon_models.acl import PoliticalViewCategory, SexualOrientationCategory


def test_shared_tenant_roundtrip():
    tid = uuid.uuid4()
    t = SharedTenant(id=tid, name="Acme", slug="acme-corp")
    raw = t.model_dump(mode="json")
    assert SharedTenant.model_validate(raw) == t
    assert json.loads(json.dumps(raw)) == raw


def test_shared_tenant_slug_invalid():
    with pytest.raises(ValidationError):
        SharedTenant(id=uuid.uuid4(), name="X", slug="Bad_Slug")


def test_shared_user_roundtrip():
    uid = uuid.uuid4()
    u = SharedUser(
        id=uid,
        email="user@example.com",
        display_name="User",
        tenant_id=None,
    )
    raw = u.model_dump(mode="json")
    assert SharedUser.model_validate(raw) == u


def test_shared_group_organization_roundtrip():
    tid = uuid.uuid4()
    g = SharedGroup(id=uuid.uuid4(), name="admins", tenant_id=tid)
    o = SharedOrganization(
        id=uuid.uuid4(), legal_name="Acme LLC", tenant_id=tid
    )
    assert SharedGroup.model_validate(g.model_dump(mode="json")) == g
    assert SharedOrganization.model_validate(o.model_dump(mode="json")) == o


def test_sociacl_and_acl_policy_roundtrip():
    soc = SociACLAttributes(
        political_view=PoliticalViewCategory.OTHER,
        sexual_orientation=SexualOrientationCategory.PREFER_NOT_TO_SAY,
        verification_level=2,
    )
    p = ACLPolicy(
        id=uuid.uuid4(),
        name="doc-read",
        effect="allow",
        resource_pattern="documents:*",
        action="read",
        sociacl=soc,
    )
    d = p.model_dump(mode="json")
    assert ACLPolicy.model_validate(d) == p


def test_acl_blank_resource_pattern():
    with pytest.raises(ValidationError):
        ACLPolicy(
            id=uuid.uuid4(),
            name="x",
            effect="deny",
            resource_pattern="   ",
            action="read",
        )


def test_acl_whitespace_only_resource_pattern():
    with pytest.raises(ValidationError):
        ACLPolicy(
            id=uuid.uuid4(),
            name="x",
            effect="allow",
            resource_pattern="\n\t  \n",
            action="read",
        )


def test_sociacl_verification_level_bounds():
    with pytest.raises(ValidationError):
        SociACLAttributes(verification_level=5)


def test_payment_intent_roundtrip():
    pid = uuid.uuid4()
    p = PaymentIntent(
        id=pid,
        amount_cents=100,
        currency="USD",
        status="requires_payment_method",
    )
    d = p.model_dump(mode="json")
    assert PaymentIntent.model_validate(d) == p
    p2 = PaymentIntent(
        id=pid,
        amount_cents=50,
        currency="eur",
        status="succeeded",
    )
    assert p2.currency == "eur"


def test_payment_intent_rejects_non_positive_amount():
    with pytest.raises(ValidationError):
        PaymentIntent(
            id=uuid.uuid4(),
            amount_cents=0,
            currency="usd",
            status="canceled",
        )


def test_crypto_budget_roundtrip():
    owner = str(Keypair().pubkey())
    mint = str(Keypair().pubkey())
    c = CryptoBudget(
        owner=owner,
        mint=mint,
        allocated_lamports=100,
        ceiling_lamports=500,
    )
    d = c.model_dump(mode="json")
    assert CryptoBudget.model_validate(d) == c


def test_crypto_budget_allocated_exceeds_ceiling():
    owner = str(Keypair().pubkey())
    mint = str(Keypair().pubkey())
    with pytest.raises(ValidationError):
        CryptoBudget(
            owner=owner,
            mint=mint,
            allocated_lamports=600,
            ceiling_lamports=500,
        )


def test_crypto_budget_invalid_pubkey():
    with pytest.raises(ValidationError):
        CryptoBudget(
            owner="not-a-real-pubkey-string-here-at-all",
            mint=str(Keypair().pubkey()),
            allocated_lamports=0,
            ceiling_lamports=1,
        )


def test_model_forbid_extra():
    with pytest.raises(ValidationError):
        SharedTenant(
            id=uuid.uuid4(),
            name="A",
            slug="a",
            extra_field="nope",  # type: ignore[call-arg]
        )
