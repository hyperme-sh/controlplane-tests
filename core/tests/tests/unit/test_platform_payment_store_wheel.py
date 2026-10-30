"""The platform owner contract installs without service implementations."""
from test_identity_tenancy_libraries import wheel_run


def test_platform_payment_store_wheel_has_no_http_or_service_dependency():
    wheel_run("controlplane-db", """
import importlib.util, uuid
from dataclasses import FrozenInstanceError
from controlplane_db.payment_store import StripeCredentials, UnstablePayerIdentity, mismatch_person_sub
assert importlib.util.find_spec('payment_service') is None
assert importlib.util.find_spec('api') is None
assert importlib.util.find_spec('fastapi') is None
credentials = StripeCredentials(uuid.uuid4(), 'sk_test_private')
assert 'sk_test_private' not in repr(credentials)
try:
    credentials.stripe_secret_key = 'replacement'
except FrozenInstanceError:
    pass
else:
    raise AssertionError('mutable credentials')
try:
    mismatch_person_sub(None, uuid.uuid4(), 'apikey:invalid')
except UnstablePayerIdentity:
    pass
else:
    raise AssertionError('invalid key accepted')
""")


def test_platform_budget_store_wheel_has_no_service_dependency():
    wheel_run("controlplane-db", """
import importlib.util, uuid
from datetime import datetime, timezone
from dataclasses import FrozenInstanceError
from controlplane_db.budget_store import AgentBudget, try_allocate, burn_legacy_budget_allocation
assert importlib.util.find_spec('payment_service') is None
assert importlib.util.find_spec('api') is None
stamp = datetime.now(timezone.utc)
budget = AgentBudget(uuid.uuid4(), uuid.uuid4(), None, None, 'owner', 'mint', 100, 0, stamp, stamp)
assert not hasattr(budget, '_sa_instance_state')
try:
    budget.allocated_lamports = 1
except FrozenInstanceError:
    pass
else:
    raise AssertionError('mutable budget view')
assert try_allocate(None, budget.tenant_id, budget.id, 0) is None
burn_legacy_budget_allocation(None, budget.tenant_id, budget_id=budget.id, user_id=None, atoms=0)
""")
