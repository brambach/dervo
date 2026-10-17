"""Beta.16's shared operations: the ones other threads asked b16/bridge for (PLAN-beta16-build-20260925.md, "Asks
between threads").

b16/lifecycle's (idle threads, Next up, tester reports) are its own: it built them first, in project_lifecycle.FIELDS
and next_up.FIELDS, which the host routes before anything here, and Bryce chose its names (25 Sep). They're listed in
LIFECYCLE only so src/bridge.rs lets the renderer send them; their fields, types and screens stay in b16/lifecycle.

b16/git's (GitHub and pull requests) aren't built yet, so their names, fields and field types are fixed here first and
the owner only writes the handler. The same names are in src/bridge.rs and frontend/src/local/api.ts
(test_shared_operations checks they agree).

The host checks every request against its spec before the owner sees it: the exact fields, each field's type and
range. Then it calls ``shared_dispatch(host, operation, args, actor)`` in the owner's module. Until that module has
one, the operation answers that it isn't in this build, and nothing else happens.

None of these runs a command on the renderer's say-so. The outward-facing GitHub steps (push and open a pull
request) take the id of the confirm the user saw (``previewId``), the way push_start takes push_preview's.
"""
from dervo_connections import Rejected

ID_LIMIT = 64
PR_MODES = ('thread', 'branch')  # this thread's commits on their own branch (the default), or everything on the current branch


def _id(value):
    return type(value) is str and 0 < len(value) <= ID_LIMIT


def _flag(value):
    return type(value) is bool


TYPES = {
    'projectId': _id, 'channelId': _id, 'previewId': _id,
    'draft': _flag, 'refresh': _flag, 'recheck': _flag,
    'mode': lambda value: value in PR_MODES,
}


def op(owner, module, required, optional=(), *, archived_thread=False):
    """``archived_thread``: allowed on an archived conversation. Everything else waits for Restore, as elsewhere."""
    return dict(owner=owner, module=module, required=frozenset(required), optional=frozenset(optional), archived_thread=archived_thread)


# b16/lifecycle's operations, as its screens send them. Handled by its project_lifecycle.dispatch, never by dispatch
# below; after the merge test_shared_operations checks this list against its FIELDS.
LIFECYCLE = ('conversations_tidy', 'conversations_tidied', 'conversations_tidy_undo', 'conversation_pin', 'conversation_pins',
             'project_tidy_setting', 'project_tidy_set',
             'next_up_read', 'next_up_add', 'next_up_move', 'next_up_ship', 'next_up_work', 'next_up_reports_here',
             'tester_reports', 'tester_report_mark', 'tester_report_add')
GIT = 'b16/git'
OPERATIONS = {
    # GitHub (b16/git), through the gh you're signed in to, never a token Dervo holds. Status and PR status only read,
    # as you. pr_thread_branch says what a branch of only this thread's commits would hold, and writes nothing.
    # pr_create pushes and opens the pull request the confirm showed, and only that one (``previewId``).
    'github_status': op(GIT, 'github_host', {'projectId'}, {'recheck'}),
    'pr_preview': op(GIT, 'github_host', {'projectId', 'channelId', 'mode'}),
    'pr_create': op(GIT, 'github_host', {'projectId', 'channelId', 'previewId', 'draft'}),
    'pr_status': op(GIT, 'github_host', {'projectId', 'channelId'}, {'refresh'}, archived_thread=True),
    'pr_thread_branch': op(GIT, 'github_host', {'projectId', 'channelId'}),
}
NOT_BUILT = 'This isn’t in this build of Dervo yet.'


def check(operation, args):
    """The request's fields against the spec, or a refusal that names no value."""
    spec = OPERATIONS[operation]
    if not isinstance(args, dict) or not spec['required'] <= set(args) <= spec['required'] | spec['optional']:
        raise Rejected('Unsupported operation or fields')
    for key, value in args.items():
        if not TYPES[key](value):
            raise Rejected(f'{key} isn’t valid for {operation}')
    return spec


def dispatch(host, operation, args, actor):
    """Checked, then handed to the owner's ``shared_dispatch``. The host has already applied the archived-project rule;
    the archived-thread rule is here, from the spec."""
    import importlib
    spec = check(operation, args)
    if 'channelId' in args and not spec['archived_thread'] and host.store.db.execute(
            'SELECT 1 FROM conversation_archives WHERE thread_id=?', (args['channelId'],)).fetchone():
        raise Rejected('This conversation is archived. Restore it before opening or changing it.')
    try:
        module = importlib.import_module(spec['module'])
    except ModuleNotFoundError as exc:
        if exc.name != spec['module']:
            raise
        raise Rejected(NOT_BUILT) from None
    handler = getattr(module, 'shared_dispatch', None)
    if handler is None or operation not in getattr(module, 'SHARED', ()):
        raise Rejected(NOT_BUILT)
    return handler(host, operation, dict(args), actor)
