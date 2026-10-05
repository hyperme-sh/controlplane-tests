"""The VM overlay must inject every credential the code fails closed without.

Compose loads the repo-root ``.env`` for ``${VAR}`` interpolation only. A name
that infra writes there but no service declares in ``environment:`` never
reaches the container. That is how ``HYPERMESH_TICKET_HMAC_KEY`` went missing:
provisioned from Key Vault, dropped at the container boundary, and surfaced as
``409 instance not ready`` on every chat instead of a config error.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[4]
VM_OVERLAY = REPO_ROOT / "docker-compose.vm.yaml"
ENV_EXAMPLE = REPO_ROOT / ".env.vm.example"

# Credential -> services that must receive it. Signing keys are symmetric, so
# both the signer and the verifier need the same value.
REQUIRED_BY_SERVICE: dict[str, tuple[str, ...]] = {
    "HYPERMESH_TICKET_HMAC_KEY": ("hypermesh-service", "hypermesh-gateway"),
    "HYPERMESH_GATEWAY_SERVICE_SECRET": ("hypermesh-service", "hypermesh-gateway"),
    "TURN_AUTH_SECRET": ("turn-service",),
}

# Shared secrets that must be identical across every service that speaks them.
REQUIRED_IN_SHARED_ANCHOR = (
    "API_KEYS_INTERNAL_TOKEN",
    "API_KEY_EXCHANGE_JWT_SECRET",
    "HYPERMESH_INTERNAL_TOKEN",
    "PAYMENT_INTERNAL_TOKEN",
)

# Optional at boot, but must still be declared or payment-service cannot see them.
DECLARED_BUT_OPTIONAL = {
    "payment-service": ("STRIPE_SECRET_KEY", "STRIPE_WEBHOOK_SECRET"),
}


class _ComposeLoader(yaml.SafeLoader):
    """SafeLoader that tolerates Compose merge tags such as ``!override``."""


def _compose_tag_kind(node: yaml.Node) -> str:
    if isinstance(node, yaml.SequenceNode):
        return "seq"
    if isinstance(node, yaml.ScalarNode):
        return "str"
    return "map"


_ComposeLoader.add_multi_constructor(
    "!",
    lambda loader, suffix, node: loader.construct_object(
        node.__class__(
            tag=f"tag:yaml.org,2002:{_compose_tag_kind(node)}",
            value=node.value,
            start_mark=node.start_mark,
            end_mark=node.end_mark,
        )
    ),
)


@pytest.fixture(scope="module")
def overlay() -> dict:
    return yaml.load(VM_OVERLAY.read_text(), Loader=_ComposeLoader)


def _service_env(overlay: dict, service: str) -> dict[str, str]:
    """Resolved environment for a service, including the merged shared anchor."""

    services = overlay["services"]
    assert service in services, f"{service} missing from {VM_OVERLAY.name}"
    env = services[service].get("environment") or {}
    assert isinstance(env, dict), f"{service} environment must be a mapping"
    return {str(k): str(v) for k, v in env.items()}


@pytest.mark.parametrize(
    ("name", "services"),
    [(name, services) for name, services in REQUIRED_BY_SERVICE.items()],
)
def test_required_secret_reaches_every_service(
    overlay: dict, name: str, services: tuple[str, ...]
) -> None:
    for service in services:
        env = _service_env(overlay, service)
        assert name in env, (
            f"{service} does not declare {name}; the value in .env would never "
            f"reach the container"
        )
        assert f"${{{name}:?" in env[name], (
            f"{service} must require {name} with ${{{name}:?...}} so compose "
            f"refuses to start rather than booting a broken plane"
        )


@pytest.mark.parametrize("name", REQUIRED_IN_SHARED_ANCHOR)
def test_shared_secret_is_required_on_every_api_service(overlay: dict, name: str) -> None:
    """Anchored into x-vm-api-env so mint and verify sides cannot drift."""

    for service in ("controlplane-edge", "payment-service", "hypermesh-service", "hypermesh-gateway"):
        env = _service_env(overlay, service)
        assert name in env, f"{service} does not declare {name}"
        assert f"${{{name}:?" in env[name], f"{service} must require {name}"


@pytest.mark.parametrize(
    ("service", "names"),
    [(service, names) for service, names in DECLARED_BUT_OPTIONAL.items()],
)
def test_optional_secret_is_still_declared(
    overlay: dict, service: str, names: tuple[str, ...]
) -> None:
    env = _service_env(overlay, service)
    for name in names:
        assert name in env, f"{service} does not declare {name}"


def test_pricing_service_token_is_passed_through_without_a_literal_default(overlay: dict) -> None:
    """infra#135 treats the KV secret as optional. ``:?`` would fail the whole plane."""

    for service in ("hypermesh-service", "hypermesh-pricing"):
        env = _service_env(overlay, service)
        assert env.get("PRICING_SERVICE_TOKEN") == "${PRICING_SERVICE_TOKEN:-}", (
            f"{service} must pass PRICING_SERVICE_TOKEN through with an empty fallback"
        )
    depends = overlay["services"]["hypermesh-service"].get("depends_on") or {}
    if isinstance(depends, dict):
        pricing = depends.get("hypermesh-pricing")
        assert pricing is None or pricing.get("condition") != "service_healthy"
    else:
        assert "hypermesh-pricing" not in depends


def test_base_compose_does_not_wait_for_pricing_health() -> None:
    base = yaml.load((REPO_ROOT / "docker-compose.yaml").read_text(), Loader=_ComposeLoader)
    service = base["services"]["hypermesh-service"]
    depends = service.get("depends_on") or {}
    assert "hypermesh-pricing" not in depends
    assert base["services"]["hypermesh-pricing"].get("profiles") == ["pricing"]


_DIGEST_IMAGE = re.compile(r"^ghcr\.io/fyberlabs/hypermesh-pricing@sha256:[0-9a-f]{64}$")


def test_pricing_image_must_be_a_digest() -> None:
    """A tag such as ``:v0.1.1`` or ``:test`` is not a pin. The real digest lands here."""

    assert _DIGEST_IMAGE.fullmatch("ghcr.io/fyberlabs/hypermesh-pricing:v0.1.1") is None
    assert _DIGEST_IMAGE.fullmatch("ghcr.io/fyberlabs/hypermesh-pricing:test") is None
    assert _DIGEST_IMAGE.fullmatch("ghcr.io/fyberlabs/hypermesh-pricing@sha256:abcd") is None
    for path in (REPO_ROOT / "docker-compose.yaml", VM_OVERLAY):
        fallbacks = re.findall(r"\$\{HYPERMESH_PRICING_IMAGE:-(.+?)\}", path.read_text())
        assert fallbacks, f"{path.name} has no HYPERMESH_PRICING_IMAGE default"
        for image in fallbacks:
            assert _DIGEST_IMAGE.fullmatch(image), image
    for line in ENV_EXAMPLE.read_text().splitlines():
        if not line.startswith("HYPERMESH_PRICING_IMAGE="):
            continue
        value = line.split("=", 1)[1].strip()
        if value:
            assert _DIGEST_IMAGE.fullmatch(value), value
    script = (REPO_ROOT / "scripts" / "compose-vm.sh").read_text()
    assert "--profile pricing" in script


def test_no_secret_defaults_to_a_value_committed_to_git(overlay: dict) -> None:
    """``${VAR:-literal}`` on a credential ships a usable secret in the repo."""

    offenders: list[str] = []
    for service, spec in overlay["services"].items():
        env = spec.get("environment") or {}
        if not isinstance(env, dict):
            continue
        for name, raw in env.items():
            if not any(w in str(name) for w in ("SECRET", "TOKEN", "PASSWORD", "HMAC_KEY")):
                continue
            value = str(raw)
            if f"${{{name}:-" not in value:
                continue
            fallback = value.split(f"${{{name}:-", 1)[1].rsplit("}", 1)[0]
            if fallback.strip():
                offenders.append(f"{service}.{name} falls back to {fallback!r}")
    assert not offenders, "credentials must fail closed, not default: " + "; ".join(offenders)


@pytest.mark.parametrize(
    "name",
    sorted({*REQUIRED_BY_SERVICE, *REQUIRED_IN_SHARED_ANCHOR}),
)
def test_env_example_lists_every_required_name(name: str) -> None:
    """Operators copy this file; a name absent here is a name nobody sets."""

    declared = {
        line.split("=", 1)[0].strip()
        for line in ENV_EXAMPLE.read_text().splitlines()
        if "=" in line and not line.lstrip().startswith("#")
    }
    assert name in declared, f"{name} is required but absent from {ENV_EXAMPLE.name}"


# Digest-pinned images we pull and do not build. ghcr-test.yml stays unchanged.
_EXTERNAL_DIGEST_IMAGES = frozenset({"hypermesh-pricing"})
_DIGEST_PIN = re.compile(r"@sha256:([0-9a-f]{64})")


def _ci_built_image_name(image: str) -> str | None:
    """Repo name CI must publish, or None for a digest-pinned external image."""

    text = image.strip()
    if text.startswith("${") and ":-" in text:
        text = text.split(":-", 1)[1]
        if text.endswith("}"):
            text = text[:-1]
    marker = "ghcr.io/fyberlabs/"
    if marker not in text:
        return None
    repo = text.split(marker, 1)[1]
    if "@sha256:" in repo:
        name = repo.split("@", 1)[0]
        digest = _DIGEST_PIN.search(repo)
        if name in _EXTERNAL_DIGEST_IMAGES and digest is not None:
            return None
        return name
    return repo.split(":", 1)[0].split("@", 1)[0]


def test_every_pulled_image_is_one_ci_builds() -> None:
    """A pull name CI never pushes silently pins the VM to a stale image.

    The overlay pulls by name and never builds, so renaming a service without
    renaming its ``image:`` is invisible: compose keeps pulling the old
    repository, which still exists and still has a ``:test`` tag, so the pull
    succeeds and the plane quietly runs pre-rename code. That is what
    ``ghcr.io/fyberlabs/gateway`` did after the service became
    ``controlplane-edge``.
    """

    overlay = yaml.load(VM_OVERLAY.read_text(), Loader=_ComposeLoader)
    pulled = {
        name
        for service in overlay["services"].values()
        if isinstance(service, dict) and (image := service.get("image", ""))
        if (name := _ci_built_image_name(str(image)))
    }

    workflow = (REPO_ROOT / ".github" / "workflows" / "ghcr-test.yml").read_text()
    # The publish matrix is the dorny/paths-filter block: one 8-space key per
    # image, each with its own path globs.
    built = set(re.findall(r"^ {12}([a-z][a-z0-9-]*):$", workflow, re.M))

    missing = sorted(pulled - built)
    assert not missing, (
        "the VM overlay pulls images ghcr-test.yml never builds: "
        + ", ".join(missing)
        + f" (built: {', '.join(sorted(built))})"
    )


def test_deploy_pointer_moves_only_after_the_fleet_is_verified() -> None:
    """``deploy/test`` is the whole deploy decision, so it must trail the proof.

    The VM checks out that ref and pins its images to the sha it names. Moving
    the advance ahead of the check that every image resolves would hand the VM
    a sha whose images are still building, which is the pull failure the pin
    was meant to end.
    """

    workflow = yaml.safe_load(
        (REPO_ROOT / ".github" / "workflows" / "ghcr-test.yml").read_text()
    )
    steps = [
        step.get("name") or step.get("uses", "")
        for step in workflow["jobs"]["deployable"]["steps"]
    ]

    advance = "Advance deploy/test to this sha"
    verified = "Every compose image resolves at this sha"
    assert advance in steps, f"{advance!r} is gone, so nothing tells the VM what to run"
    assert steps.index(verified) < steps.index(advance), (
        f"{advance!r} runs before {verified!r}"
    )
    assert steps[-1] == advance, (
        "the pointer must be the last thing this job does so every check gates "
        f"it, but the last step is {steps[-1]!r}"
    )

    # The first run has no branch to move, and asking the API to update a ref
    # that does not exist answers 422, not the 404 a status-code guess expects.
    # That left deploy/test uncreated and the gate red.
    advance_run = next(
        step["run"]
        for step in workflow["jobs"]["deployable"]["steps"]
        if step.get("name") == advance
    )
    assert "-X POST" in advance_run and "-X PATCH" in advance_run, (
        "the step has to create deploy/test as well as move it -- on the first "
        "run there is nothing to move"
    )


def _app_services(overlay: dict) -> dict[str, dict]:
    """Services running one of our images, i.e. the ones we can crash."""

    return {
        name: cfg
        for name, cfg in overlay["services"].items()
        if "ghcr.io/fyberlabs" in str((cfg or {}).get("image", ""))
    }


# Opt-in jobs (a ``profiles`` key) retry a bounded number of times, then stop.
_BOUNDED_ON_FAILURE = re.compile(r"^on-failure:([1-9][0-9]*)$")


def test_every_app_service_restarts(overlay: dict) -> None:
    """A service that exits must not stay down silently.

    hypermesh-service exited on a bad schema and sat dead for fourteen hours
    while the other twenty containers reported healthy, because a restart policy
    is set on postgres/redis/keycloak and on nothing else. The VM is
    unattended, so "it stopped" has to become visible or self-correcting.

    App services with no ``profiles`` key stay on ``unless-stopped``. A service
    with ``profiles`` is an opt-in manual job, so it is exempt from that rule.
    Each of those jobs uses ``restart: on-failure:N`` with N a positive integer,
    so a failed run stops after a bounded number of attempts.
    """

    app = _app_services(overlay)
    assert len(app) >= 15, f"only found {len(app)} app services, the scan is wrong"

    # controlplane-migrate must exit 0 and stay stopped. unless-stopped would
    # start another alembic upgrade as soon as the successful one finished.
    one_shot = {"controlplane-migrate"}
    for name in one_shot:
        assert app[name].get("restart") == "no", f"{name} must not restart after success"

    always_on = {
        n: c for n, c in app.items() if "profiles" not in c and n not in one_shot
    }
    manual = {n: c for n, c in app.items() if "profiles" in c}

    missing = sorted(n for n, c in always_on.items() if c.get("restart") != "unless-stopped")
    assert not missing, f"no restart policy on: {', '.join(missing)}"

    unbounded = sorted(
        n
        for n, c in manual.items()
        if _BOUNDED_ON_FAILURE.fullmatch(str(c.get("restart") or "")) is None
    )
    assert not unbounded, (
        "profile-gated services need restart on-failure:N with N a positive integer: "
        + ", ".join(f"{n}={manual[n].get('restart')!r}" for n in unbounded)
    )


def test_every_app_service_is_capped(overlay: dict) -> None:
    """One service must not be able to take the box with it.

    The box has no swap, so exhaustion is a hang: at 245 MiB available nothing
    could fork, and sshd and the Azure guest agent both stopped answering, which
    is a VM that cannot be recovered in band or out of band.
    """

    uncapped = sorted(n for n, c in _app_services(overlay).items() if not c.get("mem_limit"))
    assert not uncapped, f"no mem_limit on: {', '.join(uncapped)}"

    # Keycloak's JVM is container-aware, so the cap also caps its heap.
    # Postgres is deliberately uncapped: page cache counts against a cgroup,
    # so a limit there caps caching and vacuum dies first.
    assert overlay["services"]["keycloak"].get("mem_limit"), "keycloak is uncapped"
    assert not overlay["services"]["postgres"].get("mem_limit"), (
        "capping Postgres limits how much of the database it may cache"
    )


def test_aiuditor_token_is_optional_but_reaches_both_sides(overlay: dict) -> None:
    for service in ("controlplane-edge", "payment-service", "hypermesh-service", "hypermesh-gateway", "aiuditor-service"):
        assert _service_env(overlay, service)["AIUDITOR_INTERNAL_TOKEN"] == "${AIUDITOR_INTERNAL_TOKEN:-}"


def test_public_test_endpoints_are_owned_by_vm_overlay(overlay: dict) -> None:
    for service in ("hypermesh-service", "hypermesh-gateway"):
        assert _service_env(overlay, service)["HYPERMESH_WG_ENDPOINT"] == "${HYPERMESH_WG_ENDPOINT:-wg.test.hyperme.sh:51820}"
    gateway = _service_env(overlay, "hypermesh-gateway")
    assert gateway["HYPERMESH_CONTROL_URL"] == "${HYPERMESH_CONTROL_URL:-https://api.test.hyperme.sh}"
    assert gateway["HYPERMESH_CONTROL_PLANE_URL"] == "http://hypermesh-service:8000"


def test_known_host_plane_defaults_do_not_need_infra_exports(overlay: dict) -> None:
    env = _service_env(overlay, "hypermesh-known-host-enroll")
    assert env["HYPERMESH_API_BASE"] == "${HYPERMESH_API_BASE:-https://api.test.hyperme.sh}"
    assert env["HYPERMESH_PORTAL_ORIGIN"] == "${HYPERMESH_PORTAL_ORIGIN:-https://portal.test.hyperme.sh}"
    assert env["HYPERMESH_OIDC_ISSUER"] == "${HYPERMESH_OIDC_ISSUER:-https://auth.test.hyperme.sh/realms/controlplane}"
    assert env["HYPERMESH_PROD_API_BASE"] == "${HYPERMESH_PROD_API_BASE:-}"
    assert env["HYPERMESH_PROD_PORTAL_ORIGIN"] == "${HYPERMESH_PROD_PORTAL_ORIGIN:-}"
    for setting in (
        "HYPERMESH_HOST_TARGET",
        "HYPERMESH_SMOKE_HOST_NAME",
        "HYPERMESH_HOST_CLASS_ID",
    ):
        assert env[setting] == "${" + setting + ":-}"
    assert not any(key.startswith("HYPERMESH_HOST_TARGET_") for key in env)


def test_test_reload_selects_owner_runner_for_every_schema_applicator(overlay):
    for service in ('controlplane-migrate', 'controlplane-edge', 'hypermesh-service'):
        assert overlay['services'][service]['environment']['CONTROLPLANE_MIGRATION_MODE'] == 'owners'
    base = yaml.load((REPO_ROOT / 'docker-compose.yaml').read_text(), Loader=_ComposeLoader)
    assert base['services']['controlplane-migrate']['command'][-3:] == [
        'python', '-m', 'controlplane_tenancy.startup']
