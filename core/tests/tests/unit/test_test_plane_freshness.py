"""The freshness decision is the alert body. No network in these tests."""

from __future__ import annotations

import importlib.util
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[4]
NOW = datetime(2026, 9, 26, 18, 0, tzinfo=timezone.utc)
MAIN = "a" * 40
OLD = "b" * 40
OLDER = "c" * 40
STAMP = "2026-09-26T17:00:00Z"
NEW_STAMP = "2026-09-26T17:40:00Z"
CARRIED = "sha256:" + "ab" * 32
REBUILT = "sha256:" + "cd" * 32
GRACE = timedelta(minutes=30)


def _load():
    path = REPO_ROOT / "scripts" / "test_plane_freshness.py"
    spec = importlib.util.spec_from_file_location("test_plane_freshness", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


freshness = _load()


def _edge(sha: str = OLD, built_at: str = STAMP):
    return freshness.ServiceVersion(
        "controlplane-edge", git_sha=sha, built_at=built_at
    )


def _row(service: str, digest: str, sha: str, built_at: str = STAMP):
    return freshness.ExpectedImage(service, digest, sha, built_at)


def _carried_expected():
    """Manifest for main. Both images were carried, so the bake is still OLD."""

    return {
        "controlplane-edge": _row("controlplane-edge", CARRIED, OLD),
        "auth-service": _row("auth-service", CARRIED, OLD),
        "marketplace": _row("marketplace", CARRIED, OLD),
        "turn-service": _row("turn-service", REBUILT, MAIN, NEW_STAMP),
    }


def _decide(**overrides):
    params = dict(
        now=NOW,
        main_sha=MAIN,
        main_committed_at=NOW - timedelta(minutes=10),
        drift_started_at=NOW - timedelta(minutes=10),
        edge=_edge(),
        services=(_edge(),),
        expected=_carried_expected(),
        manifest_sha=MAIN,
        manifest_error=None,
        edge_error=None,
        max_lag=GRACE,
    )
    params.update(overrides)
    return freshness.evaluate_freshness(**params)


def test_publish_that_built_no_image_stays_in_sync() -> None:
    """Every bake is older than main. The running fleet matches those bakes."""

    older_stamp = "2026-09-26T16:00:00Z"
    expected = {
        "controlplane-edge": _row("controlplane-edge", CARRIED, OLD),
        "auth-service": _row("auth-service", CARRIED, OLD),
        "payment-service": _row("payment-service", CARRIED, OLDER, older_stamp),
    }
    edge = _edge(OLD)
    auth = freshness.ServiceVersion("auth-service", git_sha=OLD, built_at=STAMP)
    payment = freshness.ServiceVersion(
        "payment-service", git_sha=OLDER, built_at=older_stamp
    )
    decision = _decide(
        edge=edge,
        services=(edge, auth, payment),
        expected=expected,
        main_committed_at=NOW - timedelta(hours=2),
        drift_started_at=NOW - timedelta(hours=2),
    )
    assert decision.ok
    assert decision.status == "in_sync"
    assert decision.behind == ()
    assert OLD != MAIN
    assert OLDER != MAIN


def test_docs_only_move_stays_green_when_digests_match() -> None:
    """Carried images report an old bake. The manifest for main records that bake."""

    edge = _edge(OLD)
    auth = freshness.ServiceVersion("auth-service", git_sha=OLD, built_at=STAMP)
    decision = _decide(
        edge=edge,
        services=(edge, auth),
        main_committed_at=NOW - timedelta(hours=3),
        drift_started_at=NOW - timedelta(hours=3),
    )
    assert decision.ok
    assert decision.status == "in_sync"
    assert decision.behind == ()
    assert "services behind: none" in decision.summary
    assert OLD != MAIN


def test_marketplace_process_name_joins_the_image_row() -> None:
    edge = _edge(OLD)
    market = freshness.ServiceVersion("marketplace-service", git_sha=OLD, built_at=STAMP)
    decision = _decide(edge=edge, services=(edge, market))
    assert decision.ok
    assert decision.status == "in_sync"
    assert freshness.image_key("marketplace-service") == "marketplace"


def test_reported_digest_matches_even_when_the_bake_differs() -> None:
    edge = freshness.ServiceVersion(
        "controlplane-edge",
        git_sha=OLDER,
        built_at="2026-09-26T16:00:00Z",
        image_digest=CARRIED,
    )
    decision = _decide(edge=edge, services=(edge,))
    assert decision.ok
    assert decision.status == "in_sync"


def test_within_grace_lists_the_expected_digest_and_stays_green() -> None:
    edge = _edge(OLD)
    auth = freshness.ServiceVersion("auth-service", git_sha=OLDER, built_at=STAMP)
    decision = _decide(edge=edge, services=(edge, auth))
    assert decision.ok
    assert decision.status == "within_grace"
    assert decision.behind == ("auth-service",)
    assert f"expected digest={CARRIED}" in decision.summary
    assert "auth-service" in decision.summary


def test_behind_more_than_thirty_minutes_fails_and_names_the_digest() -> None:
    started = NOW - timedelta(minutes=45)
    edge = _edge(MAIN, NEW_STAMP)
    auth = freshness.ServiceVersion(
        "auth-service",
        git_sha=OLD,
        built_at=STAMP,
        image_digest="sha256:" + "ef" * 32,
    )
    expected = _carried_expected()
    expected["controlplane-edge"] = _row("controlplane-edge", REBUILT, MAIN, NEW_STAMP)
    expected["auth-service"] = _row("auth-service", CARRIED, MAIN, NEW_STAMP)
    decision = _decide(
        edge=edge,
        main_committed_at=started,
        drift_started_at=started,
        services=(edge, auth),
        expected=expected,
    )
    assert not decision.ok
    assert decision.status == "behind"
    assert "BEHIND" in decision.summary
    assert "auth-service" in decision.summary
    assert f"expected digest={CARRIED}" in decision.summary
    assert "image_digest=sha256:" + "ef" * 32 in decision.summary


def test_manifest_not_yet_on_main_is_grace_then_failure() -> None:
    fresh = _decide(manifest_sha=OLD, expected={})
    assert fresh.ok
    assert fresh.status == "within_grace"
    assert fresh.behind == ("image-manifest",)
    assert "does not record main" in fresh.summary

    started = NOW - timedelta(minutes=45)
    stale = _decide(
        manifest_sha=OLD,
        expected={},
        main_committed_at=started,
        drift_started_at=started,
    )
    assert not stale.ok
    assert stale.status == "behind"
    assert "image-manifest" in stale.summary


def _compare_commit(sha: str, when: datetime, parent: str, *extra_parents: str) -> dict:
    stamp = when.strftime("%Y-%m-%dT%H:%M:%SZ")
    parents = [{"sha": parent}, *({"sha": extra} for extra in extra_parents)]
    return {
        "sha": sha,
        "commit": {
            "author": {"date": "2026-09-26T15:10:00Z"},
            "committer": {"date": stamp},
        },
        "parents": parents,
    }


def test_merge_within_grace_ignores_older_second_parent_commits() -> None:
    """Branch commits are 50 minutes old. The merge landed 10 minutes ago."""

    branch = "d" * 40
    landed = NOW - timedelta(minutes=10)
    document = {
        "total_commits": 2,
        "commits": [
            _compare_commit(branch, NOW - timedelta(minutes=50), "e" * 40),
            _compare_commit(MAIN, landed, OLD, branch),
        ],
    }
    drift = freshness.drift_from_compare(200, document, OLD, MAIN, landed)
    assert drift.known
    assert drift.at == landed
    decision = _decide(
        manifest_sha=OLD,
        expected={},
        main_committed_at=landed,
        drift_started_at=drift.at,
    )
    assert decision.ok
    assert decision.status == "within_grace"


def test_a_newer_main_commit_does_not_reset_the_drift_clock() -> None:
    """HEAD landed 10 minutes ago. The first missed merge landed 40 minutes ago."""

    branch = "d" * 40
    landed = NOW - timedelta(minutes=10)
    first_land = NOW - timedelta(minutes=40)
    document = {
        "total_commits": 3,
        "commits": [
            _compare_commit(branch, NOW - timedelta(minutes=50), "e" * 40),
            _compare_commit("f" * 40, first_land, OLD),
            _compare_commit(MAIN, landed, "f" * 40, branch),
        ],
    }
    drift = freshness.drift_from_compare(200, document, OLD, MAIN, landed)
    assert drift.known
    assert drift.at == first_land
    decision = _decide(
        manifest_sha=OLD,
        expected={},
        main_committed_at=landed,
        drift_started_at=drift.at,
        services=(_edge(),),
    )
    assert not decision.ok
    assert decision.status == "behind"


def test_stuck_vm_is_stale_when_a_newer_main_commit_lands() -> None:
    """B lands at t0, C at t0+20m, the VM stays on A. At t0+45m the run is stale.

    The edge image is carried through B and C, so the edge's own digest still
    matches the tip. The auth image changed at B and the VM never refreshed.
    Anchoring on main (C) would call this 25 minutes of drift and stay green.
    """

    t0 = NOW - timedelta(minutes=45)
    c_land = t0 + timedelta(minutes=20)
    sha_a = "1" * 40
    sha_b = "2" * 40
    digest_edge = "sha256:" + "11" * 32
    digest_auth_old = "sha256:" + "22" * 32
    digest_auth_new = "sha256:" + "33" * 32

    def published(git_sha: str, auth_digest: str, auth_bake: str) -> freshness.PublishedManifest:
        return freshness.PublishedManifest(
            git_sha,
            {
                "controlplane-edge": freshness.ExpectedImage(
                    "controlplane-edge", digest_edge, OLD, STAMP
                ),
                "auth-service": freshness.ExpectedImage(
                    "auth-service", auth_digest, auth_bake, STAMP
                ),
            },
        )

    history = (
        published(MAIN, digest_auth_new, MAIN),
        published(sha_b, digest_auth_new, sha_b),
        published(sha_a, digest_auth_old, OLD),
    )
    edge = freshness.ServiceVersion(
        "controlplane-edge", git_sha=OLD, built_at=STAMP, image_digest=digest_edge
    )
    auth = freshness.ServiceVersion(
        "auth-service", git_sha=OLD, built_at=STAMP, image_digest=digest_auth_old
    )
    anchor = freshness.newest_matching_manifest(history, (edge, auth))
    assert anchor is not None
    assert anchor.git_sha == sha_a

    document = {
        "total_commits": 2,
        "commits": [
            _compare_commit(sha_b, t0, sha_a),
            _compare_commit(MAIN, c_land, sha_b),
        ],
    }
    drift = freshness.drift_from_compare(200, document, anchor.git_sha, MAIN, c_land)
    assert drift.known
    assert drift.at == t0
    decision = _decide(
        now=NOW,
        edge=edge,
        services=(edge, auth),
        expected=dict(history[0].rows),
        manifest_sha=MAIN,
        main_committed_at=c_land,
        drift_started_at=drift.at,
    )
    assert not decision.ok
    assert decision.status == "behind"


def test_mid_refresh_stays_inside_grace() -> None:
    """Half the fleet is on B and half is still on A. B landed 5 minutes ago.

    No single manifest matches both halves. The anchor is A, so the clock
    starts when the next main commit (B) landed, inside the 30 minute grace.
    """

    b_land = NOW - timedelta(minutes=5)
    sha_a = "1" * 40
    digest_edge = "sha256:" + "11" * 32
    digest_auth_old = "sha256:" + "22" * 32
    digest_auth_new = "sha256:" + "33" * 32
    digest_user_old = "sha256:" + "44" * 32
    digest_user_new = "sha256:" + "55" * 32

    def published(
        git_sha: str,
        auth_digest: str,
        auth_bake: str,
        user_digest: str,
        user_bake: str,
    ) -> freshness.PublishedManifest:
        return freshness.PublishedManifest(
            git_sha,
            {
                "controlplane-edge": freshness.ExpectedImage(
                    "controlplane-edge", digest_edge, OLD, STAMP
                ),
                "auth-service": freshness.ExpectedImage(
                    "auth-service", auth_digest, auth_bake, STAMP
                ),
                "user-service": freshness.ExpectedImage(
                    "user-service", user_digest, user_bake, STAMP
                ),
            },
        )

    history = (
        published(MAIN, digest_auth_new, MAIN, digest_user_new, MAIN),
        published(sha_a, digest_auth_old, OLD, digest_user_old, OLD),
    )
    edge = freshness.ServiceVersion(
        "controlplane-edge", git_sha=OLD, built_at=STAMP, image_digest=digest_edge
    )
    auth = freshness.ServiceVersion(
        "auth-service", git_sha=MAIN, built_at=STAMP, image_digest=digest_auth_new
    )
    user = freshness.ServiceVersion(
        "user-service", git_sha=OLD, built_at=STAMP, image_digest=digest_user_old
    )
    anchor = freshness.newest_matching_manifest(history, (edge, auth, user))
    assert anchor is not None
    assert anchor.git_sha == sha_a

    document = {
        "total_commits": 1,
        "commits": [
            _compare_commit(MAIN, b_land, sha_a),
        ],
    }
    drift = freshness.drift_from_compare(200, document, anchor.git_sha, MAIN, b_land)
    assert drift.known
    assert drift.at == b_land
    decision = _decide(
        now=NOW,
        edge=edge,
        services=(edge, auth, user),
        expected=dict(history[0].rows),
        manifest_sha=MAIN,
        main_committed_at=b_land,
        drift_started_at=drift.at,
    )
    assert decision.ok
    assert decision.status == "within_grace"


def test_no_matching_manifest_inside_the_window() -> None:
    digest = "sha256:" + "ab" * 32
    other = "sha256:" + "cd" * 32
    edge = freshness.ServiceVersion(
        "controlplane-edge", git_sha=OLD, built_at=STAMP, image_digest=digest
    )
    matched = freshness.PublishedManifest(
        "1" * 40,
        {
            "controlplane-edge": freshness.ExpectedImage(
                "controlplane-edge", digest, OLD, STAMP
            )
        },
    )
    filler = freshness.PublishedManifest(
        "2" * 40,
        {
            "controlplane-edge": freshness.ExpectedImage(
                "controlplane-edge", other, MAIN, STAMP
            )
        },
    )
    hidden = (filler,) * freshness.MANIFEST_HISTORY_LIMIT + (matched,)
    assert freshness.newest_matching_manifest(hidden, (edge,)) is None
    assert freshness.newest_matching_manifest((matched, filler), (edge,)) == matched
    stray = freshness.ServiceVersion(
        "user-service", git_sha=OLD, built_at=STAMP, image_digest=other
    )
    assert freshness.newest_matching_manifest((matched,), (edge, stray)) is None


def test_compare_failure_does_not_grant_grace_from_main_head() -> None:
    truncated = freshness.drift_from_compare(
        200, {"total_commits": 300, "commits": []}, OLD, MAIN, NOW
    )
    assert not truncated.known
    assert "truncated" in truncated.detail
    failed = freshness.drift_from_compare(502, None, OLD, MAIN, NOW)
    assert not failed.known
    assert "HTTP 502" in failed.detail
    decision = _decide(
        manifest_sha=OLD,
        expected={},
        drift_known=False,
        drift_detail=failed.detail,
        drift_started_at=NOW,
        main_committed_at=NOW - timedelta(minutes=5),
    )
    assert not decision.ok
    assert decision.status == "behind"
    assert "drift unknown" in decision.summary
    assert "grace not applied" in decision.summary


def test_edge_unreachable_fails_immediately() -> None:
    decision = _decide(edge=None, services=(), edge_error="edge /version unreachable (HTTP 0)")
    assert not decision.ok
    assert decision.status == "unreachable"
    assert "UNREACHABLE" in decision.summary
    assert decision.unreachable == ("controlplane-edge",)


def test_rebuilt_service_inside_grace_is_listed_not_failed() -> None:
    edge = _edge(OLD)
    turn = freshness.ServiceVersion("turn-service", git_sha=OLD, built_at=STAMP)
    decision = _decide(
        edge=edge,
        services=(edge, turn),
        main_committed_at=NOW - timedelta(minutes=5),
        drift_started_at=NOW - timedelta(minutes=5),
    )
    assert decision.ok
    assert decision.status == "within_grace"
    assert decision.behind == ("turn-service",)
    assert f"expected digest={REBUILT}" in decision.summary


def test_digests_in_sync_fail_when_a_peer_does_not_answer() -> None:
    edge = _edge(OLD)
    decision = _decide(
        edge=edge,
        services=(edge, freshness.ServiceVersion("oracles-service", error="unreachable")),
    )
    assert not decision.ok
    assert decision.unreachable == ("oracles-service",)


def test_peer_blip_during_grace_does_not_fail() -> None:
    decision = _decide(
        manifest_sha=OLD,
        expected={},
        services=(_edge(), freshness.ServiceVersion("acl-service", error="unreachable")),
    )
    assert decision.ok
    assert decision.status == "within_grace"
    assert "acl-service" in decision.summary


def test_unusable_edge_sha_is_unreachable() -> None:
    decision = _decide(edge=freshness.ServiceVersion("controlplane-edge", git_sha="unknown"))
    assert not decision.ok
    assert decision.status == "unreachable"


def test_parse_manifest_keeps_the_baked_pair_next_to_the_digest() -> None:
    sha, rows = freshness.parse_manifest(
        {
            "git_sha": MAIN,
            "services": [
                {
                    "service": "auth-service",
                    "digest": CARRIED,
                    "built_git_sha": OLD,
                    "built_at": STAMP,
                    "tag": MAIN,
                }
            ],
        }
    )
    assert sha == MAIN
    assert rows["auth-service"].digest == CARRIED
    assert rows["auth-service"].built_git_sha == OLD
    assert rows["auth-service"].built_at == STAMP


def test_parse_service_does_not_keep_an_upstream_error_string() -> None:
    parsed = freshness.parse_service(
        {"service": "turn-service", "error": "postgresql://user:password@db/controlplane"},
        fallback="turn-service",
    )
    assert parsed.error == "unreachable"
    assert "password" not in freshness._describe_unreachable(parsed)


def test_commit_sha_and_time_uses_the_committer_date() -> None:
    sha, when = freshness.commit_sha_and_time(
        {
            "sha": MAIN,
            "commit": {"committer": {"date": "2026-09-26T17:55:00Z"}},
        }
    )
    assert sha == MAIN
    assert when == datetime(2026, 9, 26, 17, 55, tzinfo=timezone.utc)


def test_request_retries_once_after_a_blip(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = {"n": 0}

    def once(url, token, timeout, accept="application/json"):
        del url, token, timeout, accept
        calls["n"] += 1
        if calls["n"] == 1:
            return 0, None
        return 200, {"ok": True}

    slept: list[float] = []
    monkeypatch.setattr(freshness, "_request_json_once", once)
    monkeypatch.setenv("FRESHNESS_RETRY_DELAY_S", "10")
    status, body = freshness._request_json(
        "https://api.github.com/repos/FyberLabs/panopticon/commits/main",
        "token",
        1,
        sleep=lambda delay: slept.append(delay),
    )
    assert status == 200
    assert body == {"ok": True}
    assert calls["n"] == 2
    assert slept == [10.0]


def test_request_stops_after_one_retry(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = {"n": 0}

    def once(url, token, timeout, accept="application/json"):
        del url, token, timeout, accept
        calls["n"] += 1
        return 0, None

    monkeypatch.setattr(freshness, "_request_json_once", once)
    monkeypatch.setenv("FRESHNESS_RETRY_DELAY_S", "0")
    status, body = freshness._request_json(
        "https://api.github.com/repos/FyberLabs/panopticon/commits/main",
        None,
        1,
        sleep=lambda delay: None,
    )
    assert status == 0
    assert body is None
    assert calls["n"] == 2


def test_request_json_refuses_plaintext() -> None:
    with pytest.raises(ValueError):
        freshness._request_json("http://api.test.hyperme.sh/version", None, 1)


def test_workflow_is_scheduled_on_the_org_runner() -> None:
    text = (REPO_ROOT / ".github/workflows/test-plane-freshness.yml").read_text()
    workflow = yaml.safe_load(text)
    # PyYAML 1.1 parses the bare key `on` as boolean true.
    trigger = workflow[True]
    assert trigger["schedule"][0]["cron"] == "*/15 * * * *"
    assert "workflow_dispatch" in trigger
    job = workflow["jobs"]["freshness"]
    assert job["runs-on"] == ["self-hosted", "linux", "x64"]
    assert "ubuntu-latest" not in text
    script = job["steps"][-1]["run"]
    assert "scripts/test_plane_freshness.py" in script
    assert job["steps"][-1]["env"]["EDGE_VERSION_URL"] == "https://api.test.hyperme.sh/version"
    assert "secrets." not in script
