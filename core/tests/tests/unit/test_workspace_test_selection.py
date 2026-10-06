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
    "products/tennessee-windage", "products/hypermesh", "products/hypermesh-gateway", "products/hypermesh-ticket",
    "products/hypermesh-orchestrator", "products/aiuditor", "products/turn",
    "products/oracles", "products/payments", "core/e2e-tests",
]


@pytest.fixture
def runner(tmp_path):
    log = tmp_path / "calls"
    uv = tmp_path / "uv"
    uv.write_text('#!/bin/bash\nprintf "%s|%s|%s\\n" "$PWD" "$*" "$SKIP_TESTCONTAINERS" >> "$RUNNER_TEST_LOG"\nif [[ "$PWD" == */"${RUNNER_FAIL_SUITE:-no-such-suite}" ]]; then exit 17; fi\n')
    uv.chmod(0o755)

    def run(*args, fail="", extra_env=None):
        log.write_text("")
        # CI runs this suite with its own runner settings; start from the script defaults.
        base = {k: v for k, v in os.environ.items()
                if k not in {"WORKSPACE_TESTS_SKIP_SYNC", "PYTEST_MAX_WORKERS", "PYTEST_DURATIONS"}}
        env = base | {"PATH": str(tmp_path) + os.pathsep + os.environ["PATH"],
                      "RUNNER_TEST_LOG": str(log), "RUNNER_FAIL_SUITE": fail,
                      "SKIP_TESTCONTAINERS": "1"} | (extra_env or {})
        result = subprocess.run(["bash", str(SCRIPT), *args], cwd=tmp_path,
                                env=env, capture_output=True, text=True)
        rows = [line.split("|") for line in log.read_text().splitlines()]
        syncs = [row for row in rows if row[1].startswith("sync ")]
        calls = [row for row in rows if not row[1].startswith("sync ")]
        run.syncs = syncs
        return result, calls
    return run


def paths(calls):
    return [str(Path(row[0]).relative_to(ROOT)) for row in calls]


XDIST = "-n auto --maxprocesses 4 --dist loadfile"
SERIAL = {"controlplane-edge", "core/api-keys-service", "products/template", "products/tennessee-windage",
          "products/hypermesh-gateway", "products/hypermesh-ticket", "products/hypermesh-orchestrator",
          "products/turn", "products/oracles", "products/payments", "core/e2e-tests"}


def test_no_arguments_preserves_full_order_and_commands(runner):
    result, calls = runner()
    assert result.returncode == 0
    assert paths(calls) == ALL
    # One sync up front; suites never re-resolve or install (no `uv run --with`).
    assert [row[1] for row in runner.syncs] == ["sync --frozen --all-packages --all-groups"]
    assert calls[0][1] == f"run --frozen --no-sync pytest -q --durations=25 {XDIST}"
    assert calls[11][1] == ("run --frozen --no-sync pytest --cov=hypermesh_service --cov-report=term-missing -q "
                            f"--durations=25 {XDIST}")
    assert all(row[2] == "1" for row in calls)
    for path, row in zip(paths(calls), calls):
        assert row[1].startswith("run --frozen --no-sync pytest ")
        assert row[1].endswith(XDIST) == (path not in SERIAL), path


def test_sync_can_be_skipped_and_xdist_disabled(runner):
    result, calls = runner("core/tests", extra_env={"WORKSPACE_TESTS_SKIP_SYNC": "1", "PYTEST_MAX_WORKERS": "1"})
    assert result.returncode == 0
    assert runner.syncs == []
    assert calls[0][1] == "run --frozen --no-sync pytest -q --durations=25"


def test_family_selects_hypermesh_packages_and_deduplicates(runner):
    # core/e2e-tests no longer imports hypermesh_service (WP-INV-08), so the family does not run it.
    result, calls = runner("hypermesh-family", "products/hypermesh")
    assert result.returncode == 0
    assert paths(calls) == ["products/hypermesh", "products/hypermesh-gateway", "products/hypermesh-ticket",
                            "products/hypermesh-orchestrator"]


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
    assert runner.syncs == []


def test_failure_stops_later_suites_and_preserves_exit(runner):
    result, calls = runner("hypermesh-family", fail="products/hypermesh-gateway")
    assert result.returncode == 17
    assert paths(calls) == ["products/hypermesh", "products/hypermesh-gateway"]
    assert "OK:" not in result.stdout
