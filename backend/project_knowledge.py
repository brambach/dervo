"""Human-approved local project guidance. Immutable versions and revision-checked events."""

import json
import re
import uuid
from datetime import datetime, timezone
from dervo_connections import Rejected, same_side


def request_context(base, guidance, repository=''):
    """Present saved guidance before the task, retaining its exact delivery record. The repository's instruction files
    (repo_guidance.py) come first and the user's approved rules after them, so the user's rules win where they disagree."""
    if not guidance and not repository:
        return base
    return ((repository + '\n' if repository else '')
            + ('Dervo saved project guidance for this request (approved by the user):\n' + guidance + '\n\n' if guidance else '')
            + 'This task, from Dervo (the user\'s own message is the Request at the end):\n' + base)

SCOPE = {'projectId','channelId','profileId'}
FIELDS = {'knowledge_list':SCOPE, 'knowledge_save':SCOPE|{'ruleId','expectedRevision','text','provenance','source'},
          'knowledge_approve':SCOPE|{'ruleId','versionId','expectedRevision'},
          'knowledge_archive':SCOPE|{'ruleId','expectedRevision'}, 'knowledge_adopt':SCOPE|{'procedureId'},
          'knowledge_lessons_set':SCOPE|{'enabled'}, 'knowledge_lesson_keep':SCOPE|{'lessonId'},
          'knowledge_lesson_dismiss':SCOPE|{'lessonId'}, 'knowledge_lesson_list':SCOPE|{'before'},
          # Design sweep, 19 Sep: undo in Knowledge, dismissing a rule version, and the repository's instruction files.
          'knowledge_lesson_reopen':SCOPE|{'lessonId'}, 'knowledge_version_dismiss':SCOPE|{'ruleId','versionId','expectedRevision','dismissed'},
          'knowledge_note_restore':SCOPE|{'entryId'}, 'knowledge_repo_list':SCOPE, 'knowledge_repo_send':SCOPE|{'path','send'}, 'knowledge_repo_open':SCOPE|{'path','how'},
          # beta.11 (fix-memory, F1): agents' notes and the two memory switches, read and set from Knowledge with no conversation.
          'knowledge_notes_list':SCOPE, 'knowledge_notes_set':SCOPE|{'enabled'}, 'knowledge_notes_reflect_set':SCOPE|{'enabled'}, 'knowledge_note_remove':SCOPE|{'entryId'},
          # beta.16 (b16/memory): nothing waits for a click, so what you do to a note is on the page and on the reply.
          # Edit keeps the old wording in its history, Keep clears a note's mark, Seen clears New and the sidebar dot,
          # and Keep both answers an Out of date? card. None of them makes a note a rule or grants anything.
          'knowledge_note_edit':SCOPE|{'entryId','text'}, 'knowledge_note_keep':SCOPE|{'entryId'},
          'knowledge_notes_seen':SCOPE, 'knowledge_note_pair_keep':SCOPE|{'entryIds'},
          # beta.17 (dd #22, C1): Pin keeps a note from being retired or expiring and sends it first. It grants nothing.
          'knowledge_note_pin':SCOPE|{'entryId','pinned'},
          # beta.17.1 (#41, step 8): rules an agent suggested or Home caught move to notes, or are dropped, in one batch
          # with one Undo (undoBatch). Your rules then hold what you write. It grants nothing.
          'knowledge_rule_to_note':SCOPE|{'moves','undoBatch'}}
# Arguments an operation may leave out. beta.17.1 (#41, A, step 7): a reply's Remembered line asks knowledge_notes_list
# for ``brief`` (ids, words and marks only), and Knowledge's page asks for the whole view without it. An argument, not a
# new operation, so the bridge's list of operations doesn't change.
OPTIONAL = {'knowledge_notes_list': {'brief'}}
NOTE_OPS = ('knowledge_notes_list','knowledge_notes_set','knowledge_notes_reflect_set','knowledge_note_remove',
            'knowledge_note_edit','knowledge_note_keep','knowledge_notes_seen','knowledge_note_pair_keep','knowledge_note_pin')
# Why an approved rule wasn't sent with a run, or why it still was (beta.11, fix-memory F2 and F3). Each code has one
# sentence on the Knowledge page and in "What it was told"; the record never carries prose.
NOT_SENT_REASONS = ('work_account', 'source_private', 'source_changed', 'source_unavailable', 'limit')
SOURCE_DELETED = 'source_deleted'  # the chat it came from is gone; the rule still goes out and Knowledge says so
PROCEDURES = [
    dict(id='inspect-scoped-code',title='Inspect scoped code',text='Read the approved file list first. Inspect the smallest relevant function and its callers within that scope. Name any required file outside the scope and ask before extending access.'),
    dict(id='test-and-debug',title='Test and debug',text='Reproduce the reported failure with a focused check. Identify the failing condition before editing. Run the relevant approved checks after the change and record their actual results.'),
    dict(id='review-results',title='Review results',text='Compare the changed behavior with the request. Inspect the diff for unrelated changes. Report checks that passed, checks that failed and behavior that was not checked.')]
for procedure in PROCEDURES:
    procedure.update(author='Dervo', provenance='Original procedure written for Dervo project knowledge, 2026-09-13',
                     license='Original Dervo material. No upstream content used. No separate license grant recorded.')

# Rules that came from an agent's lesson or from Home, not from what the person wrote (beta.17.1, #41 step 8). Up to
# beta.16 agents' notes and lessons became rules once approved, and on 5 Oct this project sent 33 of them, several out
# of date, ahead of true notes. Knowledge offers to move them to notes, preset by MOVE_PRESET.
AGENT_MADE=('Suggested by ','You said this on Home')
def agent_made(provenance): return isinstance(provenance,str) and provenance.startswith(AGENT_MADE)
# A rule that limits what goes out (push, deploy, publish) stays a rule by default: a note is a background fact a run may
# set aside, a rule is what it follows (the Windows push rules, 337c6a92, decided 5 Oct). One that says where something
# is right now, or a question Home caught as a rule ("sure. so dont dm them? ..."), is dropped by default. Every other
# moves. Only a preset: the person picks each row.
# A limit needs the outward word as a word, not inside a name ("deploy-allowlist.json", "r2-publish-<version>"), and a
# word that limits it. A rule that only mentions one ("rebuilt and published before the invitations") isn't a guardrail:
# rendering the project's 12 agent-made rules on 5 Oct preset three of them to Keep for that alone.
OUTWARD_RULE=re.compile(r'(?<![\w./-])(?:push(?:es|ed|ing)?|force[- ]push(?:es|ed|ing)?|deploy(?:s|ed|ing)?|publish(?:es|ed|ing)?)(?![\w/-]|\.\w)',re.I)
LIMIT_WORDS=re.compile(r"\b(?:never|only|don[’']?t|do not|must not|mustn[’']?t|without asking|ask first)\b",re.I)
def move_preset(text,provenance=''):
    import project_memory
    if OUTWARD_RULE.search(text) and LIMIT_WORDS.search(text): return 'keep'
    if project_memory.snapshot(text): return 'drop'
    if str(provenance).startswith(AGENT_MADE[1]) and ('?' in text or '？' in text): return 'drop'
    return 'move'

def stamp(): return datetime.now(timezone.utc).isoformat()
def uid(): return str(uuid.uuid4())

_COMMON_TERMS = frozenset('a an and are as at be before by can do for from how i in is it its keep make of on or our please should so that the their then this to use we when which with you your'.split())

def request_terms(text):
    """Local word matching, including code identifiers. No provider or extra source reads."""
    split = re.sub(r'([a-z0-9])([A-Z])', r'\1 \2', text[:32768])
    return {word for word in re.findall(r'[^\W_]+', split.casefold())
            if len(word) > 1 and not word.isdigit() and word not in _COMMON_TERMS}

def order_for_request(items, query):
    import words as word_matching
    # Word forms count as the same word (beta.11, memory-t1): "cutting releases" ranks the rule about cutting a release.
    terms = word_matching.terms(query)
    vocabulary = [word_matching.terms(item['text']) for item in items]
    # Repeated words don't add weight. Rare terms help specific lessons rank above generic rules.
    scored = [(score, index, item) for index, (item, score)
              in enumerate(zip(items, word_matching.weighted_overlap(terms, vocabulary)))]
    scored.sort(key=lambda row: (-row[0], row[1]))
    return [row[2] for row in scored]

class ProjectKnowledge:
    def __init__(self, store):
        self.store=store
        store.db.execute('CREATE TABLE IF NOT EXISTS project_knowledge (id TEXT PRIMARY KEY, project_id TEXT NOT NULL, profile_id TEXT NOT NULL, revision INTEGER NOT NULL, document TEXT NOT NULL)')
        from project_lessons import schema, ProjectLessons
        schema(store.db)
        store.db.commit()
        self.lessons=ProjectLessons(self)
    def binding(self,args,actor):
        if args.get('channelId') == '':
            from project_record_scope import project_record_scope
            return project_record_scope(self.store, args['projectId'], args['profileId'])
        b=self.store.binding(args['channelId'],args['projectId'],actor=actor)
        if b['profile_id']!=args['profileId'] or not same_side(b['category'], b.get('context', b['category'])):
            raise Rejected('Project knowledge is limited to this conversation\'s own account.')
        return b
    def notes_scope(self,args,actor):
        """Agents' notes belong to the project and its account's category, personal or work, so Knowledge reads and
        sets them with no conversation (channelId ''), which is how the page mounts (beta.11, F1)."""
        if args.get('channelId')!='':
            b=self.store.binding(args['channelId'],args['projectId'],actor=actor)
            if b['profile_id']!=args['profileId']: raise Rejected('This conversation uses another account.')
            return b
        if any(type(args.get(k)) is not str or not args.get(k) or len(args[k])>256 for k in ('projectId','profileId')):
            raise Rejected('Choose a project and its connected account.')
        row=self.store.db.execute('SELECT projects.id AS project_id, projects.cwd, projects.profile_id, profiles.category FROM projects '
                                  'JOIN profiles ON profiles.id=projects.profile_id WHERE projects.id=?',(args['projectId'],)).fetchone()
        if not row or row['profile_id']!=args['profileId']: raise Rejected('The project account changed or is unavailable.')
        if self.store.db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='project_archives'").fetchone():
            if self.store.db.execute('SELECT 1 FROM project_archives WHERE project_id=?',(args['projectId'],)).fetchone():
                raise Rejected('Restore this project before opening its saved work.')
        return dict(row)
    def rules_scope(self,b):
        """The scope rules live in: the project and the project's own account. A conversation moved to another personal
        account (Move to Codex, Auto, the account picker) still gets the project's rules and lessons (beta.11, F2).
        ``b`` may be a thread binding or a Knowledge scope; only ``project_id`` is read from it."""
        row=self.store.db.execute('SELECT projects.profile_id, profiles.category FROM projects JOIN profiles ON profiles.id=projects.profile_id '
                                  'WHERE projects.id=?',(b['project_id'],)).fetchone()
        if not row: raise Rejected('The project account changed or is unavailable.')
        return dict(b,profile_id=row['profile_id'],category=row['category'])
    def source_check(self,source,b,actor):
        """Why a source doesn't check out, as a code, or None when it does. ``SOURCE_DELETED`` is the one code that
        doesn't stop a rule: the chat is gone, so there's nothing left to reveal, and the rule the person approved still
        goes out (beta.11, F3). Everything else is what ``source`` refuses with a sentence."""
        if source is None: return None
        if not isinstance(source,dict) or set(source) not in ({'channelId','messageId'}, {'channelId','taskId','runId'}) or not all(isinstance(v,str) and v and len(v)<=256 for v in source.values()):
            return 'malformed'
        thread=self.store.db.execute('SELECT project_id FROM threads WHERE id=?',(source['channelId'],)).fetchone()
        if not thread: return SOURCE_DELETED
        if thread[0]!=b['project_id']: return 'source_unavailable'
        try: s=self.store.binding(source['channelId'],b['project_id'],actor=actor)
        except Rejected: return 'source_private'
        if s['visibility']!='shared': return 'source_private'
        # Any of this Mac's own accounts on the same side will do: the same chat may have moved between Claude Code and
        # Codex since. A work account's chat never sources a personal project's rule, nor the other way round.
        if not same_side(s['category'], b['category']): return 'other_category'
        if 'messageId' in source:
            if not self.store.db.execute('SELECT 1 FROM messages WHERE id=? AND thread_id=?',(source['messageId'],source['channelId'])).fetchone():
                return 'source_unavailable'
            return None
        if not self.store.db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='coding_tasks'").fetchone():
            return 'source_unavailable'
        row=self.store.db.execute('SELECT document FROM coding_tasks WHERE id=?',(source['taskId'],)).fetchone()
        task=json.loads(row[0]) if row else {}
        if task.get('id')!=source['taskId'] or task.get('context',{})!={'project_id':b['project_id'],'thread_id':source['channelId'],'actor':actor}:
            return 'source_unavailable'
        # The run must have happened under the account, model, folder and visibility the chat has now (security review:
        # a forged or moved source is refused). Since the chat may have moved to another account since (Move to Codex),
        # this is its own reason, so Knowledge can say what to do about it (beta.11, F3).
        saved=task.get('binding',{})
        if any(saved.get(k)!=s[k] for k in ('profile_id','provider','identity','model','cwd','visibility')):
            return 'source_changed'
        run=next((run for run in task.get('runs',[]) if run.get('id')==source['runId']),None)
        if not run or run.get('state') not in ('completed','failed','interrupted','uncertain'):
            return 'run_open'
        return None
    def source(self,source,b,actor):
        reason=self.source_check(source,b,actor)
        if reason is None: return
        raise Rejected({'malformed':'Choose an exact shared message or saved task run. URLs and files are unsupported.',
                        SOURCE_DELETED:'The conversation it came from was deleted.',
                        'source_private':'Private-source promotion is unavailable. Sharing permission is not implemented.',
                        'other_category':'Source belongs to another kind of account.',
                        'source_unavailable':'Shared source record is unavailable.',
                        'source_changed':'Shared task source is unavailable for this project account.',
                        'run_open':'Choose a saved task run that has ended. An unknown outcome is still unknown.'}[reason])
    def rows(self,b):
        return [json.loads(r[0]) for r in self.store.db.execute('SELECT document FROM project_knowledge WHERE project_id=? AND profile_id=? ORDER BY rowid',(b['project_id'],b['profile_id']))]
    def delivery(self,r,b,actor):
        """What a listed rule's next run does with it: ``{sent, reason}``. ``None`` for a rule not in use."""
        v=next((v for v in r['versions'] if v['id']==r['active_version_id']),None)
        if not v: return None
        reason=self.source_check(v['source'],b,actor)
        if reason is None: return dict(sent=True,reason=None)
        if reason==SOURCE_DELETED: return dict(sent=True,reason=SOURCE_DELETED)
        return dict(sent=False,reason=reason if reason in NOT_SENT_REASONS else 'source_unavailable')
    def dispatch(self,op,args,actor):
        if not isinstance(args,dict) or set(args)-OPTIONAL.get(op,set())!=FIELDS[op]: raise Rejected('Unsupported knowledge fields')
        if 'brief' in args and args['brief'] is not True: raise Rejected('brief is exactly true, or left out')
        import repo_guidance
        if op in ('knowledge_repo_list','knowledge_repo_send','knowledge_repo_open'):
            # The repository's files belong to the project folder, not to an account: every task here gets them, on a
            # personal or a work account alike, so they load even when the rules can't.
            p=self.repo_scope(args)
            if op=='knowledge_repo_list': return repo_guidance.listing(self.store.db,p)
            if op=='knowledge_repo_send': return repo_guidance.set_sending(self.store.db,p,args['path'],args['send'])
            return repo_guidance.reveal(p,args['path'],args['how'])
        if op in NOTE_OPS:
            import project_memory
            n=self.notes_scope(args,actor)
            if op=='knowledge_notes_set': project_memory.set_enabled(self.store.db,n['project_id'],args['enabled'],actor)
            if op=='knowledge_notes_reflect_set': project_memory.set_reflect(self.store.db,n['project_id'],args['enabled'],actor)
            if op=='knowledge_note_remove': return project_memory.remove(self.store.db,n,args['entryId'],actor)
            if op=='knowledge_note_edit': return project_memory.edit(self.store.db,n,args['entryId'],args['text'],actor)
            if op=='knowledge_note_keep': return project_memory.keep(self.store.db,n,args['entryId'],actor)
            if op=='knowledge_note_pin': return project_memory.pin(self.store.db,n,args['entryId'],args['pinned'],actor)
            if op=='knowledge_note_pair_keep': return project_memory.keep_pair(self.store.db,n,args['entryIds'],actor)
            if op=='knowledge_notes_seen': project_memory.mark_seen(self.store.db,n['project_id'],actor)
            if op=='knowledge_notes_list' and args.get('brief'): return project_memory.brief(self.store.db,n)
            return project_memory.view(self.store.db,n)
        b=self.binding(args,actor)
        if op=='knowledge_list': return dict(rules=self.rows(b),procedures=PROCEDURES,
            # Per rule in use, the clause Home holds jobs on in code (20 Sep, home-rules): {rule_id: clause}. A rule not
            # here still reaches Home's model, and only the model; the page can say which of the two a rule gets.
            home_holds=self.home_holds(b),
            # Per rule in use, what its next run does with it (beta.11, F3): {rule_id: {sent, reason}}. Rows stay as saved.
            deliveries={r['id']:d for r in self.rows(b) if (d:=self.delivery(r,b,actor)) is not None},
            lesson_suggestions_enabled=self.lessons.enabled(b),suggestions=self.lessons.pending(b,actor),
            repository=repo_guidance.listing(self.store.db,b),
            # Per agent-made rule in use, what Move agent-made rules to notes starts it on (beta.17.1, #41 step 8).
            move_presets=self.move_presets(b),
            helper_retrieval="Linked helpers receive their recorded collaboration packet. They don't retrieve project guidance separately.")
        if op=='knowledge_rule_to_note': return self.rule_to_note(b,actor,args['moves'],args['undoBatch'])
        if op=='knowledge_note_restore':
            import project_memory
            return project_memory.restore(self.store.db,b,args['entryId'],actor)
        if op=='knowledge_lesson_reopen': return self.reopen_lesson(b,actor,args['lessonId'])
        if op=='knowledge_lessons_set': return self.lessons.set_enabled(b,args['enabled'])
        if op=='knowledge_lesson_list': return self.lessons.pending(b,actor,args['before'])
        if op=='knowledge_lesson_dismiss': return self.lessons.dismiss(b,actor,args['lessonId'])
        if op=='knowledge_adopt':
            p=next((p for p in PROCEDURES if p['id']==args['procedureId']),None)
            if not p: raise Rejected('Unknown curated procedure')
            # Adopted starter procedures are recorded as such, so Knowledge can show "Saved as guidance" on the procedure
            # and the rule can say where it came from. Saving it twice makes no second rule.
            existing=next((r for r in self.rows(b) if r.get('procedure_id')==p['id']),None)
            if existing: return existing
            saved=self.dispatch('knowledge_save',{**{k:args[k] for k in SCOPE},'ruleId':None,'expectedRevision':0,'text':p['text'],'provenance':p['provenance']+'; Author: '+p['author']+'; License: '+p['license'],'source':None},actor)
            saved['procedure_id']=p['id']
            with self.store.db:
                self.store.db.execute('UPDATE project_knowledge SET document=? WHERE id=?',(json.dumps(saved),saved['id']))
            return saved
        lesson_id=args['lessonId'] if op=='knowledge_lesson_keep' else None
        revision=0 if op=='knowledge_lesson_keep' else args['expectedRevision']
        if type(revision)!=int or revision<0: raise Rejected('Expected revision must be a nonnegative integer')
        with self.store.db:
            # Acquire the write lock before reading the expected revision, before any revision-checked mutation.
            self.store.db.execute('BEGIN IMMEDIATE')
            if op=='knowledge_lesson_keep':
                status, suggestion=self.lessons.get(b,actor,lesson_id)
                if status=='saved':
                    return next(r for r in self.rows(b) if r['id']==suggestion['rule_id'])
                if status!='pending': raise Rejected('This lesson was dismissed. It was not saved as guidance.')
                args=dict(ruleId=None,text=suggestion['text'],source=suggestion['source'],
                          provenance='Suggested by '+suggestion['provider']+' during this task. Evidence: '+suggestion['evidence'])
                op='knowledge_save'
            r=next((r for r in self.rows(b) if r['id']==args['ruleId']),None)
            if args['ruleId'] is not None and r is None: raise Rejected('Rule is unavailable in this project account')
            if (r['revision'] if r else 0)!=revision: raise Rejected('Knowledge changed. Reload and choose a saved version again before saving or approving.')
            if op!='knowledge_save' and r is None: raise Rejected('Choose a saved rule')
            if op=='knowledge_save':
                for key,limit in (('text',4096),('provenance',1024)):
                    if not isinstance(args[key],str) or not args[key].strip() or len(args[key].encode())>limit:
                        raise Rejected(f'{key} needs nonempty text up to {limit} bytes')
                self.source(args['source'],b,actor)
                if r is None: r=dict(id=uid(),revision=0,active_version_id=None,versions=[],events=[])
                r['versions'].append(dict(id=uid(),created_at=stamp(),text=args['text'].strip(),provenance=args['provenance'].strip(),source=args['source'],author=actor))
                event='proposed'
            elif op=='knowledge_approve':
                v=next((v for v in r['versions'] if v['id']==args['versionId']),None)
                if not v: raise Rejected('Choose an existing version to approve or roll back to')
                self.source(v['source'],b,actor)
                r['active_version_id']=v['id']; event='approved'
            elif op=='knowledge_version_dismiss':
                # A saved version waiting in Knowledge is set aside without changing what's in use, or brought back (Undo).
                v=next((v for v in r['versions'] if v['id']==args['versionId']),None)
                if not v: raise Rejected('Choose an existing version')
                if type(args['dismissed']) is not bool: raise Rejected('A version is dismissed (true) or waiting again (false)')
                if v['id']==r['active_version_id']: raise Rejected('This version is in use. Archive the rule instead.')
                event='dismissed' if args['dismissed'] else 'reopened'
            else: archived=r['active_version_id']; r['active_version_id']=None; event='archived'
            r['revision']+=1
            r['events'].append(dict(id=uid(),at=stamp(),actor=actor,kind=event,revision=r['revision'],
                                    version_id=r['versions'][-1]['id'] if event=='proposed' else args['versionId'] if event in ('dismissed','reopened') else r['active_version_id'],
                                    # The version that was in use, so Restore (and Undo) can bring back exactly that one.
                                    **({'archived_version_id':archived} if event=='archived' and archived else {})))
            self.store.db.execute('INSERT INTO project_knowledge VALUES (?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET revision=excluded.revision,document=excluded.document',(r['id'],b['project_id'],b['profile_id'],r['revision'],json.dumps(r)))
            if lesson_id:
                suggestion['rule_id']=r['id']
                self.store.db.execute("UPDATE project_lessons SET status='saved',document=? WHERE id=?",(json.dumps(suggestion),lesson_id))
            return r
    def move_presets(self,b):
        found={}
        for r in self.rows(b):
            v=next((v for v in r['versions'] if v['id']==r['active_version_id']),None)
            if v and agent_made(v.get('provenance')): found[r['id']]=move_preset(v['text'],v['provenance'])
        return found
    def rule_to_note(self,b,actor,moves,undo_batch):
        """Move agent-made rules to notes, or drop them, in one write (beta.17.1, #41 step 8). Move archives the rule,
        every version kept as Archive keeps them, and saves its words (or the person's shorter ones) as a note of the
        person's own: never retired and never held for Keep. Drop only archives. Only a rule an agent suggested or
        Home caught moves: what the person wrote stays theirs to archive. ``undoBatch`` puts a whole batch back: each
        rule in use again at the version it had, and each note it made out of memory. A rule changed since is left as
        it is and named in ``changed``."""
        import project_memory
        scope=self.rules_scope(b)
        memory=dict(project_id=scope['project_id'],category=scope['category'])
        if (moves is None)==(undo_batch is None): raise Rejected('Choose the rules to move, or the move to undo.')
        db=self.store.db
        save=lambda r: db.execute('UPDATE project_knowledge SET revision=?,document=? WHERE id=?',(r['revision'],json.dumps(r),r['id']))
        with db:
            db.execute('BEGIN IMMEDIATE')
            rows={r['id']:r for r in self.rows(scope)}
            if undo_batch is not None:
                if type(undo_batch) is not str or not undo_batch or len(undo_batch)>64: raise Rejected('Choose the move to undo.')
                restored,changed=[],[]
                for r in rows.values():
                    event=next((e for e in reversed(r['events']) if e.get('batch')==undo_batch and e.get('kind')=='archived'),None)
                    if not event or any(e.get('undid_batch')==undo_batch for e in r['events']): continue
                    if r['revision']!=event['revision'] or r['active_version_id']: changed.append(r['id']); continue
                    r['active_version_id']=event['archived_version_id']; r['revision']+=1
                    r['events'].append(dict(id=uid(),at=stamp(),actor=actor,kind='approved',revision=r['revision'],version_id=event['archived_version_id'],undid_batch=undo_batch))
                    save(r); restored.append(r['id'])
                removed=project_memory.undo_rule_notes(db,memory,undo_batch,actor)
                if not restored and not removed and not changed: raise Rejected('That move was already undone.')
                return dict(undone=undo_batch,restored=restored,notes_removed=removed,changed=changed)
            if type(moves) is not list or not moves or len(moves)>100: raise Rejected('Choose between 1 and 100 rules to move.')
            batch=uid(); moved,dropped,done=[],[],set()
            for m in moves:
                if not isinstance(m,dict) or set(m)!={'ruleId','expectedRevision','do','text'}: raise Rejected('Unsupported move fields')
                r=rows.get(m['ruleId'])
                if r is None or m['ruleId'] in done: raise Rejected('Rule is unavailable in this project account')
                done.add(r['id'])
                if type(m['expectedRevision']) is not int or r['revision']!=m['expectedRevision']:
                    raise Rejected('Knowledge changed. Reload and choose again before moving rules.')
                v=next((v for v in r['versions'] if v['id']==r['active_version_id']),None)
                if v is None: raise Rejected('Only a rule in use can move to notes.')
                if not agent_made(v.get('provenance')): raise Rejected('Only rules an agent suggested or Home caught move to notes. Archive one of yours instead.')
                if m['do'] not in ('move','drop'): raise Rejected('A rule moves to notes or is dropped.')
                if m['text'] is not None and type(m['text']) is not str: raise Rejected('Write the note as one short sentence.')
                note=project_memory.note_from_rule(db,memory,v['text'] if m['text'] is None else m['text'],
                                                   dict(rule_id=r['id'],version_id=v['id']),actor,batch) if m['do']=='move' else None
                archived=r['active_version_id']; r['active_version_id']=None; r['revision']+=1
                r['events'].append(dict(id=uid(),at=stamp(),actor=actor,kind='archived',revision=r['revision'],version_id=None,
                                        archived_version_id=archived,batch=batch,**({'moved_to_note':note['id']} if note else {})))
                save(r)
                (moved.append(dict(rule_id=r['id'],note_id=note['id'])) if note else dropped.append(r['id']))
        return dict(batch=batch,moved=moved,dropped=dropped)
    def home_holds(self,b):
        from project_orchestrator import asks_first
        found={}
        for r in self.rows(b):
            v=next((v for v in r['versions'] if v['id']==r['active_version_id']),None)
            clause=asks_first(v['text']) if v and isinstance(v.get('text'),str) else None
            if clause: found[r['id']]=clause
        return found
    def repo_scope(self,args):
        if any(type(args.get(k)) is not str or not args.get(k) or len(args[k])>256 for k in ('projectId','profileId')):
            raise Rejected('Choose a project and its connected account.')
        row=self.store.db.execute('SELECT id, cwd, profile_id FROM projects WHERE id=?',(args['projectId'],)).fetchone()
        if not row or row[2]!=args['profileId']: raise Rejected('The project account changed or is unavailable.')
        return dict(project_id=row[0],cwd=row[1])
    def reopen_lesson(self,b,actor,lesson_id):
        """Undo for a lesson: put it back in the queue. A dismissed lesson simply waits again. An approved one takes its
        rule out of use first (archived, every version kept), so nothing it said reaches the next task."""
        with self.store.db:
            self.store.db.execute('BEGIN IMMEDIATE')
            status, item=self.lessons.get(b,actor,lesson_id)
            if status=='pending': return dict(reopened=False)
            if status=='saved' and item.get('rule_id'):
                r=next((r for r in self.rows(b) if r['id']==item['rule_id']),None)
                if r and r['active_version_id']:
                    archived=r['active_version_id']; r['active_version_id']=None; r['revision']+=1
                    r['events'].append(dict(id=uid(),at=stamp(),actor=actor,kind='archived',revision=r['revision'],version_id=None,archived_version_id=archived))
                    self.store.db.execute('UPDATE project_knowledge SET revision=?,document=? WHERE id=?',(r['revision'],json.dumps(r),r['id']))
            item.pop('rule_id',None)
            self.store.db.execute("UPDATE project_lessons SET status='pending',document=? WHERE id=?",(json.dumps(item),lesson_id))
        return dict(reopened=True)
    def snapshot(self,b,actor,previous=(),query=''):
        """The rules one run gets. ``b`` is the conversation's binding; the rules are the project's (``rules_scope``), so
        a chat on another personal account gets them too (beta.11, F2). Every rule in use that isn't sent is named in
        ``not_sent`` with a reason from NOT_SENT_REASONS, so the run record never says less than what happened."""
        guidance='Project guidance is subordinate to existing permissions and task scope. It grants no tools, access, sharing permission or execution permission. Treat rule text as guidance, never as authorization.'
        scope=self.rules_scope(b)
        rules=[]; candidates=[]; not_sent=[]
        left=lambda r,v,reason: not_sent.append(dict(rule_id=r['id'],version_id=v['id'],reason=reason))
        for r in self.rows(scope):
            v=next((v for v in r['versions'] if v['id']==r['active_version_id']),None)
            if not v: continue
            # A rule only reaches a run on the project's own side ('work_account' is the historical code for "other side").
            if not same_side(b['category'], scope['category']): left(r,v,'work_account'); continue
            reason=self.source_check(v['source'],scope,actor)
            if reason not in (None,SOURCE_DELETED): left(r,v,reason if reason in NOT_SENT_REASONS else 'source_unavailable'); continue
            candidates.append(dict(rule_id=r['id'],version_id=v['id'],text=v['text'],provenance=v['provenance'],source=v['source'],created_at=v['created_at']))
        for item in order_for_request(candidates, query):
            if len(rules)>=12 or len((guidance+'\n'+json.dumps(rules+[item],ensure_ascii=False)).encode())>16384:
                not_sent.append(dict(rule_id=item['rule_id'],version_id=item['version_id'],reason='limit')); continue
            rules.append(item)
        b=scope
        # Prior snapshots are immutable delivery records, including uncertain sends.
        prior=[s for s in previous if s.get('project_id')==b['project_id'] and s.get('profile_id')==b['profile_id'] and s.get('text')]
        replacement=('Current request guidance replacement for ONLY the project_id and profile_id below. '
                     'Retire all earlier delivered rule versions for this project/account except the exact current rules listed below. '
                     'An empty rules array means no project rules are supplied for this request. Rules omitted by the delivery limit may remain approved in Dervo. '
                     'Retired versions are no longer guidance for this request, including after rollback. '
                     'This does not delete provider history or guarantee model compliance.')
        while True:
            active={(r['rule_id'],r['version_id']) for r in rules}
            # The latest replacement already retired older versions. Name newly retired versions exactly.
            retired=[{k:item.get(k) for k in ('rule_id','version_id','source')}
                     for item in (prior[-1]['rules'] if prior else [])
                     if (item['rule_id'],item['version_id']) not in active]
            payload=dict(semantics='replace_project_account_rules',project_id=b['project_id'],profile_id=b['profile_id'],rules=rules,retired_versions=retired)
            text=guidance+'\n'+replacement+'\n'+json.dumps(payload,ensure_ascii=False) if rules or prior else ''
            if len(text.encode())<=16384: break
            if not rules: raise Rejected('Knowledge retirement exceeds the delivery limit. No context was sent.')
            dropped=rules.pop(); not_sent.append(dict(rule_id=dropped['rule_id'],version_id=dropped['version_id'],reason='limit'))
        return dict(captured_at=stamp(),text=text,omitted=len(not_sent),not_sent=not_sent,
                    selection=dict(method='word_forms_v1',eligible=len(candidates),
                                   detail='Approved rules matching request words come first. Ties and unmatched rules retain saved order. No semantic search or provider call.'),**payload)
