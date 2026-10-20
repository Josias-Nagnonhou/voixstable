"""Analyse, nettoyage et assemblage audio (numpy + ffmpeg embarqué)."""
import io
import os
import subprocess
import tempfile
import wave

import numpy as np
import imageio_ffmpeg

FFMPEG = imageio_ffmpeg.get_ffmpeg_exe()
SR = 16000          # analyse
OUT_SR = 44100      # export


# ---------------------------------------------------------------------------
# Entrées / sorties
# ---------------------------------------------------------------------------

def _low_priority():
    try:
        os.nice(10)
    except Exception:
        pass


def background_priority():
    """Baisse la priorité du fil de calcul courant : le site reste réactif pendant les analyses."""
    try:
        import threading
        os.setpriority(os.PRIO_PROCESS, threading.get_native_id(), 10)
    except Exception:
        pass


def _run(args, data=None):
    p = subprocess.run([FFMPEG, "-hide_banner", "-loglevel", "error", "-threads", "1", *args],
                       input=data, capture_output=True, preexec_fn=_low_priority)
    if p.returncode != 0:
        raise RuntimeError("ffmpeg: " + p.stderr.decode(errors="ignore")[-400:])
    return p.stdout


def decode(src, sr=SR, start=None, duration=None):
    """src: chemin ou bytes -> np.float32 mono [-1, 1]."""
    args = []
    if start is not None:
        args += ["-ss", f"{start:.3f}"]
    if isinstance(src, (bytes, bytearray)):
        args += ["-i", "pipe:0"]
        data = bytes(src)
    else:
        args += ["-i", src]
        data = None
    if duration is not None:
        args += ["-t", f"{duration:.3f}"]
    args += ["-vn", "-ac", "1", "-ar", str(sr), "-f", "s16le", "pipe:1"]
    raw = _run(args, data)
    return np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0


def encode_mp3(samples, sr, path, bitrate="192k", filters=None):
    pcm = (np.clip(samples, -1, 1) * 32767).astype(np.int16).tobytes()
    args = ["-f", "s16le", "-ar", str(sr), "-ac", "1", "-i", "pipe:0"]
    if filters:
        args += ["-af", filters]
    args += ["-codec:a", "libmp3lame", "-b:a", bitrate, "-y", path]
    _run(args, pcm)


# ---------------------------------------------------------------------------
# Caractéristiques
# ---------------------------------------------------------------------------

FRAME = 512   # 32 ms à 16 kHz
HOP = 256


def frame_db(x):
    n = 1 + max(0, (len(x) - FRAME) // HOP)
    if len(x) < FRAME:
        return np.array([-100.0])
    idx = np.arange(FRAME)[None, :] + HOP * np.arange(n)[:, None]
    # par blocs pour limiter la mémoire
    out = np.empty(n, dtype=np.float32)
    step = 20000
    for i in range(0, n, step):
        f = x[idx[i:i + step]]
        rms = np.sqrt(np.mean(f * f, axis=1) + 1e-12)
        out[i:i + step] = 20 * np.log10(rms + 1e-9)
    return out


def speech_mask(db):
    floor = np.percentile(db, 10)
    loud = np.percentile(db, 95)
    thr = max(floor + 0.35 * (loud - floor), loud - 35)
    return db > thr, floor, loud, thr


def pitch_of_frame(f, sr=SR, fmin=60, fmax=420):
    f = f - f.mean()
    if np.max(np.abs(f)) < 1e-4:
        return 0.0
    n = len(f)
    spec = np.fft.rfft(f * np.hanning(n), 2 * n)
    ac = np.fft.irfft(np.abs(spec) ** 2)[:n]
    ac /= ac[0] + 1e-12
    lo, hi = int(sr / fmax), int(sr / fmin)
    if hi >= n:
        hi = n - 1
    seg = ac[lo:hi]
    if len(seg) == 0:
        return 0.0
    k = int(np.argmax(seg))
    if seg[k] < 0.35:
        return 0.0
    lag = lo + k
    # interpolation parabolique
    if 0 < lag < n - 1:
        a, b, c = ac[lag - 1], ac[lag], ac[lag + 1]
        d = (a - c) / (2 * (a - 2 * b + c) + 1e-12)
        lag = lag + d
    return float(sr / lag)


def pitch_track(x, mask, max_frames=2500):
    """Renvoie (temps, f0) sur des trames voisées échantillonnées."""
    idx = np.where(mask)[0]
    if len(idx) == 0:
        return np.array([]), np.array([])
    if len(idx) > max_frames:
        idx = idx[np.linspace(0, len(idx) - 1, max_frames).astype(int)]
    times, f0 = [], []
    L = 1024
    for i in idx:
        s = i * HOP
        f = x[s:s + L]
        if len(f) < L:
            continue
        p = pitch_of_frame(f)
        if p > 0:
            times.append(s / SR)
            f0.append(p)
    return np.array(times), np.array(f0)


def syllable_rate(db, mask):
    """Approximation : pics d'énergie par seconde de parole."""
    if mask.sum() < 10:
        return 0.0
    env = np.convolve(db, np.ones(3) / 3, mode="same")
    peaks = 0
    last = -10
    min_gap = int(0.11 * SR / HOP)
    for i in range(1, len(env) - 1):
        if mask[i] and env[i] > env[i - 1] and env[i] >= env[i + 1]:
            lo = env[max(0, i - 6):i].min() if i > 0 else env[i]
            if env[i] - lo > 3.0 and i - last >= min_gap:
                peaks += 1
                last = i
    speech_sec = mask.sum() * HOP / SR
    return peaks / max(speech_sec, 0.1)


def semitone_std(f0):
    if len(f0) < 5:
        return 0.0
    st = 12 * np.log2(f0 / np.median(f0))
    st = st[np.abs(st) < 12]
    return float(np.std(st)) if len(st) else 0.0


def longest_silence(mask):
    best = cur = 0
    for m in mask:
        cur = 0 if m else cur + 1
        best = max(best, cur)
    return best * HOP / SR


# ---------------------------------------------------------------------------
# Analyse d'un échantillon à cloner
# ---------------------------------------------------------------------------

def categorize(rate, expressivity, energy_db):
    if expressivity >= 3.2 and rate >= 5.2:
        cat = "Commentateur énergique"
    elif expressivity >= 3.0:
        cat = "Orateur expressif (prédication, conférence)"
    elif rate <= 4.3 and expressivity < 2.2:
        cat = "Narrateur posé et calme"
    elif rate <= 4.6:
        cat = "Narrateur posé"
    else:
        cat = "Conversation naturelle"
    return cat


def describe_level(v, bounds, labels):
    for b, l in zip(bounds, labels):
        if v < b:
            return l
    return labels[-1]


def analyze_sample(path):
    x = decode(path, SR)
    total = len(x) / SR
    db = frame_db(x)
    mask, floor, loud, thr = speech_mask(db)
    speech_sec = float(mask.sum() * HOP / SR)
    snr = float(loud - floor)
    clip_ratio = float(np.mean(np.abs(x) > 0.985)) if len(x) else 0.0
    t, f0 = pitch_track(x, mask)
    expr = semitone_std(f0)
    rate = syllable_rate(db, mask)
    median_f0 = float(np.median(f0)) if len(f0) else 0.0

    warnings = []
    if total < 10:
        warnings.append("L'audio fait moins de 10 secondes : MiniMax en exige au moins 10.")
    if speech_sec < 25:
        warnings.append("Moins de 25 secondes de parole : le clone sera moins fidèle. Vise 1 à 2 minutes.")
    if snr < 22:
        warnings.append("Bruit de fond ou musique détecté : choisis un passage plus propre ou active la réduction de bruit.")
    if clip_ratio > 0.002:
        warnings.append("Le son sature par moments (volume trop fort à l'enregistrement).")
    if expr < 1.4 and len(f0) > 20:
        warnings.append("Voix très monotone dans cet échantillon : le clone risque d'être plat. Prends un passage plus vivant.")

    quality = 100
    quality -= max(0, 30 - snr) * 2.2
    quality -= min(25, max(0, 60 - speech_sec) * 0.5)
    quality -= min(20, clip_ratio * 4000)
    quality = int(max(5, min(100, quality)))

    return {
        "duree_totale": round(total, 1),
        "duree_parole": round(speech_sec, 1),
        "vitesse": round(rate, 2),
        "vitesse_label": describe_level(rate, [4.0, 4.8, 5.6], ["lente", "posée", "normale", "rapide"]),
        "expressivite": round(expr, 2),
        "expressivite_label": describe_level(expr, [1.6, 2.4, 3.2], ["monotone", "sobre", "naturelle", "très vivante"]),
        "energie_db": round(float(loud), 1),
        "proprete_db": round(snr, 1),
        "proprete_label": describe_level(snr, [18, 26, 34], ["bruyant", "moyen", "propre", "très propre"]),
        "hauteur_hz": round(median_f0, 1),
        "categorie": categorize(rate, expr, loud),
        "qualite": quality,
        "avertissements": warnings,
        "_x": x, "_db": db, "_mask": mask, "_t": t, "_f0": f0,
    }


def best_segment(an, target=90.0):
    """Choisit la fenêtre la plus propre et expressive (secondes)."""
    x, db, mask, t, f0 = an["_x"], an["_db"], an["_mask"], an["_t"], an["_f0"]
    total = len(x) / SR
    if total <= target + 15:
        return 0.0, total
    fps = SR / HOP
    w = int(target * fps)
    step = int(5 * fps)
    best, best_s = -1e9, 0
    noise_global = np.percentile(db, 10)
    for s in range(0, len(db) - w, step):
        seg_db = db[s:s + w]
        seg_m = mask[s:s + w]
        ratio = seg_m.mean()
        if ratio < 0.35:
            continue
        floor = np.percentile(seg_db, 10)
        loud = np.percentile(seg_db, 95)
        snr = loud - floor
        t0, t1 = s / fps, (s + w) / fps
        sel = (t >= t0) & (t < t1)
        expr = semitone_std(f0[sel]) if sel.sum() > 10 else 0
        x0, x1 = int(t0 * SR), int(t1 * SR)
        clip = np.mean(np.abs(x[x0:x1]) > 0.985)
        score = min(snr, 40) * 1.0 + min(expr, 4.5) * 4 + ratio * 15 - clip * 3000 - max(0, floor - noise_global) * 0.5
        if score > best:
            best, best_s = score, s
    start = best_s / fps
    # aligne sur un silence proche
    start = snap_to_silence(mask, start, fps)
    end = snap_to_silence(mask, start + target, fps)
    return max(0.0, start), min(total, max(end, start + 15))


def snap_to_silence(mask, t, fps, radius=2.0):
    i = int(t * fps)
    r = int(radius * fps)
    lo, hi = max(0, i - r), min(len(mask) - 1, i + r)
    cands = [j for j in range(lo, hi) if not mask[j]]
    if not cands:
        return t
    j = min(cands, key=lambda k: abs(k - i))
    return j / fps


def compress_silences(y, sr, max_sil=0.45, keep=0.3):
    """Raccourcit les longs silences (échantillon de clonage plus dense)."""
    hop = int(sr * 0.016)
    frame = hop * 2
    n = max(1, (len(y) - frame) // hop)
    rms = np.array([np.sqrt(np.mean(y[i * hop:i * hop + frame] ** 2) + 1e-12) for i in range(n)])
    dbs = 20 * np.log10(rms + 1e-9)
    thr = max(np.percentile(dbs, 10) + 0.35 * (np.percentile(dbs, 95) - np.percentile(dbs, 10)),
              np.percentile(dbs, 95) - 35)
    voiced = dbs > thr
    out = []
    run_start = None
    last = 0
    for i, v in enumerate(list(voiced) + [True]):
        if not v and run_start is None:
            run_start = i
        elif v and run_start is not None:
            length = (i - run_start) * hop / sr
            if length > max_sil:
                a = run_start * hop
                out.append(y[last:a + int(keep / 2 * sr)])
                last = i * hop - int(keep / 2 * sr)
            run_start = None
    out.append(y[last:])
    return np.concatenate(out) if out else y


def prompt_clip(y, sr, min_len=4.5, max_len=7.5):
    """Extrait < 8 s, bien rempli de parole, bordé par des silences."""
    hop = int(sr * 0.016)
    n = max(1, (len(y) - hop * 2) // hop)
    rms = np.array([np.sqrt(np.mean(y[i * hop:i * hop + hop * 2] ** 2) + 1e-12) for i in range(n)])
    dbs = 20 * np.log10(rms + 1e-9)
    thr = np.percentile(dbs, 10) + 0.35 * (np.percentile(dbs, 95) - np.percentile(dbs, 10))
    voiced = dbs > thr
    # frontières = débuts de silences d'au moins 150 ms
    bounds = [0]
    run = 0
    for i, v in enumerate(voiced):
        run = 0 if v else run + 1
        if run == int(0.15 * sr / hop):
            bounds.append(i - run // 2)
    bounds.append(n - 1)
    best, best_pair = -1, None
    for a in bounds:
        for b in bounds:
            L = (b - a) * hop / sr
            if min_len <= L <= max_len:
                dens = voiced[a:b].mean()
                score = dens + L / 20
                if score > best:
                    best, best_pair = score, (a, b)
    if not best_pair:
        a, b = 0, min(n - 1, int(6.5 * sr / hop))
    else:
        a, b = best_pair
    return y[a * hop:b * hop]


def prepare_clone_files(path, workdir, use_best=True, target=90.0):
    an = analyze_sample(path)
    total = an["duree_totale"]
    if use_best or total > 290:
        start, end = best_segment(an, min(target, 280))
    else:
        start, end = 0.0, total
    y = decode(path, OUT_SR, start=start, duration=end - start)
    y = compress_silences(y, OUT_SR)
    if len(y) / OUT_SR > 295:
        y = y[:int(295 * OUT_SR)]
    clone_path = os.path.join(workdir, "echantillon.mp3")
    encode_mp3(y, OUT_SR, clone_path, "192k", "highpass=f=70,loudnorm=I=-18:TP=-2:LRA=11")
    p = prompt_clip(y, OUT_SR)
    prompt_path = os.path.join(workdir, "extrait.mp3")
    encode_mp3(p, OUT_SR, prompt_path, "192k", "highpass=f=70,loudnorm=I=-18:TP=-2:LRA=11")
    info = {k: v for k, v in an.items() if not k.startswith("_")}
    info["segment_debut"] = round(start, 1)
    info["segment_fin"] = round(end, 1)
    info["duree_echantillon"] = round(len(y) / OUT_SR, 1)
    info["duree_extrait"] = round(len(p) / OUT_SR, 1)
    return info, clone_path, prompt_path


# ---------------------------------------------------------------------------
# Contrôle qualité d'un morceau généré
# ---------------------------------------------------------------------------

def trim(y, sr, pad=0.04):
    hop = int(sr * 0.01)
    if len(y) < hop * 3:
        return y
    n = len(y) // hop
    rms = np.sqrt(np.mean(y[:n * hop].reshape(n, hop) ** 2, axis=1) + 1e-12)
    db = 20 * np.log10(rms + 1e-9)
    thr = max(db.max() - 42, -60)
    idx = np.where(db > thr)[0]
    if len(idx) == 0:
        return y[:0]
    a = max(0, idx[0] * hop - int(pad * sr))
    b = min(len(y), (idx[-1] + 1) * hop + int(pad * sr))
    return y[a:b]


def chunk_metrics(mp3_bytes, chars, expected_pauses=0.0):
    x = decode(mp3_bytes, SR)
    x = trim(x, SR)
    dur = len(x) / SR
    if dur < 0.3:
        return {"duree": round(dur, 2), "vide": True}
    db = frame_db(x)
    mask, floor, loud, thr = speech_mask(db)
    t, f0 = pitch_track(x, mask, 220)
    speech = mask.sum() * HOP / SR
    talk_time = max(0.2, dur - expected_pauses)
    return {
        "duree": round(dur, 2),
        "vide": False,
        "cps": round(chars / talk_time, 2),             # caractères / seconde
        "niveau_db": round(float(np.median(db[mask])) if mask.any() else -60, 2),
        "hauteur": round(float(np.median(f0)), 1) if len(f0) else 0.0,
        "expressivite": round(semitone_std(f0), 2),
        "silence_max": round(longest_silence(mask), 2),
        "sature": float(np.mean(np.abs(x) > 0.985)) > 0.003,
        "ratio_parole": round(speech / dur, 2),
        "pauses": inner_gaps(mask),
    }


def inner_gaps(mask, min_s=0.12, max_s=2.0):
    """Durées des silences à l'intérieur de la parole (les vraies pauses de la voix)."""
    gaps, run, started = [], 0, False
    for m in mask:
        if m:
            if started and run:
                d = run * HOP / SR
                if min_s <= d <= max_s:
                    gaps.append(round(d, 3))
            started, run = True, 0
        elif started:
            run += 1
    return gaps


def natural_pauses(gap_lists):
    """Déduit les pauses de la voix (virgule, phrase) à partir de ce qu'elle a généré."""
    g = sorted(x for lst in gap_lists for x in (lst or []))
    if len(g) < 4:
        return {"virgule": 0.2, "phrase": 0.42}
    arr = np.array(g)
    phrase = float(np.percentile(arr, 75))
    virgule = float(np.percentile(arr, 30))
    return {"virgule": round(min(max(virgule, 0.12), 0.35), 3),
            "phrase": round(min(max(phrase, 0.25), 0.9), 3)}


def judge(metrics, ref, expected_pauses_max=0.0):
    """Compare un morceau aux valeurs de référence de la voix.

    ref: {"cps", "niveau_db", "hauteur", "expressivite"} (médianes)
    Renvoie (score 0-100, [problèmes])
    """
    if metrics.get("vide"):
        return 0, ["Audio vide ou coupé"]
    issues = []
    score = 100.0
    if ref.get("cps"):
        r = metrics["cps"] / ref["cps"]
        if r < 0.72:
            issues.append("Trop lent / traîne")
        elif r > 1.35:
            issues.append("Trop rapide / mots avalés")
        score -= abs(np.log(r)) * 90
    if ref.get("niveau_db") is not None and ref.get("niveau_db") != 0:
        d = metrics["niveau_db"] - ref["niveau_db"]
        if abs(d) > 5:
            issues.append("Volume différent du reste")
        score -= max(0, abs(d) - 2) * 4
    if ref.get("hauteur") and metrics.get("hauteur"):
        st = 12 * np.log2(metrics["hauteur"] / ref["hauteur"])
        if abs(st) > 2.8:
            issues.append("Ton de voix différent (plus aigu ou plus grave)")
        score -= max(0, abs(st) - 1.2) * 10
    if ref.get("expressivite") and metrics.get("expressivite") is not None:
        e = metrics["expressivite"] - ref["expressivite"]
        if e > 2.2:
            issues.append("Intonation exagérée / instable")
        elif e < -1.8:
            issues.append("Voix devenue plate")
        score -= max(0, abs(e) - 0.8) * 7
    allowed = max(1.4, expected_pauses_max + 0.6)
    if metrics["silence_max"] > allowed:
        issues.append("Blanc anormal au milieu")
        score -= (metrics["silence_max"] - allowed) * 15
    if metrics["sature"]:
        issues.append("Son saturé")
        score -= 15
    return int(max(0, min(100, score))), issues


def reference_from(metrics_list):
    good = [m for m in metrics_list if m and not m.get("vide")]
    if not good:
        return {}
    def med(k):
        vals = [m[k] for m in good if m.get(k)]
        return float(np.median(vals)) if vals else 0.0
    return {"cps": med("cps"), "niveau_db": med("niveau_db"),
            "hauteur": med("hauteur"), "expressivite": med("expressivite")}


# ---------------------------------------------------------------------------
# Assemblage final
# ---------------------------------------------------------------------------

def speech_level(y, sr):
    """Niveau de la parole seule (sans les silences), en dB."""
    hop = int(sr * 0.02)
    n = len(y) // hop
    if n < 3:
        return None
    rms = np.sqrt(np.mean(y[:n * hop].reshape(n, hop) ** 2, axis=1) + 1e-12)
    db = 20 * np.log10(rms + 1e-9)
    loud = db[db > db.max() - 30]
    return float(np.median(loud)) if len(loud) else None


TAIL = 0.12   # respiration gardée en fin de morceau (secondes)
HEAD = 0.05   # attaque gardée en début de morceau


def assemble(chunk_files, pauses, out_path, level_match=True, levels=None):
    """Assemble les morceaux en un seul MP3, le plus fluide possible.

    - garde la petite respiration naturelle de fin de chaque morceau ;
    - fondus doux à chaque jonction (pas de coupure sèche) ;
    - volume harmonisé sur la parole seule, avec de petites corrections seulement ;
    - un morceau à la fois en mémoire (plan gratuit de Render).
    """
    if levels is None or len(levels) != len(chunk_files):
        levels = []
        for f in chunk_files:
            y = decode(f, OUT_SR)
            levels.append(speech_level(y, OUT_SR) if len(y) else None)
            del y
    valid = [l for l in levels if l is not None]
    target = float(np.median(valid)) if valid else -20.0
    tmp_wav = out_path + ".wav"
    with wave.open(tmp_wav, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(OUT_SR)
        w.writeframes(np.zeros(int(0.15 * OUT_SR), dtype=np.int16).tobytes())
        last = len(chunk_files) - 1
        for k, (f, lvl, p) in enumerate(zip(chunk_files, levels, pauses)):
            if lvl is None:
                continue
            y = decode(f, OUT_SR)
            y = trim_soft(y, OUT_SR, HEAD, TAIL)
            diff = (target - lvl) if level_match else 0.0
            if abs(diff) <= 1.0:
                diff = 0.0
            # niveau final : parole autour de -18 dBFS (volume standard d'une voix off)
            gain = float(np.clip(diff, -4, 4)) + float(np.clip(-18.0 - target, -12, 12))
            y = y * (10 ** (gain / 20))
            peak = float(np.max(np.abs(y))) if len(y) else 0.0
            if peak > 0.97:                       # limiteur doux pour éviter la saturation
                y = np.tanh(y / peak * 1.4) / np.tanh(1.4) * 0.97
            fin = min(len(y) // 4, int(0.02 * OUT_SR))
            fout = min(len(y) // 4, int(0.06 * OUT_SR))
            if fin > 0:
                y[:fin] *= np.linspace(0, 1, fin, dtype=np.float32)
            if fout > 0:
                y[-fout:] *= np.linspace(1, 0, fout, dtype=np.float32) ** 2
            w.writeframes((np.clip(y, -1, 1) * 32767).astype(np.int16).tobytes())
            del y
            if k < last and p > 0:
                gap = max(0.06, p - TAIL)
                w.writeframes(np.zeros(int(gap * OUT_SR), dtype=np.int16).tobytes())
        w.writeframes(np.zeros(int(0.3 * OUT_SR), dtype=np.int16).tobytes())
    _run(["-i", tmp_wav, "-codec:a", "libmp3lame", "-b:a", "192k", "-y", out_path])
    os.remove(tmp_wav)
    return out_path


def trim_soft(y, sr, head=0.05, tail=0.12):
    """Retire les longs silences au début et à la fin, en gardant attaque et respiration."""
    hop = int(sr * 0.01)
    if len(y) < hop * 3:
        return y
    n = len(y) // hop
    rms = np.sqrt(np.mean(y[:n * hop].reshape(n, hop) ** 2, axis=1) + 1e-12)
    db = 20 * np.log10(rms + 1e-9)
    thr = max(db.max() - 45, -65)
    idx = np.where(db > thr)[0]
    if len(idx) == 0:
        return y[:0]
    a = max(0, idx[0] * hop - int(head * sr))
    b = min(len(y), (idx[-1] + 1) * hop + int(tail * sr))
    return y[a:b].copy()
