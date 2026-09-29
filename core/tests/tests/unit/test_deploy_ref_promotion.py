"""Exercise the actual promotion shell with a fake GitHub transport."""
import os
from pathlib import Path
import subprocess

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[4]
WORKFLOW = yaml.safe_load((ROOT / '.github/workflows/ghcr-test.yml').read_text())
STEP = next(s for s in WORKFLOW['jobs']['deployable']['steps']
            if s.get('name') == 'Advance deploy/test to this sha')


@pytest.mark.parametrize(('read_status', 'write_status', 'method', 'success'), [
    ('200', '200', 'PATCH', True),
    ('404', '201', 'POST', True),
    ('200', '403', 'PATCH', False),
    ('500', '200', None, False),
])
def test_ref_update_fails_closed(tmp_path, read_status, write_status, method, success):
    curl = tmp_path / 'curl'
    curl.write_text('''#!/usr/bin/env python3
import os, pathlib, sys
args = sys.argv[1:]
if '-X' in args:
    pathlib.Path(os.environ['CALL_LOG']).write_text(args[args.index('-X') + 1])
    pathlib.Path(args[args.index('-o') + 1]).write_text('{"message":"mock API response"}')
    print(os.environ['WRITE_STATUS'], end='')
else:
    print(os.environ['READ_STATUS'], end='')
''')
    curl.chmod(0o755)
    env = dict(os.environ, PATH=f'{tmp_path}:{os.environ["PATH"]}',
               GH_TOKEN='test-only-token', REPO='example/repo', SHA='a' * 40,
               READ_STATUS=read_status, WRITE_STATUS=write_status,
               CALL_LOG=str(tmp_path / 'calls'))
    result = subprocess.run(['bash', '-c', STEP['run']], env=env,
                            text=True, capture_output=True)
    assert (result.returncode == 0) == success
    assert ('deploy/test ->' in result.stdout) == success
    assert 'test-only-token' not in result.stdout + result.stderr
    calls = tmp_path / 'calls'
    assert (calls.read_text() if calls.exists() else None) == method
    if write_status == '403':
        assert 'DEPLOY_REF_TOKEN' in result.stderr
        assert 'Workflows write' in result.stderr


def test_promotion_credential_is_limited_to_final_step():
    assert STEP['env']['GH_TOKEN'] == '${{ secrets.DEPLOY_REF_TOKEN || secrets.GITHUB_TOKEN }}'
    text = (ROOT / '.github/workflows/ghcr-test.yml').read_text()
    assert text.count('secrets.DEPLOY_REF_TOKEN') == 1
