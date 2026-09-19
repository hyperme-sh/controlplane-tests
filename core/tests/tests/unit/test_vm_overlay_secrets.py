"""The VM overlay must inject every credential the code fails closed without.

Compose loads the repo-root ``.env`` for ``${VAR}`` interpolation only. A name
that infra writes there but no service declares in ``environment:`` never
reaches the container. That is how ``HYPERMESH_TICKET_HMAC_KEY`` went missing:
provisioned from Key Vault, dropped at the container boundary, and surfaced as
``409 instance not ready`` on every chat instead of a config error.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[4]
VM_OVERLAY = REPO_ROOT / "docker-compose.vm.yaml"
ENV_EXAMPLE = REPO_ROOT / ".env.vm.example"

# Credential -> services that must receive it. Signing keys are symmetric, so
# both the signer and the verifier need the same value.
REQUIRED_BY_SERVICE: dict[str, tuple[str, ...]] = {
    "HYPERMESH_TICKET_HMAC_KEY": ("hypermesh-service", "hypermesh-router"),
    "HYPERMESH_ROUTER_SERVICE_SECRET": ("hypermesh-service", "hypermesh-router"),
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

    for service in ("controlplane-edge", "payment-service", "hypermesh-service", "hypermesh-router"):
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
