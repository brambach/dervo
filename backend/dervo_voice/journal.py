"""Nothing heard is lost, and nothing heard is sent twice.

Every utterance is written here before anything acts on it, with the id that later becomes Home's clientId (Home
already treats a repeated clientId as the same message, project_orchestrator.Orchestrator.send). After a crash or a
relaunch, ``unfinished`` says what to do with each one:

* ``released`` (the send went to the app, no delivery came back): send again. Same id, so Home keeps one copy.
* ``pending`` (still inside its undo window): show it again with a fresh window.
* ``confirming`` (an outward request waiting for a spoken yes): drop it. A yes from before a restart doesn't count.

Only transcripts are kept, never audio, and only while they're unfinished: when an utterance is delivered or cancelled
the file is rewritten without it, so a "never mind" doesn't stay on disk.
"""
import json
import os
import time
from pathlib import Path

FINAL = ('delivered', 'cancelled')
COMPACT = 400


class Journal:
    def __init__(self, path):
        self.path = Path(path) if path else None
        self.entries = {}  # id -> latest record
        self._lines = 0
        if self.path and self.path.exists():
            text = self.path.read_text(encoding='utf-8', errors='replace')
            if text and not text.endswith('\n'):
                # A crash mid-write left half a line. Cut it off, or the next record would be glued onto it and lost too.
                keep = text.rfind('\n') + 1
                with open(self.path, 'r+b') as handle:
                    handle.truncate(len(text[:keep].encode('utf-8')))
                text = text[:keep]
            for line in text.splitlines():
                try:
                    record = json.loads(line)
                except ValueError:
                    continue  # a line cut off by a crash mid-write
                if isinstance(record, dict) and isinstance(record.get('id'), str):
                    self.entries[record['id']] = {**self.entries.get(record['id'], {}), **record}
                    self._lines += 1

    def write(self, utterance_id, state, **fields):
        record = {'id': utterance_id, 'state': state, 'at': round(time.time(), 3), **fields}
        entry = self.entries[utterance_id] = {**self.entries.get(utterance_id, {}), **record}
        if self.path:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with open(self.path, 'a', encoding='utf-8') as handle:
                handle.write(json.dumps(record) + '\n')
                handle.flush()
                os.fsync(handle.fileno())
            self._lines += 1
            if state in FINAL or self._lines > max(COMPACT, 2 * len(self.entries)):
                self.compact()
        return entry

    def state(self, utterance_id):
        return (self.entries.get(utterance_id) or {}).get('state')

    def unfinished(self):
        return [entry for entry in self.entries.values() if entry.get('state') not in FINAL]

    def compact(self):
        keep = self.unfinished()
        self.entries = {entry['id']: entry for entry in keep}
        if self.path:
            temporary = self.path.with_suffix('.tmp')
            with open(temporary, 'w', encoding='utf-8') as handle:
                handle.write(''.join(json.dumps(entry) + '\n' for entry in keep))
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, self.path)
        self._lines = len(keep)
