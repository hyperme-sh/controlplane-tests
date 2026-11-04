"""Release checks reject private source and redact findings."""

import importlib.util
from pathlib import Path
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[4]
spec = importlib.util.spec_from_file_location(
    "check_spanopticon_snapshot", ROOT / "scripts/check_spanopticon_snapshot.py"
)
checker = importlib.util.module_from_spec(spec)
spec.loader.exec_module(checker)


def write(root, name, text):
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)


@pytest.mark.parametrize("path", [
    "core/payment-service", "products/aiuditor", "products/payments",
    "infra/keycloak", ".git", "frontend", "sdk",
    "core/lib/controlplane-db/src/controlplane_db/alembic/versions",
])
def test_private_directory_copy_is_rejected(tmp_path, path):
    write(tmp_path, path + "/file.txt", "otherwise safe")
    assert f"{path}: private-path" in checker.findings(tmp_path)


@pytest.mark.parametrize(("source", "rule"), [
    ("endpoint = 'https://api.test.hyperme.sh'", "test-plane-hostname"),
    ("ADMIN_EMAIL = 'chris@fyberlabs.com'", "private-mailbox"),
    ("secret_name = 'panopticon-test-idp-google-secret'", "vault-name"),
    ("image = 'ghcr.io/fyberlabs/template-service'", "private-registry"),
    ("FYBER_AGX_DEVICE_ID = 'opaque-id'", "seed-identity"),
    ("runs-on: [self-hosted, linux, x64]", "private-ci"),
    ("from stripe.checkout import Session", "private-import"),
    ("import payments_service as commerce", "private-import"),
    ("from controlplane_db.models import BillingRecord", "private-model"),
    ("stripe_session_id = 'opaque'", "commerce-field"),
])
def test_findings_do_not_echo_matched_values(tmp_path, source, rule):
    write(tmp_path, "probe.py" if rule != "private-ci" else ".github/workflows/ci.yml", source)
    messages = checker.findings(tmp_path)
    assert any(message.endswith(rule) for message in messages)
    assert source not in "\n".join(messages)


def test_untracked_hidden_secrets_binary_and_links_are_checked(tmp_path):
    write(tmp_path, ".env.local", "TOKEN=do-not-echo")
    (tmp_path / "private.bin").write_bytes(b"\0secret")
    (tmp_path / "escape").symlink_to("/tmp")
    messages = checker.findings(tmp_path)
    assert ".env.local: environment-file" in messages
    assert "escape: symlink" in messages
    assert any(message.startswith("private.bin:") for message in messages)
    assert "do-not-echo" not in "\n".join(messages)


def test_clean_operator_snapshot_passes_and_cli_fails_on_private_content(tmp_path):
    write(tmp_path, "service.py", "from controlplane_events.envelope import ControlPlaneEventEnvelope")
    write(tmp_path, "compose.yaml", "services: {}")
    assert checker.findings(tmp_path) == []
    command = [sys.executable, str(ROOT / "scripts/check_spanopticon_snapshot.py"), str(tmp_path)]
    assert subprocess.run(command, capture_output=True).returncode == 0
    write(tmp_path, ".github/workflows/ci.yml", "runs-on: self-hosted")
    assert subprocess.run(command, capture_output=True).returncode == 1


def test_missing_snapshot_fails_closed(tmp_path):
    assert checker.findings(tmp_path / "absent") == [".: invalid-snapshot-root"]


def test_renaming_a_copied_seed_constant_does_not_hide_its_identity(tmp_path):
    values = checker._private_seed_ids()
    assert values
    identity = next(iter(values))
    write(tmp_path, "demo.py", f"DEMO_DEVICE = '{identity}'")
    assert "demo.py:1: private-seed-uuid" in checker.findings(tmp_path)
    assert identity not in "\n".join(checker.findings(tmp_path))


def test_public_voice_snapshot_mode_uses_release_gate(tmp_path):
    write(tmp_path, ".github/workflows/ci.yml", "runs-on: self-hosted")
    result = subprocess.run(
        ["bash", str(ROOT / "scripts/check-public-voice.sh"), str(tmp_path)],
        capture_output=True, text=True,
    )
    assert result.returncode == 1
    assert "private-ci" in result.stdout


@pytest.mark.parametrize("value", ["plain-secret", "${POSTGRES_PASSWORD:-plain-secret}"])
def test_compose_credential_values_are_rejected_without_echoing_them(tmp_path, value):
    write(tmp_path, "compose.yaml", f"POSTGRES_PASSWORD: '{value}'")
    messages = checker.findings(tmp_path)
    assert "compose.yaml:1: committed-credential" in messages
    assert value not in "\n".join(messages)


@pytest.mark.parametrize("value", ["", "${POSTGRES_PASSWORD}", "${POSTGRES_PASSWORD:-}", "${POSTGRES_PASSWORD:?Set a password}"])
def test_runtime_supplied_credentials_are_allowed(tmp_path, value):
    write(tmp_path, "compose.yaml", f"POSTGRES_PASSWORD: '{value}'")
    assert checker.findings(tmp_path) == []


@pytest.mark.parametrize("key", ['"POSTGRES_PASSWORD"', "'POSTGRES_PASSWORD'", "POSTGRES_PASSWORD"])
@pytest.mark.parametrize("value", ["plaintext", "", "${POSTGRES_PASSWORD}", "${POSTGRES_PASSWORD:-secret}"])
def test_quoted_credential_keys_and_json_values(tmp_path, key, value):
    write(tmp_path, "config.json", '{' + key + ': "' + value + '"}')
    messages = checker.findings(tmp_path)
    assert any("committed-credential" in message for message in messages) == (value in {"plaintext", "${POSTGRES_PASSWORD:-secret}"})
    assert "plaintext" not in "\n".join(messages)


@pytest.mark.parametrize("name,data", [
    ("artifact.pdf", b"%PDF-1.4\n1 0 obj << /Type /Catalog >> endobj\n%%EOF"),
    ("disguised.txt", b"%PDF-1.4\n%%EOF"),
    ("artifact.pdf", b"ASCII metadata only"),
    ("artifact.unknown", b"unclassified artifact"),
    ("control.txt", b"hidden\x01content"),
])
def test_utf8_binary_and_unknown_artifacts_require_review(tmp_path, name, data):
    (tmp_path / name).write_bytes(data)
    assert checker.findings(tmp_path) == [f"{name}: unreadable-or-binary (manual-review-required)"]
