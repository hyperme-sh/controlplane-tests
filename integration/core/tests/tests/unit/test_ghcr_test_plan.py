"""GHCR test-plane path filters must diff against the last published sha.

Run 36096930896 for 761d33bb (#203) was cancelled while pending: concurrency
keeps one publish in the group and drops the older queued run, so that sha
never got images. Run 36096947138 for 9d9d0671 (#205) still used
``github.event.before`` (761d33bb). The diff was frontend plus hypermesh
only. ``core/lib`` and ``controlplane-db`` (Alembic 0038 and 0039) had
landed in the cancelled commits, so they did not match, and "Carry unchanged
images" retagged the #200-era ``controlplane-edge:test`` as ``:9d9d0671``.
That image does not contain revision 0039. The database was already there,
edge exited 255 with "Can't locate revision", and refresh-test.sh looped on
stack recreate.

``refs/heads/deploy/test`` after the last successful publish (#201,
11c95f34) is the base that includes those paths. Edge has to be rebuilt.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[4]
WORKFLOW_PATH = REPO_ROOT / ".github" / "workflows" / "ghcr-test.yml"

# Last successful publish before the cancelled runs: #201.
LAST_PUBLISHED = "11c95f34f661188297814c0eaf1a11ae9d296046"
# #203, cancelled as a superseded pending run. Never published.
SUPERSEDED = "761d33bb24297ef27541feeeee80b1a0dc2f2a33"
# #205. Its push event set before=SUPERSEDED.
HEAD_205 = "9d9d06719d0a0ad0cbc1db93050067b48d97a16f"

MIGRATION_0039 = (
    "core/lib/controlplane-db/src/controlplane_db/alembic/versions/"
    "20260925_0039_lab_device_identity.py"
)
EDGE_PATH = "controlplane-edge/src/controlplane_edge/public_paths.py"

# git diff --name-only LAST_PUBLISHED..HEAD_205. The live check below fails
# the test when those commits are in the clone and this list has drifted.
PUBLISHED_DIFF = (
    ".github/workflows/hypermesh-gateway-smoke.yml",
    "controlplane-edge/src/controlplane_edge/public_paths.py",
    "controlplane-edge/tests/test_edge_proxy.py",
    "core/api-keys-service/src/api_keys_service/allowlist.py",
    "core/api-keys-service/src/api_keys_service/routes.py",
    "core/api-keys-service/tests/test_allowlist.py",
    "core/auth-service/src/auth_service/app.py",
    "core/auth-service/src/auth_service/routes.py",
    "core/auth-service/src/auth_service/schemas.py",
    "core/auth-service/src/auth_service/smoke_oidc.py",
    "core/auth-service/tests/test_hypermesh_smoke_oidc.py",
    "core/lib/controlplane-core/src/api/infrastructure/security/tenant_session_jwt.py",
    "core/lib/controlplane-db/src/controlplane_db/alembic/versions/20260925_0038_aiuditor_size_bytes_bigint.py",
    "core/lib/controlplane-db/src/controlplane_db/alembic/versions/20260925_0039_lab_device_identity.py",
    "core/lib/controlplane-db/src/controlplane_db/models.py",
    "core/tests/tests/integration/test_aiuditor_size_and_lab_migration.py",
    "docker-compose.vm.yaml",
    "frontend/src/app/dashboard/hypermesh/certified-map.ts",
    "frontend/src/app/dashboard/hypermesh/certified-pairs.test.tsx",
    "frontend/src/app/dashboard/hypermesh/components/host-panels.tsx",
    "frontend/src/app/dashboard/hypermesh/components/renter-panels.tsx",
    "frontend/src/app/dashboard/hypermesh/hypermesh-instrument.css",
    "frontend/src/app/dashboard/hypermesh/renter/catalog/catalog-page.tsx",
    "frontend/src/app/dashboard/hypermesh/renter/checkout/checkout-page.tsx",
    "frontend/src/app/dashboard/hypermesh/renter/hosts/hosts-page.tsx",
    "frontend/src/hooks/use-hypermesh.ts",
    "frontend/src/test/msw-handlers.ts",
    "products/aiuditor/src/aiuditor_service/hypermesh.py",
    "products/aiuditor/src/aiuditor_service/intake.py",
    "products/aiuditor/src/aiuditor_service/intake_loop.py",
    "products/aiuditor/src/aiuditor_service/persist.py",
    "products/aiuditor/tests/test_crawl_tenant.py",
    "products/aiuditor/tests/test_intake_admission.py",
    "products/aiuditor/tests/test_persist_schema.py",
    "products/aiuditor/tests/test_persist_sql.py",
    "products/hypermesh/pyproject.toml",
    "products/hypermesh/src/hypermesh_service/certified_pairs.py",
    "products/hypermesh/src/hypermesh_service/domain/models.py",
    "products/hypermesh/src/hypermesh_service/gateway_smoke.py",
    "products/hypermesh/src/hypermesh_service/host_certification.py",
    "products/hypermesh/src/hypermesh_service/interfaces/api/routes.py",
    "products/hypermesh/src/hypermesh_service/lab_queue.py",
    "products/hypermesh/src/hypermesh_service/leases.py",
    "products/hypermesh/src/hypermesh_service/scorecard.py",
    "products/hypermesh/src/hypermesh_service/seed.py",
    "products/hypermesh/tests/conftest.py",
    "products/hypermesh/tests/synthetic_host.py",
    "products/hypermesh/tests/test_admission_sandbox_hop.py",
    "products/hypermesh/tests/test_certified_pairs.py",
    "products/hypermesh/tests/test_compact_cert.py",
    "products/hypermesh/tests/test_gateway_smoke.py",
    "products/hypermesh/tests/test_hypermesh_service.py",
    "products/hypermesh/tests/test_lab_device_seed.py",
    "products/hypermesh/tests/test_lab_queue.py",
    "products/hypermesh/tests/test_renter_host_chooser.py",
    "products/hypermesh/tests/test_scorecard.py",
)

# git diff --name-only SUPERSEDED..HEAD_205. This is what #205 filtered on.
SUPERSEDED_DIFF = (
    "frontend/src/app/dashboard/hypermesh/certified-map.ts",
    "frontend/src/app/dashboard/hypermesh/certified-pairs.test.tsx",
    "frontend/src/app/dashboard/hypermesh/components/host-panels.tsx",
    "frontend/src/app/dashboard/hypermesh/components/renter-panels.tsx",
    "frontend/src/app/dashboard/hypermesh/hypermesh-instrument.css",
    "frontend/src/app/dashboard/hypermesh/renter/catalog/catalog-page.tsx",
    "frontend/src/app/dashboard/hypermesh/renter/checkout/checkout-page.tsx",
    "frontend/src/app/dashboard/hypermesh/renter/hosts/hosts-page.tsx",
    "frontend/src/hooks/use-hypermesh.ts",
    "frontend/src/test/msw-handlers.ts",
    "products/hypermesh/src/hypermesh_service/certified_pairs.py",
    "products/hypermesh/src/hypermesh_service/domain/models.py",
    "products/hypermesh/src/hypermesh_service/interfaces/api/routes.py",
    "products/hypermesh/src/hypermesh_service/leases.py",
    "products/hypermesh/tests/test_certified_pairs.py",
    "products/hypermesh/tests/test_hypermesh_service.py",
    "products/hypermesh/tests/test_renter_host_chooser.py",
)


def _load_plan():
    scripts = str(REPO_ROOT / "scripts")
    if scripts not in sys.path:
        sys.path.insert(0, scripts)
    import ghcr_test_plan

    return ghcr_test_plan


plan = _load_plan()


def _workflow() -> dict:
    return yaml.safe_load(WORKFLOW_PATH.read_text())


def _commit_exists(sha: str) -> bool:
    result = subprocess.run(
        ["git", "cat-file", "-e", f"{sha}^{{commit}}"],
        cwd=REPO_ROOT,
        check=False,
        capture_output=True,
    )
    return result.returncode == 0


def _git_diff(base: str, head: str) -> tuple[str, ...] | None:
    if not (_commit_exists(base) and _commit_exists(head)):
        return None
    result = subprocess.run(
        ["git", "diff", "--no-renames", "--name-only", f"{base}..{head}"],
        cwd=REPO_ROOT,
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        return None
    return tuple(line for line in result.stdout.splitlines() if line)


def _diff(base: str, head: str, recorded: tuple[str, ...]) -> tuple[str, ...]:
    live = _git_diff(base, head)
    if live is None:
        return recorded
    assert live == recorded, f"recorded diff {base[:12]}..{head[:12]} is stale"
    return live


def _glob_regex(pattern: str) -> re.Pattern[str]:
    """Enough of dorny's picomatch for the globs this workflow actually uses."""

    out = ["^"]
    i = 0
    while i < len(pattern):
        if pattern.startswith("**", i):
            out.append(".*")
            i += 2
            continue
        if pattern[i] == "*":
            out.append("[^/]*")
            i += 1
            continue
        out.append(re.escape(pattern[i]))
        i += 1
    out.append("$")
    return re.compile("".join(out))


def _filter_map() -> dict[str, list[str]]:
    steps = _workflow()["jobs"]["changes"]["steps"]
    filters = next(
        step["with"]["filters"]
        for step in steps
        if str(step.get("uses", "")).startswith("dorny/paths-filter")
    )
    parsed = yaml.safe_load(filters)
    return {name: list(patterns) for name, patterns in parsed.items()}


def _filter_hits(changed: tuple[str, ...]) -> dict[str, str]:
    hits: dict[str, str] = {}
    for name, patterns in _filter_map().items():
        matched = any(
            _glob_regex(pattern).match(path) for pattern in patterns for path in changed
        )
        hits[name] = "true" if matched else "false"
    return hits


def _prefixes() -> dict[str, list[str]]:
    return plan.load_copy_prefixes(REPO_ROOT)


def _names() -> tuple[str, ...]:
    return tuple(name for name, _ in plan.PUBLISH_IMAGES)


@pytest.fixture(scope="module")
def published_diff() -> tuple[str, ...]:
    return _diff(LAST_PUBLISHED, HEAD_205, PUBLISHED_DIFF)


@pytest.fixture(scope="module")
def superseded_diff() -> tuple[str, ...]:
    return _diff(SUPERSEDED, HEAD_205, SUPERSEDED_DIFF)


def test_recorded_diffs_describe_the_cancelled_gap(
    published_diff: tuple[str, ...], superseded_diff: tuple[str, ...]
) -> None:
    """#205's before-sha hides the migration and the edge change. deploy/test does not."""

    assert MIGRATION_0039 in published_diff
    assert EDGE_PATH in published_diff
    assert MIGRATION_0039 not in superseded_diff
    assert EDGE_PATH not in superseded_diff
    assert not any(path.startswith("core/lib/") for path in superseded_diff)
    assert any(path.startswith("core/lib/controlplane-db/") for path in published_diff)


def test_edge_image_copies_controlplane_db() -> None:
    prefixes = _prefixes()["controlplane-edge"]
    assert plan.image_copies(prefixes, "core/lib/controlplane-db")
    assert plan.image_copies(
        prefixes, "core/lib/controlplane-db/src/controlplane_db/alembic"
    )
    plan.assert_edge_copies_controlplane_db(_prefixes())


def test_published_base_rebuilds_edge_for_revision_0039(
    published_diff: tuple[str, ...],
) -> None:
    """Diff against #201, with the filter hits that diff produces, rebuilds edge.

    ``shared`` matches ``core/lib/**`` and the controlplane-db globs, so the
    full fleet is built. The guard would add edge even if that glob were absent.
    """

    hits = _filter_hits(published_diff)
    assert hits["shared"] == "true"
    assert hits["controlplane-edge"] == "true"
    assert hits["hypermesh-service"] == "true"

    decided = plan.plan_publish(
        event_name="push",
        base_state="ancestor",
        filter_hits=hits,
        changed_files=published_diff,
        copy_prefixes=_prefixes(),
    )
    assert "controlplane-edge" in decided.built
    db_images = {
        name
        for name, prefixes in _prefixes().items()
        if plan.image_copies(prefixes, "core/lib/controlplane-db")
    }
    assert "controlplane-edge" in db_images
    assert db_images <= set(decided.built)
    assert decided.built == _names()
    assert decided.carry_allowed is True

    # The path filter missed shared inputs (the #205 failure mode). The guard
    # still rebuilds every image that copies controlplane-db, including edge.
    narrow = {name: "false" for name in hits}
    narrow["hypermesh-service"] = "true"
    guarded = plan.plan_publish(
        event_name="push",
        base_state="ancestor",
        filter_hits=narrow,
        changed_files=published_diff,
        copy_prefixes=_prefixes(),
    )
    assert "controlplane-edge" in guarded.guard_added
    assert "controlplane-edge" in guarded.built
    assert db_images <= set(guarded.built)
    migration_images = plan.shared_dependency_images(published_diff, _prefixes())[
        MIGRATION_0039
    ]
    assert "controlplane-edge" in migration_images


def test_superseded_before_sha_does_not_rebuild_edge(
    superseded_diff: tuple[str, ...],
) -> None:
    """The bug: diffing #205 against #203 rebuilds hypermesh and carries edge."""

    hits = _filter_hits(superseded_diff)
    assert hits["shared"] == "false"
    assert hits["controlplane-edge"] == "false"
    assert hits["hypermesh-service"] == "true"

    decided = plan.plan_publish(
        event_name="push",
        base_state="ancestor",
        filter_hits=hits,
        changed_files=superseded_diff,
        copy_prefixes=_prefixes(),
    )
    assert decided.built == ("hypermesh-service",)
    assert "controlplane-edge" not in decided.built
    assert decided.carry_allowed is True


def test_unusable_base_rebuilds_every_image_and_forbids_carry(
    superseded_diff: tuple[str, ...],
) -> None:
    """The narrow #205 diff must not win when deploy/test cannot be used."""

    hits = _filter_hits(superseded_diff)
    for state in ("missing", "not_ancestor", "", "bogus"):
        decided = plan.plan_publish(
            event_name="push",
            base_state=state,
            filter_hits=hits,
            changed_files=superseded_diff,
            copy_prefixes=_prefixes(),
        )
        assert decided.built == _names(), state
        assert "controlplane-edge" in decided.built
        assert decided.carry_allowed is False
        assert decided.rebuild_all is True


def test_workflow_dispatch_rebuilds_every_image() -> None:
    decided = plan.plan_publish(
        event_name="workflow_dispatch",
        base_state="ancestor",
        filter_hits={"hypermesh-service": "true"},
        changed_files=SUPERSEDED_DIFF,
        copy_prefixes=_prefixes(),
    )
    assert decided.built == _names()
    assert decided.carry_allowed is False


def test_shared_copy_rebuilds_every_dependent_image() -> None:
    """A file several images COPY rebuilds those images, not the whole fleet."""

    ticket = "products/hypermesh-ticket/src/hypermesh_ticket/__init__.py"
    decided = plan.plan_publish(
        event_name="push",
        base_state="ancestor",
        filter_hits={"hypermesh-service": "true"},
        changed_files=[ticket],
        copy_prefixes=_prefixes(),
    )
    assert "hypermesh-service" in decided.built
    assert "hypermesh-gateway" in decided.guard_added
    assert "controlplane-edge" not in decided.built

    private = "products/hypermesh/src/hypermesh_service/leases.py"
    only_hypermesh = plan.plan_publish(
        event_name="push",
        base_state="ancestor",
        filter_hits={"hypermesh-service": "true"},
        changed_files=[private],
        copy_prefixes=_prefixes(),
    )
    assert only_hypermesh.built == ("hypermesh-service",)
    assert only_hypermesh.carry_allowed is True


def test_named_shared_roots_include_migrations() -> None:
    assert plan.is_named_shared_dependency(MIGRATION_0039)
    assert plan.is_named_shared_dependency(
        "core/lib/controlplane-core/src/api/infrastructure/security/tenant_session_jwt.py"
    )
    assert plan.is_named_shared_dependency("core/lib/newpkg/module.py")
    assert plan.is_named_shared_dependency("somewhere/alembic/versions/0099_x.py")
    assert plan.is_named_shared_dependency("somewhere/migrations/0001.sql")
    assert not plan.is_named_shared_dependency(
        "products/hypermesh/src/hypermesh_service/leases.py"
    )


def test_workflow_uses_deploy_test_as_the_path_filter_base() -> None:
    text = WORKFLOW_PATH.read_text()
    workflow = _workflow()
    changes = workflow["jobs"]["changes"]
    filter_step = next(
        step
        for step in changes["steps"]
        if str(step.get("uses", "")).startswith("dorny/paths-filter")
    )
    assert filter_step["with"]["base"] == "${{ steps.published.outputs.base }}"
    assert "steps.published.outputs.base != ''" in filter_step["if"]
    assert "rebuild_all != 'true'" in filter_step["if"]
    assert "base: ${{ github.event.before }}" not in text
    assert "github.event.before" in text  # the comment that explains why it is not the base
    resolve = next(
        step for step in changes["steps"] if step.get("id") == "published"
    )
    assert "resolve-base --fetch" in resolve["run"]
    matrix = next(step for step in changes["steps"] if step.get("id") == "matrix")
    assert "write-matrix" in matrix["run"]
    assert changes["outputs"]["carry_allowed"] == "${{ steps.matrix.outputs.carry_allowed }}"


def test_runners_concurrency_and_parallelism_are_unchanged() -> None:
    text = WORKFLOW_PATH.read_text()
    workflow = _workflow()
    assert "ubuntu-latest" not in text
    for name, job in workflow["jobs"].items():
        assert job["runs-on"] == ["self-hosted", "linux", "x64"], name
    assert workflow["concurrency"] == {
        "group": "ghcr-test-publish",
        "cancel-in-progress": False,
    }
    assert workflow["jobs"]["publish"]["strategy"]["max-parallel"] == 4
    assert workflow["jobs"]["publish"]["strategy"]["fail-fast"] is False


# pipefail plus `grep -q` (any quiet flag cluster, including -qxF). The
# reader exits at the first hit and the writer can take SIGPIPE.
_QUIET_GREP_PIPE = re.compile(
    r"\|\s*grep(?:\s+(?:--[A-Za-z0-9-]+|-[A-Za-z]+))*\s+(?:--quiet|-[A-Za-z]*q[A-Za-z]*)\b"
)


def _run_scripts() -> list[tuple[str, str, str]]:
    found: list[tuple[str, str, str]] = []
    for job_name, job in _workflow()["jobs"].items():
        for step in job.get("steps") or []:
            script = step.get("run")
            if isinstance(script, str):
                found.append((job_name, str(step.get("name") or step.get("id") or ""), script))
    return found


def _carry_script() -> str:
    script = next(
        script
        for job_name, name, script in _run_scripts()
        if job_name == "deployable" and name == "Carry unchanged images to this sha"
    )
    return script


def _quiet_grep_pipe_misses(built: list[str], service: str) -> bool:
    """True when the old `printf | grep -qxF` check would treat service as absent.

    The payload has to be larger than the pipe buffer. A short list fits in
    one write and the race rarely loses; past the buffer, grep -q closes the
    pipe while printf is still writing and pipefail reports 141.
    """

    script = r"""
set -o pipefail
mapfile -t built < <(printf '%s' "$BUILT" | python3 -c 'import json,sys; print("\n".join(json.load(sys.stdin)))')
if printf '%s\n' "${built[@]}" | grep -qxF "$SVC"; then
  exit 0
fi
exit 1
"""
    # A minimal environment: one string longer than 128KiB is E2BIG, and the
    # parent environment is not needed to decide membership.
    completed = subprocess.run(
        ["bash", "-c", script],
        check=False,
        capture_output=True,
        text=True,
        env={
            "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
            "HOME": os.environ.get("HOME", "/tmp"),
            "BUILT": json.dumps(built),
            "SVC": service,
        },
    )
    if completed.returncode not in (0, 1):
        raise AssertionError(completed.stderr)
    return completed.returncode != 0


def _quiet_grep_can_miss(built: list[str], service: str, *, attempts: int = 40) -> bool:
    """True when the old check loses the match at least once.

    Past the pipe buffer, bash still reports 0 or 141 for the same payload.
    One miss is the bug the carry step used to ship.
    """

    return any(_quiet_grep_pipe_misses(built, service) for _ in range(attempts))


def _built_list_that_sigpipes(service: str) -> list[str]:
    # Just past a 64KiB pipe, and under the 128KiB single-argument limit so
    # the same list can be passed to the carry script in BUILT.
    built = [service, *[f"pad-{index:05d}-{'x' * 32}" for index in range(1800)]]
    encoded = json.dumps(built)
    if len(encoded) >= 120_000:
        raise AssertionError(f"BUILT payload is {len(encoded)} bytes")
    if not _quiet_grep_can_miss(built, service):
        raise AssertionError(f"payload did not make quiet grep fail under pipefail for {service}")
    return built


def test_carry_refuses_when_the_base_was_not_published() -> None:
    workflow = _workflow()
    steps = workflow["jobs"]["deployable"]["steps"]
    names = [step.get("name") or "" for step in steps]
    carry_at = names.index("Carry unchanged images to this sha")
    gate_at = names.index("Require every changed image to have published")
    assert gate_at < carry_at

    gate = steps[gate_at]["run"]
    assert "needs.changes.result" in gate
    assert "Refusing to carry images or advance deploy/test." in gate

    carry = steps[carry_at]
    assert carry["env"]["CARRY_ALLOWED"] == "${{ needs.changes.outputs.carry_allowed }}"
    script = carry["run"]
    refusal = script.index("refusing to carry")
    retag = script.index("imagetools create")
    assert refusal < retag
    assert 'CARRY_ALLOWED:-' in script


def _code_lines(script: str) -> str:
    return "\n".join(
        line for line in script.splitlines() if not line.lstrip().startswith("#")
    )


def test_workflow_scripts_do_not_pipe_into_quiet_grep() -> None:
    """Every run script in ghcr-test.yml, not only the carry step.

    The other pipelines feed python, which reads stdin to EOF. Those are
    not this bug. A quiet grep on the read end of a pipe is.
    """

    old = "if printf '%s\\n' \"${built[@]}\" | grep -qxF \"$svc\"; then"
    assert _QUIET_GREP_PIPE.search(old)
    assert _QUIET_GREP_PIPE.search("psql | grep --quiet 1")
    assert _QUIET_GREP_PIPE.search("printf | grep -F -q x")
    assert not _QUIET_GREP_PIPE.search("printf '%s' \"$ALL\" | python3 -c 'import json'")
    assert not _QUIET_GREP_PIPE.search(
        "grep -qxF \"$svc\" <<<\"$(printf '%s\\n' \"${built[@]}\")\""
    )
    offenders = [
        f"{job} / {name}"
        for job, name, script in _run_scripts()
        if "pipefail" in script and _QUIET_GREP_PIPE.search(_code_lines(script))
    ]
    assert offenders == []


def test_carry_keeps_a_built_image_when_quiet_grep_would_sigpipe(tmp_path: Path) -> None:
    """Run 36144583444 had marketplace in BUILT and still logged carrying :test.

    The alembic-head check had already read marketplace:<sha>. The carry
    step then retagged :test onto that sha, so the guard's result was
    thrown away and the previous image was what :test promoted.
    """

    service = "marketplace"
    built = [*_built_list_that_sigpipes(service), "ai-service"]
    assert _quiet_grep_can_miss(built, service)

    docker_log = tmp_path / "docker.log"
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    stub = bin_dir / "docker"
    stub.write_text('#!/bin/sh\nprintf \'%s\\n\' "$*" >> "$DOCKER_LOG"\n')
    stub.chmod(0o755)
    sha = "3586fa8e70f540b9a47bdf90bd8f57aad92ce0d9"
    completed = subprocess.run(
        ["bash", "-c", _carry_script()],
        check=False,
        capture_output=True,
        text=True,
        env={
            "PATH": f"{bin_dir}{os.pathsep}{os.environ.get('PATH', '/usr/bin:/bin')}",
            "HOME": os.environ.get("HOME", "/tmp"),
            "DOCKER_LOG": str(docker_log),
            "ALL": json.dumps(["market", service, "ai-service", "template-service"]),
            "BUILT": json.dumps(built),
            "SHA": sha,
            "CARRY_ALLOWED": "true",
        },
    )
    assert completed.returncode == 0, completed.stderr
    assert f"  {service}: built at this sha" in completed.stdout
    assert "  ai-service: built at this sha" in completed.stdout
    assert f"  {service}: carrying :test" not in completed.stdout
    assert f"  market: carrying :test -> :{sha}" in completed.stdout
    assert f"  template-service: carrying :test -> :{sha}" in completed.stdout
    log = docker_log.read_text()
    assert f"ghcr.io/fyberlabs/{service}:" not in log
    assert f"ghcr.io/fyberlabs/ai-service:" not in log
    assert f"ghcr.io/fyberlabs/market:{sha}" in log
    assert f"ghcr.io/fyberlabs/template-service:{sha}" in log


def test_resolve_base_missing_ancestor_and_divergent(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.check_call(["git", "init", "-b", "main"], cwd=repo)
    subprocess.check_call(["git", "config", "user.email", "dev@example.com"], cwd=repo)
    subprocess.check_call(["git", "config", "user.name", "Dev"], cwd=repo)

    missing = plan.resolve_published_base(repo)
    assert missing.state == "missing"
    assert missing.sha is None
    assert missing.rebuild_all is True

    (repo / "a.txt").write_text("a")
    subprocess.check_call(["git", "add", "a.txt"], cwd=repo)
    subprocess.check_call(["git", "commit", "-m", "first"], cwd=repo)
    first = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=repo, text=True).strip()
    (repo / "b.txt").write_text("b")
    subprocess.check_call(["git", "add", "b.txt"], cwd=repo)
    subprocess.check_call(["git", "commit", "-m", "second"], cwd=repo)
    second = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=repo, text=True).strip()

    subprocess.check_call(["git", "update-ref", plan.DEPLOY_TEST_REF, first], cwd=repo)
    ancestor = plan.resolve_published_base(repo)
    assert ancestor.state == "ancestor"
    assert ancestor.sha == first
    assert ancestor.rebuild_all is False

    subprocess.check_call(["git", "update-ref", plan.DEPLOY_TEST_REF, second], cwd=repo)
    same = plan.resolve_published_base(repo)
    assert same.state == "ancestor"
    assert same.sha == second

    subprocess.check_call(["git", "checkout", "--orphan", "side"], cwd=repo)
    subprocess.check_call(["git", "commit", "--allow-empty", "-m", "side"], cwd=repo)
    side = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=repo, text=True).strip()
    subprocess.check_call(["git", "checkout", "main"], cwd=repo)
    subprocess.check_call(["git", "update-ref", plan.DEPLOY_TEST_REF, side], cwd=repo)
    divergent = plan.resolve_published_base(repo)
    assert divergent.state == "not_ancestor"
    assert divergent.sha == side
    assert divergent.rebuild_all is True


def test_resolve_base_writes_github_output(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.check_call(["git", "init", "-b", "main"], cwd=repo)
    subprocess.check_call(["git", "config", "user.email", "dev@example.com"], cwd=repo)
    subprocess.check_call(["git", "config", "user.name", "Dev"], cwd=repo)
    (repo / "a.txt").write_text("a")
    subprocess.check_call(["git", "add", "a.txt"], cwd=repo)
    subprocess.check_call(["git", "commit", "-m", "first"], cwd=repo)
    output = tmp_path / "github_output"
    monkeypatch.setenv("GITHUB_OUTPUT", str(output))
    assert plan.main(["resolve-base", "--repo", str(repo)]) == 0
    text = output.read_text()
    assert "base_state=missing" in text
    assert "rebuild_all=true" in text
    assert "base=" in text


def test_write_matrix_cli_forbids_carry_without_a_base(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    output = tmp_path / "github_output"
    monkeypatch.setenv("GITHUB_OUTPUT", str(output))
    monkeypatch.setenv("EVENT_NAME", "push")
    monkeypatch.setenv("BASE_STATE", "missing")
    monkeypatch.setenv("BASE", "")
    monkeypatch.setenv("FILTER_JSON", json.dumps({"hypermesh-service": "true", "shared": "false"}))
    assert plan.cmd_write_matrix(REPO_ROOT) == 0
    text = output.read_text()
    assert "carry_allowed=false" in text
    assert "rebuild_all=true" in text
    built = next(
        line.split("=", 1)[1] for line in text.splitlines() if line.startswith("built=")
    )
    assert "controlplane-edge" in json.loads(built)


def test_assert_compose_accepts_the_repo() -> None:
    plan.assert_compose_services(REPO_ROOT)


def test_repo_alembic_head_matches_script_directory() -> None:
    """The promote guard and Alembic agree on exactly one head at this commit.

    The revision id is whatever ``ScriptDirectory`` reports. Pinning it here
    broke when a later migration landed beside the guard.
    """

    from alembic.script import ScriptDirectory

    script_location = REPO_ROOT / "core/lib/controlplane-db/src/controlplane_db/alembic"
    heads = ScriptDirectory(str(script_location)).get_heads()
    assert len(heads) == 1
    expected = heads[0]

    versions = REPO_ROOT / plan.VERSIONS_DIR
    assert plan.alembic_head.repo_head(versions) == expected
    completed = subprocess.run(
        [sys.executable, str(REPO_ROOT / "scripts" / "alembic_head.py")],
        cwd=REPO_ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    assert json.loads(completed.stdout) == plan.alembic_head.deployment_heads(versions)


def test_alembic_versions_change_rebuilds_every_bundled_image() -> None:
    """A versions-directory edit rebuilds every controlplane-db image.

    The path filter in this case only selected hypermesh, which is what #205
    did. The versions rule still adds edge and the rest of the fleet.
    """

    prefixes = _prefixes()
    decided = plan.plan_publish(
        event_name="push",
        base_state="ancestor",
        filter_hits={"shared": "false", "hypermesh-service": "true"},
        changed_files=[MIGRATION_0039],
        copy_prefixes=prefixes,
    )
    db = plan.db_image_names(prefixes)
    assert "controlplane-edge" in db
    assert set(db) <= set(decided.built)
    assert decided.built == _names()

    only_edge = {
        "controlplane-edge": ["core/lib/controlplane-db"],
        "frontend-not-published": ["frontend"],
    }
    narrow = plan.plan_publish(
        images=(
            ("controlplane-edge", "controlplane-edge/Dockerfile"),
            ("frontend-not-published", "frontend/Dockerfile"),
        ),
        event_name="push",
        base_state="ancestor",
        filter_hits={},
        changed_files=["pkg/alembic/versions/0002_new.py"],
        copy_prefixes=only_edge,
    )
    assert narrow.built == ("controlplane-edge",)


def test_alembic_parser_accepts_annotated_roots_and_rejects_two_heads(tmp_path: Path) -> None:
    versions = tmp_path / "versions"
    versions.mkdir()
    (versions / "0001.py").write_text('revision: str = "0001"\ndown_revision: Union[str, None] = None\n')
    (versions / "0002.py").write_text('revision = "0002"\ndown_revision = "0001"\n')
    assert plan.alembic_head.repo_head(versions) == "0002"
    (versions / "side.py").write_text('revision = "side"\ndown_revision = "0001"\n')
    with pytest.raises(SystemExit, match="one head"):
        plan.alembic_head.repo_head(versions)


def test_promotion_refuses_a_stale_or_missing_head() -> None:
    errors = plan.promotion_errors(
        "0039_lab_device_identity",
        {
            "controlplane-edge": "0036_device_wg_pubkey",
            "auth-service": None,
            "hypermesh-service": "0039_lab_device_identity",
        },
        ["controlplane-edge", "auth-service", "hypermesh-service"],
    )
    assert any("controlplane-edge" in error and "0036_device_wg_pubkey" in error for error in errors)
    assert any("auth-service" in error and "no " in error for error in errors)
    assert not any(error.startswith("hypermesh-service") for error in errors)
    assert plan.promotion_errors(
        "0039_lab_device_identity",
        {"controlplane-edge": "0039_lab_device_identity"},
        ["controlplane-edge"],
    ) == []


def test_manifest_label_comes_from_the_image_config() -> None:
    single = {
        "config": {"digest": "sha256:config", "mediaType": "application/vnd.oci.image.config.v1+json"}
    }
    assert plan.manifest_target(single) == ("config", "sha256:config")
    index = {
        "manifests": [
            {"digest": "sha256:arm", "platform": {"os": "linux", "architecture": "arm64"}},
            {"digest": "sha256:amd", "platform": {"os": "linux", "architecture": "amd64"}},
        ]
    }
    assert plan.manifest_target(index) == ("manifest", "sha256:amd")
    assert (
        plan.label_from_config(
            {"config": {"Labels": {plan.alembic_head.ALEMBIC_LABEL: "0039_lab_device_identity"}}},
            plan.alembic_head.ALEMBIC_LABEL,
        )
        == "0039_lab_device_identity"
    )
    assert plan.label_from_config({"config": {"Labels": {}}}, plan.alembic_head.ALEMBIC_LABEL) is None


def test_test_tag_is_not_pushed_before_the_head_check() -> None:
    workflow = _workflow()
    build = next(
        step
        for step in workflow["jobs"]["publish"]["steps"]
        if str(step.get("uses", "")).startswith("docker/build-push-action")
    )
    assert ":test" not in build["with"]["tags"]
    assert "ALEMBIC_HEAD" in build["with"]["build-args"]
    names = [step.get("name") or "" for step in workflow["jobs"]["deployable"]["steps"]]
    check = names.index("Alembic heads match per chain before promotion")
    carry = names.index("Carry unchanged images to this sha")
    promote = names.index("Promote images to test")
    advance = names.index("Advance deploy/test to this sha")
    assert check < carry < promote < advance
    assert "check-heads" in workflow["jobs"]["deployable"]["steps"][check]["run"]
    assert "imagetools create" in workflow["jobs"]["deployable"]["steps"][promote]["run"]


def test_image_labels_compare_every_owner_head():
    heads = plan.alembic_head.deployment_heads(REPO_ROOT / plan.VERSIONS_DIR)
    assert set(heads) == {"legacy", "platform", "events", "hypermesh", "aiuditor", "payment_service"}
    expected = json.dumps(heads)
    assert plan.promotion_errors(expected, {"image": json.dumps(heads, sort_keys=True)}, ["image"]) == []
    stale = dict(heads, hypermesh="stale_hypermesh_head")
    errors = plan.promotion_errors(expected, {"image": json.dumps(stale)}, ["image"])
    assert len(errors) == 1 and "hypermesh head" in errors[0]
    missing = dict(heads)
    missing.pop("events")
    assert "events head" in plan.promotion_errors(expected, {"image": json.dumps(missing)}, ["image"])[0]
    assert plan.promotion_errors(expected, {"image": heads["legacy"]}, ["image"])


def test_product_revision_changes_only_its_image_head(tmp_path):
    import shutil
    db_root = REPO_ROOT / "core/lib/controlplane-db/src/controlplane_db"
    shutil.copytree(db_root / "alembic", tmp_path / "alembic")
    shutil.copytree(db_root / "migration_chains", tmp_path / "migration_chains")
    versions = tmp_path / "alembic/versions"
    before = plan.alembic_head.deployment_heads(versions)
    (tmp_path / "migration_chains/hypermesh/versions/planted.py").write_text(
        f'revision = "hypermesh_new"\ndown_revision = "{before["hypermesh"]}"\n')
    after = plan.alembic_head.deployment_heads(versions)
    assert after["hypermesh"] == "hypermesh_new"
    assert {k:v for k,v in before.items() if k != "hypermesh"} == {k:v for k,v in after.items() if k != "hypermesh"}
