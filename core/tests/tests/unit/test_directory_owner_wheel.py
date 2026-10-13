"""Platform directory installs without user-service or legacy implementations."""
from test_identity_tenancy_libraries import wheel_run


def test_directory_owner_wheel_has_only_platform_dependencies():
    wheel_run("controlplane-tenancy", """
import importlib.util, uuid
from dataclasses import FrozenInstanceError
from controlplane_tenancy.directory import UserView, RoleView, UserCreateData, SqlMembershipRepository
assert importlib.util.find_spec('user_service') is None
assert importlib.util.find_spec('api') is None
assert importlib.util.find_spec('fastapi') is None
attrs = {'teams': ['one']}
user = UserView(uuid.uuid4(), uuid.uuid4(), 'user@example.com', 'User', 'View', 'unknown', True, None, None, None, attrs)
user.sociacl_attributes['teams'].append('two')
assert user.sociacl_attributes == {'teams': ['one']}
assert not hasattr(user, '_sa_instance_state')
try:
    user.first_name = 'changed'
except FrozenInstanceError:
    pass
else:
    raise AssertionError('mutable directory view')
""")
