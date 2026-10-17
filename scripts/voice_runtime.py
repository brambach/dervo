#!/usr/bin/env python3
"""Voice's libraries and its Mac audio helper, staged into a built app (the release builder runs this after
python_runtime.py, before the bundle audit and signing).

runtime/voice.json pins four wheels from PyPI (numpy, onnxruntime, sherpa-onnx, sherpa-onnx-core) by URL, size and
SHA-256. ``stage`` fetches each into the shared download cache once (python_runtime.fetch: a cached file whose hash
doesn't match is never used), unpacks them into Contents/Resources/voice/site-packages, removes what voice never runs
(tests, headers, onnxruntime's training and conversion tools, sherpa's C APIs) and checks the Mach-O files left are
exactly the pin's ``mach_o`` list, so signing covers a known set. It then builds the Swift helper (voice/mac/build.sh)
into Contents/Resources/voice/dervo-voice-audio and, on a Mac, checks the app's own Python imports all three libraries
from that folder and loads nothing from anywhere else.

Nothing is installed on the Mac. Only the cache folder and the app named by --app change.

  voice_runtime.py fetch [--pin runtime/voice.json] [--cache DIR]
  voice_runtime.py stage --app "Dervo Local.app" [--pin runtime/voice.json] [--cache DIR]

``--no-helper`` stages the libraries only. It exists for checking the staging away from a Mac; the release builder
never passes it, so a shipped build always carries the helper.
"""
import argparse
import json
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parent))
import python_runtime  # noqa: E402  (fetch, inventory, safe_relative, the same rules as the Python runtime)

ROOT = Path(__file__).resolve().parent.parent
PIN = ROOT / 'runtime/voice.json'
HELPER_BUILD = ROOT / 'voice/mac/build.sh'
VOICE = 'Contents/Resources/voice'
SITE = VOICE + '/site-packages'
HELPER = VOICE + '/dervo-voice-audio'
PYTHON = 'Contents/Resources/runtime/python/bin/python3.13'
# Imported by the app's own Python with only the staged folder on the path: -I and -S keep everything else out.
# sherpa-onnx carries its own ONNX Runtime library next to the onnxruntime wheel's, so both load into the voice process.
# The check loads sherpa's first, as the engines do, then runs a model on the wheel's: a clash between the two copies
# fails the build here instead of on a tester's Mac.
SMOKE = ('import sys; sys.path.insert(0, sys.argv[1]); import json, numpy, sherpa_onnx, onnxruntime; '
         'from onnxruntime.datasets import get_example; '
         'session = onnxruntime.InferenceSession(get_example("sigmoid.onnx"), providers=["CPUExecutionProvider"]); '
         'name = session.get_inputs()[0].name; '
         'out = session.run(None, {name: numpy.zeros((3, 4, 5), dtype=numpy.float32)})[0]; '
         'assert abs(float(out.mean()) - 0.5) < 1e-6, out.mean(); '
         'outside = [m.__file__ for m in (numpy, onnxruntime, sherpa_onnx) if not m.__file__.startswith(sys.argv[1])]; '
         'print(json.dumps({"numpy": numpy.__version__, "onnxruntime": onnxruntime.__version__, "both_runtimes": True, '
         '"outside": outside}))')


def fail(message):
    raise python_runtime.StageError(message)


def read_pin(path=PIN):
    path = Path(path)
    if path.is_symlink() or not path.is_file():
        fail(f'{path} is missing. Voice pins its libraries in runtime/voice.json; nothing is fetched without it.')
    pin = json.loads(path.read_text())
    if type(pin) is not dict or not {'wheels', 'remove', 'mach_o'} <= set(pin) or type(pin['wheels']) is not list:
        fail(f'{path} must have wheels, remove and mach_o.')
    for wheel in pin['wheels']:
        if type(wheel) is not dict or not {'name', 'version', 'url', 'size', 'sha256', 'licence'} <= set(wheel):
            fail('Each pinned wheel needs name, version, url, size, sha256 and licence.')
        if not str(wheel['url']).startswith('https://files.pythonhosted.org/') or not str(wheel['url']).endswith('.whl'):
            fail('Pinned wheels come from files.pythonhosted.org: ' + str(wheel['url']))
        if not re.fullmatch(r'[0-9a-f]{64}', str(wheel['sha256'])):
            fail('A pinned wheel needs a SHA-256: ' + wheel['name'])
        if type(wheel['size']) is not int or not 0 < wheel['size'] <= 100 * 1024 * 1024:
            fail('A pinned wheel\'s size must be a whole number of bytes, at most 100 MB: ' + wheel['name'])
    for key in ('remove', 'mach_o'):
        if type(pin[key]) is not list or any(type(p) is not str or not python_runtime.safe_relative(p) for p in pin[key]):
            fail(f'The pin\'s {key} list must hold relative paths inside site-packages.')
    return pin


def fetch(pin, cache=python_runtime.CACHE, opener=None):
    return [python_runtime.fetch(wheel, cache, opener) for wheel in pin['wheels']]


def unpack_wheel(archive, into):
    """A wheel is a zip. Every member must stay inside the folder, and none may be a link."""
    root = into.resolve()
    with zipfile.ZipFile(archive) as wheel:
        for member in wheel.infolist():
            target = (into / member.filename).resolve()
            if not target.is_relative_to(root) or member.filename.startswith('/'):
                fail(f'{archive.name} has a member outside its folder: {member.filename}')
            if (member.external_attr >> 16) & 0o170000 == 0o120000:
                fail(f'{archive.name} has a link, which the bundle audit refuses: {member.filename}')
        wheel.extractall(into)


def stage(app, pin, cache=python_runtime.CACHE, opener=None, run=subprocess.run, helper=True):
    """Put the pinned libraries and the helper into a built app. Returns the record the build keeps."""
    app = Path(app).absolute()
    if app.is_symlink() or not (app / 'Contents/Info.plist').is_file():
        fail(f'{app} isn\'t a built app bundle.')
    voice = app / VOICE
    site = app / SITE
    if site.exists() or site.is_symlink() or (app / HELPER).exists():
        fail(f'{voice} already has voice\'s libraries or helper. Build the app again rather than staging twice.')
    if any(p.is_symlink() for p in (voice, *voice.parents) if p.is_relative_to(app)):
        fail('The app\'s voice folder is linked. Build the app again.')
    voice.mkdir(parents=True, exist_ok=True)
    wheels = fetch(pin, cache, opener)
    unpack = Path(tempfile.mkdtemp(prefix='.voice-', dir=voice))
    try:
        for archive in wheels:
            unpack_wheel(archive, unpack)
        for relative in pin['remove']:
            path = unpack / relative
            if path.is_symlink() or path.is_file():
                path.unlink()
            elif path.is_dir():
                shutil.rmtree(path)
            else:
                fail(f'{relative} is listed to remove but isn\'t in the wheels. Check runtime/voice.json against them.')
        for cached in list(unpack.rglob('__pycache__')):
            shutil.rmtree(cached)
        mach_o, links = python_runtime.inventory(unpack)
        if links:
            fail('Voice\'s libraries still have links, which the bundle audit refuses: ' + ', '.join(links))
        if mach_o != sorted(pin['mach_o']):
            fail('Voice\'s Mach-O files differ from runtime/voice.json: ' + ', '.join(mach_o))
        unpack.rename(site)
    finally:
        shutil.rmtree(unpack, ignore_errors=True)
    record = {'path': SITE, 'wheels': [{key: wheel[key] for key in ('name', 'version', 'sha256', 'licence')} for wheel in pin['wheels']],
              'mach_o': [SITE + '/' + p for p in pin['mach_o']],
              'bytes': sum(p.stat().st_size for p in site.rglob('*') if p.is_file())}
    python = app / PYTHON
    if python.is_file():
        # Precompiled once here, like the runtime's standard library: the signed copy is read-only.
        compiled = run([str(python), '-E', '-s', '-I', '-m', 'compileall', '-q', '-j', '0', '--invalidation-mode',
                        'unchecked-hash', str(site)], capture_output=True, text=True, timeout=600, env={})
        if compiled.returncode:
            fail('Precompiling voice\'s libraries failed: ' + (compiled.stdout + compiled.stderr)[-2000:])
        smoke = run([str(python), '-I', '-S', '-B', '-c', SMOKE, str(site)], capture_output=True, text=True, timeout=120, env={})
        if smoke.returncode:
            fail('Dervo\'s Python couldn\'t import voice\'s libraries: ' + (smoke.stdout + smoke.stderr)[-2000:])
        facts = json.loads(smoke.stdout)
        if facts['outside']:
            fail('Voice\'s libraries were imported from outside the app: ' + ', '.join(facts['outside']))
        record['imports'] = facts
    elif sys.platform == 'darwin':
        fail(f'{PYTHON} is missing. Stage the Python runtime (python_runtime.py) first.')
    else:
        record['imports'] = 'not checked: needs the app\'s own Python, which runs only on a Mac'
    if helper:
        built = run(['/bin/bash', str(HELPER_BUILD), str(voice)], capture_output=True, text=True, timeout=600)
        if built.returncode or not (app / HELPER).is_file() or not python_runtime.is_mach_o(app / HELPER):
            fail('Building the voice audio helper failed: ' + (built.stdout + built.stderr)[-2000:])
        record['helper'] = HELPER
    else:
        record['helper'] = 'not built (--no-helper)'
    return record


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split('\n\n')[0])
    parser.add_argument('command', choices=('fetch', 'stage'))
    parser.add_argument('--pin', default=str(PIN))
    parser.add_argument('--cache', default=str(python_runtime.CACHE))
    parser.add_argument('--app')
    parser.add_argument('--no-helper', action='store_true')
    args = parser.parse_args(argv)
    try:
        pin = read_pin(args.pin)
        if args.command == 'fetch':
            for path in fetch(pin, args.cache):
                print(path)
        else:
            if not args.app:
                parser.error('stage needs --app')
            print(json.dumps(stage(args.app, pin, args.cache, helper=not args.no_helper), indent=2))
    except python_runtime.StageError as error:
        print(str(error), file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
