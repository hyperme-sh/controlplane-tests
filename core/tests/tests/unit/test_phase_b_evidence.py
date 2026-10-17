"""Incomplete or mixed-revision declarations must never pass the exit gate."""

import importlib.util
import json
import os
import subprocess
import sys
from copy import deepcopy
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import NAMESPACE_URL, uuid5

import pytest

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT / "scripts"))
spec = importlib.util.spec_from_file_location(
    "check_phase_b_evidence", ROOT / "scripts/check_phase_b_evidence.py"
)
checker = importlib.util.module_from_spec(spec)
spec.loader.exec_module(checker)
REVISION = "a" * 40
COMMIT_TIME = datetime(2026, 10, 12, tzinfo=UTC)


def record(name, hour=1, **fields):
    result = {
        "id": name,
        "revision": REVISION,
        "url": f"https://example.org/evidence/{name}",
        "recorded_at": f"2026-10-12T{hour:02}:00:00+00:00",
        "passed": True,
    } | fields
    if name.startswith(("gateway-", "stripe-", "ok-")):
        at = datetime.fromisoformat(result["recorded_at"])
        token = uuid5(NAMESPACE_URL, name).hex
        ready_at, transfer_at, created_at, paid_at = [
            (at - timedelta(seconds=seconds)).isoformat()
            for seconds in (120, 60, 30, 10)
        ]
        result.update(
            started_at=(at - timedelta(minutes=5)).isoformat(),
            settlement=record(
                "settled-" + name,
                hour,
                recorded_at=(at - timedelta(seconds=150)).isoformat(),
                host_payable_cents=100,
                currency="usd",
                tenant_id="11111111-1111-1111-1111-111111111111",
                host_org_id="22222222-2222-2222-2222-222222222222",
                lease_id=str(uuid5(NAMESPACE_URL, "lease" + name)),
                billing_record_id=str(uuid5(NAMESPACE_URL, "billing" + name)),
            ),
            bank_followup=record(
                "followup-" + name,
                hour,
                recorded_at=(at + timedelta(days=8)).isoformat(),
                events_from_at=created_at,
                events_through_at=(at + timedelta(days=8)).isoformat(),
                events_complete=True,
                later_failure_seen=False,
                additional_bank_closures_accounted_for=True,
                status="paid",
                payout_id="po_" + token,
                account_id="acct_host",
                bank_id="ba_host",
            ),
            payout_method="fiat_bank",
            source_charge_type="stablecoin",
            tenant_id="11111111-1111-1111-1111-111111111111",
            host_org_id="22222222-2222-2222-2222-222222222222",
            lease_id=str(uuid5(NAMESPACE_URL, "lease" + name)),
            billing_record_id=str(uuid5(NAMESPACE_URL, "billing" + name)),
            readiness=record(
                "ready-" + name,
                hour,
                recorded_at=ready_at,
                stripe_transfers="active",
                payouts_enabled=True,
                payout_requirements_clear=True,
                destination_requirements_clear=True,
                ownership_verified=True,
                default_for_currency=True,
                currency="usd",
                bank_status="new",
                account_id="acct_host",
                bank_id="ba_host",
            ),
            connect_transfer={
                "id": "tr_" + token,
                "created_at": transfer_at,
                "amount_cents": 100,
                "currency": "usd",
                "livemode": False,
                "account_id": "acct_host",
                "destination": "acct_host",
                "destination_payment": "py_destination" + token,
            },
            bank_payout=record(
                "paid-" + name,
                hour,
                recorded_at=paid_at,
                payout_id="po_" + token,
                created_at=created_at,
                arrival_at=at.isoformat(),
                balance_transaction="txn_debit" + token,
                platform_fee_cents=0,
                host_deduction_cents=0,
                type="bank_account",
                status="paid",
                livemode=False,
                currency="usd",
                amount_cents=100,
                account_id="acct_host",
                bank_id="ba_host",
                transfer_id="tr_" + token,
            ),
        )
    if name.startswith(("gateway-", "stripe-", "ok-")):
        result["connect_transfer"]["metadata"] = {
            k: result[k]
            for k in ("tenant_id", "host_org_id", "lease_id", "billing_record_id")
        }
        opening_at = (at - timedelta(seconds=90)).isoformat()
        closing_at = (at - timedelta(seconds=20)).isoformat()
        result["bank_funding"] = record(
            "funding-" + name,
            hour,
            recorded_at=(at - timedelta(seconds=15)).isoformat(),
            account_id="acct_host",
            currency="usd",
            livemode=False,
            transactions_complete=True,
            has_more=False,
            window_start_at=opening_at,
            window_end_at=closing_at,
            opening_balance=record(
                "opening-" + name,
                hour,
                recorded_at=opening_at,
                account_id="acct_host",
                currency="usd",
                livemode=False,
                available_cents=0,
                pending_cents=0,
                reserved_cents=0,
            ),
            closing_balance=record(
                "closing-" + name,
                hour,
                recorded_at=closing_at,
                account_id="acct_host",
                currency="usd",
                livemode=False,
                available_cents=0,
                pending_cents=0,
                reserved_cents=0,
            ),
            transactions=[
                {
                    "id": "txn_credit" + token,
                    "source": "py_destination" + token,
                    "type": "payment",
                    "currency": "usd",
                    "amount": 100,
                    "fee": 0,
                    "net": 100,
                    "status": "available",
                    "created_at": transfer_at,
                    "available_at": transfer_at,
                },
                {
                    "id": "txn_debit" + token,
                    "source": "po_" + token,
                    "type": "payout",
                    "currency": "usd",
                    "amount": -100,
                    "fee": 0,
                    "net": -100,
                    "status": "available",
                    "created_at": created_at,
                },
            ],
        )
    if name.startswith("stripe-"):
        scope = {
            key: result[key]
            for key in ("tenant_id", "host_org_id", "lease_id", "billing_record_id")
        }
        payment_id, charge_id, refund_id = "pi_" + token, "py_" + token, "pyr_" + token
        result["payment"] = record(
            "capture-" + name,
            hour,
            recorded_at=(at - timedelta(seconds=180)).isoformat(),
            **scope,
            payment_intent_id=payment_id,
            charge_id=charge_id,
            amount_cents=400,
            captured=True,
            livemode=False,
            currency="usd",
        )
        result["payment"].update(
            intent={
                "id": payment_id,
                "status": "succeeded",
                "livemode": False,
                "currency": "usd",
                "amount_received": 400,
                "latest_charge": charge_id,
                "metadata": {
                    "payment_rail": "stripe_stablecoin",
                    "tenant_id": scope["tenant_id"],
                    "billing_record_id": scope["billing_record_id"],
                },
            },
            charge={
                "id": charge_id,
                "payment_intent": payment_id,
                "status": "succeeded",
                "paid": True,
                "captured": True,
                "livemode": False,
                "currency": "usd",
                "amount": 400,
                "payment_method_details": {
                    "type": "crypto",
                    "crypto": {"network": "base", "token_currency": "usdc"},
                },
            },
        )
        result["payment"].update(
            checkout_created_at=(at - timedelta(seconds=240)).isoformat(),
            dispatch_checkpoint=record(
                "dispatch-" + name,
                hour,
                recorded_at=(at - timedelta(seconds=250)).isoformat(),
                **scope,
                connect_transfer_enabled=True,
            ),
            capture_event={
                "id": "evt_" + token,
                "type": "payment_intent.succeeded",
                "livemode": False,
                "created_at": (at - timedelta(seconds=190)).isoformat(),
                "data": {"object": deepcopy(result["payment"]["intent"])},
            },
        )
        result["connect_transfer"]["metadata"] = scope.copy()
        result["connect_transfer"]["source_transaction"] = charge_id
        result["settlement"].update(
            captured_cents=400,
            retained_cents=300,
            refund_due_cents=100,
            refund_idempotency_key="hm-refund-" + token,
            post_refund_host_payable_cents=80,
            reversal_due_cents=20,
            payment_intent_id=payment_id,
            charge_id=charge_id,
        )
        result["refund"] = record(
            "refund-" + name,
            hour,
            recorded_at=(at - timedelta(seconds=5)).isoformat(),
            **scope,
            refund_id=refund_id,
            created_at=(at - timedelta(seconds=8)).isoformat(),
            metadata={
                "billing_record_id": scope["billing_record_id"],
                "idempotency_key": "hm-refund-" + token,
            },
            payment_intent_id=payment_id,
            charge_id=charge_id,
            amount_cents=100,
            status="succeeded",
            currency="usd",
            livemode=False,
            transfer_outcome=record(
                "refund-outcome-" + name,
                hour,
                recorded_at=(at - timedelta(seconds=4)).isoformat(),
                **scope,
                kind="reversed",
                refund_id=refund_id,
                transfer_id="tr_" + token,
                reversal_due_cents=20,
                reversed_cents=20,
                platform_loss_cents=0,
                reversal_id="trr_" + token,
                balance_transaction="txn_" + token,
                currency="usd",
                livemode=False,
            ),
        )
        result["refund"]["inventory"] = record(
            "inventory-" + name,
            hour,
            recorded_at=(at - timedelta(seconds=3)).isoformat(),
            payment_intent_id=payment_id,
            complete=True,
            has_more=False,
            data=[
                {
                    "id": refund_id,
                    "payment_intent": payment_id,
                    "charge": charge_id,
                    "amount": 100,
                    "status": "succeeded",
                    "currency": "usd",
                    "livemode": False,
                    "metadata": deepcopy(result["refund"]["metadata"]),
                }
            ],
        )
        result["refund"]["transfer_outcome"]["provider_reversal"] = {
            "id": "trr_" + token,
            "transfer": "tr_" + token,
            "amount": 20,
            "currency": "usd",
            "balance_transaction": "txn_" + token,
            "metadata": scope | {"reversal_target_cents": "20"},
        }
        outcome = result["refund"]["transfer_outcome"]
        outcome["provider_reversal"]["created_at"] = (
            at - timedelta(seconds=6)
        ).isoformat()
        outcome["reversal_inventory"] = record(
            "reversal-inventory-" + name,
            hour,
            recorded_at=(at - timedelta(seconds=2)).isoformat(),
            transfer_id=result["connect_transfer"]["id"],
            complete=True,
            has_more=False,
            data=[deepcopy(outcome["provider_reversal"])],
        )
    return result


def refusal_record(name, hour, *, mismatch=False):
    at = datetime(2026, 10, 12, hour, tzinfo=UTC)
    scope = {
        "tenant_id": "11111111-1111-1111-1111-111111111111",
        "host_org_id": "22222222-2222-2222-2222-222222222222",
        "lease_id": str(uuid5(NAMESPACE_URL, "lease" + name)),
        "billing_record_id": str(uuid5(NAMESPACE_URL, "billing" + name)),
    }
    token = uuid5(NAMESPACE_URL, name).hex
    intent = {
        "id": "pi_" + token,
        "status": "succeeded" if mismatch else "requires_payment_method",
        "livemode": False,
        "currency": "usd",
        "amount": 400,
        "amount_received": 400 if mismatch else 0,
        "latest_charge": "py_" + token if mismatch else None,
        "last_payment_error": {"code": "payment_failed"},
        "metadata": {
            "tenant_id": scope["tenant_id"],
            "billing_record_id": scope["billing_record_id"],
            "payment_rail": "stripe_stablecoin",
        },
    }
    row = record(
        name,
        hour,
        **scope,
        started_at=(at - timedelta(minutes=3)).isoformat(),
        attempt_id="attempt-" + token,
        payment_intent_id=intent["id"],
        amount_cents=400,
        no_lease_started=True,
        actor_sub="sandbox-actor",
        intent=intent,
        provider_event={
            "id": "evt_" + token,
            "type": "payment_intent.succeeded"
            if mismatch
            else "payment_intent.payment_failed",
            "livemode": False,
            "created_at": (at - timedelta(seconds=90)).isoformat(),
            "data": {"object": deepcopy(intent)},
        },
        no_start_audit=record(
            "audit-" + name,
            hour,
            recorded_at=(at - timedelta(seconds=10)).isoformat(),
            **scope,
            payment_intent_id=intent["id"],
            attempt_id="attempt-" + token,
            complete=True,
            lease_started=False,
            activation_count=0,
            success_event_count=0,
            window_start_at=(at - timedelta(seconds=120)).isoformat(),
            window_end_at=(at - timedelta(seconds=20)).isoformat(),
        ),
    )
    if mismatch:
        row.update(
            decision="quarantined",
            charge={
                "id": "py_" + token,
                "payment_intent": intent["id"],
                "status": "succeeded",
                "paid": True,
                "captured": True,
                "amount": 400,
                "currency": "usd",
                "livemode": False,
                "payment_method_details": {
                    "type": "crypto",
                    "crypto": {
                        "network": "base",
                        "token_currency": "usdc",
                        "buyer_address": "0x" + "2" * 40,
                    },
                },
            },
            expected_payer=record(
                "wallet-" + name,
                hour,
                recorded_at=(at - timedelta(minutes=4)).isoformat(),
                tenant_id=scope["tenant_id"],
                actor_sub="sandbox-actor",
                source="auth_siwe_me",
                address="0x" + "1" * 40,
            ),
        )
    return row


@pytest.fixture
def bundle():
    result = {
        "schema_version": 2,
        "payout_method": "fiat_bank",
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
            bank_payout_id="po_" + uuid5(NAMESPACE_URL, "stripe-5").hex,
            tenant_id="11111111-1111-1111-1111-111111111111",
            host_org_id="22222222-2222-2222-2222-222222222222",
            enrolled_at="2026-10-12T01:00:00+00:00",
        ),
        "test_credit_removed": record(
            "retired", 9, recorded_at="2026-10-21T09:00:00+00:00", path_disabled=True
        ),
        "failed_bank_payout": record(
            "failed-bank",
            8,
            status="failed",
            type="bank_account",
            started_at="2026-10-12T07:00:00+00:00",
            livemode=False,
            currency="usd",
            payout_id="po_failed",
            transfer_id="tr_failed",
            account_id="acct_host",
            bank_id="ba_failed",
            amount_cents=100,
            transfer_amount_cents=100,
            failure_code="no_account",
            bank_dispatch=record(
                "bank-dispatch",
                7,
                payout_id="po_failed",
                account_id="acct_host",
                bank_id="ba_failed",
                status="in_transit",
                event_id="evt_dispatched",
                livemode=False,
            ),
            failure_balance_transaction="txn_returned",
            operator_visible=True,
            tenant_id="11111111-1111-1111-1111-111111111111",
            host_org_id="22222222-2222-2222-2222-222222222222",
            lease_id="33333333-3333-3333-3333-333333333333",
            billing_record_id="44444444-4444-4444-4444-444444444444",
            preservation=record(
                "preserved-bank",
                8,
                settled_host_payable_cents=100,
                recoverable_after_cents=100,
                bank_paid_cents=0,
                lost_cents=0,
                duplicate_debit_cents=0,
                returned_cents=100,
                currency="usd",
                recovery_status="available",
                payout_id="po_failed",
                transfer_id="tr_failed",
                tenant_id="11111111-1111-1111-1111-111111111111",
                host_org_id="22222222-2222-2222-2222-222222222222",
                lease_id="33333333-3333-3333-3333-333333333333",
                billing_record_id="44444444-4444-4444-4444-444444444444",
            ),
        ),
        "boundary": record("boundary"),
        "closed_issues": sorted(checker.CLOSE_ISSUES),
        "closed_infra_issues": [222],
        "fiat_rollout": record(
            "rollout",
            0,
            recorded_at="2026-10-12T00:30:00+00:00",
            rendered_payout_method="fiat_bank",
            prerequisites=[
                record(
                    f"prereq-{repo}-{issue}",
                    0,
                    recorded_at="2026-10-12T00:20:00+00:00",
                    repo=repo,
                    issue=issue,
                    merged=True,
                    deployed=True,
                    merged_revision="b" * 40,
                    deployed_at="2026-10-12T00:15:00+00:00",
                )
                for repo, issue in sorted(checker.FIAT_PREREQUISITES)
            ],
            enrollments_before=[
                {
                    "tenant_id": "11111111-1111-1111-1111-111111111111",
                    "host_org_id": "22222222-2222-2222-2222-222222222222",
                    "attempt_id": "original",
                    "stripe_account_id": "acct_host",
                }
            ],
            enrollments_after=[
                {
                    "tenant_id": "11111111-1111-1111-1111-111111111111",
                    "host_org_id": "22222222-2222-2222-2222-222222222222",
                    "attempt_id": "original",
                    "stripe_account_id": "acct_host",
                }
            ],
        ),
    }
    recovery = record("stripe-recovery", 8, short_balance=True, recovered=True)
    for key in ("readiness", "bank_payout", "bank_followup", "bank_funding"):
        recovery.pop(key, None)
    scope = {
        key: recovery[key]
        for key in ("tenant_id", "host_org_id", "lease_id", "billing_record_id")
    }
    refund = recovery["refund"]
    transfer = recovery["connect_transfer"]
    outcome = refund["transfer_outcome"]
    outcome.pop("provider_reversal")
    outcome.pop("provider_reversal", None)
    outcome.pop("reversal_inventory", None)
    outcome.update(
        kind="platform_loss",
        reversal_id=None,
        balance_transaction=None,
        reversed_cents=0,
        platform_loss_cents=20,
        reversal_refused=True,
        reason="insufficient_connect_balance",
        operator_visible=True,
        financial_state="recorded",
        refunded_from_platform_cents=100,
        loss_record="billing-loss-checkpoint",
    )
    recovery["reversal_refusal"] = record(
        "short-refusal",
        8,
        recorded_at="2026-10-12T07:59:53+00:00",
        **scope,
        transfer_id=transfer["id"],
        refund_id=refund["refund_id"],
        account_id=transfer["account_id"],
        code="balance_insufficient",
        request_id="req_shortbalance",
        currency="usd",
        livemode=False,
        available_cents=0,
        amount_cents=20,
    )
    transaction = {
        "id": "txn_platformrefund",
        "source": refund["refund_id"],
        "amount": -100,
        "net": -100,
        "fee": 0,
        "currency": "usd",
        "type": "refund",
    }
    refund["inventory"]["data"][0]["balance_transaction"] = transaction["id"]
    recovery["final_accounting"] = record(
        "recovery-final",
        8,
        recorded_at="2026-10-12T07:59:59+00:00",
        **scope,
        refund_id=refund["refund_id"],
        transfer_id=transfer["id"],
        owner_checkpoint="billing-loss-checkpoint",
        status="platform_loss_recorded",
        renter_refunded_cents=100,
        platform_loss_cents=20,
        host_debited_cents=0,
        currency="usd",
        livemode=False,
        refund_balance_transaction=transaction,
    )
    result["refund_recovery"] = recovery
    result["failed_deposit"] = refusal_record("failed-deposit", 2)
    result["repeated_mismatch"] = record(
        "mismatch",
        8,
        no_lease_started=True,
        tenant_id=scope["tenant_id"],
        host_org_id=scope["host_org_id"],
        actor_sub="sandbox-actor",
        mismatch_count=3,
        operator_required=True,
        reason="repeated_mismatch",
        attempts=[
            refusal_record(f"mismatch-{i}", i + 2, mismatch=True) for i in range(3)
        ],
    )
    return result


def check(bundle, **changes):
    changes.setdefault("commit_time", COMMIT_TIME)
    changes.setdefault("now", datetime(2026, 10, 26, 12, tzinfo=UTC))
    return checker.check_evidence(
        bundle, source_revision=REVISION, exceptions=[], **changes
    )


def test_complete_declarations_pass_but_existing_exceptions_do_not(bundle):
    assert check(bundle) == []
    assert any(
        "#299" in error
        for error in checker.check_evidence(
            bundle,
            source_revision=REVISION,
            commit_time=COMMIT_TIME,
            exceptions=["INV-AUTH-HOST-BINDING"],
        )
    )
    assert checker.check_evidence(
        bundle, source_revision="b" * 40, commit_time=COMMIT_TIME, exceptions=[]
    )


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
    bundle["enrollment"]["enrolled_at"] = "2026-10-12T00:00:00+00:00"
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
    # Keep the first successful payment's financial evidence intact and place
    # the failed attempt between complete successful journeys.
    bundle["stripe_runs"][1]["recorded_at"] = "2026-10-12T05:30:00+00:00"
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


@pytest.mark.parametrize(
    "url",
    [
        "https://:secret@example.org/evidence",
        "https://user@example.org/evidence",
        "https://user:secret@example.org/evidence",
        "https://@example.org/evidence",
    ],
)
def test_evidence_url_rejects_all_userinfo(bundle, url):
    bundle["receipt"]["url"] = url
    assert any("without credentials" in error for error in check(bundle))


@pytest.mark.parametrize("port", ["not-a-port", "65536", "-1"])
def test_evidence_url_rejects_invalid_ports(bundle, port):
    bundle["receipt"]["url"] = f"https://example.org:{port}/evidence"
    assert any("HTTPS evidence link" in error for error in check(bundle))


@pytest.mark.parametrize("port", ["", ":443", ":8443"])
def test_evidence_url_accepts_valid_ports(bundle, port):
    bundle["receipt"]["url"] = f"https://example.org{port}/evidence"
    assert check(bundle) == []


@pytest.mark.parametrize("identity", [" gateway-2", "gateway-2 ", " gateway-2 "])
def test_evidence_ids_are_unique_after_trimming_across_groups(bundle, identity):
    bundle["stripe_runs"][0]["id"] = identity
    assert any("distinct nonempty" in error for error in check(bundle))


@pytest.mark.parametrize("hour", [0, 1, 2])
def test_enrollment_record_cannot_precede_enrollment(bundle, hour):
    bundle["enrollment"]["recorded_at"] = f"2026-10-12T{hour:02}:00:00+00:00"
    errors = check(bundle)
    if hour == 0:
        assert any("recorded_at must not precede enrolled_at" in e for e in errors)
    else:
        assert errors == []


def test_enrollment_must_strictly_precede_first_smoke(bundle):
    bundle["enrollment"]["enrolled_at"] = bundle["gateway_runs"][0]["recorded_at"]
    assert any("precede" in error for error in check(bundle))


def test_coherent_future_bundle_is_rejected(bundle):
    for value in bundle.values():
        records = value if isinstance(value, list) else [value]
        for row in records:
            if isinstance(row, dict):
                for key in ("recorded_at", "enrolled_at"):
                    if key in row:
                        row[key] = (datetime(2026, 10, 27, 12, tzinfo=UTC)).isoformat()
    assert any("in the future" in error for error in check(bundle))


def test_future_clock_skew_is_bounded(bundle):
    now = datetime(2026, 10, 26, 12, tzinfo=UTC)
    bundle["receipt"]["recorded_at"] = (now + checker.CLOCK_SKEW).isoformat()
    assert check(bundle, now=now) == []
    bundle["receipt"]["recorded_at"] = (
        now + checker.CLOCK_SKEW + timedelta(seconds=1)
    ).isoformat()
    assert any("in the future" in error for error in check(bundle, now=now))


@pytest.fixture
def source_repo(tmp_path):
    root = tmp_path / "checkout"
    root.mkdir()

    def git(*args):
        return subprocess.check_output(
            ["git", *args],
            cwd=root,
            text=True,
            env=os.environ
            | {
                "GIT_AUTHOR_DATE": COMMIT_TIME.isoformat(),
                "GIT_COMMITTER_DATE": COMMIT_TIME.isoformat(),
            },
        ).strip()

    git("init", "-q")
    git("config", "user.email", "test@example.org")
    git("config", "user.name", "Evidence test")
    (root / "scripts").mkdir()
    (root / "scripts/ownership.json").write_text(
        json.dumps(
            {
                "tables": {},
                "non_orm_tables": {},
                "model_sources": {},
                "owners": {},
                "exceptions": [
                    {
                        "name": "INV-AUTH-HOST-BINDING",
                        "issue": "https://github.com/FyberLabs/panopticon/issues/299",
                    }
                ],
            }
        )
    )
    git("add", ".")
    git("commit", "-qm", "initial")
    return root, git


@pytest.mark.parametrize(
    "host", ["exa mple.org", "%zz", "-bad.org", "bad_.org", "a..org"]
)
def test_evidence_url_rejects_invalid_hosts(bundle, host):
    bundle["receipt"]["url"] = f"https://{host}/evidence"
    assert any("HTTPS evidence link" in e for e in check(bundle))


@pytest.mark.parametrize("host", ["example.org", "127.0.0.1", "[::1]", "münchen.de"])
def test_evidence_url_accepts_dns_idna_and_ip_hosts(bundle, host):
    bundle["receipt"]["url"] = f"https://{host}/evidence"
    assert check(bundle) == []


@pytest.mark.parametrize("key", ["failed_deposit", "repeated_mismatch"])
@pytest.mark.parametrize("hour", [9, 10])
def test_credit_retirement_follows_refusal_exercises(bundle, key, hour):
    bundle[key]["recorded_at"] = f"2026-10-21T{hour:02}:00:00+00:00"
    assert any("removed after" in e for e in check(bundle))


@pytest.mark.parametrize(
    "key", ["receipt", "refund_recovery", "enrollment", "gateway_runs", "stripe_runs"]
)
def test_records_cannot_predate_source_commit(bundle, key):
    value = bundle[key]
    row = value[0] if isinstance(value, list) else value
    row["recorded_at"] = "2026-10-11T23:59:59+00:00"
    assert any("predates the source commit" in e for e in check(bundle))


def test_enrollment_cannot_predate_source_commit(bundle):
    bundle["enrollment"]["enrolled_at"] = "2026-10-11T23:59:59+00:00"
    assert any("enrolled_at predates" in e for e in check(bundle))


def test_source_commit_time_uses_actual_git_commit(source_repo):
    root, git = source_repo
    assert checker.source_commit_time(root, git("rev-parse", "HEAD")) == COMMIT_TIME


@pytest.mark.parametrize(
    "pair", ['"passed":false,"passed":true', '"passed":true,"passed":true']
)
def test_cli_rejects_duplicate_properties_before_source_checks(
    tmp_path, monkeypatch, capsys, pair
):
    evidence = tmp_path / "duplicate.json"
    evidence.write_text('{"receipt":{' + pair + "}}")
    monkeypatch.setattr(sys, "argv", ["checker", str(evidence)])
    with pytest.raises(SystemExit) as error:
        checker.main()
    assert error.value.code == 2
    assert "duplicate JSON property" in capsys.readouterr().err


@pytest.mark.parametrize("kind", ["modified", "staged", "untracked"])
def test_dirty_checkout_cannot_be_identified_as_clean_head(source_repo, kind):
    root, git = source_repo
    assert checker.source_revision(root) == git("rev-parse", "HEAD")
    if kind == "untracked":
        (root / "planted.py").write_text("pass\n")
    else:
        (root / "scripts/ownership.json").write_text("{}")
        if kind == "staged":
            git("add", ".")
    with pytest.raises(ValueError, match="dirty"):
        checker.source_revision(root)


@pytest.mark.parametrize("flag", [None, "--assume-unchanged", "--skip-worktree"])
def test_cli_rejects_uncommitted_exception_url_bypass(
    source_repo, tmp_path, bundle, monkeypatch, capsys, flag
):
    root, git = source_repo
    evidence = tmp_path / "bundle.json"
    evidence.write_text(json.dumps(bundle).replace(REVISION, git("rev-parse", "HEAD")))
    monkeypatch.setattr(checker, "ROOT", root)
    monkeypatch.setattr(sys, "argv", ["check_phase_b_evidence.py", str(evidence)])
    assert checker.main() == 1
    assert "#299" in capsys.readouterr().out
    path = root / "scripts/ownership.json"
    if flag:
        git("update-index", flag, "scripts/ownership.json")
    path.write_text(path.read_text().replace("/299", "/999"))
    if flag:
        assert git("status", "--porcelain", "--untracked-files=all") == ""
    with pytest.raises(SystemExit) as error:
        checker.main()
    assert error.value.code == 2
    assert ("source index" if flag else "dirty") in capsys.readouterr().err


@pytest.mark.parametrize("flag", ["--assume-unchanged", "--skip-worktree"])
def test_flagged_clean_checkout_is_rejected_until_flags_are_removed(source_repo, flag):
    root, git = source_repo
    git("update-index", flag, "scripts/ownership.json")
    assert git("status", "--porcelain") == ""
    with pytest.raises(ValueError, match="source index"):
        checker.source_revision(root)
    git("update-index", flag.replace("--", "--no-", 1), "scripts/ownership.json")
    assert checker.source_revision(root) == git("rev-parse", "HEAD")


@pytest.mark.parametrize("version", [1, True, "2"])
def test_legacy_schema_cannot_accept_fiat_exit(bundle, version):
    bundle["schema_version"] = version
    assert check(bundle)


@pytest.mark.parametrize("group", ["gateway_runs", "stripe_runs"])
@pytest.mark.parametrize(
    "key", ["payout_method", "readiness", "connect_transfer", "bank_payout", "lease_id"]
)
def test_fiat_run_requires_explicit_bank_journey(bundle, group, key):
    del bundle[group][0][key]
    assert check(bundle)


@pytest.mark.parametrize("status", ["pending", "in_transit", "failed", "canceled"])
def test_nonterminal_or_failed_bank_payout_cannot_pass(bundle, status):
    bundle["gateway_runs"][0]["bank_payout"]["status"] = status
    assert check(bundle)


@pytest.mark.parametrize(
    "key,value",
    [
        ("payouts_enabled", False),
        ("stripe_transfers", "inactive"),
        ("bank_status", "errored"),
        ("bank_status", "verification_failed"),
        ("bank_status", "tokenized_account_number_deactivated"),
        ("bank_status", "verified"),
        ("bank_status", "unknown"),
        ("default_for_currency", False),
        ("currency", "eur"),
        ("ownership_verified", False),
        ("destination_requirements_clear", False),
        ("payout_requirements_clear", False),
    ],
)
def test_unready_bank_refuses_even_when_transfer_capability_active(bundle, key, value):
    bundle["gateway_runs"][0]["readiness"][key] = value
    assert check(bundle)


@pytest.mark.parametrize("key", ["account_id", "bank_id", "transfer_id"])
def test_terminal_payout_must_match_transfer_and_destination(bundle, key):
    bundle["stripe_runs"][0]["bank_payout"][key] = "different"
    assert check(bundle)


def test_readiness_must_precede_transfer_and_be_fresh(bundle):
    ready = bundle["gateway_runs"][0]["readiness"]
    ready["recorded_at"] = "2026-10-12T01:50:00+00:00"
    assert check(bundle)
    ready["recorded_at"] = "2026-10-12T02:00:00+00:00"
    assert check(bundle)


@pytest.mark.parametrize("key", ["merged", "deployed"])
def test_prerequisite_cannot_be_unmerged_or_undeployed(bundle, key):
    bundle["fiat_rollout"]["prerequisites"][0][key] = False
    assert check(bundle)


def test_missing_prerequisite_and_infra_closure_fail(bundle):
    bundle["fiat_rollout"]["prerequisites"].pop()
    assert check(bundle)
    bundle["closed_infra_issues"] = []
    assert check(bundle)


@pytest.mark.parametrize("key", ["stripe_account_id", "attempt_id"])
def test_rollout_cannot_replace_existing_enrollment(bundle, key):
    bundle["fiat_rollout"]["enrollments_after"][0][key] = "replacement"
    assert check(bundle)


def test_non_stablecoin_source_requires_decoupling_prerequisite(bundle):
    bundle["stripe_runs"][0]["source_charge_type"] = "non_stablecoin"
    payment = bundle["stripe_runs"][0]["payment"]
    payment["intent"]["metadata"]["payment_rail"] = "stripe_checkout"
    payment["charge"]["payment_method_details"] = {"type": "card", "card": {}}
    payment["capture_event"]["data"]["object"] = deepcopy(payment["intent"])
    assert check(bundle)
    bundle["fiat_rollout"]["prerequisites"].append(
        record(
            "decoupling",
            0,
            recorded_at="2026-10-12T00:20:00+00:00",
            repo="panopticon",
            issue=491,
            merged=True,
            deployed=True,
            merged_revision="b" * 40,
            deployed_at="2026-10-12T00:15:00+00:00",
        )
    )
    assert check(bundle)
    bundle["closed_issues"].append(491)
    assert check(bundle) == []


def test_payout_cannot_be_reused_for_another_lease(bundle):
    bundle["gateway_runs"][1]["bank_payout"]["payout_id"] = bundle["gateway_runs"][0][
        "bank_payout"
    ]["payout_id"]
    assert check(bundle)


@pytest.mark.parametrize("field", ["lease_id", "billing_record_id"])
def test_each_successful_run_requires_fresh_financial_scope(bundle, field):
    bundle["gateway_runs"][1][field] = bundle["gateway_runs"][0][field]
    assert check(bundle)


@pytest.mark.parametrize("field", ["tenant_id", "host_org_id"])
def test_journeys_cannot_use_another_host_enrollment(bundle, field):
    bundle["stripe_runs"][0][field] = "99999999-9999-9999-9999-999999999999"
    assert check(bundle)


@pytest.mark.parametrize("key", ["readiness", "bank_payout", "connect_transfer"])
@pytest.mark.parametrize("malformed", [None, [], True])
def test_malformed_financial_evidence_returns_errors(bundle, key, malformed):
    bundle["gateway_runs"][0][key] = malformed
    assert check(bundle)


def test_malformed_enrollment_payout_reference_returns_errors(bundle):
    bundle["enrollment"]["bank_payout_id"] = []
    assert check(bundle)


def test_rollout_recipient_must_match_journey_owner_binding(bundle):
    bundle["fiat_rollout"]["enrollments_before"][0]["stripe_account_id"] = "acct_other"
    bundle["fiat_rollout"]["enrollments_after"][0]["stripe_account_id"] = "acct_other"
    assert check(bundle)


@pytest.mark.parametrize("status", ["failed", "canceled", "pending"])
def test_paid_event_followed_by_bad_followup_cannot_accept(bundle, status):
    bundle["gateway_runs"][0]["bank_followup"]["status"] = status
    assert check(bundle)


def test_paid_event_alone_is_not_bank_arrival_evidence(bundle):
    run = bundle["gateway_runs"][0]
    run["bank_followup"]["recorded_at"] = run["bank_payout"]["recorded_at"]
    assert check(bundle)


def test_later_failure_after_initial_paid_rejects(bundle):
    bundle["gateway_runs"][0]["bank_followup"]["later_failure_seen"] = True
    assert check(bundle)


@pytest.mark.parametrize("key", ["failed_bank_payout", "bank_followup", "settlement"])
def test_extended_financial_evidence_is_required(bundle, key):
    if key == "failed_bank_payout":
        del bundle[key]
    else:
        del bundle["gateway_runs"][0][key]
    assert check(bundle)


@pytest.mark.parametrize(
    "field,value",
    [
        ("recoverable_after_cents", 1),
        ("lost_cents", 1),
        ("duplicate_debit_cents", 1),
        ("bank_paid_cents", 100),
        ("settled_host_payable_cents", 1),
    ],
)
def test_failed_bank_must_preserve_all_earnings(bundle, field, value):
    bundle["failed_bank_payout"]["preservation"][field] = value
    assert check(bundle)


def test_one_cent_transfer_and_bank_payout_cannot_accept_large_payable(bundle):
    run = bundle["gateway_runs"][0]
    run["connect_transfer"]["amount_cents"] = run["bank_payout"]["amount_cents"] = 1
    assert check(bundle)


def test_platform_fees_cannot_reduce_the_host_bank_amount(bundle):
    payout = bundle["gateway_runs"][0]["bank_payout"]
    payout["amount_cents"] = 99
    payout["platform_fee_cents"] = 1
    payout["platform_fee_evidence"] = record(
        "fee",
        2,
        amount_cents=1,
        currency="usd",
        payer="platform",
        charged_to_host=False,
        payout_id=payout["payout_id"],
    )
    assert check(bundle)
    payout["amount_cents"] = 100
    assert check(bundle) == []
    payout["platform_fee_evidence"]["charged_to_host"] = True
    assert check(bundle)


def test_card_payout_is_not_a_bank_payout(bundle):
    bundle["gateway_runs"][0]["bank_payout"]["type"] = "card"
    assert check(bundle)


def test_harmless_snapshot_fields_do_not_change_bindings(bundle):
    bundle["fiat_rollout"]["enrollments_after"][0].update(
        payout_method="fiat_bank", capability="active"
    )
    assert check(bundle) == []


def test_stripe_execution_must_follow_fiat_rollout(bundle):
    # Record timestamps alone can look post-rollout while execution predates it.
    bundle["stripe_runs"][0]["started_at"] = "2026-10-12T00:25:00+00:00"
    assert check(bundle)


def test_bank_window_skips_weekends_and_federal_holidays():
    friday = datetime(2026, 12, 18, 12, tzinfo=UTC)
    assert checker.payout_observation_deadline(friday) == datetime(
        2026, 12, 28, 12, tzinfo=UTC
    )


@pytest.mark.parametrize("field", ["amount_cents", "transfer_amount_cents"])
def test_failed_bank_amounts_must_match_preserved_payable(bundle, field):
    bundle["failed_bank_payout"][field] = 1
    assert check(bundle)


def test_refund_credit_retirement_waits_for_bank_monitoring(bundle):
    bundle["test_credit_removed"]["recorded_at"] = "2026-10-19T01:00:00+00:00"
    assert check(bundle)


@pytest.mark.parametrize(
    "key,value",
    [
        ("events_complete", False),
        ("additional_bank_closures_accounted_for", False),
        ("events_from_at", "2026-10-12T02:00:00+00:00"),
        ("events_through_at", "2026-10-13T00:00:00+00:00"),
    ],
)
def test_incomplete_return_monitoring_rejects(bundle, key, value):
    bundle["gateway_runs"][0]["bank_followup"][key] = value
    assert check(bundle)


def test_conditional_prerequisite_closure_is_required_separately(bundle):
    bundle["gateway_runs"][0]["source_charge_type"] = "non_stablecoin"
    bundle["fiat_rollout"]["prerequisites"].append(
        record(
            "source-change",
            0,
            recorded_at="2026-10-12T00:20:00+00:00",
            repo="panopticon",
            issue=491,
            merged=True,
            deployed=True,
            merged_revision="b" * 40,
            deployed_at="2026-10-12T00:15:00+00:00",
        )
    )
    assert any("conditional #491" in error for error in check(bundle))
    bundle["closed_issues"].append(491)
    assert check(bundle) == []


@pytest.mark.parametrize("key", ["lease_id", "billing_record_id", "both"])
def test_failed_exercise_cannot_reuse_paid_financial_scope(bundle, key):
    paid = bundle["gateway_runs"][0]
    failed = bundle["failed_bank_payout"]
    for field in ("lease_id", "billing_record_id"):
        if key in (field, "both"):
            failed[field] = failed["preservation"][field] = paid[field]
    assert any("distinct unpaid lease" in error for error in check(bundle))


@pytest.mark.parametrize("status", ["canceled", "pending", "paid"])
def test_failing_bank_requires_actual_rejection(bundle, status):
    bundle["failed_bank_payout"]["status"] = status
    assert check(bundle)


def test_pre_dispatch_cancellation_cannot_count_as_bank_rejection(bundle):
    failure = bundle["failed_bank_payout"]
    failure["bank_dispatch"]["status"] = "pending"
    assert check(bundle)
    failure["bank_dispatch"]["status"] = "in_transit"
    failure["failure_code"] = "insufficient_funds"
    assert check(bundle)


@pytest.mark.parametrize("field", ["bank_dispatch", "failure_code"])
def test_bank_dispatch_and_rejection_proof_are_required(bundle, field):
    del bundle["failed_bank_payout"][field]
    assert check(bundle)


@pytest.mark.parametrize("value", [1, True])
def test_host_fee_deduction_cannot_pass_even_with_full_declared_bank_amount(
    bundle, value
):
    bundle["gateway_runs"][0]["bank_payout"]["host_deduction_cents"] = value
    assert check(bundle)


@pytest.mark.parametrize("key", ["payment", "refund"])
def test_refund_stage_boolean_is_not_financial_evidence(bundle, key):
    del bundle["stripe_runs"][0][key]
    assert check(bundle)


@pytest.mark.parametrize(
    "field,value",
    [
        ("refund_id", "unknown"),
        ("charge_id", "py_unrelated"),
        ("payment_intent_id", "pi_unrelated"),
        ("billing_record_id", "99999999-9999-9999-9999-999999999999"),
        ("amount_cents", 1),
        ("amount_cents", True),
        ("status", "pending"),
        ("status", "failed"),
        ("livemode", True),
    ],
)
def test_refund_must_match_original_payment_and_settled_amount(bundle, field, value):
    bundle["stripe_runs"][0]["refund"][field] = value
    assert check(bundle)


@pytest.mark.parametrize(
    "field,value",
    [
        ("captured_cents", 399),
        ("retained_cents", 299),
        ("refund_due_cents", 1),
        ("post_refund_host_payable_cents", 79),
        ("reversal_due_cents", 1),
    ],
)
def test_refund_settlement_equations_are_required(bundle, field, value):
    bundle["stripe_runs"][0]["settlement"][field] = value
    assert check(bundle)


@pytest.mark.parametrize(
    "field,value",
    [
        ("transfer_id", "tr_unrelated"),
        ("refund_id", "pyr_unrelated"),
        ("reversed_cents", 1),
        ("reversal_id", "unobserved"),
        ("platform_loss_cents", 1),
    ],
)
def test_matching_transfer_reversal_is_not_inferred(bundle, field, value):
    bundle["stripe_runs"][0]["refund"]["transfer_outcome"][field] = value
    assert check(bundle)


def test_platform_loss_after_short_balance_requires_completed_owner_record(bundle):
    row = bundle["stripe_runs"][0]
    outcome = row["refund"]["transfer_outcome"]
    outcome.pop("provider_reversal", None)
    outcome.pop("reversal_inventory", None)
    outcome.update(
        kind="platform_loss",
        reversal_id=None,
        balance_transaction=None,
        reversed_cents=0,
        platform_loss_cents=20,
        reversal_refused=True,
        reason="insufficient_connect_balance",
        operator_visible=True,
        financial_state="recorded",
        refunded_from_platform_cents=100,
        loss_record="billing-owner-loss-checkpoint",
    )
    assert check(bundle) == []
    outcome["operator_visible"] = False
    assert check(bundle)
    outcome["operator_visible"] = True
    outcome["financial_state"] = "planned"
    assert check(bundle)


def test_no_reversal_only_when_settlement_preserves_host_share(bundle):
    row = bundle["stripe_runs"][0]
    outcome = row["refund"]["transfer_outcome"]
    outcome.pop("provider_reversal", None)
    outcome.pop("reversal_inventory", None)
    outcome.update(
        kind="not_required",
        reversal_id=None,
        balance_transaction=None,
        reversed_cents=0,
        platform_loss_cents=0,
        reversal_due_cents=0,
    )
    assert check(bundle)
    row["settlement"].update(reversal_due_cents=0, post_refund_host_payable_cents=100)
    assert check(bundle) == []


def test_unrelated_payment_cannot_be_shared_across_lease_refunds(bundle):
    first, second = bundle["stripe_runs"][:2]
    for field in ("payment_intent_id", "charge_id"):
        for key in ("payment", "settlement", "refund"):
            second[key][field] = first["payment"][field]
    assert any("reused across financial scopes" in error for error in check(bundle))


@pytest.mark.parametrize("malformed", [None, [], True])
def test_malformed_refund_outcome_fails_closed(bundle, malformed):
    bundle["stripe_runs"][0]["refund"]["transfer_outcome"] = malformed
    assert check(bundle)


@pytest.mark.parametrize("charge_prefix,refund_prefix", [("ch", "re"), ("py", "pyr")])
def test_supported_original_charge_and_refund_ids_pass(
    bundle, charge_prefix, refund_prefix
):
    row = bundle["stripe_runs"][0]
    for key in ("payment", "settlement", "refund"):
        row[key]["charge_id"] = charge_prefix + "_supported"
    row["payment"]["intent"]["latest_charge"] = row["payment"]["charge"]["id"] = row[
        "payment"
    ]["charge_id"]
    row["connect_transfer"]["source_transaction"] = row["payment"]["charge_id"]
    row["refund"]["refund_id"] = refund_prefix + "_supported"
    row["refund"]["transfer_outcome"]["refund_id"] = row["refund"]["refund_id"]
    row["payment"]["capture_event"]["data"]["object"] = deepcopy(
        row["payment"]["intent"]
    )
    row["refund"]["inventory"]["data"][0].update(
        id=row["refund"]["refund_id"], charge=row["payment"]["charge_id"]
    )
    assert check(bundle) == []


def test_provider_refund_cannot_be_reused_for_another_billing_record(bundle):
    first, second = bundle["stripe_runs"][:2]
    second["refund"]["refund_id"] = first["refund"]["refund_id"]
    second["refund"]["transfer_outcome"]["refund_id"] = first["refund"]["refund_id"]
    assert any("reused across financial scopes" in error for error in check(bundle))


def test_refund_before_original_transfer_is_not_the_required_journey(bundle):
    row = bundle["stripe_runs"][0]
    row["refund"]["recorded_at"] = row["settlement"]["recorded_at"]
    assert check(bundle)


@pytest.mark.parametrize(
    "object_name,field,value",
    [
        ("intent", "status", "requires_payment_method"),
        ("intent", "latest_charge", "py_other"),
        ("intent", "amount_received", 1),
        ("charge", "paid", False),
        ("charge", "captured", False),
        ("charge", "status", "failed"),
        ("charge", "payment_intent", "pi_other"),
        ("charge", "amount", 1),
    ],
)
def test_provider_payment_contradictions_cannot_pass_normalized_capture(
    bundle, object_name, field, value
):
    bundle["stripe_runs"][0]["payment"][object_name][field] = value
    assert check(bundle)


@pytest.mark.parametrize("key", ["intent", "charge"])
def test_raw_provider_payment_state_is_required(bundle, key):
    del bundle["stripe_runs"][0]["payment"][key]
    assert check(bundle)


def test_refund_creation_before_transfer_cannot_hide_behind_later_observation(bundle):
    row = bundle["stripe_runs"][0]
    row["refund"]["created_at"] = row["settlement"]["recorded_at"]
    assert check(bundle)


@pytest.mark.parametrize("field", ["created_at", "metadata"])
def test_refund_provider_time_and_metadata_are_required(bundle, field):
    del bundle["stripe_runs"][0]["refund"][field]
    assert check(bundle)


@pytest.mark.parametrize(
    "field,value",
    [("billing_record_id", "unrelated"), ("idempotency_key", "manually-created")],
)
def test_frozen_refund_request_must_match_provider_metadata(bundle, field, value):
    bundle["stripe_runs"][0]["refund"]["metadata"][field] = value
    assert check(bundle)


@pytest.mark.parametrize(
    "field,value",
    [("reversal_id", "trr_success"), ("balance_transaction", "txn_success")],
)
def test_platform_loss_cannot_also_claim_successful_reversal_identifiers(
    bundle, field, value
):
    outcome = bundle["stripe_runs"][0]["refund"]["transfer_outcome"]
    outcome.pop("provider_reversal", None)
    outcome.pop("reversal_inventory", None)
    outcome.update(
        kind="platform_loss",
        reversal_id=None,
        balance_transaction=None,
        reversed_cents=0,
        platform_loss_cents=20,
        reversal_refused=True,
        reason="insufficient_connect_balance",
        operator_visible=True,
        financial_state="recorded",
        refunded_from_platform_cents=100,
        loss_record="owner-loss",
    )
    assert check(bundle) == []
    outcome[field] = value
    assert check(bundle)


def test_unrelated_source_charge_cannot_fund_recovered_transfer(bundle):
    bundle["stripe_runs"][0]["connect_transfer"]["source_transaction"] = "py_other"
    assert check(bundle)


def test_expanded_latest_charge_reference_matches_observed_charge(bundle):
    payment = bundle["stripe_runs"][0]["payment"]
    payment["intent"]["latest_charge"] = {"id": payment["charge_id"]}
    assert check(bundle) == []


@pytest.mark.parametrize("field", ["tenant_id", "billing_record_id"])
def test_provider_payment_metadata_cannot_name_another_owner(bundle, field):
    bundle["stripe_runs"][0]["payment"]["intent"]["metadata"][field] = "another-owner"
    assert check(bundle)


def test_refund_provider_creation_must_not_follow_its_observation(bundle):
    row = bundle["stripe_runs"][0]
    row["refund"]["created_at"] = row["recorded_at"]
    assert check(bundle)


def test_loss_outcome_cannot_preserve_reversal_balance_transaction(bundle):
    outcome = bundle["stripe_runs"][0]["refund"]["transfer_outcome"]
    outcome.pop("provider_reversal", None)
    outcome.pop("reversal_inventory", None)
    outcome.update(
        kind="platform_loss",
        reversal_id=None,
        reversed_cents=0,
        platform_loss_cents=20,
        reversal_refused=True,
        reason="insufficient_connect_balance",
        operator_visible=True,
        financial_state="recorded",
        refunded_from_platform_cents=100,
        loss_record="owner-loss",
    )
    assert check(bundle)


@pytest.mark.parametrize("key", ["bank_funding"])
def test_bank_payout_needs_provider_funding_chain_not_a_copied_transfer_id(bundle, key):
    del bundle["gateway_runs"][0][key]
    assert check(bundle)


@pytest.mark.parametrize(
    "field,value",
    [
        ("source", "py_unrelated"),
        ("amount", 99),
        ("net", 99),
        ("fee", 1),
        ("status", "pending"),
    ],
)
def test_unrelated_or_incomplete_destination_credit_cannot_fund_journey(
    bundle, field, value
):
    bundle["gateway_runs"][0]["bank_funding"]["transactions"][0][field] = value
    assert check(bundle)


@pytest.mark.parametrize(
    "field,value",
    [
        ("id", "txn_unrelated"),
        ("source", "po_unrelated"),
        ("amount", -1),
        ("net", -1),
        ("type", "transfer"),
    ],
)
def test_payout_debit_must_match_provider_payout_and_amount(bundle, field, value):
    bundle["gateway_runs"][0]["bank_funding"]["transactions"][1][field] = value
    assert check(bundle)


@pytest.mark.parametrize("key", ["available_cents", "pending_cents", "reserved_cents"])
def test_preexisting_connected_balance_cannot_stand_in_for_journey_credit(bundle, key):
    bundle["gateway_runs"][0]["bank_funding"]["opening_balance"][key] = 100
    assert check(bundle)


def test_unrelated_intervening_activity_and_incomplete_inventory_reject(bundle):
    funding = bundle["gateway_runs"][0]["bank_funding"]
    funding["transactions_complete"] = False
    assert check(bundle)
    funding["transactions_complete"] = True
    funding["has_more"] = True
    assert check(bundle)
    funding["has_more"] = False
    funding["transactions"].append(dict(funding["transactions"][0], id="txn_other"))
    assert check(bundle)


def test_cloning_successful_stripe_journey_does_not_count_as_three_runs(bundle):
    from copy import deepcopy

    original = bundle["stripe_runs"][0]
    copies = [deepcopy(original) for _ in range(3)]

    def rename(value, suffix):
        if isinstance(value, dict):
            # Rename evidence IDs only, leaving provider IDs and financial scope.
            if "url" in value and "recorded_at" in value:
                value["id"] += suffix
            for child in value.values():
                rename(child, suffix)
        elif isinstance(value, list):
            for child in value:
                rename(child, suffix)

    for i, row in enumerate(copies):
        rename(row, f"-copy-{i}")
        row["recorded_at"] = f"2026-10-12T0{5 + i}:00:00+00:00"
    bundle["stripe_runs"] = copies
    errors = check(bundle)
    assert any("distinct lease_id within stripe_runs" in e for e in errors)
    assert any("distinct billing_record_id within stripe_runs" in e for e in errors)


@pytest.mark.parametrize(
    "field,value",
    [("id", "evt_other"), ("type", "payment_intent.processing"), ("livemode", True)],
)
def test_capture_event_requires_provider_success(bundle, field, value):
    event = bundle["stripe_runs"][0]["payment"]["capture_event"]
    event[field] = value
    if field == "id":
        event["data"]["object"]["id"] = "pi_other"
    assert any("provider succeeded event" in e for e in check(bundle))


def test_old_capture_reread_is_not_a_fresh_journey(bundle):
    row = bundle["stripe_runs"][0]
    row["payment"]["capture_event"]["created_at"] = (
        datetime.fromisoformat(row["started_at"]) - timedelta(seconds=1)
    ).isoformat()
    assert any("provider capture and observation" in e for e in check(bundle))


def test_recovery_cannot_precede_provider_refund(bundle):
    row = bundle["stripe_runs"][0]
    row["refund"]["transfer_outcome"]["recorded_at"] = (
        datetime.fromisoformat(row["refund"]["created_at"]) - timedelta(seconds=1)
    ).isoformat()
    assert any("outcome must follow provider refund" in e for e in check(bundle))


@pytest.mark.parametrize(
    "field,value",
    [
        ("connect_transfer_enabled", False),
        ("connect_transfer_enabled", None),
        ("tenant_id", "unrelated"),
        ("recorded_at", "2026-10-12T02:59:59+00:00"),
    ],
)
def test_frozen_dispatch_checkpoint_required_before_checkout(bundle, field, value):
    row = bundle["stripe_runs"][0]
    checkpoint = row["payment"]["dispatch_checkpoint"]
    if field == "recorded_at":
        value = row["payment"]["recorded_at"]
    checkpoint[field] = value
    assert any("dispatch" in e for e in check(bundle))


@pytest.mark.parametrize(
    "field,value", [("metadata", {}), ("destination", "acct_other")]
)
def test_transfer_provider_scope_cannot_be_declared_by_bundle(bundle, field, value):
    bundle["stripe_runs"][0]["connect_transfer"][field] = value
    assert any("provider transfer must match frozen" in e for e in check(bundle))


@pytest.mark.parametrize(
    "field,value",
    [
        ("transfer", "tr_other"),
        ("amount", 1),
        ("amount", True),
        ("currency", "eur"),
        ("id", "trr_other"),
        ("balance_transaction", "txn_other"),
        ("metadata", {}),
        ("metadata", {"reversal_target_cents": "20"}),
    ],
)
def test_reversal_requires_provider_binding(bundle, field, value):
    bundle["stripe_runs"][0]["refund"]["transfer_outcome"]["provider_reversal"][
        field
    ] = value
    assert any("provider reversal must match" in e for e in check(bundle))


@pytest.mark.parametrize(
    "field,value",
    [
        ("complete", False),
        ("has_more", True),
        ("data", []),
        ("payment_intent_id", "pi_other"),
    ],
)
def test_refund_requires_complete_inventory(bundle, field, value):
    bundle["stripe_runs"][0]["refund"]["inventory"][field] = value
    assert any("refund" in e for e in check(bundle))


def test_duplicate_frozen_refunds_fail_even_with_unique_provider_ids(bundle):
    inventory = bundle["stripe_runs"][0]["refund"]["inventory"]
    duplicate = deepcopy(inventory["data"][0])
    duplicate["id"] = "pyr_duplicate"
    inventory["data"].append(duplicate)
    assert any("exactly one provider refund" in e for e in check(bundle))


def test_refund_inventory_cannot_substitute_another_result(bundle):
    bundle["stripe_runs"][0]["refund"]["inventory"]["data"][0]["amount"] = 1
    assert any("exactly one provider refund" in e for e in check(bundle))


@pytest.mark.parametrize("field", ["rail", "crypto"])
def test_stablecoin_label_cannot_bypass_non_stablecoin_prerequisite(bundle, field):
    payment = bundle["stripe_runs"][0]["payment"]
    if field == "rail":
        payment["intent"]["metadata"].pop("payment_rail")
        payment["capture_event"]["data"]["object"] = deepcopy(payment["intent"])
    else:
        payment["charge"]["payment_method_details"] = {"type": "card", "card": {}}
    assert any("stablecoin source requires provider rail" in e for e in check(bundle))


@pytest.mark.parametrize(
    "key", ["refund_recovery", "failed_deposit", "repeated_mismatch"]
)
def test_boolean_only_exercises_cannot_pass(bundle, key):
    original = bundle[key]
    bundle[key] = {
        k: original[k] for k in ("id", "revision", "url", "recorded_at", "passed")
    }
    bundle[key].update(short_balance=True, recovered=True, no_lease_started=True)
    assert any(key in error for error in check(bundle))


@pytest.mark.parametrize(
    "key",
    [
        "payment",
        "settlement",
        "connect_transfer",
        "refund",
        "reversal_refusal",
        "final_accounting",
    ],
)
def test_short_balance_recovery_requires_financial_records(bundle, key):
    bundle["refund_recovery"].pop(key)
    assert any("refund_recovery" in error for error in check(bundle))


@pytest.mark.parametrize(
    "field,value",
    [
        ("code", "unrelated_error"),
        ("amount_cents", 1),
        ("available_cents", 20),
        ("available_cents", True),
        ("transfer_id", "tr_other"),
        ("refund_id", "pyr_other"),
        ("billing_record_id", "unrelated"),
        ("request_id", None),
        ("livemode", True),
    ],
)
def test_short_balance_refusal_must_match_frozen_adjustment(bundle, field, value):
    bundle["refund_recovery"]["reversal_refusal"][field] = value
    assert any("balance_insufficient refusal" in e for e in check(bundle))


@pytest.mark.parametrize(
    "field,value",
    [
        ("renter_refunded_cents", 1),
        ("platform_loss_cents", 1),
        ("host_debited_cents", 1),
        ("owner_checkpoint", "unrelated"),
        ("status", "planned"),
        ("refund_id", "pyr_other"),
        ("lease_id", "unrelated"),
    ],
)
def test_recovery_final_checkpoint_requires_completed_full_refund(bundle, field, value):
    bundle["refund_recovery"]["final_accounting"][field] = value
    assert any("final owner checkpoint" in e for e in check(bundle))


@pytest.mark.parametrize(
    "field,value",
    [
        ("amount", -1),
        ("net", -1),
        ("fee", 1),
        ("source", "pyr_other"),
        ("currency", "eur"),
        ("id", "txn_other"),
    ],
)
def test_recovery_balance_transaction_requires_exact_original_refund(
    bundle, field, value
):
    bundle["refund_recovery"]["final_accounting"]["refund_balance_transaction"][
        field
    ] = value
    assert any("refund_recovery" in e for e in check(bundle))


def test_short_balance_recovery_cannot_reuse_a_successful_journey(bundle):
    original = bundle["stripe_runs"][0]
    bundle["refund_recovery"].update(
        {key: original[key] for key in ("lease_id", "billing_record_id")}
    )
    assert any("requires distinct lease and billing" in e for e in check(bundle))


def test_refusal_probes_cannot_reuse_recovery_payment(bundle):
    bundle["failed_deposit"]["intent"]["id"] = bundle["refund_recovery"]["payment"][
        "payment_intent_id"
    ]
    assert any("distinct provider pi attempt" in e for e in check(bundle))


@pytest.mark.parametrize(
    "field,value",
    [
        ("complete", False),
        ("activation_count", 1),
        ("success_event_count", 1),
        ("lease_started", True),
        ("billing_record_id", "unrelated"),
        ("payment_intent_id", "pi_other"),
        ("attempt_id", "other"),
    ],
)
def test_refusal_audit_must_be_complete_and_scope_bound(bundle, field, value):
    bundle["failed_deposit"]["no_start_audit"][field] = value
    assert any("complete owner no-start audit" in e for e in check(bundle))


def test_refusal_audit_cannot_stop_before_minimum_window(bundle):
    row = bundle["failed_deposit"]
    row["no_start_audit"]["window_end_at"] = (
        datetime.fromisoformat(row["provider_event"]["created_at"])
        + timedelta(seconds=59)
    ).isoformat()
    assert any("at least 60 seconds" in e for e in check(bundle))


@pytest.mark.parametrize(
    "field,value",
    [
        ("status", "succeeded"),
        ("amount_received", 400),
        ("latest_charge", "py_captured"),
        ("livemode", True),
    ],
)
def test_failed_deposit_requires_provider_failure_without_capture(bundle, field, value):
    bundle["failed_deposit"]["intent"][field] = value
    assert any("failed_deposit" in e for e in check(bundle))


def test_refusals_cannot_reuse_one_payment_attempt(bundle):
    bundle["repeated_mismatch"]["attempts"][0] = deepcopy(bundle["failed_deposit"])
    assert any("distinct provider" in e for e in check(bundle))


def test_repeated_mismatch_cannot_replay_one_event_three_times(bundle):
    repeated = bundle["repeated_mismatch"]
    repeated["attempts"] = [deepcopy(repeated["attempts"][0]) for _ in range(3)]
    assert any("distinct provider" in e for e in check(bundle))


@pytest.mark.parametrize(
    "field,value",
    [
        ("mismatch_count", 2),
        ("operator_required", False),
        ("reason", "cooldown"),
        ("actor_sub", "another-person"),
    ],
)
def test_repeated_mismatch_requires_same_person_and_repeat_routing(
    bundle, field, value
):
    bundle["repeated_mismatch"][field] = value
    assert any("repeated_mismatch" in e for e in check(bundle))


def test_matching_provider_payer_is_not_mismatch_evidence(bundle):
    row = bundle["repeated_mismatch"]["attempts"][0]
    row["charge"]["payment_method_details"]["crypto"]["buyer_address"] = row[
        "expected_payer"
    ]["address"]
    assert any("captured provider payer mismatch" in e for e in check(bundle))


@pytest.mark.parametrize(
    "key", ["expected_payer", "charge", "provider_event", "no_start_audit"]
)
def test_each_mismatch_requires_its_own_provider_and_owner_evidence(bundle, key):
    bundle["repeated_mismatch"]["attempts"][1].pop(key)
    assert any("repeated_mismatch" in e for e in check(bundle))


@pytest.mark.parametrize(
    "crypto",
    [
        {},
        {"network": "ethereum", "token_currency": "usdc"},
        {"network": "base", "token_currency": "usdt"},
    ],
)
def test_stablecoin_journeys_require_base_usdc(bundle, crypto):
    bundle["stripe_runs"][0]["payment"]["charge"]["payment_method_details"][
        "crypto"
    ] = crypto
    assert any("stablecoin source requires" in e for e in check(bundle))


@pytest.mark.parametrize("status", ["succeeded", "pending", "requires_action"])
def test_nonmatching_refund_cannot_hide_actual_or_pending_money_movement(
    bundle, status
):
    inventory = bundle["stripe_runs"][0]["refund"]["inventory"]
    manual = deepcopy(inventory["data"][0])
    manual.update(id="pyr_manual", amount=50, metadata={}, status=status)
    inventory["data"].append(manual)
    assert any(
        "additional successful or unresolved refunds" in e for e in check(bundle)
    )


@pytest.mark.parametrize("key", ["provider_reversal", "reversal_inventory"])
def test_platform_loss_cannot_retain_nested_provider_reversal(bundle, key):
    bundle["refund_recovery"]["refund"]["transfer_outcome"][key] = deepcopy(
        bundle["stripe_runs"][0]["refund"]["transfer_outcome"][key]
    )
    assert any("platform-funded refund, recorded loss" in e for e in check(bundle))


@pytest.mark.parametrize("metadata", [None, {}, {"tenant_id": "another"}])
def test_gateway_transfers_require_provider_settlement_scope(bundle, metadata):
    bundle["gateway_runs"][0]["connect_transfer"]["metadata"] = metadata
    assert any("provider transfer metadata must match" in e for e in check(bundle))


def test_historical_reversal_cannot_recover_a_later_refund(bundle):
    refund = bundle["stripe_runs"][0]["refund"]
    reversal = refund["transfer_outcome"]["provider_reversal"]
    reversal["created_at"] = (
        datetime.fromisoformat(refund["created_at"]) - timedelta(seconds=1)
    ).isoformat()
    assert any("provider reversal creation must follow" in e for e in check(bundle))


@pytest.mark.parametrize(
    "key,value",
    [
        ("complete", False),
        ("has_more", True),
        ("transfer_id", "tr_unrelated"),
        ("data", []),
    ],
)
def test_reversal_list_must_be_complete_for_the_original_transfer(bundle, key, value):
    bundle["stripe_runs"][0]["refund"]["transfer_outcome"]["reversal_inventory"][
        key
    ] = value
    assert any("reversal" in e for e in check(bundle))


def test_duplicate_provider_reversals_for_one_target_fail(bundle):
    inventory = bundle["stripe_runs"][0]["refund"]["transfer_outcome"][
        "reversal_inventory"
    ]
    duplicate = deepcopy(inventory["data"][0])
    duplicate["id"] = "trr_duplicate"
    inventory["data"].append(duplicate)
    assert any("exactly one provider reversal" in e for e in check(bundle))


def test_unrelated_provider_reversal_cannot_hide_extra_host_debit(bundle):
    inventory = bundle["stripe_runs"][0]["refund"]["transfer_outcome"][
        "reversal_inventory"
    ]
    extra = deepcopy(inventory["data"][0])
    extra.update(id="trr_manual", metadata={})
    inventory["data"].append(extra)
    assert any(
        "unrelated reversal requires settlement reconciliation" in e
        for e in check(bundle)
    )
