"""Text-to-speech for the app: Kokoro-82M (ONNX, full precision on the CPU — 1.5x real time even while the agent's
model is busy; the int8 file was 4x slower on this CPU). Loaded on first use, unloaded after a few idle minutes.

  synth(text, voice="af_heart", speed=1.0) -> WAV bytes
  voice can blend narrators: "af_heart:70,bm_george:30"
"""
import io
import os
import re
import threading
import time
import wave

import numpy as np

import paths
MODEL_DIR = os.path.join(paths.models_dir(), "kokoro")
MODEL = os.path.join(MODEL_DIR, "kokoro-v1.0.onnx")
VOICES = os.path.join(MODEL_DIR, "voices-v1.0.bin")
IDLE_UNLOAD = 5 * 60
LANG = {"a": "en-us", "b": "en-gb", "e": "es", "f": "fr-fr", "h": "hi", "i": "it", "j": "ja", "p": "pt-br", "z": "cmn"}
LANG_NAME = {"a": "American English", "b": "British English", "e": "Spanish", "f": "French", "h": "Hindi",
             "i": "Italian", "j": "Japanese", "p": "Portuguese", "z": "Chinese"}

_k = None
_lock = threading.Lock()
_last = 0.0


class TTSError(Exception):
    pass


def available():
    return os.path.isfile(MODEL) and os.path.isfile(VOICES)


def _model():
    global _k, _last
    with _lock:
        if _k is None:
            if not available():
                raise TTSError(f"voice model not found in {MODEL_DIR}")
            from kokoro_onnx import Kokoro
            _k = Kokoro(MODEL, VOICES)
        _last = time.time()
        return _k


def voices():
    out = []
    for v in _model().get_voices():
        lang = v[:1]
        out.append({"id": v, "name": v.split("_", 1)[1].replace("_", " ").title() if "_" in v else v,
                    "language": LANG_NAME.get(lang, lang), "gender": "female" if v[1:2] == "f" else "male"})
    return out


def _style(k, voice):
    """A voice name, or a blend "a:70,b:30" -> (style vector or name, language code)."""
    parts = [p.strip() for p in str(voice or "af_heart").split(",") if p.strip()]
    if len(parts) == 1 and ":" not in parts[0]:
        return parts[0], LANG.get(parts[0][:1], "en-us")
    total, mix, lang = 0.0, None, None
    for p in parts:
        name, _, w = p.partition(":")
        w = float(w or 1)
        st = k.get_voice_style(name.strip())
        mix = st * w if mix is None else mix + st * w
        total += w
        lang = lang or LANG.get(name.strip()[:1], "en-us")
    return mix / max(total, 1e-6), lang


_MD = [
    (re.compile(r"\(\s*(sources?|via|from)\s*:?[^)]*\)", re.I), " "),          # "(Source: …)" is for the screen
    (re.compile(r"\bsources?\s*:\s*(\[\d+\][,\s]*)+\.?", re.I), " "),          # "Source: [4]."
    (re.compile(r"\[\d+\]"), ""),                                              # [1] citation markers
    (re.compile(r"```[\s\S]*?```"), " (code omitted) "),
    (re.compile(r"^\s*\|.*\|\s*$", re.M), " "),                          # table rows
    (re.compile(r"\$\$([\s\S]+?)\$\$|\$([^$\n]+)\$"), lambda m: " " + _speak_math(m.group(1) or m.group(2)) + " "),
    (re.compile(r"!\[[^\]]*\]\([^)]*\)"), " "),
    (re.compile(r"\[([^\]]+)\]\([^)]*\)"), r"\1"),
    (re.compile(r"https?://\S+"), " a link "),
    (re.compile(r"[A-Za-z]:[\\/][^\s,;)]+"), lambda m: " " + re.split(r"[\\/]", m.group(0).rstrip("\\/"))[-1] + " "),
    (re.compile(r"`([^`]+)`"), r"\1"),
    (re.compile(r"^#{1,6}\s*", re.M), ""),
    (re.compile(r"^\s*[-*+]\s+", re.M), ""),
    (re.compile(r"[*_~]{1,3}([^*_~]+)[*_~]{1,3}"), r"\1"),
    (re.compile(r"^>\s?", re.M), ""),
]
_MATH = [(r"\\sqrt\{([^{}]+)\}", r"the square root of \1"), (r"\\frac\{([^{}]+)\}\{([^{}]+)\}", r"\1 over \2"),
         (r"\^\{?2\}?", " squared"), (r"\^\{?3\}?", " cubed"), (r"\^\{([^{}]+)\}", r" to the power \1"),
         (r"\^(\w)", r" to the power \1"), (r"_\{?([^{}\s]+)\}?", r" sub \1"), (r"\\pm", " plus or minus "),
         (r"\\times|\\cdot", " times "), (r"\\le(q)?", " less than or equal to "), (r"\\ge(q)?", " greater than or equal to "),
         (r"\\neq", " not equal to "), (r"\\approx", " approximately "), (r"\\infty", " infinity "),
         (r"\\int", " the integral "), (r"\\sum", " the sum "), (r"\\pi", " pi "), (r"\\(alpha|beta|gamma|delta|theta|lambda|sigma|mu)", r" \1 "),
         (r"=", " equals "), (r"\\[a-zA-Z]+", " "), (r"[{}]", " ")]


def _speak_math(tex):
    s = tex
    for a, b in _MATH:
        s = re.sub(a, b, s)
    return " ".join(s.split())


def clean(text):
    s = str(text or "")
    for rx, rep in _MD:
        s = rx.sub(rep, s)
    return " ".join(s.split())


def synth(text, voice="af_heart", speed=1.0):
    t = clean(text)[:3000]
    if not t:
        raise TTSError("nothing to read")
    k = _model()
    style, lang = _style(k, voice)
    with _lock:
        samples, sr = k.create(t, voice=style, speed=max(0.5, min(2.0, float(speed or 1.0))), lang=lang)
    pcm = (np.clip(samples, -1, 1) * 32767).astype("<i2").tobytes()
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes(pcm)
    return buf.getvalue()


def _reaper():
    global _k
    while True:
        time.sleep(60)
        with _lock:
            if _k is not None and time.time() - _last > IDLE_UNLOAD:
                _k = None                         # give the ~400 MB back


threading.Thread(target=_reaper, daemon=True, name="tts-reaper").start()
