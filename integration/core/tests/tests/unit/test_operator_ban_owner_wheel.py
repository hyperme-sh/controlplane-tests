"""The installed ban contract does not import any service application."""
from test_identity_tenancy_libraries import wheel_run


def test_operator_ban_owner_wheel_has_no_service_implementation_dependencies():
    wheel_run("operator-ban-store", """
import importlib.util
from operator_ban_store import active_ban, subject_banned_sync
for name in ('user_service', 'api', 'hypermesh_service', 'fastapi'):
    assert importlib.util.find_spec(name) is None
""")
