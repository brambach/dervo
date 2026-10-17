"""Regenerate the recorded clips the voice tests replay (backend/test_voice_recordings.py).

    python tools/voice_clips/make_clips.py MODELS_DIR

Synthetic speech from Kokoro (sherpa-onnx), several of its voices for accent spread (American and British, female and
male), with pauses, background noise and near-miss wake phrases mixed in. Deterministic: the same models give the same
files. These stand in for a real microphone in the cloud; recordings of real people belong next to them as
``real-*.wav`` with a line in clips.json, and the tests pick them up the same way.
"""
import json
import sys
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[1] / 'backend'))
from dervo_voice.audio import write_wav  # noqa: E402
from dervo_voice.engines import KokoroTts, to_int16  # noqa: E402

RATE = 16000
VOICES = {'af': 0, 'af_sarah': 3, 'am_adam': 5, 'am_michael': 6, 'bf_emma': 7, 'bm_george': 9, 'bm_lewis': 10}

# name: (parts, extra). A part is (voice, text) or a pause in seconds. 'noise' is the SNR in dB of pink noise mixed in.
# 'expect' is what the tests check; 'script' is the commands sent during the clip (seconds of clip time).
CLIPS = {
    'ptt-request': {'parts': [0.3, ('af', 'Fix the crash when exporting invoices to CSV.'), 0.5],
                    'expect': {'send': 'fix the crash when exporting invoices to csv'},
                    'script': [{'at': 0.1, 'type': 'ptt_down'}, {'at': 'end-0.2', 'type': 'ptt_up'}]},
    'ptt-british': {'parts': [0.3, ('bm_george', 'Rename the settings page to preferences.'), 0.5],
                    'expect': {'send': 'rename the settings page to preferences'},
                    'script': [{'at': 0.1, 'type': 'ptt_down'}, {'at': 'end-0.2', 'type': 'ptt_up'}]},
    'wake-request': {'parts': [1.0, ('af_sarah', "Hey Dervo, what's the export thread doing?"), 1.5],
                     'expect': {'wake': True, 'send': "what's the export thread doing"}, 'mode': 'wake'},
    'wake-male': {'parts': [1.0, ('am_michael', 'Hey Dervo, start a thread for dark mode.'), 1.5],
                  'expect': {'wake': True, 'send': 'start a thread for dark mode'}, 'mode': 'wake'},
    'wake-british': {'parts': [1.0, ('bf_emma', 'Hey Dervo, how are the tests going?'), 1.5],
                     'expect': {'wake': True, 'send': 'how are the tests going'}, 'mode': 'wake'},
    'wake-noisy': {'parts': [1.0, ('am_adam', 'Hey Dervo, check on the login bug.'), 1.5], 'noise': 12,
                   'expect': {'wake': True, 'send': 'check on the login bug'}, 'mode': 'wake'},
    'false-wake': {'parts': [0.5, ('af', 'Hey Devon, are you coming to dinner?'), 0.8,
                             ('bm_lewis', 'The server overflowed again last night.'), 0.6,
                             ('af_sarah', 'Hey, did you move the third row?'), 0.8], 'noise': 20,
                   'expect': {'wake': False, 'send': None}, 'mode': 'wake'},
    'half-sentence': {'parts': [0.3, ('af', 'Add a button that'), 0.9, ('af', 'exports the report as a PDF.'), 0.5],
                      'expect': {'send': 'add a button that exports the report as a pdf', 'utterances': 1},
                      'open_mic': True},
    'cancel': {'parts': [0.3, ('am_adam', 'Start a thread to clean up the old branches.'), 1.0, ('am_adam', 'Never mind.'), 0.5],
               'expect': {'send': None, 'cancelled': 1}, 'open_mic': True},
    'outward-yes': {'parts': [0.3, ('af_sarah', 'Push the export branch.'), 1.5, ('af_sarah', 'Yes.'), 0.5],
                    'expect': {'confirm': True, 'send': 'push the export branch'}, 'open_mic': True},
    'outward-no': {'parts': [0.3, ('bm_george', 'Merge the auth branch into main.'), 1.5, ('bm_george', 'No.'), 0.5],
                   'expect': {'confirm': True, 'send': None}, 'open_mic': True},
    'barge-in': {'parts': [2.5, ('af', 'Stop. What about the login bug?'), 0.5],
                 'expect': {'barge_in': True, 'send': 'what about the login bug'}, 'open_mic': True,
                 'script': [{'at': 0.1, 'type': 'speak', 'text': 'The export thread finished its tests. Three passed and none failed. It is waiting for your review before it commits anything.'}]},
    'follow-up': {'parts': [1.0, ('am_michael', 'Hey Dervo, start a thread for dark mode.'), 3.0,
                            ('am_michael', 'Also make the sidebar narrower.'), 1.0],
                  'expect': {'wake': True, 'sends': ['start a thread for dark mode', 'also make the sidebar narrower']},
                  'mode': 'wake'},
}


def pink(n, rng):
    white = rng.standard_normal(n)
    spectrum = np.fft.rfft(white)
    spectrum /= np.maximum(1, np.sqrt(np.arange(len(spectrum))))
    noise = np.fft.irfft(spectrum, n)
    return noise / (np.abs(noise).max() + 1e-9)


def build(tts, spec, rng):
    pieces = []
    for part in spec['parts']:
        if isinstance(part, (int, float)):
            pieces.append(np.zeros(int(part * RATE)))
        else:
            voice, text = part
            samples, rate = tts.synthesize(text, voice=VOICES[voice])
            pieces.append(np.frombuffer(to_int16(samples, rate).tobytes(), dtype=np.int16) / 32768.0)
    audio = np.concatenate(pieces) * 0.8
    if spec.get('noise') is not None:
        noise = pink(len(audio), rng)
        speech_power = np.mean(audio[np.abs(audio) > 0.01] ** 2)
        noise *= np.sqrt(speech_power / (10 ** (spec['noise'] / 10)) / np.mean(noise ** 2))
        audio = audio + noise
    return np.clip(audio, -1, 1)


def main(models):
    tts = KokoroTts(Path(models) / 'kokoro-en-v0_19')
    rng = np.random.default_rng(8)
    manifest = {}
    for name, spec in CLIPS.items():
        audio = build(tts, spec, rng)
        write_wav(HERE / f'{name}.wav', (audio * 32767).astype(np.int16))
        seconds = len(audio) / RATE
        script = []
        for command in spec.get('script', []):
            at = command['at']
            if isinstance(at, str) and at.startswith('end-'):
                at = seconds - float(at[4:])
            script.append({**command, 'at': round(at, 3)})
        manifest[name] = {'seconds': round(seconds, 2), 'mode': spec.get('mode', 'push_to_talk'),
                          'open_mic': spec.get('open_mic', False), 'script': script, 'expect': spec['expect'],
                          'noise_db': spec.get('noise')}
        print(name, round(seconds, 2))
    (HERE / 'clips.json').write_text(json.dumps(manifest, indent=1) + '\n')


if __name__ == '__main__':
    main(sys.argv[1])
