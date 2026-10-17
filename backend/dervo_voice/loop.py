"""The voice loop: microphone frames and commands in, events out.

One finished utterance becomes one Home message. Between hearing it and sending it:

1. Cancel phrases ("never mind", "scratch that") take back the last utterance still in its undo window.
2. An outward request (push, merge, deploy, send outside Dervo, delete) is read back and waits for a spoken yes.
3. Anything else is shown with a short undo window, then released to the app as ``send``. The app passes it to Home's
   own send path with the utterance id as clientId and answers ``delivered``.

A short spoken ack ("Got it.") starts as soon as the utterance is understood, before routing. The app later asks for the
routed receipt to be spoken ("Started Export fix."). Talking over Dervo stops its voice at once (barge-in).

Time is audio time: each 512-sample frame moves the clock 32 ms. That keeps the loop deterministic under recorded clips
in tests and equal to wall time on a live microphone. Stdlib only; models come in through ``engines`` (see engines.py)
and the speaker through ``speaker`` (see speaker.py), so tests run with fakes and no numpy.

States: ``off`` (muted), ``idle`` (waiting for the hotkey or the wake word), ``listening`` (armed, waiting for speech),
``hearing`` (speech in progress). ``speaking`` and ``awaiting`` (an outward request waiting for yes) are flags on top.
"""
import re
import time
import uuid
from array import array
from collections import deque

from . import intents

RATE = 16000
FRAME = 512
FRAME_SECONDS = FRAME / RATE

# Fixed lines, synthesized once at start (speaker.py) so the first response doesn't wait on the voice model.
ACKS = {'heard': 'Got it.', 'cancelled': 'Okay, dropped that.', 'declined': "Okay, I won't.",
        'timeout': "I didn't hear a yes, so I didn't send it.", 'going': 'Okay, going ahead.',
        'unclear': 'Say yes to go ahead, or no.'}
MODES = ('push_to_talk', 'wake', 'conversation')
# Anything a previous run left unsent is offered again only if it's this recent. Older words are dropped, not replayed.
REPLAY_SECONDS = 600


# What the window may change at run time, and the least each can be. The undo window can't be talked down to nothing.
ADJUSTABLE = {'undo_seconds': 1.0, 'follow_up_seconds': 0.0, 'listen_seconds': 1.0, 'confirm_seconds': 3.0,
              'barge_in_seconds': 0.1, 'speech_threshold': 0.1, 'max_pause': 0.5}


class Settings:
    def __init__(self, **overrides):
        # 'push_to_talk': the hotkey only. 'wake': "Hey Dervo" arms it, and a follow-up needs no wake word.
        # 'conversation': open mic, every utterance counts; an explicit choice with a visible indicator.
        self.mode = 'push_to_talk'
        self.undo_seconds = 3.0         # how long a heard request waits on screen before it's sent
        self.follow_up_seconds = 6.0    # after Dervo answers, keep listening this long without the wake word
        self.listen_seconds = 5.0       # after the wake word, how long to wait for speech to start
        self.min_pause = 0.25           # silence before the turn model is asked whether you've finished
        self.max_pause = 2.0            # silence that ends the utterance whatever the turn model says
        self.max_utterance = 30.0
        self.confirm_seconds = 8.0      # how long an outward request waits for a yes
        self.speech_threshold = 0.5
        self.barge_in_seconds = 0.2     # speech this long while Dervo talks stops its voice
        self.preroll_seconds = 0.3      # audio kept from just before speech was detected
        self.wake_preroll_seconds = 2.0 # audio kept from before the wake word fired (it holds the wake phrase)
        self.short_seconds = 1.5        # utterances this short are checked for yes/no/cancel when the turn model hesitates
        self.min_speech_seconds = 0.15  # shorter than this is a click or a cough, not an utterance
        for key, value in overrides.items():
            if not hasattr(self, key):
                raise ValueError(f'unknown voice setting {key}')
            setattr(self, key, value)


_WAKE = re.compile(r"^\W*(?:(?:hey|hi|ok|okay|a|eh|hay)\W+)?d[eu]rv[oa]h?\b[\s,.!?]*", re.I)


def strip_wake(text):
    """The transcript without the wake phrase it started with, or a cut-off tail of it ("Dervo, ...")."""
    stripped = _WAKE.sub('', text, count=1)
    return stripped[:1].upper() + stripped[1:] if stripped != text else text


def _energy_speech(frame):
    """A stand-in when no VAD model is loaded: loud frames count as speech."""
    if not frame:
        return 0.0
    return min(1.0, (sum(sample * sample for sample in frame) / len(frame)) ** 0.5 / 1500.0)


class VoiceLoop:
    def __init__(self, engines, emit, journal, speaker, settings=None, ids=None):
        self.engines = engines
        self.emit_event = emit
        self.journal = journal
        self.speaker = speaker
        self.settings = settings or Settings()
        self.ids = ids or (lambda: str(uuid.uuid4()))
        self.now = 0.0
        self.capture = lambda on: None  # the microphone itself; __main__ wires it to the audio helper
        self.mic_open = None
        self.scope = None  # the project and profile the window is showing; each utterance is tagged with it
        self.state = 'idle'
        self.ptt = False
        self.buffer = array('h')
        self.preroll = deque(maxlen=max(1, int(max(self.settings.preroll_seconds, self.settings.wake_preroll_seconds)
                                                    / FRAME_SECONDS)))
        self.after_wake = False
        self.early_text = None
        self.deciding = None
        self.asr_ms = 0.0
        self.turn_ms = None
        self.listen_until = None
        self.silence = 0.0
        self.speech_seconds = 0.0
        self.asked_turn = False
        self.last_speech = 0.0
        self.barge_run = 0.0
        self.pending = {}    # id -> {'text', 'until'}: in the undo window
        self.awaiting = None  # {'id', 'text', 'until', 'what'}: outward, waiting for yes
        self.was_speaking = False

    # ------------------------------------------------------------------ events
    def emit(self, kind, **fields):
        self.emit_event({'type': kind, 'at': round(self.now, 3), **fields})

    def _set_state(self, state):
        if state != self.state:
            self.state = state
            self.emit('state', state=state, speaking=self.speaker.speaking())
        self.mic()

    def mic(self):
        """Open the microphone only when something listens: while the hotkey is held (and the words after it are being
        finished), while Dervo has just asked for a spoken yes, or in "Hey Dervo" and open mic, which you turn on
        explicitly. Muted or off, it's closed."""
        on = self.state != 'off' and (self.settings.mode != 'push_to_talk' or self.ptt or self.state == 'hearing'
                                      or self.awaiting is not None)
        if on != self.mic_open:
            self.mic_open = on
            self.capture(on)
            self.emit('mic', open=on)

    # ------------------------------------------------------------------ start up
    def recover(self):
        """Settle what a previous run left in the journal (journal.py says what each state means)."""
        for entry in sorted(self.journal.unfinished(), key=lambda entry: entry.get('at', 0)):
            utterance_id, text = entry['id'], entry.get('text', '')
            if entry['state'] in ('released', 'pending') and time.time() - entry.get('at', 0) > REPLAY_SECONDS:
                self.journal.write(utterance_id, 'cancelled', reason='stale')
                self.emit('cancelled', id=utterance_id, reason='stale')
            elif entry['state'] == 'released':
                self.emit('send', id=utterance_id, text=text, replay=True, scope=entry.get('scope'))
            elif entry['state'] == 'pending':
                self._hold(utterance_id, text, replay=True)
            else:
                self.journal.write(utterance_id, 'cancelled', reason='restart')
                self.emit('cancelled', id=utterance_id, reason='restart')

    # ------------------------------------------------------------------ commands
    def command(self, message):
        try:
            self._command(message)
        finally:
            self.mic()

    def _command(self, message):
        kind = message.get('type')
        if kind == 'ptt_down':
            if self.state == 'off':
                return
            self._barge('ptt')
            self.ptt = True
            self._start_hearing()
        elif kind == 'ptt_up':
            if self.ptt:
                self.ptt = False
                if self.state == 'hearing':
                    self._finish('ptt')
        elif kind == 'mode':
            if message.get('mode') not in MODES:
                raise ValueError('mode is push_to_talk, wake or conversation')
            self.settings.mode = message['mode']
            self._reset_wake()
            self.emit('mode', mode=self.settings.mode)
        elif kind == 'mute':
            # Muting takes back anything still waiting: nothing is sent, and no yes is waited for, while you're muted.
            for utterance_id in list(self.pending):
                self._undo(utterance_id, reason='mute')
            if self.awaiting:
                self._resolve_confirm(False, source='mute')
            self.speaker.stop()
            self.ptt = False
            self.buffer = array('h')
            self._set_state('off')  # and the mic closes (mic()): muted means closed, not just ignored
        elif kind == 'unmute':
            if self.state == 'off':
                self._reset_wake()
                self._set_state('idle')
        elif kind == 'undo':
            self._undo(message.get('id'))
        elif kind == 'drop':
            # The window won't send it: voice was turned off, or it was heard for another project or profile.
            utterance_id = message.get('id')
            if utterance_id in self.pending:
                self._undo(utterance_id, reason='dropped')
            elif self.journal.state(utterance_id) == 'released':
                self.journal.write(utterance_id, 'cancelled', reason='dropped')
                self.emit('cancelled', id=utterance_id, reason='dropped')
        elif kind == 'scope':
            scope = message.get('scope')
            self.scope = scope if isinstance(scope, str) and scope else None
        elif kind == 'confirm':
            if self.awaiting and self.awaiting['id'] == message.get('id'):
                self._resolve_confirm(bool(message.get('yes')), source='click')
        elif kind == 'delivered':
            if self.journal.state(message.get('id')) == 'released':
                self.journal.write(message['id'], 'delivered')
        elif kind == 'failed':
            # Home refused it (signed out, no account for the job). It's dropped, not replayed hours later, and the
            # window says so; the words are on screen to send again by hand.
            if self.journal.state(message.get('id')) == 'released':
                self.journal.write(message['id'], 'cancelled', reason='refused')
            self.emit('send_failed', id=message.get('id'), detail=message.get('detail'))
        elif kind == 'speak':
            text = (message.get('text') or '').strip()
            if text and self.state != 'off':
                self.speaker.say(spoken(text), key=message.get('key'))
        elif kind == 'stop_speaking':
            self.speaker.stop()
        elif kind == 'settings':
            for key, value in (message.get('settings') or {}).items():
                if key in ADJUSTABLE and isinstance(value, (int, float)) and not isinstance(value, bool):
                    setattr(self.settings, key, max(ADJUSTABLE[key], float(value)))
        elif kind == 'status':
            self.emit('status', state=self.state, mode=self.settings.mode, speaking=self.speaker.speaking(),
                      pending=[{'id': key, 'text': value['text']} for key, value in self.pending.items()],
                      awaiting=self.awaiting and {'id': self.awaiting['id'], 'text': self.awaiting['text']},
                      # Sent but not confirmed delivered: a window that reloaded sends these again (same id, one copy).
                      released=[{'id': entry['id'], 'text': entry.get('text', ''), 'scope': entry.get('scope')} for entry in self.journal.unfinished()
                                if entry.get('state') == 'released'],
                      engines={name: getattr(self.engines, name) is not None
                               for name in ('vad', 'wake', 'turn', 'asr', 'tts')})
        else:
            raise ValueError(f'unknown voice command {kind!r}')

    # ------------------------------------------------------------------ audio
    def idle(self, seconds):
        """Time passing with no audio (mic denied, unplugged or muted): deadlines still come due."""
        self._advance(seconds)

    def _advance(self, seconds):
        self.now += seconds
        self._deadlines()
        speaking = self.speaker.speaking()
        if self.was_speaking and not speaking:
            self.emit('spoken')
            if self.settings.mode == 'wake' and self.state == 'idle':
                self._listen(self.settings.follow_up_seconds)
        self.was_speaking = speaking
        return speaking

    def feed(self, frame):
        speaking = self._advance(FRAME_SECONDS)
        if self.state == 'off':
            return
        if self.state == 'idle' and self.settings.mode == 'conversation':
            self.listen_until = None
            self._set_state('listening')
        vad = self.engines.vad
        speech = (vad.speech(frame) if vad else _energy_speech(frame)) >= self.settings.speech_threshold
        if speaking and self.state != 'hearing':
            self.preroll.append(frame)
            self.barge_run = self.barge_run + FRAME_SECONDS if speech else 0.0
            if self.barge_run >= self.settings.barge_in_seconds:
                self._barge('voice')
                self.barge_run = 0.0
                if self.state == 'idle' and self.settings.mode == 'push_to_talk':
                    return  # hold to talk: speaking over Dervo silences it, but only the hotkey makes a request
                self._start_hearing(preroll_seconds=self.barge_run + self.settings.preroll_seconds)
                self.speech_seconds = self.barge_run
            return
        if self.state == 'idle':
            self.preroll.append(frame)
            if self.settings.mode == 'wake' and self.engines.wake:
                keyword = self.engines.wake.heard(frame)
                if keyword:
                    # The request usually runs straight on from "Hey Dervo", and the spotter only fires a few hundred
                    # ms after the wake phrase ends. So hearing starts with the recent audio, wake phrase included, and
                    # strip_wake takes the phrase off the transcript.
                    self.emit('wake', keyword=keyword)
                    self.speaker.stop()
                    self._start_hearing(preroll_seconds=self.settings.wake_preroll_seconds)
                    self.after_wake = True
            return
        if self.state == 'listening':
            self.preroll.append(frame)
            if speech:
                self._start_hearing()
            elif self.listen_until is not None and self.now >= self.listen_until:
                self.listen_until = None
                self._reset_wake()
                self._set_state('idle')
            return
        # hearing
        self.buffer.extend(frame)
        if len(self.buffer) / RATE >= self.settings.max_utterance:
            self.ptt = False  # a hotkey held past the limit: what's heard so far is the utterance
            self._finish('long')
            return
        if speech:
            self.silence = 0.0
            self.speech_seconds += FRAME_SECONDS
            self.last_speech = self.now
            self.asked_turn = False
            return
        self.silence += FRAME_SECONDS
        if self.ptt:
            return
        if self.after_wake and self.speech_seconds == 0 and self.silence >= self.settings.max_pause:
            # "Hey Dervo" and then a pause: wait for the request without the wake word, as after any wake.
            self.after_wake = False
            self.buffer = array('h')
            self._listen(self.settings.listen_seconds)
            return
        if self.silence >= self.settings.max_pause:
            self._finish('pause')
        elif self.silence >= self.settings.min_pause and not self.asked_turn and self.speech_seconds > 0:
            self.asked_turn = True
            self.deciding = time.perf_counter()  # latency is counted from here: the pause has been heard
            turn = self.engines.turn
            if turn is not None:
                started = time.perf_counter()
                done = turn.complete(self.buffer)
                self.turn_ms = (time.perf_counter() - started) * 1000
                if done and self.engines.asr is not None:
                    # The sound says finished; the words get a say too. "Add a button that" with a falling voice still
                    # isn't a request, so a transcript ending on a dangling word waits for more (up to max_pause).
                    started = time.perf_counter()
                    text = strip_wake(self.engines.asr.transcribe(self.buffer).strip())
                    self.asr_ms = (time.perf_counter() - started) * 1000
                    if not intents.dangling(text):
                        self.early_text = text
                        self._finish('turn')
                elif done:
                    self._finish('turn')
                elif self.speech_seconds <= self.settings.short_seconds and self.engines.asr is not None:
                    # Smart Turn often hears a bare "No." or "Never mind." as unfinished. Short ones are transcribed
                    # now, and an answer or a take-back ends the turn at once instead of after max_pause.
                    started = time.perf_counter()
                    text = strip_wake(self.engines.asr.transcribe(self.buffer).strip())
                    self.asr_ms = (time.perf_counter() - started) * 1000
                    if intents.is_yes(text) or intents.is_no(text) or intents.is_cancel(text):
                        self.early_text = text
                        self._finish('short')

    # ------------------------------------------------------------------ inside
    def _listen(self, seconds):
        self.listen_until = self.now + seconds
        self._set_state('listening')

    def _reset_wake(self):
        if self.engines.wake is not None:
            self.engines.wake.reset()

    def _barge(self, source):
        if self.speaker.speaking():
            self.speaker.stop()
            self.emit('barge_in', source=source)

    def _start_hearing(self, preroll_seconds=None):
        keep = int((preroll_seconds or self.settings.preroll_seconds) / FRAME_SECONDS)
        frames = list(self.preroll)[-keep:] if keep else []
        self.buffer = array('h')
        for frame in frames:
            self.buffer.extend(frame)
        self.preroll.clear()
        self.early_text = None
        self.silence = 0.0
        self.speech_seconds = 0.0
        self.asked_turn = False
        self.turn_ms = None
        self.deciding = None
        self.listen_until = None
        self.after_wake = False
        self._set_state('hearing')

    def _after_utterance(self):
        if self.settings.mode == 'wake' and self.engines.wake is not None:
            self._listen(self.settings.follow_up_seconds)
        else:
            self._set_state('idle')
        self._reset_wake()

    def _finish(self, reason):
        audio, self.buffer = self.buffer, array('h')
        # From when the pause was first heard (turn check, early transcript) or, for the hotkey, from its release.
        decided = self.deciding if reason in ('turn', 'short') and self.deciding else time.perf_counter()
        if self.speech_seconds < self.settings.min_speech_seconds and reason != 'ptt':
            self.early_text = None
            self._after_utterance()
            return
        asr = self.engines.asr
        if self.early_text is not None:
            text, self.early_text = self.early_text, None
            asr_ms = self.asr_ms
        else:
            started = time.perf_counter()
            text = strip_wake(asr.transcribe(audio).strip()) if asr is not None and len(audio) else ''
            asr_ms = (time.perf_counter() - started) * 1000
        self._after_utterance()
        if not text:
            self.emit('heard_nothing', reason=reason)
            return
        self.emit('heard', text=text, reason=reason, asr_ms=round(asr_ms), turn_ms=self.turn_ms and round(self.turn_ms),
                  pause_ms=round(self.silence * 1000) if reason != 'ptt' else 0, seconds=round(len(audio) / RATE, 2))
        said = self._understand(text)
        if said:
            self.speaker.say(said, key='ack')
            # ack_ms: processing from the decision point to the ack starting. end_to_ack_ms adds the silence that had to
            # be heard first (min_pause, or max_pause when the turn model hesitated): what you actually wait.
            ack_ms = (time.perf_counter() - decided) * 1000
            pause_ms = 0 if reason == 'ptt' else (self.settings.min_pause if reason in ('turn', 'short') else self.silence) * 1000
            self.emit('metrics', ack_ms=round(ack_ms), asr_ms=round(asr_ms), pause_ms=round(pause_ms),
                      turn_ms=self.turn_ms and round(self.turn_ms), end_to_ack_ms=round(ack_ms + pause_ms), reason=reason)

    def _understand(self, text):
        """What a finished utterance does. Returns the short thing to say, or None."""
        if self.awaiting:
            if intents.is_yes(text) and not intents.is_clear_yes(text):
                # "Sure." or "Right." isn't enough to push or send: ask once more, and keep waiting.
                self.emit('confirm_unclear', id=self.awaiting['id'], text=text)
                self._listen(self.awaiting['until'] - self.now)
                return ACKS['unclear']
            if intents.is_yes(text):
                return self._resolve_confirm(True, source='voice')
            said = self._resolve_confirm(False, source='voice')
            if intents.is_no(text) or intents.is_cancel(text):
                return said
            # Something else entirely: the outward request is dropped, and this one is handled on its own.
        if intents.is_cancel(text):
            if self.pending:
                latest = max(self.pending, key=lambda key: self.pending[key]['until'])
                self._undo(latest, reason='voice')
                return ACKS['cancelled']
            self.emit('cancel_ignored', text=text)  # nothing left to take back; never sent as a message
            return None
        utterance_id = self.ids()
        what = intents.outward(text)
        if what:
            self.journal.write(utterance_id, 'confirming', text=text, what=what, scope=self.scope)
            self.awaiting = {'id': utterance_id, 'text': text, 'what': what,
                             'until': self.now + self.settings.confirm_seconds}
            readback = intents.readback(text, what)
            self.emit('confirm', id=utterance_id, text=text, what=what, readback=readback,
                      seconds=self.settings.confirm_seconds)
            # The yes needs no wake word: listen until the confirmation window closes.
            self._listen(self.settings.confirm_seconds)
            return readback
        self._hold(utterance_id, text)
        return ACKS['heard']

    def _hold(self, utterance_id, text, replay=False):
        until = self.now + self.settings.undo_seconds
        self.pending[utterance_id] = {'text': text, 'until': until}
        # A replay keeps the scope it was heard in (journal records merge); a new one takes the window's.
        self.journal.write(utterance_id, 'pending', text=text, **({} if replay else {'scope': self.scope}))
        self.emit('utterance', id=utterance_id, text=text, undo_seconds=self.settings.undo_seconds, replay=replay,
                  scope=self._scope_of(utterance_id))

    def _scope_of(self, utterance_id):
        return (self.journal.entries.get(utterance_id) or {}).get('scope')

    def _release(self, utterance_id, text):
        self.journal.write(utterance_id, 'released', text=text)
        self.emit('send', id=utterance_id, text=text, scope=self._scope_of(utterance_id))

    def _undo(self, utterance_id, reason='click'):
        if utterance_id in self.pending:
            self.pending.pop(utterance_id)
            self.journal.write(utterance_id, 'cancelled', reason=reason)
            self.emit('cancelled', id=utterance_id, reason=reason)
        elif self.journal.state(utterance_id) in ('released', 'delivered'):
            self.emit('undo_too_late', id=utterance_id)

    def _resolve_confirm(self, yes, source):
        waiting, self.awaiting = self.awaiting, None
        if waiting is None:
            return None
        self.mic()
        if yes:
            self._release(waiting['id'], waiting['text'])
            self.emit('confirmed', id=waiting['id'], source=source)
            return ACKS['going']
        reason = source if source in ('timeout', 'mute') else 'declined'
        self.journal.write(waiting['id'], 'cancelled', reason=reason)
        self.emit('cancelled', id=waiting['id'], reason=reason)
        return ACKS.get(reason)

    def _deadlines(self):
        for utterance_id, held in list(self.pending.items()):
            # Never send while you're mid-sentence: what you're saying may be "never mind".
            if self.now >= held['until'] and self.state != 'hearing':
                self.pending.pop(utterance_id)
                self._release(utterance_id, held['text'])
        if self.awaiting and self.now >= self.awaiting['until'] and self.state != 'hearing':
            said = self._resolve_confirm(False, source='timeout')
            if said and self.state != 'off':
                self.speaker.say(said, key='ack')


def spoken(text, limit=220):
    """A reply cut to what's worth hearing: markup dropped, the first sentence or two, and a pointer to the screen when
    there's more. The full text always stays on screen."""
    import re
    plain = re.sub(r'```.*?```', ' ', text, flags=re.S)
    plain = re.sub(r'`([^`]*)`', r'\1', plain)
    plain = re.sub(r'\[([^\]]+)\]\([^)]+\)', r'\1', plain)
    plain = re.sub(r'[*_#>]+', '', plain)
    plain = ' '.join(plain.split())
    if len(plain) <= limit:
        return plain
    sentences = re.split(r'(?<=[.?!])\s+', plain)
    kept = ''
    for sentence in sentences:
        if len(kept) + len(sentence) + 1 > limit:
            break
        kept = f'{kept} {sentence}'.strip()
    if not kept:
        kept = plain[:limit].rsplit(' ', 1)[0] + '.'
    return f'{kept} The rest is on screen.'
