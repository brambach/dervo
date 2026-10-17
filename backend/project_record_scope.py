"""Project-owned saved records, without creating or selecting a conversation."""
from dervo_connections import CONTEXTS, Rejected


def project_record_scope(store, project_id, profile_id):
    if any(type(value) is not str or not value or len(value) > 256
           for value in (project_id, profile_id)):
        raise Rejected('Choose a project and its connected account.')
    row = store.db.execute('''SELECT projects.id AS project_id, projects.cwd,
        projects.model, profiles.id AS profile_id, profiles.provider,
        profiles.identity, profiles.category, COALESCE(projects.context, profiles.category) AS context
        FROM projects JOIN profiles ON profiles.id=projects.profile_id
        WHERE projects.id=?''', (project_id,)).fetchone()
    # The project's own account, on the project's Work or Personal side (both sides work since 26 Sep 2026).
    if not row or row['profile_id'] != profile_id or row['category'] not in CONTEXTS or row['category'] != row['context']:
        raise Rejected('The project account changed or is unavailable.')
    # Host dispatch checks archives too. Keep this saved-record boundary explicit.
    if store.db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='project_archives'").fetchone():
        if store.db.execute('SELECT 1 FROM project_archives WHERE project_id=?', (project_id,)).fetchone():
            raise Rejected('Restore this project before opening its saved work.')
    return dict(row)
