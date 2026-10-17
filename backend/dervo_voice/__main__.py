"""The voice process. JSON lines in on stdin (commands), JSON lines out on stdout (events).

Live:   python -m dervo_voice --models DIR --journal FILE --helper PATH/dervo-voice-audio
Clips:  python -m dervo_voice --models DIR --input clip.wav [--script commands.jsonl] [--tail 8]

With ``--input`` the clip is played through the loop as fast as it can go, commands come from ``--script`` (each line
has ``at``, in seconds of clip time), speech goes nowhere, and the process exits at the end with ``{"type": "end"}``.
That's how test_voice_recordings.py drives it.

Live, frames and commands meet in one queue, so commands, the undo window and the yes deadline keep working when no
audio arrives (mic denied, no input device, muted). With no ``--helper`` there's no microphone at all: the process says
so (``unavailable``) and still answers commands, rather than reading its command channel as audio.

Exit codes: 0 asked to stop, the app went away (stdin closed) or the clip ended; 3 the audio helper went away (the app
restarts the process).
"""
import argparse
import json
import queue
import sys
import threading
import time
from pathlib import Path

from . import audio
from .engines import Engines
from .journal import Journal
from .loop import Settings, VoiceLoop
from .speaker import Speaker

_out = threading.Lock()


def emit(event):
    with _out:
        sys.stdout.write(json.dumps(event) + '\n')
        sys.stdout.flush()


def _commands(stream, inbox):
    for line in stream:
        line = line.strip()
        if not line:
            continue
        try:
            message = json.loads(line)
        except ValueError:
            emit({'type': 'error', 'where': 'command', 'detail': 'not JSON'})
            continue
        if isinstance(message, dict):
            inbox.put(('command', message))
    inbox.put(('command', {'type': 'shutdown'}))  # the app closed our stdin: it quit or crashed, so stop now


def _frames(source, inbox):
    for frame in source:
        inbox.put(('frame', frame))
    inbox.put(('ended', None))


IDLE = 0.25  # with no audio for this long, the loop's clock moves on wall time instead


def main(argv=None):
    parser = argparse.ArgumentParser(prog='dervo_voice')
    parser.add_argument('--models', required=True)
    parser.add_argument('--journal')
    parser.add_argument('--helper')
    parser.add_argument('--input')
    parser.add_argument('--script')
    parser.add_argument('--tail', type=float, default=8.0)
    parser.add_argument('--mode', choices=('push_to_talk', 'wake', 'conversation'), default='push_to_talk')
    parser.add_argument('--settings', default='{}', help='JSON object of loop.Settings overrides')
    parser.add_argument('--site', help="folder of voice's own libraries (numpy, onnxruntime, sherpa-onnx), see setup.py")
    args = parser.parse_args(argv)
    if args.site:
        sys.path.insert(0, args.site)

    started = time.perf_counter()
    missing_libraries = None
    try:
        # The libraries first, so a build without them says so before anyone downloads 830 MB of models for nothing.
        import numpy, onnxruntime, sherpa_onnx  # noqa: F401
        engines = Engines.load(args.models) if Path(args.models).is_dir() else Engines()
    except ImportError as error:
        # The app ships these (scripts/voice_runtime.py); a build without them says so instead of crashing in a loop.
        missing_libraries = f'Voice\'s libraries are missing from this build ({error.name or error}).'
        engines = Engines()
    loaded = {name: getattr(engines, name) is not None for name in ('vad', 'wake', 'turn', 'asr', 'tts')}
    settings = Settings(mode=args.mode, **json.loads(args.settings))
    if settings.mode == 'wake' and engines.wake is None:
        settings.mode = 'push_to_talk'

    helper = None
    if args.input:
        source = audio.frames(audio.read_wav(args.input), tail_seconds=args.tail)
        clock_box = {}
        sink = audio.TimelineSink(lambda: clock_box['loop'].now)
    else:
        source, sink = None, audio.LateSink()  # the helper starts once the voice is ready, below

    holder = {}
    speaker = Speaker(engines.tts, sink, clock=lambda: holder['loop'].now, emit=emit, threaded=not args.input)
    voice = VoiceLoop(engines, emit, Journal(args.journal), speaker, settings)
    holder['loop'] = voice
    if args.input:
        clock_box['loop'] = voice

    if not args.input and args.helper:
        helper = audio.Helper(args.helper, emit)
        source, sink.target = helper.source, helper.sink
        voice.capture = helper.sink.capture
    voice.mic()  # the helper starts with the mic closed; this opens it only if the mode listens
    emit({'type': 'ready', 'engines': loaded, 'mode': settings.mode, 'load_ms': round((time.perf_counter() - started) * 1000)})
    if missing_libraries:
        emit({'type': 'unavailable', 'reason': 'libraries', 'detail': missing_libraries})
    elif not loaded['asr']:
        emit({'type': 'unavailable', 'reason': 'models', 'detail': f'No speech model in {args.models}'})
    if not args.input and not args.helper:
        emit({'type': 'unavailable', 'reason': 'audio', 'detail': 'No microphone helper.'})
    voice.recover()

    def handle(message):
        if message.get('type') == 'shutdown':
            return False
        try:
            voice.command(message)
        except ValueError as error:
            emit({'type': 'error', 'where': 'command', 'detail': str(error)})
        return True

    def feed(frame):
        try:
            voice.feed(frame)
        except Exception as error:  # one bad frame or model hiccup never ends the process
            emit({'type': 'error', 'where': 'loop', 'detail': str(error)[:300]})

    if args.input:
        scripted = []
        if args.script:
            scripted = sorted((json.loads(line) for line in Path(args.script).read_text().splitlines() if line.strip()),
                              key=lambda message: message['at'])
        for frame in source:
            while scripted and scripted[0]['at'] <= voice.now:
                message = dict(scripted.pop(0))
                message.pop('at')
                if not handle(message):
                    return 0
            feed(frame)
        emit({'type': 'end', 'at': round(voice.now, 3), 'played': sink.played})
        return 0

    inbox = queue.Queue()
    threading.Thread(target=_commands, args=(sys.stdin, inbox), name='dervo-voice-commands', daemon=True).start()
    if source is not None:
        threading.Thread(target=_frames, args=(source, inbox), name='dervo-voice-frames', daemon=True).start()
    last = time.monotonic()
    try:
        while True:
            try:
                kind, item = inbox.get(timeout=IDLE)
            except queue.Empty:
                kind, item = 'idle', None
            if kind == 'command':
                if not handle(item):
                    return 0
            elif kind == 'frame':
                feed(item)
                last = time.monotonic()
            elif kind == 'ended':
                emit({'type': 'error', 'where': 'audio', 'detail': 'The microphone stream ended.'})
                return 3
            if time.monotonic() - last >= IDLE:  # no audio coming in: keep time moving for the deadlines
                now = time.monotonic()
                try:
                    voice.idle(now - last)
                except Exception as error:
                    emit({'type': 'error', 'where': 'loop', 'detail': str(error)[:300]})
                last = now
    finally:
        if helper:
            helper.close()


if __name__ == '__main__':
    sys.exit(main())
