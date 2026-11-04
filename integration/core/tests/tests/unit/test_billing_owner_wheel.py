"""Installed billing persistence requires no payment-service implementation."""
from test_identity_tenancy_libraries import wheel_run


def test_billing_owner_wheel_is_independent_and_hides_orm_surface():
    wheel_run("controlplane-db", """
import importlib.util, uuid
from controlplane_db.billing_store import BillingPayment, get_payment, transfer_scan_page
assert importlib.util.find_spec('payment_service') is None
assert importlib.util.find_spec('api') is None
assert importlib.util.find_spec('fastapi') is None
metadata = {'nested': {'state': 'pending'}}
payment = BillingPayment(id=uuid.uuid4(), tenant_id=uuid.uuid4(), scope='lease',
    actor='user', payment_type='usdc', currency='usdc', amount=100, status='pending',
    extra_metadata=metadata)
metadata['nested']['state'] = 'changed'
assert payment.extra_metadata['nested']['state'] == 'pending'
payment.extra_metadata['nested']['state'] = 'changed'
assert payment.extra_metadata['nested']['state'] == 'pending'
assert not hasattr(payment, '_sa_instance_state')
assert not hasattr(BillingPayment, '__table__')
""")
