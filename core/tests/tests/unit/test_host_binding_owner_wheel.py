"""Auth enrollment imports only the owner contract, not the Hypermesh app."""
from test_identity_tenancy_libraries import wheel_run


def test_host_binding_owner_wheel_has_no_application_dependencies():
    wheel_run("host-binding-store", """
import importlib.util
from host_binding_store import bind_organization, lock_identity, organization_id
for name in ('hypermesh_service', 'auth_service', 'controlplane_db', 'api', 'fastapi'):
    assert importlib.util.find_spec(name) is None
""")
