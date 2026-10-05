"""Freeze declared APIs and reject ownership/version drift without importing services."""

import importlib.util
import json
from pathlib import Path
import subprocess

import pytest

ROOT = Path(__file__).resolve().parents[4]
spec = importlib.util.spec_from_file_location("check_library_contracts", ROOT / "scripts/check_library_contracts.py")
checker = importlib.util.module_from_spec(spec)
spec.loader.exec_module(checker)


def test_current_manifest_covers_every_orm_table_once_and_snapshots_match():
    manifest = json.loads((ROOT / checker.MANIFEST).read_text(), object_pairs_hook=checker.unique_keys)
    assert checker.check(ROOT, manifest) == []
    assert set(manifest["tables"]) == checker.table_names(ROOT)


def test_missing_unknown_or_multiple_table_owners_fail():
    manifest = json.loads((ROOT / checker.MANIFEST).read_text())
    manifest["tables"].pop("hypermesh_devices")
    manifest["tables"]["invented"] = "platform"
    manifest["tables"]["users"] = ["platform", "products/hypermesh"]
    errors = checker.validate_manifest(ROOT, manifest)
    assert any("tables without owners" in error for error in errors)
    assert any("unknown ORM tables" in error for error in errors)
    assert any("users: one known owner" in error for error in errors)


def test_duplicate_manifest_keys_are_not_silently_overwritten():
    with pytest.raises(ValueError):
        json.loads('{"users":"platform","users":"products/hypermesh"}', object_pairs_hook=checker.unique_keys)


@pytest.mark.parametrize("version", ["0.1.0", "0.1.1", "0.0.9"])
def test_planted_signature_change_requires_minor_release(version, tmp_path):
    source = tmp_path / "contract.py"
    source.write_text("def verify(token: str) -> bool:\n    return True\n")
    before = {"version": "0.1.0", "modules": checker.module_exports(source, None)}
    source.write_text("def verify(token: str, tenant: str) -> bool:\n    return True\n")
    after = {"version": version, "modules": checker.module_exports(source, None)}
    assert checker.compare_release(before, after)
    after["version"] = "0.2.0"
    assert checker.compare_release(before, after) == []


def test_body_change_does_not_change_signature_and_defaults_are_redacted(tmp_path):
    source = tmp_path / "contract.py"
    source.write_text("def verify(token: str = 'never-write-this-value') -> bool:\n    return True\n")
    before = checker.module_exports(source, None)
    assert "never-write-this-value" not in json.dumps(before)
    source.write_text("def verify(token: str = 'never-write-this-value') -> bool:\n    return False\n")
    assert checker.module_exports(source, None) == before
    source.write_text("def verify(token: str = 'different') -> bool:\n    return False\n")
    assert checker.module_exports(source, None) != before


def test_private_base_fields_are_part_of_exported_class_contract(tmp_path):
    source = tmp_path / "contract.py"
    source.write_text("class _Base:\n    tenant: str\nclass Payload(_Base):\n    device: str\n")
    before = checker.module_exports(source, None)
    source.write_text("class _Base:\n    tenant: int\nclass Payload(_Base):\n    device: str\n")
    assert checker.module_exports(source, None) != before
    assert "_Base" not in before


def test_different_release_train_versions_and_unassigned_public_modules_fail():
    manifest = json.loads((ROOT / checker.MANIFEST).read_text())
    manifest["libraries"]["controlplane-db"]["version"] = "0.7.0"
    manifest["libraries"]["controlplane-events"]["public_modules"].pop("controlplane_events.envelope")
    errors = checker.validate_manifest(ROOT, manifest)
    assert any("release-train" in error for error in errors)
    assert any("unassigned shared public modules" in error for error in errors)


def test_snapshot_check_rejects_updated_source_until_snapshot_is_regenerated(tmp_path, monkeypatch):
    source = tmp_path / "contract.py"
    source.write_text("def verify(token: str):\n    return token\n")
    manifest = {"libraries": {"example": {"version": "0.1.0", "import_name": "example", "source_roots": ["."], "public_modules": {"contract": None}}}}
    monkeypatch.setattr(checker, "validate_manifest", lambda root, manifest: [])
    assert checker.check(tmp_path, manifest, write=True) == []
    source.write_text("def verify(token: str, tenant: str):\n    return token\n")
    assert checker.check(tmp_path, manifest) == ["example: source API differs from checked-in snapshot"]


def test_regenerating_snapshot_cannot_hide_unversioned_change_from_git_base(tmp_path, monkeypatch):
    source = tmp_path / "contract.py"
    source.write_text("def verify(token: str):\n    return token\n")
    manifest = {"libraries": {"example": {"version": "0.1.0", "import_name": "example", "source_roots": ["."], "public_modules": {"contract": None}}}}
    monkeypatch.setattr(checker, "validate_manifest", lambda root, manifest: [])
    assert checker.check(tmp_path, manifest, write=True) == []
    (tmp_path / checker.MANIFEST).write_text(json.dumps(manifest))
    def git(*args):
        return subprocess.run(["git", *args], cwd=tmp_path, check=True, capture_output=True)
    git("init")
    git("add", ".")
    git("-c", "user.name=Contract Test", "-c", "user.email=contract@example.invalid", "commit", "-m", "baseline")
    source.write_text("def verify(token: str, tenant: str):\n    return token\n")
    assert checker.check(tmp_path, manifest, write=True) == []
    assert checker.check(tmp_path, manifest, base="HEAD") == ["example: API changed without a version bump"]
    manifest["libraries"]["example"]["version"] = "0.2.0"
    assert checker.check(tmp_path, manifest, write=True, base="HEAD") == []


@pytest.mark.parametrize("declaration", ['__tablename__ = "new_table"', '__tablename__: str = "new_table"'])
def test_new_table_requires_owner_for_plain_and_annotated_declarations(tmp_path, declaration):
    source = tmp_path / "core/lib/controlplane-db/src/controlplane_db/models.py"
    source.parent.mkdir(parents=True)
    source.write_text("class NewModel:\n    " + declaration + "\n")
    assert checker.table_names(tmp_path) == {"new_table"}
    manifest = json.loads((ROOT / checker.MANIFEST).read_text())
    manifest["model_sources"] = {"platform": "core/lib/controlplane-db/src/controlplane_db/models.py"}
    # Preserve the real import contract while exercising a newly planted ORM table.
    contract = tmp_path / "scripts/import_boundaries.json"
    contract.parent.mkdir(parents=True)
    contract.write_text((ROOT / "scripts/import_boundaries.json").read_text())
    assert any("tables without owners" in error and "new_table" in error
               for error in checker.validate_manifest(tmp_path, manifest))


def test_model_source_owner_must_match_declared_table_owner():
    manifest = json.loads((ROOT / checker.MANIFEST).read_text())
    manifest["tables"]["hypermesh_devices"] = "platform"
    assert "hypermesh_devices: declared owner differs from model source owner" in checker.validate_manifest(ROOT, manifest)
