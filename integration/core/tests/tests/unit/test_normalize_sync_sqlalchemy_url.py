"""Unit tests for DB URL normalization (no Docker)."""

from __future__ import annotations

import pytest

from panopticon_tests.fixtures.postgres import normalize_sync_sqlalchemy_url


@pytest.mark.parametrize(
    ("raw", "expected_prefix"),
    [
        ("postgresql://u:p@h:5432/db", "postgresql+psycopg://"),
        ("postgres://u:p@h:5432/db", "postgresql+psycopg://"),
        (
            "postgresql+psycopg2://u:p@h:5432/db",
            "postgresql+psycopg://",
        ),
        (
            "postgresql+asyncpg://u:p@h:5432/db",
            "postgresql+psycopg://",
        ),
    ],
)
def test_normalize_sync_sqlalchemy_url(raw: str, expected_prefix: str) -> None:
    out = normalize_sync_sqlalchemy_url(raw)
    assert out.startswith(expected_prefix)
    assert "u:p@h:5432/db" in out


def test_normalize_preserves_already_psycopg_v3() -> None:
    raw = "postgresql+psycopg://u:p@localhost:5432/db"
    assert normalize_sync_sqlalchemy_url(raw) == raw
