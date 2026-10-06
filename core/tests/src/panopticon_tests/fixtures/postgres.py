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


def database_url(server_url: str, name: str) -> str:
    """Return ``server_url`` pointed at database ``name`` (same host, port and credentials)."""
    from sqlalchemy.engine import make_url

    url = make_url(normalize_sync_sqlalchemy_url(server_url)).set(database=name)
    return url.render_as_string(hide_password=False)


def _alembic(sync_url: str, action: str, revision: str) -> None:
    """Run one Alembic command against ``sync_url`` and restore ``DATABASE_URL`` afterwards."""
    previous = os.environ.get("DATABASE_URL")
    os.environ["DATABASE_URL"] = normalize_sync_sqlalchemy_url(sync_url)
    try:
        getattr(command, action)(Config(str(alembic_ini_path())), revision)
    finally:
        if previous is None:
            os.environ.pop("DATABASE_URL", None)
        else:
            os.environ["DATABASE_URL"] = previous


class MigrationTemplates:
    """Copy-on-demand databases for migration tests that share one PostgreSQL server.

    Starting a container and replaying the whole Alembic chain for every test
    dominated the core suite. This keeps one server per pytest process (one per
    xdist worker), migrates each requested revision into a template database
    once, and hands every test its own ``CREATE DATABASE ... TEMPLATE`` copy.

    A template at revision R is built by copying the nearest lower template and
    upgrading it, which runs the same revision steps as an upgrade from empty.
    The server must not gain the optional ``controlplane``/``controlplane_app``
    roles: several migrations grant to them only when they exist, and the tests
    using this helper expect the role-free behavior a fresh container had.
    """

    def __init__(self, server_url: str, *, prefix: str = "mt") -> None:
        from sqlalchemy import create_engine

        self._server_url = normalize_sync_sqlalchemy_url(server_url)
        self._admin = create_engine(self._server_url, isolation_level="AUTOCOMMIT")
        self._prefix = prefix
        self._templates: dict[str, str] = {}
        self._counter = 0
        self._order: dict[str, int] | None = None

    # -- naming -----------------------------------------------------------------
    def _name(self, kind: str, key: str = "") -> str:
        import re

        self._counter += 1
        slug = re.sub(r"[^a-z0-9]+", "_", key.lower()).strip("_")
        return f"{self._prefix}_{kind}_{self._counter}_{slug}"[:63]

    def url(self, name: str) -> str:
        return database_url(self._server_url, name)

    def _create(self, name: str, template: str = "template0") -> None:
        with self._admin.connect() as cx:
            cx.exec_driver_sql(f'CREATE DATABASE "{name}" TEMPLATE "{template}"')

    def drop(self, name: str) -> None:
        with self._admin.connect() as cx:
            cx.exec_driver_sql(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')

    # -- revisions --------------------------------------------------------------
    def _revision_index(self, revision: str) -> int:
        from alembic.script import ScriptDirectory

        if self._order is None:
            script = ScriptDirectory.from_config(Config(str(alembic_ini_path())))
            heads = script.get_heads()
            if len(heads) != 1:
                raise RuntimeError(f"migration templates need one Alembic head, found {heads}")
            chain = [rev.revision for rev in script.walk_revisions()]  # head first
            self._order = {rev: len(chain) - i for i, rev in enumerate(chain)}
            self._order["head"] = self._order[heads[0]]
        return self._order[revision]

    def template_at(self, revision: str) -> str:
        """Name of a template database upgraded from empty to ``revision`` (built once)."""
        if revision == "head":
            self._revision_index("head")
            assert self._order is not None
            revision = next(rev for rev, i in self._order.items() if rev != "head" and i == self._order["head"])
        key = f"rev:{revision}"
        if key in self._templates:
            return self._templates[key]
        target = self._revision_index(revision)
        base, base_index = "template0", 0
        for built_key, name in self._templates.items():
            if not built_key.startswith("rev:"):
                continue
            index = self._revision_index(built_key.removeprefix("rev:"))
            if base_index < index <= target:
                base, base_index = name, index
        name = self._name("tpl", revision)
        self._create(name, base)
        if base_index != target:
            _alembic(self.url(name), "upgrade", revision)
        self._templates[key] = name
        return name

    def derived_template(self, key: str, base_revision: str, build) -> str:
        """Template made by copying ``template_at(base_revision)`` and running ``build(url)`` once."""
        cache_key = f"derived:{key}"
        if cache_key in self._templates:
            return self._templates[cache_key]
        name = self._name("tpl", key)
        self._create(name, self.template_at(base_revision))
        build(self.url(name))
        self._templates[cache_key] = name
        return name

    def database(self, template: str = "template0", *, label: str = "db") -> tuple[str, str]:
        """Create a fresh database copied from ``template``; return ``(name, sync_url)``."""
        name = self._name("db", label)
        self._create(name, template)
        return name, self.url(name)

    def dispose(self) -> None:
        self._admin.dispose()


def alembic_upgrade(sync_url: str, revision: str = "head") -> None:
    """Upgrade ``sync_url`` to ``revision`` without leaking ``DATABASE_URL``."""
    _alembic(sync_url, "upgrade", revision)


def alembic_downgrade(sync_url: str, revision: str) -> None:
    """Downgrade ``sync_url`` to ``revision`` without leaking ``DATABASE_URL``."""
    _alembic(sync_url, "downgrade", revision)
