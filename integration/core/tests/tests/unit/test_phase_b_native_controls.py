"""Complete native-policy evidence is accepted without fabricated Express probes."""

from datetime import datetime, timedelta
from uuid import NAMESPACE_URL, uuid5

import pytest
import test_phase_b_evidence as legacy
from phase_b_native_controls import FACTORY_HASHES

bundle = legacy.bundle


def native_bundle(data):
    controls = []

    def find(value):
        if isinstance(value, dict):
            if isinstance(value.get("payout_controls"), dict):
                controls.append(value)
            for child in value.values():
                find(child)
        elif isinstance(value, list):
            for child in value:
                find(child)

    find(data)
    approved = min(
        datetime.fromisoformat(row["payout_controls"]["recorded_at"])
        for row in controls
    ) - timedelta(minutes=1)
    for ready in controls:
        row = ready["payout_controls"]
        binding = row["ownership_snapshot"]["subject_to_host"]
        tenant, host, account, bank = (
            binding["tenant_id"],
            binding["host_org_id"],
            binding["stripe_account_id"],
            ready["bank_id"],
        )
        observed = datetime.fromisoformat(row["recorded_at"])
        current = {
            "source": "hypermesh_host_management",
            "tenant_id": tenant,
            "host_org_id": host,
            "subject": binding["subject"],
            "member_id": str(uuid5(NAMESPACE_URL, "native-member" + tenant + host)),
            "active": True,
            "observed_at": (observed - timedelta(seconds=1)).isoformat(),
        }
        token = uuid5(NAMESPACE_URL, row["id"]).hex
        platform = ready["platform_payout_schedule"]["account_id"]
        native = {
            "policy": "embedded-api/v1",
            "api_version": "2026-03-25.dahlia",
            "sdk_version": "15.0.0",
            "tenant_id": tenant,
            "host_org_id": host,
            "enrollment_attempt_id": binding["enrollment_attempt_id"],
            "account_id": account,
            "bank_id": bank,
            "platform_id": platform,
            "owner_sub": binding["subject"],
            "livemode": False,
            "access_epoch": 1,
            "policy_hashes": dict(FACTORY_HASHES),
            "started_at": (observed - timedelta(seconds=2)).isoformat(),
            "observed_at": observed.isoformat(),
            "request_ids": {
                "platform": "req_platform" + token,
                "platform_balance": "req_balance" + token,
                "account": "req_account" + token,
            },
            "native_platform": {"object": "account", "id": platform},
            "native_platform_balance": {"object": "balance", "livemode": False},
            "native_account": {
                "id": account,
                "object": "v2.core.account",
                "livemode": False,
                "closed": False,
                "dashboard": "none",
                "applied_configurations": ["recipient"],
                "metadata": {"tenant_id": tenant, "host_org_id": host},
                "identity": {"country": "US"},
                "defaults": {
                    "responsibilities": {
                        "fees_collector": "application",
                        "losses_collector": "application",
                        "requirements_collector": "application",
                    }
                },
                "configuration": {
                    "recipient": {
                        "capabilities": {
                            "stripe_balance": {"stripe_transfers": {"status": "active"}}
                        }
                    }
                },
                "requirements": {"entries": [], "summary": {"minimum_deadline": None}},
            },
            "current_owner": current,
            "bank_approval": {
                "approval_id": str(
                    uuid5(NAMESPACE_URL, "native-approval" + tenant + host + bank)
                ),
                "tenant_id": tenant,
                "host_org_id": host,
                "enrollment_attempt_id": binding["enrollment_attempt_id"],
                "platform_id": platform,
                "account_id": account,
                "bank_id": bank,
                "owner_sub": binding["subject"],
                "member_id": current["member_id"],
                "access_epoch": 1,
                "policy_hashes": dict(FACTORY_HASHES),
                "approved_at": approved.isoformat(),
                "second_factor_at": (approved - timedelta(minutes=1)).isoformat(),
            },
            "grant_fence": {
                "active_or_unknown": False,
                "access_epoch": 1,
                "observed_at": observed.isoformat(),
            },
        }
        row.pop("host_probes", None)
        row.pop("signed_report", None)
        row.update(
            policy="embedded-api/v1",
            source="stripe_native_account_policy",
            access_epoch=1,
            native_controls=native,
            current_owner=current,
        )
    return data


def test_complete_native_bundle_requires_no_express_refusal_probe(bundle):
    assert legacy.check(native_bundle(bundle)) == []


@pytest.mark.parametrize(
    "case",
    [
        "dashboard",
        "epoch",
        "bank",
        "member",
        "request",
        "fence",
        "factory",
        "legacy_probes",
    ],
)
def test_native_control_omissions_cannot_be_replaced_by_a_legacy_label(bundle, case):
    data = native_bundle(bundle)
    controls = data["gateway_runs"][0]["readiness"]["payout_controls"]
    native = controls["native_controls"]
    if case == "dashboard":
        native["native_account"]["dashboard"] = "express"
    elif case == "epoch":
        controls.pop("access_epoch")
    elif case == "bank":
        native["bank_approval"]["bank_id"] = "ba_other"
    elif case == "member":
        native["bank_approval"]["member_id"] = str(uuid5(NAMESPACE_URL, "other-member"))
    elif case == "request":
        native["request_ids"].pop("platform_balance")
    elif case == "fence":
        native["grant_fence"]["active_or_unknown"] = True
    elif case == "factory":
        native["policy_hashes"]["maintenance"] = "0" * 64
    elif case == "legacy_probes":
        controls["host_probes"] = []
    assert any("native" in error for error in legacy.check(data))
