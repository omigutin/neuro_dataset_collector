"use strict";
// Веб-интерфейс сборщика датасета: плейлист и нарезка видео, правка зон, настройки, итог.

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
  playlist: { dir: null, items: [] },
  edit: null,          // правка зон: { regions: [{id, shape, points}], active, drag }
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

// Системное окно выбора; запасной выбор в браузере — только если системное окно открыть нельзя.
async function pickPath(kind, filter, initial, title) {
  let data;
  try {
    data = await api("POST", "/api/pick", { kind, filter: filter || "any", initial: initial || null, title: title || "" });
  } catch (error) {
    toast(error.message, "bad");
    return null;
  }
  if (data.available) return data.path || null;
  if (kind === "save") { toast("Окно сохранения недоступно: укажите путь в настройках вручную", "bad"); return null; }
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
  if (path) await openVideo(path);
}

async function openVideo(path) {
  if (state.edit && !confirm("Зоны изменены, но не применены. Перейти к другому видео без сохранения?")) return;
  stopEdit();
  try {
    pause();
    const info = await api("POST", "/api/video/open", { path });
    if (info.regions_origin === "video") toast(`Разметка рядом с видео: ${info.regions_file}`, "ok");
    else if (info.suggested_regions) toast(`Разметка по номеру камеры: ${info.suggested_regions}`, "ok");
    await applyVideo(info, info.frame);
    renderPlaylist();
  } catch (error) {
    toast(error.message, "bad");
  }
}

// ---------------------------------------------------------------- плейлист

async function chooseFolder() {
  const path = await pickPath("dir", null, state.playlist.dir || (state.info && state.info.video), "Папка с видео");
  if (!path) return;
  try {
    state.playlist = await api("POST", "/api/playlist", { path });
    renderPlaylist();
    if (!state.playlist.items.length) { toast("В папке нет видео", "bad"); return; }
    const inList = state.playlist.items.some((item) => item.path === (state.info && state.info.video));
    if (!inList) await openVideo(state.playlist.items[0].path);
  } catch (error) {
    toast(error.message, "bad");
  }
}

async function loadPlaylist() {
  try {
    state.playlist = await api("GET", "/api/playlist");
    renderPlaylist();
  } catch (error) {
    toast(error.message, "bad");
  }
}

const REGION_ICONS = { video: ["📌", "json рядом с видео"], camera: ["📷", "json по номеру камеры"] };

function renderPlaylist() {
  const current = state.info && state.info.video;
  $("#playlist-dir").textContent = state.playlist.dir || "папка не выбрана";
  $("#playlist-dir").classList.toggle("muted", !state.playlist.dir);
  $("#playlist").innerHTML = state.playlist.items.map((item, i) => {
    const icon = REGION_ICONS[item.regions];
    return `<li data-index="${i}" class="${item.path === current ? "current" : ""}" title="${escapeHtml(item.path)}">
      <span class="name">${escapeHtml(item.name)}</span>
      ${icon ? `<span title="${icon[1]}">${icon[0]}</span>` : ""}
      <span class="count">${item.saved ?? ""}</span></li>`;
  }).join("") || (state.playlist.dir ? `<li class="muted">видео не найдено</li>` : "");
  $$("#playlist li[data-index]").forEach((li) => (li.onclick = () => openVideo(state.playlist.items[Number(li.dataset.index)].path)));
  const active = $("#playlist li.current");
  if (active) active.scrollIntoView({ block: "nearest" });
}

function stepVideo(delta) {
  const items = state.playlist.items;
  if (!items.length) return;
  const current = items.findIndex((item) => item.path === (state.info && state.info.video));
  const next = current < 0 ? 0 : current + delta;
  if (next < 0 || next >= items.length) { toast(delta > 0 ? "Это последнее видео" : "Это первое видео"); return; }
  openVideo(items[next].path);
}

function countSaved(results) {
  const item = state.playlist.items.find((it) => it.path === (state.info && state.info.video));
  if (!item || item.saved == null) return;
  item.saved += results.filter((r) => r.ok).length;
  renderPlaylist();
}

async function chooseRegions() {
  stopEdit();
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
  stopEdit();
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
  const origin = { video: " (рядом с видео)", camera: " (по номеру камеры)" }[info.regions_origin] || "";
  $("#regions-path").textContent = useRegions ? info.regions_file + origin : "весь кадр";
  $("#regions-path").classList.toggle("muted", !useRegions);
  $("#edit-regions").disabled = !info.regions_editable;
  $("#save-regions-as").disabled = !useRegions;
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
  $("#frame").src = `/api/video/frame/${index}?zones=${state.edit ? 0 : 1}&t=${Date.now()}`;
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
    countSaved(data.results);
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
  if (state.edit) { editKey(event); return; }
  if (key === "PageDown") { event.preventDefault(); stepVideo(1); return; }
  if (key === "PageUp") { event.preventDefault(); stepVideo(-1); return; }
  if (key === " ") { event.preventDefault(); state.playing ? pause() : play(); return; }
  if (key === "ArrowRight") { event.preventDefault(); pause(); showFrame(state.frame + step() * big); return; }
  if (key === "ArrowLeft") { event.preventDefault(); pause(); showFrame(state.frame - step() * big); return; }
  if (!state.config) return;
  const plain = event.code.startsWith("Digit") ? event.code.slice(5) : event.code.startsWith("Key") ? event.code.slice(3).toLowerCase() : key.toLowerCase();
  const label = state.config.manual.keys[plain];
  if (label) { event.preventDefault(); saveCurrent(label, event.shiftKey); return; }
  if (plain === state.config.manual.save_all_key.toLowerCase()) { event.preventDefault(); saveCurrent(null, event.shiftKey); }
}

// ---------------------------------------------------------------- правка зон

const HANDLE = 7;   // половина размера маркера, пикс. экрана

function startEdit() {
  if (!state.info || !state.info.regions_editable) return;
  pause();
  state.edit = {
    regions: state.info.regions.map((r) => ({ id: r.id, shape: r.shape, points: r.polygon.map(([x, y]) => [x, y]) })),
    active: 0,
    drag: null,
  };
  $("#editor").hidden = false;
  $("#edit-bar").hidden = false;
  $("#edit-regions").disabled = true;
  showFrame(state.frame);
  drawEditor();
}

function stopEdit() {
  state.edit = null;
  $("#editor").hidden = true;
  $("#edit-bar").hidden = true;
  if (state.info) $("#edit-regions").disabled = !state.info.regions_editable;
}

function cancelEdit() {
  stopEdit();
  showFrame(state.frame);
}

async function applyEdit() {
  const regions = Object.fromEntries(state.edit.regions.map((r) => [r.id, r.points.map(([x, y]) => [Math.round(x * 10) / 10, Math.round(y * 10) / 10])]));
  try {
    const info = await api("POST", "/api/video/regions/edit", { regions });
    stopEdit();
    await applyVideo(info, state.frame);
    toast(`Разметка сохранена рядом с видео: ${info.regions_file}`, "ok");
    loadPlaylist();
  } catch (error) {
    toast(error.message, "bad");
  }
}

async function saveRegionsAs() {
  try {
    const target = await api("GET", "/api/video/regions/save-target");
    const path = await pickPath("save", "json", target.path, "Сохранить файл разметки");
    if (!path) return;
    const saved = await api("POST", "/api/video/regions/save-as", { path });
    toast(`Разметка сохранена: ${saved.path}`, "ok");
    loadPlaylist();
  } catch (error) {
    toast(error.message, "bad");
  }
}

// Масштаб: координаты кадра → пиксели картинки на экране.
function editScale() {
  const img = $("#frame");
  return img.clientWidth / (state.info.width || 1);
}

function drawEditor() {
  if (!state.edit) return;
  const canvas = $("#editor"), img = $("#frame");
  const ratio = window.devicePixelRatio || 1;
  canvas.width = Math.round(img.clientWidth * ratio);
  canvas.height = Math.round(img.clientHeight * ratio);
  const ctx = canvas.getContext("2d");
  ctx.setTransform(ratio, 0, 0, ratio, 0, 0);
  ctx.clearRect(0, 0, img.clientWidth, img.clientHeight);
  const k = editScale();
  state.edit.regions.forEach((region, i) => {
    const active = i === state.edit.active;
    const color = active ? "#facc15" : "#22c55e";
    ctx.beginPath();
    region.points.forEach(([x, y], j) => (j ? ctx.lineTo(x * k, y * k) : ctx.moveTo(x * k, y * k)));
    ctx.closePath();
    ctx.lineWidth = active ? 3 : 2;
    ctx.strokeStyle = color;
    ctx.fillStyle = active ? "rgba(250, 204, 21, 0.12)" : "rgba(34, 197, 94, 0.08)";
    ctx.fill();
    ctx.stroke();
    ctx.fillStyle = color;
    ctx.font = "bold 15px system-ui, sans-serif";
    const [lx, ly] = region.points[0];
    ctx.fillText(region.id, lx * k + 5, ly * k + 18);
    if (!active) return;
    handles(region).forEach((h) => {
      ctx.fillStyle = "#111";
      ctx.fillRect(h.x * k - HANDLE, h.y * k - HANDLE, HANDLE * 2, HANDLE * 2);
      ctx.fillStyle = color;
      ctx.fillRect(h.x * k - HANDLE + 2, h.y * k - HANDLE + 2, HANDLE * 2 - 4, HANDLE * 2 - 4);
    });
  });
}

function bbox(points) {
  const xs = points.map((p) => p[0]), ys = points.map((p) => p[1]);
  return { x1: Math.min(...xs), y1: Math.min(...ys), x2: Math.max(...xs), y2: Math.max(...ys) };
}

// Маркеры: у прямоугольника — углы и середины сторон, у многоугольника — его точки.
function handles(region) {
  if (region.shape !== "rectangle") return region.points.map(([x, y], i) => ({ x, y, vertex: i }));
  const b = bbox(region.points), mx = (b.x1 + b.x2) / 2, my = (b.y1 + b.y2) / 2;
  return [
    { x: b.x1, y: b.y1, sx: "x1", sy: "y1" }, { x: b.x2, y: b.y1, sx: "x2", sy: "y1" },
    { x: b.x2, y: b.y2, sx: "x2", sy: "y2" }, { x: b.x1, y: b.y2, sx: "x1", sy: "y2" },
    { x: mx, y: b.y1, sy: "y1" }, { x: b.x2, y: my, sx: "x2" }, { x: mx, y: b.y2, sy: "y2" }, { x: b.x1, y: my, sx: "x1" },
  ];
}

function rectPoints(b) {
  return [[b.x1, b.y1], [b.x2, b.y1], [b.x2, b.y2], [b.x1, b.y2]];
}

function inside(points, x, y) {
  let hit = false;
  for (let i = 0, j = points.length - 1; i < points.length; j = i++) {
    const [xi, yi] = points[i], [xj, yj] = points[j];
    if ((yi > y) !== (yj > y) && x < ((xj - xi) * (y - yi)) / (yj - yi) + xi) hit = !hit;
  }
  return hit;
}

const clampX = (x) => Math.min(Math.max(0, x), state.info.width);
const clampY = (y) => Math.min(Math.max(0, y), state.info.height);

function pointerPos(event) {
  const rect = $("#editor").getBoundingClientRect();
  const k = editScale();
  return [(event.clientX - rect.left) / k, (event.clientY - rect.top) / k];
}

function bindEditor() {
  const canvas = $("#editor");
  canvas.addEventListener("pointerdown", (event) => {
    if (!state.edit) return;
    const [x, y] = pointerPos(event);
    const tolerance = (HANDLE + 3) / editScale();
    const order = [state.edit.active, ...state.edit.regions.keys()].filter((v, i, a) => a.indexOf(v) === i);
    for (const i of order) {
      const region = state.edit.regions[i];
      const handle = i === state.edit.active
        ? handles(region).find((h) => Math.abs(h.x - x) <= tolerance && Math.abs(h.y - y) <= tolerance) : null;
      if (handle || inside(region.points, x, y)) {
        state.edit.active = i;
        state.edit.drag = { handle, start: [x, y], points: region.points.map((p) => [...p]) };
        canvas.setPointerCapture(event.pointerId);
        drawEditor();
        return;
      }
    }
  });
  canvas.addEventListener("pointermove", (event) => {
    if (!state.edit) return;
    const [x, y] = pointerPos(event);
    const drag = state.edit.drag;
    const region = state.edit.regions[state.edit.active];
    if (!drag) {
      const tolerance = (HANDLE + 3) / editScale();
      const over = region && handles(region).some((h) => Math.abs(h.x - x) <= tolerance && Math.abs(h.y - y) <= tolerance);
      canvas.style.cursor = over ? "pointer" : state.edit.regions.some((r) => inside(r.points, x, y)) ? "move" : "crosshair";
      return;
    }
    const h = drag.handle;
    if (!h) {
      // Перенос целиком: зона не выходит за край кадра.
      const b = bbox(drag.points);
      const dx = Math.min(Math.max(x - drag.start[0], -b.x1), state.info.width - b.x2);
      const dy = Math.min(Math.max(y - drag.start[1], -b.y1), state.info.height - b.y2);
      region.points = drag.points.map(([px, py]) => [px + dx, py + dy]);
    } else if (region.shape === "rectangle") {
      const b = bbox(drag.points);
      if (h.sx) b[h.sx] = clampX(x);
      if (h.sy) b[h.sy] = clampY(y);
      const fixed = { x1: Math.min(b.x1, b.x2), x2: Math.max(b.x1, b.x2), y1: Math.min(b.y1, b.y2), y2: Math.max(b.y1, b.y2) };
      region.points = rectPoints(fixed);
    } else {
      region.points = drag.points.map((p, i) => (i === h.vertex ? [clampX(x), clampY(y)] : p));
    }
    drawEditor();
  });
  const end = () => { if (state.edit && state.edit.drag) { state.edit.drag = null; drawEditor(); } };
  canvas.addEventListener("pointerup", end);
  canvas.addEventListener("pointercancel", end);
  window.addEventListener("resize", drawEditor);
  $("#frame").addEventListener("load", drawEditor);
}

function editKey(event) {
  const moves = { ArrowLeft: [-1, 0], ArrowRight: [1, 0], ArrowUp: [0, -1], ArrowDown: [0, 1] };
  if (event.key === "Escape") { event.preventDefault(); cancelEdit(); return; }
  if (event.key === "Enter") { event.preventDefault(); applyEdit(); return; }
  if (event.key === "Tab") {
    event.preventDefault();
    state.edit.active = (state.edit.active + 1) % state.edit.regions.length;
    drawEditor();
    return;
  }
  const move = moves[event.key];
  if (!move) return;
  event.preventDefault();
  const region = state.edit.regions[state.edit.active];
  const b = bbox(region.points);
  const size = event.shiftKey ? 10 : 1;
  const dx = Math.min(Math.max(move[0] * size, -b.x1), state.info.width - b.x2);
  const dy = Math.min(Math.max(move[1] * size, -b.y1), state.info.height - b.y2);
  region.points = region.points.map(([x, y]) => [x + dx, y + dy]);
  drawEditor();
}

// ---------------------------------------------------------------- автонарезка

let autoTimer = null;

let autoWasRunning = false;

async function autoStart(scope) {
  if (scope === "playlist" && !confirm(`Запустить автонарезку всех видео плейлиста (${state.playlist.items.length})?`)) return;
  try {
    await api("POST", "/api/auto/start", { scope });
    pollAuto();
  } catch (error) {
    toast(error.message, "bad");
  }
}

async function pollAuto() {
  clearTimeout(autoTimer);
  const s = await api("GET", "/api/auto/status");
  const where = s.total > 1 ? `видео ${s.index} из ${s.total}: ${s.video}` : s.video;
  const line = s.running ? `Идёт: ${where}, сохранено ${s.saved}` : s.error ? `Ошибка: ${s.error}` : s.summary;
  $("#auto-status").textContent = line || "";
  ["#auto-start", "#auto-start-all"].forEach((id) => ($(id).disabled = s.running));
  if (s.running) { autoWasRunning = true; autoTimer = setTimeout(pollAuto, 1000); return; }
  if (state.info && state.info.video) loadHistory();
  if (autoWasRunning) { autoWasRunning = false; loadPlaylist(); }
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
  $("#pick-folder").onclick = chooseFolder;
  $("#prev-video").onclick = () => stepVideo(-1);
  $("#next-video").onclick = () => stepVideo(1);
  $("#refresh-playlist").onclick = loadPlaylist;
  $("#edit-regions").onclick = startEdit;
  $("#edit-apply").onclick = applyEdit;
  $("#edit-cancel").onclick = cancelEdit;
  $("#save-regions-as").onclick = saveRegionsAs;
  bindEditor();
  $("#pick-regions").onclick = chooseRegions;
  $("#no-regions").onclick = noRegions;
  $("#frame").addEventListener("load", onFrameLoaded);
  $("#frame").addEventListener("error", onFrameError);
  $("#play").onclick = () => (state.playing ? pause() : play());
  $("#next").onclick = () => { pause(); showFrame(state.frame + step()); };
  $("#prev").onclick = () => { pause(); showFrame(state.frame - step()); };
  $("#seek").addEventListener("input", (e) => { pause(); showFrame(Number(e.target.value)); });
  $("#frame-number").addEventListener("change", (e) => { pause(); showFrame(Number(e.target.value)); });
  $("#auto-start").onclick = () => autoStart("video");
  $("#auto-start-all").onclick = () => autoStart("playlist");
  $("#auto-stop").onclick = async () => { await api("POST", "/api/auto/stop"); pollAuto(); };
  $("#refresh-stats").onclick = loadStats;
  $("#sync").onclick = runSync;
  $("#export").onclick = runExport;
  document.addEventListener("keydown", onKey);
  loadConfig()
    .then(() => api("GET", "/api/video"))
    .then((info) => applyVideo(info, info.frame))
    .then(loadPlaylist)
    .then(pollAuto)
    .catch((error) => toast(error.message, "bad"));
}

init();
