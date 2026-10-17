"""Dervo's voice: text in, audio out to a sink, stoppable at once.

``say`` returns immediately. A worker thread synthesizes one sentence at a time and hands each to the sink as soon as
it's ready, so the first words play while the rest are still being made. Short fixed lines (the acks in loop.ACKS)
are synthesized once at start and replayed from memory, which is what keeps the first spoken response fast.

``speaking()`` is judged on the loop's own clock: playback started at t and lasting d seconds is busy until t + d. The
sink only has to play what it's given and drop everything on ``stop``.
"""
import queue
import re
import threading

from . import loop


def sentences(text):
    parts = [part.strip() for part in re.split(r'(?<=[.?!:;])\s+', text) if part.strip()]
    return parts or [text]


class Speaker:
    def __init__(self, tts, sink, clock, emit=None, cache=None, threaded=True):
        self.tts, self.sink, self.clock = tts, sink, clock
        self.emit = emit or (lambda event: None)
        self.cache = {}
        self.busy_until = 0.0
        self.generation = 0
        self.lock = threading.Lock()
        self.queue = queue.Queue()
        self.threaded = threaded
        if tts is not None:
            from .intents import READBACKS
            for line in (cache if cache is not None else [*loop.ACKS.values(), *READBACKS]):
                for sentence in sentences(line):  # _speak looks sentences up one at a time
                    self.cache[sentence] = self._make(sentence)
        if threaded:
            threading.Thread(target=self._work, name='dervo-voice-speaker', daemon=True).start()

    def _make(self, sentence):
        samples, rate = self.tts.synthesize(sentence)
        from .engines import to_int16
        return to_int16(samples, rate, want=rate), rate

    def speaking(self):
        with self.lock:
            return self.clock() < self.busy_until or (self.queue.unfinished_tasks > 0)

    def say(self, text, key=None):
        if self.tts is None:
            self.emit({'type': 'speak_skipped', 'text': text, 'key': key, 'reason': 'no voice model'})
            return
        with self.lock:
            generation = self.generation
        self.emit({'type': 'speaking', 'text': text, 'key': key})
        if self.threaded:
            self.queue.put((generation, text))
        else:
            self._speak(generation, text)

    def stop(self):
        with self.lock:
            self.generation += 1
            self.busy_until = 0.0
        while True:
            try:
                self.queue.get_nowait()
                self.queue.task_done()
            except queue.Empty:
                break
        self.sink.stop()

    def _work(self):
        while True:
            generation, text = self.queue.get()
            try:
                self._speak(generation, text)
            except Exception as error:  # a bad sentence never kills the voice
                self.emit({'type': 'error', 'where': 'speaker', 'detail': str(error)[:300]})
            finally:
                self.queue.task_done()

    def _speak(self, generation, text):
        for sentence in sentences(text):
            pcm, rate = self.cache.get(sentence) or self._make(sentence)
            with self.lock:
                if generation != self.generation:
                    return  # stopped while this was being made
                start = max(self.clock(), self.busy_until)
                self.busy_until = start + len(pcm) / rate
                # Under the lock, so a stop() can't land between the check and the play.
                self.sink.play(pcm, rate, at=start)
