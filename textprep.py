"""Préparation du texte français avant envoi à MiniMax.

Objectif : que MiniMax n'ait plus rien à "deviner" (chiffres, abréviations,
tirets, références bibliques, mots difficiles), puis découper le script en
morceaux courts avec des pauses explicites.
"""
import re
import unicodedata

from num2words import num2words

# ---------------------------------------------------------------------------
# Outils
# ---------------------------------------------------------------------------

def _n(value, to="cardinal"):
    """Nombre en lettres, sans tirets (MiniMax lit mieux 'vingt six')."""
    try:
        txt = num2words(value, lang="fr", to=to)
    except Exception:
        return str(value)
    return txt.replace("-", " ")


def _int(s):
    return int(re.sub(r"[\s  .]", "", s))


ROMAN = {"I": 1, "V": 5, "X": 10, "L": 50, "C": 100}


def roman_to_int(s):
    total, prev = 0, 0
    for ch in reversed(s.upper()):
        v = ROMAN.get(ch)
        if v is None:
            return None
        if v < prev:
            total -= v
        else:
            total += v
            prev = v
    return total


# ---------------------------------------------------------------------------
# Règles par défaut
# ---------------------------------------------------------------------------

ABBREVIATIONS = [
    (r"\bav\.?\s*J\.?-?C\.?", "avant Jésus-Christ"),
    (r"\bap\.?\s*J\.?-?C\.?", "après Jésus-Christ"),
    (r"\bJ\.-C\.", "Jésus-Christ"),
    (r"\bM\.(?=\s+[A-ZÉÈ])", "monsieur"),
    (r"\bMM\.(?=\s)", "messieurs"),
    (r"\bMme\b\.?", "madame"),
    (r"\bMlle\b\.?", "mademoiselle"),
    (r"\bDr\b\.?", "docteur"),
    (r"\bPr\b\.?(?=\s+[A-ZÉÈ])", "professeur"),
    (r"\bSt\b\.?(?=\s+[A-ZÉÈ])", "saint"),
    (r"\bSte\b\.?(?=\s+[A-ZÉÈ])", "sainte"),
    (r"\betc\.(?=\s*$|\s*\n|\s+[A-ZÉÈ])", "et cetera."),
    (r"\betc\b\.?", "et cetera"),
    (r"\bex\.\s", "par exemple "),
    (r"\bc-à-d\b\.?|\bc\.-à-d\.", "c'est-à-dire"),
    (r"\bvs\.?(?=\s)", "contre"),
    (r"\bN°\s?", "numéro "),
    (r"\bn°\s?", "numéro "),
    (r"\bkm\b", "kilomètres"),
    (r"\bkg\b", "kilos"),
    (r"\bmin\b(?=\s|\.|,)", "minutes"),
    (r"ﷺ", " sallallahou alayhi wa sallam"),
    (r"\(\s*SAW\s*\)|\(\s*SAWS\s*\)|\(\s*PSL\s*\)", ", sallallahou alayhi wa sallam,"),
    (r"\(\s*AS\s*\)", ", alayhi salam,"),
    (r"\(\s*RA\s*\)", ", radiyallahou anhou,"),
    (r"\bQ\.?S\.?L\.?\b", ", que la paix soit sur lui,"),
]

# Mots devant lesquels un chiffre romain doit être lu
ROMAN_CONTEXT = (
    r"psaumes?|chapitres?|livres?|tomes?|parties?|actes?|sc[eè]nes?|"
    r"rois|chroniques|samuel|corinthiens|thessaloniciens|timoth[ée]e|pierre|jean|"
    r"louis|henri|charles|fran[cç]ois|napol[ée]on|jean-paul|beno[iî]t|pie|"
    r"l[ée]on|elizabeth|[ée]douard|philippe|guillaume|richard|benoit"
)

CURRENCY = [
    (r"(\d[\d\s  .]*\d|\d)(?:[,](\d+))?\s?(?:F\s?CFA|FCFA|XOF)", "francs CFA"),
    (r"(\d[\d\s  .]*\d|\d)(?:[,](\d+))?\s?€", "euros"),
    (r"(\d[\d\s  .]*\d|\d)(?:[,](\d+))?\s?\$", "dollars"),
    (r"(\d[\d\s  .]*\d|\d)(?:[,](\d+))?\s?%", "pour cent"),
]


# ---------------------------------------------------------------------------
# Étapes
# ---------------------------------------------------------------------------

def clean_characters(text):
    text = unicodedata.normalize("NFC", text)
    rep = {
        "’": "'", "‘": "'", "ʼ": "'", "`": "'", "´": "'",
        "“": '"', "”": '"', "«": '"', "»": '"', "„": '"',
        "…": "...", "–": " - ", "—": " - ", "‒": " - ",
        " ": " ", " ": " ", " ": " ", "\t": " ",
        "​": "", "﻿": "",
    }
    for a, b in rep.items():
        text = text.replace(a, b)
    # Markdown / mise en forme copiée depuis ChatGPT
    text = re.sub(r"\*\*|__|\*|#{1,6}\s?|`", "", text)
    text = re.sub(r"^\s*[-•▪►]\s+", "", text, flags=re.M)
    text = re.sub(r"\[(.*?)\]\((.*?)\)", r"\1", text)
    # Emojis et symboles décoratifs
    text = "".join(ch for ch in text if not (unicodedata.category(ch) in ("So", "Cs", "Co")
                                             and ch not in "°"))
    text = re.sub(r"[ ]{2,}", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def apply_dictionary(text, entries):
    """entries: liste de {"mot": ..., "lecture": ...}. Mot entier, insensible à la casse."""
    items = [(e.get("mot", "").strip(), e.get("lecture", "").strip()) for e in (entries or [])]
    items = [i for i in items if i[0] and i[1]]
    items.sort(key=lambda x: -len(x[0]))
    for word, reading in items:
        pattern = r"(?<![\wÀ-ÿ])" + re.escape(word) + r"(?![\wÀ-ÿ])"
        text = re.sub(pattern, reading, text, flags=re.I)
    return text


def apply_abbreviations(text):
    for pat, rep in ABBREVIATIONS:
        text = re.sub(pat, rep, text)
    return text


def bible_refs(text):
    # Jean 3:16-18 / Jean 3.16 / Psaume 23:1
    def ref(m):
        book, ch, v1, v2 = m.group(1), m.group(2), m.group(3), m.group(4)
        out = f"{book} chapitre {_n(int(ch))}, verset{'s' if v2 else ''} {_n(int(v1))}"
        if v2:
            out += f" à {_n(int(v2))}"
        return out
    text = re.sub(
        r"\b((?:[1-3]\s)?[A-ZÉÈ][a-zéèêëïîôûç]+)\s(\d{1,3})\s?[:.]\s?(\d{1,3})(?:\s?[-–]\s?(\d{1,3}))?\b",
        ref, text)
    # Sourate 2, verset 255 -> déjà lisible ; "S. 2:255"
    return text


def roman_numerals(text):
    def rep(m):
        word, num = m.group(1), m.group(2)
        v = roman_to_int(num)
        if not v:
            return m.group(0)
        if num.upper() == "I" and re.match(r"(?i)louis|henri|charles|fran|napol|pie|l[ée]on|elizabeth|[ée]douard|philippe|guillaume|richard", word):
            return f"{word} premier"
        return f"{word} {_n(v)}"
    text = re.sub(r"(?i)\b(" + ROMAN_CONTEXT + r")\s+([IVXLC]{1,7})\b(?!['\w])", rep, text)
    # XVIe siècle, XXIe siècle
    def siecle(m):
        v = roman_to_int(m.group(1))
        if not v:
            return m.group(0)
        return ("premier" if v == 1 else _n(v, "ordinal")) + " siècle"
    text = re.sub(r"\b([IVXLC]{1,6})(?:e|ème|eme|è)\s+si[eè]cle", siecle, text)
    return text


def numbers(text):
    # Monnaies et pourcentages
    for pat, unit in CURRENCY:
        def cur(m, unit=unit):
            whole = _int(m.group(1))
            dec = m.group(2)
            out = _n(whole)
            if dec:
                out += " virgule " + _n(int(dec)) if unit == "pour cent" else " " + unit + " " + _n(int(dec))
                return out if unit != "pour cent" else out + " pour cent"
            return f"{out} {unit}"
        text = re.sub(pat, cur, text)

    # Heures : 14h30, 9h, 14:30 (si suivi de h)
    def heure(m):
        h, mi = int(m.group(1)), m.group(2)
        out = f"{_n(h)} heure{'s' if h > 1 else ''}"
        if mi and int(mi) > 0:
            out += " " + _n(int(mi))
        return out
    text = re.sub(r"\b(\d{1,2})\s?h\s?(\d{2})?\b", heure, text)

    # Ordinaux : 1er, 1re, 1ère, 2e, 2ème, 3ᵉ
    def ordinal(m):
        v, suf = int(m.group(1)), m.group(2).lower()
        if v == 1:
            return "première" if suf.startswith(("re", "ère", "ere")) else "premier"
        return _n(v, "ordinal")
    text = re.sub(r"\b(\d{1,4})(er|re|ère|ere|e|ème|eme|ᵉ|è)\b", ordinal, text)

    # Scores et intervalles : 2-1, 2020-2022
    text = re.sub(r"\b(\d{1,4})\s?-\s?(\d{1,4})\b",
                  lambda m: f"{_n(int(m.group(1)))} à {_n(int(m.group(2)))}", text)

    # Grands nombres avec séparateurs : 5 000 / 5.000 / 1 250 000
    text = re.sub(r"\b\d{1,3}(?:[ .]\d{3})+\b(?![,.]\d)", lambda m: _n(_int(m.group(0))), text)

    # Décimaux : 3,5
    text = re.sub(r"\b(\d+),(\d+)\b",
                  lambda m: f"{_n(int(m.group(1)))} virgule {_n(int(m.group(2))) if not m.group(2).startswith('0') else ' '.join(_n(int(c)) for c in m.group(2))}",
                  text)

    # Tout le reste
    text = re.sub(r"\b\d+\b", lambda m: _n(int(m.group(0))), text)
    return text


def hyphens(text):
    # a-t-il, va-t-elle : on garde tel quel (MiniMax fait bien la liaison)
    keep = {}
    def _k(m):
        key = f"§{len(keep)}§"
        keep[key] = m.group(0)
        return key
    text = re.sub(r"(?i)\b\w+-t-(il|elle|on|ils|elles)\b", _k, text)
    # tirets entre mots : peut-être, est-ce, dit-il -> espace
    text = re.sub(r"(?<=[A-Za-zÀ-ÿ])-(?=[A-Za-zÀ-ÿ])", " ", text)
    for k, v in keep.items():
        text = text.replace(k, v)
    # tirets isolés (incises) -> virgule
    text = re.sub(r"\s-\s", ", ", text)
    return text


def final_cleanup(text):
    text = re.sub(r"\.{4,}", "...", text)
    text = re.sub(r"([!?]){2,}", r"\1", text)
    text = re.sub(r"\s+([,.;:!?])", r"\1", text)
    text = re.sub(r",\s*,", ",", text)
    text = re.sub(r"[ ]{2,}", " ", text)
    text = re.sub(r"[;:](?=\s)", ",", text)
    return text.strip()


def prepare(text, dictionary=None, options=None):
    """Renvoie le texte prêt pour MiniMax."""
    o = {"chiffres": True, "abreviations": True, "tirets": True}
    o.update(options or {})
    t = clean_characters(text)
    t = apply_dictionary(t, dictionary)
    if o["abreviations"]:
        t = apply_abbreviations(t)
    if o["chiffres"]:
        t = bible_refs(t)
        t = roman_numerals(t)
        t = numbers(t)
    if o["tirets"]:
        t = hyphens(t)
    # Le dictionnaire passe une 2e fois (au cas où une règle a recréé le mot)
    t = apply_dictionary(t, dictionary)
    return final_cleanup(t)


# ---------------------------------------------------------------------------
# Découpage en morceaux
# ---------------------------------------------------------------------------

SENT_RE = re.compile(r"[^.!?]+(?:\.\.\.|[.!?])+[\"')\]]*|[^.!?]+$")


def split_sentences(paragraph):
    out = []
    for m in SENT_RE.finditer(paragraph):
        s = m.group(0).strip()
        if not s:
            continue
        if not re.search(r"[.!?]$|\.\.\.$", s.rstrip("\"')]")):
            s += "."
        out.append(s)
    # recolle les phrases ultra courtes ("Oui.") à la suivante
    merged = []
    for s in out:
        if merged and len(merged[-1]) < 12:
            merged[-1] = merged[-1] + " " + s
        else:
            merged.append(s)
    return merged


def ending_of(sentence):
    s = sentence.rstrip("\"')] ")
    if s.endswith("..."):
        return "..."
    return s[-1] if s else "."


def make_chunks(prepared_text, max_chars=600, pauses=None, natural=True):
    """Découpe en morceaux de 1 à quelques phrases.

    Entre deux phrases d'un même morceau, on insère une pause MiniMax <#x#>
    pour qu'il respecte les points. Entre deux morceaux, c'est l'assemblage
    qui ajoute le silence exact (champ 'pause_after').
    """
    p = {"virgule": 0.0, "point": 0.45, "question": 0.5, "exclamation": 0.5,
         "suspension": 0.65, "paragraphe": 0.85}
    p.update(pauses or {})

    def pause_for(end):
        return {".": p["point"], "?": p["question"], "!": p["exclamation"],
                "...": p["suspension"], ",": max(0.12, p["virgule"] or 0.18)}.get(end, p["point"])

    paragraphs = [x.strip() for x in re.split(r"\n\s*\n|\n", prepared_text) if x.strip()]
    chunks = []
    for pi, para in enumerate(paragraphs):
        sentences = split_sentences(para)
        # phrases trop longues : coupe aux virgules
        pieces = []
        for s in sentences:
            if len(s) <= max_chars:
                pieces.append(s)
                continue
            parts = re.split(r"(?<=,)\s+", s)
            cur = ""
            for part in parts:
                if cur and len(cur) + len(part) + 1 > max_chars:
                    pieces.append(cur.rstrip(",") + ",")
                    cur = part
                else:
                    cur = (cur + " " + part).strip()
            if cur:
                pieces.append(cur)
        group = []
        for si, s in enumerate(pieces):
            if group and len(" ".join(group)) + len(s) + 1 > max_chars:
                chunks.append({"sentences": group, "paragraph": pi})
                group = []
            group.append(s)
        if group:
            chunks.append({"sentences": group, "paragraph": pi})

    out = []
    for i, c in enumerate(chunks):
        parts = []
        for j, s in enumerate(c["sentences"]):
            parts.append(s)
            if not natural and j < len(c["sentences"]) - 1:
                parts.append(f"<#{pause_for(ending_of(s)):.2f}#>")
        if not natural and p["virgule"] > 0:
            joined = " ".join(parts)
            joined = re.sub(r",\s+(?=[A-Za-zÀ-ÿ])", f", <#{p['virgule']:.2f}#> ", joined)
        else:
            joined = " ".join(parts)
        plain = " ".join(c["sentences"])
        last = c["sentences"][-1]
        is_para_end = i == len(chunks) - 1 or chunks[i + 1]["paragraph"] != c["paragraph"]
        pause_after = 0.0 if i == len(chunks) - 1 else (
            p["paragraphe"] if is_para_end else pause_for(ending_of(last)))
        out.append({
            "index": i,
            "text": plain,
            "minimax_text": joined,
            "chars": len(plain),
            "pause_after": round(pause_after, 2),
            "paragraph": c["paragraph"],
            "end": ending_of(last),
            "para_end": bool(is_para_end and i < len(chunks) - 1),
        })
    return out


def diff_preview(original, prepared):
    """Liste des transformations visibles (approximation par mots)."""
    import difflib
    a = re.findall(r"\S+", clean_characters(original))
    b = re.findall(r"\S+", prepared)
    sm = difflib.SequenceMatcher(a=a, b=b, autojunk=False)
    changes = []
    seen = set()
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag != "equal":
            before = " ".join(a[i1:i2]).strip(".,;:!?")
            after = " ".join(b[j1:j2]).strip(".,;:!?")
            norm = lambda z: re.sub(r"[\s.,;:!?]", "", z)
            if norm(before) != norm(after) and (before or after):
                key = (before, after)
                if key not in seen:
                    seen.add(key)
                    changes.append({"avant": before, "apres": after})
    return changes[:300]


# ---------------------------------------------------------------------------
# Réécoute : le texte entendu correspond-il au texte attendu ?
# ---------------------------------------------------------------------------

def _norm_words(text, dictionary=None):
    t = clean_characters(text or "")
    t = apply_dictionary(t, dictionary)
    t = numbers(t)
    t = unicodedata.normalize("NFKD", t.lower())
    t = "".join(ch for ch in t if not unicodedata.combining(ch))
    t = re.sub(r"<#[\d.]+#>", " ", t)
    t = re.sub(r"[^a-z0-9]+", " ", t)
    return t.split()


def heard_coverage(expected, heard, dictionary=None):
    """Renvoie {"coverage": 0-1, "missing": [mots non entendus]}."""
    import difflib
    a = _norm_words(expected, dictionary)
    b = _norm_words(heard, dictionary)
    if not a:
        return {"coverage": 1.0, "missing": []}
    sm = difflib.SequenceMatcher(a=a, b=b, autojunk=False)
    matched = 0
    missing = []
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag == "equal":
            matched += i2 - i1
        elif tag in ("delete", "replace"):
            seg_a, seg_b = a[i1:i2], b[j1:j2]
            # tolère les petites différences d'orthographe (noms propres…)
            ok = 0
            if tag == "replace" and seg_b:
                ra = difflib.SequenceMatcher(a=" ".join(seg_a), b=" ".join(seg_b)).ratio()
                if ra >= 0.72:
                    ok = len(seg_a)
            matched += ok
            if not ok:
                missing.extend(seg_a)
    return {"coverage": round(matched / len(a), 3), "missing": missing[:12]}
