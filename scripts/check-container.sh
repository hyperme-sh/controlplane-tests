#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
: "${QUALIFIED_DIST_DIR:?Use qualified package output}"
: "${GITHUB_SHA:?Record the qualified source revision}"
scratch=$(mktemp -d "${RUNNER_TEMP:-/tmp}/api-container.XXXXXX")
image="ghcr.io/hyperme-sh/controlplane-tests/runtime:preparation-${GITHUB_RUN_ID:-local}-${GITHUB_RUN_ATTEMPT:-1}"
qualified=false
cleanup() {
  if [[ "$qualified" != true || -z "${QUALIFIED_IMAGE_OUTPUT:-}" ]]; then
    docker image rm "$image" >/dev/null 2>&1 || true
  fi
  rm -rf -- "$scratch"
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM
python3 scripts/prepare-runtime-context.py "$QUALIFIED_DIST_DIR" "$scratch/context"
python3 scripts/container_audit.py wheels "$scratch/context"
wheel=$(sha256sum "$scratch"/context/spanopticon_tests-*.whl | cut -d ' ' -f 1)
docker build --build-arg "SOURCE_SHA=$GITHUB_SHA" --build-arg "WHEEL_SHA256=$wheel" --tag "$image" "$scratch/context"
docker save --output "$scratch/image.tar" "$image"
python3 scripts/assert-no-source-bundles.py "$scratch/image.tar"
python3 scripts/container_audit.py image "$scratch/image.tar" --output "$scratch/layers"
# Scan every layer, including lower-layer files hidden by a later deletion.
version=8.30.1
archive="gitleaks_${version}_linux_x64.tar.gz"
curl --retry 3 --retry-all-errors --connect-timeout 20 --max-time 120 --fail --silent --show-error --location "https://github.com/gitleaks/gitleaks/releases/download/v${version}/${archive}" -o "$scratch/$archive"
printf '%s  %s\n' 551f6fc83ea457d62a0d98237cbad105af8d557003051f41f3e7ca7b3f2470eb "$scratch/$archive" | sha256sum --check
mkdir "$scratch/tool"
tar -xzf "$scratch/$archive" -C "$scratch/tool" gitleaks
status=0
"$scratch/tool/gitleaks" dir "$scratch/layers" --no-banner --max-archive-depth 3 --ignore-gitleaks-allow --report-format json --report-path "$scratch/findings.json" >"$scratch/scanner.log" 2>&1 || status=$?
if [[ "$status" == 1 ]]; then
  python3 scripts/classify_base_fingerprint.py "$scratch/findings.json" "$scratch/layers"
elif [[ "$status" != 0 ]]; then
  exit "$status"
fi


# Metadata and source-byte inspection only; no service or database is started.
docker run --rm --network none --read-only --cap-drop ALL --security-opt no-new-privileges \
  --mount "type=bind,src=$PWD/scripts/container_audit.py,dst=/audit.py,readonly" \
  --mount "type=bind,src=$PWD,dst=/audit-root,readonly" \
  --entrypoint /opt/tests/bin/python "$image" -I /audit.py installed /audit-root/src
printf 'Prepared image %s: %s\n' "$image" "$(docker image inspect "$image" --format '{{.Id}}')"

if [[ -n "${QUALIFIED_IMAGE_OUTPUT:-}" ]]; then
  python3 - "$image" "$GITHUB_SHA" "$QUALIFIED_IMAGE_OUTPUT" <<'PYTHON'
import json, subprocess, sys
from pathlib import Path
image, revision, output = sys.argv[1:]
image_id = subprocess.check_output(['docker', 'image', 'inspect', image, '--format', '{{.Id}}'], text=True).strip()
Path(output).write_text(json.dumps({'image': image, 'image_id': image_id, 'revision': revision}) + '\n')
PYTHON
  qualified=true
fi
