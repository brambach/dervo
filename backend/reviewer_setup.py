"""Create a same-account read-only reviewer and enable it in one local transaction."""
import hashlib
import json
import time
import uuid

from connections_ui import owned_profile
from dervo_connections import Rejected


def setup(collab, args, ctx, binding):
    if binding['visibility'] != 'shared':
        raise Rejected('Use a project conversation to enable review. Private conversations stay private.')
    profile = owned_profile(collab.store, collab.host.root, binding['profile_id'], context=binding.get('context'))
    if args['profileId'] != profile['id'] or args['model'] != binding['model']:
        raise Rejected('This conversation’s account or model changed. Open reviewer setup again.')
    if collab.library is None:
        raise Rejected('The review runtime is unavailable. Reopen Dervo and try again.')
    effort = args['effort']
    if profile['provider'] == 'codex':
        catalog = collab.host.dispatch('model_status', {'profileId': profile['id']})
        entry = next((m for m in catalog.get('models', []) if m.get('model') == binding['model']), None)
        offered = [e.get('reasoningEffort') for e in (entry or {}).get('supportedReasoningEfforts', []) if isinstance(e, dict)]
        if catalog.get('state') != 'catalog' or not entry or effort not in offered:
            raise Rejected('Reload the Codex model options and choose an available review effort.')
    from collaboration import CLAUDE_EFFORTS
    if type(effort) is not str or not effort or (profile['provider'] == 'claude' and effort not in CLAUDE_EFFORTS):
        raise Rejected('Choose an available review effort.')

    # A stable internal name makes a retry reuse its reviewer without taking over a custom agent.
    signature = json.dumps([binding['project_id'], profile['id'], binding['model']])
    name = 'review-' + hashlib.sha256(signature.encode()).hexdigest()[:24]
    db = collab.store.db
    with db:
        policy = collab.policy_record(binding['project_id'])
        desired = {'enabled': True, 'reviewer': name, 'effort': effort}
        if policy['review']['enabled'] and policy['review'] != desired:
            raise Rejected('This project already has a reviewer. Change it in the custom review settings.')
        agent = collab.agent(name)
        if agent:
            if (agent.get('setup_source') != 'same-account-review' or agent['role'] != 'reviewer'
                    or agent['profile_id'] != profile['id'] or agent['model'] != binding['model']
                    or collab.agent_reason(agent, project_id=binding['project_id'], thread_id=binding['id'], profile_id=profile['id'])):
                raise Rejected('The saved reviewer changed. Check the project’s custom review settings.')
        else:
            key = uuid.uuid4().hex
            agent = dict(name=name, profile_id=profile['id'], provider=profile['provider'], model=binding['model'],
                         project_id=binding['project_id'], channel_id=key, role='reviewer', instructions='',
                         created_at=int(time.time()), actor=ctx['actor'], setup_source='same-account-review')
            db.execute('INSERT INTO threads VALUES (?,?,?,?,?,?,?,NULL)',
                       (key, binding['project_id'], key, profile['id'], binding['model'], 'shared', None))
            db.execute('INSERT INTO local_channels VALUES (?,?)', (key, 'Review'))
            db.execute('INSERT INTO collab_agents VALUES (?,?)', (name, json.dumps(agent)))
        policy = {**policy, 'review': collab.check_review(desired, binding['project_id']),
                  'created_at': int(time.time()), 'actor': ctx['actor']}
        db.execute('INSERT INTO collab_policies VALUES (?,?) ON CONFLICT(project_id) DO UPDATE SET document=excluded.document',
                   (binding['project_id'], json.dumps(policy)))
        result = collab.policy_view(binding)
    # No provider turn, coding grant, link, routing change or human acceptance is created here.
    return result
