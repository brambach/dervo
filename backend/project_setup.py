"""Keep a native folder choice separate from account setup. Never accept a renderer path."""
from pathlib import Path
import time
import unicodedata
import uuid
from dervo_connections import Rejected

FIELDS = {'project_setup_save': {'selectionId', 'profileId', 'model', 'name'}}

HOME_REFUSAL = 'Your home folder can’t be a project. Conversations started there stay where they are.'

def refuse_unsafe(host, folder):
    """The folders no project can be. Your home folder never comes in (onboarding_host._keys holds the same rule for the
    whole-Mac look), nor a whole disk, nor anything holding Dervo's own Store or an account's sign-in folder: the overview
    and every coding grant would refuse them later, after the project was already made (RC review, 9 Oct)."""
    import os
    import onboarding_host
    home = Path(os.path.realpath(str(onboarding_host.HOME) if onboarding_host.HOME else os.path.expanduser('~')))
    if folder == home:
        raise Rejected(HOME_REFUSAL)
    if folder.parent == folder or home.is_relative_to(folder):
        raise Rejected('That folder holds your whole home folder, so it can’t be a project. Choose the folder of one project.')
    roots = [Path(host.root).resolve()] + [Path(row[0]).resolve() for row in host.store.db.execute('SELECT home FROM profiles') if row[0]]
    if any(folder.is_relative_to(root) or root.is_relative_to(folder) for root in roots):
        raise Rejected('That folder holds Dervo’s own data or an account’s sign-in, so it can’t be a project. Choose the folder of one project.')

def stage(host, path):
    folder = Path(path).resolve(strict=True)
    if not folder.is_dir():
        raise Rejected('Choose a project folder.')
    refuse_unsafe(host, folder)
    stat = folder.stat()
    selection = {'id': uuid.uuid4().hex, 'path': str(folder), 'identity': (stat.st_dev, stat.st_ino),
                 'created': time.monotonic(), 'saved': None}
    host.picked_folder = selection
    return {'selectionId': selection['id'], 'path': str(folder), 'name': folder.name}

def save(host, args):
    if type(args) is not dict or set(args) != FIELDS['project_setup_save']:
        raise Rejected('Unsupported project setup fields.')
    selection = getattr(host, 'picked_folder', None)
    if not selection or args['selectionId'] != selection['id']:
        raise Rejected('Choose the project folder again. Its previous selection is no longer available.')
    signature = {k:args[k] for k in ('profileId', 'model', 'name')}
    if selection['saved']:
        if signature != selection['saved']['args']:
            raise Rejected('This project was already created. Open it or choose another folder.')
        return selection['saved']['result']
    if time.monotonic() - selection['created'] > 1800:
        raise Rejected('Choose the project folder again to finish setup.')
    for field, limit in [('profileId',256), ('model',256), ('name',80)]:
        value = args[field]
        if type(value) is not str or not value.strip() or len(value.strip()) > limit or any(unicodedata.category(c).startswith('C') for c in value):
            raise Rejected('Choose an account, model and a project name with 1 to 80 characters.')
    if args['model'] != args['model'].strip():
        raise Rejected('The model name has extra spaces. Select the model again.')
    from connections_ui import owned_profile
    # Add project doesn't ask Work or Personal: the account you pick decides it, and the project keeps it from then on.
    owned_profile(host.store, host.root, args['profileId'])
    folder = Path(selection['path'])
    try:
        stat = folder.stat()
        if folder.resolve(strict=True) != folder or not folder.is_dir() or (stat.st_dev,stat.st_ino) != selection['identity']:
            raise Rejected('The selected folder changed. Choose it again.')
    except OSError as exc:
        raise Rejected('The selected folder is no longer available. Choose it again.') from exc
    refuse_unsafe(host, folder)
    project_id, channel_id = make(host, str(folder), args['profileId'], args['model'], args['name'].strip())
    result = {'id':project_id,'channelId':channel_id,'name':args['name'].strip()}
    selection['saved'] = {'args':signature,'result':result}
    return result


def make(host, path, profile_id, model, name, context=None):
    """Write one project from a checked folder, account, model and name: the project, its name and its starter thread.
    Add project (save) and the whole-Mac onboarding (onboarding_host.bring_in) both make projects here, so they can't
    drift apart (behaviour review N3, 24 Sep). The callers do the checking. ``context`` is Work or Personal; without it
    the project takes its account's, and either way the account has to match it."""
    from dervo_connections import account_context, require_context
    project_id = uuid.uuid4().hex
    db = host.store.db
    organisation = host.organisation()
    channel_id = uuid.uuid4().hex
    context = context or account_context(db, profile_id)
    require_context(db, context, profile_id)
    with db:
        db.execute('INSERT INTO projects (id, cwd, profile_id, model, context) VALUES (?,?,?,?,?)', (project_id,path,profile_id,model,context))
        db.execute('INSERT INTO project_names VALUES (?,?)', (project_id,name))
        db.execute('INSERT INTO threads VALUES (?,?,?,?,?,?,?,NULL)', (channel_id,project_id,channel_id,profile_id,model,'shared',None))
        db.execute('INSERT INTO local_channels VALUES (?,?)', (channel_id,'New conversation'))
        # The host made this thread, not the person (project_lifecycle.schema says what reads the mark).
        db.execute("INSERT INTO conversation_origins VALUES (?,'starter')", (channel_id,))
        organisation.initial(channel_id, automatic=True)
    return project_id, channel_id
