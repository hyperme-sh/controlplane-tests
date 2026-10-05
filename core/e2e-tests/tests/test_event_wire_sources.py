"""Owner serializers match checked-in consumer contracts, without runtime product coupling."""
import importlib.util
from pathlib import Path

from controlplane_events.contracts import event_schema

ROOT = Path(__file__).resolve().parents[3]
spec = importlib.util.spec_from_file_location('event_schema_generator', ROOT/'scripts/generate_event_contracts.py')
generator = importlib.util.module_from_spec(spec)
spec.loader.exec_module(generator)


def test_every_owner_payload_projection_matches_its_versioned_schema():
    for event, expected in generator.expected_schemas().items():
        assert event_schema(event) == expected, f'{event}: payload changed without a new schema version'
