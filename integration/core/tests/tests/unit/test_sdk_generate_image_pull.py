"""SDK generate must retry a digest-pinned openapi-generator pull."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[4]
WORKFLOW = REPO_ROOT / ".github" / "workflows" / "sdk-generate.yml"
# v7.10.0 manifest list from registry-1.docker.io.
PINNED_DIGEST = (
    "sha256:f2054a5a7908ad81017d0f0839514ba5eab06ae628914ff71554d46fac1bcf7a"
)


def test_sdk_job_retries_pinned_generator_image() -> None:
    workflow = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    job = workflow["jobs"]["sdk"]
    assert job["runs-on"] == ["self-hosted", "linux", "x64"]
    step = next(
        step
        for step in job["steps"]
        if step.get("name") == "REST SDKs (openapi-generator)"
    )
    script = step["run"]
    image = f"openapitools/openapi-generator-cli@{PINNED_DIGEST}"
    assert image in script
    assert "export OPENAPI_GENERATOR_IMAGE=" in script
    assert "for attempt in 1 2 3" in script
    assert script.count('docker pull "$IMAGE"') == 1
    assert 'docker pull "$IMAGE" 2>"$err"' in script
    assert "sleep $((attempt * 5))" in script
    transient = (
        "lease does not exist|TLS handshake timeout|i/o timeout|"
        "connection reset|unexpected EOF|toomanyrequests|50[234]"
    )
    assert transient in script
    pull_loop = script.split("for attempt in 1 2 3", 1)[1]
    assert 'docker pull "$IMAGE"' in pull_loop
    assert pull_loop.index("not retrying") < pull_loop.index("sleep $((attempt * 5))")


def _pull_script() -> str:
    workflow = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    step = next(
        step
        for step in workflow["jobs"]["sdk"]["steps"]
        if step.get("name") == "REST SDKs (openapi-generator)"
    )
    return step["run"]


def _run_pull(tmp_path: Path, docker_body: str) -> subprocess.CompletedProcess[str]:
    bindir = tmp_path / "bin"
    bindir.mkdir()
    docker = bindir / "docker"
    docker.write_text("#!/bin/sh\n" + docker_body, encoding="utf-8")
    docker.chmod(0o755)
    sleep = bindir / "sleep"
    sleep.write_text(
        '#!/bin/sh\necho "$1" >> "$SLEEP_LOG"\n',
        encoding="utf-8",
    )
    sleep.chmod(0o755)
    uv = bindir / "uv"
    uv.write_text('#!/bin/sh\ntouch "$UV_RAN"\n', encoding="utf-8")
    uv.chmod(0o755)
    env = os.environ.copy()
    env["PATH"] = f"{bindir}{os.pathsep}{env.get('PATH', '')}"
    env["SLEEP_LOG"] = str(tmp_path / "sleeps")
    env["UV_RAN"] = str(tmp_path / "uv-ran")
    env["PULL_COUNT"] = str(tmp_path / "pulls")
    return subprocess.run(
        ["bash", "-c", _pull_script()],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )


def test_pull_retries_transient_lease_error(tmp_path: Path) -> None:
    proc = _run_pull(
        tmp_path,
        """
count=0
if [ -f "$PULL_COUNT" ]; then count=$(cat "$PULL_COUNT"); fi
count=$((count + 1))
echo "$count" > "$PULL_COUNT"
if [ "$count" -eq 1 ]; then
  echo "failed to commit snapshot: NotFound: lease does not exist" >&2
  exit 1
fi
exit 0
""",
    )
    assert proc.returncode == 0, proc.stderr
    assert (tmp_path / "pulls").read_text(encoding="utf-8").strip() == "2"
    assert (tmp_path / "sleeps").read_text(encoding="utf-8").strip() == "5"
    assert (tmp_path / "uv-ran").is_file()
    assert "lease does not exist" in proc.stderr


def test_pull_fails_fast_on_non_transient_error(tmp_path: Path) -> None:
    proc = _run_pull(
        tmp_path,
        """
count=0
if [ -f "$PULL_COUNT" ]; then count=$(cat "$PULL_COUNT"); fi
count=$((count + 1))
echo "$count" > "$PULL_COUNT"
echo "Error response from daemon: manifest unknown" >&2
exit 1
""",
    )
    assert proc.returncode == 1
    assert (tmp_path / "pulls").read_text(encoding="utf-8").strip() == "1"
    assert not (tmp_path / "sleeps").exists()
    assert not (tmp_path / "uv-ran").exists()
    assert "manifest unknown" in proc.stderr
    assert "not retrying" in proc.stderr


@pytest.mark.parametrize(
    "message",
    [
        "TLS handshake timeout",
        "i/o timeout",
        "connection reset by peer",
        "unexpected EOF",
        "toomanyrequests: too many requests",
        "received status code 502",
        "HTTP 503",
        "error 504",
    ],
)
def test_transient_pattern_matches_known_flakes(message: str) -> None:
    script = _pull_script()
    pattern = script.split("TRANSIENT=", 1)[1].split("\n", 1)[0].strip().strip("'")
    proc = subprocess.run(
        ["grep", "-E", "-q", pattern, "-"],
        input=message,
        text=True,
        check=False,
    )
    assert proc.returncode == 0
