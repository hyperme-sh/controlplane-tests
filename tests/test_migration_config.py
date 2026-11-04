"""Fixture configuration never selects an implicit database/migration owner."""
import pytest
from spanopticon_tests.fixtures import postgres


def test_configuration_is_required(monkeypatch):
    monkeypatch.delenv("SPANOPTICON_TEST_ALEMBIC_INI", raising=False)
    with pytest.raises(RuntimeError, match="must select"):
        postgres.alembic_ini_path()


@pytest.mark.parametrize("value", ["relative.ini", "/nonexistent/alembic.ini", "/tmp"])
def test_invalid_configuration_is_rejected(monkeypatch, value):
    monkeypatch.setenv("SPANOPTICON_TEST_ALEMBIC_INI", value)
    with pytest.raises(RuntimeError, match="existing absolute file"):
        postgres.alembic_ini_path()


@pytest.mark.parametrize("previous", [None, "postgresql://original/db"])
@pytest.mark.parametrize("fail", [False, True])
def test_upgrade_restores_environment(monkeypatch, tmp_path, previous, fail):
    config = tmp_path / "alembic.ini"
    config.write_text("[alembic]\nscript_location = %(here)s/migrations\n")
    monkeypatch.setenv("SPANOPTICON_TEST_ALEMBIC_INI", str(config))
    if previous is None:
        monkeypatch.delenv("DATABASE_URL", raising=False)
    else:
        monkeypatch.setenv("DATABASE_URL", previous)
    calls = []
    def upgrade(cfg, revision):
        calls.append((cfg.config_file_name, revision))
        assert postgres.os.environ["DATABASE_URL"] == "postgresql+psycopg://test/db"
        if fail:
            raise ValueError("migration failed")
    monkeypatch.setattr(postgres.command, "upgrade", upgrade)
    if fail:
        with pytest.raises(ValueError, match="migration failed"):
            postgres.run_alembic_upgrade("postgresql://test/db")
    else:
        postgres.run_alembic_upgrade("postgresql://test/db")
    assert calls == [(str(config), "head")]
    assert postgres.os.environ.get("DATABASE_URL") == previous


def test_missing_config_restores_environment(monkeypatch):
    monkeypatch.delenv("SPANOPTICON_TEST_ALEMBIC_INI", raising=False)
    monkeypatch.setenv("DATABASE_URL", "original")
    with pytest.raises(RuntimeError):
        postgres.alembic_upgrade("postgresql://test/db")
    assert postgres.os.environ["DATABASE_URL"] == "original"


def test_database_url_preserves_credentials_and_host():
    from sqlalchemy.engine import make_url
    url = make_url(postgres.database_url("postgresql://name:p%40ss@localhost:5432/old", "new"))
    assert (url.drivername, url.username, url.password, url.host, url.port, url.database) == (
        "postgresql+psycopg", "name", "p@ss", "localhost", 5432, "new")


def test_async_driver_conversion():
    assert postgres.async_db_url("postgresql+psycopg://u:p@h/db") == "postgresql+asyncpg://u:p@h/db"
