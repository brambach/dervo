"""Synchronous provider runner. Native Codex uses the same incremental pump."""
from pathlib import Path
import threading
import time
from adapters import ClaudeAdapter, CodexAdapter, launch_live
from dervo_connections import Rejected
from transport import clean_environment


class ProtocolRunner:
    """Synchronous single-turn pump for a trusted host worker.

    transport_factory is an offline-test injection seam, not a renderer argument.
    Codex verifies the owned profile, model and permissions before each turn.
    Claude production launch remains gated.
    """
    def __init__(self, store, *, transport_factory=None):
        self.store, self.factory = store, transport_factory

    def run(self, message_id, thread_id, project_id, *, actor, claude_metadata=None,
            timeout=60, stop=None, on_event=None):
        binding = self.store.binding(thread_id, project_id, actor=actor)
        turn = self.store.turn(message_id)
        if turn['thread_id'] != thread_id:
            raise Rejected('Message belongs to another conversation')
        if not 0 < timeout <= 120:
            raise Rejected('Use a deadline between zero and 120 seconds')
        if binding['provider']=='codex':
            from codex_live import CodexLiveTurn
            from connections_ui import owned_profile
            database=self.store.db.execute('PRAGMA database_list').fetchone()['file']
            owned_profile(self.store,Path(database).parent,binding['profile_id'],context=binding.get('context'))
            if turn['state']!='queued':raise Rejected('Only queued messages can start')
            live=CodexLiveTurn(self.store,binding,message_id,factory=self.factory,on_event=on_event)
            live.deadline=time.monotonic()+timeout
            try:
                while not live.closed:
                    if stop is not None and stop.is_set():live.stop()
                    live.tick()
                    time.sleep(.001)
                if live.error and not live.claimed:raise Rejected(live.error)
                return self.store.turn(message_id)
            finally:live.close()
        if self.factory is None:
            launch_live(binding)
        message = next(m for m in self.store.messages(thread_id, project_id, actor=actor) if m['id'] == message_id)
        stop = stop or threading.Event()
        on_event = on_event or (lambda *_: None)
        def emit(kind, data):
            self.store.record(message_id, kind, data)
            if kind == 'rate_limits':
                import provider_usage
                provider_usage.schema(self.store.db)
                provider_usage.record_rate_limits(self.store.db, binding['profile_id'], data)
            on_event(kind, data)
        env = clean_environment(binding['provider'], binding['home'], binding['home'])
        process = None
        if binding['provider'] == 'claude':
            adapter = ClaudeAdapter(binding, emit)
            # Only test metadata is injected today. Live code must execute the
            # official auth-status command in this same sanitized profile first.
            adapter.authenticate(claude_metadata or {})
            # This branch requires an injected test transport. Live Claude uses
            # its separate verified runtime, never this placeholder executable.
            argv = adapter.command('/dervo-test-tools/claude')
        else:
            from codex_policy import argv as codex_argv, login_method
            argv = codex_argv(login_method=login_method(binding), cwd=binding['cwd'])
            adapter = None
        self.store.claim(message_id)
        deadline = time.monotonic() + timeout
        begun, stopped = False, False
        try:
            with self.factory(argv, cwd=binding['cwd'], env=env) as process:
                if adapter is None:
                    adapter = CodexAdapter(binding, process.send, emit)
                    adapter.connect()
                else:
                    begun = True
                    process.send_text(message['text'])
                    process.process.stdin.close()
                while time.monotonic() < deadline:
                    if binding['provider'] == 'codex' and adapter.state == 'ready' and not begun:
                        begun = True
                        adapter.begin(message['text'], message['client_id'])
                    if stop.is_set() and not stopped and adapter.state == 'running':
                        adapter.stop()
                        if binding['provider'] == 'claude':
                            process.interrupt()
                        stopped = True
                    try:
                        batch = process.read(min(0.1, max(0.001, deadline - time.monotonic())))
                    except TimeoutError:
                        continue
                    if batch is None:
                        if binding['provider'] == 'claude':
                            adapter.exited(process.process.wait(timeout=max(0.001, deadline - time.monotonic())))
                        elif self.store.turn(message_id)['state'] not in ('completed', 'interrupted', 'failed'):
                            emit('terminal', {'state': 'uncertain' if begun else 'failed', 'error': 'Provider disconnected before completion'})
                        break
                    for event in batch:
                        adapter.receive(event)
                    if binding['provider'] == 'codex' and adapter.state in ('completed', 'interrupted', 'failed'):
                        break
                else:
                    emit('terminal', {'state': 'uncertain' if begun else 'failed', 'error': 'Provider deadline exceeded'})
        except Exception as exc:
            if self.store.turn(message_id)['state'] in ('sending', 'running', 'stopping'):
                emit('terminal', {'state': 'uncertain' if begun else 'failed', 'error': str(exc)})
            raise
        return self.store.turn(message_id)
