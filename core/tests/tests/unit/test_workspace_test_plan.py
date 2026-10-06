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
    shared = [path for path in paths if any(fnmatch.fnmatchcase(path, pattern) for pattern in FILTERS["shared"])]
    return module.plan(names, shared)


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


def test_owner_revision_selects_only_owner_and_other_changed_product():
    revision = "core/lib/controlplane-db/src/controlplane_db/migration_chains/hypermesh/versions/0054_model_cache_capacity.py"
    assert selection(revision, "products/aiuditor/src/x.py") == (True, ["hypermesh-family", "products/aiuditor"])
    assert selection(revision, "core/lib/controlplane-db/src/controlplane_db/session.py") == (True, [])
    assert selection(revision.replace("0054_model_cache_capacity.py", "0053_baseline.py")) == (True, [])
