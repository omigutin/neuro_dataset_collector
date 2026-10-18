"use strict";
// Веб-интерфейс сборщика датасета: нарезка одного видео, настройки, итог.

const $ = (sel) => document.querySelector(sel);
const $$ = (sel) => Array.from(document.querySelectorAll(sel));

const state = {
  config: null,
  info: null,          // сведения об открытом видео
  frame: 0,
  playing: false,
  loading: false,
  zones: new Set(),
  positionTimer: null,
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

const escapeHtml = (text) => String(text).replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));

// Имя класса или метки: заглавная латиница, цифры, «_» и «-»; первый символ — буква или цифра.
function cleanName(value) {
  return value.toUpperCase().replace(/\s+/g, "_").replace(/[^A-Z0-9_-]/g, "").replace(/^[_-]+/, "");
}

// Клавиша: одна цифра или латинская буква (в нижнем регистре).
function cleanKey(value) {
  const key = value.toLowerCase().replace(/[^0-9a-z]/g, "");
  return key.slice(-1);
}

// ---------------------------------------------------------------- выбор файлов

// Системное окно выбора; если недоступно — запасной выбор в браузере.
async function pickPath(kind, filter, initial, title) {
  try {
    const data = await api("POST", "/api/pick", { kind, filter: filter || "any", initial: initial || null, title: title || "" });
    if (data.available) return data.path || null;
  } catch (error) {
    toast(error.message, "bad");
  }
  return browserPick(kind === "dir" ? "dir" : (filter || "any"), initial);
}

const picker = { kind: "dir", resolve: null, path: null };

function browserPick(kind, initial) {
  picker.kind = kind;
  return new Promise((resolve) => {
    picker.resolve = resolve;
    browse(initial ? initial.replace(/[\\/][^\\/]*\.[^\\/]*$/, "") : null).then(() => $("#picker").showModal());
  });
}

async function browse(path) {
  const query = new URLSearchParams({ kind: picker.kind });
  if (path) query.set("path", path);
  try {
    const data = await api("GET", `/api/browse?${query}`);
    picker.path = data.path;
    $("#picker-path").textContent = data.path || "Выберите диск";
    $("#picker-up").onclick = () => browse(data.parent);
    const sep = data.path && data.path.includes("\\") ? "\\" : "/";
    const join = (name) => (data.path ? `${data.path.replace(/[\\/]$/, "")}${sep}${name}` : name);
    const items = data.dirs.map((d) => `<li class="dir" data-path="${escapeHtml(join(d))}">${escapeHtml(d)}</li>`)
      .concat(data.files.map((f) => `<li class="file" data-path="${escapeHtml(join(f))}">${escapeHtml(f)}</li>`));
    $("#picker-list").innerHTML = items.join("") || `<li class="muted">пусто</li>`;
    $$("#picker-list li.dir").forEach((li) => (li.onclick = () => browse(li.dataset.path)));
    $$("#picker-list li.file").forEach((li) => (li.onclick = () => finishPick(li.dataset.path)));
    $("#picker-choose-dir").style.display = picker.kind === "dir" && data.path ? "" : "none";
  } catch (error) {
    toast(error.message, "bad");
  }
}

function finishPick(path) {
  $("#picker").close();
  if (picker.resolve) picker.resolve(path);
  picker.resolve = null;
}

// ---------------------------------------------------------------- настройки

const form = () => $("#settings-form");
const field = (name) => form().elements.namedItem(name);
const findRule = (list, type) => (list || []).find((r) => r.type === type);

function classRow(name = "", key = "") {
  const tr = document.createElement("tr");
  tr.innerHTML = `<td><input class="name-input" value="${escapeHtml(name)}" placeholder="ИМЯ_КЛАССА"></td>
    <td><input class="key-input short key" maxlength="2" value="${escapeHtml(key)}"></td>
    <td><button type="button" class="remove" title="Удалить">−</button></td>`;
  bindNameRow(tr);
  tr.querySelector(".key-input").addEventListener("input", (e) => { e.target.value = cleanKey(e.target.value); });
  return tr;
}

function labelRow(name = "") {
  const tr = document.createElement("tr");
  tr.innerHTML = `<td><input class="name-input" value="${escapeHtml(name)}" placeholder="CROP"></td>
    <td><button type="button" class="remove" title="Удалить">−</button></td>`;
  bindNameRow(tr);
  return tr;
}

function bindNameRow(tr) {
  const input = tr.querySelector(".name-input");
  input.addEventListener("input", () => {
    const pos = input.selectionStart;
    const before = input.value.length;
    input.value = cleanName(input.value);
    input.setSelectionRange(Math.max(0, pos - (before - input.value.length)), Math.max(0, pos - (before - input.value.length)));
    if (tr.closest("#classes-body")) renderReviewAlways();
  });
  tr.querySelector(".remove").onclick = () => { tr.remove(); renderReviewAlways(); };
}

function renderClasses(classes, keys) {
  const byClass = {};
  Object.entries(keys || {}).forEach(([key, cls]) => (byClass[cls] = key));
  const body = $("#classes-body");
  body.innerHTML = "";
  classes.forEach((cls) => body.appendChild(classRow(cls, byClass[cls] || "")));
  if (!classes.length) body.appendChild(classRow());
}

function renderLabels(labels) {
  const body = $("#labels-body");
  body.innerHTML = "";
  labels.forEach((label) => body.appendChild(labelRow(label.toUpperCase())));
}

function currentClasses() {
  return $$("#classes-body .name-input").map((el) => el.value.trim()).filter(Boolean);
}

function renderReviewAlways(selected) {
  const current = selected || $$("#review-always input:checked").map((el) => el.value);
  $("#review-always").innerHTML = currentClasses().map((cls) =>
    `<label class="check chip"><input type="checkbox" value="${escapeHtml(cls)}" ${current.includes(cls) ? "checked" : ""}> ${escapeHtml(cls)}</label>`).join("");
}

function fillForm(config) {
  const f = (name, value) => { const el = field(name); if (el) el.type === "checkbox" ? (el.checked = !!value) : (el.value = value ?? ""); };
  f("task", config.task);
  renderClasses(config.classes, config.manual.keys);
  f("save_all_key", config.manual.save_all_key);
  f("on_duplicate", config.manual.on_duplicate);
  f("next_to_video", config.catalog.next_to_video);
  f("root", config.catalog.root);
  f("collection", config.catalog.collection);
  f("dedup_scope", config.catalog.dedup_scope);
  f("filename", config.storage.filename);
  f("regions_source", config.regions.source === "api" ? "none" : config.regions.source);
  f("regions_search_dir", config.regions.search_dir);
  renderLabels(config.regions.labels);
  f("regions_id_field", config.regions.id_field);
  f("mask_outside", config.regions.mask_outside);
  f("padding", config.regions.padding);
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
  renderReviewAlways(config.consistency.review_always);
  f("profile", config.runtime.profile);
  toggleRoot();
}

function toggleRoot() {
  const nextToVideo = field("next_to_video").checked;
  field("root").disabled = nextToVideo;
  field("root").closest("label").querySelector("button").disabled = nextToVideo;
}

// Проверка таблиц перед сохранением: имена и клавиши без повторов.
function validateTables() {
  const errors = [];
  const names = $$("#classes-body .name-input").map((el) => el.value.trim());
  const keys = $$("#classes-body .key-input").map((el) => el.value.trim());
  $$("#classes-body .name-input, #classes-body .key-input, #labels-body .name-input").forEach((el) => el.classList.remove("invalid"));
  names.forEach((name, i) => {
    const input = $$("#classes-body .name-input")[i];
    if (!name && keys[i]) { input.classList.add("invalid"); errors.push(`Строка ${i + 1}: пустое имя класса`); }
    if (name && names.indexOf(name) !== i) { input.classList.add("invalid"); errors.push(`Класс ${name} указан дважды`); }
  });
  const saveAll = field("save_all_key").value.trim();
  keys.forEach((key, i) => {
    if (!key) return;
    const input = $$("#classes-body .key-input")[i];
    if (keys.indexOf(key) !== i) { input.classList.add("invalid"); errors.push(`Клавиша «${key}» назначена дважды`); }
    if (key === saveAll) { input.classList.add("invalid"); errors.push(`Клавиша «${key}» уже занята под «без класса»`); }
  });
  const labels = $$("#labels-body .name-input").map((el) => el.value.trim());
  labels.forEach((label, i) => {
    if (label && labels.indexOf(label) !== i) { $$("#labels-body .name-input")[i].classList.add("invalid"); errors.push(`Метка ${label} указана дважды`); }
  });
  return errors;
}

function collectForm() {
  const c = structuredClone(state.config);
  const v = (name) => field(name).value.trim();
  const n = (name) => Number(field(name).value);
  const on = (name) => field(name).checked;
  c.task = v("task");
  const keys = {};
  c.classes = [];
  $$("#classes-body tr").forEach((tr) => {
    const name = tr.querySelector(".name-input").value.trim();
    const key = tr.querySelector(".key-input").value.trim();
    if (!name) return;
    c.classes.push(name);
    if (key) keys[key] = name;
  });
  c.manual.keys = keys;
  c.manual.save_all_key = v("save_all_key") || "s";
  c.manual.on_duplicate = v("on_duplicate");
  c.catalog.next_to_video = on("next_to_video");
  c.catalog.root = v("root") || c.catalog.root || "datasets";
  c.catalog.collection = v("collection");
  c.catalog.dedup_scope = v("dedup_scope");
  c.storage.filename = v("filename");
  c.regions.source = v("regions_source");
  if (c.regions.source === "file" && !c.regions.file) c.regions.source = "none";
  c.regions.search_dir = v("regions_search_dir") || null;
  c.regions.labels = $$("#labels-body .name-input").map((el) => el.value.trim()).filter(Boolean);
  c.regions.id_field = v("regions_id_field") || null;
  c.regions.mask_outside = on("mask_outside");
  c.regions.padding = n("padding") || 0;
  c.source.step = Math.max(1, n("step") || 1);
  c.model.path = v("model_path") || null;
  c.auto.enabled = on("auto_enabled");
  // Правила, которых нет на форме, сохраняются как были.
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
  // Правила по классам и последовательности — только для существующих классов.
  c.auto.per_label = Object.fromEntries(Object.entries(c.auto.per_label || {}).filter(([k]) => c.classes.includes(k) || k === c.unsorted_label));
  c.consistency.enabled = on("cons_enabled");
  c.consistency.context = n("cons_context") || 5;
  c.consistency.max_run = n("cons_max_run") || 2;
  c.consistency.review_always = $$("#review-always input:checked").map((el) => el.value).filter((x) => c.classes.includes(x));
  c.consistency.trusted_flips = Object.fromEntries(Object.entries(c.consistency.trusted_flips || {})
    .filter(([k]) => c.classes.includes(k)).map(([k, list]) => [k, list.filter((x) => c.classes.includes(x))]));
  c.runtime.profile = v("profile");
  return c;
}

function applyConfig(data) {
  state.config = data.config;
  fillForm(data.config);
  $("#yaml").value = data.yaml;
  $("#config-path").textContent = data.path;
  $("#settings-file").textContent = data.path;
  $("#settings-errors").textContent = "";
  renderLegend();
}

async function loadConfig() {
  applyConfig(await api("GET", "/api/config"));
}

async function saveSettings(event) {
  if (event) event.preventDefault();
  const errors = validateTables();
  if (errors.length) { $("#settings-errors").textContent = errors.join("\n"); return; }
  try {
    applyConfig(await api("PUT", "/api/config", collectForm()));
    $("#settings-status").textContent = "Сохранено ✓";
    setTimeout(() => ($("#settings-status").textContent = ""), 2500);
    await refreshVideo();
  } catch (error) {
    $("#settings-errors").textContent = error.message;
  }
}

// ---------------------------------------------------------------- видео

async function chooseVideo() {
  const path = await pickPath("file", "video", state.info && state.info.video, "Выберите видео");
  if (!path) return;
  try {
    pause();
    const info = await api("POST", "/api/video/open", { path });
    if (info.suggested_regions) toast(`Разметка по номеру камеры: ${info.suggested_regions}`, "ok");
    await applyVideo(info, 0);
  } catch (error) {
    toast(error.message, "bad");
  }
}

async function chooseRegions() {
  const start = (state.info && state.info.regions_file) || (state.config && state.config.regions.search_dir) || (state.info && state.info.video);
  const path = await pickPath("file", "json", start, "Выберите файл разметки (LabelMe json)");
  if (!path) return;
  try {
    await applyVideo(await api("POST", "/api/video/regions", { path }), state.frame);
    await loadConfig();
  } catch (error) {
    toast(error.message, "bad");
  }
}

async function noRegions() {
  try {
    await applyVideo(await api("POST", "/api/video/regions", { path: null }), state.frame);
    await loadConfig();
  } catch (error) {
    toast(error.message, "bad");
  }
}

async function refreshVideo() {
  try {
    await applyVideo(await api("GET", "/api/video"), state.frame);
  } catch (error) {
    toast(error.message, "bad");
  }
}

async function applyVideo(info, frame) {
  state.info = info;
  $("#video-path").textContent = info.video || "не выбрано";
  $("#video-path").classList.toggle("muted", !info.video);
  const useRegions = info.regions_source === "file" && info.regions_file;
  $("#regions-path").textContent = useRegions ? info.regions_file : "весь кадр";
  $("#regions-path").classList.toggle("muted", !useRegions);
  $("#dataset-path").textContent = info.dataset_dir || "";
  $("#frame-placeholder").style.display = info.video ? "none" : "";
  $("#frame").style.display = info.video ? "" : "none";
  if (!info.video) { $("#video-info").textContent = ""; $("#zones").innerHTML = ""; return; }
  state.zones = new Set((info.regions || []).map((r) => r.id));
  $("#video-info").textContent = `${info.name}: ${info.width}×${info.height}, ${Number(info.fps).toFixed(1)} к/с, кадров: ${info.frames}`;
  $("#seek").max = Math.max(0, info.frames - 1);
  renderZones();
  showFrame(frame ?? info.frame ?? 0);
  loadHistory();
}

function renderZones() {
  const regions = state.info ? state.info.regions || [] : [];
  $("#zones").innerHTML = regions.length
    ? "Сохранять зоны: " + regions.map((r) => `<label class="check"><input type="checkbox" data-zone="${escapeHtml(r.id)}" checked> ${escapeHtml(r.id)}</label>`).join("")
    : `<span class="muted">Зон нет — сохраняется весь кадр</span>`;
  $$("#zones input").forEach((el) => el.addEventListener("change", () => {
    el.checked ? state.zones.add(el.dataset.zone) : state.zones.delete(el.dataset.zone);
  }));
}

function renderLegend() {
  if (!state.config) return;
  const keys = Object.entries(state.config.manual.keys);
  const buttons = keys.map(([key, cls]) => `<button data-label="${escapeHtml(cls)}"><kbd>${escapeHtml(key)}</kbd>${escapeHtml(cls)}</button>`);
  buttons.push(`<button data-label=""><kbd>${escapeHtml(state.config.manual.save_all_key)}</kbd>${state.config.task === "classify" ? "без класса" : "сохранить"}</button>`);
  $("#legend").innerHTML = buttons.join("") +
    `<span class="note">Пробел — пауза · ←/→ — шаг · Shift+←/→ — 10 шагов · Shift+клавиша — сохранить даже похожий</span>`;
  $$("#legend button").forEach((b) => (b.onclick = () => saveCurrent(b.dataset.label || null, false)));
}

const step = () => (state.config ? state.config.source.step : 1);

function showFrame(index) {
  if (!state.info || !state.info.video) return;
  const last = Math.max(0, state.info.frames - 1);
  index = Math.min(Math.max(0, index), last || index);
  state.frame = index;
  state.loading = true;
  $("#seek").value = index;
  $("#frame-number").value = index;
  $("#frame").src = `/api/video/frame/${index}?t=${Date.now()}`;
  rememberPosition();
}

// Запоминаем позицию не чаще раза в 2 секунды — чтобы при следующем запуске продолжить с того же места.
function rememberPosition() {
  clearTimeout(state.positionTimer);
  state.positionTimer = setTimeout(() => api("POST", "/api/video/position", { frame: state.frame }).catch(() => {}), 2000);
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
  if (!state.info || !state.info.video) return;
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
  if (!state.info || !state.info.video) { toast("Сначала выберите видео", "bad"); return; }
  const regions = state.info.regions.length ? Array.from(state.zones) : null;
  if (regions && !regions.length) { toast("Не выбрано ни одной зоны", "bad"); return; }
  try {
    const data = await api("POST", "/api/save", { frame: state.frame, label, regions, force });
    data.results.forEach((r) => toast(r.message, r.ok ? "ok" : "bad"));
    loadHistory();
    if ($("#step-mode").checked) showFrame(state.frame + step());
  } catch (error) {
    toast(error.message, "bad");
  }
}

function fileLink(path, text) {
  const url = `/api/file?path=${encodeURIComponent(path)}`;
  return `<a href="${url}" target="_blank">${escapeHtml(text)}</a><img src="${url}" loading="lazy" alt="">`;
}

async function loadHistory() {
  const data = await api("GET", "/api/history");
  $("#history").innerHTML = data.items.map((item) => {
    let extra = "";
    if (item.ok && item.path) extra = fileLink(item.path, "открыть");
    else if (item.similar_to) extra = `похож на: ${fileLink(item.similar_to, item.similar_to.split(/[\\/]/).pop())}`;
    return `<li class="${item.ok ? "ok" : "bad"}">[${escapeHtml(item.region)}] ${escapeHtml(item.message)}${extra ? "<br>" + extra : ""}</li>`;
  }).join("");
}

function onKey(event) {
  if (!$("#tab-slicer").classList.contains("active")) return;
  if (["INPUT", "SELECT", "TEXTAREA"].includes(event.target.tagName) && !["range", "checkbox"].includes(event.target.type)) return;
  if ($("#picker").open) return;
  const key = event.key;
  const big = event.shiftKey ? 10 : 1;
  if (key === " ") { event.preventDefault(); state.playing ? pause() : play(); return; }
  if (key === "ArrowRight") { event.preventDefault(); pause(); showFrame(state.frame + step() * big); return; }
  if (key === "ArrowLeft") { event.preventDefault(); pause(); showFrame(state.frame - step() * big); return; }
  if (!state.config) return;
  const plain = event.code.startsWith("Digit") ? event.code.slice(5) : event.code.startsWith("Key") ? event.code.slice(3).toLowerCase() : key.toLowerCase();
  const label = state.config.manual.keys[plain];
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
  const line = s.running ? `Идёт: ${s.video}, сохранено ${s.saved}` : s.error ? `Ошибка: ${s.error}` : s.summary;
  $("#auto-status").textContent = line || "";
  if (s.running) autoTimer = setTimeout(pollAuto, 1000);
  else if (state.info && state.info.video) loadHistory();
}

// ---------------------------------------------------------------- итог

function table(obj, title) {
  const rows = Object.entries(obj || {}).map(([k, v]) => `<tr><td>${escapeHtml(k || "(без класса)")}</td><td>${v}</td></tr>`);
  return `<h3>${title}</h3>` + (rows.length ? `<table>${rows.join("")}</table>` : `<p class="muted">нет</p>`);
}

const REASON_NAMES = {
  duplicate: "похож на сохранённый", interval: "слишком часто", quota: "набрано достаточно",
  scene_unchanged: "картинка не изменилась", rate_limit: "лимит в час", disk_full: "мало места на диске",
  queue_full: "очередь записи переполнена", disabled: "сборщик отключён", empty_region: "зона вне кадра",
};
const STATUS_NAMES = { pending: "ждут проверки", suspect: "подозрительные", verified: "проверены", rejected: "отклонены", failed: "ошибка записи" };
const rename = (obj, names) => Object.fromEntries(Object.entries(obj || {}).map(([k, v]) => [names[k] || k, v]));

async function loadStats() {
  try {
    const s = await api("GET", "/api/stats");
    $("#stats").innerHTML = `<p>Папка: <code>${escapeHtml(s.root)}</code></p>` +
      table(s.labels, "По классам") + table(rename(s.statuses, STATUS_NAMES), "По состояниям") +
      table(s.session, "За этот запуск") + table(rename(s.rejected, REASON_NAMES), "Отказы за этот запуск");
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

function bindPickButtons() {
  $$("[data-pick]").forEach((button) => {
    button.addEventListener("click", async () => {
      const target = button.dataset.target ? field(button.dataset.target) : $(`#${button.dataset.targetId}`);
      const path = await pickPath(button.dataset.pick, button.dataset.filter, target.value);
      if (path) target.value = path;
    });
  });
}

function init() {
  $$(".tab").forEach((b) => (b.onclick = () => showTab(b.dataset.tab)));
  form().addEventListener("submit", saveSettings);
  field("next_to_video").addEventListener("change", toggleRoot);
  field("save_all_key").addEventListener("input", (e) => { e.target.value = cleanKey(e.target.value); });
  $("#add-class").onclick = () => { $("#classes-body").appendChild(classRow()).querySelector("input").focus(); };
  $("#add-label").onclick = () => { $("#labels-body").appendChild(labelRow()).querySelector("input").focus(); };
  $("#apply-yaml").onclick = async () => {
    try { applyConfig(await api("PUT", "/api/config/yaml", { text: $("#yaml").value })); toast("Настройки применены", "ok"); await refreshVideo(); }
    catch (error) { $("#settings-errors").textContent = error.message; }
  };
  $("#open-config").onclick = async () => {
    const path = await pickPath("file", "yaml", state.config && $("#settings-file").textContent, "Открыть файл настроек");
    if (!path) return;
    try { applyConfig(await api("POST", "/api/config/open", { path })); toast("Настройки открыты", "ok"); await refreshVideo(); }
    catch (error) { toast(error.message, "bad"); }
  };
  $("#save-config-as").onclick = async () => {
    const path = await pickPath("save", "yaml", $("#settings-file").textContent, "Сохранить настройки как");
    if (!path) return;
    try { applyConfig(await api("POST", "/api/config/save-as", { path })); toast("Сохранено", "ok"); }
    catch (error) { toast(error.message, "bad"); }
  };
  bindPickButtons();
  $("#picker-choose-dir").onclick = () => finishPick(picker.path);
  $("#picker-cancel").onclick = () => finishPick(null);
  $("#pick-video").onclick = chooseVideo;
  $("#pick-regions").onclick = chooseRegions;
  $("#no-regions").onclick = noRegions;
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
  loadConfig()
    .then(() => api("GET", "/api/video"))
    .then((info) => applyVideo(info, info.frame))
    .then(pollAuto)
    .catch((error) => toast(error.message, "bad"));
}

init();
