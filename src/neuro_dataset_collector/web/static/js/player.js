"use strict";
// Нарезка: открытое видео, состояние разметки и модели, кадр, зоны, кнопки классов и ручное сохранение.

const step = () => (state.config ? state.config.source.step : 1);
const modelSet = () => !!(state.config && state.config.model.path);
const modelOn = () => state.model && modelSet();
const hasVideo = () => !!(state.info && state.info.video);

// ---------------------------------------------------------------- состояние в верхней строке

const MARKUP_NAMES = { sidecar: "рядом с видео", production: "боевая", file: "явный файл" };

// Подпись разметки: вариант, версия рядом с видео или ключ боевой, имя явного файла.
function markupTitle(markup) {
  const name = MARKUP_NAMES[markup.variant] || markup.variant;
  if (markup.variant === "sidecar") return markup.version ? `${name} · v${markup.version}` : `${name} · изменена вне версий`;
  if (markup.variant === "production") return `${name} · ${markup.key}`;
  return `${name} · ${fileName(markup.path)}`;
}

function renderMarkupMenu(info) {
  const choice = info.markup_choice || { variant: "auto", version: null };
  const has = hasVideo();
  $$("#regions-menu [data-markup]").forEach((button) => {
    const variant = button.dataset.markup;
    button.classList.toggle("active", has && info.regions_source === "file" && choice.variant === variant);
    button.disabled = !has || (variant === "sidecar" && !info.markup_sidecar)
      || (variant === "production" && !info.markup_production_key) || (variant === "file" && !info.markup_file);
  });
  const production = $('#regions-menu [data-markup="production"]');
  production.textContent = info.markup_production_key ? `Боевая: ${info.markup_production_key}` : "Боевая: не найдена";
  const file = $('#regions-menu [data-markup="file"]');
  file.textContent = info.markup_file ? `Явный файл: ${fileName(info.markup_file)}` : "Явный файл: не выбран";
  file.title = info.markup_file || "";
  const versions = info.markup_versions || [];
  $("#markup-version-row").hidden = !has || !versions.length;
  const current = info.markup_current;
  $("#markup-version").innerHTML = `<option value="">текущая${current ? ` (v${current})` : ""}</option>`
    + versions.slice().reverse().map((v) => `<option value="${v.number}">v${v.number}${v.number === current ? " · текущая" : ""}${v.comment ? ` · ${escapeHtml(v.comment)}` : ""}</option>`).join("");
  $("#markup-version").value = choice.variant === "sidecar" && choice.version ? String(choice.version) : "";
  $("#open-markup-editor").disabled = !has || autoRunning();
  $("#edit-markup").disabled = !has || autoRunning();
}

function updateStatus() {
  const info = state.info || {};
  const chip = $("#regions-chip");
  const useFile = info.regions_source === "file" && info.markup;
  chip.querySelector(".txt").textContent = useFile ? `Разметка: ${markupTitle(info.markup)}` : "Разметка: весь кадр, без зон";
  chip.title = useFile ? info.markup.path : "Зоны не заданы — сохраняется кадр целиком. Щелчок — выбрать разметку";
  chip.classList.toggle("ok", !!useFile && !info.regions_warning);
  chip.classList.toggle("warn", !!info.regions_warning);
  renderMarkupMenu(info);
  const warning = $("#regions-warning");
  warning.hidden = !info.regions_warning;
  if (info.regions_warning) {
    warning.innerHTML = `<b>Разметка:</b> ${escapeHtml(info.regions_warning)}
      <button class="btn small" type="button" data-pick-regions>Открыть файл разметки…</button>`;
  }
  const model = $("#model-chip");
  const name = modelSet() ? fileName(state.config.model.path) : null;
  model.querySelector(".txt").textContent = name ? `Модель: ${name}${modelOn() ? " · на кадре" : ""}` : "Модель: не выбрана";
  model.title = name ? "Показывать ответы модели на кадре (Alt+M)" : "Модель не выбрана — щелчок откроет настройки";
  model.classList.toggle("ok", !!name);
  model.classList.toggle("on", modelOn());
  const show = $("#show-model");
  show.classList.toggle("active", modelOn());
  show.setAttribute("aria-pressed", String(modelOn()));
  show.title = name ? "Ответы модели на кадре (Alt+M)" : "Модель не выбрана: Настройки → Автонарезка → Модель";
  $("#show-zones").classList.toggle("active", state.showZones);
  $("#show-zones").setAttribute("aria-pressed", String(state.showZones));
}

function openModelSettings() {
  openDrawer("settings-drawer");
  const input = field("model_path");
  input.closest("details").open = true;
  input.scrollIntoView({ block: "center" });
  input.focus();
}

function toggleModel() {
  if (!modelSet()) { toast("Модель не выбрана: укажите файл модели в настройках", "bad"); openModelSettings(); return; }
  state.model = !state.model;
  try { localStorage.setItem("ndc.model", state.model ? "1" : "0"); } catch (error) { /* хранилище браузера недоступно */ }
  state.answers = null;
  updateStatus();
  renderZones();
  showFrame(state.frame);
}

function toggleZones() {
  state.showZones = !state.showZones;
  updateStatus();
  showFrame(state.frame);
}

async function loadAnswers(index) {
  if (!modelOn()) return;
  try {
    const data = await api("GET", `api/model/answers/${index}`);
    if (data.frame !== state.frame) return;
    state.answers = data;
  } catch (error) {
    state.answers = { frame: index, error: error.message, answers: [] };
  }
  renderZones();
}

// ---------------------------------------------------------------- видео

async function chooseRegions() {
  closeMenus();
  const start = (state.info && (state.info.markup_file || state.info.regions_file)) || (state.config && state.config.regions.search_dir) || (state.info && state.info.video);
  const path = await pickPath("file", "json", start, "Выберите файл разметки (LabelMe json)");
  if (!path) return;
  try {
    await applyVideo(await api("POST", "api/video/regions", { path }), state.frame);
    await loadPlaylist();
  } catch (error) {
    toast(error.message, "bad");
  }
}

async function noRegions() {
  closeMenus();
  try {
    await applyVideo(await api("POST", "api/video/regions", { path: null }), state.frame);
  } catch (error) {
    toast(error.message, "bad");
  }
}

async function setMarkup(variant, version) {
  closeMenus();
  try {
    await applyVideo(await api("POST", "api/video/markup", { variant, version: version || null }), state.frame);
    await loadPlaylist();
  } catch (error) {
    toast(error.message, "bad");
  }
}

// Редактор разметки neuro_markup_lab: открывается на разметке, по которой сейчас режется видео.
function openMarkupEditor() {
  closeMenus();
  if (!hasVideo()) return;
  pause();
  const info = state.info;
  const markup = info.markup;
  const variant = markup && markup.variant !== "file" ? markup.variant : null;
  if (markup && markup.variant === "file") toast("Явный файл в редакторе не открывается: открыта разметка рядом с видео или боевая", "bad");
  NML.editor.open({
    video: info.video,
    jsonsDir: info.jsons_dir || "",
    variant,
    version: variant === "sidecar" && info.markup_choice.variant === "sidecar" ? info.markup_choice.version : null,
    productionKey: info.markup_production_key || null,
  });
}

// Редактор сохранил разметку открытого видео — перечитать зоны.
async function onMarkupSaved(event) {
  const detail = event.detail || {};
  if (!hasVideo() || detail.video !== state.info.video) return;
  try {
    await applyVideo(await api("POST", "api/video/markup/saved", { variant: detail.variant }), state.frame);
    await loadPlaylist();
  } catch (error) {
    toast(error.message, "bad");
  }
}

async function refreshVideo() {
  try {
    await applyVideo(await api("GET", "api/video"), state.frame);
  } catch (error) {
    toast(error.message, "bad");
  }
}

// Путь видео в плейлисте: подпапки и имя файла.
function videoParts(info) {
  const item = state.playlist.items.find((it) => it.path === info.video);
  const parts = item ? item.name.split(/[\\/]/) : [info.name];
  const root = state.playlist.dir ? fileName(state.playlist.dir.replace(/[\\/]+$/, "")) : null;
  return (root ? [root] : []).concat(parts);
}

function renderCrumbs() {
  const info = state.info;
  if (!info || !info.video) { $("#crumbs").innerHTML = `<span>${state.playlist.dir ? escapeHtml(state.playlist.dir) : "видео не открыто"}</span>`; return; }
  const parts = videoParts(info);
  $("#crumbs").innerHTML = parts.map((p, i) => (i === parts.length - 1 ? `<b>${escapeHtml(p)}</b>` : `<span>${escapeHtml(p)}</span><span class="sep">/</span>`)).join("");
  $("#crumbs").title = info.video;
}

async function applyVideo(info, frame) {
  state.info = info;
  state.answers = null;
  // Сервер сам включает зоны, когда у видео нашлась разметка: иначе следующее сохранение настроек
  // вернуло бы прежний источник зон.
  const regions = state.config && state.config.regions;
  if (regions && regions.source !== info.regions_source) {
    await loadConfig();
  }
  const has = hasVideo();
  renderCrumbs();
  updateStatus();
  $("#video-title").textContent = has ? info.name : "Видео не открыто";
  $("#video-title").title = info.video || "";
  $("#dataset-chip").disabled = !has;
  $("#dataset-path").textContent = has ? info.dataset_dir : "";
  $("#frame-placeholder").hidden = has;
  $(".view-cut .frame-wrap").hidden = !has;
  $(".view-cut .controls").hidden = !has;
  $("#timeline").hidden = !has;
  $("#show-zones").disabled = !has;
  $("#show-model").disabled = !has;
  renderHints();
  if (!has) { $("#video-info").textContent = ""; renderZones(); return; }
  const duration = info.fps ? info.frames / info.fps : 0;
  $("#video-info").textContent = `${info.width}×${info.height} · ${Number(info.fps).toFixed(1)} к/с · ${fmtTime(duration)} · кадров: ${info.frames}`;
  $("#seek").max = Math.max(0, info.frames - 1);
  renderZones();
  showFrame(frame ?? info.frame ?? 0);
  loadCounts();
  loadRecent();
  loadTimeline();
}

// ---------------------------------------------------------------- зоны

function answerFor(zone) {
  const data = state.answers;
  if (!modelOn() || !data || data.frame !== state.frame) return null;
  return data.answers.find((a) => a.zone === zone) || null;
}

function answerChip(answer) {
  if (!modelOn()) return "";
  if (!answer) return `<span class="answer none">…</span>`;
  const conf = answer.conf !== null && answer.conf !== undefined ? ` ${Number(answer.conf).toFixed(2)}` : "";
  return `<span class="answer" style="background:${classColor(answer.label)}">${escapeHtml(answer.label || "—")}${conf}</span>`;
}

function renderZones() {
  const box = $("#zones");
  if (!hasVideo()) { box.innerHTML = `<p class="muted small">Откройте видео</p>`; return; }
  const shapes = state.info.shapes || [];
  const error = state.answers && state.answers.error ? `<p class="bad small">${escapeHtml(state.answers.error)}</p>` : "";
  if (!shapes.length) {
    const full = answerFor("full");
    box.innerHTML = `<div class="zone-row"><span class="zname">Весь кадр<br><span class="muted small">зоны не заданы</span></span>${answerChip(full)}</div>
      <button class="btn small" type="button" data-pick-regions>Открыть файл разметки…</button>${error}`;
    return;
  }
  box.innerHTML = shapes.map((s) => `<label class="zone-row ${s.selected ? "" : "off"}" title="${escapeHtml(s.label)}">
      <input type="checkbox" data-zone="${escapeHtml(s.id)}" ${s.selected ? "checked" : ""}>
      <span class="zname"><b>Зона ${escapeHtml(s.id)}</b> ${s.label && s.label !== s.id ? `<span class="muted">${escapeHtml(s.label)}</span>` : ""}</span>
      ${s.selected ? answerChip(answerFor(s.id)) : `<span class="muted small">не режем</span>`}</label>`).join("") + error
    + `<p class="hint">Галочка или щелчок по зоне на кадре — резать её или нет. Запоминается для камеры.</p>`;
}

async function setZones(ids) {
  if (!ids.length) { toast("Нужна хотя бы одна зона", "bad"); renderZones(); return; }
  try {
    const info = await api("POST", "api/video/zones", { ids });
    await applyVideo(info, state.frame);
  } catch (error) {
    toast(error.message, "bad");
    renderZones();
  }
}

function onZoneToggle(event) {
  const input = event.target.closest("input[data-zone]");
  if (!input) return;
  setZones($$("#zones input[data-zone]:checked").map((el) => el.dataset.zone));
}

// Щелчок по зоне на кадре — включить или выключить её нарезку.
// Точка внутри многоугольника (луч вправо пересекает стороны нечётное число раз).
function inside(points, x, y) {
  let hit = false;
  for (let i = 0, j = points.length - 1; i < points.length; j = i++) {
    const [xi, yi] = points[i], [xj, yj] = points[j];
    if ((yi > y) !== (yj > y) && x < ((xj - xi) * (y - yi)) / (yj - yi) + xi) hit = !hit;
  }
  return hit;
}

function onFrameClick(event) {
  if (autoRunning() || !state.info || !state.info.shapes) return;
  const img = $("#frame");
  const rect = img.getBoundingClientRect();
  // Кадр вписан в рамку с сохранением пропорций: учитываем поля.
  const k = Math.min(rect.width / (state.info.width || 1), rect.height / (state.info.height || 1));
  const dx = (rect.width - state.info.width * k) / 2, dy = (rect.height - state.info.height * k) / 2;
  const x = (event.clientX - rect.left - dx) / k, y = (event.clientY - rect.top - dy) / k;
  const shape = state.info.shapes.find((s) => inside(s.polygon, x, y));
  if (!shape) return;
  const ids = state.info.shapes.filter((s) => (s.id === shape.id ? !s.selected : s.selected)).map((s) => s.id);
  setZones(ids);
}

// ---------------------------------------------------------------- классы

// Сколько кадров открытого видео уже сохранено по классам.
async function loadCounts() {
  if (!hasVideo()) { state.counts = {}; renderLegend(); return; }
  try {
    const data = await api("GET", "api/stats?scope=video");
    state.counts = data.labels || {};
    const item = state.playlist.items.find((it) => it.path === state.info.video);
    if (item && item.labels !== null) {
      // В плейлисте классы — имена папок: «без класса» — это папка «_unsorted».
      item.labels = Object.fromEntries(Object.entries(state.counts).map(([k, v]) => [k || state.config.unsorted_label, v]));
      item.saved = Object.values(state.counts).reduce((a, b) => a + b, 0);
    }
  } catch (error) {
    state.counts = {};
  }
  renderLegend();
}

// Кнопки классов с клавишами: в нарезке (с числом сохранённых), в журнале и на участках.
function classButtons(withCounts) {
  if (!state.config) return "";
  const counts = state.counts || {};
  const keyOfClass = {};
  Object.entries(state.config.manual.keys).forEach(([key, cls]) => (keyOfClass[cls] = key));
  const button = (label, name, key, color) => `<button type="button" class="cls" data-label="${escapeHtml(label)}" style="--c:${color}" ${key ? "" : 'title="Клавиша не назначена"'}>
      ${key ? `<kbd>${escapeHtml(key)}</kbd>` : ""}<span class="cname">${escapeHtml(name)}</span>${withCounts ? `<span class="count">${counts[label] || 0}</span>` : ""}</button>`;
  const html = state.config.classes.map((cls) => button(cls, cls, keyOfClass[cls], classColor(cls)));
  html.push(button("", state.config.task === "classify" ? "Без класса" : "Сохранить", state.config.manual.save_all_key, "#969696"));
  return html.join("");
}

function renderLegend() {
  if (!state.config) return;
  $("#legend").innerHTML = state.config.classes.length || state.config.task !== "classify"
    ? classButtons(true)
    : `<p class="muted small">Классов нет — добавьте их кнопкой «Классы и клавиши».</p>` + classButtons(true);
  if (typeof renderStripClasses === "function") renderStripClasses();
}

// ---------------------------------------------------------------- кадр и воспроизведение

function showFrame(index) {
  if (!hasVideo()) return;
  const last = Math.max(0, state.info.frames - 1);
  index = Math.min(Math.max(0, index), last || index);
  state.frame = index;
  state.loading = true;
  $("#seek").value = index;
  $("#frame-number").value = index;
  const fps = state.info.fps || 25;
  $("#time").textContent = `${fmtTime(index / fps)} / ${fmtTime(state.info.frames / fps)}`;
  $("#frame-badge").hidden = false;
  $("#frame-badge").textContent = `Кадр ${index} · ${fmtTime(index / fps)}`;
  const model = modelOn() ? 1 : 0;
  const zones = state.showZones ? 1 : 0;
  $("#frame").src = `api/video/frame/${index}?zones=${zones}&model=${model}&t=${Date.now()}`;
  moveTimelineCursor();
  rememberPosition();
}

// Запоминаем позицию не чаще раза в 2 секунды — чтобы при следующем запуске продолжить с того же места.
function rememberPosition() {
  clearTimeout(state.positionTimer);
  state.positionTimer = setTimeout(() => api("POST", "api/video/position", { frame: state.frame }).catch(() => {}), 2000);
}

function onFrameLoaded() {
  state.loading = false;
  if (modelOn()) loadAnswers(state.frame);
  if (!state.playing) return;
  const fps = state.info.fps || 25;
  const delay = Math.max(15, (1000 * step()) / fps / Number($("#speed").value));
  setTimeout(() => { if (state.playing) showFrame(state.frame + step()); }, delay);
}

function onFrameError() {
  state.loading = false;
  if (state.playing) { pause(); toast("Конец видео"); return; }
  // Модель не загрузилась — показать кадр без неё и причину.
  if (modelOn()) { state.model = false; updateStatus(); showFrame(state.frame); loadAnswersError(); }
}

async function loadAnswersError() {
  try { await api("GET", `api/model/answers/${state.frame}`); } catch (error) { toast(`Модель: ${error.message}`, "bad"); }
}

function play() {
  if (!hasVideo()) return;
  if ($("#step-mode").checked) { showFrame(state.frame + step()); return; }
  state.playing = true;
  $("#play").textContent = "❚❚";
  if (!state.loading) showFrame(state.frame + step());
}

function pause() {
  state.playing = false;
  if ($("#play")) $("#play").textContent = "▶";
}

async function saveCurrent(label, force, onlyRegions) {
  if (!hasVideo()) { toast("Сначала откройте видео", "bad"); return; }
  if (autoRunning()) { toast("Идёт автонарезка — дождитесь окончания или остановите её", "bad"); return; }
  try {
    const data = await api("POST", "api/save", { frame: state.frame, label, regions: onlyRegions || null, force, model: modelOn() });
    data.results.forEach((r) => toast(r.message, r.ok ? "ok" : "bad"));
    const button = $(`#legend .cls[data-label="${CSS.escape(label || "")}"]`);
    if (button && data.results.some((r) => r.ok)) { button.classList.add("flash"); setTimeout(() => button.classList.remove("flash"), 500); }
    loadCounts().then(renderPlaylist);
    loadGroup(state.info.video);
    loadRecent();
    loadTimeline();
    if ($("#step-mode").checked) showFrame(state.frame + step());
  } catch (error) {
    toast(error.message, "bad");
  }
}

// ---------------------------------------------------------------- меню и клавиши

function closeMenus() {
  $$(".menu-list").forEach((m) => (m.hidden = true));
}

function toggleMenu(id) {
  const menu = $(id);
  const open = menu.hidden;
  closeMenus();
  menu.hidden = !open;
}

function onKey(event) {
  // Открыт редактор разметки: клавиши его, кадры не сохраняются
  if (window.NML && NML.editor.isOpen()) return;
  if ($("#picker").open || $("#classes-dialog").open || $("#help-dialog").open) return;
  if (event.key === "Escape") { closeMenus(); if (drawerOpen()) { closeDrawers(); return; } }
  if (drawerOpen()) return;
  if (["INPUT", "SELECT", "TEXTAREA"].includes(event.target.tagName) && !["range", "checkbox"].includes(event.target.type)) return;
  if (state.view === "journal" || state.view === "review") { stripKeyEvent(event); return; }
  if (state.view !== "cut") return;
  const key = event.key;
  const big = event.shiftKey ? 10 : 1;
  if (autoRunning()) return;
  if (event.altKey && event.code === "KeyM") { event.preventDefault(); toggleModel(); return; }
  if (key === "PageDown") { event.preventDefault(); stepVideo(1); return; }
  if (key === "PageUp") { event.preventDefault(); stepVideo(-1); return; }
  if (key === " ") { event.preventDefault(); state.playing ? pause() : play(); return; }
  if (key === "ArrowRight") { event.preventDefault(); pause(); showFrame(state.frame + step() * big); return; }
  if (key === "ArrowLeft") { event.preventDefault(); pause(); showFrame(state.frame - step() * big); return; }
  const label = classOfKey(event);
  if (label === undefined) return;
  event.preventDefault();
  saveCurrent(label, event.shiftKey);
}

// Класс по нажатой клавише: имя, null — «без класса», undefined — клавиша не назначена.
function classOfKey(event) {
  if (!state.config || event.ctrlKey || event.altKey || event.metaKey) return undefined;
  const pressed = keyOf(event);
  const label = state.config.manual.keys[pressed];
  if (label) return label;
  if (pressed === state.config.manual.save_all_key.toLowerCase()) return null;
  return undefined;
}

function bindPlayer() {
  $("#pick-regions").onclick = chooseRegions;
  $$("#regions-menu [data-markup]").forEach((button) => (button.onclick = () => setMarkup(button.dataset.markup)));
  $("#markup-version").onchange = (event) => setMarkup("sidecar", Number(event.target.value) || null);
  $("#open-markup-editor").onclick = openMarkupEditor;
  $("#edit-markup").onclick = openMarkupEditor;
  document.addEventListener("nml:saved", onMarkupSaved);
  $("#no-regions").onclick = noRegions;
  $("#regions-chip").onclick = (event) => { event.stopPropagation(); toggleMenu("#regions-menu"); };
  document.addEventListener("click", (event) => {
    if (!event.target.closest(".menu")) closeMenus();
    if (event.target.closest("[data-pick-regions]")) chooseRegions();
  });
  $("#model-chip").onclick = toggleModel;
  $("#show-model").onclick = toggleModel;
  $("#show-zones").onclick = toggleZones;
  $("#dataset-chip").onclick = () => hasVideo() && openFolder(state.info.video);
  $("#zones").addEventListener("change", onZoneToggle);
  $("#legend").addEventListener("click", (event) => {
    const button = event.target.closest(".cls");
    if (button) saveCurrent(button.dataset.label || null, event.shiftKey);
  });
  $("#frame").addEventListener("click", onFrameClick);
  $("#frame").addEventListener("load", onFrameLoaded);
  $("#frame").addEventListener("error", onFrameError);
  $("#play").onclick = () => (state.playing ? pause() : play());
  $("#next").onclick = () => { pause(); showFrame(state.frame + step()); };
  $("#prev").onclick = () => { pause(); showFrame(state.frame - step()); };
  $("#seek").addEventListener("input", (e) => { pause(); showFrame(Number(e.target.value)); });
  $("#frame-number").addEventListener("change", (e) => { pause(); showFrame(Number(e.target.value)); });
  document.addEventListener("keydown", onKey);
  try { state.model = localStorage.getItem("ndc.model") === "1"; } catch (error) { state.model = false; }
}
