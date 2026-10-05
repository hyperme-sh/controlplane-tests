"""Wire consumers validate immutable schemas without publisher imports."""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import shutil
import uuid

import pytest
from jsonschema import Draft202012Validator, ValidationError

from controlplane_events.contracts import event_contracts, event_schema, validate_event_envelope, validate_event_payload
from controlplane_events.envelope import ControlPlaneEventEnvelope

ROOT = Path(__file__).resolve().parents[4]
spec = importlib.util.spec_from_file_location('event_checker', ROOT/'scripts/check_event_contracts.py')
checker = importlib.util.module_from_spec(spec)
spec.loader.exec_module(checker)


@pytest.mark.parametrize('event', sorted(event_contracts()['events']))
def test_all_registered_wire_schemas_are_valid(event):
    schema = event_schema(event)
    Draft202012Validator.check_schema(schema)
    assert schema['x-event-type'] == event
    assert schema['x-topic'] == 'controlplane.events.v1'
    assert schema['x-owner']


def test_payment_consumer_contract_rejects_bad_fields_and_tenant_mismatch():
    tid = uuid.uuid4()
    payload = {'tenant_id':str(tid), 'billing_record_id':str(uuid.uuid4())}
    validate_event_payload('payment.succeeded.v1', payload)
    for bad in ({**payload, 'amount_cents':-1}, {**payload, 'tenant_id':'not-a-uuid'},
                {**payload, 'prompt':'unpublished'}, {'tenant_id':str(tid)}):
        with pytest.raises(ValidationError):
            validate_event_payload('payment.succeeded.v1', bad)
    envelope = ControlPlaneEventEnvelope(event_type='payment.succeeded.v1', service='payment-service', tenant_id=tid, payload=payload)
    validate_event_envelope(envelope)
    with pytest.raises(ValueError, match='shared|controlplane.events.v1'):
        validate_event_envelope(envelope, topic='another-topic')
    with pytest.raises(ValueError, match='tenant'):
        validate_event_envelope(envelope.model_copy(update={'tenant_id':uuid.uuid4()}))
    with pytest.raises(KeyError):
        event_schema('payment.succeeded.v99')


def test_registry_reads_are_not_mutable_shared_state():
    registry = event_contracts()
    registry['events'].clear()
    assert event_contracts()['events']


def test_inventory_and_edge_bundle_agree():
    assert checker.check(ROOT) == []


def test_existing_event_version_changes_and_removals_fail(tmp_path, monkeypatch):
    shutil.copytree(ROOT/checker.SCHEMAS, tmp_path/checker.SCHEMAS)
    (tmp_path/'scripts').mkdir()
    shutil.copyfile(ROOT/'scripts/event_contract_sources.json', tmp_path/'scripts/event_contract_sources.json')
    for source in (ROOT/'products').glob('*/edge-routes.json'):
        destination = tmp_path/'products'/source.parent.name/'edge-routes.json'
        destination.parent.mkdir(parents=True)
        shutil.copyfile(source, destination)
    (tmp_path/checker.ROUTES).parent.mkdir(parents=True)
    shutil.copyfile(ROOT/checker.ROUTES, tmp_path/checker.ROUTES)
    relative = checker.SCHEMAS/'payment.succeeded.v1.json'
    previous = (tmp_path/relative).read_text()
    monkeypatch.setattr(checker.subprocess, 'check_output', lambda args, **kwargs:
                        str(relative)+'\n' if args[1] == 'ls-tree' else previous)
    current = json.loads(previous)
    current['properties']['amount_cents'] = {'type':'string'}
    (tmp_path/relative).write_text(json.dumps(current))
    assert any('published event version changed' in e for e in checker.check(tmp_path, 'base'))
    (tmp_path/relative).write_text(previous)
    assert checker.check(tmp_path, 'base') == []
    bundle = json.loads((tmp_path/checker.ROUTES).read_text())
    bundle['hypermesh']['edge_routes'].pop()
    (tmp_path/checker.ROUTES).write_text(json.dumps(bundle))
    assert 'product route declarations do not match edge bundle' in checker.check(tmp_path)


def test_installed_events_wheel_contains_all_wire_contracts():
    import os
    import subprocess
    env = dict(os.environ)
    env.pop('PYTHONPATH', None)
    env.pop('UV_NO_SYNC', None)
    result = subprocess.run([
        'uv','run','--isolated','--no-editable','--frozen','--no-dev',
        '--refresh-package','controlplane-events','--package','controlplane-events',
        'python','-c',
        "from controlplane_events.contracts import event_contracts, event_schema; "
        "events=event_contracts()['events']; "
        "assert all(event_schema(event)['x-event-type']==event for event in events); "
        "print(len(events))",
    ], cwd=ROOT, env=env, check=True, capture_output=True, text=True)
    assert int(result.stdout) == len(event_contracts()['events'])
