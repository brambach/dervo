"""Reopen the explicitly selected version without changing a retained app bundle."""
import json
import sys
from release_handoff import RECOVERY_MARKER, local_path
from release_history import history_root


def publish(workspace, target, data_override):
    root = history_root(workspace)
    path = local_path(root / 'selected.json')
    temporary = local_path(root / 'selected.tmp')
    record = {'format': 1, 'workspace': str(local_path(workspace)), 'target': target, 'dataOverride': data_override}
    temporary.write_text(json.dumps(record, indent=2)); temporary.chmod(0o600); temporary.replace(path)


def selected_target(workspace, current_executable, data_override):
    workspace = local_path(workspace)
    current = local_path(current_executable)
    marker = workspace / RECOVERY_MARKER
    if marker.exists() or marker.is_symlink():
        raise RuntimeError('Finish the interrupted workspace restore before reopening a version.')
    path = local_path(history_root(workspace) / 'selected.json')
    if not path.exists():
        return None
    record = json.loads(path.read_text())
    if (type(record) is not dict or record.get('format') != 1 or record.get('workspace') != str(workspace)
            or record.get('dataOverride') is not data_override or type(record.get('target')) is not dict):
        raise RuntimeError('The selected version record doesn’t match this workspace.')
    target = record['target']
    # Do not loop when the selected app opens itself. Store still enforces the
    # restore marker, and normal startup handles missing runtime prerequisites.
    if target.get('executable') == str(current):
        return None
    from release_switch import verify_bundle
    verified = verify_bundle(target['bundle'], release_id=target['id'], handoff=True)
    if verified != target:
        raise RuntimeError('The selected Dervo version has changed. Open its release folder for recovery.')
    return verified['executable']


if __name__ == '__main__':
    try:
        args = json.load(sys.stdin)
        if type(args) is not dict or set(args) != {'workspace', 'currentExecutable', 'dataOverride'} or type(args['dataOverride']) is not bool:
            raise RuntimeError('The native version selection request is invalid.')
        result = selected_target(args['workspace'], args['currentExecutable'], args['dataOverride'])
        print(json.dumps({'executable': result}))
    except Exception as exc:
        print(json.dumps({'error': str(exc)}))
        sys.exit(1)
