"""
Google Drive pour VoixStable : connexion (OAuth, autorisation « drive.file » : l'outil ne voit
que les fichiers qu'il a lui-même créés), envoi des voix off terminées dans le dossier
« VoixStable », historique permanent, et nettoyage automatique.

La clé de connexion (refresh token) est chiffrée puis rangée dans la variable
d'environnement GDRIVE_TOKEN_ENC du serveur, pour survivre aux redémarrages.
"""

import base64
import hashlib
import json
import logging
import os
import secrets
import threading
import time
from datetime import datetime, timedelta, timezone

import requests
from cryptography.fernet import Fernet, InvalidToken

log = logging.getLogger("voixstable.drive")

CLIENT_ID = os.environ.get("GOOGLE_CLIENT_ID", "")
CLIENT_SECRET = os.environ.get("GOOGLE_CLIENT_SECRET", "")
BASE_URL = os.environ.get("RENDER_EXTERNAL_URL", "http://localhost:8000").rstrip("/")
REDIRECT_URI = BASE_URL + "/oauth/callback"
SCOPE = "https://www.googleapis.com/auth/drive.file"
FOLDER_NAME = "VoixStable"
API = "https://www.googleapis.com/drive/v3"
UPLOAD = "https://www.googleapis.com/upload/drive/v3"

_fernet = Fernet(base64.urlsafe_b64encode(
    hashlib.sha256((CLIENT_SECRET + "|voixstable|" + os.environ.get("APP_PASSWORD", "")).encode()).digest()))
_lock = threading.Lock()
_tok = {"refresh": None, "access": None, "exp": 0}
_ids = {"folder": None}
_states = set()


def configured():
    return bool(CLIENT_ID and CLIENT_SECRET)


def connected():
    return bool(_tok["refresh"])


def encrypt(tok):
    return _fernet.encrypt(tok.encode()).decode()


def load_token():
    enc = os.environ.get("GDRIVE_TOKEN_ENC", "")
    if enc:
        try:
            _tok["refresh"] = _fernet.decrypt(enc.encode()).decode()
            log.info("Google Drive : connexion chargée.")
        except InvalidToken:
            log.warning("GDRIVE_TOKEN_ENC illisible (code secret ou mot de passe changé ?)")


# ---------------------------------------------------------------- OAuth
def auth_url():
    st = secrets.token_urlsafe(24)
    _states.add(st)
    from urllib.parse import urlencode
    q = urlencode({"client_id": CLIENT_ID, "redirect_uri": REDIRECT_URI, "response_type": "code",
                   "scope": SCOPE, "access_type": "offline", "prompt": "consent", "state": st})
    return "https://accounts.google.com/o/oauth2/v2/auth?" + q


def exchange(code, state):
    if state not in _states:
        raise ValueError("Lien de connexion expiré, recommence depuis l'onglet Historique.")
    _states.discard(state)
    r = requests.post("https://oauth2.googleapis.com/token", data={
        "code": code, "client_id": CLIENT_ID, "client_secret": CLIENT_SECRET,
        "redirect_uri": REDIRECT_URI, "grant_type": "authorization_code"}, timeout=30)
    j = r.json()
    if "refresh_token" not in j:
        raise ValueError(f"Google n'a pas donné de clé : {j.get('error_description') or j}")
    _tok.update(refresh=j["refresh_token"], access=j.get("access_token"),
                exp=time.time() + j.get("expires_in", 3000) - 60)
    _ids["folder"] = None
    return j["refresh_token"]


def disconnect():
    _tok.update(refresh=None, access=None, exp=0)
    _ids["folder"] = None


def _access():
    with _lock:
        if _tok["access"] and time.time() < _tok["exp"]:
            return _tok["access"]
        if not _tok["refresh"]:
            raise RuntimeError("Google Drive n'est pas connecté.")
        r = requests.post("https://oauth2.googleapis.com/token", data={
            "client_id": CLIENT_ID, "client_secret": CLIENT_SECRET,
            "refresh_token": _tok["refresh"], "grant_type": "refresh_token"}, timeout=30)
        j = r.json()
        if "access_token" not in j:
            raise RuntimeError(f"Connexion Google refusée : {j.get('error_description') or j.get('error')}")
        _tok.update(access=j["access_token"], exp=time.time() + j.get("expires_in", 3000) - 60)
        return _tok["access"]


def _h(extra=None):
    h = {"Authorization": f"Bearer {_access()}"}
    if extra:
        h.update(extra)
    return h


def _check(r):
    if r.status_code >= 400:
        try:
            msg = r.json().get("error", {}).get("message", r.text[:200])
        except Exception:
            msg = r.text[:200]
        raise RuntimeError(f"Google Drive ({r.status_code}) : {msg}")
    return r


# ---------------------------------------------------------------- Dossier
def folder_id():
    if _ids["folder"]:
        return _ids["folder"]
    mime = "application/vnd.google-apps.folder"
    r = _check(requests.get(f"{API}/files", headers=_h(), params={
        "q": f"name = '{FOLDER_NAME}' and mimeType = '{mime}' and trashed = false",
        "fields": "files(id)", "spaces": "drive"}, timeout=30))
    files = r.json().get("files", [])
    if files:
        fid = files[0]["id"]
    else:
        r = _check(requests.post(f"{API}/files", headers=_h(), params={"fields": "id"},
                                 json={"name": FOLDER_NAME, "mimeType": mime}, timeout=30))
        fid = r.json()["id"]
    _ids["folder"] = fid
    return fid


def get_settings():
    r = _check(requests.get(f"{API}/files/{folder_id()}", headers=_h(),
                            params={"fields": "appProperties"}, timeout=30))
    p = r.json().get("appProperties") or {}
    return {"cleanup_days": int(p.get("cleanup_days", "0") or 0)}


def set_settings(cleanup_days):
    _check(requests.patch(f"{API}/files/{folder_id()}", headers=_h(),
                          json={"appProperties": {"cleanup_days": str(int(cleanup_days))}}, timeout=30))


# ---------------------------------------------------------------- Fichiers
FIELDS = "id,name,size,createdTime,modifiedTime,appProperties"


def upload(path, name, props, file_id=None):
    """Envoie (ou remplace) un MP3. Renvoie l'identifiant du fichier."""
    size = os.path.getsize(path)
    meta = {"name": name, "appProperties": {k: str(v)[:110] for k, v in props.items()}}
    if file_id:
        url = f"{UPLOAD}/files/{file_id}"
        start = requests.patch(url, params={"uploadType": "resumable", "fields": "id"},
                               headers=_h({"X-Upload-Content-Type": "audio/mpeg",
                                           "X-Upload-Content-Length": str(size)}),
                               json=meta, timeout=60)
    else:
        meta["parents"] = [folder_id()]
        start = requests.post(f"{UPLOAD}/files", params={"uploadType": "resumable", "fields": "id"},
                              headers=_h({"X-Upload-Content-Type": "audio/mpeg",
                                          "X-Upload-Content-Length": str(size)}),
                              json=meta, timeout=60)
    _check(start)
    session = start.headers["Location"]
    for attempt in range(5):
        try:
            with open(path, "rb") as f:
                r = requests.put(session, data=f, headers={"Content-Length": str(size)}, timeout=600)
            _check(r)
            return r.json()["id"]
        except (requests.RequestException, RuntimeError):
            if attempt == 4:
                raise
            time.sleep(4 * (attempt + 1))


def list_files():
    out, token = [], None
    while True:
        params = {"q": f"'{folder_id()}' in parents and trashed = false and mimeType = 'audio/mpeg'",
                  "fields": f"nextPageToken,files({FIELDS})", "orderBy": "createdTime desc",
                  "pageSize": 200, "spaces": "drive"}
        if token:
            params["pageToken"] = token
        r = _check(requests.get(f"{API}/files", headers=_h(), params=params, timeout=30))
        j = r.json()
        out += j.get("files", [])
        token = j.get("nextPageToken")
        if not token or len(out) > 2000:
            return out


def set_props(file_id, props):
    _check(requests.patch(f"{API}/files/{file_id}", headers=_h(),
                          json={"appProperties": {k: str(v) for k, v in props.items()}}, timeout=30))


def get_meta(file_id):
    r = _check(requests.get(f"{API}/files/{file_id}", headers=_h(), params={"fields": FIELDS}, timeout=30))
    return r.json()


def trash(file_id):
    _check(requests.patch(f"{API}/files/{file_id}", headers=_h(), json={"trashed": True}, timeout=30))


def open_media(file_id, range_header=None):
    """Flux de lecture (avec prise en charge du « Range » pour avancer dans l'audio)."""
    h = {}
    if range_header:
        h["Range"] = range_header
    r = requests.get(f"{API}/files/{file_id}", params={"alt": "media"}, headers=_h(h),
                     stream=True, timeout=60)
    _check(r)
    return r


def storage():
    r = _check(requests.get(f"{API}/about", headers=_h(), params={"fields": "storageQuota"}, timeout=30))
    q = r.json().get("storageQuota", {})
    return {"used": int(q.get("usage", 0) or 0), "limit": int(q.get("limit", 0) or 0)}


def cleanup(days):
    """Met à la corbeille les voix off plus anciennes que `days` jours. Renvoie le nombre."""
    if days <= 0:
        return 0
    limit = datetime.now(timezone.utc) - timedelta(days=days)
    n = 0
    for f in list_files():
        try:
            created = datetime.fromisoformat(f["createdTime"].replace("Z", "+00:00"))
        except Exception:
            continue
        if created < limit:
            try:
                trash(f["id"])
                n += 1
            except Exception as e:
                log.warning("Nettoyage : %s", e)
    return n
