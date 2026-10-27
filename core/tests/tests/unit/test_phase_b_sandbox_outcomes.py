"""Sandbox terminal evidence never substitutes a shorter live observation window."""

import sys
from copy import deepcopy
from datetime import UTC, datetime
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[4] / "scripts"))
from check_phase_b_evidence import sandbox_followup_errors


@pytest.fixture
def outcome():
    scope = {
        key: "11111111-1111-4111-8111-111111111111"
        for key in ("tenant_id", "host_org_id", "lease_id", "billing_record_id")
    }
    native = {
        "id": "po_test",
        "object": "payout",
        "livemode": False,
        "amount": 100,
        "currency": "usd",
        "destination": "ba_test",
        "status": "paid",
        "automatic": False,
        "method": "standard",
        "type": "bank_account",
        "source_type": "card",
        "created": 1700000000,
        "metadata": scope,
    }
    events = [
        {
            "id": "evt_" + status,
            "object": "event",
            "livemode": False,
            "account": "acct_test",
            "created": 1700000000 + index,
            "type": "payout." + status,
            "data": {
                "object": deepcopy(native)
                | {"status": "pending" if status == "created" else "paid"}
            },
        }
        for index, status in enumerate(("created", "paid"))
    ]
    followup = {
        "native_payout": native,
        "payout_events": events,
        "returned_funds": None,
        "operator_reconciliation_required": False,
        "observation_window_complete": True,
    }
    return (
        followup,
        {"payout_id": "po_test", "amount_cents": 100, "source_type": "card"},
        scope,
    )


def check(value):
    row, payout, scope = value
    return sandbox_followup_errors(
        row,
        payout,
        "acct_test",
        "ba_test",
        scope,
        datetime.fromtimestamp(1700000010, UTC),
    )


def test_concrete_native_sandbox_outcomes_pass_without_elapsed_banking_days(outcome):
    assert check(outcome) == []


@pytest.mark.parametrize(
    "change",
    [
        "live",
        "pending",
        "amount",
        "destination",
        "scope",
        "reversed",
        "returned",
        "incomplete",
        "reconcile",
        "missing_paid",
        "missing_created",
        "duplicate",
        "foreign_account",
        "live_event",
        "failed_event",
        "event_tamper",
        "future_event",
        "paid_status",
    ],
)
def test_missing_mixed_live_failed_or_foreign_native_facts_never_pass(outcome, change):
    row = outcome[0]
    native = row["native_payout"]
    if change == "live":
        native["livemode"] = True
    elif change == "pending":
        native["status"] = "pending"
    elif change == "amount":
        native["amount"] = True
    elif change == "destination":
        native["destination"] = "ba_other"
    elif change == "scope":
        native["metadata"] = native["metadata"] | {"lease_id": "other"}
    elif change == "reversed":
        native["reversed_by"] = "po_reverse"
    elif change == "returned":
        row["returned_funds"] = {"id": "txn_return"}
    elif change == "incomplete":
        row["observation_window_complete"] = False
    elif change == "reconcile":
        row["operator_reconciliation_required"] = True
    elif change == "missing_paid":
        row["payout_events"] = row["payout_events"][:1]
    elif change == "missing_created":
        row["payout_events"] = row["payout_events"][1:]
    elif change == "duplicate":
        row["payout_events"].append(deepcopy(row["payout_events"][0]))
    elif change == "foreign_account":
        row["payout_events"][0]["account"] = "acct_other"
    elif change == "live_event":
        row["payout_events"][0]["livemode"] = True
    elif change == "failed_event":
        row["payout_events"][1]["type"] = "payout.failed"
    elif change == "event_tamper":
        row["payout_events"][0]["data"]["object"]["metadata"]["lease_id"] = "other"
    elif change == "future_event":
        row["payout_events"][1]["created"] = 1700000011
    elif change == "paid_status":
        row["payout_events"][1]["data"]["object"]["status"] = "pending"
    assert check(outcome)
