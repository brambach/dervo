"""The whole voice process on recorded clips: real models, real audio, the same command line the app runs.

Needs the models (DERVO_VOICE_MODELS, a folder laid out as dervo_voice/engines.py names) and numpy, onnxruntime and
sherpa-onnx in the Python running the tests. Without them every test here is skipped, so the gate stays green on a
machine that never turned voice on. Clips and what each should do: tools/voice_clips/clips.json.

Each clip runs as its own ``python -m dervo_voice --input clip.wav`` process. Results, with latency, go to
DERVO_VOICE_REPORT when it's set (a JSON file), which is where the numbers in the PR come from.
"""
import json
import os
import re
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
CLIPS = HERE.parent / 'tools' / 'voice_clips'
MODELS = os.environ.get('DERVO_VOICE_MODELS', '')


def _ready():
    if not MODELS or not Path(MODELS).is_dir():
        return 'set DERVO_VOICE_MODELS to a voice models folder'
    try:
        import numpy  # noqa: F401
        import onnxruntime  # noqa: F401
        import sherpa_onnx  # noqa: F401
    except ImportError as error:
        return f'voice dependencies missing: {error}'
    return None


SKIP = _ready()
REPORT = {}


def words(text):
    return ' '.join(re.findall(r"[a-z0-9']+", (text or '').lower()))


def word_errors(heard, wanted):
    """Word error rate: edits (substitutions, insertions, deletions) over the words wanted."""
    a, b = words(heard).split(), words(wanted).split()
    row = list(range(len(b) + 1))
    for i, word in enumerate(a, 1):
        previous, row[0] = row[0], i
        for j, other in enumerate(b, 1):
            previous, row[j] = row[j], min(row[j] + 1, row[j - 1] + 1, previous + (word != other))
    return row[-1] / max(1, len(b))


# A misheard word in a request is shown before it's sent (undo window), so the bar is "close", not "exact". Tighten it
# as the models get better; the report has each clip's actual rate.
MAX_WORD_ERRORS = 0.25


def run(name, spec):
    mode = 'conversation' if spec['open_mic'] else spec['mode']
    with tempfile.TemporaryDirectory() as folder:
        script = Path(folder) / 'script.jsonl'
        script.write_text(''.join(json.dumps(command) + '\n' for command in spec['script']))
        started = time.perf_counter()
        done = subprocess.run([sys.executable, '-m', 'dervo_voice', '--models', MODELS, '--input', str(CLIPS / f'{name}.wav'),
                               '--script', str(script), '--mode', mode, '--journal', str(Path(folder) / 'journal.jsonl')],
                              cwd=HERE, capture_output=True, text=True, timeout=300)
        wall = time.perf_counter() - started
    if done.returncode != 0:
        raise AssertionError(f'{name}: exit {done.returncode}\n{done.stderr[-2000:]}')
    events = [json.loads(line) for line in done.stdout.splitlines()]
    return events, wall


@unittest.skipIf(SKIP, SKIP)
class Recordings(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.manifest = json.loads((CLIPS / 'clips.json').read_text())

    @classmethod
    def tearDownClass(cls):
        if os.environ.get('DERVO_VOICE_REPORT'):
            Path(os.environ['DERVO_VOICE_REPORT']).write_text(json.dumps(REPORT, indent=1) + '\n')

    def check(self, name):
        spec = self.manifest[name]
        events, wall = run(name, spec)
        of = lambda kind: [event for event in events if event['type'] == kind]  # noqa: E731
        expect = spec['expect']
        sends = [words(event['text']) for event in of('send')]
        REPORT[name] = {'sends': [event['text'] for event in of('send')],
                        'heard': [event['text'] for event in of('heard')],
                        'metrics': of('metrics'), 'wake': [event['at'] for event in of('wake')],
                        'barge_in': [event['at'] for event in of('barge_in')], 'wall_seconds': round(wall, 2),
                        'clip_seconds': spec['seconds'], 'noise_db': spec.get('noise_db')}
        if 'wake' in expect:
            self.assertEqual(bool(of('wake')), expect['wake'], f'{name}: wake {of("wake")}')
        wanted = expect.get('sends') or ([expect['send']] if expect.get('send') else [])
        if 'send' in expect and expect['send'] is None:
            self.assertEqual(sends, [], f'{name}: nothing should reach Home')
        if wanted:
            self.assertEqual(len(sends), len(wanted), f'{name}: sent {sends}')
            rates = [word_errors(got, want) for got, want in zip(sends, wanted)]
            REPORT[name]['word_errors'] = [round(rate, 3) for rate in rates]
            for got, want, rate in zip(sends, wanted, rates):
                self.assertLessEqual(rate, MAX_WORD_ERRORS, f'{name}: heard {got!r} for {want!r}')
        if expect.get('utterances'):
            self.assertEqual(len(of('heard')), expect['utterances'], f'{name}: cut into {REPORT[name]["heard"]}')
        if expect.get('confirm'):
            self.assertEqual(len(of('confirm')), 1, f'{name}: no read-back')
        if expect.get('cancelled'):
            self.assertEqual(len(of('cancelled')), expect['cancelled'], name)
        if expect.get('barge_in'):
            self.assertTrue(of('barge_in'), f'{name}: Dervo kept talking')
            end = of('end')[0]
            cut = [item for item in end['played'] if item['cut'] is not None]
            self.assertTrue(cut, f'{name}: playback was never cut')
        return events

    def test_ptt_request(self):
        self.check('ptt-request')

    def test_ptt_british(self):
        self.check('ptt-british')

    def test_wake_request(self):
        self.check('wake-request')

    def test_wake_male(self):
        self.check('wake-male')

    def test_wake_british(self):
        self.check('wake-british')

    def test_wake_noisy(self):
        self.check('wake-noisy')

    def test_false_wake_phrases_do_not_wake_it(self):
        self.check('false-wake')

    def test_a_thinking_pause_is_not_the_end(self):
        self.check('half-sentence')

    def test_never_mind_takes_it_back(self):
        self.check('cancel')

    def test_outward_request_needs_spoken_yes(self):
        self.check('outward-yes')

    def test_outward_request_dropped_on_no(self):
        self.check('outward-no')

    def test_barge_in(self):
        self.check('barge-in')

    def test_follow_up_without_wake_word(self):
        self.check('follow-up')

    def test_first_response_is_fast(self):
        """Speech end to Dervo starting its ack, on this machine's CPU. The 800 ms target in the design is for the
        Mac; this checks processing time after the pause (ASR + turn + ack), which is what code can change."""
        for name in ('ptt-request', 'wake-request'):
            events = self.check(name)
            metrics = [event for event in events if event['type'] == 'metrics']
            self.assertTrue(metrics, name)
            self.assertLess(metrics[0]['end_to_ack_ms'], 1500, f'{name}: {metrics}')


if __name__ == '__main__':
    unittest.main()
