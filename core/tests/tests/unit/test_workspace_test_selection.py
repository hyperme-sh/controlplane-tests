"""Exercise the shell runner without launching nested pytest or Docker."""
import os
from pathlib import Path
import subprocess

import pytest

ROOT = Path(__file__).resolve().parents[4]
SCRIPT = ROOT / "scripts/run_workspace_tests.sh"
ALL = [
    "core/tests", "controlplane-edge", "core/auth-service", "core/user-service",
    "core/acl-service", "core/payment-service", "core/marketplace-service",
    "core/ai-service", "core/api-keys-service", "products/template",
    "products/tennessee-windage", "products/hypermesh", "products/hypermesh-gateway",
    "products/hypermesh-orchestrator", "products/aiuditor", "products/turn",
    "products/oracles", "products/payments", "core/e2e-tests",
]


@pytest.fixture
def runner(tmp_path):
    log = tmp_path / "calls"
    uv = tmp_path / "uv"
    uv.write_text('#!/bin/bash\nprintf "%s|%s|%s\\n" "$PWD" "$*" "$SKIP_TESTCONTAINERS" >> "$RUNNER_TEST_LOG"\nif [[ "$PWD" == */"${RUNNER_FAIL_SUITE:-no-such-suite}" ]]; then exit 17; fi\n')
    uv.chmod(0o755)

    def run(*args, fail=""):
        log.write_text("")
        env = os.environ | {"PATH": str(tmp_path) + os.pathsep + os.environ["PATH"],
                            "RUNNER_TEST_LOG": str(log), "RUNNER_FAIL_SUITE": fail,
                            "SKIP_TESTCONTAINERS": "1"}
        result = subprocess.run(["bash", str(SCRIPT), *args], cwd=tmp_path,
                                env=env, capture_output=True, text=True)
        calls = [line.split("|") for line in log.read_text().splitlines()]
        return result, calls
    return run


def paths(calls):
    return [str(Path(row[0]).relative_to(ROOT)) for row in calls]


def test_no_arguments_preserves_full_order_and_commands(runner):
    result, calls = runner()
    assert result.returncode == 0
    assert paths(calls) == ALL
    assert calls[0][1] == "run --with pytest-cov pytest -q"
    assert calls[11][1] == "run --with pytest-cov pytest --cov=hypermesh_service --cov-report=term-missing -q"
    assert all(row[2] == "1" for row in calls)


def test_family_includes_e2e_and_deduplicates(runner):
    result, calls = runner("hypermesh-family", "products/hypermesh")
    assert result.returncode == 0
    assert paths(calls) == ["products/hypermesh", "products/hypermesh-gateway",
                            "products/hypermesh-orchestrator", "core/e2e-tests"]


def test_explicit_suites_use_canonical_order(runner):
    result, calls = runner("products/turn", "core/auth-service")
    assert result.returncode == 0
    assert paths(calls) == ["core/auth-service", "products/turn"]


@pytest.mark.parametrize("bad", ["", "unknown", "../core/tests", "products/*", "--bad"])
def test_invalid_selection_runs_nothing(runner, bad):
    result, calls = runner("core/tests", bad)
    assert result.returncode == 2
    assert calls == []


def test_list_does_not_invoke_uv(runner):
    result, calls = runner("--list")
    assert result.returncode == 0
    assert result.stdout.splitlines() == ALL
    assert calls == []


def test_failure_stops_later_suites_and_preserves_exit(runner):
    result, calls = runner("hypermesh-family", fail="products/hypermesh-gateway")
    assert result.returncode == 17
    assert paths(calls) == ["products/hypermesh", "products/hypermesh-gateway"]
    assert "OK:" not in result.stdout
