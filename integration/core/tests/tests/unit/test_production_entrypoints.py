"""Production startup must not resolve omitted development workspace members."""
import json
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[4]


def test_production_images_use_the_built_runtime():
    images = [ROOT / "controlplane-edge/Dockerfile", *ROOT.glob("core/*/Dockerfile"), *ROOT.glob("products/*/Dockerfile")]
    checked = 0
    for path in images:
        source = path.read_text()
        if "RUN uv sync" not in source:
            continue
        checked += 1
        assert "uv sync --frozen --no-dev" in source, path
        for line in source.splitlines():
            if line.startswith("CMD ["):
                assert json.loads(line[4:])[0] == "/app/.venv/bin/python", path
            if line.startswith('ENTRYPOINT ["/docker-entrypoint.sh"]'):
                entrypoint = path.with_name("docker-entrypoint.sh").read_text()
                assert not re.search(r"\buv\s+(run|sync|pip)\b", entrypoint), path
                assert "/app/.venv/bin/python" in entrypoint, path
    assert checked >= 16
