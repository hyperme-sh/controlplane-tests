#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/../integration/runtime"
unset PYTHONPATH UV_NO_SYNC SKIP_TESTCONTAINERS
docker info >/dev/null
uv sync --frozen --python 3.13
scratch=$(mktemp -d)
trap 'rm -rf "$scratch"' EXIT
# All runtime dependencies are immutable installed distributions, not a sibling checkout.
uv run --frozen pytest -q --junitxml="$scratch/owner-integration.xml"
python3 - "$scratch/owner-integration.xml" <<'PY'
from xml.etree import ElementTree
import sys
root = ElementTree.parse(sys.argv[1]).getroot()
cases = list(root.iter('testcase'))
assert cases, 'No integration tests executed'
assert not any(list(root.iter(tag)) for tag in ('skipped', 'error', 'failure')), 'Integration did not fully execute'
print(f'{len(cases)} owner integration cases passed without skips')
PY
if [[ -n "${TEST_EVIDENCE_DIR:-}" ]]; then
  mkdir -p "$TEST_EVIDENCE_DIR"
  cp "$scratch/owner-integration.xml" "$TEST_EVIDENCE_DIR/"
fi
