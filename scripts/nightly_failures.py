"""File one regression issue per failed nightly case."""

import hashlib
import json
import os
from pathlib import Path
import re
import sys
from urllib.request import Request, urlopen
from xml.etree import ElementTree


def failures(path):
    if not path.is_file():
        return ["owner integration setup"]
    cases = {
        f"{case.get('classname', '')}::{case.get('name', '')}"
        for case in ElementTree.parse(path).iter("testcase")
        if any(case.find(tag) is not None for tag in ("failure", "error", "skipped"))
    }
    return sorted(cases) or ["owner integration execution"]


def failure_record(case):
    return {"id": hashlib.sha256(case.encode()).hexdigest(), "label": safe_case(case)}


def marker(case):
    return f"<!-- nightly-owner-integration:{case['id']} -->"


def issue_history(issues, case):
    match = marker(case)
    return [issue for issue in issues if match in (issue.get("body") or "").splitlines()]


def safe_case(case):
    # Never publish JUnit messages, output, or arbitrary parametrized values.
    base = case.partition("[")[0]
    return re.sub(r"[^a-zA-Z0-9_.: -]", "_", base)[:150]


def api(endpoint, payload=None):
    request = Request(
        os.environ["GITHUB_API_URL"] + "/" + endpoint,
        data=json.dumps(payload).encode() if payload is not None else None,
        headers={
            "Authorization": "Bearer " + os.environ["GH_TOKEN"],
            "Accept": "application/vnd.github+json",
            "Content-Type": "application/json",
        },
    )
    with urlopen(request, timeout=30) as response:
        return json.load(response)


def list_issues(endpoint):
    issues = []
    for page in range(1, 101):
        batch = api(f"{endpoint}?state=all&per_page=100&page={page}")
        issues.extend(issue for issue in batch if "pull_request" not in issue)
        if len(batch) < 100:
            return issues
    raise RuntimeError("Issue inventory too large to deduplicate reliably")


def report(cases):
    repo = os.environ["GITHUB_REPOSITORY"]
    run_id = os.environ["GITHUB_RUN_ID"]
    run_url = f"https://github.com/{repo}/actions/runs/{run_id}"
    endpoint = f"repos/{repo}/issues"
    issues = list_issues(endpoint)
    for case in {case["id"]: case for case in cases}.values():
        if not re.fullmatch(r"[a-f0-9]{64}", case["id"]):
            raise ValueError("Invalid nightly failure identity")
        history = issue_history(issues, case)
        if any(issue["state"] == "open" for issue in history):
            continue
        previous = max(history, key=lambda issue: issue["number"], default=None)
        label = safe_case(case["label"])
        body = f"{marker(case)}\n\nThe nightly integration batch failed for `{label}`.\n\n[CI run]({run_url})"
        if previous:
            body += f"\n\nThis case previously failed in closed issue #{previous['number']}."
        api(endpoint, {"title": f"Nightly integration regression in {label}", "body": body})


def main():
    if len(sys.argv) == 3 and sys.argv[1] == "collect":
        print("cases=" + json.dumps([failure_record(case) for case in failures(Path(sys.argv[2]))], separators=(",", ":")))
    elif sys.argv[1:] == ["report"]:
        raw = os.environ.get("NIGHTLY_FAILURES")
        cases = json.loads(raw) if raw else []
        if os.environ.get("OWNER_RESULT") == "failure" and not cases:
            cases.append(failure_record("owner integration setup"))
        if os.environ.get("GATEWAY_RESULT") == "failure":
            cases.append(failure_record("public TEST gateway smoke"))
        report(cases)
    else:
        raise SystemExit("Use collect JUNIT_FILE or report")


if __name__ == "__main__":
    main()
