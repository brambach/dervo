"""Render Dervo's vector icons with the project's installed Tauri CLI.

`icons/dervo.svg` is the icon; `icons/dervo-small.svg` is the same tile with the pebble's face drawn on the 16px pixel
grid, for the 16 and 32px bitmaps where the regular face loses its eyes. On a Mac the ICNS is assembled with iconutil
so it carries the small face at 16 and 32; elsewhere Tauri's own ICNS, from the regular source, is kept.

The icon choices (beta.15, group 7) are listed in `icons/choices/choices.json`, each with a large source and a small
face. A choice whose small face loses detail at 16px also has a "tiny" source, used for the 16px bitmap only. On a Mac each one becomes `icons/choices/<id>.icns` the same way, for the Dock tile plug-in and the running app's
Dock icon, plus `frontend/public/app-icons/<id>.png` (128px, the large art) for the picker in Settings."""
from pathlib import Path
import json
import shutil
import subprocess
from tempfile import TemporaryDirectory

root = Path(__file__).resolve().parents[1]
node = shutil.which('node')
if not node:
    raise SystemExit('Install Node before generating the app icons.')
cli = root / 'frontend/node_modules/@tauri-apps/cli/tauri.js'
if not cli.is_file():
    raise SystemExit('Install the frontend dependencies before generating the app icons.')
icons = root / 'icons'
choices = icons / 'choices'
previews = root / 'frontend/public/app-icons'


def tauri_icon(source, output, sizes=None):
    args = [node, str(cli), 'icon', str(source), '--output', str(output)]
    if sizes:
        args += ['--png', ','.join(str(size) for size in sizes)]
    subprocess.run(args, check=True)


def icns(iconutil, large, small, output, temporary, tiny=None):
    """An ICNS with the small face at 16 and 32 (and 16@2x) and the large art from 32@2x up. A tiny source replaces the
    small face at 16 only."""
    sized, faces = Path(temporary) / 'sized', Path(temporary) / 'faces'
    tauri_icon(small, faces, [16, 32])
    if tiny:
        tauri_icon(tiny, Path(temporary) / 'tiny', [16])
        shutil.copyfile(Path(temporary) / 'tiny' / '16x16.png', faces / '16x16.png')
    tauri_icon(large, sized, [64, 128, 256, 512, 1024])
    iconset = Path(temporary) / 'dervo.iconset'
    iconset.mkdir()
    for name, source in {
        'icon_16x16.png': faces / '16x16.png', 'icon_16x16@2x.png': faces / '32x32.png',
        'icon_32x32.png': faces / '32x32.png', 'icon_32x32@2x.png': sized / '64x64.png',
        'icon_128x128.png': sized / '128x128.png', 'icon_128x128@2x.png': sized / '256x256.png',
        'icon_256x256.png': sized / '256x256.png', 'icon_256x256@2x.png': sized / '512x512.png',
        'icon_512x512.png': sized / '512x512.png', 'icon_512x512@2x.png': sized / '1024x1024.png',
    }.items():
        shutil.copyfile(source, iconset / name)
    subprocess.run([iconutil, '--convert', 'icns', str(iconset), '--output', str(output)], check=True)
    return sized


# Tauri also generates other platform assets. Retain only the Mac build inputs.
with TemporaryDirectory(prefix='dervo-icons-') as temporary:
    full, small = (Path(temporary) / name for name in ('full', 'small'))
    tauri_icon(icons / 'dervo.svg', full)
    for name in ('icon.png', '128x128.png', '128x128@2x.png', 'icon.icns'):
        shutil.copyfile(full / name, icons / name)
    tauri_icon(icons / 'dervo-small.svg', small, [16, 32])
    shutil.copyfile(small / '32x32.png', icons / '32x32.png')
    iconutil = shutil.which('iconutil')
    if iconutil:
        work = Path(temporary) / 'bundle'
        work.mkdir()
        icns(iconutil, icons / 'dervo.svg', icons / 'dervo-small.svg', icons / 'icon.icns', work)

# The choices only matter on a Mac: the Dock tile plug-in and NSApp's icon read their ICNS files.
listed = json.loads((choices / 'choices.json').read_text())['choices']
if shutil.which('iconutil'):
    previews.mkdir(parents=True, exist_ok=True)
    for choice in listed:
        with TemporaryDirectory(prefix='dervo-icon-' + choice['id'] + '-') as temporary:
            sized = icns(shutil.which('iconutil'), choices / choice['source'], choices / choice['small'],
                         choices / (choice['id'] + '.icns'), temporary,
                         choices / choice['tiny'] if choice.get('tiny') else None)
            shutil.copyfile(sized / '128x128.png', previews / (choice['id'] + '.png'))
