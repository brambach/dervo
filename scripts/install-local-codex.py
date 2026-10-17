"""Install the pinned official arm64 package without npm scripts or global state."""
import base64
import hashlib
import io
import json
from pathlib import Path
import subprocess
import tarfile
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
VERSION = '0.156.1'
evidence = ROOT / 'evidence/codex-local-upgrade'
metadata = json.loads((evidence / 'npm.json').read_text())
release = json.loads((evidence / 'github.json').read_text())
assert release['tag_name'] == 'rust-v' + VERSION and not release['prerelease']
package = metadata['versions'][VERSION + '-darwin-arm64']
dist = package['dist']
assert dist['tarball'].startswith('https://registry.npmjs.org/@openai/codex/')
cache = ROOT / 'tools/cache'
cache.mkdir(parents=True, exist_ok=True)
archive = cache / ('codex-' + VERSION + '-darwin-arm64.tgz')
if not archive.exists():
    archive.write_bytes(urllib.request.urlopen(dist['tarball']).read())
raw = archive.read_bytes()
actual = 'sha512-' + base64.b64encode(hashlib.sha512(raw).digest()).decode()
assert actual == dist['integrity'], 'Official npm integrity mismatch'
target = ROOT / 'tools' / ('codex-' + VERSION)
if not target.exists():
    with tarfile.open(fileobj=io.BytesIO(raw), mode='r:gz') as tar:
        tar.extractall(target, filter='data')
binary = target / 'package/vendor/aarch64-apple-darwin/bin/codex'
with tarfile.open(fileobj=io.BytesIO(raw), mode='r:gz') as tar:
    assert binary.read_bytes() == tar.extractfile('package/vendor/aarch64-apple-darwin/bin/codex').read()
env = {'HOME': str(cache), 'CODEX_HOME': str(cache / 'codex-home'), 'PATH': '/usr/bin:/bin'}
result = subprocess.run([str(binary), '--version'], env=env, capture_output=True, text=True, check=True)
assert result.stdout.strip() == 'codex-cli ' + VERSION
lock = {'version': VERSION, 'platform': 'aarch64-apple-darwin', 'executable': str(binary),
        'npm_package_version': package['version'], 'tarball': dist['tarball'], 'integrity': actual,
        'binary_sha256': hashlib.sha256(binary.read_bytes()).hexdigest(),
        'official_release': release['html_url'], 'version_output': result.stdout.strip()}
(ROOT / 'tools/codex-lock.json').write_text(json.dumps(lock, indent=2) + '\n')
print(json.dumps(lock, indent=2))
