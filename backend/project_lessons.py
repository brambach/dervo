"""Task lessons. Since beta.16 (b16/memory; Bryce, 25 Sep) a lesson saves at once as a project memory note, with its
evidence, and reaches later runs as a background fact like any note. It never becomes a rule by itself: only the
person writes or promotes rules. Suggestions saved before beta.16 still wait in the project_lessons queue, and
Knowledge shows them until someone keeps or dismisses each one."""
import json
import uuid
from datetime import datetime, timezone
from dervo_connections import Rejected
import secret_scan

TOOL = 'dervo_suggest_lesson'
PROMPT = ('Lesson suggestions are enabled for this shared project account. If this task reveals a useful '
          'project decision, repeated mistake or reusable procedure, call dervo_suggest_lesson once with the lesson in '
          'one or two sentences (at most 400 characters) and the concrete evidence for it. Skip routine summaries, secrets '
          'and speculative rules. State failed or unverified outcomes honestly. It saves at once as a project note that '
          'later chats get as a background fact, and the person can edit or delete it in Knowledge. It is not a rule.')


def schema(db):
    db.execute('CREATE TABLE IF NOT EXISTS project_lesson_preferences (project_id TEXT, profile_id TEXT, enabled INTEGER NOT NULL, PRIMARY KEY(project_id,profile_id))')
    db.execute('CREATE TABLE IF NOT EXISTS project_lessons (id TEXT PRIMARY KEY, project_id TEXT NOT NULL, profile_id TEXT NOT NULL, task_id TEXT NOT NULL, run_id TEXT NOT NULL, status TEXT NOT NULL, document TEXT NOT NULL, UNIQUE(task_id,run_id))')
    db.execute('CREATE INDEX IF NOT EXISTS project_lessons_pending ON project_lessons(project_id,profile_id,status)')


class ProjectLessons:
    def __init__(self, knowledge):
        self.knowledge, self.store = knowledge, knowledge.store

    def enabled(self, b):
        """The switch is the project's, like its rules (beta.11, F2): a chat on another personal account reads it too."""
        scope = self.knowledge.rules_scope(b)
        row = self.store.db.execute('SELECT enabled FROM project_lesson_preferences WHERE project_id=? AND profile_id=?',
                                   (scope['project_id'], scope['profile_id'])).fetchone()
        return bool(row and row[0] == 1)

    def set_enabled(self, b, enabled):
        if type(enabled) is not bool:
            raise Rejected('Choose whether Dervo should suggest task lessons.')
        scope = self.knowledge.rules_scope(b)
        with self.store.db:
            self.store.db.execute('INSERT INTO project_lesson_preferences VALUES (?,?,?) ON CONFLICT(project_id,profile_id) DO UPDATE SET enabled=excluded.enabled',
                                  (scope['project_id'], scope['profile_id'], int(enabled)))
        return dict(enabled=enabled)

    def offered(self, task, b):
        if task.get('origin') == 'collaboration' or task.get('role') or task.get('review_phase'):
            return False
        from dervo_connections import same_side
        if b['category'] not in ('personal', 'work') or b['visibility'] != 'shared':
            return False
        try:
            # Lessons stay on the project's own side, Work or Personal.
            return same_side(self.knowledge.rules_scope(b)['category'], b['category']) and self.enabled(b)
        except Rejected:
            return False

    def capture(self, task, args):
        from codex_coding import validate_arguments
        validate_arguments(TOOL, args)
        for field, limit in (('lesson',4096),('evidence',800)):
            if len(args[field].encode('utf-8'))>limit:
                raise Rejected(f'Shorten {field} to {limit} UTF-8 bytes and retry. No lesson was saved.')
        b = self.store.binding(**task['context'])
        if not self.offered(task, b) or task['state'] != 'working':
            raise Rejected('Task lesson suggestions are unavailable for this task or have been turned off.')
        if any(task['binding'].get(k) != b[k] for k in ('profile_id','provider','identity','model','cwd','visibility')):
            raise Rejected('The task account changed. No lesson was saved.')
        if any(not args[k].strip() for k in ('lesson','evidence')):
            raise Rejected('Describe the lesson and the evidence for it.')
        run = task['runs'][-1]
        if secret_scan.find_any(args['lesson'], args['evidence']):
            # The thread says a lesson was held back, never its words. The host saves the task after this tool result.
            run.setdefault('remembered', []).append(dict(action='withheld', what='lesson', at=int(datetime.now(timezone.utc).timestamp())))
            run['remembered'] = run['remembered'][-20:]
            raise Rejected(secret_scan.REFUSAL.replace('Nothing was saved.', 'No lesson was saved.'))
        if run['state'] != 'running':
            raise Rejected('A lesson needs an acknowledged running task.')
        # One lesson a run, as before: the same words again return the saved one, other words are refused.
        earlier = next((r for r in run.get('remembered') or [] if r.get('kind') == 'lesson' and r.get('entry_id')), None)
        if earlier is not None:
            if earlier.get('text') == ' '.join(args['lesson'].split()):
                # Nothing changes, and the thread says so, as a note saved twice does (behaviour review S6, 25 Sep).
                run['remembered'] = (run.get('remembered') or []) + [dict(action='duplicate', entry_id=earlier['entry_id'], text=earlier['text'],
                                                                          at=int(datetime.now(timezone.utc).timestamp()))]
                return dict(saved=False, entry_id=earlier['entry_id'], detail='This lesson is already saved as a project note.')
            raise Rejected('This run already saved a lesson. The person can edit it in Knowledge. No lesson was saved.')
        import project_memory
        result = project_memory.note_from_lesson(self.store, task, args['lesson'].strip(), args['evidence'].strip())
        if result.get('saved') and ', marked ' not in result['detail']:
            result = dict(result, detail=result['detail'].replace('Saved to project memory.', 'Saved as a project note.', 1)
                          + ' It is a background fact for later chats, not a rule. The person can edit or delete it in Knowledge.')
        return result

    def _queue(self, task, b, run, text, evidence, again):
        """Put one suggestion in the pre-beta.16 queue. Nothing calls this since lessons became notes; it stays so the
        queue's shape is documented beside the records it made, and for tests that seed the queue."""
        scope = self.knowledge.rules_scope(b)
        with self.store.db:
            self.store.db.execute('BEGIN IMMEDIATE')
            row = self.store.db.execute('SELECT document FROM project_lessons WHERE task_id=? AND run_id=?', (task['id'],run['id'])).fetchone()
            if row:
                saved = json.loads(row[0])
                if saved['text'] != text or saved['evidence'] != evidence:
                    raise Rejected(again)
            else:
                saved = dict(id=str(uuid.uuid4()), text=text, evidence=evidence,
                             created_at=datetime.now(timezone.utc).isoformat(), provider=b['provider'],
                             source=dict(channelId=task['context']['thread_id'],taskId=task['id'],runId=run['id']))
                self.store.db.execute('INSERT INTO project_lessons VALUES (?,?,?,?,?,?,?)',
                                     (saved['id'],scope['project_id'],scope['profile_id'],task['id'],run['id'],'pending',json.dumps(saved)))
                # The thread's Progress panel says a lesson is waiting (beta.11). The host saves the task after this tool result.
                run.setdefault('remembered', []).append(dict(action='lesson', entry_id=saved['id'], text=saved['text'], at=int(datetime.now(timezone.utc).timestamp())))
                run['remembered'] = run['remembered'][-20:]
        return saved

    def get(self, b, actor, lesson_id):
        if not isinstance(lesson_id, str) or not lesson_id or len(lesson_id)>256:
            raise Rejected('Choose a saved lesson suggestion.')
        row = self.store.db.execute('SELECT status,document FROM project_lessons WHERE id=? AND project_id=? AND profile_id=?',
                                   (lesson_id,b['project_id'],b['profile_id'])).fetchone()
        if not row:
            raise Rejected('Lesson suggestion is unavailable in this project account.')
        item = json.loads(row[1])
        self.knowledge.source(item['source'],b,actor)
        return row[0], item

    def pending(self, b, actor, before=None):
        if before is not None and (type(before) is not int or not 0<before<=9223372036854775807):
            raise Rejected('Choose a valid saved lesson page.')
        result=[]
        # Indexed bounded read, only when Knowledge is opened or explicitly refreshed.
        ids=self.store.db.execute('SELECT rowid,id FROM project_lessons WHERE project_id=? AND profile_id=? AND status=?'
                                 +(' AND rowid<?' if before is not None else '')+' ORDER BY rowid DESC LIMIT 51',
                                 (b['project_id'],b['profile_id'],'pending')+((before,) if before is not None else ())).fetchall()
        for row in ids[:50]:
            try:
                _, item=self.get(b,actor,row[1])
            except Rejected:
                continue
            task=json.loads(self.store.db.execute('SELECT document FROM coding_tasks WHERE id=?',(item['source']['taskId'],)).fetchone()[0])
            run=next(r for r in task['runs'] if r['id']==item['source']['runId'])
            result.append(dict(item,run_state=run['state'],task_state=task['state']))
        return dict(items=result,limited=len(ids)>50,nextCursor=ids[49][0] if len(ids)>50 else None)

    def dismiss(self, b, actor, lesson_id):
        with self.store.db:
            self.store.db.execute('BEGIN IMMEDIATE')
            status, _=self.get(b,actor,lesson_id)
            if status=='saved':
                raise Rejected('This lesson is already saved. Manage its version in Your guidance.')
            self.store.db.execute("UPDATE project_lessons SET status='dismissed' WHERE id=?",(lesson_id,))
        return dict(dismissed=True)
