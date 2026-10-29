"use strict";
// Lien vers le code source (exigé par l'AGPL pour une application web) : à adapter une fois le dépôt créé.
const REPO_URL = "https://github.com/YOUR-USER/pdf2docx";

const $ = (id) => document.getElementById(id);
const STORE = "pdf2docx_settings";
const DEFAULTS = { ocr: "auto", lang: "fra+eng", pages: "0", borderless: true, boxes: true, figures: true,
  shapes: true, headers: true, keep: false, formulas: false };
let limits = { max_mb: 30, max_pages: 60 };
const done = new Map();           // id -> nom du .docx, pour le zip

// ---------- réglages mémorisés (localStorage peut être indisponible : on ignore l'erreur)
function loadSettings() {
  try { return Object.assign({}, DEFAULTS, JSON.parse(localStorage.getItem(STORE) || "{}")); }
  catch (e) { return Object.assign({}, DEFAULTS); }
}
function saveSettings(s) { try { localStorage.setItem(STORE, JSON.stringify(s)); } catch (e) { /* ignoré */ } }
function readSettings() {
  const s = {};
  for (const k of ["ocr", "lang", "pages"]) s[k] = document.querySelector(`input[name=${k}]:checked`).value;
  for (const k of ["borderless", "boxes", "figures", "shapes", "headers", "keep", "formulas"]) s[k] = $(k).checked;
  return s;
}
function applySettings(s) {
  for (const k of ["ocr", "lang", "pages"]) {
    const el = document.querySelector(`input[name=${k}][value="${s[k]}"]`);
    if (el && !el.disabled) el.checked = true;
  }
  for (const k of ["borderless", "boxes", "figures", "shapes", "headers", "keep", "formulas"]) $(k).checked = !!s[k];
}
function query() {
  const s = readSettings();
  const b = (v) => (v ? "1" : "0");
  return new URLSearchParams({ ocr: s.ocr, lang: s.lang, pages: s.pages, formulas: s.formulas ? "text" : "image",
    keep: b(s.keep), borderless: b(s.borderless), boxes: b(s.boxes), figures: b(s.figures), shapes: b(s.shapes),
    headers: b(s.headers) }).toString();
}

// ---------- initialisation
$("year").textContent = new Date().getFullYear();
$("source").href = REPO_URL;
applySettings(loadSettings());
document.querySelectorAll(".settings input").forEach((el) => el.addEventListener("change", () => saveSettings(readSettings())));
$("reset").onclick = () => { applySettings(DEFAULTS); saveSettings(DEFAULTS); };

fetch("/info").then((r) => r.json()).then((info) => {
  limits = info.limits || limits;
  $("limits").textContent = `PDF jusqu'à ${limits.max_pages} pages et ${limits.max_mb} Mo. Comptez 1 à 3 secondes par page.`;
  const notes = [];
  if (!info.tesseract) {
    notes.push("Tesseract (reconnaissance de texte) est introuvable : les PDF normaux fonctionnent, pas les PDF scannés.");
    document.querySelector("input[name=ocr][value=off]").checked = true;
  } else if (info.langs.length) {
    if (!info.langs.includes("fra")) notes.push("La langue française manque à Tesseract : la lecture des scans en français sera mauvaise.");
    for (const el of document.querySelectorAll("input[name=lang]")) {
      if (!el.value.split("+").every((l) => info.langs.includes(l))) { el.disabled = true; el.parentElement.style.opacity = ".45"; }
    }
    if (document.querySelector("input[name=lang]:checked").disabled) {
      const ok = [...document.querySelectorAll("input[name=lang]")].find((el) => !el.disabled);
      if (ok) ok.checked = true;
    }
  }
  if (notes.length) { $("notice").hidden = false; $("notice").textContent = notes.join(" "); }
}).catch(() => { $("limits").textContent = "Le serveur local ne répond pas. Relancez le programme."; });

// ---------- dépôt de fichiers
const drop = $("drop"), input = $("file");
["dragenter", "dragover"].forEach((ev) => drop.addEventListener(ev, (e) => { e.preventDefault(); drop.classList.add("over"); }));
["dragleave", "drop"].forEach((ev) => drop.addEventListener(ev, (e) => { e.preventDefault(); drop.classList.remove("over"); }));
drop.addEventListener("drop", (e) => addFiles(e.dataTransfer.files));
input.addEventListener("change", () => { addFiles(input.files); input.value = ""; });
window.addEventListener("dragover", (e) => e.preventDefault());
window.addEventListener("drop", (e) => e.preventDefault());

const esc = (s) => String(s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const SING = { tableaux: "tableau", "encadrés": "encadré", formules: "formule", formes: "forme", "lignes OCR": "ligne OCR", "pages scannées": "page scannée" };
const mb = (n) => (n / 1048576).toFixed(1).replace(".", ",") + " Mo";

function addFiles(files) {
  for (const f of files) {
    $("results").hidden = false;
    const row = document.createElement("li");
    row.className = "row";
    row.innerHTML = `<div><div class="name"><b>PDF</b>${esc(f.name)}</div><div class="state"></div><div class="tags"></div></div>
      <div class="act"></div><div class="bar indet"><i></i></div><div class="prev"></div>`;
    $("list").prepend(row);
    track(f, row);
  }
}

function track(f, row) {
  const state = row.querySelector(".state"), bar = row.querySelector(".bar"), fill = bar.querySelector("i"),
    act = row.querySelector(".act"), tags = row.querySelector(".tags");
  let id = null;
  const remove = () => { if (id) { fetch("/delete?id=" + id, { method: "POST" }); done.delete(id); refreshZip(); } row.remove(); if (!$("list").children.length) $("results").hidden = true; };
  const closeBtn = '<button class="x" type="button" aria-label="Retirer de la liste" title="Retirer">×</button>';
  const fail = (msg) => {
    state.className = "state bad"; state.textContent = msg; bar.className = "bar bad"; fill.style.width = "100%";
    act.innerHTML = closeBtn; act.querySelector(".x").onclick = remove;
  };
  if (!/\.pdf$/i.test(f.name) && f.type !== "application/pdf") return fail("Ce fichier n'est pas un PDF.");
  if (f.size > limits.max_mb * 1048576) return fail(`Fichier trop lourd (${mb(f.size)}). Limite : ${limits.max_mb} Mo.`);
  state.textContent = `Envoi… (${mb(f.size)})`;
  fetch("/convert?" + query(), { method: "POST", headers: { "X-Filename": encodeURIComponent(f.name) }, body: f })
    .then((r) => r.json().then((j) => ({ ok: r.ok, j })))
    .then(({ ok, j }) => { if (!ok) return fail(j.error || "Fichier refusé."); id = j.id; poll(); })
    .catch(() => fail("Le serveur local ne répond pas."));

  function poll() {
    fetch("/status?id=" + id).then((r) => r.json()).then((s) => {
      if (s.error && s.state !== "error") return fail(s.error);
      if (s.state === "queued") { state.textContent = "En attente…"; }
      else if (s.state === "running") {
        bar.classList.remove("indet");
        const p = s.total ? s.page / s.total : 0;
        fill.style.width = Math.max(4, Math.round(p * 92)) + "%";
        state.textContent = s.page >= s.total && s.total ? "Assemblage du document Word…" : `Page ${s.page} sur ${s.total || "?"}`;
      } else if (s.state === "done") return finish(s);
      else if (s.state === "error") return fail(s.error || "La conversion a échoué.");
      setTimeout(poll, 450);
    }).catch(() => fail("Le serveur local ne répond pas."));
  }

  function finish(s) {
    bar.classList.remove("indet"); bar.classList.add("fin");
    const st = s.stats || {};
    state.className = "state ok";
    state.textContent = `Terminé en ${String(s.seconds).replace(".", ",")} s · ${st.pages} page${st.pages > 1 ? "s" : ""}`;
    tags.innerHTML = Object.entries(st).filter(([k, v]) => v > 0 && k !== "pages")
      .map(([k, v]) => `<span>${v} ${v === 1 ? (SING[k] || k) : k}</span>`).join("");
    act.innerHTML = `<a class="btn" href="/download?id=${id}">Télécharger</a><button class="btn ghost" type="button">Voir l'original</button>${closeBtn}`;
    act.querySelector(".x").onclick = remove;
    act.querySelector(".ghost").onclick = () => {
      const pv = row.querySelector(".prev");
      if (!pv.firstChild) pv.innerHTML = `<img alt="Première page du PDF d'origine" src="/preview?id=${id}&p=0">`; else pv.innerHTML = "";
    };
    done.set(id, s.out_name); refreshZip();
  }
}

function refreshZip() {
  const z = $("zip");
  z.hidden = done.size < 2;
  z.href = "/download_all?ids=" + [...done.keys()].join(",");
}
