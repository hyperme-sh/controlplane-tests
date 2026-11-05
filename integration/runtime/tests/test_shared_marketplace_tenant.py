"""Every marketplace consumer resolves the same tenancy setting."""

import os
from pathlib import Path
import subprocess
import sys
import uuid

import pytest

ROOT = Path(__file__).resolve().parents[3]


@pytest.mark.parametrize("override", [False, True])
def test_marketplace_setting_is_shared_across_core_consumers(override):
    env = dict(os.environ)
    env.pop("AIUDITOR_CRAWL_TENANT_ID", None)
    env.pop("MARKETPLACE_TENANT_ID", None)
    if override:
        env["MARKETPLACE_TENANT_ID"] = str(uuid.uuid4())
    env.pop("PYTHONPATH", None)
    subprocess.run(
        [
            sys.executable,
            "-c",
            """
import os
from controlplane_tenancy.marketplace import MARKETPLACE_TENANT_ID
from controlplane_tenancy.tenant_membership import MARKETPLACE_TENANT_ID as member
assert MARKETPLACE_TENANT_ID == member
if os.environ.get('MARKETPLACE_TENANT_ID'):
    assert str(MARKETPLACE_TENANT_ID) == os.environ['MARKETPLACE_TENANT_ID']
""",
        ],
        env=env,
        check=True,
        capture_output=True,
        text=True,
    )

# Business flows must publish envelopes matching the registered wire contracts.
pytestmark = pytest.mark.usefixtures("validate_published_wire_contracts")
