"""Claude Code started ahead of time, so a follow-up feels like typing into Claude Code itself (8 Oct 2026).

A send used to launch a new Claude Code, load the thread's saved session and connect its MCP servers before the task
could go (about 10 s for seven servers on Bryce's Mac, tools/run_timings.py). Now, when you start typing in a thread,
the host launches the process the next message would launch, with the same command, account check, folder and continued
session, and finishes the handshake and the connector wait. Nothing is sent to it. On the next send, ``take`` hands it
over if everything the launch depends on is still exactly the same (``signature``); otherwise it's closed and the send
launches as before. So a warm start never changes what a run is allowed to do, only when its process started.

Bounded: at most MAX_WARM processes (one: the thread you're typing in), each closed after WARM_SECONDS unused, and closed when the host closes. A held
process has nothing to say: anything it writes that would start a turn ends it (ClaudeCodingTurn's own guard), and if
the host dies its stdin closes and Claude Code exits."""
import json
import time

# Changes after which a held Claude Code is closed at once rather than when it expires: its thread, project, account or
# access went away or changed (code review 8 Oct, 6). A send would refuse it anyway (signature); this frees it sooner.
ENDS_HELD = frozenset({'conversation_delete', 'conversation_archive', 'conversations_archive', 'conversations_delete_archived',
                       'project_archive', 'project_delete', 'coding_full_access_set', 'coding_computer_access_set',
                       'coding_connectors_set', 'coding_permission_revoke', 'conversation_profile_set', 'conversation_model_set',
                       'account_remove', 'dervo_account_sign_out'})
SETTLE_SECONDS = 30  # a held process's handshake and connector wait are ticked at full speed this long, then idly
FRESH_SECONDS = 15   # connector states older than this aren't replayed into the task that takes it: they'd be stale
MAX_WARM = 1        # an idle held Claude Code measured 214 MB before its MCP servers (code review, 8 Oct)
WARM_SECONDS = 150


def signature(binding, *, tools, full_access, computer_access, connectors_allowed, commands, connectors_only, limits):
    """Everything a Claude Code launch depends on, as one comparable string. Two launches with the same signature run
    the same command in the same folder for the same account, continuing the same session."""
    return json.dumps({'binding': dict(binding), 'tools': list(tools), 'full_access': full_access,
                       'computer_access': computer_access,
                       'connectors_allowed': None if connectors_allowed is None else list(connectors_allowed),
                       'commands': commands, 'connectors_only': connectors_only, 'limits': dict(limits or {})},
                      sort_keys=True, default=str)


class Held:
    """One process started ahead of time: its turn, what it was started for, and the events it raised before any task
    (the plan, a continuity fallback, its session and its connectors), replayed into the task that takes it."""

    def __init__(self, signature, born=None):
        self.signature, self.turn, self.events = signature, None, []
        self.born = time.monotonic() if born is None else born

    def emit(self, kind, data):
        if kind != 'terminal':
            self.events.append((kind, dict(data), time.monotonic()))

    def replay(self, now=None):
        """(kind, data) to replay into the task taking it. A connector state it saw long ago isn't said as current: the
        record then has none, which reads as unknown, never as a stale "ready" (code review 8 Oct, 4)."""
        now = time.monotonic() if now is None else now
        return [(kind, data) for kind, data, at in self.events if kind != 'connectors' or now - at <= FRESH_SECONDS]


class WarmPool:
    def __init__(self):
        self.held = {}  # thread id -> Held

    def warming(self, now=None):
        now = time.monotonic() if now is None else now
        return any(now - held.born < SETTLE_SECONDS and not held.turn.closed for held in self.held.values())

    def has(self, thread_id, signature):
        held = self.held.get(thread_id)
        return held is not None and held.signature == signature and not held.turn.closed

    def offer(self, thread_id, held):
        self.discard(thread_id)
        self.held[thread_id] = held
        while len(self.held) > MAX_WARM:
            self.discard(min(self.held, key=lambda key: self.held[key].born))

    def take(self, thread_id, signature):
        """The held process for this thread if it was started for exactly this launch, else None (and it's closed)."""
        held = self.held.pop(thread_id, None)
        if held is None:
            return None
        if held.signature != signature or held.turn.closed or held.turn.begun:
            held.turn.close()
            return None
        return held

    def discard(self, thread_id):
        held = self.held.pop(thread_id, None)
        if held is not None:
            held.turn.close()

    def tick(self, now=None):
        now = time.monotonic() if now is None else now
        for thread_id, held in list(self.held.items()):
            if now - held.born > WARM_SECONDS:
                self.discard(thread_id)
                continue
            held.turn.tick()  # the handshake and the connector wait go on; the task never goes while it's held
            if held.turn.closed:
                self.held.pop(thread_id, None)

    def close(self):
        for thread_id in list(self.held):
            self.discard(thread_id)
