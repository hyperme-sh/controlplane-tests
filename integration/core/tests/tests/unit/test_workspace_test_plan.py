"""Exercise the actual workflow filters and resulting runner selection."""
import fnmatch
import importlib.util
from pathlib import Path
import subprocess

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[4]
spec = importlib.util.spec_from_file_location("workspace_test_plan", ROOT / "scripts/workspace_test_plan.py")
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
workflow = yaml.safe_load((ROOT / ".github/workflows/test.yml").read_text())
filter_step = next(step for step in workflow["jobs"]["changes"]["steps"] if step.get("id") == "filter")
FILTERS = yaml.safe_load(filter_step["with"]["filters"])


def selection(*paths):
    names = [name for name, patterns in FILTERS.items()
             if any(fnmatch.fnmatchcase(path, pattern) for path in paths for pattern in patterns)]
    return module.plan(names)


def test_shared_markdown_changes_keep_full_run():
    # AGENTS.md: shared changes run everything, including documentation-only edits.
    assert selection("core/lib/controlplane-db/README.md") == (True, [])
    assert selection("core/lib/controlplane-db/README.md", "products/turn/src/x.py") == (True, [])


def test_hypermesh_change_selects_family_only():
    enabled, selectors = selection("products/hypermesh/src/hypermesh_service/seed.py")
    assert enabled
    result = subprocess.run(["bash", str(ROOT / "scripts/run_workspace_tests.sh"), "--list", *selectors],
                            check=True, capture_output=True, text=True)
    assert result.stdout.splitlines() == ["products/hypermesh", "products/hypermesh-gateway", "products/hypermesh-ticket",
                                         "products/hypermesh-orchestrator"]


@pytest.mark.parametrize("path", ["core/lib/new-library/src/x.py", "core/events/x.py", "core/models/x.py",
                                  "core/auth-service/x.py", "pyproject.toml", "uv.lock", "proto/x.proto",
                                  "scripts/run_workspace_tests.sh", ".github/workflows/test.yml",
                                  "docker-compose.vm.yaml", "infra/keycloak/seed_pan_test_identities.py"])
def test_shared_changes_run_everything(path):
    assert selection(path, "products/turn/src/x.py") == (True, [])


def test_multiple_products_and_frontend_select_union():
    assert selection("products/turn/src/x.py", "products/aiuditor/src/x.py", "frontend/app.ts") == (
        True, ["products/aiuditor", "products/turn"])


def test_docs_or_frontend_alone_skip_python():
    assert selection("README.md") == (False, [])
    assert selection("frontend/app.ts") == (False, [])


def test_all_products_and_filter_names_have_planner_entries():
    assert set(FILTERS) == {*module.PRODUCTS, "shared", "frontend"}
    for directory in (ROOT / "products").iterdir():
        if directory.is_dir() and (directory / "pyproject.toml").exists():
            assert selection(f"products/{directory.name}/src/example.py")[0]


@pytest.mark.parametrize("bad", [["unknown"], ["turn;echo bad"], "shared", [None]])
def test_invalid_filter_input_fails_closed(bad):
    with pytest.raises(ValueError):
        module.plan(bad)


def test_runner_labels_remain_private_self_hosted():
    assert all(job["runs-on"] == ["self-hosted", "linux", "x64"] for job in workflow["jobs"].values())


def test_edge_and_hypermesh_select_only_affected_suites():
    enabled, selectors = selection("controlplane-edge/src/controlplane_edge/public_hypermesh_browse.py",
                                   "products/hypermesh/src/hypermesh_service/updates/source.py")
    assert enabled
    result = subprocess.run(["bash", str(ROOT / "scripts/run_workspace_tests.sh"), "--list", *selectors],
                            check=True, capture_output=True, text=True)
    assert result.stdout.splitlines() == ["controlplane-edge", "products/hypermesh",
                                         "products/hypermesh-gateway", "products/hypermesh-ticket", "products/hypermesh-orchestrator"]
    assert selection("controlplane-edge/src/controlplane_edge/edge_proxy.py") == (True, ["controlplane-edge"])


@pytest.mark.parametrize("path", [
    "products/hypermesh/contracts/src/host_binding_store/__init__.py",
    "products/hypermesh/contracts/pyproject.toml",
    "products/hypermesh/contracts/AGENTS.md",
])
def test_host_binding_contract_changes_run_owning_and_consumer_suites(path):
    enabled, selectors = selection(path)
    assert enabled and selectors == []  # All suites include shared owner tests and auth enrollment.


def test_shared_matrix_covers_each_runner_suite_once():
    entries = module.matrix(["shared"])["include"]
    actual = [suite for entry in entries for suite in entry["suites"].split()]
    expected = subprocess.check_output(["bash", str(ROOT / "scripts/run_workspace_tests.sh"), "--list"], text=True).splitlines()
    assert sorted(actual) == sorted(expected)
    assert len(entries) == 6
    assert all(entry["suites"] for entry in entries)


def test_product_matrix_keeps_exact_selection():
    assert module.matrix(["frontend"]) == {"include": []}
    assert module.matrix([]) == {"include": []}
    assert module.matrix(["turn", "aiuditor"]) == {"include": [
        {"group": "other-products", "suites": "products/aiuditor products/turn"}]}
    entries = module.matrix(["hypermesh", "edge"])["include"]
    assert {e["group"] for e in entries} == {"hypermesh", "other-products"}
    assert next(e for e in entries if e["group"] == "other-products")["suites"] == "controlplane-edge"


def test_unassigned_suite_fails_closed(monkeypatch):
    monkeypatch.setattr(module, "GROUPS", {})
    with pytest.raises(ValueError):
        module.matrix(["shared"])


def test_duplicate_suite_fails_closed(monkeypatch):
    monkeypatch.setattr(module, "GROUPS", {**module.GROUPS, "duplicate": ["core/tests"]})
    with pytest.raises(ValueError):
        module.matrix(["shared"])


@pytest.mark.parametrize("changes,enabled,result,accepted", [
    ("success", "true", "success", True), ("success", "false", "skipped", True),
    ("success", "true", "failure", False), ("success", "true", "cancelled", False),
    ("success", "true", "skipped", False), ("failure", "false", "skipped", False),
    ("cancelled", "true", "success", False), ("success", "", "skipped", False),
])
def test_aggregate_gate_fails_closed(changes, enabled, result, accepted):
    import os
    job = workflow["jobs"]["workspace"]
    assert job["name"] == "Workspace pytest"
    assert job["if"] == "${{ always() }}"
    assert job["needs"] == ["changes", "workspace-suite"]
    command = job["steps"][0]["run"]
    ran = subprocess.run(["bash", "-c", command], env={**os.environ,
        "CHANGES_RESULT": changes, "WORKSPACE_ENABLED": enabled, "SUITE_RESULT": result})
    assert (ran.returncode == 0) == accepted


def test_matrix_keeps_failures_independent_and_limits_host_load():
    job = workflow["jobs"]["workspace-suite"]
    assert job["strategy"]["fail-fast"] is False
    assert job["strategy"]["max-parallel"] == 2
    assert job["timeout-minutes"] == 30
    assert job["strategy"]["matrix"] == "${{ fromJSON(needs.changes.outputs.matrix) }}"
    cache = next(step["with"]["cache-local-path"] for step in job["steps"] if "cache-local-path" in step.get("with", {}))
    assert "matrix.group" in cache
