"""Workspace-owned release history. Only explicit user actions restore data."""
import hashlib
import json
from pathlib import Path
import re
from release_handoff import local_path
from dervo_connections import Rejected
import platform_os


def history_root(workspace):
    workspace = local_path(workspace)
    key = hashlib.sha256(str(workspace).encode()).hexdigest()[:24]
    return local_path(platform_os.app_support_dir() / 'Dervo/Version history' / key)


def switch_record(workspace, switch_id):
    if type(switch_id) is not str or not re.fullmatch('[0-9a-f]{32}', switch_id):
        raise Rejected('Choose a saved version from this workspace.')
    folder = local_path(history_root(workspace) / switch_id)
    path = local_path(folder / 'switch.json')
    try:
        record = json.loads(path.read_text())
    except (OSError, ValueError):
        raise Rejected('The saved version record is missing or unreadable.') from None
    if (type(record) is not dict or record.get('format') != 1 or record.get('state') != 'opened'
            or record.get('workspace') != str(local_path(workspace)) or record.get('directory') != str(folder)
            or type(record.get('current')) is not dict or type(record.get('target')) is not dict):
        raise Rejected('This version record doesn’t belong to the workspace or never finished opening.')
    return folder, record


def rollback_choice(workspace, current_id):
    root = history_root(workspace)
    if not root.exists() or not current_id:
        return None
    candidates = []
    for path in root.glob('*/switch.json'):
        try:
            local_path(path)
            candidates.append((path.stat().st_mtime_ns, path))
        except (RuntimeError, OSError):
            continue
    for _, path in sorted(candidates, reverse=True):
        try:
            folder, record = switch_record(workspace, path.parent.name)
            if record.get('rollback') or record['target'].get('id') != current_id:
                continue
            # A Windows update (windows_update) keeps the previous install, not an app bundle to switch back to.
            # Restoring it from Settings comes later; until then Recovery.txt in its folder says how.
            # A Linux update (linux_update) keeps the previous AppImage the same way, and a package keeps nothing.
            if record.get('channel') in ('installer', 'appimage', 'package'):
                continue
            if not (folder / 'backup/backup.json').is_file():
                continue
            return {'switchId': folder.name, 'version': record['current']['id'],
                    'detail': 'Restore the workspace saved before this update. Newer work is kept in a separate backup.'}
        except (Rejected, RuntimeError, OSError, KeyError, TypeError):
            continue
    return None


def prepare_rollback(host, args):
    from release_switch import require_idle
    if type(args) is not dict or set(args) != {'switchId', 'currentExecutable', 'dataOverride', 'parentPid'}:
        raise Rejected('Unsupported rollback request')
    if type(args['dataOverride']) is not bool or type(args['parentPid']) is not int or args['parentPid'] < 2:
        raise Rejected('The native app must select this workspace.')
    require_idle(host)
    folder, record = switch_record(host.root, args['switchId'])
    executable = local_path(args['currentExecutable'])
    if str(executable) != record['target'].get('executable') or args['dataOverride'] != record.get('dataOverride'):
        raise Rejected('This rollback belongs to a different app version or workspace mode.')
    if record.get('rollback'):
        raise Rejected('Choose the original update record to restore its earlier workspace.')
    return {'format': 1, 'target': record['current']['bundle'], 'current': record['target']['bundle'],
            'releaseId': record['current']['id'], 'workspace': str(local_path(host.root)),
            'dataOverride': args['dataOverride'], 'parentPid': args['parentPid'], 'rollback': folder.name}
