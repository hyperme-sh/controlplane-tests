"""Unit tests for DB URL normalization (no Docker)."""

from __future__ import annotations

import pytest

from spanopticon_tests.fixtures.postgres import normalize_sync_sqlalchemy_url


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


@pytest.mark.parametrize("driver", [
    "postgres", "postgresql", "postgresql+asyncpg", "postgresql+psycopg2",
    "postgresql+psycopg",
])
@pytest.mark.parametrize("convert, expected_driver", [
    (normalize_sync_sqlalchemy_url, "postgresql+psycopg"),
    (pytest.param(None, "postgresql+asyncpg", id="async")),
])
def test_driver_conversion_preserves_every_other_url_component(driver, convert, expected_driver):
    from sqlalchemy.engine import make_url
    from spanopticon_tests.fixtures.postgres import async_db_url

    convert = convert or async_db_url
    tail = "u%40name:p%40ss+asyncpg+psycopg%2F@host+asyncpg:5433/db+psycopg?application_name=app%2Basyncpg&options=x%2Bpsycopg"
    raw = driver + "://" + tail
    result = convert(raw)
    assert result == expected_driver + "://" + tail
    before, after = make_url(raw), make_url(result)
    assert after.drivername == expected_driver
    for field in ("username", "password", "host", "port", "database", "query"):
        assert getattr(after, field) == getattr(before, field)
    assert after.username == "u@name"
    assert after.password == "p@ss+asyncpg+psycopg/"


def test_database_url_preserves_driver_like_credentials_and_query():
    from sqlalchemy.engine import make_url
    from spanopticon_tests.fixtures.postgres import database_url

    raw = "postgresql+psycopg://u:p%40ss+asyncpg@host:5432/old?application_name=app%2Bpsycopg"
    before = make_url(raw)
    after = make_url(database_url(raw, "new"))
    assert after.database == "new"
    assert after.password == before.password == "p@ss+asyncpg"
    assert after.query == before.query
    assert after.username == before.username
    assert after.host == before.host
    assert after.port == before.port


@pytest.mark.parametrize("raw", [
    "mysql+pymysql://u:password+asyncpg@localhost/db",
    "sqlite:///db+psycopg",
])
def test_unrelated_drivers_are_not_rewritten(raw):
    from spanopticon_tests.fixtures.postgres import async_db_url

    assert normalize_sync_sqlalchemy_url(raw) == raw
    assert async_db_url(raw) == raw
