"""Image manifest digests, and the wiring that bakes /version into every image."""

from __future__ import annotations

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
    document = plan.image_manifest_document(
        git_sha=sha,
        services=["auth-service", "controlplane-edge"],
        digests={"auth-service": carried, "controlplane-edge": rebuilt},
        generated_at="2026-09-26T18:00:00Z",
    )
    rows = {row["service"]: row for row in document["services"]}
    assert rows["auth-service"]["digest"] == carried
    assert rows["auth-service"]["digest"] != sha
    assert rows["controlplane-edge"]["image"] == f"ghcr.io/fyberlabs/controlplane-edge@{rebuilt}"
    assert rows["controlplane-edge"]["tag"] == sha
    assert [row["service"] for row in document["services"]] == [
        "auth-service",
        "controlplane-edge",
    ]


def test_manifest_refuses_a_missing_or_non_digest_value() -> None:
    sha = "12" * 20
    with pytest.raises(SystemExit, match="missing digests"):
        plan.image_manifest_document(
            git_sha=sha,
            services=["auth-service", "acl-service"],
            digests={"auth-service": "sha256:" + "ab" * 32},
            generated_at="2026-09-26T18:00:00Z",
        )
    with pytest.raises(SystemExit, match="missing digests"):
        plan.image_manifest_document(
            git_sha=sha,
            services=["auth-service"],
            digests={"auth-service": "test"},
            generated_at="2026-09-26T18:00:00Z",
        )


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
            assert payload == {"sha": "commitsha", "force": True}
            return 200, {"object": {"sha": "commitsha"}}
        raise AssertionError(url)

    commit = plan.push_manifest_branch(
        "FyberLabs/panopticon",
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
            "FyberLabs/panopticon",
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
        assert "version_endpoint" in app, name
        assert "include_router(version_router)" in app, name


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
