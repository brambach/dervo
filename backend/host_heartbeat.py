"""The host's heartbeat (beta.18, notes/STOPPING-explore-20261005.md section 3).

The window used to kill the host when one request took 10 s, and the next host's restart pass ended every running
thread. A second thread here writes one line a second on the host's stdout, beside the replies, saying what the request
loop is on and for how long. The bridge (src/bridge.rs) reads it: a slow request with a beating host is answered "busy"
and the host is kept. Only a host that has exited or stopped beating for 30 s is ended.

A line is ``{"heartbeat": {"operation": "task_detail", "id": 7, "started": 1791288000.5, "seconds": 12.3}}``, or
``{"heartbeat": {"operation": null}}`` while the loop is waiting for a request. ``tick`` is background work between
requests. The thread keeps beating while the loop waits on git, the disk or a lock: it only needs the GIL for a moment.
"""
import json
import threading
import time

EVERY = 1.0


class Heartbeat:
    """Beats on ``stdout`` every ``every`` seconds. Replies and beats share ``lock``, so each line goes out whole."""

    def __init__(self, stdout, lock, every=EVERY, clock=time.monotonic, wall=time.time):
        self.stdout, self.lock, self.every = stdout, lock, every
        self.clock, self.wall = clock, wall
        self._doing = None
        self._stop = threading.Event()
        self._thread = None

    def doing(self, operation, request_id=None):
        """The loop has started ``operation`` (one assignment, so the beating thread reads a whole tuple)."""
        self._doing = (operation, request_id, self.clock(), self.wall())

    def idle(self):
        self._doing = None

    def line(self):
        doing = self._doing
        if doing is None:
            return json.dumps({'heartbeat': {'operation': None}})
        operation, request_id, began, started = doing
        beat = {'operation': operation, 'started': round(started, 3), 'seconds': round(self.clock() - began, 1)}
        if request_id is not None:
            beat['id'] = request_id
        return json.dumps({'heartbeat': beat})

    def beat(self):
        text = self.line()
        with self.lock:
            self.stdout.write(text + '\n')
            self.stdout.flush()

    def _run(self):
        while True:
            try:
                self.beat()
            except (OSError, ValueError):
                return  # the window has gone (a closed pipe or file); the loop ends on its own
            if self._stop.wait(self.every):
                return

    @property
    def started(self):
        return self._thread is not None

    def start(self):
        """Start beating. Calling it again does nothing."""
        if self._thread is None:
            self._thread = threading.Thread(target=self._run, name='dervo-heartbeat', daemon=True)
            self._thread.start()
        return self

    def stop(self):
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=self.every + 1)
