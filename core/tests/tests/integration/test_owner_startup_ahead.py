"""An N-1 image must boot when Postgres is already at a newer owner head."""
from __future__ import annotations

import logging
import shutil
from pathlib import Path

import pytest
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, inspect

from controlplane_db import migrations
from controlplane_db.startup import main
from controlplane_tenancy import startup as tenancy_startup

pytestmark = pytest.mark.integration
ROOT = Path(__file__).resolve().parents[4]
CHAINS = ROOT / 'core/lib/controlplane-db/src/controlplane_db/migration_chains'


def _drop_head_script(root: Path, chain: str, revision: str) -> None:
    marker = f'revision = "{revision}"'
    matches = [path for path in (root / chain / 'versions').glob('*.py') if marker in path.read_text()]
    assert len(matches) == 1
    matches[0].unlink()


def test_startup_boots_when_database_is_ahead_of_the_image(migration_database, tmp_path, monkeypatch, caplog):
    url = migration_database()
    migrations.upgrade(url)
    before = migrations.current_heads(url)
    root = tmp_path / 'chains'
    shutil.copytree(CHAINS, root)
    _drop_head_script(root, 'hypermesh', before['hypermesh'])
    image_head = ScriptDirectory.from_config(migrations._config(root, 'hypermesh')).get_current_head()
    assert image_head != before['hypermesh']

    monkeypatch.setattr(migrations, '_ROOT', root)
    monkeypatch.setenv('DATABASE_URL', url)
    monkeypatch.setenv('CONTROLPLANE_MIGRATION_MODE', 'owners')
    with caplog.at_level(logging.WARNING):
        assert main() == 0
        assert tenancy_startup.main() == 0

    assert migrations.current_heads(url) == before
    warnings = [record.getMessage() for record in caplog.records if record.name == 'controlplane_db.startup']
    expected = (
        'skipping hypermesh upgrade: database revision '
        f'{before["hypermesh"]} is not in this image (image head {image_head})'
    )
    assert warnings == [expected, expected]
    engine = create_engine(url)
    try:
        with engine.connect() as connection:
            assert inspect(connection).has_table('hypermesh_protection_approvals')
    finally:
        engine.dispose()
