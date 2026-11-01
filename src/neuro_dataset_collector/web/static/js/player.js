"use strict";
// Кадр: открытое видео, воспроизведение, выбор зон, кнопки классов и ручное сохранение.

const step = () => (state.config ? state.config.source.step : 1);
const modelSet = () => !!(state.config && state.config.model.path);
const modelOn = () => state.model && modelSet();

// Цвета классов — те же, что рисует сервер на кадре (там BGR, здесь RGB).
const CLASS_COLORS = ["#50c850", "#ff783c", "#28a0ff", "#dc50c8", "#dcdc28", "#a05aff", "#ff9696", "#a0c85a"];
function classColor(label) {
  if (!label) return "#969696";
  const classes = (state.config && state.config.classes) || [];
  const index = classes.includes(label) ? classes.indexOf(label) : [...label].reduce((a, c) => a + c.charCodeAt(0), 0);
  return CLASS_COLORS[index % CLASS_COLORS.length];
}

// Ответы модели на кадре: включаются кнопкой «🧠 Модель» (или Alt+M), выбор запоминается в браузере.
function updateModelChip() {
  const chip = $("#model-chip");
  if (!chip) return;
  chip.disabled = !modelSet() || !(state.info && state.info.video);
  chip.classList.toggle("on", modelOn());
  chip.title = modelSet() ? "Показывать ответы модели на кадре (Alt+M)" : "Модель не задана: ⚙ → Автонарезка → Модель";
  if (!modelOn()) $("#model-answers").hidden = true;
}

function toggleModel() {
  if (!modelSet()) { toast("Модель не задана: ⚙ → Автонарезка → Модель", "bad"); return; }
  state.model = !state.model;
  try { localStorage.setItem("ndc.model", state.model ? "1" : "0"); } catch (error) { /* хранилище браузера недоступно */ }
  updateModelChip();
  showFrame(state.frame);
}

async function loadAnswers(index) {
  if (!modelOn()) return;
  try {
    const data = await api("GET", `api/model/answers/${index}`);
    if (data.frame !== state.frame) return;
    $("#model-answers").innerHTML = `<span class="muted">Модель:</span>` + data.answers.map((a) =>
      `<span class="answer"><span class="swatch" style="background:${classColor(a.label)}"></span>${escapeHtml(a.zone)}: <b>${escapeHtml(a.label || "—")}</b>
        ${a.conf !== null ? `<span class="muted">${a.conf.toFixed(2)}</span>` : ""}</span>`).join("");
    $("#model-answers").hidden = false;
  } catch (error) {
    $("#model-answers").innerHTML = `<span class="bad">${escapeHtml(error.message)}</span>`;
    $("#model-answers").hidden = false;
  }
}

async function chooseRegions() {
  stopEdit();
  closeRegionsMenu();
  const start = (state.info && state.info.regions_file) || (state.config && state.config.regions.search_dir) || (state.info && state.info.video);
  const path = await pickPath("file", "json", start, "Выберите файл разметки (LabelMe json)");
  if (!path) return;
  try {
    await applyVideo(await api("POST", "api/video/regions", { path }), state.frame);
    await loadConfig();
  } catch (error) {
    toast(error.message, "bad");
  }
}

async function noRegions() {
  stopEdit();
  closeRegionsMenu();
  try {
    await applyVideo(await api("POST", "api/video/regions", { path: null }), state.frame);
    await loadConfig();
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

// Название видео: подпапка плейлиста и имя файла.
function videoTitle(info) {
  const item = state.playlist.items.find((it) => it.path === info.video);
  return item ? item.name.split(/[\\/]/).join(" › ") : info.name;
}

async function applyVideo(info, frame) {
  state.info = info;
  // Сервер сам меняет файл разметки (при открытии видео, после правки зон): иначе следующее сохранение
  // настроек вернуло бы старый файл.
  const regions = state.config && state.config.regions;
  if (regions && ((regions.file || null) !== (info.regions_file || null) || regions.source !== info.regions_source)) {
    await loadConfig();
  }
  const hasVideo = !!info.video;
  $("#video-title").textContent = hasVideo ? videoTitle(info) : "Выберите папку с видео 📁 или одно видео 🎞";
  $("#video-title").title = info.video || "";
  $("#video-title").classList.toggle("muted", !hasVideo);
  const useRegions = info.regions_source === "file" && info.regions_file;
  const origin = { video: "📌 ", camera: "📷 " }[info.regions_origin] || "";
  $("#regions-chip").textContent = `Зоны: ${useRegions ? origin + fileName(info.regions_file) : "весь кадр"} ▾`;
  $("#regions-chip").title = useRegions ? info.regions_file : "Сохраняется кадр целиком";
  $("#save-regions-as-menu").disabled = !useRegions;
  $("#dataset-chip").title = `Открыть папку датасета: ${info.dataset_dir || ""}`;
  $("#dataset-chip").disabled = !hasVideo;
  updateModelChip();
  updateFrameTools();
  $("#frame-placeholder").style.display = hasVideo ? "none" : "";
  $("#frame").style.display = hasVideo && !autoRunning() ? "" : "none";
  if (!hasVideo) { $("#video-info").textContent = ""; $("#zones").innerHTML = ""; return; }
  $("#video-info").textContent = `${info.width}×${info.height}, ${Number(info.fps).toFixed(1)} к/с, кадров: ${info.frames}`;
  $("#seek").max = Math.max(0, info.frames - 1);
  renderZones();
  showFrame(frame ?? info.frame ?? 0);
  loadCounts();
  renderHistory();
}

// Зоны файла разметки: какие резать (галочки или щелчок по зоне на кадре).
function renderZones() {
  const shapes = (state.info && state.info.shapes) || [];
  if (!shapes.length) {
    $("#zones").innerHTML = `<span class="muted">Зон нет — сохраняется весь кадр</span>`;
    return;
  }
  $("#zones").innerHTML = `<span>Резать зоны:</span>` + shapes.map((s) =>
    `<label class="zone-chip ${s.selected ? "on" : ""}" title="${escapeHtml(s.label)}">
      <input type="checkbox" data-zone="${escapeHtml(s.id)}" ${s.selected ? "checked" : ""}> ${escapeHtml(s.id)}
      ${s.label && s.label !== s.id ? `<span class="muted">${escapeHtml(s.label)}</span>` : ""}</label>`).join("")
    + `<span class="hint">щелчок по зоне на кадре тоже включает и выключает её</span>`;
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
function onFrameClick(event) {
  if (state.edit || autoRunning() || !state.info || !state.info.shapes) return;
  const img = $("#frame");
  const rect = img.getBoundingClientRect();
  const k = img.clientWidth / (state.info.width || 1);
  const x = (event.clientX - rect.left) / k, y = (event.clientY - rect.top) / k;
  const shape = state.info.shapes.find((s) => inside(s.polygon, x, y));
  if (!shape) return;
  const ids = state.info.shapes.filter((s) => (s.id === shape.id ? !s.selected : s.selected)).map((s) => s.id);
  setZones(ids);
}

// Сколько кадров открытого видео уже сохранено по классам.
async function loadCounts() {
  if (!state.info || !state.info.video) { state.counts = {}; renderLegend(); return; }
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

function renderLegend() {
  if (!state.config) return;
  const counts = state.counts || {};
  const keyOfClass = {};
  Object.entries(state.config.manual.keys).forEach(([key, cls]) => (keyOfClass[cls] = key));
  const buttons = state.config.classes.map((cls) => {
    const key = keyOfClass[cls];
    return `<button data-label="${escapeHtml(cls)}" ${key ? "" : 'title="Клавиша не назначена"'}>
      ${key ? `<kbd>${escapeHtml(key)}</kbd>` : ""}${escapeHtml(cls)}<span class="count">${counts[cls] || 0}</span></button>`;
  });
  const unsorted = state.config.task === "classify" ? "без класса" : "сохранить";
  buttons.push(`<button data-label=""><kbd>${escapeHtml(state.config.manual.save_all_key)}</kbd>${unsorted}<span class="count">${counts[""] || 0}</span></button>`);
  $("#legend").innerHTML = buttons.join("");
  $$("#legend button").forEach((b) => (b.onclick = () => saveCurrent(b.dataset.label || null, false)));
}

function showFrame(index) {
  if (!state.info || !state.info.video) return;
  const last = Math.max(0, state.info.frames - 1);
  index = Math.min(Math.max(0, index), last || index);
  state.frame = index;
  state.loading = true;
  $("#seek").value = index;
  $("#frame-number").value = index;
  const model = modelOn() && !state.edit ? 1 : 0;
  $("#frame").src = `api/video/frame/${index}?zones=${state.edit ? 0 : 1}&model=${model}&t=${Date.now()}`;
  rememberPosition();
}

// Запоминаем позицию не чаще раза в 2 секунды — чтобы при следующем запуске продолжить с того же места.
function rememberPosition() {
  clearTimeout(state.positionTimer);
  state.positionTimer = setTimeout(() => api("POST", "api/video/position", { frame: state.frame }).catch(() => {}), 2000);
}

function onFrameLoaded() {
  state.loading = false;
  if (modelOn() && !state.edit) loadAnswers(state.frame);
  if (!state.playing) return;
  const fps = state.info.fps || 25;
  const delay = Math.max(15, (1000 * step()) / fps / Number($("#speed").value));
  setTimeout(() => { if (state.playing) showFrame(state.frame + step()); }, delay);
}

function onFrameError() {
  state.loading = false;
  if (state.playing) { pause(); toast("Конец видео"); return; }
  // Модель не загрузилась — показать кадр без неё и причину.
  if (modelOn()) { state.model = false; updateModelChip(); showFrame(state.frame); loadAnswersError(); }
}

// Причина, почему кадр с ответами модели не показался (например, не установлен ultralytics).
async function loadAnswersError() {
  try { await api("GET", `api/model/answers/${state.frame}`); } catch (error) { toast(error.message, "bad"); }
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

async function saveCurrent(label, force, onlyRegions) {
  if (!state.info || !state.info.video) { toast("Сначала выберите видео", "bad"); return; }
  if (autoRunning()) { toast("Идёт автонарезка — дождитесь окончания или остановите её", "bad"); return; }
  try {
    const data = await api("POST", "api/save", { frame: state.frame, label, regions: onlyRegions || null, force, model: modelOn() });
    data.results.forEach((r) => toast(r.message, r.ok ? "ok" : "bad"));
    loadCounts().then(renderPlaylist);
    loadGroup(state.info.video);
    if ($("#step-mode").checked) showFrame(state.frame + step());
  } catch (error) {
    toast(error.message, "bad");
  }
}

function toggleRegionsMenu() {
  $("#regions-menu").hidden = !$("#regions-menu").hidden;
}

function closeRegionsMenu() {
  $("#regions-menu").hidden = true;
}

function onKey(event) {
  if ($("#picker").open || $("#classes-dialog").open || $("#help-dialog").open) return;
  if (event.key === "Escape" && drawerOpen()) { closeDrawers(); return; }
  if (drawerOpen()) return;
  if (["INPUT", "SELECT", "TEXTAREA"].includes(event.target.tagName) && !["range", "checkbox"].includes(event.target.type)) return;
  const key = event.key;
  const big = event.shiftKey ? 10 : 1;
  if (state.edit) { editKey(event); return; }
  if (autoRunning()) return;
  if (event.altKey && event.code === "KeyM") { event.preventDefault(); toggleModel(); return; }
  if (key === "PageDown") { event.preventDefault(); stepVideo(1); return; }
  if (key === "PageUp") { event.preventDefault(); stepVideo(-1); return; }
  if (key === " ") { event.preventDefault(); state.playing ? pause() : play(); return; }
  if (key === "ArrowRight") { event.preventDefault(); pause(); showFrame(state.frame + step() * big); return; }
  if (key === "ArrowLeft") { event.preventDefault(); pause(); showFrame(state.frame - step() * big); return; }
  if (!state.config || event.ctrlKey || event.altKey || event.metaKey) return;
  const pressed = keyOf(event);
  const label = state.config.manual.keys[pressed];
  if (label) { event.preventDefault(); if (!stripKey(label, event.shiftKey)) saveCurrent(label, event.shiftKey); return; }
  if (pressed === state.config.manual.save_all_key.toLowerCase()) {
    event.preventDefault();
    if (!stripKey(null, event.shiftKey)) saveCurrent(null, event.shiftKey);
  }
}

function bindPlayer() {
  $("#pick-regions").onclick = chooseRegions;
  $("#no-regions").onclick = noRegions;
  $("#regions-chip").onclick = (event) => { event.stopPropagation(); toggleRegionsMenu(); };
  document.addEventListener("click", (event) => { if (!event.target.closest(".menu")) closeRegionsMenu(); });
  $("#dataset-chip").onclick = () => state.info && state.info.video && openFolder(state.info.video);
  $("#model-chip").onclick = toggleModel;
  try { state.model = localStorage.getItem("ndc.model") === "1"; } catch (error) { state.model = false; }
  $("#zones").addEventListener("change", onZoneToggle);
  $("#frame").addEventListener("click", onFrameClick);
  $("#frame").addEventListener("load", onFrameLoaded);
  $("#frame").addEventListener("error", onFrameError);
  $("#play").onclick = () => (state.playing ? pause() : play());
  $("#next").onclick = () => { pause(); showFrame(state.frame + step()); };
  $("#prev").onclick = () => { pause(); showFrame(state.frame - step()); };
  $("#seek").addEventListener("input", (e) => { pause(); showFrame(Number(e.target.value)); });
  $("#frame-number").addEventListener("change", (e) => { pause(); showFrame(Number(e.target.value)); });
  document.addEventListener("keydown", onKey);
}
