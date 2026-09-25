"""0038 widens aiuditor size_bytes. 0039 retargets the lab device.

A GGUF larger than int4 (11264441216) has to insert after 0038 and must
not fit before it. 0039 sets lab on the live AGX only when that row
exists, retires the placeholder in place, and leaves listing,
certification, and price columns alone. Downgrade of 0039 is a no-op.
"""

from __future__ import annotations

import uuid

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text
from sqlalchemy.exc import DBAPIError
from testcontainers.postgres import PostgresContainer

from panopticon_tests.fixtures.postgres import (
    alembic_ini_path,
    normalize_sync_sqlalchemy_url,
)

pytestmark = pytest.mark.integration

_MARKETPLACE = "8f8e8d8c-7b6a-4a59-8c3d-2e1f0a9b8c7d"
_LIVE = "f6124d28-772c-4f1f-8e03-1a7a17724381"
_PLACEHOLDER = "a6400000-0640-4000-8000-000000000001"
_OVERSIZE = 11_264_441_216
_BEFORE_BIGINT = "0037_api_key_last_used"
_BIGINT = "0038_aiuditor_size_bytes_bigint"


def _upgrade(url: str, revision: str = "head") -> None:
    import os

    os.environ["DATABASE_URL"] = url
    command.upgrade(Config(str(alembic_ini_path())), revision)


def _downgrade(url: str, revision: str) -> None:
    import os

    os.environ["DATABASE_URL"] = url
    command.downgrade(Config(str(alembic_ini_path())), revision)


def _size_type(url: str, table: str) -> str:
    eng = create_engine(url)
    with eng.begin() as cx:
        row = cx.execute(
            text(
                "SELECT data_type FROM information_schema.columns "
                "WHERE table_name = :table AND column_name = 'size_bytes'"
            ),
            {"table": table},
        ).one()
    return row[0]


def _insert_candidate(url: str, size_bytes: int) -> None:
    eng = create_engine(url)
    with eng.begin() as cx:
        cx.execute(
            text(
                """
                INSERT INTO aiuditor_candidates (
                    id, tenant_id, repo, revision, file_name, size_bytes,
                    publisher, catalog_id, class_id, status, created_at, updated_at
                ) VALUES (
                    CAST(:id AS uuid), CAST(:tenant AS uuid), 'bartowski/Llama', 'main',
                    'model.gguf', :size,
                    'bartowski', 'llama', 'agx-large', 'fit', now(), now()
                )
                """
            ),
            {"id": uuid.uuid4(), "tenant": _MARKETPLACE, "size": size_bytes},
        )


def _insert_device(
    cx,
    *,
    device_id: str,
    serial: str,
    lab: bool,
    enrolled: bool,
    sell_state: str,
    path_b_state: str,
) -> None:
    cx.execute(
        text(
            """
            INSERT INTO hypermesh_devices (
                id, tenant_id, class_id, host_org_id, serial, path_a,
                path_b_state, loaded_instance_state, enrolled, created_at,
                sell_state, role, lab
            ) VALUES (
                CAST(:id AS uuid), CAST(:tenant AS uuid), 'agx-large', CAST(:host AS uuid),
                :serial, CAST(:path_a AS jsonb),
                :path_b, 'none', :enrolled, now(), :sell, 'host', :lab
            )
            """
        ),
        {
            "id": device_id,
            "tenant": _MARKETPLACE,
            "host": str(uuid.uuid4()),
            "serial": serial,
            "path_a": '{"sku":"AGX Orin 64GB"}',
            "path_b": path_b_state,
            "enrolled": enrolled,
            "sell": sell_state,
            "lab": lab,
        },
    )


def _device(url: str, device_id: str) -> dict[str, object]:
    eng = create_engine(url)
    with eng.begin() as cx:
        row = cx.execute(
            text(
                """
                SELECT lab, enrolled, sell_state, path_b_state
                FROM hypermesh_devices
                WHERE id = CAST(:id AS uuid) AND tenant_id = CAST(:tenant AS uuid)
                """
            ),
            {"id": device_id, "tenant": _MARKETPLACE},
        ).one()
    return {
        "lab": row[0],
        "enrolled": row[1],
        "sell_state": row[2],
        "path_b_state": row[3],
    }


def _prices(url: str) -> dict[str, object]:
    eng = create_engine(url)
    with eng.begin() as cx:
        hw = cx.execute(
            text(
                """
                SELECT hourly_rate_cents FROM hypermesh_hardware_classes
                WHERE tenant_id = CAST(:tenant AS uuid) AND class_id = 'agx-large'
                """
            ),
            {"tenant": _MARKETPLACE},
        ).one()
        cat = cx.execute(
            text(
                """
                SELECT certified, tokens_per_sec, per_1k_token_cents, loaded_hour_min_cents
                FROM hypermesh_catalog
                WHERE tenant_id = CAST(:tenant AS uuid) AND catalog_id = 'llama-3.1-8b-q4'
                """
            ),
            {"tenant": _MARKETPLACE},
        ).one()
    return {
        "hourly_rate_cents": hw[0],
        "certified": cat[0],
        "tokens_per_sec": cat[1],
        "per_1k_token_cents": cat[2],
        "loaded_hour_min_cents": cat[3],
    }


@pytest.fixture
def postgres(require_docker: None):
    with PostgresContainer("postgres:16-alpine") as pg:
        yield normalize_sync_sqlalchemy_url(pg.get_connection_url())


def _narrow_size_bytes(url: str) -> None:
    """Rebuild the pan-test shape: INTEGER columns from before this change.

    Revision 0001 runs ``Base.metadata.create_all``. Current models are
    BIGINT, so a fresh chain is already wide and 0030's CREATE is a no-op.
    Production was migrated when the models were still Integer.
    """

    eng = create_engine(url)
    with eng.begin() as cx:
        cx.execute(
            text(
                "ALTER TABLE aiuditor_candidates "
                "ALTER COLUMN size_bytes TYPE INTEGER"
            )
        )
        cx.execute(
            text(
                "ALTER TABLE aiuditor_admissions "
                "ALTER COLUMN size_bytes TYPE INTEGER"
            )
        )


def test_size_bytes_bigint_upgrade_and_downgrade(postgres: str) -> None:
    _upgrade(postgres, _BEFORE_BIGINT)
    _narrow_size_bytes(postgres)
    assert _size_type(postgres, "aiuditor_candidates") == "integer"
    assert _size_type(postgres, "aiuditor_admissions") == "integer"
    with pytest.raises(DBAPIError):
        _insert_candidate(postgres, _OVERSIZE)

    _upgrade(postgres, _BIGINT)
    assert _size_type(postgres, "aiuditor_candidates") == "bigint"
    assert _size_type(postgres, "aiuditor_admissions") == "bigint"
    _insert_candidate(postgres, _OVERSIZE)
    eng = create_engine(postgres)
    with eng.begin() as cx:
        stored = cx.execute(
            text("SELECT size_bytes FROM aiuditor_candidates")
        ).scalar_one()
        cx.execute(text("DELETE FROM aiuditor_candidates"))
    assert stored == _OVERSIZE

    _downgrade(postgres, _BEFORE_BIGINT)
    assert _size_type(postgres, "aiuditor_candidates") == "integer"
    assert _size_type(postgres, "aiuditor_admissions") == "integer"
    with pytest.raises(DBAPIError):
        _insert_candidate(postgres, _OVERSIZE)


def test_lab_identity_keeps_listing_cert_and_price(postgres: str) -> None:
    _upgrade(postgres, _BIGINT)
    eng = create_engine(postgres)
    with eng.begin() as cx:
        _insert_device(
            cx,
            device_id=_LIVE,
            serial="FYBER-AGX64-1",
            lab=False,
            enrolled=True,
            sell_state="selling",
            path_b_state="green",
        )
        _insert_device(
            cx,
            device_id=_PLACEHOLDER,
            serial="FYBER-AGX-ORIN-64-001",
            lab=True,
            enrolled=True,
            sell_state="selling",
            path_b_state="green",
        )
        cx.execute(
            text(
                """
                INSERT INTO hypermesh_hardware_classes (
                    id, tenant_id, class_id, vendor, sku, module, memory_gb,
                    public_sku, path_b_suite_id, hourly_rate_cents
                ) VALUES (
                    CAST(:id AS uuid), CAST(:tenant AS uuid), 'agx-large',
                    'NVIDIA', 'AGX', 'AGX', 64, true, 'thin', 1234
                )
                """
            ),
            {"tenant": _MARKETPLACE, "id": str(uuid.uuid4())},
        )
        cx.execute(
            text(
                """
                INSERT INTO hypermesh_catalog (
                    id, tenant_id, catalog_id, artifact_name, class_id,
                    certified, tokens_per_sec, per_1k_token_cents, loaded_hour_min_cents
                ) VALUES (
                    CAST(:id AS uuid), CAST(:tenant AS uuid), 'llama-3.1-8b-q4',
                    'Llama', 'agx-large', true, 9.5, 77, 88
                )
                """
            ),
            {"tenant": _MARKETPLACE, "id": str(uuid.uuid4())},
        )
    before_prices = _prices(postgres)

    _upgrade(postgres, "head")
    live = _device(postgres, _LIVE)
    assert live == {
        "lab": True,
        "enrolled": True,
        "sell_state": "selling",
        "path_b_state": "green",
    }
    placeholder = _device(postgres, _PLACEHOLDER)
    assert placeholder == {
        "lab": False,
        "enrolled": False,
        "sell_state": "paused",
        "path_b_state": "green",
    }
    assert _prices(postgres) == before_prices

    _downgrade(postgres, _BIGINT)
    assert _device(postgres, _LIVE)["lab"] is True
    assert _device(postgres, _PLACEHOLDER)["sell_state"] == "paused"
    assert _prices(postgres) == before_prices

    _upgrade(postgres, "head")
    assert _device(postgres, _LIVE) == live
    assert _device(postgres, _PLACEHOLDER) == placeholder
    assert _prices(postgres) == before_prices


def test_lab_identity_is_noop_when_devices_are_absent(postgres: str) -> None:
    _upgrade(postgres, _BIGINT)
    eng = create_engine(postgres)
    with eng.begin() as cx:
        count = cx.execute(text("SELECT count(*) FROM hypermesh_devices")).scalar_one()
    assert count == 0
    _upgrade(postgres, "head")
    with eng.begin() as cx:
        count = cx.execute(text("SELECT count(*) FROM hypermesh_devices")).scalar_one()
    assert count == 0
