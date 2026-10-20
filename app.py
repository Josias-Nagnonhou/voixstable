"""VoixStable — voix off MiniMax constantes, sans gaspiller de crédits."""
import base64
import json
import logging
import os
import secrets
import re
import shutil
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor

from fastapi import FastAPI, File, Form, Header, HTTPException, Request, UploadFile
from fastapi.responses import (FileResponse, HTMLResponse, JSONResponse, RedirectResponse,
                               Response, StreamingResponse)
from fastapi.staticfiles import StaticFiles

import audio
import gdrive as G
import textprep
from demo import DemoClient
from minimax import Client, MiniMaxError

DATA = os.environ.get("VOIX_DATA", "/tmp/voixstable")
SAMPLES = os.path.join(DATA, "samples")
JOBS_DIR = os.path.join(DATA, "jobs")
for d in (SAMPLES, JOBS_DIR):
    os.makedirs(d, exist_ok=True)

PRICES = {"speech-2.8-hd": 100, "speech-2.8-turbo": 60, "speech-2.6-hd": 100,
          "speech-2.6-turbo": 60, "speech-02-hd": 100, "speech-02-turbo": 60}
FCFA_PER_USD = 600
ASR_USD_PER_HOUR = 0.38
ASR_LANG = {"French": "fr", "English": "en", "Arabic": "ar", "auto": ""}

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("voixstable")
APP_PASSWORD = os.environ.get("APP_PASSWORD", "")

app = FastAPI(title="VoixStable")
JOBS = {}
LOCK = threading.Lock()
PUBLIC_PATHS = {"/api/health", "/oauth/callback", "/favicon.ico"}

_PAGE = """<!doctype html><html lang="fr"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{t}</title><style>body{{font-family:system-ui,sans-serif;max-width:640px;margin:60px auto;padding:0 18px;line-height:1.6;color:#17142b;background:#f4f2fb}}
a{{color:#6d4aff;font-weight:600}}</style></head><body><h1>{t}</h1>{b}</body></html>"""


@app.middleware("http")
async def auth(request: Request, call_next):
    """Mot de passe du site (APP_PASSWORD). Le navigateur le demande une fois et s'en souvient."""
    if not APP_PASSWORD or request.url.path in PUBLIC_PATHS:
        return await call_next(request)
    h = request.headers.get("authorization", "")
    if h.startswith("Basic "):
        try:
            _, pwd = base64.b64decode(h[6:]).decode().split(":", 1)
            if secrets.compare_digest(pwd, APP_PASSWORD):
                return await call_next(request)
        except Exception:
            pass
    return Response("Mot de passe requis.", status_code=401,
                    headers={"WWW-Authenticate": 'Basic realm="VoixStable", charset="UTF-8"'})


G.load_token()


def client_for(key, base=None):
    if key and key.strip().lower() == "demo":
        return DemoClient()
    if not key:
        raise HTTPException(400, "Ajoute ta clé API MiniMax dans Réglages (ou tape « demo » pour essayer).")
    return Client(key, base or None)


def is_demo(key):
    return bool(key) and key.strip().lower() == "demo"


def jsonable(o):
    return json.loads(json.dumps(o, default=lambda v: float(v) if hasattr(v, "__float__") else str(v)))


def cost(chars, model, asr_seconds=0.0):
    usd = chars * PRICES.get(model, 100) / 1_000_000 + asr_seconds / 3600 * ASR_USD_PER_HOUR
    return {"usd": round(usd, 4), "fcfa": round(usd * FCFA_PER_USD)}


@app.exception_handler(MiniMaxError)
async def mm_error(request, exc):
    return JSONResponse({"detail": str(exc)}, status_code=502)


# ---------------------------------------------------------------------------
# Texte
# ---------------------------------------------------------------------------

def english_opt(b):
    """Réglage « lire l'anglais en anglais » envoyé par la page."""
    if b.get("english", True) is False:
        return None
    forced = [w.strip() for w in (b.get("english_words") or []) if str(w).strip()][:200]
    return {"forced": forced}


LANG_BOOST = {"en": "English", "mixte": "auto"}
ASR_FOR = {"en": "en", "mixte": ""}


@app.post("/api/prepare")
async def prepare(request: Request):
    b = await request.json()
    text = b.get("text", "")
    prepared = textprep.prepare(text, b.get("dictionary"), b.get("options"))
    chunks = textprep.make_chunks(prepared, int(b.get("max_chars", 1500)), b.get("pauses"),
                                  natural=b.get("pause_mode", "naturel") == "naturel", english=english_opt(b))
    chars = sum(c["chars"] for c in chunks)
    model = b.get("model", "speech-2.8-hd")
    return {
        "prepared": prepared,
        "changes": textprep.diff_preview(text, prepared),
        "chunks": chunks,
        "chars": chars,
        "cost": cost(chars, model),
        "duree_estimee_min": round(chars / 14.5 / 60, 1),
    }


# ---------------------------------------------------------------------------
# Échantillons et clonage
# ---------------------------------------------------------------------------

@app.post("/api/analyze")
async def analyze(file: UploadFile = File(...), use_best: str = Form("true"),
                  target: float = Form(90)):
    sid = uuid.uuid4().hex[:12]
    d = os.path.join(SAMPLES, sid)
    os.makedirs(d)
    ext = os.path.splitext(file.filename or "")[1].lower() or ".mp3"
    src = os.path.join(d, "original" + ext)
    size = 0
    with open(src, "wb") as f:
        while True:
            chunk = await file.read(1 << 20)
            if not chunk:
                break
            size += len(chunk)
            if size > 300 * (1 << 20):
                shutil.rmtree(d, ignore_errors=True)
                raise HTTPException(413, "Fichier trop lourd (300 Mo maximum).")
            f.write(chunk)
    try:
        info, _, _ = await run_blocking(audio.prepare_clone_files, src, d,
                                        use_best == "true", float(target))
    except Exception as e:
        shutil.rmtree(d, ignore_errors=True)
        raise HTTPException(400, f"Impossible de lire cet audio : {str(e)[:200]}")
    os.remove(src)
    info["sample_id"] = sid
    info["url_echantillon"] = f"/api/samples/{sid}/echantillon.mp3"
    info["url_extrait"] = f"/api/samples/{sid}/extrait.mp3"
    json.dump(jsonable(info), open(os.path.join(d, "info.json"), "w"))
    return jsonable(info)


@app.get("/api/samples/{sid}/{name}")
def sample_file(sid: str, name: str):
    if not re.fullmatch(r"[a-f0-9]{12}", sid) or name not in ("echantillon.mp3", "extrait.mp3"):
        raise HTTPException(404)
    p = os.path.join(SAMPLES, sid, name)
    if not os.path.exists(p):
        raise HTTPException(404, "Échantillon expiré, réimporte l'audio.")
    return FileResponse(p, media_type="audio/mpeg")


def slug_voice_id(name):
    base = re.sub(r"[^A-Za-z0-9]", "", textprep.unicodedata.normalize("NFKD", name)
                  .encode("ascii", "ignore").decode()) or "Voix"
    if not base[0].isalpha():
        base = "V" + base
    return (base[:20] + uuid.uuid4().hex[:6])


@app.post("/api/clone")
async def clone(request: Request, x_api_key: str = Header(""), x_api_base: str = Header("")):
    b = await request.json()
    sid = b.get("sample_id", "")
    d = os.path.join(SAMPLES, sid)
    if not re.fullmatch(r"[a-f0-9]{12}", sid) or not os.path.isdir(d):
        raise HTTPException(400, "Échantillon introuvable ou expiré, réimporte l'audio.")
    cl = client_for(x_api_key, x_api_base)
    name = (b.get("name") or "Ma voix").strip()
    voice_id = slug_voice_id(name)
    model = b.get("model", "speech-2.8-hd")
    prompt_text = (b.get("prompt_text") or "").strip()
    if prompt_text and not re.search(r"[.!?]$", prompt_text):
        prompt_text += "."

    def work():
        nonlocal prompt_text
        if not prompt_text and b.get("use_prompt", True):
            try:
                prompt_text, _ = cl.transcribe(os.path.join(d, "extrait.mp3"),
                                               ASR_LANG.get(b.get("language_boost", "French"), "fr"))
                prompt_text = re.sub(r"\s+", " ", prompt_text).strip()
                if prompt_text and not re.search(r"[.!?]$", prompt_text):
                    prompt_text += "."
            except MiniMaxError:
                prompt_text = ""
        fid = cl.upload(os.path.join(d, "echantillon.mp3"), "voice_clone")
        pid = None
        if prompt_text and b.get("use_prompt", True):
            pid = cl.upload(os.path.join(d, "extrait.mp3"), "prompt_audio")
        res = cl.clone(fid, voice_id, prompt_file_id=pid, prompt_text=prompt_text if pid else None,
                       preview_text=None, model=model,
                       noise_reduction=b.get("noise_reduction", False),
                       volume_norm=True, language_boost=b.get("language_boost", "French"))
        return res, pid is not None
    res, used_prompt = await run_blocking(work)
    # prompt_text a pu être rempli automatiquement
    info = json.load(open(os.path.join(d, "info.json")))
    return {"voice_id": voice_id, "name": name, "analyse": info, "extrait_utilise": used_prompt,
            "demo": is_demo(x_api_key)}


@app.post("/api/transcribe-sample")
async def transcribe_sample(request: Request, x_api_key: str = Header(""), x_api_base: str = Header("")):
    b = await request.json()
    sid = b.get("sample_id", "")
    p = os.path.join(SAMPLES, sid, "extrait.mp3")
    if not re.fullmatch(r"[a-f0-9]{12}", sid) or not os.path.exists(p):
        raise HTTPException(400, "Échantillon introuvable ou expiré, réimporte l'audio.")
    cl = client_for(x_api_key, x_api_base)
    text, dur = await run_blocking(cl.transcribe, p, ASR_LANG.get(b.get("language_boost", "French"), "fr"))
    text = re.sub(r"\s+", " ", text).strip()
    if text and not re.search(r"[.!?]$", text):
        text += "."
    return {"text": text, "cost": cost(0, "speech-2.8-hd", dur)}


VARIANTS = {
    "naturel": {"label": "Naturel (fidèle à l'échantillon)", "emotion": "auto", "speed": 1.0},
    "stable": {"label": "Stable (calme et régulier)", "emotion": "calm", "speed": 1.0},
    "pose": {"label": "Posé (un peu plus lent)", "emotion": "auto", "speed": 0.93},
}

TEST_TEXT = ("Bonjour et bienvenue. Aujourd'hui, je vais vous raconter quelque chose d'important. "
             "Écoutez bien, car ce que vous allez entendre peut changer votre façon de voir les choses !")


@app.post("/api/test-variants")
async def test_variants(request: Request, x_api_key: str = Header(""), x_api_base: str = Header("")):
    b = await request.json()
    cl = client_for(x_api_key, x_api_base)
    voice_id = b["voice_id"]
    model = b.get("model", "speech-2.8-hd")
    text = textprep.prepare(b.get("text") or TEST_TEXT, b.get("dictionary"))
    chunks = textprep.make_chunks(text, 2000)
    mm_text = chunks[0]["minimax_text"] if chunks else text
    tid = uuid.uuid4().hex[:12]
    d = os.path.join(SAMPLES, "t" + tid)
    os.makedirs(d)
    lb = b.get("language_boost", "French")

    def one(key):
        v = VARIANTS[key]
        data, extra = cl.tts(mm_text, voice_id, model=model, speed=v["speed"],
                             emotion=v["emotion"], language_boost=lb)
        p = os.path.join(d, key + ".mp3")
        open(p, "wb").write(data)
        pz = [float(x) for x in re.findall(r"<#([\d.]+)#>", mm_text)]
        m = audio.chunk_metrics(data, len(text), sum(pz))
        return key, m, extra

    def work():
        with ThreadPoolExecutor(3) as ex:
            return list(ex.map(one, VARIANTS.keys()))
    results = await run_blocking(work)
    out = []
    total_chars = 0
    for key, m, extra in results:
        total_chars += extra.get("usage_characters", len(text))
        out.append({"key": key, "label": VARIANTS[key]["label"], "emotion": VARIANTS[key]["emotion"],
                    "speed": VARIANTS[key]["speed"], "metrics": m,
                    "url": f"/api/tests/{tid}/{key}.mp3"})
    return jsonable({"variants": out, "cost": cost(total_chars, model)})


@app.get("/api/tests/{tid}/{name}")
def test_file(tid: str, name: str):
    if not re.fullmatch(r"[a-f0-9]{12}", tid) or not re.fullmatch(r"[a-z]+\.mp3", name):
        raise HTTPException(404)
    p = os.path.join(SAMPLES, "t" + tid, name)
    if not os.path.exists(p):
        raise HTTPException(404)
    return FileResponse(p, media_type="audio/mpeg")


@app.post("/api/speak")
async def speak(request: Request, x_api_key: str = Header(""), x_api_base: str = Header("")):
    """Petit test d'écoute (réglages d'une voix, mot du dictionnaire)."""
    b = await request.json()
    cl = client_for(x_api_key, x_api_base)
    v = b.get("voice") or {}
    if not v.get("voice_id"):
        raise HTTPException(400, "Choisis d'abord une voix.")
    text = textprep.prepare((b.get("text") or "")[:600], b.get("dictionary"))
    if not text:
        raise HTTPException(400, "Texte vide.")
    chunks = textprep.make_chunks(text, 2000, b.get("pauses"), natural=b.get("pause_mode", "naturel") == "naturel",
                                  english=english_opt(b))
    mm = " ".join(c["minimax_text"] for c in chunks)
    langs = {c.get("lang", "fr") for c in chunks}
    boost = v.get("language_boost", "French")
    if langs == {"en"}:
        boost = "English"
    elif langs & {"en", "mixte"}:
        boost = "auto"
    model = v.get("model", "speech-2.8-hd")
    data, extra = await run_blocking(cl.tts, mm, v["voice_id"], model=model,
                                     speed=v.get("speed", 1.0), vol=v.get("vol", 1.0),
                                     pitch=v.get("pitch", 0), emotion=v.get("emotion", "auto"),
                                     language_boost=boost)
    tid = uuid.uuid4().hex[:12]
    d = os.path.join(SAMPLES, "t" + tid)
    os.makedirs(d)
    open(os.path.join(d, "test.mp3"), "wb").write(data)
    return {"url": f"/api/tests/{tid}/test.mp3", "texte_lu": text,
            "cost": cost(int(extra.get("usage_characters") or len(text)), model)}


@app.get("/api/voices")
async def voices(x_api_key: str = Header(""), x_api_base: str = Header("")):
    cl = client_for(x_api_key, x_api_base)
    v = await run_blocking(cl.voices)
    return {"voices": v}


@app.post("/api/check-key")
async def check_key(x_api_key: str = Header(""), x_api_base: str = Header("")):
    if is_demo(x_api_key):
        return {"ok": True, "demo": True, "message": "Mode démo activé : sons de test, aucun crédit dépensé."}
    cl = client_for(x_api_key, x_api_base)
    v = await run_blocking(cl.voices)
    return {"ok": True, "demo": False, "message": f"Clé valide. {len(v)} voix clonée(s) trouvée(s) sur ton compte."}


@app.post("/api/voices/delete")
async def delete_voice(request: Request, x_api_key: str = Header(""), x_api_base: str = Header("")):
    b = await request.json()
    cl = client_for(x_api_key, x_api_base)
    await run_blocking(cl.delete_voice, b["voice_id"])
    return {"ok": True}


# ---------------------------------------------------------------------------
# Génération
# ---------------------------------------------------------------------------

class Job:
    def __init__(self, jid, cl, b, chunks):
        self.id = jid
        self.cl = cl
        self.dir = os.path.join(JOBS_DIR, jid)
        os.makedirs(self.dir, exist_ok=True)
        self.voice = b["voice"]           # voice_id, emotion, speed, vol, pitch, model, language_boost
        self.ref = b.get("ref") or {}
        self.threshold = int(b.get("threshold", 62))
        self.max_retries = int(b.get("max_retries", 2))
        self.workers = max(1, min(4, int(b.get("workers", 3))))
        self.normalization = b.get("normalization")
        self.model = self.voice.get("model", "speech-2.8-hd")
        self.chunks = [dict(c, status="attente", attempts=[], best=None, score=None,
                            issues=[], locked=False) for c in chunks]
        self.status = "en cours"
        self.phase = "Génération des morceaux"
        self.chars_billed = 0
        self.error = None
        self.cancel = False
        self.final = None
        self.created = time.time()
        self.lock = threading.Lock()
        self.job_ref = {}
        self.asr = bool(b.get("asr", True))
        self.asr_lang = ASR_LANG.get(self.voice.get("language_boost", "French"), "fr")
        self.asr_seconds = 0.0
        self.asr_note = None
        self.dictionary = b.get("dictionary") or []
        self.english = english_opt(b)
        self.review = bool(b.get("review", False))
        self.pause_mode = b.get("pause_mode", "naturel")
        self.title = (b.get("title") or "voix-off").strip()[:100]
        self.voice_name = (b.get("voice_name") or "").strip()[:80]
        self.downloaded = False
        self.drive_id = None
        self.drive_status = None
        self.natural = None

    def listen(self, c, a):
        """Réécoute : transcrit le morceau et vérifie qu'aucun mot ne manque."""
        if not self.asr or a["metrics"].get("vide"):
            return
        try:
            heard, dur = self.cl.transcribe(a["file"], ASR_FOR.get(c.get("lang"), self.asr_lang))
        except MiniMaxError as e:
            self.asr = False
            self.asr_note = f"Réécoute automatique désactivée pour cette génération : {e}"
            return
        except Exception:
            return
        with self.lock:
            self.asr_seconds += dur
        res = textprep.heard_coverage(c["text"], heard, self.dictionary)
        res["heard"] = heard
        a["asr"] = res

    # -- un essai ----------------------------------------------------------
    def attempt(self, i, text_override=None):
        c = self.chunks[i]
        if text_override is not None:
            c["text"] = text_override
            c["minimax_text"] = text_override
            c["chars"] = len(text_override)
        v = self.voice
        data, extra = self.cl.tts(c["minimax_text"], v["voice_id"], model=self.model,
                                  speed=v.get("speed", 1.0), vol=v.get("vol", 1.0),
                                  pitch=v.get("pitch", 0), emotion=v.get("emotion", "auto"),
                                  language_boost=LANG_BOOST.get(c.get("lang"), v.get("language_boost", "French")),
                                  normalization=self.normalization)
        n = len(c["attempts"]) + 1
        path = os.path.join(self.dir, f"m{i:04d}_{n}.mp3")
        with open(path, "wb") as f:
            f.write(data)
        pauses = [float(x) for x in re.findall(r"<#([\d.]+)#>", c["minimax_text"])]
        m = audio.chunk_metrics(data, c["chars"], sum(pauses))
        with self.lock:
            self.chars_billed += int(extra.get("usage_characters") or c["chars"])
            a = {"n": n, "file": path, "metrics": m, "score": None, "issues": []}
            c["attempts"].append(a)
        self.listen(c, a)
        return n, max(pauses) if pauses else 0.0

    def score_attempt(self, i, a, max_pause):
        ref = self.reference()
        s, issues = audio.judge(a["metrics"], ref, max_pause)
        h = a.get("asr")
        if h:
            cov = h["coverage"]
            m = len(h["missing"])
            pen = max((0.97 - cov) * 420 if cov < 0.97 else 0, 12 if m == 1 else 20 * m)
            s = int(max(0, s - pen))
            if m >= 2 or cov < 0.92:
                miss = ", ".join(h["missing"][:6])
                issues = issues + [f"Mots sautés ou mal lus ({round(cov * 100)} % entendus)" + (f" : {miss}" if miss else "")]
        a["score"], a["issues"] = s, issues
        return s

    def reference(self):
        r = dict(self.job_ref)
        # la hauteur de la voix enregistrée (profil) prime : elle évite qu'une
        # dérive générale du job passe inaperçue
        for k in ("hauteur", "cps"):
            if self.ref.get(k):
                if k == "cps" and r.get("cps"):
                    r[k] = 0.5 * r[k] + 0.5 * self.ref[k]
                else:
                    r[k] = self.ref[k]
        if not r.get("expressivite") and self.ref.get("expressivite"):
            r["expressivite"] = self.ref["expressivite"]
        return r

    def pick_best(self, i):
        c = self.chunks[i]
        scored = [a for a in c["attempts"] if a["score"] is not None]
        if not scored:
            return
        best = max(scored, key=lambda a: a["score"])
        if not c.get("locked"):
            c["best"] = best["n"]
        chosen = next((a for a in c["attempts"] if a["n"] == c["best"]), best)
        c["score"], c["issues"] = chosen["score"], chosen["issues"]
        c["status"] = "ok" if chosen["score"] >= self.threshold else "a_verifier"

    def max_pause(self, i):
        p = [float(x) for x in re.findall(r"<#([\d.]+)#>", self.chunks[i]["minimax_text"])]
        return max(p) if p else 0.0

    # -- déroulement -------------------------------------------------------
    def run(self):
        audio.background_priority()
        try:
            n = len(self.chunks)

            def first(i):
                audio.background_priority()
                if self.cancel:
                    return
                self.chunks[i]["status"] = "generation"
                try:
                    self.attempt(i)
                    self.chunks[i]["status"] = "genere"
                    self.chunks[i]["best"] = self.chunks[i]["attempts"][-1]["n"]
                except MiniMaxError as e:
                    self.chunks[i]["status"] = "erreur"
                    self.chunks[i]["issues"] = [str(e)]
                    if e.code in (1004, 1008, 2013):
                        self.cancel = True
                        self.error = str(e)

            with ThreadPoolExecutor(self.workers) as ex:
                list(ex.map(first, range(n)))
            if self.error:
                raise MiniMaxError(0, self.error)

            # référence = médianes du job (robuste aux quelques ratés)
            self.phase = "Contrôle qualité"
            self.job_ref = audio.reference_from(
                [c["attempts"][0]["metrics"] for c in self.chunks if c["attempts"]])
            for i, c in enumerate(self.chunks):
                for a in c["attempts"]:
                    self.score_attempt(i, a, self.max_pause(i))
                self.pick_best(i)

            # régénération automatique des morceaux douteux
            for round_ in range(self.max_retries):
                bad = [i for i, c in enumerate(self.chunks)
                       if c["attempts"] and c["score"] is not None and c["score"] < self.threshold]
                if not bad or self.cancel:
                    break
                self.phase = f"Correction automatique ({len(bad)} morceau(x), passage {round_ + 1})"

                def redo(i):
                    audio.background_priority()
                    if self.cancel:
                        return
                    self.chunks[i]["status"] = "correction"
                    try:
                        nn, mp = self.attempt(i)
                        a = self.chunks[i]["attempts"][-1]
                        self.score_attempt(i, a, self.max_pause(i))
                    except MiniMaxError as e:
                        self.chunks[i]["issues"] = [str(e)]
                    self.pick_best(i)
                with ThreadPoolExecutor(self.workers) as ex:
                    list(ex.map(redo, bad))

            if self.cancel:
                self.status = "annule"
                self.phase = "Annulé"
                return
            if self.review:
                self.status = "a_valider"
                self.phase = "Toutes les parties sont prêtes : écoute-les, refais celles qui ne vont pas, puis assemble"
                return
            self.phase = "Assemblage final"
            self.assemble()
            self.status = "termine"
            self.phase = "Terminé"
        except Exception as e:
            self.status = "erreur"
            self.error = self.error or str(e)
            self.phase = "Erreur"

    def assemble(self):
        files, pauses, chosen = [], [], []
        for c in self.chunks:
            a = next((a for a in c["attempts"] if a["n"] == c["best"]), None)
            if a:
                files.append(a["file"])
                chosen.append((c, a))
        if self.pause_mode == "naturel":
            # pauses mesurées dans la voix elle-même
            nat = audio.natural_pauses([a["metrics"].get("pauses") for _, a in chosen])
            self.natural = nat
            last = len(chosen) - 1
            for k, (c, a) in enumerate(chosen):
                if k == last:
                    pauses.append(0.0)
                    continue
                end = c.get("end", ".")
                if c.get("para_end"):
                    p = min(max(nat["phrase"] * 1.7, 0.55), 1.3)
                elif end == ",":
                    p = nat["virgule"]
                elif end == "...":
                    p = nat["phrase"] * 1.3
                elif end in ("?", "!"):
                    p = nat["phrase"] * 1.1
                else:
                    p = nat["phrase"]
                pauses.append(round(p, 3))
        else:
            pauses = [c["pause_after"] for c, _ in chosen]
        if not files:
            raise RuntimeError("Aucun morceau généré.")
        out = os.path.join(self.dir, f"voix_finale_{int(time.time())}.mp3")
        audio.assemble(files, pauses, out)
        if self.final and os.path.exists(self.final):
            os.remove(self.final)
        self.final = out
        self.send_to_drive()

    def send_to_drive(self):
        if not G.connected() or not self.final:
            return
        self.drive_status = "envoi"
        path, chars = self.final, sum(c["chars"] for c in self.chunks)

        def work():
            try:
                props = {"voix": self.voice_name, "job": self.id, "caracteres": chars,
                         "duree": int(os.path.getsize(path) * 8 / 192000),
                         "telechargee": "1" if self.downloaded else "0"}
                self.drive_id = G.upload(path, safe_name(self.title) + ".mp3", props, self.drive_id)
                self.drive_status = "ok"
            except Exception as e:
                log.warning("Envoi Drive échoué : %s", e)
                self.drive_status = "erreur"
        threading.Thread(target=work, daemon=True).start()

    def public(self):
        total = len(self.chunks)
        done = sum(1 for c in self.chunks if c["status"] in ("ok", "a_verifier", "genere", "erreur"))
        chunks = []
        for c in self.chunks:
            chunks.append({
                "index": c["index"], "text": c["text"], "chars": c["chars"], "pause_after": c.get("pause_after", 0.4),
                "status": c["status"], "score": c["score"], "issues": c["issues"],
                "best": c["best"], "locked": c.get("locked", False),
                "attempts": [{"n": a["n"], "score": a["score"], "issues": a["issues"],
                              "duree": (a.get("metrics") or {}).get("duree"),
                              "url": f"/api/jobs/{self.id}/chunk/{c['index']}/{a['n']}.mp3"}
                             for a in c["attempts"]],
            })
        return jsonable({
            "id": self.id, "status": self.status, "phase": self.phase, "error": self.error,
            "progress": round(100 * done / max(1, total)),
            "total": total,
            "ok": sum(1 for c in self.chunks if c["status"] == "ok"),
            "a_verifier": sum(1 for c in self.chunks if c["status"] == "a_verifier"),
            "regenerations": sum(max(0, len(c["attempts"]) - 1) for c in self.chunks),
            "cost": cost(self.chars_billed, self.model, self.asr_seconds),
            "asr": self.asr, "asr_note": self.asr_note, "pauses_voix": self.natural,
            "drive_id": self.drive_id, "drive_status": self.drive_status, "downloaded": self.downloaded,
            "final": f"/api/jobs/{self.id}/final.mp3" if self.final else None,
            "chunks": chunks,
        })


async def run_blocking(fn, *a, **k):
    import asyncio
    return await asyncio.get_running_loop().run_in_executor(None, lambda: fn(*a, **k))


@app.post("/api/jobs")
async def create_job(request: Request, x_api_key: str = Header(""), x_api_base: str = Header("")):
    b = await request.json()
    cl = client_for(x_api_key, x_api_base)
    chunks = b.get("chunks")
    if not chunks:
        prepared = textprep.prepare(b.get("text", ""), b.get("dictionary"), b.get("options"))
        chunks = textprep.make_chunks(prepared, int(b.get("max_chars", 1500)), b.get("pauses"),
                                      natural=b.get("pause_mode", "naturel") == "naturel", english=english_opt(b))
    if not chunks:
        raise HTTPException(400, "Le texte est vide.")
    if not b.get("voice", {}).get("voice_id"):
        raise HTTPException(400, "Choisis une voix.")
    jid = uuid.uuid4().hex[:12]
    job = Job(jid, cl, b, chunks)
    with LOCK:
        JOBS[jid] = job
    threading.Thread(target=job.run, daemon=True).start()
    return {"id": jid}


def get_job(jid):
    j = JOBS.get(jid)
    if not j:
        raise HTTPException(404, "Génération introuvable (le serveur a peut-être redémarré).")
    return j


@app.get("/api/jobs/{jid}")
def job_status(jid: str):
    return get_job(jid).public()


@app.post("/api/jobs/{jid}/cancel")
def job_cancel(jid: str):
    get_job(jid).cancel = True
    return {"ok": True}


@app.post("/api/jobs/{jid}/chunks/{i}/regen")
async def chunk_regen(jid: str, i: int, request: Request, x_api_key: str = Header(""),
                      x_api_base: str = Header("")):
    j = get_job(jid)
    b = await request.json()
    if i < 0 or i >= len(j.chunks):
        raise HTTPException(404)
    j.cl = client_for(x_api_key, x_api_base)
    text = b.get("text")
    if text is not None:
        text = textprep.prepare(text, b.get("dictionary"))
        mm = textprep.make_chunks(text, 10000, natural=j.pause_mode == "naturel", english=j.english)
        text_mm = " ".join(c["minimax_text"] for c in mm) if mm else text
        ls = {x.get("lang", "fr") for x in mm}
        new_lang = "en" if ls == {"en"} else ("mixte" if ls & {"en", "mixte"} else "fr")
    c = j.chunks[i]
    c["locked"] = False
    c["status"] = "correction"

    def work():
        if text is not None:
            c["text"], c["minimax_text"], c["chars"] = text, text_mm, len(text)
            c["lang"] = new_lang
        j.attempt(i)
        j.score_attempt(i, c["attempts"][-1], j.max_pause(i))
        # un regen manuel : on prend le nouvel essai d'office
        c["best"] = c["attempts"][-1]["n"]
        c["locked"] = True
        j.pick_best(i)
    try:
        await run_blocking(work)
    except MiniMaxError:
        c["status"] = "erreur"
        raise
    return j.public()


@app.post("/api/jobs/{jid}/chunks/{i}/choose")
async def chunk_choose(jid: str, i: int, request: Request):
    j = get_job(jid)
    b = await request.json()
    c = j.chunks[i]
    n = int(b["n"])
    if not any(a["n"] == n for a in c["attempts"]):
        raise HTTPException(404)
    c["best"], c["locked"] = n, True
    j.pick_best(i)
    return j.public()


@app.post("/api/jobs/{jid}/assemble")
async def job_assemble(jid: str):
    j = get_job(jid)
    await run_blocking(j.assemble)
    if j.status in ("annule", "a_valider"):
        j.status = "termine"
        j.phase = "Terminé"
    return j.public()


@app.get("/api/jobs/{jid}/chunk/{i}/{n}.mp3")
def chunk_file(jid: str, i: int, n: int):
    j = get_job(jid)
    c = j.chunks[i]
    a = next((a for a in c["attempts"] if a["n"] == n), None)
    if not a:
        raise HTTPException(404)
    return FileResponse(a["file"], media_type="audio/mpeg")


@app.get("/api/jobs/{jid}/final.mp3")
def final_file(jid: str, name: str = "", dl: int = 0):
    j = get_job(jid)
    if not j.final:
        raise HTTPException(404)
    if dl:
        j.downloaded = True
        if j.drive_id and G.connected():
            threading.Thread(target=lambda: _quiet(G.set_props, j.drive_id, {"telechargee": "1"}),
                             daemon=True).start()
    return FileResponse(j.final, media_type="audio/mpeg", filename=f"{safe_name(name or j.title)}.mp3")


def safe_name(name):
    safe = re.sub(r'[\\/:*?"<>|\x00-\x1f]+', " ", name or "")
    return re.sub(r"\s+", " ", safe).strip(" .")[:80] or "voix-off"


def _quiet(fn, *a):
    try:
        fn(*a)
    except Exception as e:
        log.warning("Drive : %s", e)


# ---------------------------------------------------------------------------
# Google Drive : connexion et historique permanent
# ---------------------------------------------------------------------------

def need_drive():
    if not G.connected():
        raise HTTPException(400, "Google Drive n'est pas connecté.")


@app.get("/oauth/start")
def oauth_start():
    if not G.configured():
        raise HTTPException(400, "Google Drive n'est pas configuré sur le serveur.")
    return RedirectResponse(G.auth_url())


@app.get("/oauth/callback", response_class=HTMLResponse)
def oauth_callback(code: str = "", state: str = "", error: str = ""):
    back = "<p><a href='/#historique'>Retour à VoixStable</a></p>"
    if error or not code:
        return _PAGE.format(t="Connexion annulée", b=f"<p>Google a répondu : {error or 'aucun code'}.</p>" + back)
    try:
        refresh = G.exchange(code, state)
        G.folder_id()
    except Exception as e:
        return _PAGE.format(t="Connexion impossible", b=f"<p>{e}</p>" + back)
    log.warning("GDRIVE_TOKEN_ENC_TO_SAVE=%s", G.encrypt(refresh))
    for j in list(JOBS.values()):
        if j.final and not j.drive_id:
            j.send_to_drive()
    return _PAGE.format(t="Google Drive connecté", b="<p>Tes voix off terminées seront rangées "
                        f"automatiquement dans le dossier « {G.FOLDER_NAME} » de ton Drive.</p>" + back)


def _drive_item(f):
    p = f.get("appProperties") or {}
    name = f.get("name", "")
    return {"id": f["id"], "title": name[:-4] if name.lower().endswith(".mp3") else name,
            "size": int(f.get("size", 0) or 0), "created": f.get("createdTime"),
            "voice": p.get("voix", ""), "job": p.get("job", ""), "duration": int(p.get("duree", 0) or 0),
            "downloaded": p.get("telechargee") == "1"}


@app.get("/api/drive/status")
async def drive_status():
    out = {"configured": G.configured(), "connected": G.connected()}
    if G.connected():
        try:
            files = await run_blocking(G.list_files)
            out["folder_bytes"] = sum(int(f.get("size", 0) or 0) for f in files)
            out["count"] = len(files)
            out["settings"] = await run_blocking(G.get_settings)
            out["storage"] = await run_blocking(G.storage)
        except Exception as e:
            out["error"] = str(e)
    return out


@app.get("/api/drive/files")
async def drive_files():
    need_drive()
    try:
        files = await run_blocking(G.list_files)
    except Exception as e:
        raise HTTPException(502, str(e))
    return {"files": [_drive_item(f) for f in files]}


def _stream(fid, request, filename=None):
    need_drive()
    try:
        r = G.open_media(fid, request.headers.get("range"))
    except Exception as e:
        raise HTTPException(502, str(e))
    headers = {k: r.headers[k] for k in ("Content-Length", "Content-Range", "Accept-Ranges") if k in r.headers}
    headers.setdefault("Accept-Ranges", "bytes")
    if filename:
        from urllib.parse import quote
        headers["Content-Disposition"] = f"attachment; filename*=UTF-8''{quote(filename)}"
    return StreamingResponse(r.iter_content(64 * 1024), status_code=r.status_code,
                             media_type="audio/mpeg", headers=headers)


@app.get("/api/drive/files/{fid}/audio")
def drive_audio(fid: str, request: Request):
    return _stream(fid, request)


@app.get("/api/drive/files/{fid}/download")
def drive_download(fid: str, request: Request, name: str = "voix-off"):
    threading.Thread(target=lambda: _quiet(G.set_props, fid, {"telechargee": "1"}), daemon=True).start()
    for j in JOBS.values():
        if j.drive_id == fid:
            j.downloaded = True
    return _stream(fid, request, safe_name(name) + ".mp3")


@app.post("/api/drive/files/{fid}/downloaded")
async def drive_mark(fid: str):
    need_drive()
    await run_blocking(G.set_props, fid, {"telechargee": "1"})
    return {"ok": True}


@app.post("/api/drive/files/{fid}/delete")
async def drive_delete(fid: str):
    need_drive()
    await run_blocking(G.trash, fid)
    for j in JOBS.values():
        if j.drive_id == fid:
            j.drive_id = None
    return {"ok": True}


@app.post("/api/drive/settings")
async def drive_settings(request: Request):
    need_drive()
    b = await request.json()
    days = max(0, min(365, int(b.get("cleanup_days", 0))))
    await run_blocking(G.set_settings, days)
    n = await run_blocking(G.cleanup, days) if days else 0
    return {"ok": True, "cleanup_days": days, "removed": n}


@app.get("/api/health")
def health():
    return {"ok": True, "jobs": len(JOBS)}


# ---------------------------------------------------------------------------
# Nettoyage automatique (fichiers de plus de 24 h)
# ---------------------------------------------------------------------------

def janitor():
    while True:
        time.sleep(1800)
        limit = time.time() - 24 * 3600
        for base in (SAMPLES, JOBS_DIR):
            for name in os.listdir(base):
                p = os.path.join(base, name)
                try:
                    if os.path.getmtime(p) < limit:
                        shutil.rmtree(p, ignore_errors=True)
                except OSError:
                    pass
        with LOCK:
            for jid in [k for k, j in JOBS.items() if j.created < limit]:
                JOBS.pop(jid, None)
        # nettoyage automatique du Drive (une fois par jour environ)
        if G.connected() and time.time() - janitor.last_drive > 20 * 3600:
            janitor.last_drive = time.time()
            try:
                n = G.cleanup(G.get_settings()["cleanup_days"])
                if n:
                    log.info("Drive : %d ancienne(s) voix off mise(s) à la corbeille.", n)
            except Exception as e:
                log.warning("Nettoyage Drive : %s", e)


janitor.last_drive = 0


threading.Thread(target=janitor, daemon=True).start()

app.mount("/", StaticFiles(directory=os.path.join(os.path.dirname(__file__), "static"), html=True),
          name="static")
