"""
Récupère l'audio d'une vidéo en ligne (YouTube, TikTok, Facebook, Instagram, X…) à partir
de son lien, pour cloner une voix sans avoir à télécharger le fichier soi-même.

Pour ménager le serveur, on ne télécharge qu'une fenêtre de la vidéo (12 minutes au plus,
à partir du moment choisi) : l'analyse choisit ensuite le meilleur passage dedans.
"""
import glob
import os
import re

import audio

WINDOW = 12 * 60          # secondes téléchargées au maximum
MAX_VIDEO = 6 * 3600      # vidéos de plus de 6 h refusées


def parse_time(s):
    """« 2:10 », « 1:02:30 », « 130 » -> secondes."""
    s = (s or "").strip()
    if not s:
        return 0.0
    if not re.fullmatch(r"\d+(?::\d{1,2}){0,2}(?:\.\d+)?", s):
        raise ValueError("Moment de départ illisible : écris par exemple 2:10 ou 1:05:00.")
    v = 0.0
    for p in s.split(":"):
        v = v * 60 + float(p)
    return v


def friendly(err):
    e = re.sub(r"\x1b\[[0-9;]*m", "", str(err))
    e = re.split(r";\s*please report this issue", e)[0]
    low = e.lower()
    if "sign in to confirm" in low or "not a bot" in low:
        return ("YouTube a bloqué le téléchargement depuis le serveur (protection anti-robot). "
                "Réessaie dans quelques minutes, ou télécharge l'audio et importe-le comme fichier.")
    if "private" in low:
        return "Cette vidéo est privée : l'outil ne peut pas y accéder."
    if "unsupported url" in low:
        return "Ce lien n'est pas reconnu. Colle le lien complet de la vidéo (YouTube, TikTok, Facebook…)."
    if "unable to connect to proxy" in low or "timed out" in low or "connection" in low:
        return "Le serveur n'arrive pas à joindre ce site pour l'instant. Réessaie dans un moment."
    if re.search(r"age[- ]restricted|confirm your age|inappropriate for some users", low):
        return "Cette vidéo est réservée aux adultes connectés : l'outil ne peut pas la récupérer."
    if "unavailable" in low or "removed" in low:
        return "Cette vidéo n'est plus disponible."
    if "live" in low and "not" not in low:
        return "C'est un direct en cours : attends qu'il soit terminé et réessaie."
    return "Impossible de récupérer l'audio de ce lien : " + e.replace("ERROR: ", "")[-220:]


def fetch_audio(url, workdir, start=0.0):
    """Télécharge la fenêtre audio et renvoie (chemin, infos)."""
    import yt_dlp
    from yt_dlp.utils import download_range_func

    url = (url or "").strip()
    if not re.match(r"https?://", url):
        raise ValueError("Colle un lien qui commence par https://")
    base = {"quiet": True, "no_warnings": True, "noprogress": True, "noplaylist": True,
            "ffmpeg_location": audio.FFMPEG, "socket_timeout": 30, "retries": 3}
    try:
        with yt_dlp.YoutubeDL(base) as y:
            meta = y.extract_info(url, download=False)
    except Exception as e:
        raise ValueError(friendly(e))
    if meta.get("_type") == "playlist":
        raise ValueError("C'est une playlist : colle le lien d'une seule vidéo.")
    if meta.get("is_live"):
        raise ValueError("C'est un direct en cours : attends qu'il soit terminé et réessaie.")
    dur = float(meta.get("duration") or 0)
    if dur > MAX_VIDEO:
        raise ValueError("Vidéo trop longue (plus de 6 heures).")
    if dur and start >= dur - 20:
        raise ValueError("Le moment de départ est après la fin de la vidéo.")

    opts = dict(base, format="bestaudio/best", outtmpl=os.path.join(workdir, "source.%(ext)s"))
    cut = dur == 0 or dur > WINDOW or start > 0
    if cut:
        end = start + WINDOW if not dur else min(dur, start + WINDOW)
        opts["download_ranges"] = download_range_func(None, [(start, end)])
        opts["force_keyframes_at_cuts"] = False
    try:
        with yt_dlp.YoutubeDL(opts) as y:
            y.download([url])
    except Exception as e:
        raise ValueError(friendly(e))
    files = [f for f in glob.glob(os.path.join(workdir, "source.*")) if not f.endswith(".part")]
    if not files:
        raise ValueError("Le téléchargement n'a rien donné. Réessaie, ou importe le fichier.")
    return files[0], {
        "titre": (meta.get("title") or "")[:120],
        "auteur": (meta.get("uploader") or meta.get("channel") or "")[:80],
        "site": meta.get("extractor_key") or "",
        "duree_video": dur,
        "debut": start,
        "fenetre": min(WINDOW, dur - start) if dur else WINDOW,
    }
