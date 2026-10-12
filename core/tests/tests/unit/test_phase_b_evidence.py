"""Incomplete or mixed-revision declarations must never pass the exit gate."""

import importlib.util
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT / "scripts"))
spec = importlib.util.spec_from_file_location(
    "check_phase_b_evidence", ROOT / "scripts/check_phase_b_evidence.py"
)
checker = importlib.util.module_from_spec(spec)
spec.loader.exec_module(checker)
REVISION = "a" * 40


def record(name, hour=1, **fields):
    return {
        "id": name,
        "revision": REVISION,
        "url": f"https://example.org/evidence/{name}",
        "recorded_at": f"2026-10-12T{hour:02}:00:00+00:00",
        "passed": True,
    } | fields


@pytest.fixture
def bundle():
    return {
        "schema_version": 1,
        "environment": "test",
        "revision": REVISION,
        "gateway_runs": [record(f"gateway-{i}", i) for i in (2, 3, 4)],
        "stripe_runs": [
            record(
                f"stripe-{i}",
                i,
                mode="test",
                stages=dict.fromkeys(checker.STAGES, True),
            )
            for i in (5, 6, 7)
        ],
        "refund_recovery": record("recovery", 8, short_balance=True, recovered=True),
        "failed_deposit": record("failed", no_lease_started=True),
        "repeated_mismatch": record("mismatch", no_lease_started=True),
        "receipt": record(
            "receipt",
            tax_version="tax-1",
            ruleset_version="rules-1",
            throughput_map_version="map-1",
        ),
        "enrollment": record(
            "enrollment",
            host="AGX64-1",
            owner_approved=True,
            connect_onboarding=True,
            payout=True,
            enrolled_at="2026-10-12T01:00:00+00:00",
        ),
        "test_credit_removed": record("retired", 9, path_disabled=True),
        "boundary": record("boundary"),
        "closed_issues": sorted(checker.CLOSE_ISSUES),
    }


def check(bundle, **changes):
    return checker.check_evidence(
        bundle, source_revision=REVISION, exceptions=[], **changes
    )


def test_complete_declarations_pass_but_existing_exceptions_do_not(bundle):
    assert check(bundle) == []
    assert any(
        "#299" in error
        for error in checker.check_evidence(
            bundle, source_revision=REVISION, exceptions=["INV-AUTH-HOST-BINDING"]
        )
    )
    assert checker.check_evidence(bundle, source_revision="b" * 40, exceptions=[])


@pytest.mark.parametrize(
    "key",
    [
        "refund_recovery",
        "failed_deposit",
        "repeated_mismatch",
        "receipt",
        "enrollment",
        "test_credit_removed",
        "boundary",
    ],
)
def test_required_evidence_cannot_be_omitted(bundle, key):
    del bundle[key]
    assert check(bundle)


@pytest.mark.parametrize(
    "change",
    [
        {"revision": "b" * 40},
        {"passed": False},
        {"passed": "true"},
        {"url": "http://example.org"},
        {"recorded_at": "2026-10-12T01:00:00"},
    ],
)
def test_each_run_requires_same_revision_and_authentic_record_fields(bundle, change):
    bundle["gateway_runs"][1].update(change)
    assert check(bundle)


def test_consecutive_distinct_smokes_not_total_success_count(bundle):
    bundle["gateway_runs"].insert(2, record("failed-run", 3, passed=False))
    assert check(bundle)
    bundle["gateway_runs"] = [record("older-failure", 1, passed=False)] + [
        record(f"ok-{i}", i) for i in (2, 3, 4)
    ]
    assert check(bundle) == []
    bundle["gateway_runs"][2]["id"] = bundle["gateway_runs"][1]["id"]
    assert check(bundle)


@pytest.mark.parametrize("stage", checker.STAGES)
def test_three_payments_are_not_three_complete_journeys(bundle, stage):
    bundle["stripe_runs"][1]["stages"][stage] = False
    assert check(bundle)


def test_live_stripe_and_retirement_before_runs_rejected(bundle):
    bundle["stripe_runs"][0]["mode"] = "live"
    assert check(bundle)
    bundle["stripe_runs"][0]["mode"] = "test"
    bundle["test_credit_removed"]["recorded_at"] = "2026-10-12T06:00:00+00:00"
    assert check(bundle)


def test_stripe_requires_three_full_journeys_not_a_consecutive_streak(bundle):
    bundle["stripe_runs"].insert(
        1,
        record("stripe-failed", 5, mode="test", passed=False, stages={}),
    )
    bundle["stripe_runs"][0]["recorded_at"] = "2026-10-12T04:30:00+00:00"
    assert check(bundle) == []


def test_incomplete_bundle_and_boolean_schema_version_fail_closed(bundle):
    assert check({})
    bundle["schema_version"] = True
    assert check(bundle)


def test_recovery_is_additional_and_not_a_reused_payment_run(bundle):
    bundle["refund_recovery"]["id"] = bundle["stripe_runs"][0]["id"]
    assert check(bundle)


def test_no_lease_and_version_evidence_cannot_be_inferred(bundle):
    bundle["failed_deposit"]["no_lease_started"] = False
    bundle["receipt"]["throughput_map_version"] = ""
    bundle["closed_issues"].remove(381)
    errors = check(bundle)
    assert any("no lease" in error for error in errors)
    assert any("throughput_map_version" in error for error in errors)
    assert any("#381" in error for error in errors)
