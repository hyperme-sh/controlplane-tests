"""Installed API scaffolding preserves legacy state and HTTP contracts."""
import importlib

import pytest

from test_identity_tenancy_libraries import wheel_run

ALIASES = {'api.core.config': 'controlplane_api.config', 'api.common.logging': 'controlplane_api.logging', 'api.common.exceptions': 'controlplane_api.exceptions', 'api.common.utils': 'controlplane_api.utils', 'api.common.event_types': 'controlplane_api.event_types', 'api.infrastructure.metrics': 'controlplane_api.metrics', 'api.infrastructure.service_cors': 'controlplane_api.service_cors', 'api.interfaces.api_controllers.health_endpoint': 'controlplane_api.health', 'api.interfaces.api_controllers.version_endpoint': 'controlplane_api.version', 'api.domain.enum.permission': 'controlplane_api.permissions', 'api.domain.operator.plugins': 'controlplane_api.operator.plugins', 'api.common.dtos.health_dto': 'controlplane_api.dtos.health', 'api.domain.dtos.audit_logs_dto': 'controlplane_api.dtos.audit'}


def test_legacy_scaffolding_aliases_share_all_implementations(monkeypatch):
    for old, new in ALIASES.items():
        assert importlib.import_module(old) is importlib.import_module(new)
    from api.core.config import settings as old
    from controlplane_api.config import settings as new
    monkeypatch.setattr(old, "app_title", "Same process settings")
    assert new.app_title == "Same process settings"
    from api.common.exceptions import ForbiddenException as legacy_error
    from controlplane_api.exceptions import ForbiddenException
    assert legacy_error is ForbiddenException


def test_api_wheel_has_no_legacy_or_product_dependency():
    wheel_run("controlplane-api", """
import importlib.util, sys
from fastapi import FastAPI
from fastapi.testclient import TestClient
from controlplane_api.config import settings
from controlplane_identity.configuration import settings as identity_settings
from controlplane_api import metrics
from controlplane_api.health import router as health
from controlplane_api.version import router as version
from controlplane_api.operator.plugins import plugin_registry
from controlplane_api.utils import get_app_title
assert importlib.util.find_spec('api') is None
assert not any(name.startswith(('hypermesh_service', 'aiuditor_service', 'user_service')) for name in sys.modules)
assert identity_settings.usable_api_key_exchange_secret() == settings.usable_api_key_exchange_secret()
assert get_app_title() == settings.app_title
app = FastAPI()
app.include_router(health, prefix='/api/v1')
app.include_router(version)
with TestClient(app) as client:
    assert client.get('/api/v1/health/').json() == {'status': 'Ok'}
    assert client.get('/version').json() == {'service': 'api-wheel-test', 'git_sha': 'unknown', 'built_at': 'unknown'}
assert metrics.HTTP_REQUESTS is not None
assert [row['id'] for row in plugin_registry()] == ['hypermesh.aiuditor', 'hypermesh.models', 'hypermesh.hosts', 'hypermesh.aimmune', 'tennessee-windage']
""", {"SERVICE_NAME": "api-wheel-test", "GIT_SHA": "", "SOURCE_SHA": "", "HYPERMESH_SOURCE_SHA": "",
       "BUILT_AT": "", "PANOPTICON_IMAGE_DIGEST": "", "IMAGE_DIGEST": "", "CPEDGE_HYPERMESH_SERVICE_URL": ""})


def test_plugin_registration_does_not_alias_or_overwrite_declarations():
    from controlplane_api.operator.plugins import PLUGINS, known_action, plugin_by_id, register_plugin
    before = list(PLUGINS)
    declaration = {"id": "example.local", "title": "Example", "kind": "builtin", "permission": "operator:support",
                   "reads": ["/example"], "actions": ["inspect"]}
    try:
        register_plugin(declaration)
        declaration["actions"].append("mutate")
        assert known_action("example.local", "inspect")
        assert not known_action("example.local", "mutate")
        row = plugin_by_id("example.local")
        row["actions"].append("overwrite")
        assert not known_action("example.local", "overwrite")
        with pytest.raises(ValueError, match="already registered"):
            register_plugin(declaration)
        with pytest.raises(ValueError, match="required"):
            register_plugin({"id": "invalid"})
    finally:
        PLUGINS[:] = before
