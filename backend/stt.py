"""Speech-to-text for the app: faster-whisper on the CPU (int8). base.en transcribes ~8x faster than real time on this
laptop; small.en is more accurate at ~3x. Loaded on first use, unloaded after a few idle minutes.

  transcribe(wav_bytes) -> text      (16-bit PCM WAV at any sample rate, mono or stereo)
"""
import io
import os
import threading
import time
import wave

import numpy as np

import paths
MODEL_DIR = os.path.join(paths.models_dir(), "whisper")
IDLE_UNLOAD = 5 * 60
_models = {}
_lock = threading.Lock()
_last = 0.0


class STTError(Exception):
    pass


# approximate download sizes (int8 CTranslate2 weights + tokenizer), for the consent prompt and the progress bar
WHISPER_SIZES = {"tiny.en": 75 << 20, "base.en": 145 << 20, "small.en": 485 << 20, "medium.en": 1530 << 20}


def _cached(size):
    """Is this whisper model already in the models folder (faster-whisper's Hugging Face cache layout)?"""
    if not os.path.isdir(MODEL_DIR):
        return False
    for name in os.listdir(MODEL_DIR):
        if name.startswith("models--") and name.endswith("faster-whisper-" + size):
            for dp, _, files in os.walk(os.path.join(MODEL_DIR, name)):
                if "model.bin" in files:
                    return True
    return False


def _model(size):
    global _last
    with _lock:
        if size not in _models:
            try:
                from faster_whisper import WhisperModel
            except ImportError:
                # installed builds leave it out: its audio decoder (PyAV) bundles GPL-licensed FFmpeg codecs
                raise RuntimeError("Voice input isn't included in this version of Zazen (it will be an optional "
                                   "download).") from None
            rid = None
            if not _cached(size):                             # first use: the model (~75-250 MB) is downloaded: ask
                import downloads
                ok, rid = downloads.ask("whisper:" + size, f"Speech recognition model ({size})",
                                        "Voice input needs a speech-to-text model. It is downloaded once and runs on "
                                        "your CPU; your audio never leaves this PC.",
                                        WHISPER_SIZES.get(size, 0), "huggingface.co (Systran/faster-whisper)")
                if not ok:
                    raise STTError("Voice input needs its speech model: the download was declined")
                downloads.track(rid, MODEL_DIR, WHISPER_SIZES.get(size, 0))
            _models.clear()                                   # keep only one loaded
            try:
                _models[size] = WhisperModel(size, device="cpu", compute_type="int8", cpu_threads=8,
                                             download_root=MODEL_DIR)
            except Exception as e:
                if rid:
                    import downloads
                    downloads.finish(rid, False, e)
                raise
            if rid:
                import downloads
                downloads.finish(rid, True)
        _last = time.time()
        return _models[size]


def _pcm16k(wav_bytes):
    try:
        with wave.open(io.BytesIO(wav_bytes)) as w:
            sr, ch, width = w.getframerate(), w.getnchannels(), w.getsampwidth()
            raw = w.readframes(w.getnframes())
    except wave.Error as e:
        raise STTError(f"not a WAV recording ({e})")
    if width != 2:
        raise STTError("expected 16-bit PCM audio")
    audio = np.frombuffer(raw, dtype="<i2").astype(np.float32) / 32768.0
    if ch > 1:
        audio = audio.reshape(-1, ch).mean(axis=1)
    if sr != 16000:
        from scipy.signal import resample_poly
        g = np.gcd(int(sr), 16000)
        audio = resample_poly(audio, 16000 // g, int(sr) // g).astype(np.float32)
    return audio


def transcribe(wav_bytes, size="base.en"):
    audio = _pcm16k(wav_bytes)
    if len(audio) < 16000 * 0.3:
        return ""
    segs, _info = _model(size or "base.en").transcribe(audio, beam_size=1, vad_filter=True,
                                                       language="en" if str(size).endswith(".en") else None,
                                                       condition_on_previous_text=False)
    return " ".join(s.text.strip() for s in segs).strip()


def _reaper():
    while True:
        time.sleep(60)
        with _lock:
            if _models and time.time() - _last > IDLE_UNLOAD:
                _models.clear()


threading.Thread(target=_reaper, daemon=True, name="stt-reaper").start()
