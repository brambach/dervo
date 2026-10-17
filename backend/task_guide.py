"""Provider-reported plans and manual test steps, separate from host execution evidence.

The guide is also where the coding agent names the thread: its ``title`` (two to four words about the work) becomes the
thread's name once, unless the name was chosen by hand. Every line the agent reports goes through voice.plain before it
is saved, so an em dash never reaches the Progress panel, the sidebar or Home.
"""
import time
from dervo_connections import Rejected
import voice

TOOL = 'dervo_update_task_guide'
LIMITS = {'title': 60, 'goal': 240, 'step': 240, 'why': 600, 'next': 600, 'test_steps': 2400, 'limits': 800}
OPTIONAL = ('title',)  # a guide update without a title is still a guide update
MAX_UPDATES = 24
PROMPT = (
    'Keep the Task guide useful with dervo_update_task_guide. The person reads it to know what you are doing, why, '
    'and what comes next, often after being away. Update it when you have understood the request, when you move '
    'from reading to editing, before you run a check, and before your final answer. Don\'t update it for every '
    'read or repeated status. Write for the person, in plain present-tense words that name the file or behaviour: '
    'title is two to four words about the work, in sentence case (like "Connector picker" or "Fix login"), the same on '
    'every update unless the work changed, and Dervo uses it once as the thread\'s name; '
    'goal is what they will have when you finish; step is what you are doing now, one plain sentence; why is one sentence '
    'on why this step matters to the goal; next is the one concrete thing you will do after it. Before your final '
    'answer, update it with concrete steps to try the result and expected outcomes. Derive any '
    'preview command from project files you actually inspected, name the working directory, '
    'and distinguish source edits, automated checks, build/preview and manual testing. If needed '
    'setup files are outside your grant, explain what is missing instead of inventing commands. '
    'Give user actions in plain language. Only suggest a configured automated check when this task has one. '
    'Never tell the user to invoke an internal tool. For nonvisual code or a text-only answer, do not require '
    'a browser preview. Use empty test_steps while they are not '
    'known. Limits must state what you have not checked. These are your reported plan and test '
    'instructions, not proof of execution, approval, a running preview or task completion. '
    'Do not include secrets. This tool neither runs commands nor grants more access.\n\n'
    'Voice, for the guide and for everything you write to the person: ' + voice.RULES + '\n\n'
    'Your final answer is a message to the person, not a report form. After work: one short paragraph that says '
    'what changed and where (name files or behaviours), one that says what you ran, what it showed and what you '
    'didn\'t run or couldn\'t see, and one line on the one thing to do next, or that there\'s nothing. Five to '
    'twelve sentences, the outcome in the first sentence. No headings, no labels like "Changed:", no bullets unless '
    'you\'re listing more than three files, no preamble, no closing line, no repeat of the request. When you changed '
    'nothing and the person is talking, asking your opinion or thinking out loud, answer the way a colleague would '
    'in conversation, a few short paragraphs, and still say what you didn\'t check.'
)


def offered(task):
    return task.get('origin') != 'collaboration' and not task.get('role') and not task.get('review_phase')


def capture(coding, task, token, args):
    from codex_coding import validate_arguments
    validate_arguments(TOOL, args)
    for field, limit in LIMITS.items():
        if len(args.get(field, '').encode('utf-8')) > limit:
            raise Rejected(f'Shorten {field} to {limit} UTF-8 bytes and retry. No guide update was saved.')
    if not offered(task) or task['state'] != 'working':
        raise Rejected('Task guide updates are only available to this task’s active owner.')
    binding = coding.store.binding(**task['context'])
    if any(task['binding'].get(k) != binding[k] for k in ('profile_id', 'provider', 'identity', 'model', 'cwd', 'visibility')):
        raise Rejected('The task account changed. No guide update was saved.')
    coding.require_grant(task)
    run = task['runs'][-1]
    if run['state'] != 'running' or token['run_id'] != run['id']:
        raise Rejected('A guide update needs this task’s current acknowledged run.')
    if any(not args[field].strip() for field in ('goal', 'step', 'why', 'next', 'limits')):
        raise Rejected('Describe the goal, step, reason, next action and verification limits.')
    count = run.get('guide_updates', 0)
    if count >= MAX_UPDATES:
        return dict(saved=False, detail='This run has reached its guide-update limit. Continue the task and put further updates in your answer.')
    # The agent's words in house style before they are saved: the record holds what the screen shows.
    guide = dict((field, voice.plain(args.get(field, '')).strip()) for field in LIMITS)
    guide.update(run_id=run['id'], tool_token=token['token'], updated_at=int(time.time()))
    run['guide'], run['guide_updates'] = guide, count + 1
    # The steps this run has reported, in order, so Progress can show where the agent is in its own plan.
    steps = run.setdefault('guide_steps', [])
    if not steps or steps[-1]['step'] != guide['step']:
        steps.append(dict(step=guide['step'], at=guide['updated_at']))
    coding.save(task)
    name_thread(coding, task, guide.get('title'))
    return dict(saved=True, source='provider_reported', execution_verified=False,
                detail='Task guide updated. This does not run a check, launch a preview or complete the task.' + memory_reminder(coding, task, run))


# Named exclusions, because a bare reminder made agents save the one thing Dervo records itself: with the first wording,
# m3 and m4 (rename greet, no setup fact in sight) each saved "Tests run with python unittest ..." where three earlier
# real runs of each had saved nothing.
MEMORY_REMINDER = (' Memory step still open: this run has made no dervo_remember call. Before your final answer, save a fact this '
                   'task showed about how the project is set up (package manager, port, host, version, where something lives, a '
                   'test command other than the check Dervo runs) that no note says, or correct a note you found wrong (add with '
                   'replaces). Not the check Dervo already runs, not what you changed, not small talk. If none came up, save nothing.')


def memory_reminder(coding, task, run):
    """One sentence in a guide update's result while this run has made no dervo_remember call: the memory step is the
    one agents skip (eval m1: the finishing list was in the request, the agent read package.json's "pnpm@9", then
    answered without saving it, 20 and 21 Sep). A guide update comes right before the final answer, so its result is
    the last thing the agent reads before deciding what else to do. Nothing once the step is taken, and nothing when
    memory isn't on offer to this task."""
    import project_memory
    if not project_memory.offered(coding, task) or project_memory.REMEMBER_TOOL not in (coding.tools_for(task) if hasattr(coding, 'tools_for') else (project_memory.REMEMBER_TOOL,)):
        return ''
    called = any(isinstance(t, dict) and t.get('run_id') == run.get('id') and t.get('tool') == project_memory.REMEMBER_TOOL for t in task.get('tokens') or [])
    return '' if called or run.get('remembered') else MEMORY_REMINDER


def name_thread(coding, task, title):
    """The thread takes the agent's title once. Naming never fails a guide update: a store problem is reported to the
    host's log and the guide stays saved."""
    if not isinstance(title, str) or not title.strip():
        return False
    try:
        host = getattr(coding, 'host', None)
        organisation = host.organisation() if host is not None and hasattr(host, 'organisation') else None
        if organisation is None:
            from conversation_organisation import Organisation
            organisation = Organisation(coding.store)
        return organisation.take_title(task['context']['thread_id'], title, source=dict(kind='guide', id=task['id']))
    except Exception as exc:  # noqa: BLE001 - the guide is saved; the name is a convenience
        import sys
        print(f'The thread could not take the agent’s title: {exc}', file=sys.stderr)
        return False
