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
ENROLLMENT_ATTEMPT = str(uuid5(NAMESPACE_URL, "original-enrollment"))
COMMIT_TIME = datetime(2026, 10, 12, tzinfo=UTC)


def bind_saved_deposit(row):
    payment = row["payment"]
    checkout = datetime.fromisoformat(payment["checkout_created_at"])
    payment["deposit_binding"] = record(
        "saved-deposit-" + row["id"],
        recorded_at=(checkout - timedelta(seconds=5)).isoformat(),
        source="payment_deposit_binding",
        binding={
            "version": 1,
            "tenant_id": row["tenant_id"],
            "billing_record_id": row["billing_record_id"],
            "lease_id": row["lease_id"],
            "wallet": payment["payer_snapshot"]["address"].lower(),
            "actor_sub": payment["actor_sub"],
            "intent_id": payment["payment_intent_id"],
            "amount_cents": payment["amount_cents"],
            "subtotal_cents": payment["subtotal_cents"],
            "tax_payable": deepcopy(payment["tax_payable"]),
            "operation_id": str(uuid5(NAMESPACE_URL, "deposit" + row["id"])),
            "attempted_at": (checkout - timedelta(seconds=10)).isoformat(),
            "return_url": "https://example.org/return",
        },
    )


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
                delivered_compute_cents=400,
                platform_compute_cents=300,
                currency="usd",
                tenant_id="11111111-1111-1111-1111-111111111111",
                host_org_id="22222222-2222-2222-2222-222222222222",
                lease_id=str(uuid5(NAMESPACE_URL, "lease" + name)),
                billing_record_id=str(uuid5(NAMESPACE_URL, "billing" + name)),
            ),
            bank_followup=record(
                "followup-" + name,
                hour,
                recorded_at=(at + timedelta(days=9)).isoformat(),
                events_from_at=created_at,
                events_through_at=(at + timedelta(days=9)).isoformat(),
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
                automatic=False,
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
    if name.startswith(("gateway-", "stripe-", "ok-")):
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
            refund_idempotency_key=f"lease-settle-{result['billing_record_id']}-100",
            post_refund_host_payable_cents=80,
            post_refund_platform_compute_cents=220,
            tax_allocation={
                "delivered_compute_cents": 300,
                "retained_tax_cents": 0,
                "refund_compute_cents": 100,
                "refund_tax_cents": 0,
                "refund_total_cents": 100,
                "allocation_policy": "EXAMPLE-compute-proportional-tax-v1",
            },
            reversal_due_cents=20,
            payment_intent_id=payment_id,
            charge_id=charge_id,
        )
    if name.startswith(("gateway-", "ok-")):
        result["settlement"].update(
            retained_cents=400,
            refund_due_cents=0,
            post_refund_host_payable_cents=100,
            post_refund_platform_compute_cents=300,
            reversal_due_cents=0,
            tax_allocation={
                "delivered_compute_cents": 400,
                "retained_tax_cents": 0,
                "refund_compute_cents": 0,
                "refund_tax_cents": 0,
                "refund_total_cents": 0,
                "allocation_policy": "EXAMPLE-compute-proportional-tax-v1",
            },
        )
    if name.startswith("stripe-"):
        result["refund"] = record(
            "refund-" + name,
            hour,
            recorded_at=(at - timedelta(seconds=47)).isoformat(),
            **scope,
            refund_id=refund_id,
            created_at=(at - timedelta(seconds=50)).isoformat(),
            metadata={
                "billing_record_id": scope["billing_record_id"],
                "idempotency_key": f"lease-settle-{result['billing_record_id']}-100",
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
                recorded_at=(at - timedelta(seconds=46)).isoformat(),
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
            recorded_at=(at - timedelta(seconds=45)).isoformat(),
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
                    "created_at": result["refund"]["created_at"],
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
            at - timedelta(seconds=48)
        ).isoformat()
        outcome["reversal_inventory"] = record(
            "reversal-inventory-" + name,
            hour,
            recorded_at=(at - timedelta(seconds=44)).isoformat(),
            transfer_id=result["connect_transfer"]["id"],
            complete=True,
            has_more=False,
            data=[deepcopy(outcome["provider_reversal"])],
        )
    if name.startswith(("gateway-", "stripe-", "ok-")):
        scope = {
            k: result[k]
            for k in ("tenant_id", "host_org_id", "lease_id", "billing_record_id")
        }
        transfer = result["connect_transfer"]
        transfer.setdefault("source_transaction", "py_source" + token)
        provider = deepcopy(transfer)
        provider["amount"] = provider.pop("amount_cents")
        result["transfer_inventory"] = record(
            "transfers-" + name,
            hour,
            recorded_at=(at - timedelta(seconds=1)).isoformat(),
            destination=transfer["account_id"],
            complete=True,
            has_more=False,
            data=[provider],
        )
        controls_at = at - timedelta(seconds=120)
        result["readiness"]["payout_controls"] = record(
            "controls-" + name,
            hour,
            recorded_at=controls_at.isoformat(),
            tenant_id=scope["tenant_id"],
            host_org_id=scope["host_org_id"],
            account_id=transfer["account_id"],
            livemode=False,
            schedule_interval="manual",
            host_standard_payouts=False,
            host_instant_payouts=False,
            host_schedule_editing=False,
            host_probes=[
                record(
                    "probe-" + operation + "-" + name,
                    hour,
                    recorded_at=(controls_at - timedelta(seconds=10)).isoformat(),
                    tenant_id=scope["tenant_id"],
                    host_org_id=scope["host_org_id"],
                    account_id=transfer["account_id"],
                    livemode=False,
                    actor_sub="sandbox-host-owner",
                    actor_role="host",
                    operation=operation,
                    allowed=False,
                    http_status=403,
                )
                for operation in ("standard_payout", "instant_payout", "schedule_edit")
            ],
        )
    if name.startswith(("gateway-", "stripe-", "ok-")):
        payment = result["payment"]
        payment["actor_sub"] = "sandbox-renter"
        payment["charge"].update(amount_refunded=0, refunded=False)
        payment["charge"]["payment_method_details"]["crypto"]["buyer_address"] = (
            "0x" + "1" * 40
        )
        payment["payer_snapshot"] = record(
            "payer-" + name,
            hour,
            recorded_at=(at - timedelta(seconds=260)).isoformat(),
            source="auth_siwe_me",
            tenant_id=scope["tenant_id"],
            actor_sub="sandbox-renter",
            address="0x" + "1" * 40,
        )
    if name.startswith(("gateway-", "stripe-", "ok-")):
        controls = result["readiness"]["payout_controls"]
        controls["ownership_snapshot"] = record(
            "ownership-" + name,
            hour,
            recorded_at=(at - timedelta(seconds=140)).isoformat(),
            source="platform_tenant_registry",
            subject_to_host={
                "subject": "sandbox-host-owner",
                "role": "owner",
                "active": True,
                "tenant_id": scope["tenant_id"],
                "host_org_id": scope["host_org_id"],
                "stripe_account_id": transfer["account_id"],
                "enrollment_attempt_id": ENROLLMENT_ATTEMPT,
            },
        )
    if name.startswith(("gateway-", "stripe-", "ok-")):
        result["readiness"]["platform_payout_schedule"] = record(
            "platform-" + name,
            hour,
            recorded_at=(at - timedelta(seconds=120)).isoformat(),
            account_id="acct_platform",
            livemode=False,
            source="stripe_platform_account",
            schedule_interval="manual",
        )
    if name.startswith(("gateway-", "stripe-", "ok-")):
        result["payment"]["intent"].update(object="payment_intent", amount=400)
        result["payment"]["charge"].update(
            object="charge",
            amount_captured=400,
            created=int((at - timedelta(seconds=190)).timestamp()),
        )
        result["payment"]["charge"]["payment_method_details"]["crypto"][
            "transaction_hash"
        ] = "0x" + "ef" * 32
    if name.startswith(("gateway-", "stripe-", "ok-")):
        result["bank_payout"]["destination"] = result["bank_payout"]["bank_id"]
        result["payout_readiness"] = fresh_payout_readiness(result)
    if name.startswith(("gateway-", "stripe-", "ok-")):
        payment = result["payment"]
        payment["intent"]["metadata"].update(
            lease_id=result["lease_id"], payment_flow="deposit"
        )
        payment["intent"]["payment_method_options"] = {"crypto": {"mode": "deposit"}}
        payment.update(
            tax_revision_id=str(uuid5(NAMESPACE_URL, "tax" + name)),
            tax_calculation_id="taxcalc_" + token,
        )
        payment["intent"]["metadata"].update(
            {k: payment[k] for k in ("tax_revision_id", "tax_calculation_id")}
        )
        payment.update(
            subtotal_cents=400,
            tax_payable={
                "version": 1,
                "revision_id": payment["tax_revision_id"],
                "calculation_id": payment["tax_calculation_id"],
                "subtotal_cents": 400,
                "tax_cents": 0,
                "total_cents": 400,
                "policy_version": "EXAMPLE-tax-1",
                "tax_code": "txcd_10000000",
                "expires_at": int(at.timestamp()),
            },
        )
        payment["capture_event"]["data"]["object"] = deepcopy(payment["intent"])
    if name.startswith(("gateway-", "stripe-", "ok-")):
        bind_saved_deposit(result)
    if name.startswith("stripe-"):
        sync_bank_to_outcome(result)
    if name.startswith(("gateway-", "stripe-", "ok-")):
        bind_connect_dispatch(result)
        bind_source_type(result, result["bank_payout"])
    return result


def sync_bank_to_outcome(row):
    """Build an isolated provider ledger for the selected refund outcome."""
    outcome = row["refund"]["transfer_outcome"]
    reversed_host = outcome["kind"] == "reversed"
    payable = (
        row["settlement"]["post_refund_host_payable_cents"]
        if reversed_host
        else row["settlement"]["host_payable_cents"]
    )
    row["bank_payout"]["amount_cents"] = payable
    transactions = row["bank_funding"]["transactions"]
    transactions[:] = [tx for tx in transactions if tx["type"] != "payment_refund"]
    transactions[1].update(amount=-payable, net=-payable)
    if outcome["kind"] == "platform_loss":
        # The bank has already drained the isolated balance when recovery fails.
        at = datetime.fromisoformat(row["recorded_at"])
        refund = row["refund"]
        refund["created_at"] = (at - timedelta(seconds=8)).isoformat()
        refund["recorded_at"] = (at - timedelta(seconds=5)).isoformat()
        refund["inventory"]["recorded_at"] = (at - timedelta(seconds=3)).isoformat()
        outcome["recorded_at"] = (at - timedelta(seconds=4)).isoformat()
        refusal = outcome.get("provider_refusal", row.get("reversal_refusal"))
        refusal["recorded_at"] = (at - timedelta(seconds=5)).isoformat()
        refund["inventory"]["data"][0]["created_at"] = refund["created_at"]
        refund["discovery"] = record(
            "refund-discovered-" + row["id"],
            recorded_at=(at - timedelta(seconds=9)).isoformat(),
            source="payment_refund_discovery",
            **{
                k: row[k]
                for k in ("tenant_id", "host_org_id", "lease_id", "billing_record_id")
            },
            payment_intent_id=row["payment"]["payment_intent_id"],
            refund_idempotency_key=row["settlement"]["refund_idempotency_key"],
            refund_due_cents=row["settlement"]["refund_due_cents"],
        )
        empty_reversals(row)
    if reversed_host:
        reversal = outcome["provider_reversal"]
        reversal["destination_payment_refund"] = (
            "pyr_connected" + row["connect_transfer"]["id"][3:]
        )
        outcome["reversal_inventory"]["data"][0] = deepcopy(reversal)
        transactions.append(
            {
                "id": "txn_connected" + row["connect_transfer"]["id"][3:],
                "source": reversal["destination_payment_refund"],
                "type": "payment_refund",
                "currency": "usd",
                "status": "available",
                "amount": -outcome["reversed_cents"],
                "fee": 0,
                "net": -outcome["reversed_cents"],
                "created_at": reversal["created_at"],
                "available_at": reversal["created_at"],
            }
        )

    if "bank_dispatch_plan" in row:
        bind_source_type(row, row["bank_payout"])
    bind_money_dispatch(row)


def empty_reversals(row):
    row["refund"]["transfer_outcome"]["reversal_inventory"] = record(
        "empty-reversals-" + row["id"],
        recorded_at=row["recorded_at"],
        transfer_id=row["connect_transfer"]["id"],
        complete=True,
        has_more=False,
        data=[],
    )


def bind_money_dispatch(row, mismatch=False):
    refund = row.get("refund")
    if not isinstance(refund, dict):
        return
    scope = {
        k: row[k] for k in ("tenant_id", "host_org_id", "lease_id", "billing_record_id")
    }
    payment = (
        {
            "payment_intent_id": row["intent"]["id"],
            "charge_id": row["charge"]["id"],
            "subtotal_cents": row["subtotal_cents"],
        }
        if mismatch
        else row["payment"]
    )
    settlement = row.get("settlement") or {}
    raw = row["refund_plan"]["plan"] if mismatch else {}
    allocation = (
        raw.get("stripe_tax_allocation")
        if mismatch
        else settlement.get("tax_allocation")
    )
    common = scope | {
        "payment_intent_id": payment["payment_intent_id"],
        "charge_id": payment["charge_id"],
        "amount_cents": refund["amount_cents"],
        "currency": "usd",
        "livemode": False,
        "reserved_compute_cents": payment["subtotal_cents"],
        "refund_compute_cents": raw.get("refund_cents")
        if mismatch
        else allocation["refund_compute_cents"],
        "delivered_compute_cents": 0
        if mismatch
        else allocation["delivered_compute_cents"],
        "tax_allocation": deepcopy(allocation),
    }
    key = (
        row["final_accounting"]["refund_idempotency_key"]
        if mismatch
        else settlement["refund_idempotency_key"]
    )
    created = datetime.fromisoformat(refund["created_at"])
    attempted = (
        raw["stripe_refund_attempted_at"]
        if mismatch
        else (created - timedelta(seconds=3)).isoformat()
    )
    plan_fields = common | {"idempotency_key": key, "attempted_at": attempted}
    if mismatch:
        plan_fields["approval_at"] = row["refund_approval"]["approved_at"]
    refund["dispatch_plan"] = record(
        "dispatch-refund-plan-" + row["id"],
        recorded_at=(created - timedelta(seconds=2)).isoformat(),
        source="payment_refund_plan",
        status="prepared",
        **plan_fields,
    )
    refund["dispatch_checkpoint"] = record(
        "dispatch-refund-bound-" + row["id"],
        recorded_at=(created + timedelta(microseconds=500000)).isoformat(),
        source="payment_refund_dispatch",
        status="bound",
        refund_id=refund["refund_id"],
        **plan_fields,
        provider_request=record(
            "request-refund-" + row["id"],
            recorded_at=refund["created_at"],
            started_at=refund["created_at"],
            source="stripe_request",
            request_id="req_refund" + refund["refund_id"].replace("_", ""),
            method="POST",
            path="/v1/refunds",
            livemode=False,
            idempotency_key=key,
            refund_id=refund["refund_id"],
            parameters={
                "payment_intent": payment["payment_intent_id"],
                "amount": refund["amount_cents"],
                "metadata": {
                    "billing_record_id": row["billing_record_id"],
                    "idempotency_key": key,
                },
            },
        ),
    )
    outcome = refund.get("transfer_outcome") or {}
    if outcome.get("kind") not in ("reversed", "platform_loss"):
        return
    failed = outcome["kind"] == "platform_loss"
    refusal = (
        row.get("reversal_refusal", outcome.get("provider_refusal")) if failed else None
    )
    reversal = (
        {"id": None, "created_at": refusal["recorded_at"]}
        if failed
        else outcome["provider_reversal"]
    )
    transfer = row["connect_transfer"]
    due = settlement["reversal_due_cents"]
    fields = scope | {
        "transfer_id": transfer["id"],
        "refund_id": refund["refund_id"],
        "account_id": transfer["account_id"],
        "charge_id": payment["charge_id"],
        "amount_cents": due,
        "target_cents": due,
        "currency": "usd",
        "livemode": False,
        "idempotency_key": f"hm-sandbox-reversal:{row['billing_record_id']}:{due}",
        "attempted_at": refund["dispatch_checkpoint"]["recorded_at"]
        if failed
        else (
            datetime.fromisoformat(reversal["created_at"]) - timedelta(seconds=1)
        ).isoformat(),
    }
    outcome["owner_reversal"] = {
        "dispatch_plan": record(
            "owner-reverse-plan-" + row["id"],
            recorded_at=fields["attempted_at"],
            source="payment_connect_reversal_plan",
            status="reversing",
            **fields,
        ),
        "dispatch_checkpoint": record(
            "owner-reverse-bound-" + row["id"],
            recorded_at=outcome["recorded_at"],
            source="payment_connect_reversal_dispatch",
            status="platform_loss"
            if failed
            else "reversed"
            if due == transfer["amount_cents"]
            else "partially_reversed",
            reversal_id=reversal["id"],
            **fields,
            provider_request=record(
                "owner-reverse-request-" + row["id"],
                recorded_at=reversal["created_at"],
                started_at=fields["attempted_at"] if failed else reversal["created_at"],
                source="stripe_request",
                request_id=refusal["request_id"]
                if failed
                else "req_reversal" + reversal["id"].replace("_", ""),
                method="POST",
                path=f"/v1/transfers/{transfer['id']}/reversals",
                livemode=False,
                reversal_id=reversal["id"],
                idempotency_key=fields["idempotency_key"],
                parameters={
                    "amount": due,
                    "metadata": scope | {"reversal_target_cents": str(due)},
                },
            ),
        ),
    }

    if failed:
        checkpoint = outcome["owner_reversal"]["dispatch_checkpoint"]
        checkpoint.update(
            operator_required=True,
            error="InvalidRequestError",
            reversed_cents=0,
            platform_loss_cents=due,
            reversal={
                k: fields[k] for k in ("amount_cents", "target_cents", "attempted_at")
            },
        )
        checkpoint["provider_request"].update(
            error_type="InvalidRequestError", code="balance_insufficient"
        )


def bind_policy_sequence(repeated):
    count = cents = 0
    for index, row in enumerate(repeated["attempts"]):
        policy = row["policy_observation"]
        amount = row["amount_cents"]
        reason = (
            "repeated_mismatch"
            if index + 1 >= 3
            else "daily_count_cap"
            if count >= 2
            else "daily_value_cap"
            if cents + amount > 5000
            else "eligible_pending_fee_accounting"
        )
        eligible = reason == "eligible_pending_fee_accounting"
        policy.update(
            eligible=eligible,
            reason=reason,
            reserved_cents=amount if eligible else 0,
            daily_count_limit=2,
            daily_value_limit_cents=5000,
            repeat_threshold=3,
            reserved_count_before=count,
            reserved_total_cents_before=cents,
        )
        if eligible:
            count += 1
            cents += amount
        policy.update(reserved_count_after=count, reserved_total_cents_after=cents)


def bind_automatic_guard(row, repeated):
    # Use a small eligible capture in tests of automatic dispatch; the other
    # operator-only captures still fit native checkout admission and routing.
    policy = deepcopy(row["policy_observation"])
    replacement = refusal_record(
        row["id"],
        datetime.fromisoformat(row["recorded_at"]).hour,
        mismatch=True,
        amount=400,
    )
    row.clear()
    row.update(replacement)
    row["policy_observation"] = policy
    bind_policy_sequence(repeated)
    policy = row["policy_observation"]
    policy.update(
        eligible=True,
        reason="eligible_pending_fee_accounting",
        reserved_cents=row["amount_cents"],
        daily_count_limit=2,
        daily_value_limit_cents=5000,
        repeat_threshold=3,
    )
    row["refund_approval"].update(
        kind="automatic", eligible=True, reason="eligible_pending_fee_accounting"
    )
    plan = row["refund_plan"]["plan"]
    plan["automatic_approved_mismatch_at"] = plan.pop("operator_approved_mismatch_at")
    created = datetime.fromisoformat(row["refund"]["created_at"])
    row["automatic_guard"] = record(
        "automatic-guard-" + row["id"],
        recorded_at=(created - timedelta(seconds=1)).isoformat(),
        source="payment_mismatch_creation_guard",
        **{
            k: row[k]
            for k in (
                "tenant_id",
                "host_org_id",
                "lease_id",
                "billing_record_id",
                "actor_sub",
            )
        },
        payment_intent_id=row["intent"]["id"],
        charge_id=row["charge"]["id"],
        day=policy["day"],
        mismatches=policy["counter_after"],
        reserved_count=1,
        reserved_cents=row["amount_cents"],
        person_lock_held=True,
        enabled_before=True,
        enabled_after=True,
        checked_before_at=(created - timedelta(seconds=2)).isoformat(),
        checked_after_at=(created - timedelta(seconds=1)).isoformat(),
        fee_observation={
            "currency": "usd",
            "captured_cents": row["amount_cents"],
            "refunded_cents": 0,
            "refunds": [],
            "charge_balance_transaction": deepcopy(
                row["final_accounting"]["charge_balance_transaction"]
            ),
        },
    )
    bind_money_dispatch(row, mismatch=True)


def bind_connect_dispatch(row):
    transfer = row["connect_transfer"]
    transfer.setdefault("source_type", "card")
    for raw in row["transfer_inventory"]["data"]:
        if raw["id"] == transfer["id"]:
            raw["source_type"] = transfer["source_type"]
    at = datetime.fromisoformat(transfer["created_at"])
    profile = {
        "legal_type": "individual",
        "country": "US",
        "state": "CA",
        "payout_method": "fiat_bank",
        "eligibility_rule_version": "fiat-us/v1",
        "enrollment_attempt_id": ENROLLMENT_ATTEMPT,
    }
    row["readiness"]["recipient_profile"] = profile.copy()
    frozen = {
        k: row[k] for k in ("tenant_id", "host_org_id", "lease_id", "billing_record_id")
    } | {
        "account_id": transfer["account_id"],
        "charge_id": row["payment"]["charge_id"],
        "amount_cents": transfer["amount_cents"],
        "attempted_at": (at - timedelta(seconds=5)).isoformat(),
        "profile": profile,
        "reversed_cents": 0,
    }
    authority = {}
    for stage, seconds in (("prepared", 4), ("dispatching", 2), ("succeeded", -1)):
        state = deepcopy(frozen) | {
            "status": stage,
            "attempts": 0 if stage == "prepared" else 1,
        }
        if stage == "succeeded":
            state.update(transfer_id=transfer["id"], operator_required=False)
        authority[stage] = record(
            "connect-" + stage + "-" + row["id"],
            recorded_at=(at - timedelta(seconds=seconds)).isoformat(),
            source="payment_connect_dispatch",
            state=state,
        )
    authority["source_recheck"] = record(
        "source-recheck-" + row["id"],
        recorded_at=(at - timedelta(seconds=3)).isoformat(),
        source="payment_connect_source_recheck",
        **{
            k: v
            for k, v in frozen.items()
            if k not in ("profile", "attempted_at", "reversed_cents")
        },
        charge=deepcopy(row["payment"]["charge"]),
    )
    authority["provider_request"] = record(
        "transfer-request-" + row["id"],
        recorded_at=transfer["created_at"],
        source="stripe_request",
        request_id="req_connect" + row["connect_transfer"]["id"][3:],
        method="POST",
        path="/v1/transfers",
        livemode=False,
        transfer_id=transfer["id"],
        idempotency_key=f"hm-sandbox-transfer:{row['tenant_id']}:{row['lease_id']}:{row['billing_record_id']}",
        parameters={
            "amount": transfer["amount_cents"],
            "currency": "usd",
            "destination": transfer["account_id"],
            "source_transaction": row["payment"]["charge_id"],
            "metadata": {
                k: row[k]
                for k in ("tenant_id", "host_org_id", "lease_id", "billing_record_id")
            },
        },
    )
    row["connect_dispatch"] = authority
    inventory = row["transfer_inventory"]
    inventory.update(
        destination=None,
        scope="platform",
        window_start_at=datetime.fromtimestamp(
            row["payment"]["charge"]["created"], UTC
        ).isoformat(),
        window_end_at=inventory["recorded_at"],
    )
    inventory["filters"] = {
        "created": {
            "gte": int(
                datetime.fromisoformat(inventory["window_start_at"]).timestamp()
            ),
            "lte": int(datetime.fromisoformat(inventory["window_end_at"]).timestamp()),
        }
    }
    bind_money_dispatch(row)


def bind_source_type(row, payout, source_type="card"):
    row["connect_transfer"]["source_type"] = source_type
    for raw in row["transfer_inventory"]["data"]:
        if raw["id"] == row["connect_transfer"]["id"]:
            raw["source_type"] = source_type
    funding = row["bank_funding"]
    funding["source_type"] = source_type
    for side in ("opening_balance", "closing_balance"):
        balance = funding[side]
        balance["available_source_types"] = {source_type: balance["available_cents"]}
        balance["pending_source_types"] = {source_type: 0}
    bind_bank_dispatch(row, payout)


def bind_platform_payout_inventory(row, payout):
    observed = row.get("bank_followup", row.get("preservation", row))["recorded_at"]
    start = row["payment"]["charge"]["created"]
    filters = {
        "created": {
            "gte": start,
            "lte": int(datetime.fromisoformat(observed).timestamp()),
        }
    }
    provider = {
        "id": payout["payout_id"],
        "object": "payout",
        "automatic": False,
        "amount": payout["amount_cents"],
        "currency": "usd",
        "livemode": False,
        "destination": payout["bank_id"],
        "source_type": payout["source_type"],
        "metadata": deepcopy(payout["metadata"]),
        "status": payout["status"],
        "created": int(datetime.fromisoformat(payout["created_at"]).timestamp()),
    }
    account_ids = [payout["account_id"], "acct_otherInventory"]
    row["platform_payout_inventory"] = record(
        "all-payouts-" + row["id"],
        recorded_at=observed,
        source="stripe_platform_payout_inventory",
        platform_account_id="acct_platform",
        livemode=False,
        complete=True,
        window_start_at=datetime.fromtimestamp(start, UTC).isoformat(),
        window_end_at=observed,
        accounts=record(
            "all-accounts-" + row["id"],
            recorded_at=observed,
            source="stripe_account_inventory",
            platform_account_id="acct_platform",
            complete=True,
            has_more=False,
            filters={},
            total_count=2,
            data=[{"id": value, "object": "account"} for value in account_ids],
        ),
        contexts=[
            record(
                "payout-context-" + row["id"] + str(index),
                recorded_at=observed,
                source="stripe_payout_inventory",
                account_id=account,
                livemode=False,
                complete=True,
                has_more=False,
                filters=deepcopy(filters),
                total_count=1 if index == 0 else 0,
                data=[provider] if index == 0 else [],
            )
            for index, account in enumerate(account_ids)
        ],
    )


def bind_bank_dispatch(row, payout):
    token = row["id"]
    created = datetime.fromisoformat(payout["created_at"])
    scope = {
        k: row[k] for k in ("tenant_id", "host_org_id", "lease_id", "billing_record_id")
    }
    plan_id, operation_id = (
        str(uuid5(NAMESPACE_URL, prefix + token))
        for prefix in ("bank-plan", "bank-operation")
    )
    key = "hm-bank-payout-" + operation_id
    frozen = scope | {
        "payment_intent_id": row["payment"]["payment_intent_id"],
        "charge_id": row["payment"]["charge_id"],
        "transfer_id": row["connect_transfer"]["id"],
        "account_id": payout["account_id"],
        "bank_id": payout["bank_id"],
        "source_type": row["connect_transfer"].get("source_type", "card"),
        "amount_cents": payout["amount_cents"],
        "currency": "usd",
        "livemode": False,
        "plan_id": plan_id,
        "operation_id": operation_id,
        "idempotency_key": key,
    }
    row["bank_dispatch_plan"] = record(
        "bank-plan-" + token,
        recorded_at=(created - timedelta(seconds=4)).isoformat(),
        source="payment_bank_payout_plan",
        **frozen,
    )
    metadata = scope | {
        k: frozen[k] for k in ("plan_id", "operation_id", "transfer_id", "charge_id")
    }
    payout["metadata"] = deepcopy(metadata)
    payout["source_type"] = frozen["source_type"]
    row["bank_dispatch_checkpoint"] = record(
        "bank-checkpoint-" + token,
        recorded_at=(created + timedelta(seconds=2)).isoformat(),
        source="payment_bank_dispatch",
        status="bound",
        **frozen,
        payout_id=payout["payout_id"],
        attempted_at=(created - timedelta(seconds=2)).isoformat(),
        provider_request=record(
            "bank-request-" + token,
            recorded_at=(created + timedelta(seconds=1)).isoformat(),
            source="stripe_request",
            request_id="req_" + uuid5(NAMESPACE_URL, "bank-request" + token).hex,
            method="POST",
            path="/v1/payouts",
            livemode=False,
            account_id=payout["account_id"],
            idempotency_key=key,
            payout_id=payout["payout_id"],
            parameters={
                "amount": payout["amount_cents"],
                "currency": "usd",
                "destination": payout["bank_id"],
                "source_type": frozen["source_type"],
                "metadata": metadata,
            },
        ),
    )

    attempt_id = str(uuid5(NAMESPACE_URL, "bank-attempt-" + token))
    checkpoint = row["bank_dispatch_checkpoint"]
    checkpoint.update(attempt_count=1, provider_attempt_id=attempt_id)
    row["bank_dispatch_attempts"] = record(
        "bank-attempts-" + token,
        recorded_at=checkpoint["recorded_at"],
        source="payment_bank_dispatch_attempt_inventory",
        complete=True,
        has_more=False,
        filters={},
        total_count=1,
        plan=frozen,
        window_start_at=row["bank_dispatch_plan"]["recorded_at"],
        window_end_at=checkpoint["recorded_at"],
        data=[
            record(
                "bank-attempt-" + token,
                recorded_at=checkpoint["provider_request"]["recorded_at"],
                source="payment_bank_dispatch_attempt",
                attempt_id=attempt_id,
                sequence=1,
                plan=frozen,
                started_at=checkpoint["attempted_at"],
                outcome="succeeded",
                retention_guaranteed=True,
                readiness=row["payout_readiness"],
                provider_request=checkpoint["provider_request"],
            )
        ],
    )

    bind_platform_payout_inventory(row, payout)


def fresh_payout_readiness(row):
    ready = deepcopy(row["readiness"])

    def shift(value):
        if isinstance(value, dict):
            if "revision" in value and "url" in value:
                value["id"] += "-payout"
                value["recorded_at"] = (
                    datetime.fromisoformat(value["recorded_at"]) + timedelta(seconds=85)
                ).isoformat()
            for child in value.values():
                shift(child)
        elif isinstance(value, list):
            for child in value:
                shift(child)

    shift(ready)
    ready.update(
        {
            k: row[k]
            for k in ("tenant_id", "host_org_id", "lease_id", "billing_record_id")
        }
    )
    return ready


def refusal_record(name, hour, *, mismatch=False, amount=400):
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
        "amount": amount,
        "amount_received": amount if mismatch else 0,
        "payment_method_types": ["crypto"],
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
        amount_cents=amount,
        subtotal_cents=amount,
        tax_payable=None,
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
                "amount": amount,
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
    if mismatch:
        refund_id = "pyr_mismatch" + token
        key = f"lease-settle-{scope['billing_record_id']}-{amount}"
        charge_tx = {
            "id": "txn_mismatchcharge" + token,
            "source": row["charge"]["id"],
            "amount": amount,
            "fee": 10,
            "net": amount - 10,
            "currency": "usd",
            "type": "charge",
            "status": "available",
        }
        refund_tx = {
            "id": "txn_mismatchrefund" + token,
            "source": refund_id,
            "amount": -amount,
            "fee": 0,
            "net": -amount,
            "currency": "usd",
            "type": "refund",
            "status": "available",
        }
        row["charge"]["balance_transaction"] = charge_tx["id"]
        metadata = {
            "billing_record_id": scope["billing_record_id"],
            "idempotency_key": key,
        }
        created_at = (at - timedelta(seconds=60)).isoformat()
        provider_refund = {
            "id": refund_id,
            "payment_intent": intent["id"],
            "charge": row["charge"]["id"],
            "amount": amount,
            "currency": "usd",
            "status": "succeeded",
            "livemode": False,
            "metadata": metadata,
            "balance_transaction": refund_tx["id"],
            "created_at": created_at,
        }
        row["refund"] = record(
            "refunded-" + name,
            hour,
            recorded_at=(at - timedelta(seconds=15)).isoformat(),
            **scope,
            refund_id=refund_id,
            payment_intent_id=intent["id"],
            charge_id=row["charge"]["id"],
            amount_cents=amount,
            currency="usd",
            status="succeeded",
            livemode=False,
            metadata=metadata,
            created_at=created_at,
            inventory=record(
                "refunds-" + name,
                hour,
                recorded_at=(at - timedelta(seconds=10)).isoformat(),
                complete=True,
                has_more=False,
                payment_intent_id=intent["id"],
                data=[provider_refund],
            ),
        )
        row["final_accounting"] = record(
            "mismatch-accounting-" + name,
            hour,
            recorded_at=(at - timedelta(seconds=5)).isoformat(),
            **scope,
            payment_intent_id=intent["id"],
            charge_id=row["charge"]["id"],
            refund_id=refund_id,
            refund_idempotency_key=key,
            status="complete",
            currency="usd",
            livemode=False,
            owner_checkpoint="checkpoint-" + token,
            principal_refunded_cents=amount,
            host_payable_cents=0,
            retained_fee_cost_cents=10,
            charge_balance_transaction=charge_tx,
            refund_balance_transaction=refund_tx,
        )
    if mismatch:
        approved = (at - timedelta(seconds=70)).isoformat()
        checkpoint = row["final_accounting"]["owner_checkpoint"]
        row["refund_approval"] = record(
            "refund-approval-" + name,
            hour,
            recorded_at=(at - timedelta(seconds=69)).isoformat(),
            source="payment_mismatch_approval",
            kind="operator",
            approved_at=approved,
            operator_required=False,
            **scope,
            payment_intent_id=intent["id"],
            charge_id=row["charge"]["id"],
            owner_checkpoint=checkpoint,
        )
        row["refund_plan"] = record(
            "refund-plan-" + name,
            hour,
            recorded_at=(at - timedelta(seconds=65)).isoformat(),
            source="payment_refund_plan",
            **scope,
            owner_checkpoint=checkpoint,
            plan={
                "reserved_amount_cents": amount,
                "refund_cents": amount,
                "delivered_cents": 0,
                "pass_stripe_amount": False,
                "currency": "usd",
                "stripe_payment_intent_id": intent["id"],
                "stripe_charge_id": row["charge"]["id"],
                "stripe_refund_id": None,
                "idempotency_key": key,
                "operator_approved_mismatch_at": approved,
                "stripe_refund_attempted_at": (at - timedelta(seconds=66)).isoformat(),
            },
        )
        row["intent"].update(object="payment_intent")
        row["intent"]["metadata"].update(
            lease_id=scope["lease_id"], payment_flow="deposit"
        )
        row["provider_event"]["data"]["object"] = deepcopy(row["intent"])
        row["charge"].update(
            object="charge",
            amount_captured=amount,
            amount_refunded=0,
            refunded=False,
            created=int((at - timedelta(seconds=90)).timestamp()),
        )
        row["charge"]["payment_method_details"]["crypto"]["transaction_hash"] = (
            "0x" + "ef" * 32
        )
        counter = (
            int(name.rsplit("-", 1)[-1]) if name.rsplit("-", 1)[-1].isdigit() else 0
        )
        row["policy_observation"] = record(
            "policy-" + name,
            hour,
            recorded_at=(at - timedelta(seconds=85)).isoformat(),
            **scope,
            source="payment_mismatch_policy",
            actor_sub=row["actor_sub"],
            payment_intent_id=intent["id"],
            event_id=row["provider_event"]["id"],
            day="2026-10-12",
            counter_before=counter,
            counter_after=counter + 1,
        )
    row["intent"].update(
        object="payment_intent", payment_method_options={"crypto": {"mode": "deposit"}}
    )
    row["intent"]["metadata"].update(lease_id=scope["lease_id"], payment_flow="deposit")
    row["provider_event"]["data"]["object"] = deepcopy(row["intent"])
    if mismatch:
        bind_money_dispatch(row, mismatch=True)
    return row


def loss_provider_error(outcome):
    at = datetime.fromisoformat(outcome["recorded_at"]) - timedelta(seconds=1)
    return record(
        "refusal-" + outcome["id"],
        recorded_at=at.isoformat(),
        **{
            k: outcome[k]
            for k in ("tenant_id", "host_org_id", "lease_id", "billing_record_id")
        },
        transfer_id=outcome["transfer_id"],
        refund_id=outcome["refund_id"],
        account_id="acct_host",
        error_type="InvalidRequestError",
        code="balance_insufficient",
        request_id="req_" + uuid5(NAMESPACE_URL, outcome["id"]).hex,
        currency="usd",
        livemode=False,
        available_cents=0,
        amount_cents=outcome["reversal_due_cents"],
    )


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
            tax_version="EXAMPLE-tax-1",
            ruleset_version="lease-fixed-hour/v1",
            throughput_map_version="map-1",
        ),
        "enrollment": record(
            "enrollment",
            host="AGX64-1",
            owner_approved=True,
            owner_sub="sandbox-host-owner",
            connect_onboarding=True,
            bank_payout_id="po_" + uuid5(NAMESPACE_URL, "stripe-5").hex,
            stripe_account_id="acct_host",
            attempt_id=ENROLLMENT_ATTEMPT,
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
            automatic=False,
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
                    "attempt_id": ENROLLMENT_ATTEMPT,
                    "owner_sub": "sandbox-host-owner",
                    "stripe_account_id": "acct_host",
                    "payout_method": "fiat_bank",
                    "eligibility_rule_version": "fiat-us/v1",
                }
            ],
            enrollments_after=[
                {
                    "tenant_id": "11111111-1111-1111-1111-111111111111",
                    "host_org_id": "22222222-2222-2222-2222-222222222222",
                    "attempt_id": ENROLLMENT_ATTEMPT,
                    "owner_sub": "sandbox-host-owner",
                    "stripe_account_id": "acct_host",
                    "payout_method": "fiat_bank",
                    "eligibility_rule_version": "fiat-us/v1",
                }
            ],
        ),
    }
    recovery = record("stripe-recovery", 8, short_balance=True, recovered=True)
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
        recorded_at="2026-10-12T07:59:13+00:00",
        **scope,
        transfer_id=transfer["id"],
        refund_id=refund["refund_id"],
        account_id=transfer["account_id"],
        code="balance_insufficient",
        error_type="InvalidRequestError",
        request_id="req_shortbalance",
        currency="usd",
        livemode=False,
        available_cents=0,
        amount_cents=20,
    )
    sync_bank_to_outcome(recovery)
    empty_reversals(recovery)
    transaction = {
        "status": "available",
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
    bind_money_dispatch(recovery)
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
            refusal_record(f"mismatch-{i}", i + 2, mismatch=True, amount=6000)
            for i in range(3)
        ],
    )
    failure = result["failed_bank_payout"]
    synthetic = record("ok-failed-bank", 8)
    failure_scope = {
        k: failure[k]
        for k in ("tenant_id", "host_org_id", "lease_id", "billing_record_id")
    }
    transfer = synthetic["connect_transfer"]
    transfer.update(id=failure["transfer_id"], metadata=failure_scope)
    failure["connect_transfer"] = transfer
    failure["readiness"] = synthetic["readiness"]
    failure["readiness"]["bank_id"] = failure["bank_id"]
    failure["transfer_inventory"] = synthetic["transfer_inventory"]
    failure["transfer_inventory"]["data"][0].update(
        id=transfer["id"], metadata=failure_scope
    )
    failure["created_at"] = synthetic["bank_payout"]["created_at"]
    failure["balance_transaction"] = synthetic["bank_payout"]["balance_transaction"]
    failure["bank_dispatch"]["recorded_at"] = "2026-10-12T07:59:35+00:00"
    funding = synthetic["bank_funding"]
    funding["closing_balance"].update(
        recorded_at="2026-10-12T07:59:50+00:00", available_cents=100
    )
    funding.update(
        recorded_at="2026-10-12T07:59:55+00:00",
        window_end_at=funding["closing_balance"]["recorded_at"],
    )
    funding["transactions"][1]["source"] = failure["payout_id"]
    funding["transactions"].append(
        {
            "id": failure["failure_balance_transaction"],
            "source": failure["payout_id"],
            "type": "payout_failure",
            "currency": "usd",
            "status": "available",
            "amount": 100,
            "fee": 0,
            "net": 100,
            "created_at": "2026-10-12T07:59:40+00:00",
            "available_at": "2026-10-12T07:59:42+00:00",
        }
    )
    failure["bank_funding"] = funding
    failure["payment"] = synthetic["payment"]
    failure["settlement"] = synthetic["settlement"]
    failure["settlement"].update(
        retained_cents=400,
        refund_due_cents=0,
        post_refund_host_payable_cents=100,
        post_refund_platform_compute_cents=300,
        reversal_due_cents=0,
        tax_allocation={
            "delivered_compute_cents": 400,
            "retained_tax_cents": 0,
            "refund_compute_cents": 0,
            "refund_tax_cents": 0,
            "refund_total_cents": 0,
            "allocation_policy": "EXAMPLE-compute-proportional-tax-v1",
        },
    )
    failure["source_charge_type"] = synthetic["source_charge_type"]
    failure["payment"].update(failure_scope)
    failure["payment"]["dispatch_checkpoint"].update(failure_scope)
    failure["payment"]["intent"]["metadata"].update(
        {k: failure[k] for k in ("tenant_id", "lease_id", "billing_record_id")}
    )
    failure["payment"]["capture_event"]["data"]["object"] = deepcopy(
        failure["payment"]["intent"]
    )
    failure["settlement"].update(failure_scope)
    journey = result["stripe_runs"][0]
    receipt = result["receipt"]
    receipt.update(
        {
            k: journey[k]
            for k in ("tenant_id", "host_org_id", "lease_id", "billing_record_id")
        }
    )
    receipt.update(
        journey_id=journey["id"],
        payment_intent_id=journey["payment"]["payment_intent_id"],
        charge_id=journey["payment"]["charge_id"],
        amount_cents=400,
        currency="usd",
        livemode=False,
        recorded_at=journey["recorded_at"],
    )
    receipt["throughput_map_version"] = "catalog-throughput/v1:" + "b" * 64
    receipt["frozen_lease"] = record(
        "receipt-frozen-lease",
        5,
        recorded_at="2026-10-12T04:55:30+00:00",
        source="hypermesh_lease",
        **{
            k: receipt[k]
            for k in (
                "tenant_id",
                "host_org_id",
                "lease_id",
                "billing_record_id",
                "ruleset_version",
                "throughput_map_version",
            )
        },
    )
    receipt["frozen_tax_calculation"] = record(
        "receipt-frozen-tax",
        5,
        recorded_at="2026-10-12T04:55:40+00:00",
        source="payment_tax_payable",
        revision_id=journey["payment"]["tax_revision_id"],
        calculation_id=journey["payment"]["tax_calculation_id"],
        policy_version=receipt["tax_version"],
        **{
            k: receipt[k]
            for k in (
                "tenant_id",
                "host_org_id",
                "lease_id",
                "billing_record_id",
                "payment_intent_id",
                "amount_cents",
            )
        },
    )
    receipt["owner_artifact"] = record(
        "receipt-artifact",
        5,
        recorded_at="2026-10-12T04:59:59+00:00",
        source="billing_receipt",
        **{
            k: v
            for k, v in receipt.items()
            if k
            not in (
                "id",
                "url",
                "revision",
                "recorded_at",
                "passed",
                "frozen_lease",
                "frozen_tax_calculation",
            )
        },
    )
    result["fiat_rollout"]["platform_account_id"] = "acct_platform"
    result["fiat_rollout"]["platform_payout_schedule"] = record(
        "platform-rollout",
        0,
        recorded_at="2026-10-12T00:20:00+00:00",
        account_id="acct_platform",
        livemode=False,
        source="stripe_platform_account",
        schedule_interval="manual",
    )
    failure["payout_readiness"] = synthetic["payout_readiness"]
    failure["payout_readiness"].update(failure_scope, bank_id=failure["bank_id"])
    failure["destination"] = failure["bank_id"]
    bind_bank_dispatch(failure, failure)
    repeated = result["repeated_mismatch"]
    repeated["policy_decision"] = record(
        "policy-repeat-decision",
        8,
        source="payment_mismatch_policy",
        actor_sub=repeated["actor_sub"],
        tenant_id=repeated["tenant_id"],
        host_org_id=repeated["host_org_id"],
        day="2026-10-12",
        mismatches=3,
        repeat_threshold=3,
        operator_required=True,
        reason="repeated_mismatch",
    )
    bind_policy_sequence(repeated)
    for row in [
        *result["gateway_runs"],
        *result["stripe_runs"],
        result["refund_recovery"],
        result["failed_bank_payout"],
    ]:
        bind_saved_deposit(row)
        bind_connect_dispatch(row)
        if "bank_funding" in row:
            bind_source_type(row, row.get("bank_payout", row))
    rollout = result["fiat_rollout"]
    rollout["started_at"] = "2026-10-12T00:10:00+00:00"
    for stage, observed in (
        ("enrollments_before", "00:05:00"),
        ("enrollments_after", "00:25:00"),
    ):
        rows = rollout[stage]
        rollout[stage] = record(
            stage,
            recorded_at=f"2026-10-12T{observed}+00:00",
            source="payment_connect_recipient_inventory",
            scope="environment",
            environment="test",
            platform_account_id="acct_platform",
            complete=True,
            has_more=False,
            filters={},
            total_count=len(rows),
            data=rows,
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
        ("bank_status", "validated"),
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


def test_verified_sandbox_bank_preserves_all_other_acceptance_guards(bundle):
    changed = 0

    def visit(value):
        nonlocal changed
        if isinstance(value, dict):
            if "bank_status" in value:
                value["bank_status"] = "verified"
                changed += 1
            for child in value.values():
                visit(child)
        elif isinstance(value, list):
            for child in value:
                visit(child)

    visit(bundle)
    assert changed > 0
    assert not check(bundle)
    bundle["gateway_runs"][0]["readiness"]["payouts_enabled"] = False
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
    bundle["fiat_rollout"]["enrollments_after"]["data"][0][key] = "replacement"
    assert check(bundle)


def test_non_stablecoin_source_requires_decoupling_prerequisite(bundle):
    bundle["stripe_runs"][0]["source_charge_type"] = "non_stablecoin"
    payment = bundle["stripe_runs"][0]["payment"]
    payment["intent"]["metadata"]["payment_rail"] = "stripe_checkout"
    payment["intent"]["metadata"]["payment_flow"] = "checkout"
    payment["tax_payable"]["expires_at"] = (
        int(datetime.fromisoformat(payment["checkout_created_at"]).timestamp())
        + 31 * 60
    )
    payment["charge"]["payment_method_details"] = {"type": "card", "card": {}}
    for candidate in [*bundle["gateway_runs"], *bundle["stripe_runs"]]:
        if candidate["payment"] is payment:
            candidate["connect_dispatch"]["source_recheck"]["charge"][
                "payment_method_details"
            ] = deepcopy(payment["charge"]["payment_method_details"])
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
    assert any("native lease adapter is not implemented" in e for e in check(bundle))


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
    bundle["fiat_rollout"]["enrollments_before"]["data"][0]["stripe_account_id"] = (
        "acct_other"
    )
    bundle["fiat_rollout"]["enrollments_after"]["data"][0]["stripe_account_id"] = (
        "acct_other"
    )
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
    bundle["fiat_rollout"]["enrollments_after"]["data"][0].update(
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
        2026, 12, 29, 12, tzinfo=UTC
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
    payment = bundle["gateway_runs"][0]["payment"]
    payment["intent"]["metadata"].update(
        payment_rail="stripe_checkout", payment_flow="checkout"
    )
    payment["tax_payable"]["expires_at"] = (
        int(datetime.fromisoformat(payment["checkout_created_at"]).timestamp())
        + 31 * 60
    )
    payment["charge"]["payment_method_details"] = {"type": "card", "card": {}}
    for candidate in [*bundle["gateway_runs"], *bundle["stripe_runs"]]:
        if candidate["payment"] is payment:
            candidate["connect_dispatch"]["source_recheck"]["charge"][
                "payment_method_details"
            ] = deepcopy(payment["charge"]["payment_method_details"])
    payment["capture_event"]["data"]["object"] = deepcopy(payment["intent"])
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
    assert any("native lease adapter is not implemented" in e for e in check(bundle))


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
    outcome["provider_refusal"] = loss_provider_error(outcome)
    sync_bank_to_outcome(bundle["stripe_runs"][0])
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
    row["settlement"].update(
        reversal_due_cents=0,
        post_refund_host_payable_cents=100,
        post_refund_platform_compute_cents=200,
    )
    sync_bank_to_outcome(row)
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
    row["transfer_inventory"]["data"][0]["source_transaction"] = row["payment"][
        "charge_id"
    ]
    bundle["receipt"]["charge_id"] = bundle["receipt"]["owner_artifact"][
        "charge_id"
    ] = row["payment"]["charge_id"]
    row["refund"]["refund_id"] = refund_prefix + "_supported"
    row["refund"]["transfer_outcome"]["refund_id"] = row["refund"]["refund_id"]
    row["payment"]["capture_event"]["data"]["object"] = deepcopy(
        row["payment"]["intent"]
    )
    row["refund"]["inventory"]["data"][0].update(
        id=row["refund"]["refund_id"], charge=row["payment"]["charge_id"]
    )
    bind_bank_dispatch(row, row["bank_payout"])
    bind_connect_dispatch(row)
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
    outcome["provider_refusal"] = loss_provider_error(outcome)
    sync_bank_to_outcome(bundle["stripe_runs"][0])
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
    outcome["provider_refusal"] = loss_provider_error(outcome)
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
    assert any(
        "distinct lease_id across gateway and Stripe journeys" in e for e in errors
    )
    assert any(
        "distinct billing_record_id across gateway and Stripe journeys" in e
        for e in errors
    )


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
    for candidate in [*bundle["gateway_runs"], *bundle["stripe_runs"]]:
        if candidate["payment"] is payment:
            candidate["connect_dispatch"]["source_recheck"]["charge"][
                "payment_method_details"
            ] = deepcopy(payment["charge"]["payment_method_details"])
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
    assert any(
        "platform-funded refund, recorded loss" in e or "reversal inventory" in e
        for e in check(bundle)
    )


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


@pytest.mark.parametrize("group", ["gateway_runs", "stripe_runs", "refund_recovery"])
def test_provider_duplicate_transfers_cannot_hide_outside_payout_window(bundle, group):
    run = bundle[group][0] if isinstance(bundle[group], list) else bundle[group]
    inventory = run["transfer_inventory"]
    duplicate = deepcopy(inventory["data"][0])
    duplicate["id"] = "tr_duplicate"
    inventory["data"].append(duplicate)
    assert any("exactly one provider transfer" in e for e in check(bundle))


@pytest.mark.parametrize(
    "key,value",
    [
        ("complete", False),
        ("has_more", True),
        ("destination", "acct_wrong"),
        ("data", []),
    ],
)
def test_transfer_inventory_must_cover_all_destination_pages(bundle, key, value):
    bundle["gateway_runs"][0]["transfer_inventory"][key] = value
    assert any("transfer" in e for e in check(bundle))


@pytest.mark.parametrize(
    "key,value",
    [
        ("amount_refunded", 400),
        ("amount_refunded", True),
        ("amount_refunded", None),
        ("refunded", True),
        ("refunded", None),
    ],
)
def test_pre_settlement_capture_requires_an_unrefunded_source(bundle, key, value):
    bundle["stripe_runs"][0]["payment"]["charge"][key] = value
    assert any("paid/captured succeeded charge" in e for e in check(bundle))


@pytest.mark.parametrize("buyer", [None, "0x" + "2" * 40, "invalid"])
def test_positive_capture_requires_the_frozen_verified_buyer(bundle, buyer):
    bundle["stripe_runs"][0]["payment"]["charge"]["payment_method_details"]["crypto"][
        "buyer_address"
    ] = buyer
    assert any("verified payer snapshot" in e for e in check(bundle))


@pytest.mark.parametrize(
    "field,value",
    [
        ("actor_sub", "other"),
        ("source", "self_declared"),
        ("tenant_id", "other"),
        ("address", "0x" + "2" * 40),
    ],
)
def test_payer_snapshot_is_server_owned_and_scope_bound(bundle, field, value):
    bundle["stripe_runs"][0]["payment"]["payer_snapshot"][field] = value
    assert any("verified payer snapshot" in e for e in check(bundle))


def test_buyer_wallet_normalization_is_case_insensitive(bundle):
    payment = bundle["stripe_runs"][0]["payment"]
    payment["payer_snapshot"]["address"] = "0x" + "aB" * 20
    payment["charge"]["payment_method_details"]["crypto"]["buyer_address"] = (
        "0x" + "Ab" * 20
    )
    bundle["stripe_runs"][0]["connect_dispatch"]["source_recheck"]["charge"][
        "payment_method_details"
    ] = deepcopy(payment["charge"]["payment_method_details"])
    bind_saved_deposit(bundle["stripe_runs"][0])
    assert check(bundle) == []


@pytest.mark.parametrize(
    "field,value",
    [
        ("schedule_interval", "daily"),
        ("host_standard_payouts", True),
        ("host_instant_payouts", True),
        ("host_schedule_editing", True),
        ("account_id", "acct_wrong"),
        ("host_probes", []),
    ],
)
def test_transfers_require_platform_controlled_manual_payouts(bundle, field, value):
    bundle["gateway_runs"][0]["readiness"]["payout_controls"][field] = value
    assert any("payout" in e for e in check(bundle))


@pytest.mark.parametrize(
    "field,value",
    [
        ("allowed", True),
        ("http_status", 200),
        ("actor_role", "platform_admin"),
        ("tenant_id", "other"),
        ("account_id", "acct_wrong"),
    ],
)
def test_host_refusal_probe_cannot_be_an_admin_or_unrelated_request(
    bundle, field, value
):
    bundle["stripe_runs"][0]["readiness"]["payout_controls"]["host_probes"][0][
        field
    ] = value
    assert any("owner-bound host refusal probes" in e for e in check(bundle))


def test_failed_payout_requires_its_own_funding_chain(bundle):
    del bundle["failed_bank_payout"]["bank_funding"]
    assert any("failed_bank_payout" in e for e in check(bundle))


@pytest.mark.parametrize(
    "field,value",
    [
        ("source", "po_other"),
        ("amount", 1),
        ("net", 1),
        ("type", "transfer"),
        ("id", "txn_unrelated"),
    ],
)
def test_failed_bank_return_must_match_the_original_debit(bundle, field, value):
    bundle["failed_bank_payout"]["bank_funding"]["transactions"][2][field] = value
    assert any("failed_bank_payout" in e for e in check(bundle))


def test_failed_payout_inventory_cannot_omit_debit(bundle):
    bundle["failed_bank_payout"]["bank_funding"]["transactions"].pop(1)
    assert any("failed_bank_payout" in e for e in check(bundle))


def test_failed_payout_cannot_hide_another_credit(bundle):
    funding = bundle["failed_bank_payout"]["bank_funding"]
    extra = deepcopy(funding["transactions"][0])
    extra["id"] = "txn_othercredit"
    funding["transactions"].append(extra)
    assert any("failed_bank_payout" in e for e in check(bundle))


def test_failed_payout_closing_balance_must_reconcile_preserved_earnings(bundle):
    bundle["failed_bank_payout"]["bank_funding"]["closing_balance"][
        "available_cents"
    ] = 1
    assert any("failed_bank_payout" in e for e in check(bundle))


def test_payer_snapshot_cannot_be_created_after_checkout(bundle):
    payment = bundle["stripe_runs"][0]["payment"]
    payment["payer_snapshot"]["recorded_at"] = payment["recorded_at"]
    assert any(
        "verified payer snapshot must precede checkout" in e for e in check(bundle)
    )


def test_controls_cannot_be_read_after_the_transfer(bundle):
    row = bundle["gateway_runs"][0]
    row["readiness"]["payout_controls"]["recorded_at"] = row["recorded_at"]
    assert any("payout controls must be observed" in e for e in check(bundle))


def test_returned_funds_cannot_predate_payout_debit(bundle):
    returned = bundle["failed_bank_payout"]["bank_funding"]["transactions"][2]
    returned["created_at"] = "2026-10-12T07:59:20+00:00"
    assert any("return must follow debit" in e for e in check(bundle))


def test_cleanup_cancellation_is_not_a_failed_deposit(bundle):
    row = bundle["failed_deposit"]
    row["intent"]["status"] = "canceled"
    row["intent"].pop("last_payment_error")
    row["provider_event"].update(
        type="payment_intent.canceled", data={"object": deepcopy(row["intent"])}
    )
    assert any("requires provider failed deposit attempt" in e for e in check(bundle))


def test_failed_deposit_requires_the_provider_error(bundle):
    row = bundle["failed_deposit"]
    row["intent"].pop("last_payment_error")
    row["provider_event"]["data"]["object"] = deepcopy(row["intent"])
    assert any("requires provider failed deposit attempt" in e for e in check(bundle))


@pytest.mark.parametrize(
    "field,value",
    [
        ("status", "pending"),
        ("amount_cents", 1),
        ("charge_id", "py_other"),
        ("payment_intent_id", "pi_other"),
        ("metadata", {}),
        ("billing_record_id", "other"),
    ],
)
def test_every_captured_mismatch_needs_full_bound_refund(bundle, field, value):
    bundle["repeated_mismatch"]["attempts"][0]["refund"][field] = value
    assert any("captured mismatch requires succeeded full" in e for e in check(bundle))


@pytest.mark.parametrize(
    "field,value",
    [
        ("status", "planned"),
        ("principal_refunded_cents", 390),
        ("host_payable_cents", 1),
        ("retained_fee_cost_cents", 0),
        ("owner_checkpoint", ""),
        ("refund_id", "pyr_other"),
    ],
)
def test_mismatch_final_accounting_cannot_remain_unresolved(bundle, field, value):
    bundle["repeated_mismatch"]["attempts"][1]["final_accounting"][field] = value
    assert any("mismatch accounting requires" in e for e in check(bundle))


def test_extra_mismatch_refund_cannot_be_hidden_by_metadata(bundle):
    inventory = bundle["repeated_mismatch"]["attempts"][0]["refund"]["inventory"]
    extra = deepcopy(inventory["data"][0])
    extra.update(id="pyr_extra", metadata={})
    inventory["data"].append(extra)
    assert any("additional mismatch refund" in e for e in check(bundle))


@pytest.mark.parametrize(
    "kind", ["charge_balance_transaction", "refund_balance_transaction"]
)
def test_mismatch_fee_accounting_uses_original_provider_transactions(bundle, kind):
    bundle["repeated_mismatch"]["attempts"][0]["final_accounting"][kind]["source"] = (
        "unrelated"
    )
    assert any("original-charge/refund provider balance" in e for e in check(bundle))


@pytest.mark.parametrize(
    "field,value",
    [
        ("journey_id", "unrelated"),
        ("lease_id", "unrelated"),
        ("billing_record_id", "unrelated"),
        ("payment_intent_id", "pi_other"),
        ("charge_id", "py_other"),
        ("amount_cents", 1),
    ],
)
def test_receipt_must_reference_an_accepted_capture(bundle, field, value):
    bundle["receipt"][field] = value
    assert any("receipt:" in e for e in check(bundle))


@pytest.mark.parametrize(
    "field,value",
    [
        ("tax_version", "other"),
        ("amount_cents", 1),
        ("billing_record_id", "other"),
        ("source", "self_declared"),
    ],
)
def test_receipt_requires_the_matching_owner_artifact(bundle, field, value):
    bundle["receipt"]["owner_artifact"][field] = value
    assert any("owner receipt artifact must bind" in e for e in check(bundle))


def test_receipt_artifact_cannot_predate_its_capture(bundle):
    bundle["receipt"]["owner_artifact"]["recorded_at"] = "2026-10-12T01:00:00+00:00"
    assert any(
        "owner receipt artifact must follow accepted capture" in e
        for e in check(bundle)
    )


def test_arbitrary_forbidden_subjects_do_not_prove_owner_payout_refusal(bundle):
    probes = bundle["gateway_runs"][0]["readiness"]["payout_controls"]["host_probes"]
    for i, probe in enumerate(probes):
        probe["actor_sub"] = "unauthorized-" + str(i)
    assert any("owner-bound host refusal probes" in e for e in check(bundle))


@pytest.mark.parametrize(
    "field,value",
    [
        ("subject", "other"),
        ("role", "member"),
        ("tenant_id", "other"),
        ("stripe_account_id", "acct_other"),
        ("enrollment_attempt_id", "other"),
    ],
)
def test_probe_ownership_snapshot_must_identify_enrolled_owner(bundle, field, value):
    bundle["gateway_runs"][0]["readiness"]["payout_controls"]["ownership_snapshot"][
        "subject_to_host"
    ][field] = value
    assert any("server-owned enrolled subject-to-host" in e for e in check(bundle))


@pytest.mark.parametrize(
    "field,value",
    [
        ("code", "other"),
        ("error_type", "ValueError"),
        ("amount_cents", 1),
        ("transfer_id", "tr_other"),
        ("request_id", None),
    ],
)
def test_regular_stripe_loss_requires_provider_refusal(bundle, field, value):
    row = bundle["stripe_runs"][0]
    outcome = row["refund"]["transfer_outcome"]
    outcome.pop("provider_reversal")
    outcome.pop("reversal_inventory")
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
    outcome["provider_refusal"] = loss_provider_error(outcome)
    sync_bank_to_outcome(bundle["stripe_runs"][0])
    assert check(bundle) == []
    outcome["provider_refusal"][field] = value
    assert any(
        "scoped provider balance_insufficient refusal" in e for e in check(bundle)
    )


def test_inactive_owner_does_not_establish_payout_control(bundle):
    bundle["gateway_runs"][0]["readiness"]["payout_controls"]["ownership_snapshot"][
        "subject_to_host"
    ]["active"] = False
    assert any("server-owned enrolled subject-to-host" in e for e in check(bundle))


def test_provider_error_request_cannot_be_reused_for_another_loss(bundle):
    outcomes = []
    for row in bundle["stripe_runs"][:2]:
        outcome = row["refund"]["transfer_outcome"]
        outcome.pop("provider_reversal")
        outcome.pop("reversal_inventory")
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
            loss_record="loss-" + row["id"],
        )
        outcome["provider_refusal"] = loss_provider_error(outcome)
        sync_bank_to_outcome(row)
        outcomes.append(outcome)
    assert check(bundle) == []
    outcomes[1]["provider_refusal"]["request_id"] = outcomes[0]["provider_refusal"][
        "request_id"
    ]
    assert any("provider error request reused" in e for e in check(bundle))


@pytest.mark.parametrize("amount", [None, 1, True])
def test_capture_requires_full_typed_provider_amount_captured(bundle, amount):
    bundle["stripe_runs"][0]["payment"]["charge"]["amount_captured"] = amount
    assert any("paid/captured succeeded charge" in e for e in check(bundle))


@pytest.mark.parametrize("value", [None, "0x" + "0" * 64, "bad", "0x12"])
def test_capture_requires_nonzero_provider_transaction_hash(bundle, value):
    bundle["stripe_runs"][0]["payment"]["charge"]["payment_method_details"]["crypto"][
        "transaction_hash"
    ] = value
    assert any("nonzero provider transaction hash" in e for e in check(bundle))


def test_simulation_marker_does_not_authorize_itself(bundle):
    from phase_b_capture_contract import SANDBOX_SUCCESS_MARKER

    bundle["stripe_runs"][0]["payment"]["charge"]["payment_method_details"]["crypto"][
        "transaction_hash"
    ] = SANDBOX_SUCCESS_MARKER
    assert any("owner-authorized sandbox simulation" in e for e in check(bundle))


@pytest.mark.parametrize(
    "key,value",
    [("schedule_interval", "daily"), ("account_id", "acct_host"), ("livemode", True)],
)
def test_platform_payout_schedule_is_separate_from_host_controls(bundle, key, value):
    bundle["fiat_rollout"]["platform_payout_schedule"][key] = value
    assert any("platform manual payout schedule" in e for e in check(bundle))


def test_platform_schedule_must_be_rechecked_before_transfer(bundle):
    bundle["gateway_runs"][0]["readiness"]["platform_payout_schedule"][
        "schedule_interval"
    ] = "daily"
    assert any("platform manual payout schedule" in e for e in check(bundle))


def standalone_fees(row, *, operator=False, zero=False):
    final = row["final_accounting"]
    final["charge_balance_transaction"].update(fee=0, net=row["amount_cents"])
    final["retained_fee_cost_cents"] = 0 if zero else 10
    at = datetime.fromisoformat(final["recorded_at"])
    report = record(
        "standalone-report-" + row["id"],
        recorded_at=(at - timedelta(seconds=2)).isoformat(),
        complete=True,
        incurred_by=row["charge"]["id"],
        basis="incurred_at",
        sha256="b" * 64,
        rows=[]
        if zero
        else [
            {
                "line_number": 1,
                "incurred_by": row["charge"]["id"],
                "currency": "usd",
                "amount_cents": 8,
                "tax_cents": 2,
            }
        ],
    )
    final["fee_reconciliation"] = record(
        "fees-" + row["id"],
        recorded_at=(at - timedelta(seconds=1)).isoformat(),
        **{
            k: row[k]
            for k in ("tenant_id", "host_org_id", "lease_id", "billing_record_id")
        },
        source="operator_reconciliation" if operator else "stripe_all_fees",
        status="reconciled",
        charge_id=row["charge"]["id"],
        refund_id=row["refund"]["refund_id"],
        owner_checkpoint=final["owner_checkpoint"],
        currency="usd",
        livemode=False,
        total_fee_cents=final["retained_fee_cost_cents"],
        report=report,
        operator_sub="test-operator" if operator else None,
        approved=operator,
        reason="Reviewed complete charge-attributed report" if operator else None,
    )
    if zero:
        final["fee_reconciliation"]["zero_fee_confirmation"] = record(
            "zero-confirmation-" + row["id"],
            recorded_at=(at - timedelta(seconds=2)).isoformat(),
            charge_id=row["charge"]["id"],
            source="stripe_support",
            zero_cost_confirmed=True,
            fee_cents=0,
            provider_reference="test-support-reference",
        )
    return final["fee_reconciliation"]


@pytest.mark.parametrize("operator,zero", [(False, False), (True, False), (True, True)])
def test_zero_inline_fee_accepts_bound_complete_standalone_or_operator_evidence(
    bundle, operator, zero
):
    standalone_fees(
        bundle["repeated_mismatch"]["attempts"][0], operator=operator, zero=zero
    )
    assert check(bundle) == []


@pytest.mark.parametrize(
    "field,value",
    [
        ("source", "estimate"),
        ("charge_id", "py_other"),
        ("total_fee_cents", 1),
        ("owner_checkpoint", "unrelated"),
    ],
)
def test_standalone_fee_evidence_is_bound_not_an_estimate(bundle, field, value):
    fees = standalone_fees(bundle["repeated_mismatch"]["attempts"][0])
    fees[field] = value
    assert any("standalone-fee or operator reconciliation" in e for e in check(bundle))


@pytest.mark.parametrize(
    "field,value",
    [("incurred_by", "py_other"), ("amount_cents", 1), ("tax_cents", True)],
)
def test_standalone_fee_rows_must_reconcile_the_original_charge(bundle, field, value):
    fees = standalone_fees(bundle["repeated_mismatch"]["attempts"][0])
    fees["report"]["rows"][0][field] = value
    assert check(bundle)


def test_zero_inline_fee_without_reconciliation_remains_unresolved(bundle):
    row = bundle["repeated_mismatch"]["attempts"][0]
    row["final_accounting"]["charge_balance_transaction"].update(fee=0, net=400)
    assert any("zero-inline fees require" in e for e in check(bundle))


def test_empty_fee_report_does_not_establish_zero_cost(bundle):
    fees = standalone_fees(
        bundle["repeated_mismatch"]["attempts"][0], operator=True, zero=True
    )
    fees.pop("zero_fee_confirmation")
    assert any("empty report is not zero-cost proof" in e for e in check(bundle))


def authorize_simulated_fixture(row):
    from phase_b_capture_contract import SANDBOX_SUCCESS_MARKER

    payment = row["payment"]
    at = datetime.fromisoformat(row["recorded_at"])
    binding = {
        "version": 1,
        "tenant_id": row["tenant_id"],
        "billing_record_id": row["billing_record_id"],
        "lease_id": row["lease_id"],
        "wallet": payment["payer_snapshot"]["address"],
        "amount_cents": payment["amount_cents"],
        "subtotal_cents": payment["subtotal_cents"],
        "tax_payable": deepcopy(payment["tax_payable"]),
        "return_url": "https://example.org/return",
        "actor_sub": payment["actor_sub"],
        "operation_id": "55555555-5555-5555-5555-555555555555",
        "intent_id": payment["payment_intent_id"],
        "attempted_at": (at - timedelta(seconds=250)).isoformat(),
    }
    payment["deposit_binding"]["binding"] = binding
    binding["simulation"] = {
        "version": 1,
        "tenant_id": row["tenant_id"],
        "billing_record_id": row["billing_record_id"],
        "lease_id": row["lease_id"],
        "actor_sub": payment["actor_sub"],
        "intent_id": payment["payment_intent_id"],
        "wallet": payment["payer_snapshot"]["address"],
        "deposit_operation_id": binding["operation_id"],
        "operation_id": "66666666-6666-6666-6666-666666666666",
        "attempted_at": (at - timedelta(seconds=210)).isoformat(),
        "authorized_from_status": "requires_action",
        "outcome": "success",
    }
    payment["simulation_authorization"] = record(
        "sim-authority",
        recorded_at=(at - timedelta(seconds=209)).isoformat(),
        deposit_binding=binding,
    )
    payment["charge"]["payment_method_details"]["crypto"]["transaction_hash"] = (
        SANDBOX_SUCCESS_MARKER
    )
    row["connect_dispatch"]["source_recheck"]["charge"]["payment_method_details"] = (
        deepcopy(payment["charge"]["payment_method_details"])
    )
    return binding


def test_owner_authorized_simulation_marker_can_certify_sandbox_capture(bundle):

    row = bundle["stripe_runs"][0]
    binding = authorize_simulated_fixture(row)
    assert check(bundle) == []
    binding["simulation"]["billing_record_id"] = "wrong"
    assert any("owner-authorized sandbox simulation" in e for e in check(bundle))


@pytest.mark.parametrize(
    "field,value",
    [("lease_id", "other"), ("payment_flow", "checkout"), ("extra", "unexpected")],
)
def test_capture_metadata_matches_the_complete_frozen_lease_plan(bundle, field, value):
    payment = bundle["stripe_runs"][0]["payment"]
    payment["intent"]["metadata"][field] = value
    payment["capture_event"]["data"]["object"] = deepcopy(payment["intent"])
    assert any("full frozen lease/flow/tax metadata" in e for e in check(bundle))


@pytest.mark.parametrize(
    "field", ["object", "amount_captured", "amount_refunded", "refunded", "created"]
)
def test_mismatch_capture_is_authoritative_before_quarantine(bundle, field):
    bundle["repeated_mismatch"]["attempts"][0]["charge"].pop(field)
    assert any("paid/captured succeeded charge" in e for e in check(bundle))


def test_cross_day_mismatches_do_not_establish_repetition(bundle):
    row = bundle["repeated_mismatch"]["attempts"][1]
    row["policy_observation"]["day"] = "2026-10-13"
    assert any("same UTC policy day" in e for e in check(bundle))


def test_replayed_server_counter_does_not_increment_repeat_threshold(bundle):
    observation = bundle["repeated_mismatch"]["attempts"][1]["policy_observation"]
    observation.update(counter_before=0, counter_after=1)
    assert any("same UTC policy day" in e for e in check(bundle))


@pytest.mark.parametrize(
    "field,value",
    [
        ("source", "self_declared"),
        ("actor_sub", "other"),
        ("mismatches", 1),
        ("operator_required", False),
        ("repeat_threshold", 2),
    ],
)
def test_repeat_routing_requires_server_owned_decision(bundle, field, value):
    bundle["repeated_mismatch"]["policy_decision"][field] = value
    assert any("authoritative same-day actor counter" in e for e in check(bundle))


@pytest.mark.parametrize(
    "field,value",
    [
        ("payouts_enabled", False),
        ("bank_id", "ba_changed"),
        ("payout_requirements_clear", False),
        ("ownership_verified", False),
        ("billing_record_id", "unrelated"),
    ],
)
def test_payout_time_destination_and_readiness_must_still_match(bundle, field, value):
    bundle["gateway_runs"][0]["payout_readiness"][field] = value
    assert any("payout" in e for e in check(bundle))


def test_bank_readiness_cannot_be_reused_only_from_before_transfer(bundle):
    row = bundle["gateway_runs"][0]
    row["payout_readiness"]["recorded_at"] = row["readiness"]["recorded_at"]
    assert any("payout-time read must follow transfer" in e for e in check(bundle))


def test_waiting_for_funds_requires_another_bank_read_before_payout(bundle):
    row = bundle["gateway_runs"][0]
    row["bank_payout"]["created_at"] = (
        datetime.fromisoformat(row["payout_readiness"]["recorded_at"])
        + timedelta(minutes=6)
    ).isoformat()
    row["bank_dispatch_checkpoint"]["attempted_at"] = row["bank_payout"]["created_at"]
    row["bank_dispatch_attempts"]["data"][0]["started_at"] = row["bank_payout"][
        "created_at"
    ]
    assert any("fresh bank readiness required" in e for e in check(bundle))


def test_failed_bank_dispatch_also_requires_payout_time_readiness(bundle):
    bundle["failed_bank_payout"].pop("payout_readiness")
    assert any("payout_readiness" in e for e in check(bundle))


@pytest.mark.parametrize("failed", [False, True])
def test_provider_payout_destination_must_be_the_frozen_bank(bundle, failed):
    payout = (
        bundle["failed_bank_payout"]
        if failed
        else bundle["gateway_runs"][0]["bank_payout"]
    )
    payout["destination"] = "ba_changed"
    assert check(bundle)


@pytest.mark.parametrize("group", ["gateway_runs", "stripe_runs", "failed_bank_payout"])
@pytest.mark.parametrize(
    "mutation",
    ["missing_source", "other_charge", "missing_payment", "wrong_scope", "uncaptured"],
)
def test_every_bank_funding_path_requires_authoritative_source_capture(
    bundle, group, mutation
):
    row = bundle[group] if group == "failed_bank_payout" else bundle[group][0]
    if mutation == "missing_source":
        row["connect_transfer"].pop("source_transaction")
        row["transfer_inventory"]["data"][0].pop("source_transaction")
    elif mutation == "other_charge":
        row["connect_transfer"]["source_transaction"] = "py_other"
        row["transfer_inventory"]["data"][0]["source_transaction"] = "py_other"
    elif mutation == "missing_payment":
        row.pop("payment")
    elif mutation == "wrong_scope":
        row["payment"]["lease_id"] = "other"
    else:
        row["payment"]["charge"]["captured"] = False
    assert any(
        ("source_transaction" in e or "captured" in e or "payment" in e)
        for e in check(bundle)
    )


@pytest.mark.parametrize(
    "mutation",
    [
        "full_original_payout",
        "missing_debit",
        "wrong_debit",
        "after_bank",
        "other_source",
    ],
)
def test_reversal_reduces_bank_entitlement_and_must_balance_before_dispatch(
    bundle, mutation
):
    row = bundle["stripe_runs"][0]
    txs = row["bank_funding"]["transactions"]
    if mutation == "full_original_payout":
        row["bank_payout"]["amount_cents"] = 100
        txs[1].update(amount=-100, net=-100)
    elif mutation == "missing_debit":
        txs.pop()
    elif mutation == "wrong_debit":
        txs[-1].update(amount=-1, net=-1)
    elif mutation == "after_bank":
        txs[-1]["created_at"] = row["recorded_at"]
    else:
        txs[-1]["source"] = "pyr_other"
    assert any(
        "funding" in e or "reversal" in e or "bank payout" in e for e in check(bundle)
    )


@pytest.mark.parametrize(
    "case",
    [
        "wrong_lease",
        "wrong_flow",
        "extra_meta",
        "wrong_object",
        "missing_mode",
        "wrong_mode",
        "on_behalf_of",
        "transfer_data",
        "application_fee_amount",
    ],
)
def test_failed_deposit_cannot_use_an_intent_runtime_would_refuse(bundle, case):
    row = bundle["failed_deposit"]
    intent = row["intent"]
    if case == "wrong_lease":
        intent["metadata"]["lease_id"] = "other"
    elif case == "wrong_flow":
        intent["metadata"]["payment_flow"] = "checkout"
    elif case == "extra_meta":
        intent["metadata"]["extra"] = "unfrozen"
    elif case == "wrong_object":
        intent["object"] = "setup_intent"
    elif case == "missing_mode":
        intent.pop("payment_method_options")
    elif case == "wrong_mode":
        intent["payment_method_options"]["crypto"]["mode"] = "payment"
    else:
        intent[case] = 0 if case == "application_fee_amount" else "acct_other"
    row["provider_event"]["data"]["object"] = deepcopy(intent)
    assert any("frozen" in e for e in check(bundle))


@pytest.mark.parametrize(
    "revision,calculation",
    [
        ("bad", "taxcalc_valid"),
        (None, "taxcalc_valid"),
        ("88888888-8888-8888-8888-888888888888", None),
        ("88888888-8888-8888-8888-888888888888", "bad"),
    ],
)
def test_matching_tax_metadata_does_not_validate_an_invalid_order(
    bundle, revision, calculation
):
    row = bundle["stripe_runs"][0]["payment"]
    row.update(tax_revision_id=revision, tax_calculation_id=calculation)
    row["intent"]["metadata"].update(
        tax_revision_id=revision, tax_calculation_id=calculation
    )
    row["capture_event"]["data"]["object"] = deepcopy(row["intent"])
    assert any("invalid frozen deposit tax binding" in e for e in check(bundle))


def test_api_key_actor_cannot_establish_person_repeat_policy(bundle):
    repeated = bundle["repeated_mismatch"]
    repeated["actor_sub"] = repeated["policy_decision"]["actor_sub"] = (
        "apikey:automation"
    )
    for row in repeated["attempts"]:
        row["actor_sub"] = row["expected_payer"]["actor_sub"] = row[
            "policy_observation"
        ]["actor_sub"] = "apikey:automation"
    assert any("actor/scope-bound mismatch counter" in e for e in check(bundle))


@pytest.mark.parametrize("artifact", ["frozen_lease", "frozen_tax_calculation"])
@pytest.mark.parametrize("mutation", ["missing", "scope", "source", "version", "late"])
def test_receipt_versions_require_frozen_owner_provenance(bundle, artifact, mutation):
    receipt = bundle["receipt"]
    if mutation == "missing":
        receipt.pop(artifact)
    elif mutation == "scope":
        receipt[artifact]["lease_id"] = "other"
    elif mutation == "source":
        receipt[artifact]["source"] = "current_configuration"
    elif mutation == "late":
        receipt[artifact]["recorded_at"] = receipt["recorded_at"]
    else:
        receipt[artifact][
            "ruleset_version" if artifact == "frozen_lease" else "policy_version"
        ] = "other"
    assert any("receipt:" in e for e in check(bundle))


@pytest.mark.parametrize(
    "field", ["tax_version", "ruleset_version", "throughput_map_version"]
)
@pytest.mark.parametrize(
    "value",
    [
        "unknown",
        "unset-policy",
        "legacy-placeholder",
        "current",
        "unstamped",
        "unversioned",
        "latest",
        "n/a",
    ],
)
def test_copied_placeholder_receipt_versions_are_not_provenance(bundle, field, value):
    receipt = bundle["receipt"]
    receipt[field] = receipt["owner_artifact"][field] = value
    artifact = (
        receipt["frozen_tax_calculation"]
        if field == "tax_version"
        else receipt["frozen_lease"]
    )
    artifact["policy_version" if field == "tax_version" else field] = value
    assert any("placeholder/non-frozen version" in e for e in check(bundle))


@pytest.mark.parametrize(
    "field,value",
    [
        ("tenant_id", "other"),
        ("host_org_id", "other"),
        ("lease_id", "other"),
        ("billing_record_id", "other"),
        ("currency", "eur"),
        ("host_payable_cents", 1),
        ("host_payable_cents", True),
        ("recorded_at", "2026-10-13T08:00:00+00:00"),
    ],
)
def test_failed_bank_requires_authoritative_settlement_before_dispatch(
    bundle, field, value
):
    bundle["failed_bank_payout"]["settlement"][field] = value
    assert any(
        "settled host payable" in e or "settlement must precede" in e
        for e in check(bundle)
    )


@pytest.mark.parametrize(
    "value",
    [
        "garbage-but-not-placeholder",
        "EXAMPLE-",
        "EXAMPLE-" + "a" * 65,
        "example-v1",
        "EXAMPLE-invalid.space",
    ],
)
def test_tax_receipt_uses_native_persisted_policy_format(bundle, value):
    receipt = bundle["receipt"]
    receipt["tax_version"] = receipt["owner_artifact"]["tax_version"] = receipt[
        "frozen_tax_calculation"
    ]["policy_version"] = value
    assert any("missing tax_version" in e for e in check(bundle))


@pytest.mark.parametrize(
    "field",
    [
        "version",
        "tenant_id",
        "billing_record_id",
        "lease_id",
        "wallet",
        "subtotal_cents",
        "amount_cents",
        "tax_payable",
        "return_url",
    ],
)
def test_simulated_capture_requires_the_complete_frozen_owner_order(bundle, field):
    row = bundle["stripe_runs"][0]
    binding = authorize_simulated_fixture(row)
    assert check(bundle) == []
    binding.pop(field)
    assert any("owner-authorized sandbox simulation" in e for e in check(bundle))


@pytest.mark.parametrize(
    "field,value",
    [
        ("version", 2),
        ("tenant_id", "other"),
        ("billing_record_id", "other"),
        ("lease_id", "other"),
        ("wallet", "0x" + "2" * 40),
        ("subtotal_cents", 1),
        ("amount_cents", 1),
        ("tax_payable", None),
        ("return_url", "http://example.org/return"),
    ],
)
def test_simulation_outer_binding_must_match_authoritative_payment(
    bundle, field, value
):
    row = bundle["stripe_runs"][0]
    binding = authorize_simulated_fixture(row)
    binding[field] = value
    assert any("owner-authorized sandbox simulation" in e for e in check(bundle))


@pytest.mark.parametrize(
    "field", ["charge_balance_transaction", "refund_balance_transaction"]
)
@pytest.mark.parametrize("status", [None, "failed", "succeeded", "", True])
def test_mismatch_fee_snapshots_require_native_provider_status(bundle, field, status):
    tx = bundle["repeated_mismatch"]["attempts"][0]["final_accounting"][field]
    if status is None:
        tx.pop("status")
    else:
        tx["status"] = status
    assert any("provider balance transaction accounting" in e for e in check(bundle))


@pytest.mark.parametrize(
    "field", ["charge_balance_transaction", "refund_balance_transaction"]
)
@pytest.mark.parametrize("status", ["pending", "available"])
def test_mismatch_fee_native_pending_or_available_statuses_pass(bundle, field, status):
    bundle["repeated_mismatch"]["attempts"][0]["final_accounting"][field]["status"] = (
        status
    )
    assert check(bundle) == []


@pytest.mark.parametrize(
    "field", ["charge_balance_transaction", "refund_balance_transaction"]
)
def test_mismatch_fee_exchange_conversion_cannot_certify_usd_cost(bundle, field):
    bundle["repeated_mismatch"]["attempts"][0]["final_accounting"][field][
        "exchange_rate"
    ] = 1.0
    assert any("provider balance transaction accounting" in e for e in check(bundle))


def bank_case(bundle, group):
    return bundle[group] if group == "failed_bank_payout" else bundle[group][0]


@pytest.mark.parametrize("group", ["gateway_runs", "stripe_runs", "failed_bank_payout"])
@pytest.mark.parametrize(
    "mutation",
    [
        "missing_plan",
        "missing_checkpoint",
        "scope",
        "amount",
        "bank",
        "operation",
        "key",
        "checkpoint_key",
        "checkpoint_scope",
        "checkpoint_payout",
        "late_plan",
        "late_checkpoint",
    ],
)
def test_bank_payout_requires_its_pre_io_owner_plan_and_dispatch_checkpoint(
    bundle, group, mutation
):
    row = bank_case(bundle, group)
    plan, checkpoint = row["bank_dispatch_plan"], row["bank_dispatch_checkpoint"]
    if mutation == "missing_plan":
        row.pop("bank_dispatch_plan")
    elif mutation == "missing_checkpoint":
        row.pop("bank_dispatch_checkpoint")
    elif mutation == "scope":
        plan["billing_record_id"] = "other"
    elif mutation == "amount":
        plan["amount_cents"] = 1
    elif mutation == "bank":
        plan["bank_id"] = "ba_other"
    elif mutation == "operation":
        plan["operation_id"] = "invalid"
    elif mutation == "key":
        plan.pop("idempotency_key")
    elif mutation == "checkpoint_key":
        checkpoint["idempotency_key"] = "another-key"
    elif mutation == "checkpoint_scope":
        checkpoint["lease_id"] = "other"
    elif mutation == "checkpoint_payout":
        checkpoint["payout_id"] = "po_other"
    elif mutation == "late_plan":
        plan["recorded_at"] = row["recorded_at"]
    elif mutation == "late_checkpoint":
        checkpoint["recorded_at"] = "2026-10-13T08:00:00+00:00"
    assert any(
        "bank plan" in e or "bank plan/key" in e or "bank plan must persist" in e
        for e in check(bundle)
    )


@pytest.mark.parametrize("group", ["gateway_runs", "stripe_runs", "failed_bank_payout"])
@pytest.mark.parametrize(
    "mutation",
    [
        "missing_request",
        "request_key",
        "request_id",
        "request_account",
        "request_amount",
        "request_bank",
        "request_metadata",
        "payout_metadata",
        "request_time",
    ],
)
def test_bank_dispatch_must_match_native_provider_request_identity(
    bundle, group, mutation
):
    row = bank_case(bundle, group)
    payout = row if group == "failed_bank_payout" else row["bank_payout"]
    checkpoint = row["bank_dispatch_checkpoint"]
    request = checkpoint["provider_request"]
    if mutation == "missing_request":
        checkpoint.pop("provider_request")
    elif mutation == "request_key":
        request["idempotency_key"] = "different-key"
    elif mutation == "request_id":
        request.pop("request_id")
    elif mutation == "request_account":
        request["account_id"] = "acct_other"
    elif mutation == "request_amount":
        request["parameters"]["amount"] = 1
    elif mutation == "request_bank":
        request["parameters"]["destination"] = "ba_other"
    elif mutation == "request_metadata":
        request["parameters"]["metadata"]["plan_id"] = "other"
    elif mutation == "payout_metadata":
        payout["metadata"] = {}
    elif mutation == "request_time":
        request["recorded_at"] = row["started_at"]
    assert any(
        "provider request" in e or "bank plan must persist" in e for e in check(bundle)
    )


@pytest.mark.parametrize("field", ["plan_id", "operation_id", "idempotency_key"])
def test_bank_retry_authority_cannot_be_reused_for_another_payout(bundle, field):
    first, second = bundle["stripe_runs"][:2]
    value = first["bank_dispatch_plan"][field]
    second["bank_dispatch_plan"][field] = second["bank_dispatch_checkpoint"][field] = (
        value
    )
    request = second["bank_dispatch_checkpoint"]["provider_request"]
    if field == "idempotency_key":
        request[field] = value
    else:
        request["parameters"]["metadata"][field] = value
        second["bank_payout"]["metadata"][field] = value
    assert any("bank plan/operation/key reused" in e for e in check(bundle))


def test_legacy_missing_policy_backfills_only_fixed_native_migration_defaults(bundle):
    before = bundle["fiat_rollout"]["enrollments_before"]["data"][0]
    before.pop("payout_method")
    before.pop("eligibility_rule_version")
    assert check(bundle) == []
    bundle["fiat_rollout"]["enrollments_after"]["data"][0].update(
        payout_method="usdc", eligibility_rule_version="usdc-stablecoin/v1"
    )
    assert any("history was lost or replaced" in e for e in check(bundle))


def add_historical_usdc_enrollment(bundle):
    row = {
        "tenant_id": "11111111-1111-1111-1111-111111111111",
        "host_org_id": "99999999-9999-9999-9999-999999999999",
        "attempt_id": "historical-usdc",
        "owner_sub": "historical-host-owner",
        "stripe_account_id": "acct_historicalusdc",
        "payout_method": "usdc",
        "eligibility_rule_version": "usdc-stablecoin/v1",
    }
    for stage in ("enrollments_before", "enrollments_after"):
        bundle["fiat_rollout"][stage]["data"].append(deepcopy(row))
        bundle["fiat_rollout"][stage]["total_count"] += 1


def test_existing_usdc_history_survives_fiat_default_rollout(bundle):
    add_historical_usdc_enrollment(bundle)
    assert check(bundle) == []
    bundle["fiat_rollout"]["enrollments_after"]["data"][-1].update(
        payout_method="fiat_bank", eligibility_rule_version="fiat-us/v1"
    )
    assert any("history was lost or replaced" in e for e in check(bundle))


@pytest.mark.parametrize("stage", ["enrollments_before", "enrollments_after"])
@pytest.mark.parametrize(
    "mutation",
    [
        "missing_method",
        "missing_rule",
        "null_method",
        "null_rule",
        "unknown_method",
        "unknown_rule",
        "wrong_pair",
    ],
)
def test_rollout_requires_complete_native_method_and_admission_policy(
    bundle, stage, mutation
):
    row = bundle["fiat_rollout"][stage]["data"][0]
    if mutation == "missing_method":
        row.pop("payout_method")
    elif mutation == "missing_rule":
        row.pop("eligibility_rule_version")
    elif mutation == "null_method":
        row["payout_method"] = None
    elif mutation == "null_rule":
        row["eligibility_rule_version"] = None
    elif mutation == "unknown_method":
        row["payout_method"] = "other"
    elif mutation == "unknown_rule":
        row["eligibility_rule_version"] = "other/v1"
    else:
        row["eligibility_rule_version"] = "usdc-stablecoin/v1"
    assert any("persisted payout method/rule" in e for e in check(bundle))


@pytest.mark.parametrize(
    "group", ["gateway_runs", "stripe_runs", "failed_bank_payout", "refund_recovery"]
)
@pytest.mark.parametrize(
    "mutation",
    [
        "expired",
        "expires_at_checkout",
        "missing_expiry",
        "boolean_expiry",
        "wrong_total",
        "wrong_revision",
        "wrong_calculation",
        "missing_snapshot",
    ],
)
def test_every_stablecoin_capture_requires_its_valid_live_at_creation_payable(
    bundle, group, mutation
):
    row = (
        bank_case(bundle, group) if group not in ("refund_recovery",) else bundle[group]
    )
    paid = row["payment"]
    payable = paid["tax_payable"]
    if mutation == "expired":
        payable["expires_at"] = 1
    elif mutation == "expires_at_checkout":
        payable["expires_at"] = int(
            datetime.fromisoformat(paid["checkout_created_at"]).timestamp()
        )
    elif mutation == "missing_expiry":
        payable.pop("expires_at")
    elif mutation == "boolean_expiry":
        payable["expires_at"] = True
    elif mutation == "wrong_total":
        payable["total_cents"] = 1
    elif mutation == "wrong_revision":
        payable["revision_id"] = "other"
    elif mutation == "wrong_calculation":
        payable["calculation_id"] = "taxcalc_other"
    else:
        paid.pop("tax_payable")
    assert any("tax payable" in e for e in check(bundle))


def test_valid_checkout_quote_can_expire_before_capture_observation(bundle):
    paid = bundle["stripe_runs"][0]["payment"]
    checkout = datetime.fromisoformat(paid["checkout_created_at"])
    paid["tax_payable"]["expires_at"] = int(
        (checkout + timedelta(seconds=1)).timestamp()
    )
    bind_saved_deposit(bundle["stripe_runs"][0])
    assert check(bundle) == []


@pytest.mark.parametrize(
    "group", ["gateway_runs", "stripe_runs", "failed_bank_payout", "refund_recovery"]
)
def test_zero_wallet_cannot_be_the_verified_payer_of_a_successful_capture(
    bundle, group
):
    row = bank_case(bundle, group) if group != "refund_recovery" else bundle[group]
    paid = row["payment"]
    paid["payer_snapshot"]["address"] = paid["charge"]["payment_method_details"][
        "crypto"
    ]["buyer_address"] = "0x" + "0" * 40
    assert any("verified payer snapshot" in e for e in check(bundle))


@pytest.mark.parametrize("status", [None, "failed", "succeeded", True])
def test_platform_loss_refund_debit_requires_native_transaction_status(bundle, status):
    tx = bundle["refund_recovery"]["final_accounting"]["refund_balance_transaction"]
    if status is None:
        tx.pop("status")
    else:
        tx["status"] = status
    assert any("platform balance debit" in e for e in check(bundle))


@pytest.mark.parametrize("status", ["pending", "available"])
def test_recovery_refund_native_statuses_remain_valid(bundle, status):
    tx = bundle["refund_recovery"]["final_accounting"]["refund_balance_transaction"]
    tx["status"] = status
    assert check(bundle) == []
    tx["exchange_rate"] = 1.0
    assert any("platform balance debit" in e for e in check(bundle))


@pytest.mark.parametrize(
    "group",
    [
        "gateway_runs",
        "stripe_runs",
        "failed_bank_payout",
        "refund_recovery",
        "repeated_mismatch",
    ],
)
@pytest.mark.parametrize("when", ["ancient", "before_checkout", "after_event"])
def test_native_charge_time_binds_the_fresh_checkout_and_event(bundle, group, when):
    if group == "repeated_mismatch":
        row = bundle[group]["attempts"][0]
        charge = row["charge"]
        checkout = datetime.fromisoformat(row["started_at"])
        event = datetime.fromisoformat(row["provider_event"]["created_at"])
    else:
        row = bank_case(bundle, group) if group != "refund_recovery" else bundle[group]
        charge = row["payment"]["charge"]
        checkout = datetime.fromisoformat(row["payment"]["checkout_created_at"])
        event = datetime.fromisoformat(row["payment"]["capture_event"]["created_at"])
    charge["created"] = (
        1
        if when == "ancient"
        else int(
            (
                checkout - timedelta(seconds=1)
                if when == "before_checkout"
                else event + timedelta(seconds=1)
            ).timestamp()
        )
    )
    assert any("native charge creation" in e for e in check(bundle))


@pytest.mark.parametrize("group", ["stripe_runs", "refund_recovery"])
@pytest.mark.parametrize(
    "when", [None, "1970-01-01T00:00:01+00:00", "2026-10-12T08:01:00+00:00"]
)
def test_refund_inventory_creation_cannot_disagree_with_selected_provider_refund(
    bundle, group, when
):
    row = bundle[group] if group == "refund_recovery" else bundle[group][0]
    refund = row["refund"]["inventory"]["data"][0]
    if when is None:
        refund.pop("created_at")
    else:
        refund["created_at"] = when
    assert any("exactly one provider refund matching" in e for e in check(bundle))


def test_bank_monitoring_covers_entire_fifth_day_for_latest_us_zone(bundle):
    anchor = datetime(2026, 10, 12, 23, 59, tzinfo=UTC)
    cutoff = checker.payout_observation_deadline(anchor)
    assert cutoff == datetime(2026, 10, 20, 12, tzinfo=UTC)
    row = bundle["stripe_runs"][0]
    cutoff = checker.payout_observation_deadline(
        max(
            datetime.fromisoformat(row["bank_payout"]["arrival_at"]),
            datetime.fromisoformat(row["bank_payout"]["recorded_at"]),
        )
    )
    follow = row["bank_followup"]
    follow["recorded_at"] = follow["events_through_at"] = (
        cutoff - timedelta(microseconds=1)
    ).isoformat()
    assert any("five banking days" in e for e in check(bundle))
    follow["recorded_at"] = follow["events_through_at"] = cutoff.isoformat()
    assert check(bundle) == []


@pytest.mark.parametrize(
    "mutation",
    [
        "missing",
        "incomplete",
        "pagination",
        "wrong_transfer",
        "before_outcome",
        "successful_reversal",
        "after_completion",
    ],
)
def test_platform_loss_requires_complete_post_outcome_empty_reversal_inventory(
    bundle, mutation
):
    row = bundle["refund_recovery"]
    outcome = row["refund"]["transfer_outcome"]
    inventory = outcome["reversal_inventory"]
    if mutation == "missing":
        outcome.pop("reversal_inventory")
    elif mutation == "incomplete":
        inventory["complete"] = False
    elif mutation == "pagination":
        inventory["has_more"] = True
    elif mutation == "wrong_transfer":
        inventory["transfer_id"] = "tr_other"
    elif mutation == "before_outcome":
        inventory["recorded_at"] = row["started_at"]
    elif mutation == "after_completion":
        inventory["recorded_at"] = "2026-10-12T09:00:00+00:00"
    else:
        inventory["data"] = [
            deepcopy(
                bundle["stripe_runs"][0]["refund"]["transfer_outcome"][
                    "provider_reversal"
                ]
            )
        ]
    assert any(
        "reversal inventory" in e or "no successful provider reversal" in e
        for e in check(bundle)
    )


@pytest.mark.parametrize("which", ["receipt", "payment"])
def test_receipt_tax_policy_is_the_same_frozen_quote_that_funded_capture(bundle, which):
    paid, receipt = bundle["stripe_runs"][0]["payment"], bundle["receipt"]
    if which == "receipt":
        receipt["tax_version"] = receipt["owner_artifact"]["tax_version"] = receipt[
            "frozen_tax_calculation"
        ]["policy_version"] = "EXAMPLE-different-valid-policy"
    else:
        paid["tax_payable"]["policy_version"] = "EXAMPLE-different-valid-policy"
    assert any("original frozen payable policy" in e for e in check(bundle))


@pytest.mark.parametrize(
    "revision,calculation",
    [
        (None, None),
        (None, "taxcalc_present"),
        ("88888888-8888-8888-8888-888888888888", None),
        ("invalid", "taxcalc_present"),
        ("88888888-8888-8888-8888-888888888888", "invalid"),
    ],
)
def test_non_null_payable_cannot_claim_an_untaxed_or_invalid_deposit_order(
    bundle, revision, calculation
):
    paid = bundle["gateway_runs"][0]["payment"]
    paid.update(tax_revision_id=revision, tax_calculation_id=calculation)
    paid["tax_payable"].update(revision_id=revision, calculation_id=calculation)
    metadata = paid["intent"]["metadata"]
    for field in ("tax_revision_id", "tax_calculation_id"):
        metadata.pop(field)
    if revision is not None or calculation is not None:
        metadata.update(tax_revision_id=revision, tax_calculation_id=calculation)
    paid["capture_event"]["data"]["object"] = deepcopy(paid["intent"])
    assert any("non-null frozen tax payable" in e for e in check(bundle))


@pytest.mark.parametrize("amount", [400, 401, 500])
def test_fully_reconciled_gateway_cannot_transfer_more_than_original_capture(
    bundle, amount
):
    row = bundle["gateway_runs"][0]
    row["settlement"]["host_payable_cents"] = row["connect_transfer"][
        "amount_cents"
    ] = row["bank_payout"]["amount_cents"] = amount
    row["settlement"]["platform_compute_cents"] = 400 - amount
    row["settlement"].update(
        retained_cents=400,
        refund_due_cents=0,
        post_refund_host_payable_cents=amount,
        post_refund_platform_compute_cents=400 - amount,
        reversal_due_cents=0,
        tax_allocation={
            "delivered_compute_cents": 400,
            "retained_tax_cents": 0,
            "refund_compute_cents": 0,
            "refund_tax_cents": 0,
            "refund_total_cents": 0,
            "allocation_policy": "EXAMPLE-compute-proportional-tax-v1",
        },
    )
    row["transfer_inventory"]["data"][0]["amount"] = amount
    row["bank_funding"]["transactions"][0].update(amount=amount, net=amount)
    row["bank_funding"]["transactions"][1].update(amount=-amount, net=-amount)
    bind_bank_dispatch(row, row["bank_payout"])
    bind_connect_dispatch(row)
    if amount <= 400:
        assert check(bundle) == []
    else:
        assert any(
            "cannot exceed the authoritative captured charge" in e
            for e in check(bundle)
        )


@pytest.mark.parametrize("delta", [-1, 0, 1])
def test_next_mismatch_checkout_respects_prior_persisted_cooldown_boundary(
    bundle, delta
):
    first, second = bundle["repeated_mismatch"]["attempts"][:2]
    previous = datetime.fromisoformat(first["policy_observation"]["recorded_at"])
    start = previous + timedelta(minutes=15, microseconds=delta)
    second["started_at"] = start.isoformat()
    second["expected_payer"]["recorded_at"] = (start - timedelta(minutes=1)).isoformat()
    if delta < 0:
        assert any("15-minute mismatch cooldown" in e for e in check(bundle))
    else:
        assert check(bundle) == []


@pytest.mark.parametrize("age", [timedelta(hours=23), timedelta(days=10000)])
def test_simulation_marker_rejects_creation_attempt_expired_at_original_checkout(
    bundle, age
):
    row = bundle["stripe_runs"][0]
    binding = authorize_simulated_fixture(row)
    checkout = datetime.fromisoformat(row["payment"]["checkout_created_at"])
    binding["attempted_at"] = (checkout - age).isoformat()
    assert any("owner-authorized sandbox simulation" in e for e in check(bundle))


@pytest.mark.parametrize("length,valid", [(64, False), (65, False), (128, False)])
def test_receipt_ruleset_version_uses_native_persisted_64_character_limit(
    bundle, length, valid
):
    receipt = bundle["receipt"]
    receipt["ruleset_version"] = receipt["owner_artifact"]["ruleset_version"] = receipt[
        "frozen_lease"
    ]["ruleset_version"] = "r" * length
    if valid:
        assert check(bundle) == []
    else:
        assert any("missing ruleset_version" in e for e in check(bundle))


@pytest.mark.parametrize("count", [2, 3, 4])
def test_mismatch_capture_sequence_stops_at_native_repeat_threshold(bundle, count):
    repeated = bundle["repeated_mismatch"]
    if count == 4:
        extra = deepcopy(repeated["attempts"][-1])
        repeated["attempts"].append(extra)
    else:
        repeated["attempts"] = repeated["attempts"][:count]
    repeated["mismatch_count"] = repeated["policy_decision"]["mismatches"] = count
    if count == 3:
        assert check(bundle) == []
    else:
        assert any("exactly three distinct" in e for e in check(bundle))


@pytest.mark.parametrize("offset", [-1, 0, 1])
def test_simulation_must_precede_native_charge_creation_with_second_precision(
    bundle, offset
):
    row = bundle["stripe_runs"][0]
    binding = authorize_simulated_fixture(row)
    authorized = datetime.fromisoformat(binding["simulation"]["attempted_at"])
    # A sub-second authority shares its native integer second with the charge.
    binding["simulation"]["attempted_at"] = (
        authorized + timedelta(microseconds=999999)
    ).isoformat()
    row["payment"]["charge"]["created"] = int(authorized.timestamp()) + offset
    row["connect_dispatch"]["source_recheck"]["charge"]["created"] = row["payment"][
        "charge"
    ]["created"]
    inventory = row["transfer_inventory"]
    inventory["window_start_at"] = datetime.fromtimestamp(
        row["payment"]["charge"]["created"], UTC
    ).isoformat()
    inventory["filters"]["created"]["gte"] = row["payment"]["charge"]["created"]
    bind_platform_payout_inventory(row, row["bank_payout"])
    if offset < 0:
        assert any("owner-authorized sandbox simulation" in e for e in check(bundle))
    else:
        assert check(bundle) == []


def terminal_mismatch_refund(bundle, status="failed"):
    row = bundle["repeated_mismatch"]["attempts"][0]
    refund = row["refund"]
    extra = {
        "id": "re_priorTerminal",
        "payment_intent": row["intent"]["id"],
        "charge": row["charge"]["id"],
        "status": status,
        "amount": 400,
        "currency": "usd",
        "livemode": False,
        "metadata": {},
    }
    for field, kind, amount, fee in (
        ("balance_transaction", "refund", -400, 2),
        ("failure_balance_transaction", "refund_failure", 400, 3),
    ):
        extra[field] = {
            "id": "txn_prior" + field.replace("_", ""),
            "source": extra["id"],
            "type": kind,
            "amount": amount,
            "fee": fee,
            "net": amount - fee,
            "currency": "usd",
            "status": "available",
        }
    refund["inventory"]["data"].append(extra)
    row["final_accounting"]["retained_fee_cost_cents"] += 5
    return row, extra


@pytest.mark.parametrize("status", ["failed", "canceled"])
def test_terminal_mismatch_refunds_reconcile_all_transaction_fees(bundle, status):
    row, _extra = terminal_mismatch_refund(bundle, status)
    assert check(bundle) == []
    row["final_accounting"]["retained_fee_cost_cents"] -= 5
    assert any("inline retained fee" in e for e in check(bundle))


@pytest.mark.parametrize(
    "field,value",
    [
        ("charge", "ch_wrong"),
        ("currency", "eur"),
        ("amount", 0),
        ("amount", 6001),
        ("amount", True),
        ("livemode", True),
        ("balance_transaction", None),
        ("failure_balance_transaction", None),
    ],
)
def test_terminal_mismatch_refund_requires_complete_native_accounting(
    bundle, field, value
):
    _, extra = terminal_mismatch_refund(bundle)
    extra[field] = value
    assert any("terminal refund" in e for e in check(bundle))


@pytest.mark.parametrize(
    "field,value",
    [
        ("source", "re_wrong"),
        ("amount", 1),
        ("net", 400),
        ("fee", True),
        ("status", "unknown"),
        ("exchange_rate", 1),
        ("type", "charge"),
    ],
)
@pytest.mark.parametrize("side", ["balance_transaction", "failure_balance_transaction"])
def test_terminal_mismatch_transactions_validate_provider_fields(
    bundle, side, field, value
):
    _, extra = terminal_mismatch_refund(bundle)
    extra[side][field] = value
    assert any("terminal refund requires native" in e for e in check(bundle))


def taxable_settlement_fixture(row):
    payment, settlement = row["payment"], row["settlement"]
    payment["subtotal_cents"] = 320
    payment["tax_payable"].update(subtotal_cents=320, tax_cents=80)
    settlement.update(
        delivered_compute_cents=320,
        platform_compute_cents=220,
        post_refund_platform_compute_cents=160,
    )
    settlement["tax_allocation"].update(
        delivered_compute_cents=240,
        retained_tax_cents=60,
        refund_compute_cents=80,
        refund_tax_cents=20,
    )

    bind_saved_deposit(row)
    original_key = settlement["refund_idempotency_key"]
    replace_tax_revision_everywhere(
        row, original_key, f"lease-settle-{row['billing_record_id']}-80"
    )
    bind_money_dispatch(row)


def test_taxable_host_proceeds_use_compute_not_gross_capture(bundle):
    row = bundle["stripe_runs"][1]
    taxable_settlement_fixture(row)
    assert check(bundle) == []
    row["settlement"]["delivered_compute_cents"] = 400
    row["settlement"]["platform_compute_cents"] = 300
    assert any("compute-only allocation" in e for e in check(bundle))


@pytest.mark.parametrize(
    "field,value",
    [
        ("delivered_compute_cents", 300),
        ("retained_tax_cents", 0),
        ("refund_compute_cents", 100),
        ("refund_tax_cents", 0),
        ("refund_total_cents", 80),
        ("allocation_policy", "other"),
    ],
)
def test_taxed_refund_binds_native_compute_tax_allocation(bundle, field, value):
    row = bundle["stripe_runs"][1]
    taxable_settlement_fixture(row)
    row["settlement"]["tax_allocation"][field] = value
    assert any("refund must reconcile native compute-only" in e for e in check(bundle))


def test_post_refund_host_cannot_take_retained_sales_tax(bundle):
    row = bundle["stripe_runs"][1]
    taxable_settlement_fixture(row)
    row["settlement"]["post_refund_host_payable_cents"] = 250
    row["settlement"]["post_refund_platform_compute_cents"] = -10
    assert any("refund must reconcile native compute-only" in e for e in check(bundle))


@pytest.mark.parametrize("operator", [False, True])
def test_terminal_refund_fees_add_to_standalone_charge_cost(bundle, operator):
    row, _ = terminal_mismatch_refund(bundle)
    standalone_fees(row, operator=operator)
    row["final_accounting"]["retained_fee_cost_cents"] += 5
    assert check(bundle) == []


def test_terminal_failure_credit_can_return_a_processing_fee(bundle):
    row, extra = terminal_mismatch_refund(bundle)
    extra["failure_balance_transaction"].update(fee=-2, net=402)
    row["final_accounting"]["retained_fee_cost_cents"] = 10
    assert check(bundle) == []


def test_bare_failed_refund_cannot_hide_missing_fee_accounting(bundle):
    row = bundle["repeated_mismatch"]["attempts"][0]
    row["refund"]["inventory"]["data"].append(
        {"id": "re_bare", "payment_intent": row["intent"]["id"], "status": "failed"}
    )
    assert any("terminal refund" in e for e in check(bundle))


@pytest.mark.parametrize(
    "group", ["gateway_runs", "stripe_runs", "failed_bank_payout", "refund_recovery"]
)
@pytest.mark.parametrize(
    "field,value",
    [
        ("delivered_compute_cents", 401),
        ("delivered_compute_cents", True),
        ("platform_compute_cents", -1),
        ("platform_compute_cents", 299),
    ],
)
def test_every_transferred_payment_proves_compute_only_host_allocation(
    bundle, group, field, value
):
    row = bundle[group][0] if group.endswith("runs") else bundle[group]
    row["settlement"][field] = value
    assert any(
        "host payable must reconcile owner compute-only" in e for e in check(bundle)
    )


def replace_tax_revision_everywhere(value, original, replacement):
    if isinstance(value, dict):
        for key, child in value.items():
            if child == original:
                value[key] = replacement
            else:
                replace_tax_revision_everywhere(child, original, replacement)
    elif isinstance(value, list):
        for child in value:
            replace_tax_revision_everywhere(child, original, replacement)


@pytest.mark.parametrize("style", ["hex", "uppercase", "urn", "braces", "canonical"])
def test_tax_revision_must_match_native_canonical_persisted_uuid(bundle, style):
    original = bundle["stripe_runs"][0]["payment"]["tax_revision_id"]
    replacement = {
        "hex": original.replace("-", ""),
        "uppercase": original.upper(),
        "urn": "urn:uuid:" + original,
        "braces": "{" + original + "}",
        "canonical": original,
    }[style]
    replace_tax_revision_everywhere(bundle, original, replacement)
    if style == "canonical":
        assert check(bundle) == []
    else:
        assert any(
            "tax binding" in e or "revision/calculation pair" in e
            for e in check(bundle)
        )


@pytest.mark.parametrize(
    "ruleset",
    ["lease-fixed-hour/v1", "garbage-but-not-placeholder", "lease-fixed-hour/v2"],
)
def test_receipt_proves_native_ruleset_for_fresh_journeys(bundle, ruleset):
    receipt = bundle["receipt"]
    for artifact in (receipt, receipt["owner_artifact"], receipt["frozen_lease"]):
        artifact["ruleset_version"] = ruleset
    if ruleset == "lease-fixed-hour/v1":
        assert check(bundle) == []
    else:
        assert any("missing ruleset_version" in e for e in check(bundle))


@pytest.mark.parametrize(
    "group", ["gateway_runs", "stripe_runs", "refund_recovery", "failed_bank_payout"]
)
@pytest.mark.parametrize(
    "stage",
    ["prepared", "dispatching", "succeeded", "source_recheck", "provider_request"],
)
def test_transfers_cannot_bypass_persisted_connect_owner_dispatch(bundle, group, stage):
    row = bundle[group][0] if group.endswith("runs") else bundle[group]
    del row["connect_dispatch"][stage]
    assert any("Connect" in e or "connect_dispatch" in e for e in check(bundle))


@pytest.mark.parametrize("stage", ["prepared", "dispatching", "succeeded"])
@pytest.mark.parametrize(
    "field,value",
    [
        ("amount_cents", 101),
        ("charge_id", "ch_other"),
        ("account_id", "acct_other"),
        ("status", "waiting_recipient"),
        ("attempts", True),
        ("profile", {}),
        ("attempted_at", "2026-10-12T00:00:00+00:00"),
    ],
)
def test_connect_dispatch_snapshots_preserve_native_immutable_attempt(
    bundle, stage, field, value
):
    bundle["stripe_runs"][0]["connect_dispatch"][stage]["state"][field] = value
    assert any("Connect" in e for e in check(bundle))


@pytest.mark.parametrize(
    "age,valid",
    [
        (timedelta(hours=23) - timedelta(microseconds=1), True),
        (timedelta(hours=23), False),
    ],
)
def test_connect_creation_uses_original_bounded_mutation_window(bundle, age, valid):
    row = bundle["stripe_runs"][0]
    authority = row["connect_dispatch"]
    attempted = datetime.fromisoformat(authority["prepared"]["state"]["attempted_at"])
    created = attempted + age
    row["connect_transfer"]["created_at"] = created.isoformat()
    authority["dispatching"]["recorded_at"] = (
        created - timedelta(seconds=2)
    ).isoformat()
    authority["source_recheck"]["recorded_at"] = (
        created - timedelta(seconds=3)
    ).isoformat()
    authority["succeeded"]["recorded_at"] = (created + timedelta(seconds=1)).isoformat()
    # Isolate this predicate: later refund/bank records intentionally remain unmoved.
    window_errors = [e for e in check(bundle) if "23-hour mutation window" in e]
    assert bool(window_errors) is not valid


@pytest.mark.parametrize(
    "field,value",
    [
        ("amount_refunded", 301),
        ("amount_refunded", True),
        ("payment_intent", "pi_wrong"),
        ("amount", 399),
        ("currency", "eur"),
        ("livemode", True),
        ("paid", False),
    ],
)
def test_connect_source_charge_is_rechecked_before_provider_dispatch(
    bundle, field, value
):
    bundle["gateway_runs"][0]["connect_dispatch"]["source_recheck"]["charge"][field] = (
        value
    )
    assert any("source-charge recheck" in e for e in check(bundle))


@pytest.mark.parametrize("source_type", ["card", "bank_account", "fpx"])
def test_bank_payout_freezes_the_transfer_funded_source_balance(bundle, source_type):
    for row in [
        *bundle["gateway_runs"],
        *bundle["stripe_runs"],
        bundle["failed_bank_payout"],
    ]:
        bind_source_type(row, row.get("bank_payout", row), source_type)
    assert check(bundle) == []


@pytest.mark.parametrize(
    "location",
    [
        "transfer",
        "payout",
        "plan",
        "checkpoint",
        "request",
        "funding",
        "opening",
        "closing",
    ],
)
def test_bank_payout_cannot_switch_or_omit_frozen_balance_source_type(bundle, location):
    row = bundle["stripe_runs"][0]
    targets = {
        "transfer": row["connect_transfer"],
        "payout": row["bank_payout"],
        "plan": row["bank_dispatch_plan"],
        "checkpoint": row["bank_dispatch_checkpoint"],
        "request": row["bank_dispatch_checkpoint"]["provider_request"]["parameters"],
        "funding": row["bank_funding"],
        "opening": row["bank_funding"]["opening_balance"],
        "closing": row["bank_funding"]["closing_balance"],
    }
    field = (
        "available_source_types"
        if location in ("opening", "closing")
        else "source_type"
    )
    targets[location].pop(field)
    assert check(bundle)


def test_failed_bank_returns_funds_to_original_source_pool(bundle):
    balance = bundle["failed_bank_payout"]["bank_funding"]["closing_balance"]
    balance["available_source_types"] = {
        "card": 0,
        "bank_account": balance["available_cents"],
    }
    assert any("source_types must reconcile" in e for e in check(bundle))


@pytest.mark.parametrize(
    "field,value",
    [
        ("idempotency_key", "fresh-manual-key"),
        ("transfer_id", "tr_other"),
        ("path", "/v1/payouts"),
        ("method", "GET"),
        ("livemode", True),
        ("request_id", ""),
    ],
)
def test_connect_request_proves_native_idempotent_transfer_dispatch(
    bundle, field, value
):
    bundle["stripe_runs"][0]["connect_dispatch"]["provider_request"][field] = value
    assert any("Connect provider request must bind" in e for e in check(bundle))


@pytest.mark.parametrize(
    "field,value",
    [
        ("amount", 99),
        ("currency", "eur"),
        ("destination", "acct_other"),
        ("source_transaction", "ch_other"),
        ("metadata", {}),
    ],
)
def test_connect_provider_parameters_match_the_persisted_owner_attempt(
    bundle, field, value
):
    bundle["stripe_runs"][0]["connect_dispatch"]["provider_request"]["parameters"][
        field
    ] = value
    assert any("Connect provider request must bind" in e for e in check(bundle))


def test_transfer_inventory_must_preserve_provider_balance_source_type(bundle):
    bundle["stripe_runs"][0]["transfer_inventory"]["data"][0]["source_type"] = (
        "bank_account"
    )
    assert any("exactly one provider transfer" in e for e in check(bundle))


@pytest.mark.parametrize("code", list(range(32)) + [127])
def test_simulation_return_url_rejects_native_nonprintable_ascii(bundle, code):
    binding = authorize_simulated_fixture(bundle["stripe_runs"][0])
    binding["return_url"] = "https://example.org/" + chr(code)
    assert any("owner-authorized sandbox simulation" in e for e in check(bundle))


@pytest.mark.parametrize(
    "field,value",
    [
        ("stripe_transfers", "restricted"),
        ("stripe_transfers", None),
        ("payouts_enabled", False),
        ("payout_requirements_clear", False),
        ("destination_requirements_clear", False),
        ("ownership_verified", False),
        ("account_id", "acct_other"),
    ],
)
def test_standalone_recovery_cannot_dispatch_without_active_owner_bound_readiness(
    bundle, field, value
):
    bundle["refund_recovery"]["readiness"][field] = value
    assert any("readiness" in e or "recipient account" in e for e in check(bundle))


@pytest.mark.parametrize(
    "legal_type,state,valid",
    [
        ("individual", "CA", True),
        ("sole_proprietor", "TX", True),
        ("company", "CA", True),
        ("individual", "NY", True),
        ("individual", "HI", True),
        ("individual", "ZZ", False),
    ],
)
def test_connect_profiles_follow_merged_fiat_method_eligibility(
    bundle, legal_type, state, valid
):
    row = bundle["gateway_runs"][0]
    profile = dict(
        row["readiness"]["recipient_profile"], legal_type=legal_type, state=state
    )
    row["readiness"]["recipient_profile"] = profile
    for stage in ("prepared", "dispatching", "succeeded"):
        row["connect_dispatch"][stage]["state"]["profile"] = profile.copy()
    if valid:
        assert check(bundle) == []
    else:
        assert any("immutable attempt/profile" in e for e in check(bundle))


@pytest.mark.parametrize("field", ["refund_approval", "refund_plan"])
def test_captured_mismatch_cannot_refund_by_direct_provider_call_without_owner_authority(
    bundle, field
):
    bundle["repeated_mismatch"]["attempts"][2].pop(field)
    assert check(bundle)


@pytest.mark.parametrize(
    "field,value",
    [
        ("source", "provider_refund"),
        ("kind", "unknown"),
        ("operator_required", True),
        ("payment_intent_id", "pi_other"),
        ("owner_checkpoint", "other"),
        ("approved_at", "2026-10-12T04:00:00+00:00"),
    ],
)
def test_mismatch_refund_approval_is_bound_server_provenance(bundle, field, value):
    bundle["repeated_mismatch"]["attempts"][0]["refund_approval"][field] = value
    assert check(bundle)


@pytest.mark.parametrize("index,valid", [(0, True), (2, False)])
def test_repeat_threshold_forbids_automatic_refund_approval(bundle, index, valid):
    row = bundle["repeated_mismatch"]["attempts"][index]
    bind_automatic_guard(row, bundle["repeated_mismatch"])
    if valid:
        assert check(bundle) == []
    else:
        assert any("repeated threshold requires operator" in e for e in check(bundle))


@pytest.mark.parametrize(
    "field,value",
    [
        ("reserved_amount_cents", 399),
        ("refund_cents", 399),
        ("delivered_cents", 1),
        ("delivered_cents", False),
        ("pass_stripe_amount", True),
        ("currency", "eur"),
        ("stripe_payment_intent_id", "pi_other"),
        ("stripe_charge_id", "ch_other"),
        ("idempotency_key", "manual-refund"),
        ("operator_approved_mismatch_at", None),
        ("stripe_refund_id", "re_already"),
    ],
)
def test_full_mismatch_refund_plan_is_immutable_before_provider_io(
    bundle, field, value
):
    bundle["repeated_mismatch"]["attempts"][2]["refund_plan"]["plan"][field] = value
    assert any("full native refund plan" in e for e in check(bundle))


@pytest.mark.parametrize("offset", [-1, 0, 1])
def test_platform_loss_cannot_be_manufactured_by_paying_after_known_refund(
    bundle, offset
):
    row = bundle["stripe_runs"][0]
    outcome = row["refund"]["transfer_outcome"]
    outcome.pop("provider_reversal", None)
    outcome.update(
        kind="platform_loss",
        reversal_id=None,
        balance_transaction=None,
        reversed_cents=0,
        platform_loss_cents=20,
        operator_visible=True,
        financial_state="recorded",
        reason="insufficient_connect_balance",
        reversal_refused=True,
        refunded_from_platform_cents=100,
        loss_record="recorded-owner-loss",
        owner_checkpoint="loss-checkpoint",
        recovery_status="complete",
    )
    outcome["provider_refusal"] = loss_provider_error(outcome)
    sync_bank_to_outcome(row)
    paid = datetime.fromisoformat(row["bank_payout"]["recorded_at"])
    row["refund"]["discovery"]["recorded_at"] = (
        paid + timedelta(seconds=offset)
    ).isoformat()
    if offset > 0:
        assert check(bundle) == []
    else:
        assert any(
            "must already be paid before refund discovery" in e for e in check(bundle)
        )


@pytest.mark.parametrize(
    "suffix_length,valid", [(199, True), (200, True), (201, False), (500, False)]
)
def test_complete_payment_id_binding_obeys_native_suffix_bounds(
    bundle, suffix_length, valid
):
    row = bundle["gateway_runs"][0]
    original = row["payment"]["payment_intent_id"]
    replace_tax_revision_everywhere(row, original, "pi_" + "x" * suffix_length)
    if valid:
        assert check(bundle) == []
    else:
        assert any("captured USD sandbox payment" in e for e in check(bundle))


@pytest.mark.parametrize("prefix", ["ch", "py", "re", "pyr"])
@pytest.mark.parametrize("length,valid", [(200, True), (201, False)])
def test_charge_and_refund_id_alphabets_and_bounds_match_native_adapters(
    bundle, prefix, length, valid
):
    row = bundle["stripe_runs"][1]
    original = (
        row["payment"]["charge_id"]
        if prefix in ("ch", "py")
        else row["refund"]["refund_id"]
    )
    replace_tax_revision_everywhere(
        row, original, prefix + "_" + "a_" * (length // 2) + "a" * (length % 2)
    )
    if valid:
        assert check(bundle) == []
    else:
        assert check(bundle)


@pytest.mark.parametrize(
    "field,value",
    [
        ("eligible", False),
        ("reason", "daily_count_cap"),
        ("reserved_cents", 0),
        ("reserved_cents", True),
        ("day", "2026-10-11"),
        ("daily_count_limit", 3),
        ("daily_value_limit_cents", 5001),
        ("repeat_threshold", 4),
    ],
)
def test_automatic_approval_cannot_override_persisted_ineligible_policy(
    bundle, field, value
):
    row = bundle["repeated_mismatch"]["attempts"][0]
    bind_automatic_guard(row, bundle["repeated_mismatch"])
    row["policy_observation"][field] = value
    assert any(
        "automatic refund must bind eligible persisted policy" in e
        for e in check(bundle)
    )


@pytest.mark.parametrize(
    "field,value",
    [
        ("enabled_before", False),
        ("enabled_after", False),
        ("person_lock_held", False),
        ("reserved_count", 0),
        ("reserved_count", 3),
        ("reserved_count", True),
        ("reserved_cents", 399),
        ("reserved_cents", 5001),
        ("mismatches", 3),
        ("mismatches", 0),
        ("actor_sub", "apikey:other"),
        ("day", "2026-10-11"),
        ("checked_after_at", "2026-10-13T00:00:00+00:00"),
    ],
)
def test_automatic_creation_guard_must_hold_current_reservation_through_io(
    bundle, field, value
):
    row = bundle["repeated_mismatch"]["attempts"][0]
    bind_automatic_guard(row, bundle["repeated_mismatch"])
    row["automatic_guard"][field] = value
    assert any("automatic" in e for e in check(bundle))


@pytest.mark.parametrize(
    "field,value",
    [("refunds", [{"id": "re_external"}]), ("refunded_cents", 1), ("currency", "eur")],
)
def test_automatic_refund_guard_requires_fresh_unmodified_fee_evidence(
    bundle, field, value
):
    row = bundle["repeated_mismatch"]["attempts"][0]
    bind_automatic_guard(row, bundle["repeated_mismatch"])
    row["automatic_guard"]["fee_observation"][field] = value
    assert any("unchanged fee proof" in e for e in check(bundle))


@pytest.mark.parametrize("group", ["stripe_runs", "refund_recovery", "mismatch"])
@pytest.mark.parametrize(
    "missing", ["dispatch_plan", "dispatch_checkpoint", "provider_request"]
)
def test_provider_refund_and_copied_metadata_cannot_replace_owner_dispatch(
    bundle, group, missing
):
    row = (
        bundle["stripe_runs"][0]
        if group == "stripe_runs"
        else bundle["refund_recovery"]
        if group == "refund_recovery"
        else bundle["repeated_mismatch"]["attempts"][0]
    )
    refund = row["refund"]
    if missing == "provider_request":
        refund["dispatch_checkpoint"].pop(missing)
    else:
        refund.pop(missing)
    assert any("refund" in e for e in check(bundle))


@pytest.mark.parametrize("side", ["refund", "reversal"])
@pytest.mark.parametrize(
    "field,value",
    [
        ("idempotency_key", "copied-metadata-key"),
        ("path", "/v1/manual"),
        ("parameters", {}),
        ("request_id", ""),
    ],
)
def test_refund_and_reversal_requests_bind_original_owner_idempotency(
    bundle, side, field, value
):
    row = bundle["stripe_runs"][0]
    authority = (
        row["refund"]
        if side == "refund"
        else row["refund"]["transfer_outcome"]["owner_reversal"]
    )
    authority["dispatch_checkpoint"]["provider_request"][field] = value
    assert any("original provider request" in e for e in check(bundle))


@pytest.mark.parametrize("side", ["refund", "reversal"])
@pytest.mark.parametrize(
    "field,value",
    [
        ("amount_cents", 1),
        ("charge_id", "ch_other"),
        ("billing_record_id", "other"),
        ("idempotency_key", "new-key"),
        ("attempted_at", "2026-10-12T00:00:00+00:00"),
    ],
)
def test_refund_and_reversal_checkpoints_preserve_immutable_owner_authority(
    bundle, side, field, value
):
    row = bundle["stripe_runs"][0]
    authority = (
        row["refund"]
        if side == "refund"
        else row["refund"]["transfer_outcome"]["owner_reversal"]
    )
    authority["dispatch_checkpoint"][field] = value
    assert any("immutable owner" in e for e in check(bundle))


@pytest.mark.parametrize("missing", ["dispatch_plan", "dispatch_checkpoint"])
def test_successful_native_reversal_requires_deployed_owner_recovery(bundle, missing):
    bundle["stripe_runs"][0]["refund"]["transfer_outcome"]["owner_reversal"].pop(
        missing
    )
    assert any("reversal" in e for e in check(bundle))


@pytest.mark.parametrize("tie", ["charge", "billing", "lease"])
def test_global_inventory_rejects_duplicate_transfers_to_other_accounts(bundle, tie):
    row = bundle["stripe_runs"][0]
    extra = deepcopy(row["transfer_inventory"]["data"][0])
    extra.update(id="tr_otherDestination", destination="acct_other")
    extra["metadata"] = {k: "other" for k in extra["metadata"]}
    extra["source_transaction"] = "ch_other"
    if tie == "charge":
        extra["source_transaction"] = row["connect_transfer"]["source_transaction"]
    else:
        key = "billing_record_id" if tie == "billing" else "lease_id"
        extra["metadata"][key] = row[key]
    row["transfer_inventory"]["data"].append(extra)
    assert any("exactly one provider transfer" in e for e in check(bundle))


def test_complete_platform_inventory_can_include_unrelated_transfer(bundle):
    row = bundle["stripe_runs"][0]
    extra = deepcopy(row["transfer_inventory"]["data"][0])
    extra.update(
        id="tr_unrelated",
        destination="acct_unrelated",
        source_transaction="ch_unrelated",
        metadata={},
    )
    row["transfer_inventory"]["data"].append(extra)
    assert check(bundle) == []


@pytest.mark.parametrize(
    "field,value",
    [
        ("destination", "acct_host"),
        ("scope", "destination"),
        ("filters", {}),
        ("window_start_at", "2026-10-12T04:59:59+00:00"),
    ],
)
def test_global_inventory_must_span_every_destination_since_charge_creation(
    bundle, field, value
):
    bundle["stripe_runs"][0]["transfer_inventory"][field] = value
    assert any("transfer inventory" in e for e in check(bundle))


@pytest.mark.parametrize(
    "field,value",
    [
        ("stripe_account_id", "acct_other"),
        ("attempt_id", "other"),
        ("owner_sub", "other-owner"),
        ("host_org_id", "other-host"),
        ("tenant_id", "other-tenant"),
    ],
)
def test_agx64_enrollment_cannot_borrow_another_host_payout(bundle, field, value):
    bundle["enrollment"][field] = value
    assert any("enrollment" in e for e in check(bundle))


@pytest.mark.parametrize(
    "field,value",
    [
        ("delivered_compute_cents", False),
        (
            "tax_allocation",
            {
                "delivered_compute_cents": 300,
                "retained_tax_cents": False,
                "refund_compute_cents": 100,
                "refund_tax_cents": 0,
                "refund_total_cents": 100,
                "allocation_policy": "EXAMPLE-compute-proportional-tax-v1",
            },
        ),
    ],
)
def test_normalized_owner_money_plans_preserve_integer_types(bundle, field, value):
    row = (
        bundle["repeated_mismatch"]["attempts"][0]
        if field == "delivered_compute_cents"
        else bundle["stripe_runs"][0]
    )
    row["refund"]["dispatch_plan"][field] = value
    assert any("immutable owner" in e for e in check(bundle))


@pytest.mark.parametrize("field", ["outer", "inner", "both"])
@pytest.mark.parametrize("style", ["canonical", "hex", "uppercase", "urn", "braces"])
def test_simulation_operation_ids_are_native_canonical_uuid_strings(
    bundle, field, style
):
    binding = authorize_simulated_fixture(bundle["stripe_runs"][0])
    outer = "abcdefab-5555-4555-8555-555555555555"
    inner = "bcdefabc-6666-4666-8666-666666666666"

    def spelling(value):
        return {
            "canonical": value,
            "hex": value.replace("-", ""),
            "uppercase": value.upper(),
            "urn": "urn:uuid:" + value,
            "braces": "{" + value + "}",
        }[style]

    binding["operation_id"] = spelling(outer) if field in ("outer", "both") else outer
    binding["simulation"]["deposit_operation_id"] = binding["operation_id"]
    binding["simulation"]["operation_id"] = (
        spelling(inner) if field in ("inner", "both") else inner
    )
    if style == "canonical":
        assert check(bundle) == []
    else:
        assert any("owner-authorized sandbox simulation" in e for e in check(bundle))


@pytest.mark.parametrize("kind", ["refund", "payment_refund"])
@pytest.mark.parametrize("status", ["pending", "available"])
def test_succeeded_mismatch_refund_can_return_processing_fees(bundle, kind, status):
    final = bundle["repeated_mismatch"]["attempts"][0]["final_accounting"]
    final["refund_balance_transaction"].update(
        type=kind,
        status=status,
        fee=-3,
        net=3 - bundle["repeated_mismatch"]["attempts"][0]["amount_cents"],
    )
    final["retained_fee_cost_cents"] = 7
    assert check(bundle) == []
    final["retained_fee_cost_cents"] = 10
    assert any("inline retained fee" in e for e in check(bundle))


@pytest.mark.parametrize("operator", [False, True])
def test_returned_refund_fee_reconciles_with_standalone_charge_cost(bundle, operator):
    row = bundle["repeated_mismatch"]["attempts"][0]
    standalone_fees(row, operator=operator)
    final = row["final_accounting"]
    final["refund_balance_transaction"].update(
        fee=-3, net=3 - bundle["repeated_mismatch"]["attempts"][0]["amount_cents"]
    )
    final["retained_fee_cost_cents"] = 7
    assert check(bundle) == []


def non_stablecoin_gateway_fixture(bundle):
    row = bundle["gateway_runs"][0]
    row["source_charge_type"] = "non_stablecoin"
    payment = row["payment"]
    payment["tax_payable"]["expires_at"] = (
        int(datetime.fromisoformat(payment["checkout_created_at"]).timestamp())
        + 31 * 60
    )
    payment["intent"]["metadata"].update(
        payment_rail="stripe_checkout", payment_flow="checkout"
    )
    payment["charge"]["payment_method_details"] = {"type": "card", "card": {}}
    for candidate in [*bundle["gateway_runs"], *bundle["stripe_runs"]]:
        if candidate["payment"] is payment:
            candidate["connect_dispatch"]["source_recheck"]["charge"][
                "payment_method_details"
            ] = deepcopy(payment["charge"]["payment_method_details"])
    payment["capture_event"]["data"]["object"] = deepcopy(payment["intent"])
    bundle["fiat_rollout"]["prerequisites"].append(
        record(
            "nonstable-source",
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
    bundle["closed_issues"].append(491)
    return row


@pytest.mark.parametrize("subtotal,valid", [(399, False), (400, True), (1000, False)])
def test_nonstablecoin_compute_cannot_exceed_or_understate_untaxed_capture(
    bundle, subtotal, valid
):
    row = non_stablecoin_gateway_fixture(bundle)
    payment = row["payment"]
    payment["tax_payable"] = None
    for field in ("tax_revision_id", "tax_calculation_id"):
        payment.pop(field)
        payment["intent"]["metadata"].pop(field)
    payment["capture_event"]["data"]["object"] = deepcopy(payment["intent"])
    payment["subtotal_cents"] = subtotal
    row["settlement"].update(
        delivered_compute_cents=subtotal, platform_compute_cents=subtotal - 100
    )
    if valid:
        assert any(
            "native lease adapter is not implemented" in e for e in check(bundle)
        )
    else:
        assert any("tax payable binding or breakdown" in e for e in check(bundle))


def test_nonstablecoin_taxed_compute_reconciles_subtotal_tax_and_gross(bundle):
    row = non_stablecoin_gateway_fixture(bundle)
    row["payment"]["subtotal_cents"] = 320
    row["payment"]["tax_payable"].update(subtotal_cents=320, tax_cents=80)
    row["settlement"].update(delivered_compute_cents=320, platform_compute_cents=220)
    row["settlement"].update(
        retained_cents=400,
        refund_due_cents=0,
        post_refund_host_payable_cents=100,
        post_refund_platform_compute_cents=220,
        reversal_due_cents=0,
        tax_allocation={
            "delivered_compute_cents": 320,
            "retained_tax_cents": 80,
            "refund_compute_cents": 0,
            "refund_tax_cents": 0,
            "refund_total_cents": 0,
            "allocation_policy": "EXAMPLE-compute-proportional-tax-v1",
        },
    )
    assert any("native lease adapter is not implemented" in e for e in check(bundle))
    row["payment"]["tax_payable"]["tax_cents"] = 81
    assert any("tax payable" in e for e in check(bundle))


@pytest.mark.parametrize("kind", ["refund", "payment_refund"])
def test_short_balance_recovery_accepts_both_native_refund_debit_types(bundle, kind):
    bundle["refund_recovery"]["final_accounting"]["refund_balance_transaction"][
        "type"
    ] = kind
    assert check(bundle) == []


@pytest.mark.parametrize("taxed", [False, True])
def test_ordinary_refund_cannot_replace_native_retry_key_even_when_copied_everywhere(
    bundle, taxed
):
    row = bundle["stripe_runs"][1]
    if taxed:
        taxable_settlement_fixture(row)
    assert check(bundle) == []
    original = row["settlement"]["refund_idempotency_key"]
    replace_tax_revision_everywhere(row, original, "hm-refund-manual")
    assert any("native lease-settle" in e for e in check(bundle))


@pytest.mark.parametrize("offset,valid", [(-1, False), (0, True), (1, True)])
def test_refund_plan_origin_is_bounded_by_owner_capture(bundle, offset, valid):
    row = bundle["stripe_runs"][0]
    plan = row["refund"]["dispatch_plan"]
    at = datetime.fromisoformat(row["payment"]["recorded_at"]) + timedelta(
        microseconds=offset
    )
    plan["attempted_at"] = plan["recorded_at"] = at.isoformat()
    row["refund"]["dispatch_checkpoint"]["attempted_at"] = at.isoformat()
    if valid:
        assert check(bundle) == []
    else:
        assert any(
            "follow original checkout/payment chronology" in e for e in check(bundle)
        )


@pytest.mark.parametrize("rate", [None, False, 0, 1, "1.0"])
def test_automatic_guard_rejects_any_native_currency_conversion(bundle, rate):
    row = bundle["repeated_mismatch"]["attempts"][0]
    bind_automatic_guard(row, bundle["repeated_mismatch"])
    row["automatic_guard"]["fee_observation"]["charge_balance_transaction"][
        "exchange_rate"
    ] = rate
    if rate is None:
        assert check(bundle) == []
    else:
        assert any("unchanged fee proof" in e for e in check(bundle))


@pytest.mark.parametrize(
    "details",
    [
        None,
        {"type": "crypto", "crypto": {"network": "base", "token_currency": "usdc"}},
        {"type": "card", "card": {"brand": "different"}},
    ],
)
def test_nonstable_source_recheck_preserves_immutable_original_charge_details(
    bundle, details
):
    row = non_stablecoin_gateway_fixture(bundle)
    row["connect_dispatch"]["source_recheck"]["charge"]["payment_method_details"] = (
        details
    )
    assert any("source-charge recheck" in e for e in check(bundle))


@pytest.mark.parametrize(
    "field,value",
    [
        ("eligible", True),
        ("reason", "eligible_pending_fee_accounting"),
        ("reserved_cents", 400),
        ("daily_count_limit", 3),
        ("daily_value_limit_cents", 10000),
        ("repeat_threshold", 4),
    ],
)
def test_third_mismatch_must_persist_native_repeat_refusal_and_fixed_limits(
    bundle, field, value
):
    bundle["repeated_mismatch"]["attempts"][2]["policy_observation"][field] = value
    assert any("each persisted native policy result" in e for e in check(bundle))


@pytest.mark.parametrize("index", [0, 1, 2])
@pytest.mark.parametrize(
    "field",
    [
        "eligible",
        "reason",
        "reserved_cents",
        "daily_count_limit",
        "daily_value_limit_cents",
        "repeat_threshold",
    ],
)
def test_every_mismatch_policy_observation_includes_native_decision_fields(
    bundle, index, field
):
    bundle["repeated_mismatch"]["attempts"][index]["policy_observation"].pop(field)
    assert any("each persisted native policy result" in e for e in check(bundle))


def test_repeat_journey_cannot_checkout_after_two_reserved_small_refunds(bundle):
    repeated = bundle["repeated_mismatch"]
    for row in repeated["attempts"][:2]:
        policy = deepcopy(row["policy_observation"])
        replacement = refusal_record(
            row["id"],
            datetime.fromisoformat(row["recorded_at"]).hour,
            mismatch=True,
            amount=400,
        )
        row.clear()
        row.update(replacement)
        row["policy_observation"] = policy
    bind_policy_sequence(repeated)
    assert any("already reserved native daily refund cap" in e for e in check(bundle))


@pytest.mark.parametrize("offset,valid", [(-1, False), (0, True), (1, True)])
def test_global_transfer_inventory_waits_for_terminal_owner_dispatch(
    bundle, offset, valid
):
    row = bundle["stripe_runs"][0]
    terminal = datetime.fromisoformat(
        row["connect_dispatch"]["succeeded"]["recorded_at"]
    )
    inventory = row["transfer_inventory"]
    at = terminal + timedelta(microseconds=offset)
    inventory["recorded_at"] = inventory["window_end_at"] = at.isoformat()
    inventory["filters"]["created"]["lte"] = int(at.timestamp())
    if valid:
        assert check(bundle) == []
    else:
        assert any("terminal owner dispatch reconciliation" in e for e in check(bundle))


@pytest.mark.parametrize("start_offset,valid", [(200000, True), (50000, False)])
def test_same_second_transfer_and_refund_use_precise_owner_request_order(
    bundle, start_offset, valid
):
    row = bundle["stripe_runs"][0]
    refund = row["refund"]
    created = datetime.fromisoformat(row["connect_transfer"]["created_at"])
    row["connect_dispatch"]["succeeded"]["recorded_at"] = (
        created + timedelta(microseconds=100000)
    ).isoformat()
    refund["created_at"] = row["connect_transfer"]["created_at"]
    refund["inventory"]["data"][0]["created_at"] = refund["created_at"]
    plan = refund["dispatch_plan"]
    plan["attempted_at"] = plan["recorded_at"] = (
        created + timedelta(microseconds=start_offset)
    ).isoformat()
    checkpoint = refund["dispatch_checkpoint"]
    checkpoint["attempted_at"] = plan["attempted_at"]
    checkpoint["recorded_at"] = (created + timedelta(microseconds=600000)).isoformat()
    checkpoint["provider_request"].update(
        started_at=plan["attempted_at"],
        recorded_at=(created + timedelta(microseconds=500000)).isoformat(),
    )
    if valid:
        assert check(bundle) == []
    else:
        assert any("owner transfer completion must precede" in e for e in check(bundle))


def test_automatic_refund_owner_guard_uses_precise_request_time_not_provider_second(
    bundle,
):
    row = bundle["repeated_mismatch"]["attempts"][0]
    bind_automatic_guard(row, bundle["repeated_mismatch"])
    created = datetime.fromisoformat(row["refund"]["created_at"])

    def at(micros):
        return (created + timedelta(microseconds=micros)).isoformat()

    approval = row["refund_approval"]
    approval.update(approved_at=at(100000), recorded_at=at(150000))
    raw = row["refund_plan"]
    raw["recorded_at"] = at(250000)
    raw["plan"].update(
        stripe_refund_attempted_at=at(200000), automatic_approved_mismatch_at=at(100000)
    )
    row["automatic_guard"].update(
        checked_before_at=at(350000),
        checked_after_at=at(400000),
        recorded_at=at(450000),
    )
    refund = row["refund"]
    refund["dispatch_plan"].update(
        attempted_at=at(200000), recorded_at=at(300000), approval_at=at(100000)
    )
    checkpoint = refund["dispatch_checkpoint"]
    checkpoint.update(
        attempted_at=at(200000), recorded_at=at(700000), approval_at=at(100000)
    )
    checkpoint["provider_request"].update(started_at=at(500000), recorded_at=at(600000))
    assert check(bundle) == []


@pytest.mark.parametrize("kind", ["gateway", "stripe", "failed"])
@pytest.mark.parametrize("offset,valid", [(-1, False), (0, True), (1, True)])
def test_bank_inventory_cannot_close_before_terminal_dispatch(
    bundle, kind, offset, valid
):
    row = (
        bundle["failed_bank_payout"]
        if kind == "failed"
        else bundle["gateway_runs" if kind == "gateway" else "stripe_runs"][0]
    )
    funding = row["bank_funding"]
    terminal = datetime.fromisoformat(row["bank_dispatch_checkpoint"]["recorded_at"])
    if kind == "failed":
        # Keep the returned-funds proof complete while moving terminal reconciliation.
        terminal = datetime.fromisoformat(funding["closing_balance"]["recorded_at"])
        row["bank_dispatch_checkpoint"]["recorded_at"] = terminal.isoformat()
        row["bank_dispatch"]["recorded_at"] = terminal.isoformat()
        row["bank_dispatch_attempts"]["window_end_at"] = terminal.isoformat()
        row["bank_dispatch_attempts"]["recorded_at"] = terminal.isoformat()
    closing = (terminal + timedelta(microseconds=offset)).isoformat()
    funding["closing_balance"]["recorded_at"] = funding["window_end_at"] = closing
    if valid:
        assert check(bundle) == []
    else:
        assert any("terminal bank dispatch reconciliation" in e for e in check(bundle))


@pytest.mark.parametrize("group", ["gateway_runs", "stripe_runs", "failed_bank_payout"])
def test_late_duplicate_bank_debit_cannot_hide_in_balanced_inventory(bundle, group):
    row = bundle[group] if group == "failed_bank_payout" else bundle[group][0]
    funding = row["bank_funding"]
    at = funding["closing_balance"]["recorded_at"]
    debit = next(tx for tx in funding["transactions"] if tx["type"] == "payout")
    extra = deepcopy(debit)
    extra.update(id="txn_duplicatepayout", source="po_duplicate", created_at=at)
    # Offset the extra debit so net/balance equality alone cannot detect it.
    credit = deepcopy(extra)
    credit.update(
        id="txn_duplicatecredit",
        source="py_unrelated",
        type="payment",
        amount=-extra["amount"],
        net=-extra["net"],
    )
    funding["transactions"].extend([extra, credit])
    assert any(
        "isolated funding window must contain exactly" in e for e in check(bundle)
    )


def financial_row(bundle, group):
    return (
        bundle[group][0] if group in ("gateway_runs", "stripe_runs") else bundle[group]
    )


@pytest.mark.parametrize(
    "group", ["gateway_runs", "stripe_runs", "failed_bank_payout", "refund_recovery"]
)
@pytest.mark.parametrize("refunded", [1, 300, True])
def test_source_recheck_cannot_admit_unaccounted_prior_refunds(bundle, group, refunded):
    row = financial_row(bundle, group)
    row["connect_dispatch"]["source_recheck"]["charge"]["amount_refunded"] = refunded
    assert any("source-charge recheck" in e for e in check(bundle))


@pytest.mark.parametrize("group", ["gateway_runs", "stripe_runs", "failed_bank_payout"])
@pytest.mark.parametrize("offset,valid", [(-1, False), (0, True), (1, True)])
def test_bank_plan_follows_source_transfer_and_payout_readiness(
    bundle, group, offset, valid
):
    row = financial_row(bundle, group)
    ready = datetime.fromisoformat(row["payout_readiness"]["recorded_at"])
    row["bank_dispatch_plan"]["recorded_at"] = (
        ready + timedelta(microseconds=offset)
    ).isoformat()
    row["bank_dispatch_attempts"]["window_start_at"] = row["bank_dispatch_plan"][
        "recorded_at"
    ]
    if valid:
        assert check(bundle) == []
    else:
        assert any("bank plan must follow source transfer" in e for e in check(bundle))


@pytest.mark.parametrize(
    "group", ["gateway_runs", "stripe_runs", "failed_bank_payout", "refund_recovery"]
)
@pytest.mark.parametrize(
    "field,value",
    [
        ("object", "charge"),
        ("amount", 0),
        ("amount", True),
        ("amount", None),
        ("payment_method_options", {}),
        ("payment_method_options", {"crypto": {"mode": "payment"}}),
    ],
)
def test_capture_event_retains_immutable_intent_fields(bundle, group, field, value):
    row = financial_row(bundle, group)
    row["payment"]["capture_event"]["data"]["object"][field] = value
    assert any("succeeded event bound" in e for e in check(bundle))


@pytest.mark.parametrize(
    "part", ["owner_reversal", "dispatch_plan", "dispatch_checkpoint"]
)
def test_refused_reversal_requires_original_owner_authority(bundle, part):
    outcome = bundle["refund_recovery"]["refund"]["transfer_outcome"]
    if part == "owner_reversal":
        outcome.pop(part)
    else:
        outcome["owner_reversal"].pop(part)
    assert check(bundle)


@pytest.mark.parametrize(
    "field,value",
    [
        ("status", "reversing"),
        ("operator_required", False),
        ("error", "ValueError"),
        ("platform_loss_cents", 1),
        ("reversed_cents", 20),
        ("reversal", None),
        ("reversal_id", "trr_unexpected"),
        ("amount_cents", 1),
        ("idempotency_key", "another-key"),
    ],
)
def test_loss_checkpoint_retains_native_pending_reversal_and_outcome(
    bundle, field, value
):
    outcome = bundle["refund_recovery"]["refund"]["transfer_outcome"]
    outcome["owner_reversal"]["dispatch_checkpoint"][field] = value
    assert check(bundle)


@pytest.mark.parametrize(
    "field,value",
    [
        ("request_id", "req_unrelated"),
        ("code", "other"),
        ("error_type", "ValueError"),
        ("parameters", {}),
        ("idempotency_key", "other"),
    ],
)
def test_loss_owner_request_binds_the_exact_provider_refusal(bundle, field, value):
    outcome = bundle["refund_recovery"]["refund"]["transfer_outcome"]
    outcome["owner_reversal"]["dispatch_checkpoint"]["provider_request"][field] = value
    assert check(bundle)


def test_loss_plan_cannot_predate_refund_owner_completion(bundle):
    refund = bundle["refund_recovery"]["refund"]
    plan = refund["transfer_outcome"]["owner_reversal"]["dispatch_plan"]
    plan["recorded_at"] = (
        datetime.fromisoformat(refund["dispatch_checkpoint"]["recorded_at"])
        - timedelta(microseconds=1)
    ).isoformat()
    assert any("owner refund checkpoint must precede" in e for e in check(bundle))


def test_refusal_cannot_predate_precise_reversal_request_start(bundle):
    row = bundle["refund_recovery"]
    refusal = datetime.fromisoformat(row["reversal_refusal"]["recorded_at"])
    request = row["refund"]["transfer_outcome"]["owner_reversal"][
        "dispatch_checkpoint"
    ]["provider_request"]
    request.update(
        started_at=(refusal + timedelta(microseconds=1)).isoformat(),
        recorded_at=(refusal + timedelta(microseconds=2)).isoformat(),
    )
    assert any(
        "provider refusal must follow original reversal request start" in e
        for e in check(bundle)
    )


@pytest.mark.parametrize("stage", ["enrollments_before", "enrollments_after"])
@pytest.mark.parametrize(
    "field,value",
    [
        ("complete", False),
        ("complete", None),
        ("has_more", True),
        ("total_count", 0),
        ("total_count", True),
        ("filters", {"host_org_id": "survivor-only"}),
        ("scope", "tenant"),
        ("source", "untrusted"),
        ("platform_account_id", "acct_other"),
    ],
)
def test_rollout_inventories_are_complete_environment_wide_owner_reads(
    bundle, stage, field, value
):
    bundle["fiat_rollout"][stage][field] = value
    assert any(
        "complete authoritative unfiltered enrollment" in e for e in check(bundle)
    )


def test_rollout_cannot_hide_a_second_enrollment_by_omitting_both_copies(bundle):
    add_historical_usdc_enrollment(bundle)
    # Both native totals remain two even if a projection drops the historical host.
    for name in ("enrollments_before", "enrollments_after"):
        bundle["fiat_rollout"][name]["data"].pop()
    assert any(
        "complete authoritative unfiltered enrollment" in e for e in check(bundle)
    )


@pytest.mark.parametrize("stage", ["enrollments_before", "enrollments_after"])
def test_rollout_requires_equal_full_enrollment_identity_sets(bundle, stage):
    add_historical_usdc_enrollment(bundle)
    snapshot = bundle["fiat_rollout"][stage]
    snapshot["data"].pop()
    snapshot["total_count"] -= 1
    assert any("history was lost or replaced" in e for e in check(bundle))


@pytest.mark.parametrize(
    "stage,at",
    [
        ("enrollments_before", "2026-10-12T00:11:00+00:00"),
        ("enrollments_after", "2026-10-12T00:09:00+00:00"),
        ("enrollments_after", "2026-10-12T00:31:00+00:00"),
    ],
)
def test_complete_inventory_observations_bracket_rollout(bundle, stage, at):
    bundle["fiat_rollout"][stage]["recorded_at"] = at
    assert any("inventories must bracket rollout" in e for e in check(bundle))


@pytest.mark.parametrize("field", ["lease_id", "billing_record_id"])
def test_failed_bank_scope_is_distinct_from_standalone_refund_recovery(bundle, field):
    bundle["failed_bank_payout"][field] = bundle["refund_recovery"][field]
    assert any(
        "exercise requires distinct lease and billing" in e for e in check(bundle)
    )


@pytest.mark.parametrize(
    "path",
    [
        ("payment", "payment_intent_id"),
        ("payment", "charge_id"),
        ("connect_transfer", "id"),
        ("connect_transfer", "destination_payment"),
        ("payment", "capture_event", "id"),
        ("bank_dispatch_checkpoint", "provider_request", "request_id"),
    ],
)
def test_failed_bank_cannot_reuse_recovery_financial_provider_identity(bundle, path):
    failure, recovery = bundle["failed_bank_payout"], bundle["refund_recovery"]
    if path == ("bank_dispatch_checkpoint", "provider_request", "request_id"):
        value = recovery["reversal_refusal"]["request_id"]
    else:
        value = recovery
        for key in path:
            value = value[key]
    target = failure
    for key in path[:-1]:
        target = target[key]
    target[path[-1]] = value
    assert any(
        "provider financial identities must be distinct" in e for e in check(bundle)
    )


@pytest.mark.parametrize("valid", [True, False])
def test_same_second_refund_and_reversal_use_precise_owner_order(bundle, valid):
    row = bundle["stripe_runs"][0]
    refund = row["refund"]
    native = datetime.fromisoformat(refund["created_at"])

    def at(micros):
        return (native + timedelta(microseconds=micros)).isoformat()

    refund["dispatch_checkpoint"]["recorded_at"] = at(100000)
    outcome = refund["transfer_outcome"]
    reversal = outcome["provider_reversal"]
    reversal["created_at"] = refund["created_at"]
    outcome["reversal_inventory"]["data"][0] = deepcopy(reversal)
    for tx in row["bank_funding"]["transactions"]:
        if tx["type"] == "payment_refund":
            tx["created_at"] = tx["available_at"] = reversal["created_at"]
    owner = outcome["owner_reversal"]
    plan = owner["dispatch_plan"]
    plan["attempted_at"] = plan["recorded_at"] = at(200000 if valid else 40000)
    checkpoint = owner["dispatch_checkpoint"]
    checkpoint["attempted_at"] = plan["attempted_at"]
    checkpoint["provider_request"].update(
        started_at=at(300000 if valid else 50000), recorded_at=at(400000)
    )
    if valid:
        assert check(bundle) == []
    else:
        assert any(
            "owner refund checkpoint must precede reversal preparation" in e
            for e in check(bundle)
        )


@pytest.mark.parametrize(
    "group", ["gateway_runs", "stripe_runs", "failed_bank_payout", "refund_recovery"]
)
@pytest.mark.parametrize(
    "case", ["same_second", "earlier_provider_second", "request_before_sending"]
)
def test_connect_dispatch_compares_provider_creation_at_native_precision(
    bundle, group, case
):
    row = financial_row(bundle, group)
    transfer = row["connect_transfer"]
    native = datetime.fromisoformat(transfer["created_at"])

    def at(micros):
        return (native + timedelta(microseconds=micros)).isoformat()

    authority = row["connect_dispatch"]
    for stage in ("prepared", "dispatching", "succeeded"):
        authority[stage]["state"]["attempted_at"] = at(50000)
    authority["prepared"]["recorded_at"] = at(100000)
    authority["source_recheck"]["recorded_at"] = at(150000)
    authority["dispatching"]["recorded_at"] = at(200000)
    authority["provider_request"]["recorded_at"] = at(
        100000 if case == "request_before_sending" else 300000
    )
    authority["succeeded"]["recorded_at"] = at(400000)
    inventory = row["transfer_inventory"]
    if case == "earlier_provider_second":
        transfer["created_at"] = (native - timedelta(seconds=1)).isoformat()
        inventory["data"][0]["created_at"] = transfer["created_at"]
    if case == "same_second":
        assert check(bundle) == []
    else:
        assert any(
            "Connect plan must persist" in e or "Connect provider creation/request" in e
            for e in check(bundle)
        )


@pytest.mark.parametrize(
    "suffix", ["dest_common", "common_", "_common", "dest__common"]
)
def test_shared_underscored_destination_payment_cannot_cross_special_exercises(
    bundle, suffix
):
    def destination(row, value):
        old = row["connect_transfer"]["destination_payment"]
        row["connect_transfer"]["destination_payment"] = value
        for item in row["transfer_inventory"]["data"]:
            if item.get("destination_payment") == old:
                item["destination_payment"] = value
        for tx in row.get("bank_funding", {}).get("transactions", []):
            if tx.get("source") == old:
                tx["source"] = value

    recovery, failed = bundle["refund_recovery"], bundle["failed_bank_payout"]
    destination(recovery, "py_" + suffix)
    destination(failed, "py_distinct_" + suffix)
    assert check(bundle) == []
    destination(failed, "py_" + suffix)
    errors = check(bundle)
    assert errors == [
        "failed_bank_payout: provider financial identities must be distinct from refund_recovery",
        "failed_bank_payout: destination payment reused across financial scopes",
    ]


@pytest.mark.parametrize(
    "kind,index", [("operator", 0), ("operator", 2), ("automatic", 0)]
)
@pytest.mark.parametrize("offset,valid", [(-1, False), (0, True), (1, True)])
def test_mismatch_approval_follows_its_persisted_policy(
    bundle, kind, index, offset, valid
):
    repeated = bundle["repeated_mismatch"]
    row = repeated["attempts"][index]
    if kind == "automatic":
        bind_automatic_guard(row, repeated)
    policy_at = datetime.fromisoformat(row["policy_observation"]["recorded_at"])
    approved = (policy_at + timedelta(microseconds=offset)).isoformat()
    row["refund_approval"]["approved_at"] = approved
    row["refund_plan"]["plan"][f"{kind}_approved_mismatch_at"] = approved
    for stage in ("dispatch_plan", "dispatch_checkpoint"):
        row["refund"][stage]["approval_at"] = approved
    if valid:
        assert check(bundle) == []
    else:
        assert any(
            "mismatch policy must persist before approval" in e for e in check(bundle)
        )


def shift_evidence_times(value, delta, suffix):
    if isinstance(value, dict):
        if "revision" in value and "recorded_at" in value:
            value["recorded_at"] = (
                datetime.fromisoformat(value["recorded_at"]) + delta
            ).isoformat()
            value["id"] += suffix
        for child in value.values():
            shift_evidence_times(child, delta, suffix)
    elif isinstance(value, list):
        for child in value:
            shift_evidence_times(child, delta, suffix)


def bind_bank_failure(attempt, *, provider=False):
    req = attempt["provider_request"]
    attempt["error_type"] = "InvalidRequestError" if provider else "TimeoutError"
    failure = record(
        "bank-failure-" + attempt["attempt_id"],
        recorded_at=attempt["recorded_at"],
        source="payment_bank_dispatch_failure",
        attempt_id=attempt["attempt_id"],
        plan=deepcopy(attempt["plan"]),
        started_at=attempt["started_at"],
        request_id=req.get("request_id"),
        kind="provider_error" if provider else "transport_error",
        error_type=attempt["error_type"],
        response_received=provider,
    )
    if provider:
        failure["code"] = "balance_insufficient"
        failure["response"] = record(
            "bank-provider-error-" + attempt["attempt_id"],
            recorded_at=req["recorded_at"],
            source="stripe_error_response",
            request_id=req["request_id"],
            account_id=req["account_id"],
            livemode=False,
            idempotency_key=req["idempotency_key"],
            http_status=400,
            error={"type": "invalid_request_error", "code": "balance_insufficient"},
        )
    attempt["failure"] = failure


def bank_timeout_then_retry(row):
    inventory = row["bank_dispatch_attempts"]
    checkpoint = row["bank_dispatch_checkpoint"]
    original = inventory["data"][0]
    created = datetime.fromisoformat(
        checkpoint["provider_request"]["recorded_at"]
    ) - timedelta(seconds=1)
    first = deepcopy(original)
    first.update(
        outcome="transport_error",
        error_type="TimeoutError",
        recorded_at=(created - timedelta(seconds=1)).isoformat(),
    )
    first["provider_request"].update(
        request_id=None, payout_id=None, recorded_at=first["recorded_at"]
    )
    first["provider_request"]["id"] += "-initial-timeout"
    bind_bank_failure(first)
    original.update(
        sequence=2,
        attempt_id=str(uuid5(NAMESPACE_URL, "bank-retry-" + row["id"])),
        started_at=(created + timedelta(microseconds=250000)).isoformat(),
        id=original["id"] + "-retry",
    )
    ready = deepcopy(row["payout_readiness"])
    shift_evidence_times(
        ready,
        created
        + timedelta(microseconds=100000)
        - datetime.fromisoformat(ready["recorded_at"]),
        "-retry",
    )
    original["readiness"] = ready
    inventory["data"] = [first, original]
    inventory["total_count"] = checkpoint["attempt_count"] = 2
    checkpoint["provider_attempt_id"] = original["attempt_id"]
    return first, original


@pytest.mark.parametrize("group", ["gateway_runs", "stripe_runs", "failed_bank_payout"])
def test_bank_timeout_retry_preserves_every_post_and_its_fresh_readiness(bundle, group):
    bank_timeout_then_retry(financial_row(bundle, group))
    assert check(bundle) == []


@pytest.mark.parametrize(
    "mutation",
    [
        "omit_first",
        "wrong_first_key",
        "wrong_first_amount",
        "stale_retry_read",
        "missing_retry_read",
        "reorder",
        "duplicate_attempt",
        "count",
        "incomplete",
        "filtered",
        "different_plan",
        "provider_request_reuse",
        "terminal_attempt",
    ],
)
def test_complete_bank_attempt_journal_cannot_hide_earlier_mutations(bundle, mutation):
    row = bundle["gateway_runs"][0]
    first, last = bank_timeout_then_retry(row)
    inventory = row["bank_dispatch_attempts"]
    if mutation == "omit_first":
        inventory["data"].pop(0)
    elif mutation == "wrong_first_key":
        first["provider_request"]["idempotency_key"] = "manual-first-key"
    elif mutation == "wrong_first_amount":
        first["provider_request"]["parameters"]["amount"] += 1
    elif mutation == "stale_retry_read":
        shift_evidence_times(last["readiness"], -timedelta(minutes=6), "-stale")
    elif mutation == "missing_retry_read":
        last.pop("readiness")
    elif mutation == "reorder":
        inventory["data"].reverse()
    elif mutation == "duplicate_attempt":
        last["attempt_id"] = first["attempt_id"]
    elif mutation == "count":
        row["bank_dispatch_checkpoint"]["attempt_count"] = 1
    elif mutation == "incomplete":
        inventory["complete"] = False
    elif mutation == "filtered":
        inventory["filters"] = {"outcome": "succeeded"}
    elif mutation == "different_plan":
        first["plan"]["bank_id"] = "ba_other"
    elif mutation == "provider_request_reuse":
        first["provider_request"]["request_id"] = last["provider_request"]["request_id"]
    else:
        row["bank_dispatch_checkpoint"]["provider_attempt_id"] = first["attempt_id"]
    assert check(bundle)


@pytest.mark.parametrize("field", ["plan_id", "operation_id"])
@pytest.mark.parametrize("form", ["hex", "upper", "braces", "urn", "canonical"])
def test_bank_uuid_alias_cannot_bypass_changed_authority_reuse(bundle, field, form):
    from uuid import UUID

    first, row = bundle["gateway_runs"][:2]
    original = first["bank_dispatch_plan"][field]
    value = {
        "hex": UUID(original).hex,
        "upper": original.upper(),
        "braces": "{" + original + "}",
        "urn": UUID(original).urn,
        "canonical": original,
    }[form]
    row["bank_dispatch_plan"][field] = row["bank_dispatch_checkpoint"][field] = value
    row["bank_payout"]["metadata"][field] = value
    row["bank_dispatch_checkpoint"]["provider_request"]["parameters"]["metadata"][
        field
    ] = value
    row["bank_dispatch_attempts"]["plan"][field] = value
    for attempt in row["bank_dispatch_attempts"]["data"]:
        attempt["plan"][field] = value
    errors = check(bundle)
    assert any(
        "bank plan/operation/key reused" in e
        if form == "canonical"
        else "persisted scope-bound bank plan" in e
        for e in errors
    )


@pytest.mark.parametrize("group", ["gateway_runs", "stripe_runs", "failed_bank_payout"])
@pytest.mark.parametrize(
    "case",
    [
        "same_second",
        "provider_before_attempt_second",
        "read_after_attempt",
        "request_before_attempt",
    ],
)
def test_bank_native_creation_precision_preserves_precise_owner_order(
    bundle, group, case
):
    row = financial_row(bundle, group)
    payout = row if group == "failed_bank_payout" else row["bank_payout"]
    native = datetime.fromisoformat(payout["created_at"])
    ready = row["payout_readiness"]
    delta = (
        native
        + timedelta(microseconds=500000)
        - datetime.fromisoformat(ready["recorded_at"])
    )
    shift_evidence_times(ready, delta, "-fractional")
    row["bank_dispatch_plan"]["recorded_at"] = (
        native + timedelta(microseconds=600000)
    ).isoformat()
    checkpoint = row["bank_dispatch_checkpoint"]
    checkpoint["attempted_at"] = (native + timedelta(microseconds=700000)).isoformat()
    inventory = row["bank_dispatch_attempts"]
    inventory["window_start_at"] = row["bank_dispatch_plan"]["recorded_at"]
    inventory["data"][0]["started_at"] = checkpoint["attempted_at"]
    if case == "provider_before_attempt_second":
        payout["created_at"] = (native - timedelta(seconds=1)).isoformat()
    elif case == "read_after_attempt":
        ready["recorded_at"] = (native + timedelta(microseconds=800000)).isoformat()
    elif case == "request_before_attempt":
        checkpoint["provider_request"]["recorded_at"] = (
            native + timedelta(microseconds=650000)
        ).isoformat()
    if case == "same_second":
        assert check(bundle) == []
    else:
        assert check(bundle)


def bank_read_only_recovery(row):
    payout = row.get("bank_payout", row)
    checkpoint = row["bank_dispatch_checkpoint"]
    inventory = row["bank_dispatch_attempts"]
    attempt = inventory["data"][0]
    checkpoint["provider_request"]["request_id"] = None
    attempt["provider_request"] = deepcopy(checkpoint["provider_request"])
    attempt["provider_request"]["payout_id"] = None
    attempt.update(outcome="reconciled", error_type="TimeoutError")
    bind_bank_failure(attempt)
    attempt["reconciliation"] = record(
        "payout-lookup-" + row["id"],
        recorded_at=(
            datetime.fromisoformat(attempt["recorded_at"])
            + timedelta(microseconds=500000)
        ).isoformat(),
        source="stripe_request",
        request_id="req_lookup" + row["id"].replace("-", ""),
        method="GET",
        path=f"/v1/payouts/{payout['payout_id']}",
        account_id=payout["account_id"],
        livemode=False,
        payout_id=payout["payout_id"],
        payout={
            "id": payout["payout_id"],
            "object": "payout",
            "automatic": False,
            "amount": payout["amount_cents"],
            "currency": "usd",
            "livemode": False,
            "destination": payout["bank_id"],
            "source_type": payout["source_type"],
            "metadata": deepcopy(payout["metadata"]),
        },
    )
    lookup = attempt["reconciliation"]
    native_created = int(datetime.fromisoformat(payout["created_at"]).timestamp())
    lookup["creation_event"] = record(
        "payout-created-event-" + row["id"],
        recorded_at=lookup["recorded_at"],
        source="stripe_event",
        attempt_id=attempt["attempt_id"],
        event={
            "id": "evt_created" + row["id"].replace("-", ""),
            "object": "event",
            "type": "payout.created",
            "livemode": False,
            "account": payout["account_id"],
            "created": native_created,
            "request": {
                "id": "req_created" + row["id"].replace("-", ""),
                "idempotency_key": checkpoint["idempotency_key"],
            },
            "data": {
                "object": dict(deepcopy(lookup["payout"]), created=native_created)
            },
        },
    )
    return attempt["reconciliation"]


@pytest.mark.parametrize("group", ["gateway_runs", "stripe_runs", "failed_bank_payout"])
def test_bank_lost_response_can_reconcile_without_another_post(bundle, group):
    row = financial_row(bundle, group)
    bank_read_only_recovery(row)
    assert row["bank_dispatch_attempts"]["total_count"] == 1
    assert check(bundle) == []


@pytest.mark.parametrize(
    "field,value",
    [
        ("method", "POST"),
        ("payout_id", "po_other"),
        ("account_id", "acct_other"),
        ("request_id", None),
        ("payout", {}),
        ("recorded_at", "2026-10-12T00:01:00+00:00"),
    ],
)
def test_bank_read_only_recovery_requires_bound_provider_proof(bundle, field, value):
    lookup = bank_read_only_recovery(bundle["gateway_runs"][0])
    lookup[field] = value
    assert check(bundle)


@pytest.mark.parametrize("offset,valid", [(-1, False), (0, False), (1, True)])
def test_standalone_refund_recovery_follows_final_fiat_rollout(bundle, offset, valid):
    at = datetime.fromisoformat(bundle["fiat_rollout"]["recorded_at"])
    bundle["refund_recovery"]["started_at"] = (
        at + timedelta(microseconds=offset)
    ).isoformat()
    if valid:
        assert check(bundle) == []
    else:
        assert any("including refund_recovery" in e for e in check(bundle))


def test_retry_cannot_relabel_the_original_readiness_even_within_five_minutes(bundle):
    row = bundle["gateway_runs"][0]
    _first, last = bank_timeout_then_retry(row)
    last["readiness"] = deepcopy(row["payout_readiness"])
    shift_evidence_times(last["readiness"], timedelta(0), "-relabeled")
    assert any(
        "new readiness observation after the prior journal outcome" in e
        for e in check(bundle)
    )


def test_complete_bank_attempt_inventory_can_be_collected_after_initial_paid_observation(
    bundle,
):
    row = bundle["gateway_runs"][0]
    paid = datetime.fromisoformat(row["bank_payout"]["recorded_at"])
    row["bank_dispatch_attempts"]["recorded_at"] = (
        paid + timedelta(microseconds=1)
    ).isoformat()
    assert check(bundle) == []


@pytest.mark.parametrize(
    "group", ["gateway_runs", "stripe_runs", "failed_bank_payout", "refund_recovery"]
)
@pytest.mark.parametrize(
    "field,value",
    [
        ("object", "garbage"),
        ("amount_captured", 1),
        ("amount_captured", True),
        ("created", 1),
        ("created", True),
        ("refunded", True),
    ],
)
def test_connect_recheck_preserves_original_charge_snapshot(
    bundle, group, field, value
):
    row = financial_row(bundle, group)
    row["connect_dispatch"]["source_recheck"]["charge"][field] = value
    assert any("source-charge recheck" in e for e in check(bundle))


@pytest.mark.parametrize(
    "group", ["gateway_runs", "stripe_runs", "failed_bank_payout", "refund_recovery"]
)
@pytest.mark.parametrize("offset,valid", [(-1, False), (0, True), (1, True)])
def test_dispatch_opt_in_is_part_of_this_journey(bundle, group, offset, valid):
    row = financial_row(bundle, group)
    start = datetime.fromisoformat(row["started_at"])
    row["payment"]["dispatch_checkpoint"]["recorded_at"] = (
        start + timedelta(microseconds=offset)
    ).isoformat()
    if valid:
        assert check(bundle) == []
    else:
        assert any(
            "payment dispatch opt-in must follow journey start" in e
            for e in check(bundle)
        )


@pytest.mark.parametrize(
    "group", ["gateway_runs", "stripe_runs", "failed_bank_payout", "refund_recovery"]
)
@pytest.mark.parametrize("field", ["id", "revision", "url", "passed", "all"])
def test_every_transfer_requires_authenticated_readiness_evidence(bundle, group, field):
    ready = financial_row(bundle, group)["readiness"]
    for key in ("id", "revision", "url", "passed") if field == "all" else (field,):
        ready.pop(key)
    assert any(".readiness:" in e for e in check(bundle))


@pytest.mark.parametrize("group", ["gateway_runs", "stripe_runs", "failed_bank_payout"])
def test_bank_provider_refusal_retains_actual_error_response_before_retry(
    bundle, group
):
    first, _last = bank_timeout_then_retry(financial_row(bundle, group))
    first["outcome"] = "provider_error"
    first["provider_request"]["request_id"] = "req_initialrefusal" + group.replace(
        "_", ""
    )
    bind_bank_failure(first, provider=True)
    assert check(bundle) == []


@pytest.mark.parametrize(
    "mutation",
    [
        "missing",
        "type",
        "attempt",
        "plan",
        "start",
        "request",
        "response",
        "received",
        "time",
        "provenance",
    ],
)
def test_transport_outcome_requires_bound_exception_artifact(bundle, mutation):
    first, _last = bank_timeout_then_retry(bundle["gateway_runs"][0])
    failure = first["failure"]
    if mutation == "missing":
        first.pop("failure")
    elif mutation == "type":
        first["error_type"] = failure["error_type"] = "ValueError"
    elif mutation == "attempt":
        failure["attempt_id"] = "other"
    elif mutation == "plan":
        failure["plan"]["amount_cents"] += 1
    elif mutation == "start":
        failure["started_at"] = first["recorded_at"]
    elif mutation == "request":
        failure["request_id"] = "req_unrelated"
    elif mutation == "response":
        failure["response"] = {"payout_id": "po_success"}
    elif mutation == "received":
        failure["response_received"] = True
    elif mutation == "time":
        failure["recorded_at"] = "2026-10-12T00:00:01+00:00"
    else:
        failure["passed"] = False
    assert check(bundle)


@pytest.mark.parametrize(
    "mutation",
    [
        "response",
        "request_id",
        "code",
        "missing_code",
        "status",
        "type",
        "exception",
        "account",
        "key",
        "provenance",
        "time",
    ],
)
def test_provider_error_outcome_requires_bound_response_and_request(bundle, mutation):
    first, _last = bank_timeout_then_retry(bundle["gateway_runs"][0])
    first["outcome"] = "provider_error"
    first["provider_request"]["request_id"] = "req_initialrefusal"
    bind_bank_failure(first, provider=True)
    failure, response = first["failure"], first["failure"]["response"]
    if mutation == "response":
        failure.pop("response")
    elif mutation == "request_id":
        first["provider_request"]["request_id"] = failure["request_id"] = response[
            "request_id"
        ] = None
    elif mutation == "code":
        failure["code"] = "different"
    elif mutation == "missing_code":
        response["error"].pop("code")
    elif mutation == "status":
        response["http_status"] = 200
    elif mutation == "type":
        response["error"]["type"] = "payout"
    elif mutation == "exception":
        first["error_type"] = failure["error_type"] = "TimeoutError"
    elif mutation == "account":
        response["account_id"] = "acct_other"
    elif mutation == "key":
        response["idempotency_key"] = "other"
    elif mutation == "provenance":
        response["passed"] = False
    else:
        response["recorded_at"] = "2026-10-12T00:00:01+00:00"
    assert check(bundle)


def delay_bank_completion(row, delta):
    checkpoint = row["bank_dispatch_checkpoint"]
    inventory = row["bank_dispatch_attempts"]
    payout = row["bank_payout"]
    funding = row["bank_funding"]
    for obj in (
        row,
        checkpoint,
        inventory,
        payout,
        funding,
        funding["closing_balance"],
    ):
        obj["recorded_at"] = (
            datetime.fromisoformat(obj["recorded_at"]) + delta
        ).isoformat()
    inventory["window_end_at"] = checkpoint["recorded_at"]
    funding["window_end_at"] = funding["closing_balance"]["recorded_at"]
    followup = row["bank_followup"]
    floor = checker.payout_observation_deadline(
        max(
            datetime.fromisoformat(payout["recorded_at"]),
            datetime.fromisoformat(payout["arrival_at"]),
        )
    )
    observed = max(
        datetime.fromisoformat(followup["recorded_at"]), floor + timedelta(hours=1)
    )
    followup["recorded_at"] = followup["events_through_at"] = observed.isoformat()
    bind_platform_payout_inventory(row, payout)


@pytest.mark.parametrize(
    "age,valid",
    [
        (timedelta(hours=23) - timedelta(microseconds=1), True),
        (timedelta(hours=23), False),
        (timedelta(days=2), False),
    ],
)
def test_bank_retries_use_original_safe_idempotency_window(bundle, age, valid):
    row = bundle["gateway_runs"][-1]
    first, last = bank_timeout_then_retry(row)
    delta = age - (
        datetime.fromisoformat(last["started_at"])
        - datetime.fromisoformat(first["started_at"])
    )
    delay_bank_completion(row, delta)
    bundle["test_credit_removed"]["recorded_at"] = "2026-10-26T11:00:00+00:00"
    last["started_at"] = (
        datetime.fromisoformat(last["started_at"]) + delta
    ).isoformat()
    last["recorded_at"] = (
        datetime.fromisoformat(last["recorded_at"]) + delta
    ).isoformat()
    last["provider_request"]["recorded_at"] = (
        datetime.fromisoformat(last["provider_request"]["recorded_at"]) + delta
    ).isoformat()
    shift_evidence_times(last["readiness"], delta, "-late-retry")
    if valid:
        assert check(bundle) == []
    else:
        assert any("original 23-hour retry window" in e for e in check(bundle))


@pytest.mark.parametrize("index", [0, 1])
@pytest.mark.parametrize("known", [False, None])
def test_unknown_retention_cannot_authorize_even_a_fresh_bank_post(
    bundle, index, known
):
    row = bundle["gateway_runs"][0]
    bank_timeout_then_retry(row)
    row["bank_dispatch_attempts"]["data"][index]["retention_guaranteed"] = known
    assert any("known idempotency retention" in e for e in check(bundle))


def test_read_only_payout_recovery_remains_available_after_retention_expires(bundle):
    row = bundle["gateway_runs"][-1]
    lookup = bank_read_only_recovery(row)
    delta = timedelta(days=2)
    delay_bank_completion(row, delta)
    bundle["test_credit_removed"]["recorded_at"] = "2026-10-26T11:00:00+00:00"
    lookup["recorded_at"] = (
        datetime.fromisoformat(lookup["recorded_at"]) + delta
    ).isoformat()
    assert row["bank_dispatch_attempts"]["total_count"] == 1
    assert check(bundle) == []


@pytest.mark.parametrize(
    "url,valid",
    [
        ("https://\ud800.example/", False),
        ("https://\u200d.example/", False),
        ("https://b\u00fccher.example/", True),
        ("https://[::1]/return", True),
    ],
)
def test_simulated_url_hostname_matches_runtime_parser(bundle, url, valid):
    row = bundle["stripe_runs"][0]
    binding = authorize_simulated_fixture(row)
    binding["return_url"] = url
    if valid:
        assert check(bundle) == []
    else:
        assert any("simulation" in e for e in check(bundle))


@pytest.mark.parametrize(
    "field,value",
    [
        ("retained_cents", 12645),
        ("refund_due_cents", 12445),
        ("captured_cents", 12645),
        ("post_refund_host_payable_cents", 101),
        ("post_refund_platform_compute_cents", 301),
        ("reversal_due_cents", 1),
    ],
)
@pytest.mark.parametrize("group", ["gateway_runs", "failed_bank_payout"])
def test_failed_bank_must_reconcile_full_native_settlement(bundle, group, field, value):
    financial_row(bundle, group)["settlement"][field] = value
    assert any(
        "allocation" in e
        or "settlement must reconcile" in e
        or "isolated bank rejection" in e
        for e in check(bundle)
    )


@pytest.mark.parametrize(
    "field",
    [
        "delivered_compute_cents",
        "retained_tax_cents",
        "refund_compute_cents",
        "refund_tax_cents",
        "refund_total_cents",
    ],
)
@pytest.mark.parametrize("group", ["gateway_runs", "failed_bank_payout"])
def test_failed_bank_cannot_hide_inconsistent_tax_or_refund_allocation(
    bundle, group, field
):
    financial_row(bundle, group)["settlement"]["tax_allocation"][field] += 1
    assert any("compute-only allocation" in e for e in check(bundle))


@pytest.mark.parametrize(
    "fields", [("lease_id",), ("billing_record_id",), ("lease_id", "billing_record_id")]
)
def test_gateway_and_stripe_cannot_count_the_same_billing_journey_twice(bundle, fields):
    gateway, stripe = bundle["gateway_runs"][-1], bundle["stripe_runs"][-1]
    changes = {stripe[field]: gateway[field] for field in fields}

    def replace(value):
        if isinstance(value, str):
            for old, new in changes.items():
                value = value.replace(old, new)
            return value
        if isinstance(value, dict):
            return {k: replace(v) for k, v in value.items()}
        if isinstance(value, list):
            return [replace(v) for v in value]
        return value

    bundle["stripe_runs"][-1] = replace(stripe)
    errors = check(bundle)
    assert len(errors) == len(fields)
    assert all("across gateway and Stripe journeys" in e for e in errors)


@pytest.mark.parametrize("stage", ["enrollments_before", "enrollments_after"])
@pytest.mark.parametrize(
    "owner", [None, "", " padded-owner ", "owner name", "owner\x00subject"]
)
def test_every_historical_enrollment_requires_canonical_owner(bundle, stage, owner):
    add_historical_usdc_enrollment(bundle)
    bundle["fiat_rollout"][stage]["data"][-1]["owner_sub"] = owner
    assert any("canonical owner_sub" in e for e in check(bundle))


def test_rollout_preserves_owners_beyond_the_selected_host(bundle):
    add_historical_usdc_enrollment(bundle)
    assert check(bundle) == []
    bundle["fiat_rollout"]["enrollments_after"]["data"][-1]["owner_sub"] = (
        "replacement-owner"
    )
    assert any("history was lost or replaced" in e for e in check(bundle))


def test_inventory_owner_must_match_the_enrolled_owners_authorization(bundle):
    for stage in ("enrollments_before", "enrollments_after"):
        bundle["fiat_rollout"][stage]["data"][0]["owner_sub"] = "unrelated-owner"
    assert any("subject-to-host ownership snapshot" in e for e in check(bundle))


@pytest.mark.parametrize(
    "remaining,valid", [(1, False), (1859, False), (1860, True), (1861, True)]
)
def test_checkout_quote_has_native_initial_lifetime(bundle, remaining, valid):
    row = non_stablecoin_gateway_fixture(bundle)
    payment = row["payment"]
    payment["tax_payable"]["expires_at"] = (
        int(datetime.fromisoformat(payment["checkout_created_at"]).timestamp())
        + remaining
    )
    errors = check(bundle)
    if valid:
        assert any("native lease adapter is not implemented" in e for e in errors)
    else:
        assert any("31 minutes" in error for error in errors)


@pytest.mark.parametrize("group", ["gateway_runs", "stripe_runs", "failed_bank_payout"])
@pytest.mark.parametrize("automatic", [True, None, 0])
def test_bank_payout_requires_native_manual_origin(bundle, group, automatic):
    row = financial_row(bundle, group)
    row.get("bank_payout", row)["automatic"] = automatic
    assert check(bundle)


@pytest.mark.parametrize("automatic", [True, None, 0])
def test_read_only_payout_recovery_requires_manual_provider_object(bundle, automatic):
    lookup = bank_read_only_recovery(bundle["gateway_runs"][0])
    lookup["payout"]["automatic"] = automatic
    assert any("reconciliation" in e for e in check(bundle))


@pytest.mark.parametrize("group", ["gateway_runs", "stripe_runs", "failed_bank_payout"])
def test_coherent_bank_change_cannot_redirect_frozen_transfer(bundle, group):
    row = financial_row(bundle, group)
    original_ready = deepcopy(row["readiness"])
    replace_tax_revision_everywhere(row, original_ready["bank_id"], "ba_replacement")
    row["readiness"] = original_ready
    assert any("frozen owner/account/bank destination" in e for e in check(bundle))


@pytest.mark.parametrize("group", ["gateway_runs", "stripe_runs", "failed_bank_payout"])
@pytest.mark.parametrize("offset,valid", [(-1, False), (0, True), (1, True)])
def test_verified_payer_snapshot_belongs_to_current_journey(
    bundle, group, offset, valid
):
    row = financial_row(bundle, group)
    row["payment"]["payer_snapshot"]["recorded_at"] = (
        datetime.fromisoformat(row["started_at"]) + timedelta(seconds=offset)
    ).isoformat()
    if valid:
        assert check(bundle) == []
    else:
        assert any("snapshot must follow journey start" in e for e in check(bundle))


def test_failed_deposit_event_cannot_replace_native_zero_with_boolean(bundle):
    row = bundle["failed_deposit"]
    row["provider_event"]["data"]["object"]["amount_received"] = False
    assert any(
        "bound test-mode USD intent and provider event" in e for e in check(bundle)
    )


def test_gateway_cannot_omit_due_refund_and_pay_original_host_share(bundle):
    row = bundle["gateway_runs"][0]
    row["settlement"].update(
        retained_cents=300,
        refund_due_cents=100,
        post_refund_host_payable_cents=80,
        post_refund_platform_compute_cents=220,
        reversal_due_cents=20,
    )
    row["settlement"]["tax_allocation"].update(
        delivered_compute_cents=300, refund_compute_cents=100, refund_total_cents=100
    )
    assert any("requires a complete refund journey" in e for e in check(bundle))


def preserve_host_share(row):
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
    row["settlement"].update(
        reversal_due_cents=0,
        post_refund_host_payable_cents=100,
        post_refund_platform_compute_cents=200,
    )
    sync_bank_to_outcome(row)


@pytest.mark.parametrize("reversals", [0, 1])
def test_acceptance_requires_actual_successful_reversal_not_only_balance_refusal(
    bundle, reversals
):
    for row in bundle["stripe_runs"][reversals:]:
        preserve_host_share(row)
    if reversals:
        assert check(bundle) == []
    else:
        assert check(bundle) == [
            "requires at least one successful transfer reversal and residual bank payout in accepted journeys"
        ]


def test_taxed_mismatch_plan_cannot_replace_zero_allocation_with_boolean(bundle):
    row = bundle["repeated_mismatch"]["attempts"][0]
    # Zero-tax quotes are still native frozen tax allocations, with typed cents.
    row["tax_payable"] = deepcopy(bundle["stripe_runs"][0]["payment"]["tax_payable"])
    row["tax_payable"].update(
        subtotal_cents=row["amount_cents"], total_cents=row["amount_cents"]
    )
    row.update(
        tax_revision_id=row["tax_payable"]["revision_id"],
        tax_calculation_id=row["tax_payable"]["calculation_id"],
    )
    row["intent"]["metadata"].update(
        tax_revision_id=row["tax_payable"]["revision_id"],
        tax_calculation_id=row["tax_payable"]["calculation_id"],
    )
    row["provider_event"]["data"]["object"] = deepcopy(row["intent"])
    plan = row["refund_plan"]["plan"]
    plan.update(
        stripe_tax_revision_id=row["tax_payable"]["revision_id"],
        stripe_tax_calculation_id=row["tax_payable"]["calculation_id"],
        stripe_captured_total_cents=row["amount_cents"],
        stripe_refund_total_cents=row["amount_cents"],
        stripe_tax_allocation={
            "delivered_compute_cents": 0,
            "retained_tax_cents": 0,
            "refund_compute_cents": row["amount_cents"],
            "refund_tax_cents": 0,
            "refund_total_cents": row["amount_cents"],
            "allocation_policy": "EXAMPLE-compute-proportional-tax-v1",
        },
    )
    bind_money_dispatch(row, mismatch=True)
    assert check(bundle) == []
    plan["stripe_tax_allocation"]["retained_tax_cents"] = False
    bind_money_dispatch(row, mismatch=True)
    assert any("frozen compute/tax refund allocation" in e for e in check(bundle))


@pytest.mark.parametrize(
    "account,valid",
    [
        ("acct_Ab012Z", True),
        ("acct_" + "a" * 250, True),
        ("acct_bad_id", False),
        ("acct__bad", False),
        ("acct_bad_", False),
        ("acct_", False),
        ("acct_" + "a" * 251, False),
        ("acct_bad-id", False),
    ],
)
def test_complete_enrollment_uses_native_account_identifier_grammar(
    bundle, account, valid
):
    replace_tax_revision_everywhere(bundle, "acct_host", account)
    if valid:
        assert check(bundle) == []
    else:
        assert check(bundle)


@pytest.mark.parametrize(
    "group", ["gateway_runs", "stripe_runs", "refund_recovery", "failed_bank_payout"]
)
def test_connect_request_cannot_substitute_float_cents(bundle, group):
    row = financial_row(bundle, group)
    parameters = row["connect_dispatch"]["provider_request"]["parameters"]
    parameters["amount"] = float(parameters["amount"])
    assert any("Connect provider request must bind native" in e for e in check(bundle))


@pytest.mark.parametrize("value", [20.0, True])
def test_reversal_inventory_must_retain_native_typed_amount(bundle, value):
    outcome = bundle["stripe_runs"][0]["refund"]["transfer_outcome"]
    outcome["reversal_inventory"]["data"][0]["amount"] = value
    assert type(outcome["provider_reversal"]["amount"]) is int
    assert any("exactly one provider reversal" in e for e in check(bundle))


@pytest.mark.parametrize(
    "group", ["gateway_runs", "stripe_runs", "refund_recovery", "failed_bank_payout"]
)
def test_transfer_inventory_cannot_omit_charge_lifetime_before_owner_attempt(
    bundle, group
):
    row = financial_row(bundle, group)
    inventory = row["transfer_inventory"]
    attempted = row["connect_dispatch"]["prepared"]["state"]["attempted_at"]
    assert datetime.fromisoformat(
        inventory["window_start_at"]
    ) < datetime.fromisoformat(attempted)
    inventory["window_start_at"] = attempted
    inventory["filters"]["created"]["gte"] = int(
        datetime.fromisoformat(attempted).timestamp()
    )
    assert any("source charge creation" in e for e in check(bundle))


@pytest.mark.parametrize("tie", ["charge", "billing", "lease"])
@pytest.mark.parametrize(
    "group", ["gateway_runs", "stripe_runs", "refund_recovery", "failed_bank_payout"]
)
def test_complete_charge_lifetime_inventory_exposes_pre_attempt_duplicate(
    bundle, tie, group
):
    row = financial_row(bundle, group)
    inventory = row["transfer_inventory"]
    assert check(bundle) == []
    earlier = deepcopy(inventory["data"][0])
    earlier.update(
        id="tr_beforeOwnerAttempt",
        destination="acct_other",
        source_transaction="ch_unrelated",
        metadata={},
    )
    earlier["created_at"] = (
        datetime.fromtimestamp(row["payment"]["charge"]["created"], UTC)
        + timedelta(seconds=1)
    ).isoformat()
    assert datetime.fromisoformat(earlier["created_at"]) < datetime.fromisoformat(
        row["connect_dispatch"]["prepared"]["state"]["attempted_at"]
    )
    if tie == "charge":
        earlier["source_transaction"] = row["payment"]["charge_id"]
    else:
        field = "billing_record_id" if tie == "billing" else "lease_id"
        earlier["metadata"][field] = row[field]
    inventory["data"].append(earlier)
    assert any("exactly one provider transfer" in e for e in check(bundle))


@pytest.mark.parametrize(
    "target_group",
    ["gateway_runs", "stripe_runs", "refund_recovery", "failed_bank_payout"],
)
@pytest.mark.parametrize("suffix", ["shared", "shared_payment"])
def test_destination_payment_is_unique_across_all_financial_scopes(
    bundle, target_group, suffix
):
    first = bundle["gateway_runs"][0]
    other = (
        bundle["gateway_runs"][1]
        if target_group == "gateway_runs"
        else financial_row(bundle, target_group)
    )
    shared = "py_" + suffix
    for row in [first, other]:
        original = row["connect_transfer"]["destination_payment"]
        replace_tax_revision_everywhere(row, original, shared)
    assert any(
        "destination payment reused across financial scopes" in e for e in check(bundle)
    )


@pytest.mark.parametrize("value", [400.0, True, None])
def test_receipt_tax_provenance_requires_native_integer_cents(bundle, value):
    bundle["receipt"]["frozen_tax_calculation"]["amount_cents"] = value
    assert any("frozen tax calculation policy provenance" in e for e in check(bundle))


@pytest.mark.parametrize(
    "mutation",
    [
        "missing",
        "missing_origin",
        "wrong_source",
        "wrong_type",
        "wrong_account",
        "live",
        "wrong_key",
        "wrong_request",
        "no_request",
        "wrong_payout",
        "float_amount",
        "automatic",
        "mutated_metadata",
        "bad_created",
        "before_post",
        "after_lookup",
    ],
)
def test_lost_post_recovery_requires_immutable_creation_request_lineage(
    bundle, mutation
):
    row = bundle["gateway_runs"][0]
    lookup = bank_read_only_recovery(row)
    creation = lookup["creation_event"]
    event = creation["event"]
    if mutation == "missing":
        lookup.pop("creation_event")
    elif mutation == "missing_origin":
        creation.pop("attempt_id")
    elif mutation == "wrong_source":
        creation["source"] = "owner_log"
    elif mutation == "wrong_type":
        event["type"] = "payout.updated"
    elif mutation == "wrong_account":
        event["account"] = "acct_other"
    elif mutation == "live":
        event["livemode"] = True
    elif mutation == "wrong_key":
        event["request"]["idempotency_key"] = "different-manual-call"
    elif mutation == "wrong_request":
        row["bank_dispatch_checkpoint"]["provider_request"]["request_id"] = "req_known"
        row["bank_dispatch_attempts"]["data"][0]["provider_request"]["request_id"] = (
            "req_known"
        )
        bind_bank_failure(row["bank_dispatch_attempts"]["data"][0])
    elif mutation == "no_request":
        event["request"] = None
    elif mutation == "wrong_payout":
        event["data"]["object"]["id"] = "po_manualOther"
    elif mutation == "float_amount":
        event["data"]["object"]["amount"] = float(event["data"]["object"]["amount"])
    elif mutation == "automatic":
        event["data"]["object"]["automatic"] = True
    elif mutation == "mutated_metadata":
        event["data"]["object"]["metadata"] = {}
    elif mutation == "bad_created":
        event["data"]["object"]["created"] = False
    elif mutation == "before_post":
        event["data"]["object"]["created"] -= 60
    elif mutation == "after_lookup":
        creation["recorded_at"] = (
            datetime.fromisoformat(lookup["recorded_at"]) + timedelta(seconds=1)
        ).isoformat()
    assert any("lineage" in e for e in check(bundle))


def test_creation_event_may_follow_payout_creation_within_same_original_request(bundle):
    row = bundle["gateway_runs"][0]
    lookup = bank_read_only_recovery(row)
    lookup["creation_event"]["event"]["created"] += 1
    assert check(bundle) == []


@pytest.mark.parametrize(
    "field",
    [
        "bank_payout",
        "bank_funding",
        "bank_followup",
        "bank_dispatch_plan",
        "bank_dispatch_checkpoint",
    ],
)
def test_standalone_recovery_requires_prior_completed_bank_debit(bundle, field):
    bundle["refund_recovery"].pop(field)
    assert any("refund_recovery" in e for e in check(bundle))


@pytest.mark.parametrize(
    "mutation",
    [
        "pending_credit",
        "late_availability",
        "missing_debit",
        "unrelated_debit",
        "pending_payout",
        "refund_known_before_payout",
        "refused_before_debit",
    ],
)
def test_standalone_loss_cannot_use_pending_transfer_liquidity(bundle, mutation):
    row = bundle["refund_recovery"]
    funding = row["bank_funding"]
    credit, debit = funding["transactions"][:2]
    if mutation == "pending_credit":
        credit["status"] = "pending"
    elif mutation == "late_availability":
        credit["available_at"] = row["recorded_at"]
    elif mutation == "missing_debit":
        funding["transactions"].remove(debit)
    elif mutation == "unrelated_debit":
        debit["source"] = "po_unrelated"
    elif mutation == "pending_payout":
        row["bank_payout"]["status"] = "pending"
    elif mutation == "refund_known_before_payout":
        row["refund"]["discovery"]["recorded_at"] = row["bank_payout"]["created_at"]
    elif mutation == "refused_before_debit":
        row["reversal_refusal"]["recorded_at"] = credit["created_at"]
    assert any("refund_recovery" in e for e in check(bundle))


def test_lost_payout_response_can_recover_with_known_matching_request_id(bundle):
    row = bundle["gateway_runs"][0]
    lookup = bank_read_only_recovery(row)
    request_id = lookup["creation_event"]["event"]["request"]["id"]
    row["bank_dispatch_checkpoint"]["provider_request"]["request_id"] = request_id
    attempt = row["bank_dispatch_attempts"]["data"][0]
    attempt["provider_request"]["request_id"] = request_id
    bind_bank_failure(attempt)
    assert check(bundle) == []


@pytest.mark.parametrize("identity", ["event", "request"])
def test_provider_payout_creation_lineage_cannot_cross_financial_plans(
    bundle, identity
):
    first, second = [bank_read_only_recovery(row) for row in bundle["gateway_runs"][:2]]
    assert check(bundle) == []
    original, other = (
        first["creation_event"]["event"],
        second["creation_event"]["event"],
    )
    if identity == "event":
        other["id"] = original["id"]
    else:
        other["request"]["id"] = original["request"]["id"]
    assert any("lineage reused across plans" in e for e in check(bundle))


@pytest.mark.parametrize(
    "group", ["gateway_runs", "stripe_runs", "refund_recovery", "failed_bank_payout"]
)
@pytest.mark.parametrize(
    "mutation", ["missing", "scope", "operation", "tax_float", "late", "url"]
)
def test_every_real_hash_capture_requires_its_native_saved_binding(
    bundle, group, mutation
):
    row = financial_row(bundle, group)
    artifact = row["payment"]["deposit_binding"]
    binding = artifact["binding"]
    if mutation == "missing":
        row["payment"].pop("deposit_binding")
    elif mutation == "scope":
        binding["billing_record_id"] = "other"
    elif mutation == "operation":
        binding["operation_id"] = "manual"
    elif mutation == "tax_float":
        binding["tax_payable"]["tax_cents"] = 0.0
    elif mutation == "late":
        artifact["recorded_at"] = row["recorded_at"]
    elif mutation == "url":
        binding["return_url"] = "http://example.org/return"
    assert any("saved deposit binding" in e for e in check(bundle))


@pytest.mark.parametrize("field,value", [("tax_cents", 0.0), ("version", True)])
def test_simulation_cannot_authorize_a_changed_typed_saved_tax_snapshot(
    bundle, field, value
):
    row = bundle["stripe_runs"][0]
    binding = authorize_simulated_fixture(row)
    binding["tax_payable"][field] = value
    assert any("owner-authorized sandbox simulation" in e for e in check(bundle))


@pytest.mark.parametrize(
    "field", ["transfer_data", "on_behalf_of", "application_fee_amount"]
)
def test_checkout_cannot_claim_platform_charge_with_connect_fields(bundle, field):
    row = non_stablecoin_gateway_fixture(bundle)
    row["payment"]["intent"][field] = (
        0
        if field == "application_fee_amount"
        else {"destination": "acct_other"}
        if field == "transfer_data"
        else "acct_other"
    )
    row["payment"]["capture_event"]["data"]["object"] = deepcopy(
        row["payment"]["intent"]
    )
    assert any("platform charge binding" in e for e in check(bundle))


@pytest.mark.parametrize("field", ["payer_snapshot", "actor_sub"])
def test_checkout_also_requires_authenticated_current_payer_snapshot(bundle, field):
    row = non_stablecoin_gateway_fixture(bundle)
    row["payment"].pop(field)
    assert any("verified payer snapshot" in e for e in check(bundle))


@pytest.mark.parametrize(
    "group", ["gateway_runs", "stripe_runs", "refund_recovery", "failed_bank_payout"]
)
def test_source_recheck_amount_is_native_integer_cents(bundle, group):
    financial_row(bundle, group)["connect_dispatch"]["source_recheck"][
        "amount_cents"
    ] = 100.0
    assert any("Connect source-charge recheck" in e for e in check(bundle))


@pytest.mark.parametrize(
    "group", ["gateway_runs", "stripe_runs", "refund_recovery", "failed_bank_payout"]
)
def test_transfer_inventory_retains_native_boolean_mode(bundle, group):
    financial_row(bundle, group)["transfer_inventory"]["data"][0]["livemode"] = 0
    assert any("exactly one provider transfer" in e for e in check(bundle))


def test_refund_plan_cannot_precede_its_authoritative_capture(bundle):
    row = bundle["stripe_runs"][0]
    plan = row["refund"]["dispatch_plan"]
    before = (
        datetime.fromisoformat(row["payment"]["capture_event"]["created_at"])
        - timedelta(seconds=1)
    ).isoformat()
    plan["attempted_at"] = plan["recorded_at"] = before
    row["refund"]["dispatch_checkpoint"]["attempted_at"] = before
    assert any("original checkout/payment chronology" in e for e in check(bundle))


@pytest.mark.parametrize("other", ["refund", "reversal", "bank", "lookup"])
def test_stripe_request_ids_use_one_namespace_across_http_endpoints(bundle, other):
    row = bundle["stripe_runs"][0]
    identity = row["connect_dispatch"]["provider_request"]["request_id"]
    if other == "refund":
        request = row["refund"]["dispatch_checkpoint"]["provider_request"]
    elif other == "reversal":
        request = row["refund"]["transfer_outcome"]["owner_reversal"][
            "dispatch_checkpoint"
        ]["provider_request"]
    elif other == "bank":
        request = row["bank_dispatch_checkpoint"]["provider_request"]
    else:
        request = bank_read_only_recovery(row)
    request["request_id"] = identity
    assert any(
        "provider request ID reused across different HTTP operations" in e
        for e in check(bundle)
    )


@pytest.mark.parametrize("group", ["refund_recovery", "failed_bank_payout"])
def test_nonstablecoin_special_exercises_require_491_closure_even_while_adapter_is_blocked(
    bundle, group
):
    row = bundle[group]
    row["source_charge_type"] = "non_stablecoin"
    assert any("conditional #491" in e for e in check(bundle))
    bundle["closed_issues"].append(491)
    assert not any("conditional #491" in e for e in check(bundle))
    assert any("native lease adapter is not implemented" in e for e in check(bundle))


@pytest.mark.parametrize(
    "field",
    ["lease_id", "billing_record_id", "payment_intent_id", "charge_id", "event_id"],
)
def test_failed_bank_cannot_reuse_any_refusal_financial_identity(bundle, field):
    failed = bundle["failed_bank_payout"]
    refusal = bundle["repeated_mismatch"]["attempts"][0]
    if field in ("lease_id", "billing_record_id"):
        failed[field] = refusal[field]
    elif field == "event_id":
        failed["payment"]["capture_event"]["id"] = refusal["provider_event"]["id"]
    else:
        failed["payment"][field] = refusal["intent"][
            "id" if field == "payment_intent_id" else "latest_charge"
        ]
    assert any("must be distinct from refusal attempts" in e for e in check(bundle))


@pytest.mark.parametrize(
    "tie",
    [
        "plan_id",
        "operation_id",
        "charge_id",
        "transfer_id",
        "lease_id",
        "billing_record_id",
    ],
)
@pytest.mark.parametrize(
    "group", ["gateway_runs", "stripe_runs", "refund_recovery", "failed_bank_payout"]
)
def test_platform_inventory_detects_related_payout_on_different_connected_account(
    bundle, group, tie
):
    row = financial_row(bundle, group)
    contexts = row["platform_payout_inventory"]["contexts"]
    duplicate = deepcopy(contexts[0]["data"][0])
    duplicate.update(
        id="po_otherAccount",
        destination="ba_other",
        metadata={tie: duplicate["metadata"][tie]},
    )
    contexts[1]["data"] = [duplicate]
    contexts[1]["total_count"] = 1
    assert any(
        "exactly one provider payout across all connected accounts" in e
        for e in check(bundle)
    )


@pytest.mark.parametrize(
    "mutation",
    [
        "missing",
        "missing_context",
        "incomplete_accounts",
        "incomplete_context",
        "narrow_window",
        "stale",
        "float_count",
    ],
)
def test_platform_inventory_requires_all_accounts_pages_and_full_window(
    bundle, mutation
):
    row = bundle["gateway_runs"][0]
    inventory = row["platform_payout_inventory"]
    if mutation == "missing":
        row.pop("platform_payout_inventory")
    elif mutation == "missing_context":
        inventory["contexts"].pop()
    elif mutation == "incomplete_accounts":
        inventory["accounts"]["has_more"] = True
    elif mutation == "incomplete_context":
        inventory["contexts"][0]["has_more"] = True
    elif mutation == "narrow_window":
        inventory["contexts"][0]["filters"]["created"]["gte"] += 1
    elif mutation == "stale":
        inventory["window_end_at"] = row["recorded_at"]
    elif mutation == "float_count":
        inventory["contexts"][0]["total_count"] = 1.0
    assert any(
        "payout inventory" in e
        or "connected-account inventory" in e
        or "native payout pages" in e
        for e in check(bundle)
    )


def test_platform_inventory_allows_unrelated_payout_in_another_account(bundle):
    contexts = bundle["gateway_runs"][0]["platform_payout_inventory"]["contexts"]
    unrelated = deepcopy(contexts[0]["data"][0])
    unrelated.update(id="po_unrelatedOtherAccount", destination="ba_other", metadata={})
    contexts[1]["data"] = [unrelated]
    contexts[1]["total_count"] = 1
    assert check(bundle) == []


@pytest.mark.parametrize("group", ["gateway_runs", "stripe_runs", "failed_bank_payout"])
@pytest.mark.parametrize("invalid_initial", [False, True])
def test_bank_initial_readiness_and_post_refresh_are_distinct(
    bundle, group, invalid_initial
):
    row = financial_row(bundle, group)
    initial = deepcopy(row["payout_readiness"])

    def rename(value):
        if isinstance(value, dict):
            if "id" in value and "revision" in value:
                value["id"] += "-initial"
            for child in value.values():
                rename(child)
        elif isinstance(value, list):
            for child in value:
                rename(child)

    rename(initial)
    plan = row["bank_dispatch_plan"]
    plan["initial_readiness"] = initial
    planned = datetime.fromisoformat(plan["recorded_at"])
    row["payout_readiness"]["recorded_at"] = (
        planned + timedelta(seconds=1)
    ).isoformat()
    if invalid_initial:
        initial["recorded_at"] = (planned + timedelta(microseconds=1)).isoformat()
        assert any(
            "bank plan must follow source transfer" in error for error in check(bundle)
        )
    else:
        assert check(bundle) == []


def test_bank_retention_keeps_the_original_prepared_plan_clock(bundle):
    row = bundle["refund_recovery"]
    started = datetime.fromisoformat(row["bank_dispatch_checkpoint"]["attempted_at"])
    row["bank_dispatch_plan"]["attempted_at"] = (
        started - timedelta(hours=23)
    ).isoformat()
    assert any(
        "23-hour retry window" in error
        for error in check(bundle, commit_time=COMMIT_TIME - timedelta(days=2))
    )


@pytest.mark.parametrize("group", ["gateway_runs", "stripe_runs", "failed_bank_payout"])
@pytest.mark.parametrize(
    "mutation",
    [
        None,
        "wrong_provider",
        "omitted_retry",
        "early_lookup",
        "wrong_lineage",
        "lookup_reuse",
        "wrong_origin",
    ],
)
def test_bank_recovery_can_bind_earlier_post_after_failed_retry(
    bundle, group, mutation
):
    row = financial_row(bundle, group)
    lookup = bank_read_only_recovery(row)
    inventory = row["bank_dispatch_attempts"]
    original = inventory["data"][0]
    prior = datetime.fromisoformat(original["recorded_at"])
    retry = deepcopy(original)
    retry.pop("reconciliation")
    retry.pop("failure")
    retry.update(
        id=original["id"] + "-later-retry",
        sequence=2,
        attempt_id=str(uuid5(NAMESPACE_URL, "late-bank-retry-" + row["id"])),
        outcome="provider_error",
        started_at=(prior + timedelta(microseconds=100000)).isoformat(),
        recorded_at=(prior + timedelta(microseconds=200000)).isoformat(),
    )
    retry["provider_request"].update(
        id=retry["provider_request"]["id"] + "-later-retry",
        request_id="req_later" + row["id"].replace("-", ""),
        payout_id=None,
        recorded_at=retry["recorded_at"],
    )
    ready = deepcopy(row["payout_readiness"])
    shift_evidence_times(
        ready,
        prior
        + timedelta(microseconds=50000)
        - datetime.fromisoformat(ready["recorded_at"]),
        "-later-retry",
    )
    retry["readiness"] = ready
    bind_bank_failure(retry, provider=True)
    inventory["data"].append(retry)
    inventory["total_count"] = row["bank_dispatch_checkpoint"]["attempt_count"] = 2
    if mutation == "wrong_provider":
        row["bank_dispatch_checkpoint"]["provider_attempt_id"] = retry["attempt_id"]
    elif mutation == "omitted_retry":
        inventory["data"].pop()
    elif mutation == "early_lookup":
        lookup["creation_event"]["recorded_at"] = (
            prior + timedelta(microseconds=150000)
        ).isoformat()
    elif mutation == "wrong_lineage":
        lookup["creation_event"]["event"]["request"]["id"] = retry["provider_request"][
            "request_id"
        ]
    elif mutation == "lookup_reuse":
        lookup["request_id"] = retry["provider_request"]["request_id"]
    elif mutation == "wrong_origin":
        lookup["creation_event"]["attempt_id"] = retry["attempt_id"]
        retry["provider_request"]["request_id"] = None
        retry.update(outcome="transport_error", error_type="TimeoutError")
        bind_bank_failure(retry)
    errors = check(bundle)
    if mutation == "wrong_origin":
        assert any(
            "immutable payout.created request/idempotency lineage" in error
            for error in errors
        )
    if mutation is None:
        assert errors == []
    else:
        assert errors


@pytest.mark.parametrize("group", ["gateway_runs", "stripe_runs", "failed_bank_payout"])
@pytest.mark.parametrize("later", ["post", "not_sent"])
def test_successful_bank_post_cannot_have_later_intents(bundle, group, later):
    row = financial_row(bundle, group)
    inventory = row["bank_dispatch_attempts"]
    original = inventory["data"][0]
    if later == "post":
        failed = deepcopy(original)
        failed.update(
            id=original["id"] + "-impossible",
            sequence=2,
            attempt_id=str(uuid5(NAMESPACE_URL, row["id"] + "impossible")),
            outcome="transport_error",
        )
        failed["provider_request"].update(
            id=original["provider_request"]["id"] + "-impossible",
            payout_id=None,
            request_id=None,
        )
        bind_bank_failure(failed)
        inventory["data"].append(failed)
        inventory["total_count"] = row["bank_dispatch_checkpoint"]["attempt_count"] = 2
    else:
        original["journal_sequence"] = 1
        row["bank_non_post_intents"] = [{"sequence": 2, "outcome": "not_sent"}]
    assert any("terminal successful POST" in error for error in check(bundle))


@pytest.mark.parametrize("group", ["gateway_runs", "stripe_runs", "failed_bank_payout"])
def test_initial_readiness_cannot_predate_plan_attempt(bundle, group):
    row = financial_row(bundle, group)
    initial = deepcopy(row["payout_readiness"])
    shift_evidence_times(initial, timedelta(0), "-prepared-initial")
    plan = row["bank_dispatch_plan"]
    plan["initial_readiness"] = initial
    plan["attempted_at"] = (
        datetime.fromisoformat(initial["recorded_at"]) + timedelta(microseconds=1)
    ).isoformat()
    assert any("initial readiness predates" in error for error in check(bundle))


@pytest.mark.parametrize("group", ["gateway_runs", "stripe_runs", "failed_bank_payout"])
@pytest.mark.parametrize(
    "mutation",
    [
        None,
        "stale_readiness",
        "late_completion",
        "duplicate_sequence",
        "missing_position",
        "wrong_plan",
        "wrong_outcome",
    ],
)
def test_retry_chronology_includes_excluded_intents(bundle, group, mutation):
    row = financial_row(bundle, group)
    first, last = bank_timeout_then_retry(row)
    first["journal_sequence"] = 1
    last["journal_sequence"] = 3
    end = datetime.fromisoformat(first["recorded_at"]) + timedelta(microseconds=100000)
    raw_plan = deepcopy(first["plan"])
    raw_plan.pop("payment_intent_id")
    raw_plan.pop("idempotency_key")
    intent = {
        "attempt_id": str(uuid5(NAMESPACE_URL, row["id"] + "-not-sent")),
        "sequence": 2,
        "outcome": "not_sent",
        "plan": raw_plan,
        "parameters": deepcopy(first["provider_request"]["parameters"]),
        "idempotency_key": first["provider_request"]["idempotency_key"],
        "started_at": first["recorded_at"],
        "completed_at": end.isoformat(),
    }
    row["bank_non_post_intents"] = [intent]
    if mutation == "stale_readiness":
        last["readiness"]["recorded_at"] = first["recorded_at"]
    elif mutation == "late_completion":
        intent["completed_at"] = last["recorded_at"]
    elif mutation == "duplicate_sequence":
        intent["sequence"] = 1
    elif mutation == "missing_position":
        last.pop("journal_sequence")
    elif mutation == "wrong_plan":
        intent["plan"]["bank_id"] = "ba_other"
    elif mutation == "wrong_outcome":
        intent["outcome"] = "uncertain"
    errors = check(bundle)
    if mutation is None:
        assert errors == []
    elif mutation == "stale_readiness":
        assert any("prior journal outcome" in error for error in errors)
    else:
        assert errors


@pytest.mark.parametrize(
    "field,value",
    [
        ("payout_method", "usdc"),
        ("payout_method", None),
        ("eligibility_rule_version", "unknown"),
        ("enrollment_attempt_id", "original"),
        ("enrollment_attempt_id", "00000000-0000-0000-0000-000000000000"),
    ],
)
def test_connect_profile_requires_bound_explicit_fiat_policy(bundle, field, value):
    row = bundle["gateway_runs"][0]
    row["readiness"]["recipient_profile"][field] = value
    for stage in ("prepared", "dispatching", "succeeded"):
        row["connect_dispatch"][stage]["state"]["profile"][field] = value
    assert any("immutable attempt/profile" in error for error in check(bundle))


def use_native_sandbox_followup(run):
    payout = run["bank_payout"]
    created = int(datetime.fromisoformat(payout["created_at"]).timestamp())
    checked = datetime.fromisoformat(payout["recorded_at"]) + timedelta(seconds=1)
    native = {
        "id": payout["payout_id"],
        "object": "payout",
        "livemode": False,
        "amount": payout["amount_cents"],
        "currency": "usd",
        "destination": payout["bank_id"],
        "status": "paid",
        "automatic": False,
        "method": "standard",
        "type": "bank_account",
        "source_type": payout["source_type"],
        "created": created,
        "metadata": deepcopy(payout["metadata"]),
    }
    followup = run["bank_followup"]
    followup.update(
        outcome_policy="stripe-sandbox-native/v1",
        native_payout=native,
        recorded_at=checked.isoformat(),
        events_through_at=checked.isoformat(),
        additional_bank_closures_accounted_for=False,
        observation_window_complete=True,
        operator_reconciliation_required=False,
        returned_funds=None,
        payout_events=[
            {
                "id": "evt_" + status,
                "object": "event",
                "livemode": False,
                "account": payout["account_id"],
                "created": created + index,
                "type": "payout." + status,
                "data": {
                    "object": deepcopy(native)
                    | {"status": "pending" if status == "created" else "paid"}
                },
            }
            for index, status in enumerate(("created", "paid"))
        ],
    )
    return followup


def test_complete_bundle_accepts_native_sandbox_outcomes_without_live_wait(bundle):
    for run in bundle["gateway_runs"] + bundle["stripe_runs"]:
        use_native_sandbox_followup(run)
    assert check(bundle) == []
    # An omitted or live policy still requires the original real banking window.
    del bundle["gateway_runs"][0]["bank_followup"]["outcome_policy"]
    assert any("five banking days" in error for error in check(bundle))


@pytest.mark.parametrize("change", ["live", "source", "missing", "unknown"])
def test_complete_bundle_refuses_incomplete_or_mixed_sandbox_outcomes(bundle, change):
    followup = use_native_sandbox_followup(bundle["gateway_runs"][0])
    if change == "live":
        followup["native_payout"]["livemode"] = True
    elif change == "source":
        followup["native_payout"]["source_type"] = "foreign"
    elif change == "missing":
        followup["payout_events"] = []
    else:
        followup["outcome_policy"] = "short-live-window"
    assert check(bundle)


def test_native_sandbox_followup_cannot_predate_retained_paid_opening(bundle):
    run = bundle["gateway_runs"][0]
    followup = use_native_sandbox_followup(run)
    early = datetime.fromisoformat(run["bank_payout"]["recorded_at"]) - timedelta(
        seconds=1
    )
    followup["recorded_at"] = followup["events_through_at"] = early.isoformat()
    assert any(
        "cannot predate the original paid observation" in e for e in check(bundle)
    )


def test_complete_bundle_refuses_native_events_shifted_outside_original_scan_window(
    bundle,
):
    followup = use_native_sandbox_followup(bundle["gateway_runs"][0])
    followup["native_payout"]["created"] = 0
    for index, event in enumerate(followup["payout_events"]):
        event["created"] = index
        event["data"]["object"]["created"] = 0
    assert any("matching native paid payout" in e for e in check(bundle))
