"""The scaler wire carries identity, never commerce fields."""

import uuid

import pytest
from pydantic import ValidationError

from controlplane_events.envelope import ControlPlaneEventEnvelope
from controlplane_events.envelope_parse import payload_matches_envelope_tenant
from controlplane_events.payloads.allocation_events import (
    ALLOCATION_ENDED_V1,
    ALLOCATION_REQUESTED_V1,
    HOST_ENROLLED_V1,
    AllocationEndedPayload,
    AllocationRequestedPayload,
    HostEnrolledPayload,
)


@pytest.mark.parametrize(("model", "event_type"), [
    (AllocationRequestedPayload, ALLOCATION_REQUESTED_V1),
    (AllocationEndedPayload, ALLOCATION_ENDED_V1),
    (HostEnrolledPayload, HOST_ENROLLED_V1),
])
def test_roundtrip_and_tenant_boundary(model, event_type):
    fields = {"tenant_id": uuid.uuid4(), "device_id": uuid.uuid4()}
    if model is not HostEnrolledPayload:
        fields.update(allocation_id=uuid.uuid4(), workload_id=uuid.uuid4())
    payload = model(**fields)
    envelope = ControlPlaneEventEnvelope(
        event_type=event_type, service="scaler", tenant_id=fields["tenant_id"],
        payload=payload.to_envelope_payload(),
    )
    decoded = ControlPlaneEventEnvelope.model_validate_json(envelope.model_dump_json())
    assert model.model_validate(decoded.payload) == payload
    assert payload_matches_envelope_tenant(decoded, payload)
    other_tenant = model(**(fields | {"tenant_id": uuid.uuid4()}))
    assert not payload_matches_envelope_tenant(decoded, other_tenant)


@pytest.mark.parametrize("model", [AllocationRequestedPayload, AllocationEndedPayload, HostEnrolledPayload])
@pytest.mark.parametrize("field", ["stripe_session_id", "billing_record_id", "gross_cents", "amount_cents", "funding_ref"])
def test_commerce_and_funding_details_do_not_cross_event_boundary(model, field):
    fields = {"tenant_id": uuid.uuid4(), "device_id": uuid.uuid4(), field: "private"}
    if model is not HostEnrolledPayload:
        fields["allocation_id"] = uuid.uuid4()
    with pytest.raises(ValidationError):
        model(**fields)


def test_request_before_placement_and_hardware_allocation():
    request = AllocationRequestedPayload(tenant_id=uuid.uuid4(), allocation_id=uuid.uuid4())
    assert request.device_id is None
    assert request.workload_id is None
    with pytest.raises(ValidationError):
        AllocationRequestedPayload(tenant_id="invalid", allocation_id=uuid.uuid4())
