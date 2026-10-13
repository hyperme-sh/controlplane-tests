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
