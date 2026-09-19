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


_ComposeLoader.add_multi_constructor(
    "!", lambda loader, suffix, node: loader.construct_object(node.__class__(
        tag=f"tag:yaml.org,2002:{'seq' if isinstance(node, yaml.SequenceNode) else 'map'}",
        value=node.value,
        start_mark=node.start_mark,
        end_mark=node.end_mark,
    ))
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
        image.split("/")[-1].split(":", 1)[0]
        for service in overlay["services"].values()
        if isinstance(service, dict) and (image := service.get("image", ""))
        if image.startswith("ghcr.io/fyberlabs/")
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
