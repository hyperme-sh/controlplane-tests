from __future__ import annotations

import json
import uuid

from controlplane_events import ControlPlaneEventEnvelope, UserCreatedEvent


def test_user_created_to_envelope_payload_roundtrip():
    uid = uuid.uuid4()
    ev = UserCreatedEvent(
        user_id=uid,
        email="new@example.com",
        tenant_id=str(uuid.uuid4()),
        display_name="New User",
    )
    payload = ev.to_envelope_payload()
    restored = UserCreatedEvent.from_envelope_payload(payload)
    assert restored == ev
    json.dumps(payload)


def test_user_created_wrap_envelope_json_serializable():
    uid = uuid.uuid4()
    tid = uuid.uuid4()
    ev = UserCreatedEvent(
        user_id=uid,
        email="wrap@example.com",
        tenant_id=str(tid),
    )
    env = ev.wrap_envelope(service="gateway", tenant_uuid=tid)
    assert isinstance(env, ControlPlaneEventEnvelope)
    assert env.event_type == "user.created.v1"
    assert env.service == "gateway"
    assert env.tenant_id == tid
    blob = env.model_dump(mode="json")
    s = json.dumps(blob)
    loaded = json.loads(s)
    assert loaded["payload"]["user_id"] == str(uid)
