"""Image manifest digests, and the wiring that bakes /version into every image."""

from __future__ import annotations

import ast
import sys
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[4]
WORKFLOW = REPO_ROOT / ".github" / "workflows" / "ghcr-test.yml"

APP_SOURCES = {
    "controlplane-edge": "core/lib/controlplane-core/src/api/__init__.py",
    "api-keys-service": "core/api-keys-service/src/api_keys_service/app.py",
    "auth-service": "core/auth-service/src/auth_service/app.py",
    "user-service": "core/user-service/src/user_service/app.py",
    "acl-service": "core/acl-service/src/acl_service/app.py",
    "payment-service": "core/payment-service/src/payment_service/app.py",
    "marketplace": "core/marketplace-service/src/marketplace_service/app.py",
    "ai-service": "core/ai-service/src/ai_service/app.py",
    "template-service": "products/template/src/template_service/app.py",
    "tennessee-windage-service": "products/tennessee-windage/src/tennessee_windage_service/app.py",
    "hypermesh-service": "products/hypermesh/src/hypermesh_service/app.py",
    "hypermesh-gateway": "products/hypermesh-gateway/src/hypermesh_gateway_service/app.py",
    "aiuditor-service": "products/aiuditor/src/aiuditor_service/app.py",
    "turn-service": "products/turn/src/turn_service/app.py",
    "oracles-service": "products/oracles/src/oracles_service/app.py",
    "payments-service": "products/payments/src/payments_service/app.py",
}


def _load_plan():
    scripts = str(REPO_ROOT / "scripts")
    if scripts not in sys.path:
        sys.path.insert(0, scripts)
    import ghcr_test_plan

    return ghcr_test_plan


plan = _load_plan()


def test_manifest_keeps_carried_digests_and_records_rebuilt_ones() -> None:
    carried = "sha256:" + "ab" * 32
    rebuilt = "sha256:" + "cd" * 32
    sha = "12" * 20
    baked = "ab" * 20
    document = plan.image_manifest_document(
        git_sha=sha,
        services=["auth-service", "controlplane-edge"],
        digests={"auth-service": carried, "controlplane-edge": rebuilt},
        identities={
            "auth-service": {"built_git_sha": baked, "built_at": "2026-09-26T17:00:00Z"},
            "controlplane-edge": {"built_git_sha": sha, "built_at": "2026-09-26T18:00:00Z"},
        },
        generated_at="2026-09-26T18:00:00Z",
    )
    rows = {row["service"]: row for row in document["services"]}
    assert rows["auth-service"]["digest"] == carried
    assert rows["auth-service"]["digest"] != sha
    assert rows["auth-service"]["built_git_sha"] == baked
    assert rows["auth-service"]["built_at"] == "2026-09-26T17:00:00Z"
    assert rows["auth-service"]["tag"] == sha
    assert rows["controlplane-edge"]["image"] == f"ghcr.io/fyberlabs/controlplane-edge@{rebuilt}"
    assert rows["controlplane-edge"]["tag"] == sha
    assert rows["controlplane-edge"]["built_git_sha"] == sha
    assert [row["service"] for row in document["services"]] == [
        "auth-service",
        "controlplane-edge",
    ]


def _identity(sha: str, stamp: str = "2026-09-26T18:00:00Z") -> dict[str, str]:
    return {"built_git_sha": sha, "built_at": stamp}


def test_manifest_refuses_a_missing_or_non_digest_value() -> None:
    sha = "12" * 20
    with pytest.raises(SystemExit, match="missing digests"):
        plan.image_manifest_document(
            git_sha=sha,
            services=["auth-service", "acl-service"],
            digests={"auth-service": "sha256:" + "ab" * 32},
            identities={"auth-service": _identity(sha), "acl-service": _identity(sha)},
            generated_at="2026-09-26T18:00:00Z",
        )
    with pytest.raises(SystemExit, match="missing digests"):
        plan.image_manifest_document(
            git_sha=sha,
            services=["auth-service"],
            digests={"auth-service": "test"},
            identities={"auth-service": _identity(sha)},
            generated_at="2026-09-26T18:00:00Z",
        )


def test_manifest_refuses_a_row_without_baked_identity() -> None:
    sha = "12" * 20
    with pytest.raises(SystemExit, match="missing baked identity"):
        plan.image_manifest_document(
            git_sha=sha,
            services=["auth-service"],
            digests={"auth-service": "sha256:" + "ab" * 32},
            identities={"auth-service": {"built_git_sha": sha, "built_at": ""}},
            generated_at="2026-09-26T18:00:00Z",
        )


def test_baked_identity_reads_the_image_config_env() -> None:
    baked = "ab" * 20
    config = {
        "config": {
            "Env": [
                "PATH=/usr/bin",
                "GIT_SHA=",
                f"GIT_SHA={baked}",
                "BUILT_AT=2026-09-26T18:00:00Z",
            ]
        }
    }
    assert plan.baked_identity(config) == (baked, "2026-09-26T18:00:00Z")
    source_only = {
        "config": {
            "Env": [
                "SOURCE_SHA=" + ("cd" * 20),
                "BUILT_AT=2026-09-26T17:00:00Z",
            ]
        }
    }
    assert plan.baked_identity(source_only) == ("cd" * 20, "2026-09-26T17:00:00Z")
    assert plan.baked_identity({"config": {"Env": ["GIT_SHA=test"]}}) == ("", "")


def test_shared_env_does_not_carry_the_deploy_sha() -> None:
    for relative in ("docker-compose.yaml", "docker-compose.vm.yaml"):
        text = (REPO_ROOT / relative).read_text(encoding="utf-8")
        assert "SPANOPTICON_IMAGE_TAG: ${SPANOPTICON_IMAGE_TAG:-}" not in text
    vm = (REPO_ROOT / "docker-compose.vm.yaml").read_text(encoding="utf-8")
    assert vm.count("HYPERMESH_REVISION: ${SPANOPTICON_IMAGE_TAG:-}") == 2


def test_image_manifest_contract_is_documented() -> None:
    text = (REPO_ROOT / "docs" / "image-manifest.md").read_text(encoding="utf-8")
    assert "deploy/image-manifest" in text
    assert "image-manifest.json" in text
    assert "built_git_sha" in text
    assert "built_at" in text
    assert "deploy/test" in text
    assert "force: false" in text
    assert "EDGE_VERSIONS_ACCESS=off" in text
    assert "terraform/spanopticon" in text
    deploy = (REPO_ROOT / "docs" / "deploy-vm.md").read_text(encoding="utf-8")
    assert "Production must set `EDGE_VERSIONS_ACCESS=off`" in deploy
    example = (REPO_ROOT / ".env.vm.example").read_text(encoding="utf-8")
    assert "Production must set EDGE_VERSIONS_ACCESS=off" in example
    vm = (REPO_ROOT / "docker-compose.vm.yaml").read_text(encoding="utf-8")
    assert vm.count("EDGE_VERSIONS_ACCESS: ${EDGE_VERSIONS_ACCESS:-public}") == 1
    assert "fails open" in vm


def test_digest_header_is_the_registry_digest() -> None:
    digest = "sha256:" + "ef" * 32
    assert plan.digest_from_headers({"Docker-Content-Digest": digest}) == digest
    assert plan.digest_from_headers({"docker-content-digest": digest}) == digest
    with pytest.raises(SystemExit):
        plan.digest_from_headers({})
    with pytest.raises(SystemExit):
        plan.digest_from_headers({"Docker-Content-Digest": "sha256:abcd"})


def test_push_manifest_branch_commits_the_file_and_does_not_move_deploy_test() -> None:
    calls: list[tuple[str, str, object]] = []
    document = '{"git_sha":"' + ("ab" * 20) + '"}\n'

    def request(method, url, token, payload):
        del token
        calls.append((method, url, payload))
        if url.endswith("/blobs"):
            assert payload["content"] == document
            return 201, {"sha": "blobsha"}
        if url.endswith("/trees"):
            assert payload["tree"][0]["path"] == "image-manifest.json"
            assert payload["tree"][0]["sha"] == "blobsha"
            return 201, {"sha": "treesha"}
        if url.endswith("/commits"):
            assert payload["tree"] == "treesha"
            assert payload["parents"] == ["previous"]
            return 201, {"sha": "commitsha"}
        if url.endswith("/git/ref/heads/deploy/image-manifest"):
            return 200, {"object": {"sha": "previous", "type": "commit"}}
        if url.endswith("/git/refs/heads/deploy/image-manifest"):
            assert payload == {"sha": "commitsha", "force": False}
            return 200, {"object": {"sha": "commitsha"}}
        raise AssertionError(url)

    commit = plan.push_manifest_branch(
        "FyberLabs/spanopticon",
        "token",
        document,
        message="chore: image manifest for test\n",
        request=request,
    )
    assert commit == "commitsha"
    urls = [url for _, url, _ in calls]
    assert not any(url.endswith("/deploy/test") or "/heads/deploy/test" in url for url in urls)


def test_push_manifest_branch_creates_the_ref_when_it_is_absent() -> None:
    def request(method, url, token, payload):
        del token
        if url.endswith("/blobs"):
            return 201, {"sha": "blobsha"}
        if url.endswith("/trees"):
            return 201, {"sha": "treesha"}
        if url.endswith("/commits"):
            assert "parents" not in payload
            return 201, {"sha": "commitsha"}
        if url.endswith("/git/ref/heads/deploy/image-manifest") and method == "GET":
            return 404, {}
        if url.endswith("/git/refs") and method == "POST":
            assert payload["ref"] == "refs/heads/deploy/image-manifest"
            assert payload["sha"] == "commitsha"
            return 201, {"ref": payload["ref"]}
        raise AssertionError((method, url))

    assert (
        plan.push_manifest_branch(
            "FyberLabs/spanopticon",
            "token",
            "{}\n",
            message="chore: image manifest\n",
            request=request,
        )
        == "commitsha"
    )


def test_every_image_bakes_git_sha_and_serves_version() -> None:
    names = [name for name, _ in plan.PUBLISH_IMAGES]
    assert set(APP_SOURCES) == set(names)
    workflow = WORKFLOW.read_text(encoding="utf-8")
    assert "GIT_SHA=${{ github.sha }}" in workflow
    assert "BUILT_AT=${{ steps.built.outputs.at }}" in workflow
    assert "SOURCE_SHA=${{ github.sha }}" in workflow
    for name, dockerfile in plan.PUBLISH_IMAGES:
        text = (REPO_ROOT / dockerfile).read_text(encoding="utf-8")
        assert "ARG GIT_SHA=" in text, name
        assert "ENV GIT_SHA=${GIT_SHA}" in text, name
        assert "ARG BUILT_AT=" in text, name
        assert "ENV BUILT_AT=${BUILT_AT}" in text, name
        app = (REPO_ROOT / APP_SOURCES[name]).read_text(encoding="utf-8")
        tree = ast.parse(app)
        aliases = {alias.asname or alias.name for node in ast.walk(tree)
                   if isinstance(node, ast.ImportFrom) and node.module in {
                       "api.interfaces.api_controllers.version_endpoint", "controlplane_api.version"}
                   for alias in node.names if alias.name == "router"}
        assert aliases, name
        assert any(isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                   and node.func.attr == "include_router" and node.args
                   and isinstance(node.args[0], ast.Name) and node.args[0].id in aliases
                   for node in ast.walk(tree)), name


def test_manifest_is_published_after_carry_and_before_deploy_test_moves() -> None:
    workflow = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    steps = workflow["jobs"]["deployable"]["steps"]
    names = [step.get("name") or "" for step in steps]
    carry = names.index("Carry unchanged images to this sha")
    promote = names.index("Promote images to test")
    resolve = names.index("Every compose image resolves at this sha")
    manifest = names.index("Publish image digest manifest")
    advance = names.index("Advance deploy/test to this sha")
    assert carry < promote < resolve < manifest < advance
    carry_script = steps[carry]["run"]
    assert "imagetools create" in carry_script
    assert "write-manifest" in steps[manifest]["run"]
    assert "deploy/test" not in steps[manifest]["run"]
