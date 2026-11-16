"""Build a credential-free container context from qualified Tests package bytes."""
import hashlib
import json
from pathlib import Path
import re
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]


def prepare(distribution, output):
    subprocess.run([sys.executable, str(ROOT / 'scripts/verify_wheel.py'), str(distribution)], check=True)
    wheel, = distribution.glob('*.whl')
    requirements = subprocess.check_output([
        'uv', 'export', '--frozen', '--no-dev', '--no-emit-project',
        '--format', 'requirements-txt'], cwd=ROOT, text=True)
    if 'git+' in requirements or re.search(r'^\S+ @ |^--', requirements, re.M):
        raise ValueError('Runtime dependencies must use hashed public registry requirements')
    output.mkdir(mode=0o700, parents=True, exist_ok=False)
    shutil.copyfile(wheel, output / wheel.name)
    shutil.copyfile(ROOT / 'deploy/Dockerfile.runtime', output / 'Dockerfile')
    (output / 'requirements.txt').write_text(requirements)
    for name in ('LICENSE', 'NOTICE'):
        shutil.copyfile(ROOT / name, output / name)
    shutil.copytree(ROOT / '.github/workflows/third-party-notices', output / 'licenses')
    shutil.copyfile(ROOT / 'scripts/prepare_notices.py', output / 'prepare_notices.py')
    inventory = {p.relative_to(output).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
                 for p in sorted(output.rglob('*')) if p.is_file()}
    (output / 'inventory.json').write_text(json.dumps(inventory, sort_keys=True, indent=2) + '\n')


if __name__ == '__main__':
    prepare(Path(sys.argv[1]).resolve(), Path(sys.argv[2]).resolve())
