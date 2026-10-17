"""Where frames come from and where Dervo's voice goes.

Live on a Mac, both ends are the ``dervo-voice-audio`` helper (voice/mac/), which runs AVAudioEngine with Apple's
voice processing on, so the microphone signal arrives with Dervo's own voice already cancelled out:

* helper stdout: raw 16 kHz mono int16 little-endian, 512 samples per frame.
* helper stdin: playback packets, ``b'DVPA'`` + uint32 sample rate + uint32 byte count + int16 samples. Packets with
  no samples are controls, by rate: 0 stop now and drop anything queued, 1 close the microphone, 2 open it again.
* helper stderr: one JSON object per line about the device (``{"audio": "ready"|"denied"|"lost"|"changed"|"closed", ...}``).

In tests both ends are files: ``WavSource`` reads a recorded clip, ``TimelineSink`` keeps what would have been played
and when, so a test can check that a barge-in cut the voice off.
"""
import json
import struct
import subprocess
import threading
import wave
from array import array

FRAME = 512
RATE = 16000
MAGIC = b'DVPA'


def read_wav(path):
    with wave.open(str(path), 'rb') as clip:
        if clip.getframerate() != RATE or clip.getnchannels() != 1 or clip.getsampwidth() != 2:
            raise ValueError(f'{path}: voice clips are 16 kHz mono 16-bit')
        samples = array('h')
        samples.frombytes(clip.readframes(clip.getnframes()))
        return samples


def write_wav(path, samples, rate=RATE):
    with wave.open(str(path), 'wb') as clip:
        clip.setnchannels(1)
        clip.setsampwidth(2)
        clip.setframerate(rate)
        clip.writeframes(array('h', samples).tobytes())


def frames(samples, tail_seconds=0.0):
    """Frames of a clip, then ``tail_seconds`` of silence so deadlines after the clip still come due."""
    for start in range(0, len(samples) - FRAME + 1, FRAME):
        yield samples[start:start + FRAME]
    for _ in range(int(tail_seconds * RATE / FRAME)):
        yield array('h', bytes(FRAME * 2))


class PipeSource:
    def __init__(self, stream):
        self.stream = stream

    def __iter__(self):
        size = FRAME * 2
        while True:
            chunk = self.stream.read(size)
            if not chunk or len(chunk) < size:
                return
            frame = array('h')
            frame.frombytes(chunk)
            yield frame


class TimelineSink:
    """Records playback as (start, samples, rate) and cuts the one playing when stopped."""

    def __init__(self, clock):
        self.clock = clock
        self.played = []
        self.stops = []

    def play(self, pcm, rate, at):
        self.played.append({'at': at, 'seconds': len(pcm) / rate, 'cut': None})

    def stop(self):
        now = self.clock()
        self.stops.append(now)
        for item in self.played:
            if item['at'] <= now < item['at'] + item['seconds'] and item['cut'] is None:
                item['cut'] = now - item['at']
            elif item['at'] > now and item['cut'] is None:
                item['cut'] = 0.0


class NullSink:
    def play(self, pcm, rate, at):
        pass

    def stop(self):
        pass


class PipeSink:
    def __init__(self, stream):
        self.stream = stream
        self.lock = threading.Lock()

    def _write(self, rate, data):
        with self.lock:
            try:
                self.stream.write(MAGIC + struct.pack('<II', rate, len(data)) + data)
                self.stream.flush()
            except (BrokenPipeError, ValueError, OSError):
                pass  # the helper is gone; the supervisor restarts the whole voice process

    def play(self, pcm, rate, at):
        self._write(rate, array('h', pcm).tobytes())

    def stop(self):
        self._write(0, b'')

    def capture(self, on):
        self._write(2 if on else 1, b'')


class LateSink:
    """Stands in until the helper is started, so the microphone isn't opened while the voice model is still loading."""

    def __init__(self):
        self.target = NullSink()

    def play(self, pcm, rate, at):
        self.target.play(pcm, rate, at)

    def stop(self):
        self.target.stop()


class Helper:
    """The Mac audio helper as a child process."""

    def __init__(self, path, emit):
        self.process = subprocess.Popen([str(path)], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                        stderr=subprocess.PIPE, bufsize=0)
        self.source = PipeSource(self.process.stdout)
        self.sink = PipeSink(self.process.stdin)
        threading.Thread(target=self._device_lines, args=(emit,), daemon=True).start()

    def _device_lines(self, emit):
        for line in self.process.stderr:
            try:
                event = json.loads(line)
            except ValueError:
                event = {'audio': 'log', 'detail': line.decode('utf-8', 'replace').strip()[:300]}
            if isinstance(event, dict):
                emit({'type': 'audio', **event})

    def close(self):
        if self.process.poll() is None:
            self.process.terminate()
