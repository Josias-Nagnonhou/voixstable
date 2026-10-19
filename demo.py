"""Mode démo : imite MiniMax avec des sons de synthèse (aucune vraie voix,
aucun crédit dépensé). Sert à découvrir l'outil avant d'avoir une clé API."""
import io
import os
import random
import tempfile
import time

import numpy as np

import audio


def fake_speech(sec, sr=44100, f0=128, var=2.8, rate=5.2, seed=None, gain=0.25):
    rng = np.random.default_rng(seed)
    out, t = [], 0.0
    while t < sec:
        if rng.random() < 0.1:
            n = int(sr * rng.uniform(0.15, 0.4))
            out.append(np.zeros(n)); t += n / sr; continue
        d = rng.uniform(0.7, 1.3) / rate
        n = int(sr * d)
        tt = np.arange(n) / sr
        f = f0 * 2 ** (rng.normal(0, var) / 12) * (1 + 0.04 * tt / d)
        ph = 2 * np.pi * np.cumsum(f) / sr
        s = sum(np.sin(k * ph) / k for k in range(1, 8)) * np.hanning(n) * gain * rng.uniform(0.6, 1)
        out.append(s); out.append(np.zeros(int(sr * 0.03))); t += d + 0.03
    y = np.concatenate(out)
    y += rng.normal(0, 0.002, len(y))
    return y.astype(np.float32)


def _to_mp3(y, sr=44100):
    fd, path = tempfile.mkstemp(suffix=".mp3")
    os.close(fd)
    audio.encode_mp3(y, sr, path, "128k")
    data = open(path, "rb").read()
    os.remove(path)
    return data


import hashlib
SPOKEN = {}   # empreinte audio -> texte lu (pour simuler la réécoute)


class DemoClient:
    def __init__(self, *a, **k):
        pass

    def upload(self, path, purpose):
        time.sleep(0.5)
        return random.randint(10 ** 8, 10 ** 9)

    def clone(self, file_id, voice_id, preview_text=None, **k):
        time.sleep(1.0)
        return {"demo_audio": "", "base_resp": {"status_code": 0}}

    def tts(self, text, voice_id, model="speech-2.8-hd", speed=1.0, emotion=None, **k):
        time.sleep(random.uniform(0.6, 1.4))
        plain = text
        pauses = 0.0
        import re
        for m in re.finditer(r"<#([\d.]+)#>", text):
            pauses += float(m.group(1))
        plain = re.sub(r"<#[\d.]+#>", "", text)
        sec = max(1.0, len(plain) / 14.5 / float(speed or 1))
        r = random.random()
        f0, rate, gain = 128, 5.2, 0.25
        if r < 0.12:
            f0 = 185            # ton qui dérive
        elif r < 0.2:
            sec *= 1.6          # traîne
        elif r < 0.26:
            gain = 0.07         # volume qui chute
        if emotion == "calm":
            f0 *= 0.97
        y = fake_speech(sec, f0=f0, rate=rate, gain=gain, var=2.0 if emotion == "calm" else 2.8)
        # pauses demandées
        segs = []
        if pauses:
            parts = max(1, text.count("<#") + 1)
            L = len(y) // parts
            for i in range(parts):
                segs.append(y[i * L:(i + 1) * L])
                if i < parts - 1:
                    segs.append(np.zeros(int(44100 * pauses / (parts - 1))))
            y = np.concatenate(segs)
        data = _to_mp3(y)
        SPOKEN[hashlib.sha1(data).hexdigest()] = plain
        return data, {"usage_characters": len(plain), "audio_length": int(len(y) / 44.1)}

    def transcribe(self, path, language="fr"):
        time.sleep(0.3)
        data = open(path, "rb").read()
        txt = SPOKEN.get(hashlib.sha1(data).hexdigest())
        if txt is None:
            return "Mes bien-aimés, écoutez bien ce que je vais vous dire.", 6.0
        words = txt.split()
        if len(words) > 8 and random.random() < 0.15:     # mot sauté simulé
            k = random.randrange(2, len(words) - 3)
            del words[k:k + 3]
        return " ".join(words), len(txt) / 14.5

    def voices(self, voice_type="voice_cloning"):
        return []

    def delete_voice(self, voice_id):
        return {}
