"""Incomplete or mixed-revision declarations must never pass the exit gate."""

import importlib.util
import json
import os
import subprocess
import sys
from datetime import UTC, datetime, timedelta
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
COMMIT_TIME = datetime(2026, 10, 12, tzinfo=UTC)


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
    changes.setdefault("commit_time", COMMIT_TIME)
    changes.setdefault("now", datetime(2026, 10, 12, 12, tzinfo=UTC))
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
                        row[key] = (
                            datetime.fromisoformat(row[key]) + timedelta(days=1)
                        ).isoformat()
    assert any("in the future" in error for error in check(bundle))


def test_future_clock_skew_is_bounded(bundle):
    now = datetime(2026, 10, 12, 12, tzinfo=UTC)
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
    bundle[key]["recorded_at"] = f"2026-10-12T{hour:02}:00:00+00:00"
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
