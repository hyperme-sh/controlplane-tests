"""Every marketplace consumer resolves the same tenancy setting."""

import os
from pathlib import Path
import subprocess
import sys
import uuid

import pytest

ROOT = Path(__file__).resolve().parents[3]


@pytest.mark.parametrize("override", [False, True])
def test_marketplace_setting_is_shared_across_products(override):
    env = dict(os.environ)
    env.pop("AIUDITOR_CRAWL_TENANT_ID", None)
    env.pop("MARKETPLACE_TENANT_ID", None)
    if override:
        env["MARKETPLACE_TENANT_ID"] = str(uuid.uuid4())
    env["PYTHONPATH"] = os.pathsep.join(
        str(path)
        for area in ("core", "products")
        for path in (ROOT / area).rglob("src")
        if ".venv" not in path.parts
    )
    subprocess.run(
        [
            sys.executable,
            "-c",
            """
import os
from controlplane_tenancy.marketplace import MARKETPLACE_TENANT_ID
from controlplane_tenancy.tenant_membership import MARKETPLACE_TENANT_ID as member
from hypermesh_service.platform_identity import MARKETPLACE_TENANT_ID as plane
from hypermesh_gateway_service.tenants import MARKETPLACE_TENANT_ID as gateway
from aiuditor_service.intake import MARKETPLACE_TENANT_ID as auditor, resolve_crawl_tenant
assert MARKETPLACE_TENANT_ID == member == plane == gateway == auditor == resolve_crawl_tenant()
if os.environ.get('MARKETPLACE_TENANT_ID'):
    assert str(MARKETPLACE_TENANT_ID) == os.environ['MARKETPLACE_TENANT_ID']
""",
        ],
        env=env,
        check=True,
        capture_output=True,
        text=True,
    )
