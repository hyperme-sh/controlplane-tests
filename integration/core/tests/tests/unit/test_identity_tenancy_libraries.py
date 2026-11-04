"""Installed libraries and old import aliases preserve the identity boundary."""

import importlib
import os
from pathlib import Path
import subprocess
import uuid

ROOT = Path(__file__).resolve().parents[4]


def test_legacy_imports_share_implementation_and_session_context():
    mappings = {
        "api.common.internal_token": "controlplane_identity.internal_token",
        "api.common.person_bind": "controlplane_identity.person_bind",
        "api.common.portal_controls": "controlplane_identity.portal_controls",
        "api.infrastructure.externals.api_keys_client": "controlplane_identity.api_keys_client",
        "api.infrastructure.security.keycloak_jwt": "controlplane_identity.security.keycloak_jwt",
        "api.infrastructure.security.api_key_exchange_jwt": "controlplane_identity.security.api_key_exchange_jwt",
        "api.infrastructure.security.tenant_session_jwt": "controlplane_identity.security.tenant_session_jwt",
        "api.infrastructure.persistence.db_session": "controlplane_tenancy.db_session",
        "api.infrastructure.persistence.tenant_guc": "controlplane_tenancy.tenant_guc",
        "api.infrastructure.persistence.tenant_membership": "controlplane_tenancy.tenant_membership",
    }
    for old, new in mappings.items():
        assert importlib.import_module(old) is importlib.import_module(new)


def wheel_run(package, code, env=None):
    environment = dict(os.environ)
    environment.pop("PYTHONPATH", None)
    environment.pop("UV_NO_SYNC", None)
    environment.update(env or {})
    return subprocess.run(
        [
            "uv",
            "run",
            "--isolated",
            "--no-editable",
            "--frozen",
            "--no-dev",
            "--refresh-package",
            package,
            "--package",
            package,
            "python",
            "-c",
            code,
        ],
        cwd=ROOT,
        env=environment,
        capture_output=True,
        text=True,
        check=True,
    )


def test_identity_wheel_runs_without_legacy_api_or_products():
    wheel_run(
        "controlplane-identity",
        """
import sys, uuid
from types import SimpleNamespace
from controlplane_identity.configuration import configure_identity
from controlplane_identity.wallet import BaseWalletVerifier, CounterfactualValidator, WalletProofError
from controlplane_identity.internal_token import internal_token_matches
from controlplane_identity.person_bind import can_checkout_from_claims
from controlplane_identity.security.tenant_session_jwt import mint_host_setup_session, try_decode_host_setup_session, try_decode_tenant_session_jwt
configure_identity(SimpleNamespace(usable_api_key_exchange_secret=lambda: 'isolated-wheel-test-signing-secret-32-chars'))
assert internal_token_matches('secret', 'secret')
assert can_checkout_from_claims({'realm_access': {'roles': ['renter']}})
token = mint_host_setup_session(claims={'azp': 'hypermesh-host-setup', 'sub': 'host-user'}, tenant_id=uuid.uuid4())
assert try_decode_host_setup_session(token)['purpose'] == 'host_setup'
assert try_decode_tenant_session_jwt(token) is None
assert not any(name == 'api' or name.startswith('api.') or name.startswith('hypermesh_service') for name in sys.modules)
""",
    )


def test_tenancy_wheel_uses_shared_override_without_legacy_api():
    tenant_id = str(uuid.uuid4())
    wheel_run(
        "controlplane-tenancy",
        """
import os, sys
from controlplane_tenancy.marketplace import MARKETPLACE_TENANT_ID
from controlplane_tenancy.tenant_membership import MARKETPLACE_TENANT_ID as membership_tenant
from controlplane_tenancy.db_session import get_db_session
assert str(MARKETPLACE_TENANT_ID) == os.environ['MARKETPLACE_TENANT_ID']
assert MARKETPLACE_TENANT_ID == membership_tenant
assert not any(name == 'api' or name.startswith('api.') for name in sys.modules)
""",
        {"MARKETPLACE_TENANT_ID": tenant_id},
    )


def test_installed_legacy_core_wheel_keeps_compatibility_imports():
    wheel_run(
        "controlplane-core",
        """
import importlib
from api.core.config import settings
from controlplane_identity.configuration import settings as identity_settings
assert identity_settings.usable_api_key_exchange_secret() == settings.usable_api_key_exchange_secret()
assert importlib.import_module('api.infrastructure.persistence.db_session') is importlib.import_module('controlplane_tenancy.db_session')
assert importlib.import_module('api.infrastructure.security.keycloak_jwt') is importlib.import_module('controlplane_identity.security.keycloak_jwt')
""",
        {"FASTAPI_ENV": "test"},
    )
