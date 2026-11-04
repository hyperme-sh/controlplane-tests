"""Unit tests for AI metering contracts (distinct from CryptoBudget)."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from decimal import Decimal

import pytest

from spanopticon_models.ai_metering import AIModel, AgentBudget, TokenUsage


def test_ai_model_decimal_coercion():
    m = AIModel(
        provider="openai",
        model_name="gpt-4",
        cost_per_1k_input=0.005,
        cost_per_1k_output=0.015,
    )
    assert m.cost_per_1k_input == Decimal("0.005")


def test_token_usage_roundtrip():
    u = TokenUsage(
        user_id=uuid.uuid4(),
        tenant_id=uuid.uuid4(),
        model="gpt-4",
        input_tokens=100,
        output_tokens=50,
        cost=Decimal("0.01"),
        timestamp=datetime.now(timezone.utc),
    )
    u2 = TokenUsage.model_validate_json(u.model_dump_json())
    assert u2.input_tokens == 100
    assert u2.cost == Decimal("0.01")


def test_token_usage_rejects_naive_timestamp():
    with pytest.raises(ValueError):
        TokenUsage(
            user_id=uuid.uuid4(),
            tenant_id=uuid.uuid4(),
            model="x",
            input_tokens=1,
            output_tokens=1,
            cost=Decimal("0"),
            timestamp=datetime(2024, 1, 1, 0, 0, 0),
        )


def test_agent_budget_spent_must_not_exceed_ceiling():
    with pytest.raises(ValueError):
        AgentBudget(
            id=uuid.uuid4(),
            tenant_id=uuid.uuid4(),
            ceiling_cents=100,
            spent_cents=101,
        )


def test_agent_budget_user_optional():
    b = AgentBudget(
        id=uuid.uuid4(),
        tenant_id=uuid.uuid4(),
        user_id=None,
        ceiling_cents=5000,
        spent_cents=0,
        currency="USD",
    )
    assert b.currency == "usd"
    assert b.user_id is None


def test_crypto_budget_import_still_distinct():
    from spanopticon_models import AgentBudget, CryptoBudget

    assert AgentBudget is not CryptoBudget
