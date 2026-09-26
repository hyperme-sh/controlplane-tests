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


def _edge(sha: str = OLD):
    return freshness.ServiceVersion(
        "controlplane-edge", git_sha=sha, built_at=STAMP
    )


def _decide(**overrides):
    params = dict(
        now=NOW,
        main_sha=MAIN,
        main_committed_at=NOW - timedelta(minutes=10),
        drift_started_at=NOW - timedelta(minutes=10),
        edge=_edge(),
        services=(_edge(),),
        edge_error=None,
        max_lag=GRACE,
    )
    params.update(overrides)
    return freshness.evaluate_freshness(**params)


def test_in_sync_when_edge_and_peers_match_main() -> None:
    edge = _edge(MAIN)
    decision = _decide(
        edge=edge,
        services=(edge, freshness.ServiceVersion("auth-service", git_sha=MAIN)),
        main_committed_at=NOW - timedelta(hours=3),
        drift_started_at=NOW - timedelta(hours=3),
    )
    assert decision.ok
    assert decision.status == "in_sync"
    assert decision.behind == ()
    assert "services behind: none" in decision.summary


def test_within_grace_lists_services_and_stays_green() -> None:
    auth = freshness.ServiceVersion("auth-service", git_sha=OLD, built_at=STAMP)
    decision = _decide(services=(_edge(), auth))
    assert decision.ok
    assert decision.status == "within_grace"
    assert decision.behind == ("controlplane-edge", "auth-service")
    assert "auth-service git_sha=" + OLD in decision.summary


def test_behind_more_than_thirty_minutes_fails_and_names_services() -> None:
    started = NOW - timedelta(minutes=45)
    auth = freshness.ServiceVersion(
        "auth-service",
        git_sha=OLD,
        built_at=STAMP,
        image_digest="sha256:" + "ab" * 32,
    )
    decision = _decide(
        main_committed_at=started,
        drift_started_at=started,
        services=(_edge(), auth),
    )
    assert not decision.ok
    assert decision.status == "behind"
    assert "BEHIND" in decision.summary
    assert "auth-service" in decision.summary
    assert "image_digest=sha256:" + "ab" * 32 in decision.summary


def test_a_newer_main_commit_does_not_reset_the_drift_clock() -> None:
    """HEAD landed 5 minutes ago. The first missed commit landed 40 minutes ago."""

    times = [
        NOW - timedelta(minutes=40),
        NOW - timedelta(minutes=5),
    ]
    started = freshness.resolve_drift_start(OLD, MAIN, times[-1], times)
    assert started == times[0]
    decision = _decide(
        main_committed_at=times[-1],
        drift_started_at=started,
        services=(_edge(),),
    )
    assert not decision.ok
    assert decision.status == "behind"


def test_edge_unreachable_fails_immediately() -> None:
    decision = _decide(edge=None, services=(), edge_error="edge /version unreachable (HTTP 0)")
    assert not decision.ok
    assert decision.status == "unreachable"
    assert "UNREACHABLE" in decision.summary
    assert decision.unreachable == ("controlplane-edge",)


def test_edge_on_main_fails_when_a_service_is_still_behind() -> None:
    edge = _edge(MAIN)
    decision = _decide(
        edge=edge,
        services=(
            edge,
            freshness.ServiceVersion("turn-service", git_sha=OLD, built_at=STAMP),
        ),
        main_committed_at=NOW - timedelta(minutes=5),
        drift_started_at=NOW - timedelta(minutes=5),
    )
    assert not decision.ok
    assert decision.behind == ("turn-service",)
    assert "turn-service" in decision.summary


def test_edge_on_main_fails_when_a_peer_does_not_answer() -> None:
    edge = _edge(MAIN)
    decision = _decide(
        edge=edge,
        services=(edge, freshness.ServiceVersion("oracles-service", error="unreachable")),
    )
    assert not decision.ok
    assert decision.unreachable == ("oracles-service",)


def test_peer_blip_during_grace_does_not_fail() -> None:
    decision = _decide(
        services=(_edge(), freshness.ServiceVersion("acl-service", error="unreachable")),
    )
    assert decision.ok
    assert decision.status == "within_grace"
    assert "acl-service" in decision.summary


def test_unusable_edge_sha_is_unreachable() -> None:
    decision = _decide(edge=freshness.ServiceVersion("controlplane-edge", git_sha="unknown"))
    assert not decision.ok
    assert decision.status == "unreachable"


def test_parse_service_does_not_keep_an_upstream_error_string() -> None:
    parsed = freshness.parse_service(
        {"service": "turn-service", "error": "postgresql://user:password@db/controlplane"},
        fallback="turn-service",
    )
    assert parsed.error == "unreachable"
    assert "password" not in freshness._describe(parsed)


def test_compare_uses_the_oldest_committer_time() -> None:
    document = {
        "commits": [
            {"commit": {"committer": {"date": "2026-09-26T17:20:00Z"}}},
            {"commit": {"committer": {"date": "2026-09-26T17:55:00Z"}}},
        ]
    }
    times = freshness.compare_committer_times(document)
    assert times[0] == datetime(2026, 9, 26, 17, 20, tzinfo=timezone.utc)
    sha, when = freshness.commit_sha_and_time(
        {
            "sha": MAIN,
            "commit": {"committer": {"date": "2026-09-26T17:55:00Z"}},
        }
    )
    assert sha == MAIN
    assert when == datetime(2026, 9, 26, 17, 55, tzinfo=timezone.utc)


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
