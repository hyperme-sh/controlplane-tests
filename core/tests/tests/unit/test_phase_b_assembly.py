"""Assembly copies observations; corrupt or incomplete declarations produce no bundle."""

import hashlib
import importlib.util
import json
import os
import sys
from copy import deepcopy
from pathlib import Path

import pytest
import test_phase_b_evidence as evidence

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT / "scripts"))
spec = importlib.util.spec_from_file_location(
    "assemble_phase_b_evidence", ROOT / "scripts/assemble_phase_b_evidence.py"
)
assembly = importlib.util.module_from_spec(spec)
spec.loader.exec_module(assembly)


def pin(path):
    return {"path": path.name, "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}


def save(path, data):
    path.write_text(json.dumps(data))
    return pin(path)


@pytest.fixture
def packet(tmp_path):
    original = evidence.bundle.__wrapped__()
    base = deepcopy(original)
    row = base["stripe_runs"][0]
    # The exact projected keys emitted by retained transfer/bank/refund collectors.
    groups = [
        (
            "payment_transfer_owner_collection",
            [
                "connect_dispatch",
                "readiness",
                "connect_transfer",
                "transfer_inventory",
            ],
        ),
        (
            "payment_bank_inventory_collection",
            [
                "bank_dispatch_plan",
                "bank_dispatch_checkpoint",
                "bank_dispatch_attempts",
                "payout_readiness",
                "platform_payout_inventory",
            ],
        ),
        ("payment_bank_funding_collection", ["bank_funding"]),
        ("payment_bank_followup_collection", ["bank_followup"]),
        ("payment_refund_owner_collection", ["refund"]),
    ]
    imports = []
    for kind, names in groups:
        collected = {"source": kind, "schema_version": 1, "revision": evidence.REVISION}
        collected.update({name: row.pop(name) for name in names})
        reference = save(tmp_path / (kind + ".json"), collected)
        imports.extend(
            {
                "artifact": reference,
                "pointer": "/" + name,
                "target": "/stripe_runs/0/" + name,
            }
            for name in names
        )
    manifest = {
        "schema_version": 1,
        "revision": evidence.REVISION,
        "base": save(tmp_path / "base.json", base),
        "imports": imports,
    }
    path = tmp_path / "manifest.json"
    save(path, manifest)
    return path, manifest, original


def test_collector_records_assemble_into_complete_canonical_bundle(packet):
    path, manifest, original = packet
    before = {p: p.read_bytes() for p in path.parent.glob("*.json")}
    first = assembly.assemble(path)
    assert evidence.check(first) == []
    provenance = first.pop("assembly_provenance")
    assert first == original
    assert provenance["imports"] == manifest["imports"]
    assert provenance["external_artifacts_verified"] is False
    assert (
        provenance["manifest_sha256"] == hashlib.sha256(path.read_bytes()).hexdigest()
    )
    assert {p: p.read_bytes() for p in before} == before
    assert assembly.assemble(path) == assembly.assemble(path)


@pytest.mark.parametrize(
    "mutation",
    [
        "changed_bytes",
        "mixed_revision",
        "wrong_pin",
        "missing_pin",
        "absolute_path",
        "outside_path",
        "symlink_escape",
        "duplicate_target",
        "base_collision",
        "identity_overwrite",
        "missing_pointer",
        "negative_index",
        "aliased_index",
        "append_index",
        "null_record",
        "wrong_scope",
        "late_failure",
        "future_revision",
    ],
)
def test_invalid_or_nonpassing_inputs_never_write_bundle(
    packet, tmp_path, monkeypatch, mutation
):
    path, manifest, _ = packet
    entry = manifest["imports"][0]
    artifact = tmp_path / entry["artifact"]["path"]
    data = json.loads(artifact.read_text())
    if mutation == "changed_bytes":
        artifact.write_text(artifact.read_text() + " ")
    elif mutation == "mixed_revision":
        data["retained_raw"] = {"revision": "b" * 40}
        entry["artifact"] = save(artifact, data)
    elif mutation == "wrong_pin":
        entry["artifact"]["sha256"] = "0" * 64
    elif mutation == "missing_pin":
        entry["artifact"].pop("sha256")
    elif mutation == "absolute_path":
        entry["artifact"]["path"] = str(artifact)
    elif mutation in ("outside_path", "symlink_escape"):
        outside = tmp_path.parent / ("outside-" + tmp_path.name + ".json")
        outside.write_bytes(artifact.read_bytes())
        if mutation == "outside_path":
            entry["artifact"]["path"] = "../" + outside.name
        else:
            link = tmp_path / "escape.json"
            link.symlink_to(outside)
            entry["artifact"]["path"] = link.name
    elif mutation == "duplicate_target":
        manifest["imports"].append(deepcopy(entry))
    elif mutation == "base_collision":
        entry["target"] = "/stripe_runs/0/payment"
    elif mutation == "identity_overwrite":
        entry["target"] = "/revision"
    elif mutation == "missing_pointer":
        entry["pointer"] = "/absent"
    elif mutation in ("negative_index", "aliased_index", "append_index"):
        token = {"negative_index": "-1", "aliased_index": "00", "append_index": "-"}[
            mutation
        ]
        entry["target"] = "/stripe_runs/" + token + "/connect_dispatch"
    elif mutation == "null_record":
        data["connect_dispatch"] = None
        entry["artifact"] = save(artifact, data)
    elif mutation == "wrong_scope":
        data["connect_transfer"]["account_id"] = "acct_other"
        # Repin all references to this changed collector output.
        reference = save(artifact, data)
        for item in manifest["imports"]:
            if item["artifact"]["path"] == reference["path"]:
                item["artifact"] = reference
    elif mutation == "late_failure":
        ref = next(
            e["artifact"]
            for e in manifest["imports"]
            if e["pointer"] == "/bank_followup"
        )
        followup_path = tmp_path / ref["path"]
        followup = json.loads(followup_path.read_text())
        followup["bank_followup"]["later_failure_seen"] = True
        ref.update(save(followup_path, followup))
    else:
        manifest["revision"] = "b" * 40
    save(path, manifest)
    monkeypatch.setattr(assembly, "validate", lambda data, root: evidence.check(data))
    output = tmp_path / "assembled.json"
    try:
        result = assembly.main([str(path), str(output)])
    except SystemExit as exc:
        result = exc.code
    assert result in (1, 2)
    assert not output.exists()
    assert not list(tmp_path.glob(".phase-b-*"))


@pytest.mark.parametrize(
    "bad",
    [
        '{"x":1,"x":2}',
        '{"x":NaN}',
        '{"x":Infinity}',
        '{"x":-Infinity}',
        '{"x":1e10000}',
    ],
)
def test_strict_json_rejects_ambiguous_inputs(bad):
    with pytest.raises(ValueError):
        assembly.decode(bad)


def test_private_atomic_output_and_existing_target_are_preserved(
    packet, tmp_path, monkeypatch
):
    path, _, _ = packet
    monkeypatch.setattr(assembly, "validate", lambda data, root: evidence.check(data))
    output = tmp_path / "assembled.json"
    assert assembly.main([str(path), str(output)]) == 0
    payload = output.read_bytes()
    assert os.stat(output).st_mode & 0o777 == 0o600
    with pytest.raises(SystemExit) as exc:
        assembly.main([str(path), str(output)])
    assert exc.value.code == 2 and output.read_bytes() == payload
    assert not list(tmp_path.glob(".phase-b-*"))


def test_incomplete_base_is_not_filled_with_inferred_acceptance(
    packet, tmp_path, monkeypatch
):
    path, manifest, _ = packet
    base_path = tmp_path / "base.json"
    base = json.loads(base_path.read_text())
    del base["test_credit_removed"]
    manifest["base"] = save(base_path, base)
    save(path, manifest)
    monkeypatch.setattr(assembly, "validate", lambda data, root: evidence.check(data))
    output = tmp_path / "assembled.json"
    assert assembly.main([str(path), str(output)]) == 1
    assert not output.exists()


@pytest.mark.parametrize(
    "source_problem", ["dirty", "changed_head", "exception", "boundary"]
)
def test_source_authority_and_boundary_errors_prevent_publication(
    packet, tmp_path, monkeypatch, source_problem
):
    path, _, _ = packet
    root = tmp_path / "source"
    (root / "scripts").mkdir(parents=True)
    ownership = {"exceptions": []}
    if source_problem == "exception":
        ownership["exceptions"] = [
            {
                "name": "INV-EXAMPLE",
                "issue": "https://github.com/FyberLabs/panopticon/issues/299",
            }
        ]
    save(root / "scripts/ownership.json", ownership)
    calls = []

    def source_revision(root):
        calls.append(root)
        if source_problem == "dirty":
            raise ValueError("dirty source")
        return (
            "b" * 40
            if source_problem == "changed_head" and len(calls) > 1
            else evidence.REVISION
        )

    monkeypatch.setattr(assembly.checker, "source_revision", source_revision)
    monkeypatch.setattr(
        assembly.checker,
        "source_commit_time",
        lambda root, revision: evidence.COMMIT_TIME,
    )
    original = assembly.checker.check_evidence
    monkeypatch.setattr(
        assembly.checker,
        "check_evidence",
        lambda data, **kw: original(
            data, **kw, now=evidence.datetime(2026, 10, 26, 12, tzinfo=evidence.UTC)
        ),
    )
    monkeypatch.setattr(
        assembly.checker,
        "violations",
        lambda root, ownership: (
            ["boundary violation"] if source_problem == "boundary" else []
        ),
    )
    monkeypatch.setattr(assembly, "ROOT", root)
    output = tmp_path / "assembled.json"
    try:
        result = assembly.main([str(path), str(output)])
    except SystemExit as exc:
        result = exc.code
    assert result in (1, 2)
    assert not output.exists()


def test_json_pointer_escape_is_literal_not_a_path_operation():
    assert assembly.child({"a/b~c": 7}, assembly.tokens("/a~1b~0c")[0]) == 7
