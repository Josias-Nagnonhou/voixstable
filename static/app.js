/* VoixStable — interface */
(() => {
"use strict";

const $ = (s, el = document) => el.querySelector(s);
const $$ = (s, el = document) => [...el.querySelectorAll(s)];
const esc = s => String(s ?? "").replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

const STORE = "voixstable_v1";
const DEFAULT_PAUSES = { point: 0.45, question: 0.5, exclamation: 0.5, suspension: 0.65, paragraphe: 0.85, virgule: 0 };
const pauseMode = v => (v && v.pauseMode) || "naturel";
const PRICE = { "speech-2.8-hd": 100, "speech-2.8-turbo": 60 };
const FCFA = 600;

let S = { apiKey: "", base: "https://api.minimax.io", voices: [], dictionary: [], lastVoice: "", model: "speech-2.8-hd", lastJob: "", script: "", history: [] };
function load() {
  try { const raw = localStorage.getItem(STORE); if (raw) S = Object.assign(S, JSON.parse(raw)); } catch (e) { /* stockage indisponible */ }
}
function save() {
  try { localStorage.setItem(STORE, JSON.stringify(S)); } catch (e) { /* ignore */ }
}

function toast(msg, bad = false, ms = 3800) {
  const t = $("#toast");
  t.textContent = msg; t.hidden = false; t.className = "toast" + (bad ? " bad" : "");
  clearTimeout(toast._t); toast._t = setTimeout(() => (t.hidden = true), ms);
}

async function api(path, { method = "GET", body, form } = {}) {
  const headers = { "x-api-key": S.apiKey || "", "x-api-base": S.base || "" };
  let payload;
  if (form) payload = form;
  else if (body !== undefined) { headers["Content-Type"] = "application/json"; payload = JSON.stringify(body); }
  const r = await fetch(path, { method, headers, body: payload });
  let data = null;
  try { data = await r.json(); } catch (e) { /* vide */ }
  if (r.ok && data && !isDemo()) trackSpend(path, data);
  if (!r.ok) {
    let msg = data && data.detail ? data.detail : `Erreur ${r.status}`;
    if (Array.isArray(msg)) msg = msg.map(m => m.msg).join(", ");
    throw new Error(msg);
  }
  return data;
}
/* ---------------- Suivi des dépenses MiniMax ---------------- */
const SPEND_LABEL = { "/api/speak": "Test d'écoute", "/api/test-variants": "Test des 3 versions", "/api/transcribe-sample": "Transcription de l'extrait" };
function recordSpend(key, usd, label) {
  if (!(usd > 0)) return;
  S.spend = S.spend || [];
  const ex = S.spend.find(x => x.key === key);
  if (ex) { if (usd > ex.usd) { ex.usd = usd; ex.at = Date.now(); } }
  else S.spend.push({ key, usd, label, at: Date.now() });
  if (S.spend.length > 600) S.spend = S.spend.slice(-600);
  save(); renderBalance();
}
function trackSpend(path, data) {
  if (path === "/api/clone" && data.voice_id) recordSpend("clone:" + data.voice_id, 1.5, "Clonage : " + (data.name || data.voice_id));
  const base = path.split("?")[0];
  if (SPEND_LABEL[base] && data.cost) recordSpend(base + ":" + Date.now() + Math.random(), data.cost.usd, SPEND_LABEL[base]);
}
function spentSince(ts) { return (S.spend || []).filter(x => x.at >= ts).reduce((a, x) => a + x.usd, 0); }
function balanceEstimate() {
  const b = S.balance; if (!b) return null;
  return { left: b.usd - spentSince(b.at), spent: spentSince(b.at), base: b.usd, at: b.at };
}
function renderBalance() {
  const est = balanceEstimate();
  const low = est && est.left < 3;
  const lb = document.getElementById("lowBalance");
  if (lb) lb.classList.toggle("hidden", !low || isDemo());
  if (!document.getElementById("balLeft")) return;
  if (!est) {
    $("#balLeft").textContent = "—"; $("#balSub").textContent = "Indique ton solde actuel ci-dessous pour activer le suivi.";
    $("#balBar").style.width = "0%"; $("#balList").innerHTML = ""; return;
  }
  const left = Math.max(0, est.left);
  $("#balLeft").textContent = `${left.toFixed(2).replace(".", ",")} $`;
  $("#balSub").textContent = `≈ ${fcfa(left * FCFA)} · environ ${Math.floor(left / 1.6)} voix off de 15 min en HD · ${est.spent.toFixed(2).replace(".", ",")} $ dépensés depuis le ${fmtDate(est.at)}`;
  $("#balBar").style.width = Math.max(0, Math.min(100, left / est.base * 100)) + "%";
  $("#balBar").classList.toggle("low", low);
  const recent = (S.spend || []).filter(x => x.at >= est.at).slice(-8).reverse();
  $("#balList").innerHTML = recent.length ? recent.map(x => `<div class="spend"><span>${esc(x.label)}</span><span class="muted small">${fmtDate(x.at)}</span><b>${x.usd < 0.01 ? "< 0,01" : x.usd.toFixed(2).replace(".", ",")} $</b></div>`).join("")
    : `<p class="muted small">Aucune dépense depuis ta dernière mise à jour du solde.</p>`;
}

const fcfa = n => `${Math.round(n).toLocaleString("fr-FR")} FCFA`;
const isDemo = () => (S.apiKey || "").trim().toLowerCase() === "demo";

/* ---------------- Navigation ---------------- */
function showTab(name) {
  if (name === "historique") { renderHistory(); loadDrive(); }
  $$(".tab").forEach(b => b.classList.toggle("active", b.dataset.tab === name));
  $$(".panel").forEach(p => p.classList.toggle("active", p.id === name));
  window.scrollTo({ top: 0, behavior: "smooth" });
}
$$(".tab").forEach(b => b.addEventListener("click", () => showTab(b.dataset.tab)));
document.addEventListener("click", e => {
  const g = e.target.closest("[data-goto]");
  if (g) { e.preventDefault(); showTab(g.dataset.goto); }
});

function banners() {
  $("#keyBanner").classList.toggle("hidden", !!S.apiKey);
  $("#demoBanner").classList.toggle("hidden", !isDemo());
}

/* ---------------- Voix ---------------- */
function voiceById(id) { return S.voices.find(v => v.id === id); }
function currentVoice() { return voiceById($("#voiceSelect").value); }
function voicePayload(v, model) {
  return { voice_id: v.id, emotion: v.emotion || "auto", speed: v.speed ?? 1, vol: v.vol ?? 1, pitch: v.pitch ?? 0,
           language_boost: v.lang || "French", model: model || $("#modelSelect").value };
}
function wasUsed(v) { return !!(v.usedOnce || v.ref); }
function daysSince(ts) { return ts ? (Date.now() - ts) / 86400000 : 99; }

function renderVoiceSelect() {
  const sel = $("#voiceSelect");
  if (!S.voices.length) {
    sel.innerHTML = `<option value="">Aucune voix : clone-en une dans « Mes voix »</option>`;
    return;
  }
  sel.innerHTML = S.voices.map(v => `<option value="${esc(v.id)}">${esc(v.name)}</option>`).join("");
  if (voiceById(S.lastVoice)) sel.value = S.lastVoice;
  renderOrb();
}

/* ---------------- Orbe ---------------- */
function voiceHue(id) { let h = 0; for (const c of String(id || "")) h = (h * 31 + c.charCodeAt(0)) % 360; return h; }
const MODES = { auto: "Naturel", calm: "Stable", happy: "Chaleureux", sad: "Grave", surprised: "Dynamique" };
function renderOrb() {
  const v = currentVoice(), orb = $("#orb");
  orb.classList.toggle("empty", !v);
  if (!v) { $("#orbName").textContent = "Aucune voix"; $("#orbMeta").textContent = "Clone ta première voix dans « Mes voix »."; return; }
  orb.style.setProperty("--h", voiceHue(v.id) + "deg");
  $("#orbName").textContent = v.name;
  const d = Math.floor(daysSince(v.created));
  $("#orbMeta").textContent = `${v.analyse?.categorie || "Voix clonée"} · mode ${MODES[v.emotion || "auto"].toLowerCase()}${v.created ? " · clonée " + (d < 1 ? "aujourd'hui" : `il y a ${d} j`) : ""}`;
}
let actx = null; const srcMap = new WeakMap(); let rafId = 0; let playingEl = null;
function orbListen(el) {
  try {
    actx = actx || new (window.AudioContext || window.webkitAudioContext)();
    if (actx.state === "suspended") actx.resume();
    let node = srcMap.get(el);
    if (!node) {
      const src = actx.createMediaElementSource(el);
      const an = actx.createAnalyser(); an.fftSize = 512;
      src.connect(an); an.connect(actx.destination);
      node = { an, buf: new Uint8Array(an.fftSize) }; srcMap.set(el, node);
    }
    playingEl = el;
    cancelAnimationFrame(rafId);
    const orb = $("#orb");
    const loop = () => {
      if (playingEl !== el || el.paused) { orb.style.setProperty("--lvl", 0); return; }
      node.an.getByteTimeDomainData(node.buf);
      let sum = 0; for (const x of node.buf) { const d = (x - 128) / 128; sum += d * d; }
      orb.style.setProperty("--lvl", Math.min(1, Math.sqrt(sum / node.buf.length) * 4).toFixed(3));
      rafId = requestAnimationFrame(loop);
    };
    loop();
  } catch (e) { /* animation facultative */ }
}
document.addEventListener("play", e => { if (e.target.tagName === "AUDIO") orbListen(e.target); }, true);

/* ---------------- Thème ---------------- */
function applyTheme(t) {
  document.documentElement.dataset.theme = t;
  $("#themeBtn").setAttribute("aria-label", t === "dark" ? "Passer en mode clair" : "Passer en mode sombre");
}
$("#themeBtn").onclick = () => {
  const next = document.documentElement.dataset.theme === "dark" ? "light" : "dark";
  applyTheme(next);
  try { localStorage.setItem("voixstable_theme", next); } catch (e) { /* ignore */ }
};
applyTheme(document.documentElement.dataset.theme ||
  (window.matchMedia && matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light"));

function renderVoices() {
  const list = $("#voiceList");
  if (!S.voices.length) {
    list.innerHTML = `<div class="empty"><h3>Aucune voix pour le moment</h3>
      <p>Clone ta première voix : importe un audio de 1 à 2 minutes, l'outil s'occupe du reste.</p>
      <button class="btn primary" id="emptyNew">+ Cloner une voix</button></div>`;
    $("#emptyNew").onclick = openWizard;
    updatePlayAllBtn();
    return;
  }
  list.innerHTML = S.voices.map(v => {
    const d = daysSince(v.created);
    const left = Math.max(0, Math.ceil(7 - d));
    const exp = !wasUsed(v) && d >= 4
      ? `<span class="badge bad">Jamais utilisée : à utiliser sous ${left} j, sinon MiniMax la supprime</span>`
      : `<span class="badge ok">Active</span>`;
    const mode = { auto: "Naturel", calm: "Stable", happy: "Chaleureux", sad: "Grave", surprised: "Dynamique" }[v.emotion || "auto"];
    return `<div class="voice" data-vid="${esc(v.id)}">
      ${v.imported ? `<span class="vnum">${S.voices.indexOf(v) + 1}</span>` : ""}
      <div class="vhead"><div class="orb orb-mini" style="--h:${voiceHue(v.id)}deg"><span></span></div>
      <div><h3>${esc(v.name)}</h3><div class="meta">${v.imported ? `Voix du site MiniMax${v.createdLabel ? " · créée le " + esc(v.createdLabel) : ""}` : esc(v.analyse?.categorie || "Voix clonée")}</div></div></div>
      <div class="tags"><span class="badge">${mode}</span><span class="badge">Vitesse ${(v.speed ?? 1).toFixed(2)}</span>${exp}</div>
      <div class="meta small">ID MiniMax : ${esc(v.id)}</div>
      <audio class="vprev" controls preload="none" hidden></audio>
      <div class="actions">
        <button class="btn ghost small" data-listen="${esc(v.id)}">Écouter</button>
        <button class="btn ghost small" data-rename="${esc(v.id)}">Renommer</button>
        <button class="btn primary small" data-use="${esc(v.id)}">Utiliser</button>
        <button class="btn ghost small" data-edit="${esc(v.id)}">Réglages</button>
      </div></div>`;
  }).join("");
  $$("[data-use]", list).forEach(b => b.onclick = () => { S.lastVoice = b.dataset.use; save(); renderVoiceSelect(); showTab("studio"); });
  $$("[data-edit]", list).forEach(b => b.onclick = () => openEditor(b.dataset.edit));
  updatePlayAllBtn();
  $$("[data-rename]", list).forEach(b => b.onclick = () => {
    const v = voiceById(b.dataset.rename); if (!v) return;
    const n = prompt("Nouveau nom pour cette voix :", v.name);
    if (n && n.trim()) { v.name = n.trim().slice(0, 60); save(); renderVoices(); renderVoiceSelect(); toast("Voix renommée."); }
  });
  $$("[data-listen]", list).forEach(b => b.onclick = async () => {
    const v = voiceById(b.dataset.listen); if (!v) return;
    const card = b.closest(".voice"), a = $(".vprev", card);
    b.disabled = true; b.textContent = "Génération…";
    try {
      const r = await api("/api/speak", { method: "POST", body: { voice: voicePayload(v), text: "Bonjour à tous. Aujourd'hui, je vais vous parler de quelque chose d'important. Écoutez bien.", pauses: v.pauses } });
      v.usedOnce = true; v.lastUsed = Date.now(); save();
      a.hidden = false; a.src = r.url; a.play().catch(() => {});
    } catch (e) { toast(e.message, true, 6000); }
    finally { b.disabled = false; b.textContent = "Écouter"; }
  });
}
function updatePlayAllBtn() {
  const b = $("#btnPlayAll"); const n = S.voices.length;
  b.hidden = n < 2;
  b.textContent = `Écouter toutes les voix (~${Math.max(1, Math.round(n * 1.7))} FCFA)`;
}

/* ---------------- Écouter toutes les voix (playlist) ---------------- */
const PA = { items: [], i: 0, running: false, audio: new Audio() };
function paCard(id) { return document.querySelector(`.voice[data-vid="${CSS.escape(id)}"]`); }
function paHighlight() {
  $$(".voice.playing").forEach(c => c.classList.remove("playing"));
  const it = PA.items[PA.i]; if (!it) return;
  const c = paCard(it.v.id);
  if (c) { c.classList.add("playing"); c.scrollIntoView({ behavior: "smooth", block: "center" }); }
  $("#paTitle").textContent = `Voix n°${it.num} : ${it.v.name}`;
  $("#paSub").textContent = it.url ? `${PA.i + 1} sur ${PA.items.length}` : `${PA.i + 1} sur ${PA.items.length} · génération…`;
}
async function paFetch(it) {
  if (it.url || it.err || it.pending) return it.pending;
  it.pending = api("/api/speak", { method: "POST", body: { voice: voicePayload(it.v, "speech-2.8-turbo"), text: `Voix numéro ${it.num}. Bonjour à tous.` } })
    .then(r => { it.url = r.url; it.v.usedOnce = true; it.v.lastUsed = Date.now(); })
    .catch(e => { it.err = e.message; });
  return it.pending;
}
async function paPlay() {
  if (!PA.running) return;
  const it = PA.items[PA.i];
  if (!it) { paStop("Toutes les voix ont été écoutées. Renomme celles que tu as reconnues."); return; }
  paHighlight();
  for (let k = 1; k <= 3; k++) if (PA.items[PA.i + k]) paFetch(PA.items[PA.i + k]);
  await paFetch(it);
  if (!PA.running || PA.items[PA.i] !== it) return;
  if (it.err) { toast(`Voix n°${it.num} : ${it.err}`, true, 4000); PA.i++; return paPlay(); }
  paHighlight();
  PA.audio.src = it.url;
  PA.audio.onended = () => { if (PA.running && PA.items[PA.i] === it) { PA.i++; setTimeout(paPlay, 450); } };
  try { await PA.audio.play(); orbListen(PA.audio); } catch (e) { /* lecture bloquée */ }
}
function paStop(msg) {
  PA.running = false; PA.audio.pause();
  $("#playAllBar").hidden = true;
  $$(".voice.playing").forEach(c => c.classList.remove("playing"));
  save();
  if (msg) toast(msg, false, 6000);
}
$("#btnPlayAll").onclick = () => {
  if (!S.apiKey) { toast("Ajoute d'abord ta clé API dans Réglages.", true); return; }
  PA.items = S.voices.map((v, i) => ({ v, num: i + 1 }));
  PA.i = 0; PA.running = true;
  $("#playAllBar").hidden = false;
  paPlay();
};
$("#btnPaNext").onclick = () => { PA.audio.pause(); PA.i = Math.min(PA.i + 1, PA.items.length); paPlay(); };
$("#btnPaPrev").onclick = () => { PA.audio.pause(); PA.i = Math.max(0, PA.i - 1); paPlay(); };
$("#btnPaStop").onclick = () => paStop();

function siteVoiceNumber() { return S.voices.filter(v => v.imported).length + 1; }
function frDate(s) { const m = /^(\d{4})-(\d{2})-(\d{2})/.exec(s || ""); return m ? `${m[3]}/${m[2]}/${m[1]}` : ""; }

$("#voiceSelect").addEventListener("change", () => { S.lastVoice = $("#voiceSelect").value; save(); renderOrb(); });
$("#modelSelect").addEventListener("change", () => { S.model = $("#modelSelect").value; save(); updateEstimate(); syncModelSeg(); });
function syncModelSeg() { $$("#modelSeg button").forEach(b => b.classList.toggle("on", b.dataset.model === $("#modelSelect").value)); }
$$("#modelSeg button").forEach(b => b.onclick = () => { $("#modelSelect").value = b.dataset.model; $("#modelSelect").dispatchEvent(new Event("change")); });

/* ---------------- Assistant de clonage ---------------- */
let W = {};
function openWizard() {
  W = {};
  $("#wizard").hidden = false; $("#voiceEditor").hidden = true;
  wizStep(1);
  $("#fileInput").value = ""; $("#promptText").value = ""; $("#voiceName").value = "";
  $("#wizard").scrollIntoView({ behavior: "smooth" });
}
function wizStep(n) {
  $$("#wizard .step").forEach(s => (s.hidden = +s.dataset.step !== n));
  $$("#wizard .steps li").forEach(li => {
    const k = +li.dataset.step;
    li.classList.toggle("active", k === n); li.classList.toggle("done", k < n);
  });
}
$("#btnNewVoice").onclick = openWizard;
$("#btnCloseWizard").onclick = () => ($("#wizard").hidden = true);
$("#btnBack1").onclick = () => wizStep(1);

const drop = $("#drop");
["dragenter", "dragover"].forEach(ev => drop.addEventListener(ev, e => { e.preventDefault(); drop.classList.add("over"); }));
["dragleave", "drop"].forEach(ev => drop.addEventListener(ev, e => { e.preventDefault(); drop.classList.remove("over"); }));
drop.addEventListener("drop", e => { if (e.dataTransfer.files[0]) analyzeFile(e.dataTransfer.files[0]); });
$("#fileInput").addEventListener("change", e => { if (e.target.files[0]) analyzeFile(e.target.files[0]); });

async function transcribePrompt() {
  if (!W.analysis || !S.apiKey) return;
  const ta = $("#promptText");
  ta.value = ""; ta.placeholder = "Transcription automatique en cours…"; ta.disabled = true;
  $("#promptStatus").textContent = "";
  try {
    const r = await api("/api/transcribe-sample", { method: "POST", body: { sample_id: W.analysis.sample_id, language_boost: $("#voiceLang").value } });
    ta.value = r.text;
    $("#promptStatus").textContent = r.text ? "Rempli automatiquement. Corrige seulement si un mot est faux." : "Aucune parole reconnue dans l'extrait.";
  } catch (e) {
    $("#promptStatus").textContent = "Transcription impossible : " + e.message + " Tu peux l'écrire toi-même, ou laisser vide.";
  } finally { ta.disabled = false; ta.placeholder = "Le texte de l'extrait apparaîtra ici."; }
}

$("#btnRetranscribe").onclick = () => transcribePrompt();

async function analyzeFile(file) {
  $("#analyzing").hidden = false;
  try {
    const fd = new FormData();
    fd.append("file", file); fd.append("use_best", $("#useBest").value); fd.append("target", $("#targetLen").value);
    const a = await api("/api/analyze", { method: "POST", form: fd });
    W.analysis = a;
    if (!$("#voiceName").value) $("#voiceName").value = file.name.replace(/\.[^.]+$/, "").slice(0, 40);
    renderAnalysis(a);
    wizStep(2);
    transcribePrompt();
  } catch (e) { toast(e.message, true, 6000); }
  finally { $("#analyzing").hidden = true; }
}

function mmss(s) { s = Math.round(s); return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`; }
function renderAnalysis(a) {
  const q = a.qualite >= 80 ? "ok" : a.qualite >= 55 ? "warn" : "bad";
  $("#analysis").innerHTML = `
    <div class="metric wide"><span>Catégorie détectée</span><b>${esc(a.categorie)}</b></div>
    <div class="metric"><span>Qualité pour le clonage</span><b><span class="badge ${q}">${a.qualite}/100</span></b></div>
    <div class="metric"><span>Propreté du son</span><b>${esc(a.proprete_label)}</b></div>
    <div class="metric"><span>Vitesse de parole</span><b>${esc(a.vitesse_label)}</b></div>
    <div class="metric"><span>Expressivité (intonations)</span><b>${esc(a.expressivite_label)}</b></div>
    <div class="metric"><span>Parole détectée</span><b>${mmss(a.duree_parole)} sur ${mmss(a.duree_totale)}</b></div>
    ${a.avertissements.length ? `<div class="warnbox"><b>À savoir</b><ul>${a.avertissements.map(w => `<li>${esc(w)}</li>`).join("")}</ul></div>` : ""}`;
  const t = Date.now();
  $("#aSample").src = a.url_echantillon + "?t=" + t;
  $("#aPrompt").src = a.url_extrait + "?t=" + t;
  $("#sampleInfo").textContent = a.duree_totale > a.duree_echantillon + 5
    ? `Meilleur passage choisi : de ${mmss(a.segment_debut)} à ${mmss(a.segment_fin)}, silences raccourcis, volume égalisé (${mmss(a.duree_echantillon)}).`
    : `Audio complet nettoyé : silences raccourcis, volume égalisé (${mmss(a.duree_echantillon)}).`;
  if (a.proprete_db < 24) $("#noiseRed").checked = true;
}

$("#btnClone").onclick = async () => {
  if (!S.apiKey) { toast("Ajoute d'abord ta clé API dans Réglages (ou « demo »).", true); return; }
  const name = $("#voiceName").value.trim();
  if (!name) { toast("Donne un nom à la voix.", true); return; }
  $("#cloning").hidden = false; $("#btnClone").disabled = true;
  try {
    const r = await api("/api/clone", { method: "POST", body: {
      sample_id: W.analysis.sample_id, name, prompt_text: $("#promptText").value.trim(),
      use_prompt: !!$("#promptText").value.trim(), noise_reduction: $("#noiseRed").checked,
      language_boost: $("#voiceLang").value, model: $("#modelSelect").value } });
    W.voice = { id: r.voice_id, name, lang: $("#voiceLang").value, emotion: "auto", speed: 1, vol: 1, pitch: 0,
                pauses: { ...DEFAULT_PAUSES }, analyse: r.analyse, created: Date.now(), lastUsed: Date.now(), demo: r.demo };
    if (/posé|calme/i.test(r.analyse.categorie)) W.voice.pauses.point = 0.65;
    if (/énergique/i.test(r.analyse.categorie)) { W.voice.pauses.point = 0.45; W.voice.pauses.paragraphe = 0.9; }
    S.voices.push(W.voice); S.lastVoice = W.voice.id; save();
    renderVoices(); renderVoiceSelect();
    wizStep(3);
    runVariants();
  } catch (e) { toast(e.message, true, 7000); }
  finally { $("#cloning").hidden = true; $("#btnClone").disabled = false; }
};

async function runVariants() {
  $("#testing").hidden = false; $("#variants").innerHTML = "";
  try {
    const r = await api("/api/test-variants", { method: "POST", body: {
      voice_id: W.voice.id, model: $("#modelSelect").value, language_boost: W.voice.lang, dictionary: S.dictionary } });
    W.voice.lastUsed = Date.now(); W.voice.usedOnce = true; save();
    $("#variants").innerHTML = r.variants.map(v => `
      <div class="variant">
        <b>${esc(v.label)}</b>
        <audio controls preload="none" src="${v.url}"></audio>
        <button class="btn primary small" data-pick="${v.key}">Je garde celle-ci</button>
      </div>`).join("");
    $$("[data-pick]").forEach(b => b.onclick = () => {
      const v = r.variants.find(x => x.key === b.dataset.pick);
      W.voice.emotion = v.emotion; W.voice.speed = v.speed;
      const m = v.metrics || {};
      W.voice.ref = { cps: m.cps, niveau_db: m.niveau_db, hauteur: m.hauteur, expressivite: m.expressivite };
      save(); renderVoices(); wizStep(4);
    });
  } catch (e) { toast(e.message, true, 7000); }
  finally { $("#testing").hidden = true; }
}
$("#btnRetest").onclick = runVariants;

$("#btnSync").onclick = async () => {
  if (!S.apiKey) { toast("Ajoute d'abord ta clé API dans Réglages.", true); return; }
  try {
    const r = await api("/api/voices");
    let added = 0;
    const sorted = [...r.voices].sort((a, b) => String(b.created_time || "").localeCompare(String(a.created_time || "")));
    for (const v of sorted) {
      const ex = voiceById(v.voice_id);
      if (ex) { if (ex.imported && !ex.createdLabel) ex.createdLabel = frDate(v.created_time); continue; }
      const desc = v.description && v.description[0];
      S.voices.push({ id: v.voice_id, name: desc || `Voix du site n°${siteVoiceNumber()}`, imported: true, usedOnce: true,
                      createdLabel: frDate(v.created_time), emotion: "auto", speed: 1, vol: 1, pitch: 0,
                      lang: "French", pauses: { ...DEFAULT_PAUSES }, created: Date.now(), lastUsed: Date.now() });
      added++;
    }
    if (!r.voices.length) {
      const id = prompt("Aucune voix trouvée automatiquement (MiniMax ne liste une voix qu'après sa première utilisation).\nSi tu connais l'identifiant d'une voix clonée, colle-le ici :");
      if (id && !voiceById(id.trim())) {
        S.voices.push({ id: id.trim(), name: id.trim(), emotion: "auto", speed: 1, vol: 1, pitch: 0, lang: "French",
                        pauses: { ...DEFAULT_PAUSES }, created: Date.now(), lastUsed: Date.now() });
        added++;
      }
    }
    save(); renderVoices(); renderVoiceSelect();
    toast(added ? `${added} voix ajoutée(s). Clique sur « Écouter » pour les reconnaître, puis « Renommer ».` : "Toutes tes voix sont déjà là.", false, 6000);
  } catch (e) { toast(e.message, true, 6000); }
};

/* ---------------- Éditeur de voix ---------------- */
let E = null;
const PMAP = [["edPoint", "point", "oP1"], ["edQuestion", "question", "oP2"], ["edExcl", "exclamation", "oP3"],
              ["edSusp", "suspension", "oP4"], ["edPara", "paragraphe", "oP5"], ["edComma", "virgule", "oP6"]];
function openEditor(id) {
  E = voiceById(id); if (!E) return;
  $("#wizard").hidden = true; $("#voiceEditor").hidden = false;
  $("#edTitle").textContent = "Réglages : " + E.name;
  $("#edName").value = E.name; $("#edEmotion").value = E.emotion || "auto"; $("#edLang").value = E.lang || "French";
  $("#edSpeed").value = E.speed ?? 1; $("#edPitch").value = E.pitch ?? 0; $("#edVol").value = E.vol ?? 1;
  const p = Object.assign({ ...DEFAULT_PAUSES }, E.pauses || {});
  PMAP.forEach(([inp, k]) => ($("#" + inp).value = p[k]));
  $("#edPauseMode").value = E.pauseMode || "naturel";
  $("#pauseSliders").hidden = $("#edPauseMode").value === "naturel";
  $("#edAudio").hidden = true;
  syncOutputs();
  $("#voiceEditor").scrollIntoView({ behavior: "smooth" });
}
function syncOutputs() {
  $("#oSpeed").textContent = (+$("#edSpeed").value).toFixed(2);
  $("#oPitch").textContent = $("#edPitch").value;
  $("#oVol").textContent = (+$("#edVol").value).toFixed(1);
  PMAP.forEach(([inp, , out]) => ($("#" + out).textContent = (+$("#" + inp).value).toFixed(2) + " s"));
}
$$("#voiceEditor input[type=range]").forEach(r => r.addEventListener("input", syncOutputs));
function editorValues() {
  const pauses = {}; PMAP.forEach(([inp, k]) => (pauses[k] = +$("#" + inp).value));
  return { pauseMode: $("#edPauseMode").value, name: $("#edName").value.trim() || E.name, emotion: $("#edEmotion").value, lang: $("#edLang").value,
           speed: +$("#edSpeed").value, pitch: +$("#edPitch").value, vol: +$("#edVol").value, pauses };
}
$("#edPauseMode").onchange = () => ($("#pauseSliders").hidden = $("#edPauseMode").value === "naturel");
$("#btnCloseEditor").onclick = () => ($("#voiceEditor").hidden = true);
$("#btnEdSave").onclick = () => {
  Object.assign(E, editorValues()); save(); renderVoices(); renderVoiceSelect();
  toast("Réglages enregistrés."); $("#voiceEditor").hidden = true;
};
$("#btnEdTest").onclick = async () => {
  const tmp = Object.assign({ ...E }, editorValues());
  $("#btnEdTest").disabled = true;
  try {
    const r = await api("/api/speak", { method: "POST", body: { voice: voicePayload(tmp), text: $("#edTestText").value, dictionary: S.dictionary, pauses: tmp.pauses, pause_mode: pauseMode(tmp) } });
    E.lastUsed = Date.now(); E.usedOnce = true; save();
    const a = $("#edAudio"); a.hidden = false; a.src = r.url; a.play().catch(() => {});
  } catch (e) { toast(e.message, true, 6000); }
  finally { $("#btnEdTest").disabled = false; }
};
$("#btnEdDelete").onclick = async () => {
  if (!confirm(`Retirer la voix « ${E.name} » de l'outil ?` + (E.imported ? "\n\nElle reste sur ton compte MiniMax : tu pourras la retrouver avec « Retrouver mes voix MiniMax »." : ""))) return;
  if (!E.imported && confirm("La supprimer aussi définitivement de ton compte MiniMax ?\n(OK = oui, Annuler = la garder chez MiniMax)")) {
    try { await api("/api/voices/delete", { method: "POST", body: { voice_id: E.id } }); }
    catch (e) { toast("Supprimée de l'outil, mais pas chez MiniMax : " + e.message, true, 6000); }
  }
  S.voices = S.voices.filter(v => v.id !== E.id); save();
  $("#voiceEditor").hidden = true; renderVoices(); renderVoiceSelect();
};

/* ---------------- Studio ---------------- */
const script = $("#script");
script.value = S.script || "";
let estT;
script.addEventListener("input", () => {
  clearTimeout(estT);
  estT = setTimeout(() => { S.script = script.value; save(); }, 600);
  updateEstimate();
});
function updateEstimate() {
  const n = script.value.length;
  $("#charCount").textContent = `${n.toLocaleString("fr-FR")} caractère${n > 1 ? "s" : ""}`;
  if (!n) { $("#estimate").textContent = ""; return; }
  const usd = n * (PRICE[$("#modelSelect").value] || 100) / 1e6;
  $("#estimate").textContent = `Environ ${fcfa(usd * FCFA)} · ~${Math.max(1, Math.round(n / 14.5 / 60))} min de voix`;
}

function genOptions() {
  return { chiffres: $("#optChiffres").checked, abreviations: $("#optAbrev").checked, tirets: $("#optTirets").checked };
}
async function prepareText() {
  const v = currentVoice();
  return api("/api/prepare", { method: "POST", body: {
    text: script.value, dictionary: S.dictionary, options: genOptions(), max_chars: +$("#maxChars").value,
    pauses: (v && v.pauses) || DEFAULT_PAUSES, pause_mode: pauseMode(v), model: $("#modelSelect").value } });
}
function renderPreview(r) {
  $("#previewCard").hidden = false;
  $("#previewMeta").textContent = `${r.chunks.length} morceaux · ${fcfa(r.cost.fcfa)}`;
  $("#changes").innerHTML = r.changes.length
    ? r.changes.map(c => `<span class="change"><s>${esc(c.avant) || "∅"}</s> → <b>${esc(c.apres) || "∅"}</b></span>`).join("")
    : `<span class="muted small">Aucune correction nécessaire.</span>`;
  $("#chunksPreview").innerHTML = r.chunks.map(c =>
    `<div class="cp"><span class="num">#${c.index + 1}</span>${esc(c.minimax_text).replace(/&lt;#([\d.]+)#&gt;/g, '<span class="pause">pause $1 s</span>')}${c.pause_after ? `<span class="pause">${pauseMode(currentVoice()) === "naturel" ? "pause naturelle" : "puis " + c.pause_after + " s"}</span>` : ""}</div>`).join("");
}
$("#btnPreview").onclick = async () => {
  if (!script.value.trim()) { toast("Colle d'abord ton script.", true); return; }
  try { renderPreview(await prepareText()); $("#previewCard").scrollIntoView({ behavior: "smooth" }); }
  catch (e) { toast(e.message, true); }
};

let JOB = null, pollT = null, outdated = false;
$("#btnGenerate").onclick = async () => {
  if (!S.apiKey) { toast("Ajoute ta clé API dans Réglages (ou « demo » pour essayer).", true); showTab("reglages"); return; }
  const v = currentVoice();
  if (!v) { toast("Clone ou ajoute d'abord une voix.", true); showTab("voix"); return; }
  if (!script.value.trim()) { toast("Colle d'abord ton script.", true); return; }
  if (JOB && JOB.status === "en cours" && !confirm("Une génération est déjà en cours. En lancer une nouvelle ?")) return;
  $("#btnGenerate").disabled = true;
  try {
    const prep = await prepareText();
    renderPreview(prep);
    const r = await api("/api/jobs", { method: "POST", body: {
      chunks: prep.chunks, voice: voicePayload(v), ref: v.ref || {}, threshold: +$("#threshold").value,
      asr: $("#optAsr").checked, dictionary: S.dictionary, pause_mode: pauseMode(v),
      title: fileTitle(script.value), voice_name: v.name,
      max_retries: +$("#retries").value } });
    v.lastUsed = Date.now(); v.usedOnce = true; S.lastJob = r.id;
    S.history = [{ id: r.id, title: fileTitle(script.value), voice: v.name, at: Date.now(), downloaded: false }, ...(S.history || [])].slice(0, 60);
    save(); renderVoices(); renderHistory();
    startPolling(r.id, true);
  } catch (e) { toast(e.message, true, 6000); }
  finally { $("#btnGenerate").disabled = false; }
};

const chunkEls = new Map();
/* ---------------- Historique des générations ---------------- */
function fileTitle(text) {
  let t = String(text || "").replace(/[*_#`>\[\]]/g, " ").replace(/\s+/g, " ").trim();
  const first = (t.match(/^[^.!?\n]{8,}?[.!?]/) || [t])[0];
  t = first.length > 70 ? first.slice(0, 70).replace(/\s+\S*$/, "") + "…" : first;
  return t.replace(/[\\/:*?"<>|]/g, " ").replace(/\s+/g, " ").trim() || "voix-off";
}
function histItem(id) { return (S.history || []).find(h => h.id === id); }
function markDownloaded(id) {
  const h = histItem(id);
  if (h) { h.downloaded = true; h.downloadedAt = Date.now(); save(); }
  const d = DRIVE.files.find(f => f.job === id || (h && f.id === h.driveId));
  if (d) d.downloaded = true;
  renderHistory();
  if (JOB && JOB.id === id) renderJob(JOB);
}
function fmtDate(ts) {
  const d = new Date(ts);
  return d.toLocaleDateString("fr-FR", { day: "2-digit", month: "2-digit", year: "2-digit" }) + " à " + d.toLocaleTimeString("fr-FR", { hour: "2-digit", minute: "2-digit" });
}
function fmtSize(b) { return b >= 1e9 ? (b / 1e9).toFixed(1).replace(".", ",") + " Go" : Math.max(1, Math.round(b / 1e6)) + " Mo"; }
function fmtDur(s) { s = Math.round(s || 0); return s ? `${Math.floor(s / 60)} min ${String(s % 60).padStart(2, "0")}` : ""; }

const DRIVE = { status: null, files: [], loaded: false };
async function loadDrive() {
  try {
    DRIVE.status = await api("/api/drive/status");
    if (DRIVE.status.connected && !DRIVE.status.error) {
      DRIVE.files = (await api("/api/drive/files")).files;
      DRIVE.loaded = true;
    }
  } catch (e) { DRIVE.status = { error: e.message }; }
  renderDrive(); renderHistory();
}
function renderDrive() {
  const st = DRIVE.status || {};
  const btn = $("#btnDriveConnect"), body = $("#driveBody");
  btn.hidden = true; body.hidden = true;
  if (st.error && !st.connected) { $("#driveSub").textContent = "Impossible de vérifier le Drive : " + st.error; return; }
  if (!st.configured) { $("#driveSub").textContent = "Pas encore configuré sur le serveur."; return; }
  if (!st.connected) {
    $("#driveSub").textContent = "Pas connecté. Connecte-le pour garder toutes tes voix off, même des mois après.";
    btn.hidden = false; return;
  }
  if (st.error) { $("#driveSub").textContent = "Connecté, mais Google répond : " + st.error; btn.hidden = false; btn.textContent = "Reconnecter"; return; }
  const used = st.storage?.used || 0, limit = st.storage?.limit || 0;
  $("#driveTitle").textContent = "Google Drive connecté";
  $("#driveSub").textContent = `Dossier « VoixStable » : ${st.count || 0} voix off · ${fmtSize(st.folder_bytes || 0)}` +
    (limit ? ` · Drive rempli à ${Math.round(used / limit * 100)} % (${fmtSize(used)} sur ${fmtSize(limit)})` : "");
  body.hidden = false;
  $("#driveMeterBar").style.width = (limit ? Math.min(100, used / limit * 100) : 0) + "%";
  $("#driveMeterBar").classList.toggle("high", limit && used / limit > 0.85);
  $("#driveCleanup").value = String(st.settings?.cleanup_days || 0);
}
$("#btnDriveConnect").onclick = () => { location.href = "/oauth/start"; };
$("#driveCleanup").onchange = async () => {
  const days = +$("#driveCleanup").value;
  if (days && !confirm(`Les voix off de plus de ${days} jours seront mises à la corbeille du Drive, maintenant et ensuite chaque jour. Continuer ?`)) {
    $("#driveCleanup").value = String(DRIVE.status?.settings?.cleanup_days || 0); return;
  }
  try {
    const r = await api("/api/drive/settings", { method: "POST", body: { cleanup_days: days } });
    toast(days ? `Suppression automatique réglée sur ${days} jours.` + (r.removed ? ` ${r.removed} ancienne(s) voix off mise(s) à la corbeille.` : "") : "Suppression automatique désactivée.");
    loadDrive();
  } catch (e) { toast(e.message, true); }
};

function historyEntries() {
  const local = S.history || [];
  const byJob = new Map(local.map(h => [h.id, h]));
  const out = [];
  const seen = new Set();
  for (const f of DRIVE.files) {
    const h = byJob.get(f.job);
    if (h) seen.add(h.id);
    out.push({ key: "d:" + f.id, driveId: f.id, jobId: f.job, title: f.title, voice: f.voice || h?.voice || "",
               at: Date.parse(f.created) || h?.at, downloaded: f.downloaded || !!h?.downloaded,
               size: f.size, duration: f.duration, where: "drive" });
  }
  for (const h of local) {
    if (seen.has(h.id) || (h.driveId && DRIVE.files.some(f => f.id === h.driveId))) continue;
    out.push({ key: "j:" + h.id, jobId: h.id, title: h.title, voice: h.voice, at: h.at,
               downloaded: !!h.downloaded, where: h.expired ? "expiree" : "serveur" });
  }
  return out.sort((x, y) => (y.at || 0) - (x.at || 0));
}

function renderHistory() {
  const all = historyEntries();
  const q = ($("#histSearch").value || "").toLowerCase().trim();
  const f = $("#histFilter").value;
  const list = all.filter(e => (!q || (e.title + " " + e.voice).toLowerCase().includes(q)) &&
    (f === "all" || (f === "done" ? e.downloaded : !e.downloaded)));
  const done = all.filter(e => e.downloaded).length;
  $("#historyMeta").textContent = all.length ? `${all.length} voix off · ${done} téléchargée${done > 1 ? "s" : ""}` : "";
  if (!all.length) {
    $("#historyList").innerHTML = `<div class="empty"><h3>Aucune voix off pour le moment</h3><p>Chaque voix off générée dans le Studio apparaîtra ici.</p></div>`;
    return;
  }
  if (!list.length) { $("#historyList").innerHTML = `<p class="muted">Aucun résultat.</p>`; return; }
  const BADGE = { drive: "Dans ton Drive", serveur: "Sur le serveur (24 h)", expiree: "Expirée" };
  $("#historyList").innerHTML = list.map(e => `
    <div class="hrow ${e.downloaded ? "dl" : ""} ${e.where === "expiree" ? "exp" : ""}" data-key="${esc(e.key)}">
      <span class="hmark" aria-hidden="true">${e.downloaded ? "✓" : ""}</span>
      <div class="htext"><b title="${esc(e.title)}">${esc(e.title)}</b>
        <span class="muted small">${[esc(e.voice), e.at ? fmtDate(e.at) : "", fmtDur(e.duration), e.size ? fmtSize(e.size) : ""].filter(Boolean).join(" · ")}</span></div>
      <div class="hbadges"><span class="hbadge">${e.downloaded ? "Téléchargée" : "Pas téléchargée"}</span><span class="hwhere ${e.where}">${BADGE[e.where]}</span></div>
      <div class="row-tight">
        ${e.where !== "expiree" ? `<button class="btn ghost small" data-hplay>Écouter</button>` : ""}
        ${e.where === "serveur" ? `<button class="btn ghost small" data-hopen>Ouvrir</button>` : ""}
        ${e.where !== "expiree" ? `<button class="btn ${e.downloaded ? "ghost" : "primary"} small" data-hdl>Télécharger</button>` : ""}
        <button class="btn ghost small" data-hdel aria-label="Supprimer" title="${e.where === "drive" ? "Supprimer du Drive" : "Retirer de la liste"}">✕</button>
      </div>
      <audio class="hplayer" controls preload="none" hidden></audio>
    </div>`).join("");
  const entry = el => all.find(x => x.key === el.closest(".hrow").dataset.key);
  $$("#historyList [data-hplay]").forEach(b => b.onclick = async () => {
    const e = entry(b), row = b.closest(".hrow"), a = $(".hplayer", row);
    if (!a.hidden) { a.pause(); a.hidden = true; return; }
    let src = e.driveId ? `/api/drive/files/${e.driveId}/audio` : null;
    if (!src) {
      try { const j = await api("/api/jobs/" + e.jobId); src = j.final; if (!src) { toast("Pas encore de MP3 final pour cette génération.", true); return; } }
      catch (err) { expire(e.jobId); return; }
    }
    $$("#historyList .hplayer").forEach(x => { x.pause(); x.hidden = true; });
    a.hidden = false; a.src = src; a.play().catch(() => {});
  });
  $$("#historyList [data-hopen]").forEach(b => b.onclick = () => { showTab("studio"); startPolling(entry(b).jobId, true); });
  $$("#historyList [data-hdl]").forEach(b => b.onclick = async () => {
    const e = entry(b);
    let href;
    if (e.driveId) href = `/api/drive/files/${e.driveId}/download?name=${encodeURIComponent(e.title)}`;
    else {
      try { const j = await api("/api/jobs/" + e.jobId); if (!j.final) { toast("Pas encore de MP3 final pour cette génération.", true); return; } href = `${j.final}?dl=1&name=${encodeURIComponent(e.title)}`; }
      catch (err) { expire(e.jobId); return; }
    }
    const a = document.createElement("a"); a.href = href; a.download = "";
    document.body.appendChild(a); a.click(); a.remove();
    if (e.jobId) markDownloaded(e.jobId);
    const d = DRIVE.files.find(x => x.id === e.driveId); if (d) { d.downloaded = true; renderHistory(); }
  });
  $$("#historyList [data-hdel]").forEach(b => b.onclick = async () => {
    const e = entry(b);
    if (e.where === "drive") {
      if (!confirm(`Supprimer « ${e.title} » de ton Google Drive ?\nElle ira dans la corbeille du Drive (récupérable pendant 30 jours).`)) return;
      try { await api(`/api/drive/files/${e.driveId}/delete`, { method: "POST" }); } catch (err) { toast(err.message, true); return; }
      DRIVE.files = DRIVE.files.filter(x => x.id !== e.driveId);
      if (DRIVE.status) DRIVE.status.count = Math.max(0, (DRIVE.status.count || 1) - 1);
      renderDrive();
    }
    S.history = (S.history || []).filter(h => h.id !== e.jobId); save(); renderHistory();
  });
}
function expire(jobId) {
  const h = histItem(jobId); if (h) { h.expired = true; save(); }
  renderHistory(); toast("Ce fichier n'est plus sur le serveur (plus de 24 h ou redémarrage).", true, 6000);
}
$("#histSearch").addEventListener("input", renderHistory);
$("#histFilter").addEventListener("change", renderHistory);
$("#btnDownload").addEventListener("click", () => { if (JOB) markDownloaded(JOB.id); });

function startPolling(id, scroll) {
  clearTimeout(pollT); chunkEls.clear(); $("#chunkList").innerHTML = ""; $("#leds").innerHTML = ""; outdated = false;
  $("#jobCard").hidden = false; $("#finalBox").hidden = true;
  if (scroll) $("#jobCard").scrollIntoView({ behavior: "smooth" });
  const tick = async () => {
    try {
      JOB = await api("/api/jobs/" + id);
      renderJob(JOB);
      if (JOB.status === "en cours") pollT = setTimeout(tick, 1500);
      else if (JOB.status === "termine" && scroll) toast("Voix off terminée !");
    } catch (e) {
      if (/introuvable/.test(e.message)) {
        $("#jobCard").hidden = true; S.lastJob = "";
        const h = histItem(id); if (h) { h.expired = true; renderHistory(); }
        save(); if (scroll) toast("Ce fichier n'est plus sur le serveur (plus de 24 h ou redémarrage).", true, 6000);
      }
      else pollT = setTimeout(tick, 4000);
    }
  };
  tick();
}

const STATUS = {
  attente: ["En attente", "run"], generation: ["Génération…", "run"], genere: ["Généré", "run"],
  correction: ["Correction…", "run"], ok: ["Parfait", "ok"], a_verifier: ["À écouter", "warn"], erreur: ["Erreur", "bad"],
};
function renderJob(j) {
  if (!isDemo() && j.cost) recordSpend("job:" + j.id, j.cost.usd, "Voix off : " + ((histItem(j.id) || {}).title || "génération"));
  $("#jobPhase").textContent = j.error ? "Erreur : " + j.error : j.phase;
  if (j.asr_note && !renderJob._noted) { renderJob._noted = true; toast(j.asr_note, true, 7000); }
  $("#jobBar").style.width = (j.status === "termine" ? 100 : Math.min(97, j.progress)) + "%";
  $("#stTotal").textContent = j.total; $("#stOk").textContent = j.ok; $("#stCheck").textContent = j.a_verifier;
  $("#stRegen").textContent = j.regenerations; $("#stCost").textContent = j.cost.fcfa.toLocaleString("fr-FR");
  $("#btnCancel").hidden = j.status !== "en cours";
  if (j.final) {
    $("#finalBox").hidden = false;
    const fa = $("#finalAudio");
    const ver = j.final + (JOB && JOB._ver || "");
    if (fa.dataset.src !== ver) { fa.dataset.src = ver; fa.src = j.final + "?v=" + Date.now(); }
    const h = histItem(j.id);
    $("#btnDownload").href = `${j.final}?dl=1&name=${encodeURIComponent(h ? h.title : "voix-off")}`;
    if (h && j.drive_id && h.driveId !== j.drive_id) { h.driveId = j.drive_id; save(); }
    $("#btnDownload").classList.toggle("done", !!(h && h.downloaded));
    $("#btnDownload").textContent = h && h.downloaded ? "Déjà téléchargée · retélécharger" : "Télécharger le MP3";
    $("#driveNote").textContent = { envoi: "Envoi dans ton Google Drive…", ok: "Rangée dans ton Google Drive (dossier VoixStable).", erreur: "L'envoi dans le Drive a échoué. Télécharge-la avant 24 h." }[j.drive_status] || "";
    $("#btnReassemble").classList.toggle("primary", outdated);
    $("#btnReassemble").textContent = outdated ? "Réassembler avec tes corrections" : "Réassembler";
  }
  $("#orb").classList.toggle("busy", j.status === "en cours");
  const LCLS = { ok: "ok", a_verifier: "warn", erreur: "bad", generation: "run", correction: "run" };
  const leds = $("#leds");
  if (leds.children.length !== j.chunks.length) {
    leds.innerHTML = j.chunks.map(c => `<i data-led="${c.index}" title="Morceau ${c.index + 1}"></i>`).join("");
    $$("i", leds).forEach(el => el.onclick = () => {
      const t = chunkEls.get(+el.dataset.led); if (!t) return;
      t.hidden = false; t.scrollIntoView({ behavior: "smooth", block: "center" });
      t.classList.add("flash"); setTimeout(() => t.classList.remove("flash"), 1400);
    });
  }
  j.chunks.forEach(c => {
    let k = LCLS[c.status] || "";
    if (c.status === "ok" && c.attempts.length > 1) k = "ok";
    leds.children[c.index].className = k;
  });
  const only = $("#onlyCheck").checked;
  const list = $("#chunkList");
  j.chunks.forEach(c => {
    const sig = [c.status, c.best, c.attempts.length, c.score, c.text].join("|");
    let el = chunkEls.get(c.index);
    if (!el) { el = document.createElement("div"); list.appendChild(el); chunkEls.set(c.index, el); }
    el.hidden = only && c.status !== "a_verifier" && c.status !== "erreur";
    if (el.dataset.sig === sig) return;
    const playing = el.querySelector("audio") && !el.querySelector("audio").paused;
    if (playing) return;
    el.dataset.sig = sig;
    const [lab, cls] = STATUS[c.status] || [c.status, ""];
    const best = c.attempts.find(a => a.n === c.best);
    el.className = "chunk st-" + c.status;
    el.innerHTML = `
      <div class="chunk-top">
        <div><span class="badge">#${c.index + 1}</span> <span class="badge ${cls}">${lab}${c.score != null && c.status !== "correction" ? " · " + c.score + "/100" : ""}</span></div>
        <div class="chunk-tools">
          ${best ? `<audio controls preload="none" src="${best.url}"></audio>` : ""}
        </div>
      </div>
      <div class="chunk-text">${esc(c.text)}</div>
      ${c.issues && c.issues.length ? `<div class="issues">${c.issues.map(esc).join(" · ")}</div>` : ""}
      <div class="chunk-top" style="margin-top:8px">
        <div class="attempts">${c.attempts.length > 1 ? c.attempts.map(a =>
          `<button class="att ${a.n === c.best ? "sel" : ""}" data-choose="${a.n}" title="${esc((a.issues || []).join(", "))}">Essai ${a.n}${a.score != null ? " · " + a.score : ""}</button>`).join("") : ""}</div>
        <div class="chunk-tools">
          <button class="btn ghost small" data-act="edit">Modifier le texte</button>
          <button class="btn ghost small" data-act="regen">Refaire (~${Math.max(1, Math.round(c.chars * (PRICE[$("#modelSelect").value] || 100) / 1e6 * FCFA))} FCFA)</button>
        </div>
      </div>
      <div class="edit-area"><textarea>${esc(c.text)}</textarea>
        <div class="actions"><button class="btn primary small" data-act="regen-edit">Régénérer avec ce texte</button></div></div>`;
    $$("[data-choose]", el).forEach(b => b.onclick = () => chooseAttempt(c.index, +b.dataset.choose));
    $("[data-act=edit]", el).onclick = () => $(".edit-area", el).classList.toggle("open");
    $("[data-act=regen]", el).onclick = () => regen(c.index, null);
    $("[data-act=regen-edit]", el).onclick = () => regen(c.index, $("textarea", el).value);
  });
}
JOB = null;

async function regen(i, text) {
  if (!JOB) return;
  const el = chunkEls.get(i);
  el.dataset.sig = ""; el.querySelector(".badge:nth-child(2)").textContent = "Correction…";
  try {
    const r = await api(`/api/jobs/${JOB.id}/chunks/${i}/regen`, { method: "POST", body: { text, dictionary: S.dictionary } });
    JOB = r; outdated = true; renderJob(r);
    const a = chunkEls.get(i).querySelector("audio"); if (a) a.play().catch(() => {});
  } catch (e) { toast(e.message, true, 6000); el.dataset.sig = ""; renderJob(JOB); }
}
async function chooseAttempt(i, n) {
  try { const r = await api(`/api/jobs/${JOB.id}/chunks/${i}/choose`, { method: "POST", body: { n } }); JOB = r; outdated = true; renderJob(r); }
  catch (e) { toast(e.message, true); }
}
$("#btnReassemble").onclick = async () => {
  if (!JOB) return;
  $("#btnReassemble").disabled = true;
  try { const r = await api(`/api/jobs/${JOB.id}/assemble`, { method: "POST" }); JOB = r; JOB._ver = Date.now(); outdated = false; renderJob(r); toast("Voix off mise à jour."); }
  catch (e) { toast(e.message, true); }
  finally { $("#btnReassemble").disabled = false; }
};
$("#btnCancel").onclick = async () => { if (JOB) { await api(`/api/jobs/${JOB.id}/cancel`, { method: "POST" }).catch(() => {}); toast("Arrêt demandé."); } };
$("#onlyCheck").addEventListener("change", () => JOB && (chunkEls.forEach(el => (el.dataset.sig = "")), renderJob(JOB)));

/* ---------------- Dictionnaire ---------------- */
const PACKS = {
  foot: [["PSG", "pé èsse gé"], ["OM", "o èm"], ["OL", "o èl"], ["UEFA", "ué fa"], ["FIFA", "fi fa"], ["VAR", "var"],
         ["Mbappé", "Èmbapé"], ["Vinícius", "Vinissiousse"], ["Bellingham", "Bèllinngame"], ["Haaland", "Holande"],
         ["Yamal", "Yamal"], ["Liverpool", "Liveurpoule"], ["Barça", "Barssa"], ["Ballon d'Or", "Ballon d'or"]],
  islam: [["Bismillah", "Bismillahi"], ["hadith", "hadisse"], ["hadiths", "hadisses"], ["Allahou Akbar", "Allahou akbare"],
          ["Coran", "Coran"], ["sourate", "sourate"], ["Ramadan", "Ramadane"], ["Inch'Allah", "Inn cha Allah"],
          ["Subhanallah", "Soubhanallah"], ["Alhamdoulillah", "Al hamdou lillah"], ["Médine", "Médine"], ["Ibn", "Ibne"]],
  chretien: [["Ménas", "Mé nasse"], ["Jésus-Christ", "Jésus Christ"], ["Yahvé", "Ya vé"], ["YHWH", "Ya vé"],
             ["Abraham", "Abra hame"], ["Jérusalem", "Jérusalème"], ["Amen", "Amène"]],
};
function renderDico() {
  const q = ($("#dSearch").value || "").toLowerCase();
  const items = S.dictionary.map((d, i) => ({ ...d, i })).filter(d => !q || d.mot.toLowerCase().includes(q) || d.lecture.toLowerCase().includes(q));
  $("#dicoCount").textContent = `${S.dictionary.length} mot${S.dictionary.length > 1 ? "s" : ""}`;
  $("#dicoList").innerHTML = items.length ? items.map(d => `
    <div class="drow"><b>${esc(d.mot)}</b><span class="arrow">→</span><span>${esc(d.lecture)}</span><button data-del="${d.i}">Retirer</button></div>`).join("")
    : `<p class="muted small">Ton dictionnaire est vide. Ajoute les mots que MiniMax prononce mal.</p>`;
  $$("[data-del]").forEach(b => b.onclick = () => { S.dictionary.splice(+b.dataset.del, 1); save(); renderDico(); });
}
function addWord(mot, lecture) {
  mot = mot.trim(); lecture = lecture.trim();
  if (!mot || !lecture) return false;
  const ex = S.dictionary.find(d => d.mot.toLowerCase() === mot.toLowerCase());
  if (ex) ex.lecture = lecture; else S.dictionary.push({ mot, lecture });
  return true;
}
$("#btnDAdd").onclick = () => {
  if (!addWord($("#dMot").value, $("#dLecture").value)) { toast("Remplis le mot et sa lecture.", true); return; }
  save(); renderDico(); $("#dMot").value = ""; $("#dLecture").value = ""; $("#dMot").focus();
};
$("#dLecture").addEventListener("keydown", e => { if (e.key === "Enter") $("#btnDAdd").click(); });
$("#dSearch").addEventListener("input", renderDico);
$$("[data-pack]").forEach(b => b.onclick = () => {
  let n = 0;
  PACKS[b.dataset.pack].forEach(([m, l]) => { if (!S.dictionary.some(d => d.mot.toLowerCase() === m.toLowerCase())) { S.dictionary.push({ mot: m, lecture: l }); n++; } });
  save(); renderDico(); toast(n ? `${n} mot(s) ajouté(s). Teste-les et ajuste si besoin.` : "Déjà dans ton dictionnaire.");
});
$("#btnDTest").onclick = async () => {
  const mot = $("#dMot").value.trim(), lecture = $("#dLecture").value.trim();
  const v = currentVoice();
  if (!v) { toast("Clone d'abord une voix pour tester.", true); return; }
  if (!mot) { toast("Écris le mot à tester.", true); return; }
  const base = S.dictionary.filter(d => d.mot.toLowerCase() !== mot.toLowerCase());
  const say = `Écoutez bien : ${mot}. Encore une fois, ${mot}.`;
  const variants = lecture.includes("|") ? lecture.split("|").map(x => x.trim()).filter(Boolean) : null;
  if (!variants) {
    const dict = [...base]; if (lecture) dict.push({ mot, lecture });
    try {
      const r = await api("/api/speak", { method: "POST", body: { voice: voicePayload(v), text: say, dictionary: dict } });
      v.usedOnce = true; v.lastUsed = Date.now(); save();
      $("#dCompare").hidden = true;
      const a = $("#dAudio"); a.hidden = false; a.src = r.url; a.play().catch(() => {});
    } catch (e) { toast(e.message, true, 6000); }
    return;
  }
  const box = $("#dCompare");
  box.hidden = false; $("#dAudio").hidden = true;
  box.innerHTML = variants.map((w, i) => `<div class="dvar"><b>${esc(w)}</b><span class="muted small">Génération…</span></div>`).join("");
  $("#btnDTest").disabled = true;
  const results = await Promise.all(variants.map(async w => {
    const same = w.toLowerCase() === mot.toLowerCase();
    try {
      const r = await api("/api/speak", { method: "POST", body: { voice: voicePayload(v), text: say, dictionary: same ? base : [...base, { mot, lecture: w }] } });
      return { w, url: r.url, same };
    } catch (e) { return { w, err: e.message, same }; }
  }));
  $("#btnDTest").disabled = false;
  v.usedOnce = true; v.lastUsed = Date.now(); save();
  box.innerHTML = results.map((x, i) => `
    <div class="dvar">
      <b>${esc(x.w)}</b>${x.same ? `<span class="muted small">écriture normale</span>` : ""}
      ${x.url ? `<audio controls preload="auto" src="${x.url}"></audio>` : `<span class="small" style="color:var(--bad)">${esc(x.err)}</span>`}
      ${x.url ? `<button class="btn ${i === 0 ? "ghost" : "primary"} small" data-keep="${i}">Garder celle-ci</button>` : ""}
    </div>`).join("");
  $$("[data-keep]", box).forEach(b => b.onclick = () => {
    const x = results[+b.dataset.keep];
    if (x.same) { S.dictionary = S.dictionary.filter(d => d.mot.toLowerCase() !== mot.toLowerCase()); toast(`« ${mot} » reste lu normalement.`); }
    else { addWord(mot, x.w); toast(`« ${mot} » sera lu « ${x.w} » partout.`); }
    save(); renderDico(); box.hidden = true; $("#dMot").value = ""; $("#dLecture").value = "";
  });
};
$("#chipQua").onclick = () => {
  $("#dMot").value = "quatre";
  $("#dLecture").value = "quatre | katre | catre | kattre";
  $("#btnDTest").click();
};

/* ---------------- Réglages ---------------- */
$("#btnBalSave").onclick = () => {
  const v = parseFloat(String($("#balInput").value).replace(",", "."));
  if (!(v >= 0)) { toast("Écris le montant en dollars affiché sur MiniMax (exemple : 25 ou 23,40).", true); return; }
  S.balance = { usd: v, at: Date.now() }; save(); renderBalance();
  $("#balInput").value = ""; toast("Solde enregistré. L'outil va maintenant décompter chaque dépense.");
};
$("#apiKey").value = S.apiKey || "";
$("#apiBase").value = S.base || "https://api.minimax.io";
$("#btnShowKey").onclick = () => {
  const k = $("#apiKey"); k.type = k.type === "password" ? "text" : "password";
  $("#btnShowKey").textContent = k.type === "password" ? "Afficher" : "Masquer";
};
$("#btnSaveKey").onclick = async () => {
  S.apiKey = $("#apiKey").value.trim(); S.base = $("#apiBase").value; save(); banners();
  const st = $("#keyStatus");
  if (!S.apiKey) { st.textContent = "Clé effacée."; st.className = "status"; return; }
  st.textContent = "Vérification…"; st.className = "status";
  try { const r = await api("/api/check-key", { method: "POST" }); st.textContent = "✓ " + r.message; st.className = "status ok"; }
  catch (e) { st.textContent = "✗ " + e.message; st.className = "status bad"; }
};
$("#btnExport").onclick = () => {
  const data = { ...S }; delete data.apiKey; delete data.lastJob; delete data.script;
  const blob = new Blob([JSON.stringify(data, null, 2)], { type: "application/json" });
  const a = document.createElement("a"); a.href = URL.createObjectURL(blob);
  a.download = `voixstable-reglages-${new Date().toISOString().slice(0, 10)}.json`; a.click();
  toast("Fichier exporté (ta clé API n'y est pas, par sécurité).");
};
$("#importFile").addEventListener("change", async e => {
  const f = e.target.files[0]; if (!f) return;
  try {
    const d = JSON.parse(await f.text());
    (d.voices || []).forEach(v => { const ex = voiceById(v.id); if (ex) Object.assign(ex, v); else S.voices.push(v); });
    (d.dictionary || []).forEach(x => addWord(x.mot, x.lecture));
    save(); renderVoices(); renderVoiceSelect(); renderDico(); toast("Réglages importés.");
  } catch (err) { toast("Fichier invalide.", true); }
  e.target.value = "";
});

/* ---------------- Démarrage ---------------- */
load();
(() => {
  let k = S.voices.filter(v => v.imported).length;
  S.voices.forEach(v => {
    if (!v.imported && v.name === v.id && /^moss_audio_/.test(v.id)) {
      v.imported = true; v.usedOnce = true; v.name = `Voix du site n°${++k}`;
    }
  });
  save();
})();
script.value = S.script || "";
$("#apiKey").value = S.apiKey || "";
$("#apiBase").value = S.base || "https://api.minimax.io";
$("#modelSelect").value = S.model || "speech-2.8-hd"; syncModelSeg();
banners(); renderVoiceSelect(); renderVoices(); renderDico(); updateEstimate(); renderHistory(); renderBalance();
if (S.lastJob) startPolling(S.lastJob, false);
if (location.hash === "#historique") { showTab("historique"); history.replaceState(null, "", location.pathname); }
const old = S.voices.filter(v => !wasUsed(v) && daysSince(v.created) >= 4);
if (old.length) setTimeout(() => toast(`Attention : ${old.map(v => v.name).join(", ")} n'a jamais été utilisée. Fais-lui lire une phrase avant 7 jours après le clonage, sinon MiniMax la supprime.`, true, 9000), 800);
})();
