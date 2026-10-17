"""Smart Turn v3 (Pipecat, BSD-2): has the speaker finished, judged from the sound of the last 8 seconds.

The model wants Whisper's 80-band log-mel features. Pipecat computes them with transformers' WhisperFeatureExtractor;
this is the same computation in numpy so the voice process doesn't carry transformers. test_voice_recordings.py
checks it against the reference when transformers is installed.
"""
import numpy as np

RATE = 16000
SECONDS = 8
N_FFT = 400
HOP = 160
MELS = 80


def _hz_to_mel(freq):
    # Slaney scale: linear below 1 kHz, logarithmic above.
    freq = np.asarray(freq, dtype=np.float64)
    mels = 3.0 * freq / 200.0
    log_region = freq >= 1000.0
    return np.where(log_region, 15.0 + np.log(np.maximum(freq, 1e-10) / 1000.0) / (np.log(6.4) / 27.0), mels)


def _mel_to_hz(mels):
    mels = np.asarray(mels, dtype=np.float64)
    freq = 200.0 * mels / 3.0
    log_region = mels >= 15.0
    return np.where(log_region, 1000.0 * np.exp((np.log(6.4) / 27.0) * (mels - 15.0)), freq)


def mel_filters():
    bins = 1 + N_FFT // 2
    fft_freqs = np.linspace(0, RATE // 2, bins)
    mel_points = np.linspace(_hz_to_mel(0.0), _hz_to_mel(8000.0), MELS + 2)
    filter_freqs = _mel_to_hz(mel_points)
    diff = np.diff(filter_freqs)
    slopes = filter_freqs[None, :] - fft_freqs[:, None]
    down = -slopes[:, :-2] / diff[:-1]
    up = slopes[:, 2:] / diff[1:]
    filters = np.maximum(0, np.minimum(down, up))
    enorm = 2.0 / (filter_freqs[2:MELS + 2] - filter_freqs[:MELS])
    return (filters * enorm[None, :]).astype(np.float64)  # (bins, mels)


_FILTERS = None
_WINDOW = (0.5 - 0.5 * np.cos(2 * np.pi * np.arange(N_FFT) / N_FFT))  # periodic Hann


def features(samples):
    """``samples``: float audio at 16 kHz in [-1, 1]. Returns (1, 80, 800) float32, as the model expects."""
    global _FILTERS
    if _FILTERS is None:
        _FILTERS = mel_filters()
    audio = np.asarray(samples, dtype=np.float64)
    size = RATE * SECONDS
    audio = audio[-size:] if len(audio) > size else np.pad(audio, (size - len(audio), 0))
    audio = (audio - audio.mean()) / np.sqrt(audio.var() + 1e-7)
    padded = np.pad(audio, (N_FFT // 2, N_FFT // 2), mode='reflect')
    count = 1 + (len(padded) - N_FFT) // HOP
    frames = np.lib.stride_tricks.as_strided(padded, shape=(count, N_FFT),
                                             strides=(padded.strides[0] * HOP, padded.strides[0]))
    power = np.abs(np.fft.rfft(frames * _WINDOW, n=N_FFT)) ** 2  # (frames, bins)
    mel = np.maximum(1e-10, power @ _FILTERS).T  # (mels, frames)
    log_spec = np.log10(mel)[:, :-1]
    log_spec = np.maximum(log_spec, log_spec.max() - 8.0)
    log_spec = (log_spec + 4.0) / 4.0
    return log_spec[None, :, :].astype(np.float32)


class SmartTurn:
    """``complete(samples)`` gives the probability the turn is over. Threshold 0.5, as Pipecat uses."""

    def __init__(self, model_path, threshold=0.5):
        import onnxruntime as ort
        options = ort.SessionOptions()
        options.execution_mode = ort.ExecutionMode.ORT_SEQUENTIAL
        options.inter_op_num_threads = 1
        options.intra_op_num_threads = 2
        options.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
        self.session = ort.InferenceSession(str(model_path), sess_options=options, providers=['CPUExecutionProvider'])
        self.threshold = threshold

    def probability(self, samples):
        return float(self.session.run(None, {'input_features': features(samples)})[0][0].item())

    def complete(self, samples):
        return self.probability(samples) >= self.threshold
