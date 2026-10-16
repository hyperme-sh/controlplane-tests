"""Incomplete or mixed-revision declarations must never pass the exit gate."""

import importlib.util
import json
import os
import subprocess
import sys
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
            },
            bank_payout=record(
                "paid-" + name,
                hour,
                recorded_at=paid_at,
                payout_id="po_" + token,
                created_at=created_at,
                arrival_at=at.isoformat(),
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
    return result


@pytest.fixture
def bundle():
    return {
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
