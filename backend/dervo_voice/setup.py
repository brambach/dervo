"""Download the voice models, only when you ask for them.

    python -m dervo_voice.setup --target MODELS_DIR [--only NAME ...]

Voice is off until you turn it on, and turning it on is what downloads these: about 830 MB, once, into Dervo's own data
folder (src/voice.rs puts it under Application Support). Nothing is fetched at install or launch. Each file is checked
against the SHA-256 pinned here before it's used, unpacked with tarfile's "data" filter (no links out, no absolute
paths) into a temporary folder, and only then moved into place, so a cut-off download never looks installed.

Licences are kept beside each model: Silero VAD MIT, sherpa-onnx's keyword spotter Apache-2.0, Parakeet TDT 0.6B v2
CC-BY-4.0 (NVIDIA; attribution required, carried in LICENCE.txt), Kokoro 82M Apache-2.0, Smart Turn v3.2 BSD-2-Clause.

Progress is JSON lines on stdout: {"type": "setup", "name": ..., "state": "downloading"|"checking"|"ready"|"failed", ...}.
"""
import argparse
import hashlib
import json
import os
import shutil
import sys
import tarfile
import tempfile
import urllib.request
from pathlib import Path

from . import engines

SHERPA = 'https://github.com/k2-fsa/sherpa-onnx/releases/download'
# Hashes and sizes taken from the published files on 8 Oct 2026.
MODELS = [
    {'name': 'vad', 'what': 'Silero VAD', 'licence': 'MIT', 'url': f'{SHERPA}/asr-models/silero_vad.onnx',
     'sha256': '9e2449e1087496d8d4caba907f23e0bd3f78d91fa552479bb9c23ac09cbb1fd6', 'bytes': 643854,
     'file': engines.VAD_FILE},
    {'name': 'turn', 'what': 'Smart Turn v3.2 (Pipecat)', 'licence': 'BSD-2-Clause',
     'url': 'https://huggingface.co/pipecat-ai/smart-turn-v3/resolve/main/smart-turn-v3.2-cpu.onnx',
     'sha256': '2bb026316b14a660486a75b1733cd3fbab8c2fd0314dc9af7be49f8cca967e4f', 'bytes': 8679182,
     'file': engines.TURN_FILE},
    {'name': 'wake', 'what': 'sherpa-onnx keyword spotter (zipformer, GigaSpeech)', 'licence': 'Apache-2.0',
     'url': f'{SHERPA}/kws-models/{engines.KWS_DIR}.tar.bz2',
     'sha256': 'f170013b4716e41b62b9bfd809687c207cef798ef9bc6534d524e17af9b6561a', 'bytes': 17626723,
     'folder': engines.KWS_DIR},
    {'name': 'asr', 'what': 'NVIDIA Parakeet TDT 0.6B v2 (int8, sherpa-onnx export)', 'licence': 'CC-BY-4.0',
     'url': f'{SHERPA}/asr-models/{engines.ASR_DIR}.tar.bz2',
     'sha256': '157c157bc51155e03e37d2466522a3a737dd9c72bb25f36eb18912964161e1ad', 'bytes': 482468385,
     'folder': engines.ASR_DIR},
    {'name': 'tts', 'what': 'Kokoro 82M v0.19 (sherpa-onnx export)', 'licence': 'Apache-2.0',
     'url': f'{SHERPA}/tts-models/{engines.TTS_DIR}.tar.bz2',
     'sha256': '912804855a04745fa77a30be545b3f9a5d15c4d66db00b88cbcd4921df605ac7', 'bytes': 319625534,
     'folder': engines.TTS_DIR},
]
TOTAL_BYTES = sum(model['bytes'] for model in MODELS)


def installed(target, model):
    path = Path(target) / (model.get('file') or model['folder'])
    return path.exists()


def missing(target):
    return [model for model in MODELS if not installed(target, model)]


def _download(url, destination, expected, emit, name, opener=urllib.request.urlopen):
    digest = hashlib.sha256()
    done = 0
    with opener(url, timeout=60) as response, open(destination, 'wb') as handle:
        while True:
            chunk = response.read(1 << 20)
            if not chunk:
                break
            if done + len(chunk) > expected['bytes']:
                raise ValueError(f'{name}: the download is bigger than expected, so it was stopped')
            handle.write(chunk)
            digest.update(chunk)
            done += len(chunk)
            if done % (16 << 20) < len(chunk):
                emit({'type': 'setup', 'name': name, 'state': 'downloading', 'bytes': done, 'of': expected['bytes']})
    if digest.hexdigest() != expected['sha256']:
        raise ValueError(f'{name}: the download doesn\'t match its pinned checksum')
    return done


def install(target, emit, only=None, opener=urllib.request.urlopen):
    target = Path(target)
    target.mkdir(parents=True, exist_ok=True)
    # A download cut off by quitting the app leaves its work folder (up to ~480 MB): clear those first.
    for stale in target.glob('.setup-*'):
        if stale.is_dir() and not stale.is_symlink():
            shutil.rmtree(stale, ignore_errors=True)
    for model in MODELS:
        if only and model['name'] not in only:
            continue
        if installed(target, model):
            emit({'type': 'setup', 'name': model['name'], 'state': 'ready', 'already': True})
            continue
        with tempfile.TemporaryDirectory(dir=target, prefix='.setup-') as work:
            work = Path(work)
            download = work / 'download'
            emit({'type': 'setup', 'name': model['name'], 'state': 'downloading', 'bytes': 0, 'of': model['bytes']})
            try:
                _download(model['url'], download, model, emit, model['name'], opener=opener)
                emit({'type': 'setup', 'name': model['name'], 'state': 'checking'})
                if model.get('file'):
                    placed = work / model['file']
                    os.replace(download, placed)
                else:
                    unpacked = work / 'unpacked'
                    with tarfile.open(download, 'r:*') as archive:
                        archive.extractall(unpacked, filter='data')
                    placed = unpacked / model['folder']
                    if not placed.is_dir():
                        raise ValueError(f'{model["name"]}: the archive doesn\'t hold {model["folder"]}')
                    for unused in ('test_wavs',):
                        shutil.rmtree(placed / unused, ignore_errors=True)
                    (placed / 'LICENCE.txt').write_text(f'{model["what"]}\nLicence: {model["licence"]}\nSource: {model["url"]}\n')
                os.replace(placed, target / (model.get('file') or model['folder']))
            except Exception as error:
                emit({'type': 'setup', 'name': model['name'], 'state': 'failed', 'detail': str(error)[:300]})
                raise
        emit({'type': 'setup', 'name': model['name'], 'state': 'ready'})
    (target / 'LICENCES.json').write_text(json.dumps([{key: model[key] for key in ('name', 'what', 'licence', 'url')}
                                                      for model in MODELS], indent=1) + '\n')


def main(argv=None):
    parser = argparse.ArgumentParser(prog='dervo_voice.setup')
    parser.add_argument('--target', required=True)
    parser.add_argument('--only', nargs='*')
    parser.add_argument('--list', action='store_true', help='say what would be downloaded and stop')
    args = parser.parse_args(argv)
    emit = lambda event: print(json.dumps(event), flush=True)  # noqa: E731
    if args.list:
        emit({'type': 'setup', 'state': 'plan', 'bytes': sum(model['bytes'] for model in missing(args.target)),
              'models': [model['name'] for model in missing(args.target)]})
        return 0
    try:
        install(args.target, emit, only=args.only)
    except Exception:
        return 1
    emit({'type': 'setup', 'state': 'done'})
    return 0


if __name__ == '__main__':
    sys.exit(main())
