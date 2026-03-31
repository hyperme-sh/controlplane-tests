from __future__ import annotations

import os
from pathlib import Path

from alembic import command
from alembic.config import Config


def repo_root() -> Path:
    # panopticon_tests/fixtures/postgres.py -> fixtures -> panopticon_tests -> src -> core/tests -> core -> repo
    return Path(__file__).resolve().parents[5]


def alembic_ini_path() -> Path:
    return repo_root() / "core" / "lib" / "controlplane-db" / "alembic.ini"


def normalize_sync_sqlalchemy_url(url: str) -> str:
    """Force psycopg3 driver (postgresql+psycopg). Testcontainers may emit postgresql+psycopg2://."""
    u = url.strip()
    if "+asyncpg" in u:
        return u.replace("+asyncpg", "+psycopg", 1)
    if u.startswith("postgresql+psycopg2://"):
        return "postgresql+psycopg://" + u.removeprefix("postgresql+psycopg2://")
    if u.startswith("postgresql://"):
        return "postgresql+psycopg://" + u.removeprefix("postgresql://")
    if u.startswith("postgres://"):
        return "postgresql+psycopg://" + u.removeprefix("postgres://")
    return u


def run_alembic_upgrade(sync_url: str) -> None:
    """Run Alembic migrations against a sync SQLAlchemy URL (psycopg)."""
    sync_url = normalize_sync_sqlalchemy_url(sync_url)
    os.environ["DATABASE_URL"] = sync_url
    cfg = Config(str(alembic_ini_path()))
    command.upgrade(cfg, "head")


def async_db_url(sync_psycopg_url: str) -> str:
    return sync_psycopg_url.replace("+psycopg", "+asyncpg", 1)
