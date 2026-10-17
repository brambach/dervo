"""The local models behind the voice loop, each behind a small interface so the loop can be tested with fakes and a
model can be swapped (FluidAudio on the Neural Engine, say) without touching the loop.

    vad.speech(frame) -> probability 0..1            Silero VAD, one 512-sample frame at 16 kHz
    wake.heard(frame) -> keyword or None             sherpa-onnx keyword spotter, open vocabulary ("hey dervo")
    turn.complete(samples) -> bool                   Smart Turn v3.2, see smart_turn.py
    asr.transcribe(samples) -> text                  Parakeet TDT 0.6B v2 (int8) through sherpa-onnx
    tts.synthesize(text) -> (float samples, rate)    Kokoro 82M through sherpa-onnx

numpy, onnxruntime and sherpa-onnx are imported here only, so the loop and its tests run on the stdlib alone.
Frames are ``array('h')`` of int16 samples everywhere outside this file.
"""
import os
from pathlib import Path

RATE = 16000
FRAME = 512  # 32 ms, Silero's window at 16 kHz

# "Hey Dervo" and "OK Dervo" in the keyword model's BPE pieces (sentencepiece on its bpe.model, 8 Oct 2026). The
# numbers are sherpa-onnx's per-keyword boost (:) and trigger threshold (#); tuned on tools/voice_clips.
WAKE_PHRASES = ('▁HE Y ▁DE R VO :2.0 #0.25 @hey_dervo',
                '▁O K ▁DE R VO :2.0 #0.25 @ok_dervo')

KWS_DIR = 'sherpa-onnx-kws-zipformer-gigaspeech-3.3M-2024-01-01'
ASR_DIR = 'sherpa-onnx-nemo-parakeet-tdt-0.6b-v2-int8'
TTS_DIR = 'kokoro-en-v0_19'
VAD_FILE = 'silero_vad.onnx'
TURN_FILE = 'smart-turn-v3.2-cpu.onnx'


def _floats(frame):
    import numpy as np
    return np.asarray(frame, dtype=np.float32) / 32768.0


class SileroVad:
    def __init__(self, path, threads=1):
        import numpy as np
        import onnxruntime as ort
        options = ort.SessionOptions()
        options.intra_op_num_threads = threads
        options.inter_op_num_threads = 1
        self.session = ort.InferenceSession(str(path), sess_options=options, providers=['CPUExecutionProvider'])
        names = [i.name for i in self.session.get_inputs()]
        self._v4 = 'h' in names  # the sherpa-onnx copy is v4 (h, c); v5 takes one 'state' and 'sr'
        self._np = np
        self.reset()

    def reset(self):
        np = self._np
        if self._v4:
            self._h = np.zeros((2, 1, 64), np.float32)
            self._c = np.zeros((2, 1, 64), np.float32)
        else:
            self._state = np.zeros((2, 1, 128), np.float32)

    def speech(self, frame):
        x = _floats(frame)[None, :]
        if self._v4:
            prob, self._h, self._c = self.session.run(None, {'x': x, 'h': self._h, 'c': self._c})
        else:
            prob, self._state = self.session.run(None, {'input': x, 'state': self._state,
                                                         'sr': self._np.array(RATE, dtype=self._np.int64)})
        return float(prob.reshape(-1)[0])


class KeywordWake:
    def __init__(self, folder, phrases=WAKE_PHRASES, threads=1):
        import sherpa_onnx
        import tempfile
        folder = Path(folder)
        self._keywords = tempfile.NamedTemporaryFile('w', suffix='.txt', delete=False, encoding='utf-8')
        self._keywords.write('\n'.join(phrases) + '\n')
        self._keywords.close()
        stem = 'epoch-12-avg-2-chunk-16-left-64.int8.onnx'
        self.spotter = sherpa_onnx.KeywordSpotter(
            tokens=str(folder / 'tokens.txt'), encoder=str(folder / f'encoder-{stem}'),
            decoder=str(folder / f'decoder-{stem}'), joiner=str(folder / f'joiner-{stem}'),
            keywords_file=self._keywords.name, num_threads=threads, provider='cpu',
            keywords_score=1.5, keywords_threshold=0.25, num_trailing_blanks=1)
        os.unlink(self._keywords.name)  # read once above; don't leave one behind per start
        self.reset()

    def reset(self):
        self.stream = self.spotter.create_stream()

    def heard(self, frame):
        self.stream.accept_waveform(RATE, _floats(frame))
        found = None
        while self.spotter.is_ready(self.stream):
            self.spotter.decode_stream(self.stream)
            result = self.spotter.get_result(self.stream)
            if result:
                found = result
                self.spotter.reset_stream(self.stream)
        return found


class ParakeetAsr:
    def __init__(self, folder, threads=2):
        import sherpa_onnx
        folder = Path(folder)
        self.recognizer = sherpa_onnx.OfflineRecognizer.from_transducer(
            encoder=str(folder / 'encoder.int8.onnx'), decoder=str(folder / 'decoder.int8.onnx'),
            joiner=str(folder / 'joiner.int8.onnx'), tokens=str(folder / 'tokens.txt'), num_threads=threads,
            model_type='nemo_transducer', decoding_method='greedy_search', provider='cpu')

    def transcribe(self, samples):
        stream = self.recognizer.create_stream()
        stream.accept_waveform(RATE, _floats(samples))
        self.recognizer.decode_stream(stream)
        return stream.result.text.strip()


class KokoroTts:
    def __init__(self, folder, voice=0, speed=1.0, threads=2):
        import sherpa_onnx
        folder = Path(folder)
        config = sherpa_onnx.OfflineTtsConfig(
            model=sherpa_onnx.OfflineTtsModelConfig(
                kokoro=sherpa_onnx.OfflineTtsKokoroModelConfig(
                    model=str(folder / 'model.onnx'), voices=str(folder / 'voices.bin'),
                    tokens=str(folder / 'tokens.txt'), data_dir=str(folder / 'espeak-ng-data')),
                num_threads=threads, provider='cpu'),
            max_num_sentences=1)
        self.tts = sherpa_onnx.OfflineTts(config)
        self.voice, self.speed = voice, speed

    @property
    def rate(self):
        return self.tts.sample_rate

    def synthesize(self, text, voice=None, speed=None):
        audio = self.tts.generate(text, sid=self.voice if voice is None else voice,
                                  speed=self.speed if speed is None else speed)
        return list(audio.samples), audio.sample_rate


def to_int16(samples, rate, want=RATE):
    """Float samples at ``rate`` to int16 at ``want``, by linear interpolation. Good enough for test clips and for the
    mic helper, which resamples playback itself on the Mac."""
    import numpy as np
    from array import array
    audio = np.asarray(samples, dtype=np.float64)
    if rate != want and len(audio):
        positions = np.arange(0, len(audio) * want / rate) * rate / want
        audio = np.interp(positions, np.arange(len(audio)), audio)
    return array('h', np.clip(audio * 32767, -32768, 32767).astype(np.int16).tobytes())


class Engines:
    """All five, loaded from one models folder. Anything missing is None and the loop works without it: no wake model
    means push-to-talk only, no turn model means silence decides the end, no voice means text-only receipts."""

    def __init__(self, vad=None, wake=None, turn=None, asr=None, tts=None):
        self.vad, self.wake, self.turn, self.asr, self.tts = vad, wake, turn, asr, tts

    @classmethod
    def load(cls, folder, want=('vad', 'wake', 'turn', 'asr', 'tts')):
        folder = Path(folder)
        found = {}
        if 'vad' in want and (folder / VAD_FILE).exists():
            found['vad'] = SileroVad(folder / VAD_FILE)
        if 'wake' in want and (folder / KWS_DIR).is_dir():
            found['wake'] = KeywordWake(folder / KWS_DIR)
        if 'turn' in want and (folder / TURN_FILE).exists():
            from .smart_turn import SmartTurn
            found['turn'] = SmartTurn(folder / TURN_FILE)
        if 'asr' in want and (folder / ASR_DIR).is_dir():
            found['asr'] = ParakeetAsr(folder / ASR_DIR)
        if 'tts' in want and (folder / TTS_DIR).is_dir():
            found['tts'] = KokoroTts(folder / TTS_DIR)
        return cls(**found)
