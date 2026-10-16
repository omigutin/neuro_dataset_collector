"use strict";
// Веб-интерфейс сборщика кадров: настройки, нарезка с клавишами, итог.

const $ = (sel) => document.querySelector(sel);
const $$ = (sel) => Array.from(document.querySelectorAll(sel));

const state = {
  config: null,
  videos: [],
  video: 0,
  info: null,
  frame: 0,
  playing: false,
  loading: false,
  zones: new Set(),
};

// ---------------------------------------------------------------- общее

async function api(method, url, body) {
  const options = { method, headers: {} };
  if (body !== undefined) {
    options.headers["Content-Type"] = "application/json";
    options.body = JSON.stringify(body);
  }
  const response = await fetch(url, options);
  const data = await response.json().catch(() => ({}));
  if (!response.ok) {
    const detail = data.detail || {};
    const errors = detail.errors || [typeof detail === "string" ? detail : response.statusText];
    throw new Error(errors.join("\n"));
  }
  return data;
}

function toast(text, kind = "") {
  const box = document.createElement("div");
  box.className = kind;
  box.textContent = text;
  const holder = $("#toast");
  holder.prepend(box);
  while (holder.children.length > 3) holder.lastChild.remove();
  setTimeout(() => box.remove(), kind === "bad" ? 7000 : 3500);
}

function showTab(name) {
  $$(".tab").forEach((b) => b.classList.toggle("active", b.dataset.tab === name));
  $$(".panel").forEach((p) => p.classList.toggle("active", p.id === `tab-${name}`));
  if (name === "summary") loadStats();
}

const splitList = (text) => text.split(",").map((s) => s.trim()).filter(Boolean);

// ---------------------------------------------------------------- настройки

const form = () => $("#settings-form");
const field = (name) => form().elements.namedItem(name);

function findRule(list, type) {
  return (list || []).find((r) => r.type === type);
}

function fillForm(config) {
  const f = (name, value) => { const el = field(name); if (el) el.type === "checkbox" ? (el.checked = !!value) : (el.value = value ?? ""); };
  f("task", config.task);
  f("classes", config.classes.join(", "));
  f("save_all_key", config.manual.save_all_key);
  f("on_duplicate", config.manual.on_duplicate);
  f("root", config.catalog.root);
  f("collection", config.catalog.collection);
  f("dedup_scope", config.catalog.dedup_scope);
  f("filename", config.storage.filename);
  f("regions_source", config.regions.source === "api" ? "none" : config.regions.source);
  f("regions_file", config.regions.file);
  f("regions_labels", config.regions.labels.join(", "));
  f("regions_where", Object.entries(config.regions.where).map(([k, v]) => `${k}=${v}`).join(", "));
  f("regions_id_field", config.regions.id_field);
  f("mask_outside", config.regions.mask_outside);
  f("padding", config.regions.padding);
  f("source_path", config.source.path);
  f("step", config.source.step);
  f("model_path", config.model.path);
  f("auto_enabled", config.auto.enabled);
  const t = config.auto.triggers, l = config.auto.limits;
  const conf = findRule(t, "confidence"), audit = findRule(t, "audit");
  f("t_always", !!findRule(t, "always"));
  f("t_conf", !!conf); f("t_conf_min", conf ? conf.min : 0); f("t_conf_max", conf ? conf.max : 0.8);
  f("t_audit", !!audit); f("t_audit_rate", audit ? audit.rate : 0.02);
  const interval = findRule(l, "interval"), scene = findRule(l, "scene_change");
  const quota = findRule(l, "quota"), dedup = findRule(l, "dedup");
  f("l_interval", !!interval && interval.seconds != null); f("l_interval_sec", interval?.seconds ?? 1);
  f("l_scene", !!scene); f("l_scene_diff", scene ? scene.min_diff : 6);
  f("l_quota", !!quota && quota.per_label != null); f("l_quota_n", quota?.per_label ?? 2000);
  f("l_dedup", !!dedup); f("l_dedup_t", dedup ? dedup.threshold : 5);
  f("cons_enabled", config.consistency.enabled);
  f("cons_context", config.consistency.context);
  f("cons_max_run", config.consistency.max_run);
  f("cons_review", config.consistency.review_always.join(", "));
  f("profile", config.runtime.profile);
  renderKeysTable(config.classes, config.manual.keys);
}

function renderKeysTable(classes, keys) {
  const byClass = {};
  Object.entries(keys || {}).forEach(([key, cls]) => (byClass[cls] = key));
  const rows = classes.map((cls, i) => {
    const key = byClass[cls] ?? (i < 9 ? String(i + 1) : "");
    return `<tr><td>${cls}</td><td><input class="short key-input" data-class="${cls}" maxlength="1" value="${key}"></td></tr>`;
  });
  $("#keys-table").innerHTML = classes.length
    ? `<table><tr><th>Класс</th><th>Клавиша</th></tr>${rows.join("")}</table>`
    : `<p class="hint">Укажите классы — появятся клавиши для ручного сохранения.</p>`;
}

function collectForm() {
  const c = structuredClone(state.config);
  const v = (name) => field(name).value.trim();
  const n = (name) => Number(field(name).value);
  const on = (name) => field(name).checked;
  c.task = v("task");
  c.classes = splitList(v("classes"));
  const keys = {};
  $$(".key-input").forEach((el) => { if (el.value.trim() && c.classes.includes(el.dataset.class)) keys[el.value.trim()] = el.dataset.class; });
  c.manual.keys = keys;
  c.manual.save_all_key = v("save_all_key") || "s";
  c.manual.on_duplicate = v("on_duplicate");
  c.catalog.root = v("root") || "datasets";
  c.catalog.collection = v("collection") || "default";
  c.catalog.dedup_scope = v("dedup_scope");
  c.storage.filename = v("filename");
  c.regions.source = v("regions_source");
  c.regions.file = v("regions_file") || null;
  c.regions.labels = splitList(v("regions_labels"));
  c.regions.where = Object.fromEntries(splitList(v("regions_where")).map((p) => p.split("=").map((s) => s.trim())).filter((p) => p.length === 2 && p[0]));
  c.regions.id_field = v("regions_id_field") || null;
  c.regions.mask_outside = on("mask_outside");
  c.regions.padding = n("padding") || 0;
  c.source.path = v("source_path") || null;
  c.source.step = Math.max(1, n("step") || 1);
  c.model.path = v("model_path") || null;
  c.auto.enabled = on("auto_enabled");
  // Правила, не представленные на форме, сохраняются как были.
  const known = ["always", "confidence", "audit"];
  const triggers = c.auto.triggers.filter((r) => !known.includes(r.type));
  if (on("t_always")) triggers.push({ type: "always" });
  if (on("t_conf")) triggers.push({ type: "confidence", min: n("t_conf_min"), max: n("t_conf_max"), include_empty: true });
  if (on("t_audit")) triggers.push({ type: "audit", rate: n("t_audit_rate") });
  c.auto.triggers = triggers;
  const limits = [];
  if (on("l_interval")) limits.push({ type: "interval", seconds: n("l_interval_sec"), frames: null, per: ["source", "region", "label"] });
  if (on("l_scene")) limits.push({ type: "scene_change", min_diff: n("l_scene_diff") });
  if (on("l_quota")) limits.push({ type: "quota", per_label: n("l_quota_n"), total: null });
  if (on("l_dedup")) limits.push({ type: "dedup", threshold: n("l_dedup_t") });
  c.auto.limits = limits;
  c.consistency.enabled = on("cons_enabled");
  c.consistency.context = n("cons_context") || 5;
  c.consistency.max_run = n("cons_max_run") || 2;
  c.consistency.review_always = splitList(v("cons_review"));
  c.consistency.trusted_flips = Object.fromEntries(Object.entries(c.consistency.trusted_flips || {}).filter(([k]) => c.classes.includes(k)));
  c.runtime.profile = v("profile");
  return c;
}

function applyConfig(data) {
  state.config = data.config;
  fillForm(data.config);
  $("#yaml").value = data.yaml;
  $("#config-path").textContent = data.path ? `Настройки: ${data.path}` : "Настройки не сохранены в файл";
  $("#settings-errors").textContent = "";
  renderLegend();
}

async function loadConfig() {
  applyConfig(await api("GET", "/api/config"));
}

async function saveSettings(event) {
  event.preventDefault();
  try {
    applyConfig(await api("PUT", "/api/config", collectForm()));
    $("#settings-status").textContent = "Сохранено ✓";
    setTimeout(() => ($("#settings-status").textContent = ""), 2500);
  } catch (error) {
    $("#settings-errors").textContent = error.message;
  }
}

// ---------------------------------------------------------------- выбор файлов

const picker = { kind: "dir", onPick: null, path: null };

async function openPicker(kind, onPick, start) {
  picker.kind = kind;
  picker.onPick = onPick;
  await browse(start || null);
  $("#picker").showModal();
}

async function browse(path) {
  const query = new URLSearchParams({ kind: picker.kind });
  if (path) query.set("path", path);
  try {
    const data = await api("GET", `/api/browse?${query}`);
    picker.path = data.path;
    $("#picker-path").textContent = data.path || "Выберите диск";
    $("#picker-up").disabled = !data.parent && !data.path;
    $("#picker-up").onclick = () => browse(data.parent);
    const join = (name) => (data.path ? `${data.path.replace(/[\\/]$/, "")}${data.path.includes("\\") ? "\\" : "/"}${name}` : name);
    const items = data.dirs.map((d) => `<li class="dir" data-path="${join(d)}">${d}</li>`)
      .concat(data.files.map((f) => `<li class="file" data-path="${join(f)}">${f}</li>`));
    $("#picker-list").innerHTML = items.join("") || `<li class="muted">пусто</li>`;
    $$("#picker-list li.dir").forEach((li) => (li.onclick = () => browse(li.dataset.path)));
    $$("#picker-list li.file").forEach((li) => (li.onclick = () => choose(li.dataset.path)));
    $("#picker-choose-dir").disabled = !data.path;
  } catch (error) {
    toast(error.message, "bad");
  }
}

function choose(path) {
  $("#picker").close();
  if (picker.onPick) picker.onPick(path);
}

function bindPickers() {
  $$("[data-pick]").forEach((button) => {
    button.addEventListener("click", () => {
      const kind = button.dataset.pick;
      const action = button.dataset.action;
      const target = button.dataset.target ? field(button.dataset.target) : button.dataset.targetId ? $(`#${button.dataset.targetId}`) : null;
      const start = target && target.value ? target.value.replace(/[\\/][^\\/]*\.[^\\/]*$/, "") : null;
      openPicker(kind, async (path) => {
        if (target) target.value = path;
        if (action === "open-config") {
          try { applyConfig(await api("POST", "/api/config/open", { path })); toast("Настройки открыты", "ok"); }
          catch (error) { toast(error.message, "bad"); }
        }
        if (action === "open-source") openSource(path);
      }, start);
    });
  });
  $("#picker-choose-dir").onclick = () => choose(picker.path);
  $("#picker-cancel").onclick = () => $("#picker").close();
}

// ---------------------------------------------------------------- нарезка

async function openSource(path) {
  try {
    const data = await api("POST", "/api/source", { path: path || null });
    state.videos = data.videos;
    $("#video-select").innerHTML = data.videos.map((name, i) => `<option value="${i}">${name}</option>`).join("");
    if (!data.videos.length) { toast("Видео не найдены", "bad"); return; }
    await selectVideo(0);
  } catch (error) {
    toast(error.message, "bad");
  }
}

async function selectVideo(number) {
  pause();
  state.video = number;
  state.info = await api("GET", `/api/video/${number}`);
  state.zones = new Set(state.info.regions.map((r) => r.id));
  $("#video-info").textContent = `${state.info.width}×${state.info.height}, ${state.info.fps.toFixed(1)} к/с, кадров: ${state.info.frames}`;
  $("#seek").max = Math.max(0, state.info.frames - 1);
  renderZones();
  showFrame(0);
}

function renderZones() {
  const regions = state.info ? state.info.regions : [];
  $("#zones").innerHTML = regions.length
    ? "Сохранять зоны: " + regions.map((r) => `<label class="check"><input type="checkbox" data-zone="${r.id}" checked> ${r.id}</label>`).join("")
    : `<span class="muted">Зон нет — сохраняется весь кадр</span>`;
  $$("#zones input").forEach((el) => el.addEventListener("change", () => {
    el.checked ? state.zones.add(el.dataset.zone) : state.zones.delete(el.dataset.zone);
  }));
}

function renderLegend() {
  if (!state.config) return;
  const keys = Object.entries(state.config.manual.keys);
  const buttons = keys.map(([key, cls]) => `<button data-label="${cls}"><kbd>${key}</kbd>${cls}</button>`);
  buttons.push(`<button data-label=""><kbd>${state.config.manual.save_all_key}</kbd>${state.config.task === "classify" ? "без класса" : "сохранить"}</button>`);
  $("#legend").innerHTML = buttons.join("") +
    `<span class="note">Пробел — пауза · ←/→ — шаг · Shift+←/→ — 10 шагов · Shift+клавиша — сохранить даже похожий</span>`;
  $$("#legend button").forEach((b) => (b.onclick = () => saveCurrent(b.dataset.label || null, false)));
}

const step = () => (state.config ? state.config.source.step : 1);

function showFrame(index) {
  if (!state.info) return;
  const last = Math.max(0, state.info.frames - 1);
  index = Math.min(Math.max(0, index), last || index);
  state.frame = index;
  state.loading = true;
  $("#seek").value = index;
  $("#frame-number").value = index;
  $("#frame").src = `/api/video/${state.video}/frame/${index}?t=${Date.now()}`;
}

function onFrameLoaded() {
  state.loading = false;
  if (!state.playing) return;
  const fps = state.info.fps || 25;
  const delay = Math.max(15, (1000 * step()) / fps / Number($("#speed").value));
  setTimeout(() => { if (state.playing) showFrame(state.frame + step()); }, delay);
}

function onFrameError() {
  state.loading = false;
  if (state.playing) { pause(); toast("Конец видео"); }
}

function play() {
  if (!state.info) return;
  if ($("#step-mode").checked) { showFrame(state.frame + step()); return; }
  state.playing = true;
  $("#play").textContent = "⏸";
  if (!state.loading) showFrame(state.frame + step());
}

function pause() {
  state.playing = false;
  $("#play").textContent = "▶";
}

async function saveCurrent(label, force) {
  if (!state.info) { toast("Сначала откройте видео", "bad"); return; }
  const regions = state.info.regions.length ? Array.from(state.zones) : null;
  if (regions && !regions.length) { toast("Не выбрано ни одной зоны", "bad"); return; }
  try {
    const data = await api("POST", "/api/save", { video: state.video, frame: state.frame, label, regions, force });
    data.results.forEach((r) => toast(r.message, r.ok ? "ok" : "bad"));
    loadHistory();
    if ($("#step-mode").checked) showFrame(state.frame + step());
  } catch (error) {
    toast(error.message, "bad");
  }
}

function fileLink(path, text) {
  const url = `/api/file?path=${encodeURIComponent(path)}`;
  return `<a href="${url}" target="_blank">${text}</a><img src="${url}" loading="lazy" alt="">`;
}

async function loadHistory() {
  const data = await api("GET", "/api/history");
  $("#history").innerHTML = data.items.map((item) => {
    let extra = "";
    if (item.ok && item.path) extra = fileLink(item.path, "открыть");
    else if (item.similar_to) extra = `похож на: ${fileLink(item.similar_to, item.similar_to)}`;
    return `<li class="${item.ok ? "ok" : "bad"}">[${item.region}] ${item.message}${extra ? "<br>" + extra : ""}</li>`;
  }).join("");
}

function onKey(event) {
  if (!$("#tab-slicer").classList.contains("active")) return;
  if (["INPUT", "SELECT", "TEXTAREA"].includes(event.target.tagName) && event.target.type !== "range" && event.target.type !== "checkbox") return;
  if ($("#picker").open) return;
  const key = event.key;
  const big = event.shiftKey ? 10 : 1;
  if (key === " ") { event.preventDefault(); state.playing ? pause() : play(); return; }
  if (key === "ArrowRight") { event.preventDefault(); pause(); showFrame(state.frame + step() * big); return; }
  if (key === "ArrowLeft") { event.preventDefault(); pause(); showFrame(state.frame - step() * big); return; }
  if (!state.config) return;
  const plain = event.code.startsWith("Digit") ? event.code.slice(5) : event.code.startsWith("Key") ? event.code.slice(3).toLowerCase() : key.toLowerCase();
  const label = state.config.manual.keys[plain] ?? state.config.manual.keys[key];
  if (label) { event.preventDefault(); saveCurrent(label, event.shiftKey); return; }
  if (plain === state.config.manual.save_all_key.toLowerCase()) { event.preventDefault(); saveCurrent(null, event.shiftKey); }
}

// ---------------------------------------------------------------- автонарезка

let autoTimer = null;

async function autoStart() {
  try {
    await api("POST", "/api/auto/start");
    pollAuto();
  } catch (error) {
    toast(error.message, "bad");
  }
}

async function pollAuto() {
  clearTimeout(autoTimer);
  const s = await api("GET", "/api/auto/status");
  const line = s.running
    ? `Идёт: видео ${s.video_number}/${s.videos_total} (${s.video}), сохранено ${s.saved}`
    : s.error ? `Ошибка: ${s.error}` : s.summary;
  $("#auto-status").textContent = line || "";
  if (s.running) autoTimer = setTimeout(pollAuto, 1000);
  else loadHistory();
}

// ---------------------------------------------------------------- итог

function table(obj, title) {
  const rows = Object.entries(obj || {}).map(([k, v]) => `<tr><td>${k || "(без класса)"}</td><td>${v}</td></tr>`);
  return `<h3>${title}</h3>` + (rows.length ? `<table>${rows.join("")}</table>` : `<p class="muted">нет</p>`);
}

const REASON_NAMES = {
  duplicate: "похож на сохранённый", interval: "слишком часто", quota: "набрано достаточно",
  scene_unchanged: "картинка не изменилась", rate_limit: "лимит в час", disk_full: "мало места на диске",
  queue_full: "очередь записи переполнена", disabled: "сборщик отключён", empty_region: "зона вне кадра",
};
const STATUS_NAMES = { pending: "ждут проверки", suspect: "подозрительные", verified: "проверены", rejected: "отклонены", failed: "ошибка записи" };

async function loadStats() {
  try {
    const s = await api("GET", "/api/stats");
    const statuses = Object.fromEntries(Object.entries(s.statuses).map(([k, v]) => [STATUS_NAMES[k] || k, v]));
    $("#stats").innerHTML = `<p>Коллекция <b>${s.collection}</b> в <code>${s.root}</code></p>` +
      table(s.labels, "По классам") + table(statuses, "По состояниям") + table(s.session, "За этот запуск") +
      table(Object.fromEntries(Object.entries(s.rejected).map(([k, v]) => [REASON_NAMES[k] || k, v])), "Отказы за этот запуск");
    if (!$("#export-dir").value) $("#export-dir").value = `${s.root}${s.root.includes("\\") ? "\\" : "/"}_export`;
  } catch (error) {
    $("#stats").textContent = error.message;
  }
}

async function runSync() {
  if (!confirm("Вы просмотрели кадры глазами и разложили их по папкам? Сверка отметит их как проверенные.")) return;
  try {
    const r = await api("POST", "/api/sync");
    let text = r.summary;
    if (r.unknown_files.length) text += "\n\nФайлы без истории:\n" + r.unknown_files.join("\n");
    $("#sync-result").textContent = text;
    loadStats();
  } catch (error) {
    $("#sync-result").textContent = error.message;
  }
}

async function runExport() {
  try {
    const r = await api("POST", "/api/export", {
      out_dir: $("#export-dir").value.trim(),
      only_new: !$("#export-all").checked,
      include_pending: $("#export-pending").checked,
    });
    $("#export-result").textContent = r.summary;
  } catch (error) {
    $("#export-result").textContent = error.message;
  }
}

// ---------------------------------------------------------------- запуск

function init() {
  $$(".tab").forEach((b) => (b.onclick = () => showTab(b.dataset.tab)));
  form().addEventListener("submit", saveSettings);
  field("classes").addEventListener("change", () => renderKeysTable(splitList(field("classes").value), state.config ? state.config.manual.keys : {}));
  $("#apply-yaml").onclick = async () => {
    try { applyConfig(await api("PUT", "/api/config/yaml", { text: $("#yaml").value })); toast("Настройки применены", "ok"); }
    catch (error) { $("#settings-errors").textContent = error.message; }
  };
  $("#save-as").onclick = async () => {
    const path = $("#save-as-path").value.trim();
    if (!path) return;
    try { applyConfig(await api("POST", "/api/config/save-as", { path })); toast("Сохранено", "ok"); }
    catch (error) { toast(error.message, "bad"); }
  };
  bindPickers();
  $("#open-source").onclick = () => openSource(null);
  $("#video-select").onchange = (e) => selectVideo(Number(e.target.value));
  $("#frame").addEventListener("load", onFrameLoaded);
  $("#frame").addEventListener("error", onFrameError);
  $("#play").onclick = () => (state.playing ? pause() : play());
  $("#next").onclick = () => { pause(); showFrame(state.frame + step()); };
  $("#prev").onclick = () => { pause(); showFrame(state.frame - step()); };
  $("#seek").addEventListener("input", (e) => { pause(); showFrame(Number(e.target.value)); });
  $("#frame-number").addEventListener("change", (e) => { pause(); showFrame(Number(e.target.value)); });
  $("#auto-start").onclick = autoStart;
  $("#auto-stop").onclick = async () => { await api("POST", "/api/auto/stop"); pollAuto(); };
  $("#refresh-stats").onclick = loadStats;
  $("#sync").onclick = runSync;
  $("#export").onclick = runExport;
  document.addEventListener("keydown", onKey);
  loadConfig().then(loadHistory).then(pollAuto).catch((error) => toast(error.message, "bad"));
}

init();
