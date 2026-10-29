"""Exercise the reusable workflow's real pin/authentication code, without credentials."""

import base64
import subprocess
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[4]
WORKFLOW = yaml.safe_load(
    (ROOT / ".github/workflows/controlplane-libs-sync.yml").read_text()
)
JOB = WORKFLOW["jobs"]["sync"]
STEP = next(
    s
    for s in JOB["steps"]
    if s.get("name") == "Verify tag and consumer lock, then sync"
)
PROGRAM = STEP["run"].split("python3 - <<'PY'\n", 1)[1].rsplit("\nPY", 1)[0]
NAMESPACE = {"__name__": "workflow_test"}
exec(compile(PROGRAM, "controlplane-libs-sync.yml", "exec"), NAMESPACE)  # noqa: S102 - fixed repository workflow, not user input


@pytest.fixture
def release(tmp_path):
    checkout = tmp_path / "library"
    checkout.mkdir()
    subprocess.run(["git", "init", "-q", str(checkout)], check=True)
    (checkout / "package").write_text("release")
    subprocess.run(["git", "add", "."], cwd=checkout, check=True)
    subprocess.run(
        [
            "git",
            "-c",
            "user.name=Test",
            "-c",
            "user.email=test@example.invalid",
            "commit",
            "-qm",
            "release",
        ],
        cwd=checkout,
        check=True,
    )
    sha = subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=checkout, text=True
    ).strip()
    lock = tmp_path / "uv.lock"
    lock.write_text(
        '[[package]]\nname="identity"\nsource={git="https://github.com/FyberLabs/controlplane-libs?tag=v0.21.0#'
        + sha
        + '"}\n'
    )
    return checkout, sha, lock


def test_real_checkout_and_consumer_lock_match(release):
    checkout, sha, lock = release
    NAMESPACE["validate"]("v0.21.0", sha, checkout, lock)


@pytest.mark.parametrize("kind", ["tag", "sha", "lock", "credentials", "absent"])
def test_bad_release_or_lock_refuses_before_sync(release, kind):
    checkout, sha, lock = release
    tag = "v0.21.0"
    if kind == "tag":
        tag = "main;echo unsafe"
    elif kind == "sha":
        sha = "f" * 40
    elif kind == "lock":
        lock.write_text(lock.read_text().replace(sha, "f" * 40))
    elif kind == "credentials":
        lock.write_text(
            lock.read_text().replace(
                "https://github.com", "https://user:secret@github.com"
            )
        )
    else:
        lock.write_text("package=[]\n")
    with pytest.raises(ValueError):
        NAMESPACE["validate"](tag, sha, checkout, lock)


def test_uv_gets_scoped_ephemeral_auth_not_token_arguments(monkeypatch, capsys):
    calls = []
    monkeypatch.setattr(
        subprocess, "run", lambda argv, **kwargs: calls.append((argv, kwargs))
    )
    monkeypatch.setenv("GH_LIBRARY_TOKEN", "fixture-token")
    NAMESPACE["sync"]("fixture-token")
    argv, kwargs = calls[0]
    assert argv == ["uv", "sync", "--frozen", "--no-dev"]
    env = kwargs["env"]
    assert "GH_LIBRARY_TOKEN" not in env
    assert env["GIT_CONFIG_COUNT"] == "2"
    assert (
        env["GIT_CONFIG_KEY_0"]
        == "http.https://github.com/FyberLabs/controlplane-libs.extraheader"
    )
    assert env["GIT_CONFIG_KEY_1"].endswith("controlplane-libs.git.extraheader")
    assert "fixture-token" not in capsys.readouterr().out
    assert env["GIT_CONFIG_VALUE_0"].endswith(
        base64.b64encode(b"x-access-token:fixture-token").decode()
    )


def test_private_main_only_and_read_only_build_secret():
    assert "github.event.repository.private == true" in JOB["if"]
    assert "github.ref == 'refs/heads/main'" in JOB["if"]
    assert JOB["environment"] == "controlplane-libs-read"
    mint = next(s for s in JOB["steps"] if s.get("id") == "libraries")
    assert mint["with"]["permission-contents"] == "read"
    assert mint["with"]["repositories"] == "controlplane-libs"
    assert all(
        s["with"]["persist-credentials"] is False
        for s in JOB["steps"]
        if s.get("uses", "").startswith("actions/checkout@")
    )
    build = next(
        s
        for s in JOB["steps"]
        if s.get("uses", "").startswith("docker/build-push-action@")
    )
    assert build["with"]["push"] is False
    assert "github_token=" in build["with"]["secrets"]
    assert "build-args" not in build["with"]
