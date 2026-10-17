"""Explicit read-only TypeScript inputs, copied and hashed before confined execution.

Opt in with root tsconfig.json's dervoInputs list. List every declaration, package
metadata file, original licence and root-level config import. No resolution walks,
package scripts, executable inputs, installation or cache paths occur here.
The sole data input is root preview-features.json, with its public feature schema.
"""
import json
import os
from pathlib import Path
import re
import stat

from dervo_connections import Rejected
import check_profiles
import platform_os

FILE_CAP = 2048
BYTE_CAP = 32 * 1024 * 1024
SINGLE_CAP = 4 * 1024 * 1024
PREVIEW_JSON = 'preview-features.json'
PREVIEW_CAP = 65536
CONFIG = re.compile(r'tsconfig(?:\.[A-Za-z0-9_-]+)?\.json')
DECLARATION = re.compile(r'.*\.d\.(?:ts|mts|cts)')
LICENSE = re.compile(r'(?!.*\.(?:js|cjs|mjs|ts|tsx|sh|py)$)(?:licen[cs]e|copying|notice)(?:[._-][A-Za-z0-9_.-]+)?', re.I)
BLOCKED = {'auth', 'authentication', 'credential', 'credentials', 'profile', 'profiles', 'data', 'tooling', 'store', 'stores', 'home'}


def _path(path):
    if type(path) is not str or not path or '\\' in path or '\x00' in path:
        raise Rejected('TypeScript input must be an explicit relative path')
    parts = path.split('/')
    if any(p in ('', '.', '..') or p.startswith('.') or re.split(r'[._-]', p.lower())[0] in BLOCKED for p in parts):
        raise Rejected('Sensitive or escaping TypeScript input refused')
    return parts


def _kind(path):
    parts = _path(path)
    if path == PREVIEW_JSON:
        return 'preview-manifest', None
    if len(parts) == 1 and CONFIG.fullmatch(path):
        return 'config', None
    if parts[0] != 'node_modules' or len(parts) < 3:
        raise Rejected('Only root tsconfig imports and explicit node_modules declarations, metadata and licences are supported')
    count = 3 if parts[1].startswith('@') else 2
    if len(parts) <= count or parts[1] == 'typescript' or 'node_modules' in parts[1:]:
        raise Rejected('Unsupported TypeScript dependency input path')
    package = '/'.join(parts[:count])
    if DECLARATION.fullmatch(parts[-1]):
        return 'declaration', package
    if parts[-1] == 'package.json':
        return 'metadata', package
    if LICENSE.fullmatch(parts[-1]):
        return 'licence', package
    raise Rejected('Executable, build-script or unsupported TypeScript package input refused: ' + path)


def _read(root, path, remaining):
    descriptors = []
    try:
        resolved = Path(root).resolve()
        fd = platform_os.open_root(resolved)
        descriptors.append(fd)
        for part in tuple(platform_os.below_root(resolved)) + tuple(_path(path)[:-1]):
            fd = platform_os.open_dir_at(part, fd)
            descriptors.append(fd)
        fd = platform_os.open_file_at(_path(path)[-1], fd)
        descriptors.append(fd)
        before = os.fstat(fd)
        if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1:
            raise Rejected('TypeScript input must be a single-link regular file: ' + path)
        if before.st_size > min(SINGLE_CAP, remaining):
            raise Rejected('TypeScript dependency input byte cap exceeded: ' + path)
        chunks, left = [], before.st_size + 1
        while left:
            chunk = os.read(fd, min(left, 1 << 20))
            if not chunk:
                break
            chunks.append(chunk)
            left -= len(chunk)
        data = b''.join(chunks)
        after = os.fstat(fd)
        if len(data) != before.st_size or (before.st_size, before.st_mtime_ns, before.st_ctime_ns) != (after.st_size, after.st_mtime_ns, after.st_ctime_ns):
            raise Rejected('TypeScript input changed while being read: ' + path)
        return data
    except OSError as exc:
        raise Rejected(f'TypeScript input {path} cannot be read without symlinks: {exc}') from exc
    finally:
        for fd in reversed(descriptors):
            os.close(fd)


def _json(data, path):
    try:
        value = json.loads(data.decode('utf-8'))
    except (ValueError, UnicodeDecodeError) as exc:
        raise Rejected(f'TypeScript metadata {path} is not JSON: {exc}') from exc
    if type(value) is not dict:
        raise Rejected('TypeScript metadata must be an object: ' + path)
    return value


def _preview_manifest(data):
    """A bounded public feature manifest, never a grant to arbitrary JSON trees."""
    value = _json(data, PREVIEW_JSON)
    def refuse():
        raise Rejected('Preview manifest must contain only bounded public feature definitions')
    if set(value) != {'version', 'features'} or type(value['version']) is not int or not 0 <= value['version'] <= 2147483647:
        refuse()
    features = value['features']
    if type(features) is not list or len(features) > 128:
        refuse()
    ids = set()
    for feature in features:
        if type(feature) is not dict or not {'id', 'name', 'description'} <= set(feature) \
                or set(feature) - {'id', 'name', 'description', 'defaultEnabled', 'platforms'}:
            refuse()
        for key, limit in (('id', 128), ('name', 256), ('description', 2048)):
            text = feature[key]
            if type(text) is not str or len(text) > limit or (key != 'description' and not text) \
                    or any(ord(char) < 32 for char in text):
                refuse()
        if not re.fullmatch(r'[A-Za-z][A-Za-z0-9_-]*', feature['id']) or feature['id'] in ids:
            refuse()
        ids.add(feature['id'])
        if 'defaultEnabled' in feature and type(feature['defaultEnabled']) is not bool:
            refuse()
        if 'platforms' in feature and (type(feature['platforms']) is not list or len(feature['platforms']) > 2
                or any(type(p) is not str or p not in ('desktop', 'mobile') for p in feature['platforms'])):
            refuse()


def _metadata(data, path):
    meta = _json(data, path)
    # These fields can affect declaration resolution. Scripts/bin are inert JSON,
    # never execution instructions. Relative parent hops and URLs aren't supported.
    def targets(value):
        if isinstance(value, dict):
            for item in value.values():
                targets(item)
        elif isinstance(value, list):
            for item in value:
                targets(item)
        elif isinstance(value, str) and value:
            check_profiles._inside_project(value, path)
            if ':' in value:
                raise Rejected('Unsupported package resolution target: ' + path)
    for key in ('types', 'typings', 'exports', 'imports', 'typesVersions', 'main', 'module'):
        if key in meta:
            targets(meta[key])


def configs(content, *, root_config=None):
    """Validate only copied config bytes. Root-level relative extends has no rebasing ambiguity."""
    visiting = set()
    parsed = {} if root_config is None else {'tsconfig.json': root_config}
    resolved = {}
    def visit(path):
        if len(visiting) >= 32:
            raise Rejected('TypeScript config import depth cap exceeded')
        if path in visiting:
            raise Rejected('TypeScript config import cycle refused')
        if path not in content:
            raise Rejected('Config import must be an explicit dervoInputs entry: ' + path)
        if path in resolved:
            merged, depth = resolved[path]
            if len(visiting) + depth > 32:
                raise Rejected('TypeScript config import depth cap exceeded')
            return merged
        visiting.add(path)
        if path not in parsed:
            parsed[path] = check_profiles.validate_tsconfig(content[path], bounded=True)
        config = parsed[path]
        merged, depth = {}, 1
        if 'extends' in config:
            parent = config['extends']
            if type(parent) is not str or not parent.startswith('./') or not CONFIG.fullmatch(parent[2:]):
                raise Rejected('Only explicit ./tsconfig.name.json config imports are supported')
            merged.update(visit(parent[2:]))
            depth += resolved[parent[2:]][1]
        merged.update({k: v for k, v in config.items() if k != 'compilerOptions'})
        merged['compilerOptions'] = {**merged.get('compilerOptions', {}), **config.get('compilerOptions', {})}
        visiting.remove(path)
        resolved[path] = merged, depth
        return merged
    effective = visit('tsconfig.json')
    # Validate every imported config, even an unused listed config, before launch.
    for path in content:
        if CONFIG.fullmatch(path):
            visit(path)
    if not effective.get('files') or effective.get('include'):
        raise Rejected('Bounded TypeScript inputs require explicit nonempty files and no include patterns; no-source project checks are refused')
    for path in effective['files']:
        _path(path)
        if path.startswith('node_modules/') or not path.endswith(('.ts', '.tsx', '.mts', '.cts')) or DECLARATION.fullmatch(path):
            raise Rejected('Bounded TypeScript files must name actual project source files')
    return effective


def collect(root):
    root_data = _read(root, 'tsconfig.json', min(BYTE_CAP, 262144))
    config = check_profiles.validate_tsconfig(root_data)
    if 'dervoInputs' not in config:
        return {}
    paths = config['dervoInputs']
    if type(paths) is not list or not paths or len(paths) + 1 > FILE_CAP:
        raise Rejected('dervoInputs must be a nonempty explicit file list within the file cap')
    if any(type(p) is not str for p in paths) or len(set(paths)) != len(paths) or 'tsconfig.json' in paths:
        raise Rejected('dervoInputs needs distinct explicit paths, excluding the root config')
    kinds = {p: _kind(p) for p in paths}  # validate the entire list before reading any input
    content = {'tsconfig.json': root_data}
    total = len(root_data)
    for path in paths:
        data = _read(root, path, min(BYTE_CAP - total, PREVIEW_CAP) if path == PREVIEW_JSON else BYTE_CAP - total)
        total += len(data)
        content[path] = data
    packages = {package for kind, package in kinds.values() if package}
    for package in packages:
        if package + '/package.json' not in content:
            raise Rejected('Package needs explicit package.json: ' + package)
        if not any(kind == 'licence' and pkg == package for kind, pkg in kinds.values()):
            raise Rejected('Package needs its original licence in dervoInputs: ' + package)
    for path, (kind, _) in kinds.items():
        if kind == 'metadata':
            _metadata(content[path], path)
        elif kind == 'preview-manifest':
            _preview_manifest(content[path])
    configs(content, root_config=config)
    return content


def validate_snapshot(content):
    config = check_profiles.validate_tsconfig(content['tsconfig.json'])
    if 'dervoInputs' in config:
        effective = configs(content, root_config=config)
        if any(path not in content for path in effective['files']):
            raise Rejected('Requested TypeScript source file is missing from the task snapshot')
    else:
        if 'files' in config and not any(p in content and p.endswith(('.ts', '.tsx', '.mts', '.cts')) and not DECLARATION.fullmatch(p) for p in config['files']):
            raise Rejected('No-source TypeScript config cannot pass a project check')
        if not any(not p.startswith('node_modules/') and p.endswith(('.ts', '.tsx', '.mts', '.cts')) and not DECLARATION.fullmatch(p) for p in content):
            raise Rejected('TypeScript check needs actual project source in the snapshot')
