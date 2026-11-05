#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
unset PYTHONPATH UV_NO_SYNC
: "${TEST_DATABASE_URL:?Disposable PostgreSQL is required}"
scratch=$(mktemp -d)
trap 'rm -rf "$scratch"' EXIT
uv lock --check
uv sync --frozen
uv run --frozen pytest --junitxml="$scratch/source.xml"
uv run --frozen pytest -q tests_tools
uv build --out-dir "$scratch/dist"
uv run --frozen python scripts/verify_wheel.py "$scratch/dist"
uv run --frozen python scripts/export_release_contract.py "$scratch"/dist/*.whl --output "$scratch/contract"
for qualified_manifest in releases/qualification/current/release.json releases/candidate/release.json; do
if [[ -f "$qualified_manifest" ]]; then
  uv run --frozen python scripts/verify_release_candidate.py "$qualified_manifest" "$scratch/contract/contract.json" "$scratch/contract/evidence.json" controlplane-tests
fi
done
uv export --frozen --no-emit-project --format requirements-txt --output-file "$scratch/requirements.txt" >/dev/null
uv venv --python 3.13 "$scratch/venv"
uv pip sync --python "$scratch/venv/bin/python" "$scratch/requirements.txt"
uv pip install --python "$scratch/venv/bin/python" --constraint "$scratch/requirements.txt" "$scratch"/dist/*.whl
cp -R tests "$scratch/"
(cd "$scratch" && "$scratch/venv/bin/python" -I -m pytest -c /dev/null tests --junitxml="$scratch/wheel.xml")
# Rebuild from the sdist; a checkout must not supply omitted package files.
uv run --frozen python - "$scratch" <<'PY'
import pathlib, sys, tarfile
root = pathlib.Path(sys.argv[1])
with tarfile.open(next((root / 'dist').glob('*.tar.gz'))) as archive:
    archive.extractall(root / 'sdist', filter='data')
PY
uv build --wheel "$scratch"/sdist/* --out-dir "$scratch/rebuilt"
uv run --frozen python scripts/verify_wheel.py "$scratch/rebuilt"
uv run --frozen python scripts/export_release_contract.py "$scratch"/rebuilt/*.whl --output "$scratch/rebuilt-contract"
cmp "$scratch/contract/contract.json" "$scratch/rebuilt-contract/contract.json"
uv pip install --python "$scratch/venv/bin/python" --no-deps --reinstall "$scratch"/rebuilt/*.whl
(cd "$scratch" && "$scratch/venv/bin/python" -I -m pytest -c /dev/null tests --junitxml="$scratch/sdist.xml")

# Retain the exact validated bytes for the release channel; never rebuild them.
if [[ -n "${RELEASE_ARTIFACT_DIR:-}" ]]; then
  mkdir -p "$RELEASE_ARTIFACT_DIR"
  cp "$scratch/dist"/* "$RELEASE_ARTIFACT_DIR/"
fi

python3 scripts/verify-test-results.py "$scratch/source.xml" "$scratch/wheel.xml" "$scratch/sdist.xml"
if [[ -n "${TEST_EVIDENCE_DIR:-}" ]]; then
  mkdir -p "$TEST_EVIDENCE_DIR"
  cp "$scratch"/*.xml "$TEST_EVIDENCE_DIR/"
fi
