"""Client minimal de l'API MiniMax (voix)."""
import os
import time

import requests

DEFAULT_BASE = "https://api.minimax.io"

ERRORS = {
    1000: "Erreur inconnue chez MiniMax, réessaie.",
    1001: "MiniMax a mis trop de temps à répondre.",
    1002: "Trop de demandes en même temps (limite MiniMax).",
    1004: "Clé API invalide. Vérifie-la dans Réglages.",
    1008: "Solde MiniMax insuffisant. Recharge ton compte sur platform.minimax.io.",
    1013: "Erreur interne MiniMax, réessaie.",
    1039: "Limite de vitesse MiniMax atteinte, l'outil ralentit.",
    1042: "Le texte contient trop de caractères invalides.",
    1043: "Le texte de l'extrait ne correspond pas à l'audio.",
    2013: "Paramètre refusé par MiniMax.",
    2038: "Ton compte MiniMax n'a pas encore le droit de cloner (vérification du compte requise).",
    2039: "Cet identifiant de voix existe déjà.",
}

RETRYABLE = {1000, 1001, 1002, 1013, 1039}


class MiniMaxError(Exception):
    def __init__(self, code, msg):
        self.code = code
        friendly = ERRORS.get(code)
        super().__init__(f"{friendly or msg} (code {code}{': ' + msg if friendly and msg else ''})")


class Client:
    def __init__(self, api_key, base=None):
        if not api_key:
            raise MiniMaxError(1004, "aucune clé")
        self.key = api_key.strip()
        self.base = (base or DEFAULT_BASE).rstrip("/")

    def _h(self, json=True):
        h = {"Authorization": f"Bearer {self.key}"}
        if json:
            h["Content-Type"] = "application/json"
        return h

    @staticmethod
    def _check(r):
        try:
            data = r.json()
        except Exception:
            raise MiniMaxError(r.status_code, f"réponse illisible (HTTP {r.status_code})")
        br = data.get("base_resp") or {}
        code = br.get("status_code", 0)
        if code != 0:
            raise MiniMaxError(code, br.get("status_msg", ""))
        if r.status_code >= 400:
            raise MiniMaxError(r.status_code, f"HTTP {r.status_code}")
        return data

    def _post(self, path, payload, timeout=180, tries=4):
        last = None
        for attempt in range(tries):
            try:
                r = requests.post(self.base + path, json=payload, headers=self._h(), timeout=timeout)
                return self._check(r)
            except MiniMaxError as e:
                last = e
                if e.code not in RETRYABLE:
                    raise
            except requests.RequestException as e:
                last = MiniMaxError(1001, str(e)[:120])
            time.sleep(2 * (attempt + 1))
        raise last

    # -- Fichiers ----------------------------------------------------------
    def upload(self, path, purpose):
        with open(path, "rb") as f:
            r = requests.post(self.base + "/v1/files/upload", headers=self._h(json=False),
                              data={"purpose": purpose},
                              files={"file": (path.rsplit("/", 1)[-1], f, "audio/mpeg")}, timeout=180)
        data = self._check(r)
        return data["file"]["file_id"]

    # -- Clonage -----------------------------------------------------------
    def clone(self, file_id, voice_id, prompt_file_id=None, prompt_text=None,
              preview_text=None, model="speech-2.8-hd", noise_reduction=False,
              volume_norm=True, language_boost="French"):
        payload = {"file_id": file_id, "voice_id": voice_id,
                   "need_noise_reduction": bool(noise_reduction),
                   "need_volume_normalization": bool(volume_norm)}
        if prompt_file_id and prompt_text:
            payload["clone_prompt"] = {"prompt_audio": prompt_file_id, "prompt_text": prompt_text}
        if preview_text:
            payload["text"] = preview_text
            payload["model"] = model
        if language_boost:
            payload["language_boost"] = language_boost
        return self._post("/v1/voice_clone", payload, timeout=300, tries=2)

    # -- Synthèse ----------------------------------------------------------
    def tts(self, text, voice_id, model="speech-2.8-hd", speed=1.0, vol=1.0, pitch=0,
            emotion=None, language_boost="French", normalization=None):
        vs = {"voice_id": voice_id, "speed": float(speed), "vol": float(vol), "pitch": int(pitch)}
        if emotion and emotion != "auto":
            vs["emotion"] = emotion
        payload = {
            "model": model,
            "text": text,
            "stream": False,
            "voice_setting": vs,
            "audio_setting": {"sample_rate": 44100, "bitrate": 256000, "format": "mp3", "channel": 1},
            "output_format": "hex",
        }
        if language_boost:
            payload["language_boost"] = language_boost
        if normalization in ("basic", "quality"):
            payload["text_normalization_mode"] = normalization
        data = self._post("/v1/t2a_v2", payload)
        d = data.get("data") or {}
        audio_hex = d.get("audio")
        if not audio_hex:
            raise MiniMaxError(1000, "aucun audio reçu")
        extra = data.get("extra_info") or {}
        return bytes.fromhex(audio_hex), extra

    # -- Transcription (réécoute) -----------------------------------------
    def transcribe(self, path, language="fr"):
        """Renvoie (texte, durée_en_secondes). Facturé à la durée audio."""
        headers = {"Authorization": f"Bearer {self.key}"}
        if language:
            headers["language"] = language
        last = None
        for attempt in range(3):
            try:
                with open(path, "rb") as f:
                    r = requests.post(self.base + "/v1/speech_to_text", headers=headers,
                                      data={"model": "asr-1.0", "response_format": "json"},
                                      files={"file": (os.path.basename(path), f, "audio/mpeg")},
                                      timeout=120)
            except requests.RequestException as e:
                last = MiniMaxError(1001, str(e)[:120])
                time.sleep(2 * (attempt + 1))
                continue
            if r.status_code < 400:
                try:
                    d = r.json()
                except Exception:
                    raise MiniMaxError(1000, "réponse de transcription illisible")
                br = d.get("base_resp") or {}
                if br.get("status_code", 0) != 0:
                    raise MiniMaxError(br.get("status_code"), br.get("status_msg", ""))
                return (d.get("text") or "").strip(), float(d.get("duration") or 0)
            try:
                msg = (r.json().get("error") or {}).get("message", "")
            except Exception:
                msg = r.text[:120]
            code = {401: 1004, 402: 1008, 429: 1002, 500: 1013}.get(r.status_code, r.status_code)
            last = MiniMaxError(code, msg)
            if r.status_code not in (429, 500, 502, 503):
                raise last
            time.sleep(2 * (attempt + 1))
        raise last

    # -- Voix --------------------------------------------------------------
    def voices(self, voice_type="voice_cloning"):
        data = self._post("/v1/get_voice", {"voice_type": voice_type}, tries=2)
        return data.get("voice_cloning") or [] if voice_type == "voice_cloning" else data

    def delete_voice(self, voice_id):
        return self._post("/v1/delete_voice", {"voice_type": "voice_cloning", "voice_id": voice_id}, tries=1)
