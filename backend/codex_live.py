"""Single-threaded live Codex pump used by the native host.

SQLite stays on its owning thread. A poll never waits for a complete turn.
"""
import tempfile
import platform_os
import time
from adapters import CodexAdapter, LIVE_BLOCKERS
from codex_policy import argv, check_profile, login_method
from dervo_connections import Rejected
from transport import OwnedProcess, clean_environment
from task_error import error_text


class CodexLiveTurn:
    def __init__(self, store, binding, message_id, *, factory=None, recovery=False, on_event=None, access_check=None):
        self.access_check = access_check or (lambda: None)
        self.access_check()
        if binding['provider'] != 'codex':
            raise Rejected(LIVE_BLOCKERS['claude'])
        check_profile(binding['home'])
        self.store,self.binding,self.message_id=store,binding,message_id
        self.recovery=recovery
        self.on_event=on_event or (lambda *_:None)
        self.claimed,self.begun,self.closed=False,False,False
        self.error=None
        self.deadline=time.monotonic()+120
        self.neutral=tempfile.TemporaryDirectory(prefix='dervo-turn-',dir=platform_os.scratch_root())
        self.process=None
        try:
            self.process=(factory or OwnedProcess)(argv(login_method=login_method(binding),cwd=binding['cwd']),cwd=self.neutral.name,
                env=clean_environment('codex',binding['home'],binding['home']))
            self.adapter=CodexAdapter(binding,self.send,self.emit,recovery=recovery)
            self.adapter.connect()
        except Exception:
            try:
                if self.process is not None:self.process.close()
            finally:self.neutral.cleanup()
            raise

    def send(self, message):
        if message.get('method') in ('thread/start', 'thread/resume', 'thread/read', 'turn/start'):
            self.access_check()
        self.process.send(message)

    def emit(self,kind,data):
        # No user text is delivered before the same-process preflight succeeds.
        if self.claimed:
            self.store.record(self.message_id,kind,data)
            self.on_event(kind,data)
        elif kind=='terminal':
            self.error=error_text(data.get('error')) or 'Provider preflight failed'

    def tick(self):
        if self.closed:return
        try:
            self.access_check()
            if time.monotonic()>=self.deadline:
                raise Rejected('Provider deadline exceeded. Delivery needs checking if a turn was sent.')
            try:batch=self.process.read(.001)
            except TimeoutError:return
            if batch is None:raise Rejected('Provider disconnected before terminal confirmation')
            for event in batch:self.adapter.receive(event)
            if self.adapter.state=='history':
                self.store.reconcile_codex(self.message_id,self.binding['project_id'],actor='local-user',
                    verified_binding=self.binding,history=self.adapter.history)
                self.close()
                return
            if self.adapter.state=='ready' and not self.begun:
                self.store.claim(self.message_id)
                self.claimed=True
                self.emit('session',{'session_id':self.adapter.session_id})
                message=self.store.db.execute('SELECT * FROM messages WHERE id=?',(self.message_id,)).fetchone()
                self.begun=True
                self.adapter.begin(message['text'],message['client_id'])
            if self.adapter.state=='blocked':raise Rejected('Account or selected model is unavailable. Check the personal sign-in and catalog.')
            if self.adapter.state in ('completed','interrupted','failed'):self.close()
        except Exception as exc:
            self.error=str(exc)
            if self.claimed and self.store.turn(self.message_id)['state'] in ('sending','running','stopping'):
                self.emit('terminal',{'state':'uncertain' if self.begun else 'failed','error':self.error})
            self.close()

    def stop(self):
        if self.closed:return
        if self.adapter.state=='running':
            self.adapter.stop()
            # A reply that ignores the interrupt ends on the tick after 10 seconds, as a coding run's Stop does, instead
            # of holding a project delete open until its 2-minute start deadline (lifecycle review, 23 Sep).
            self.deadline=min(self.deadline,time.monotonic()+10)
        elif not self.begun:
            self.error='Stopped before sending. The message is still queued.'
            self.close()
        elif self.adapter.state=='sending':
            # Stop before acknowledgement can't report interruption as confirmed.
            self.emit('terminal',{'state':'uncertain','error':'Stopped before turn acknowledgement. Check delivery before continuing.'})
            self.close()

    def close(self):
        if self.closed:return
        self.closed=True
        try:self.process.close()
        finally:self.neutral.cleanup()

    def status(self):
        return {'active':not self.closed,'phase':self.adapter.state,'error':self.error,
                'state':self.store.turn(self.message_id)['state']}
