"use strict";
// Настройки: боковая панель (сохраняется сразу), классы и клавиши, файл настроек.

const form = () => $("#settings-form");
const field = (name) => form().elements.namedItem(name);
const findRule = (list, type) => (list || []).find((r) => r.type === type);
const settingsTimer = { id: null };

// Ближайший вариант выпадающего списка (значения в старых файлах настроек могут быть любыми).
function selectNearest(select, value) {
  const options = Array.from(select.options).filter((o) => o.value !== "");
  if (value === null || value === undefined || value === "") { select.value = ""; return; }
  const best = options.reduce((a, b) => (Math.abs(Number(b.value) - value) < Math.abs(Number(a.value) - value) ? b : a));
  select.value = best.value;
}

function fillSettings(config) {
  const set = (name, value) => {
    const el = field(name);
    if (!el) return;
    if (el instanceof RadioNodeList) { el.value = value; return; }
    el.type === "checkbox" ? (el.checked = !!value) : (el.value = value ?? "");
  };
  set("where", config.catalog.next_to_video ? "video" : "root");
  set("root", config.catalog.root);
  set("full_frame", config.storage.keep_source_frame);
  set("mask_outside", config.regions.mask_outside);
  set("padding", config.regions.padding);
  selectNearest(field("jpeg_quality"), config.storage.jpeg_quality);
  const limits = config.auto.limits, triggers = config.auto.triggers;
  const dedup = findRule(limits, "dedup"), interval = findRule(limits, "interval"), scene = findRule(limits, "scene_change");
  selectNearest(field("dedup"), dedup ? dedup.threshold : 5);
  set("on_duplicate", config.manual.on_duplicate);
  set("regions_search_dir", config.regions.search_dir);
  set("model_path", config.model.path);
  set("interval", interval && interval.seconds != null ? interval.seconds : 1);
  selectNearest(field("scene"), scene ? scene.min_diff : null);
  set("check_every", config.auto.check_every_seconds ?? 0.2);
  const conf = findRule(triggers, "confidence"), audit = findRule(triggers, "audit");
  set("conf_min", conf ? conf.min : 0);
  set("conf_max", conf ? conf.max : 0.8);
  set("audit", audit ? Math.round(audit.rate * 1000) / 10 : 0);
  set("cons_enabled", config.consistency.enabled);
  set("cons_context", config.consistency.context);
  set("cons_max_run", config.consistency.max_run);
  renderReviewAlways(config.classes, config.consistency.review_always);
  set("journal_save", config.journal.save);
  set("journal_conf_on", config.journal.conf_below !== null);
  set("journal_conf_below", config.journal.conf_below ?? 0.6);
  set("journal_flips", config.journal.flips);
  renderJournalLabels(config.classes, config.journal.labels);
  set("filename", config.storage.filename);
  set("collection", config.catalog.collection);
  set("dedup_scope", config.catalog.dedup_scope);
  updateSettingsView();
}

function renderReviewAlways(classes, selected) {
  $("#review-always").innerHTML = classes.map((cls) =>
    `<label class="check chip"><input type="checkbox" name="review_always" value="${escapeHtml(cls)}" ${selected.includes(cls) ? "checked" : ""}> ${escapeHtml(cls)}</label>`).join("")
    || `<span class="muted">классов нет</span>`;
}

function renderJournalLabels(classes, selected) {
  $("#journal-labels").innerHTML = classes.map((cls) =>
    `<label class="check chip"><input type="checkbox" name="journal_labels" value="${escapeHtml(cls)}" ${selected.includes(cls) ? "checked" : ""}> ${escapeHtml(cls)}</label>`).join("")
    || `<span class="muted">классов нет</span>`;
}

// Что зависит от других полей: своя папка, блок модели, схема последовательности.
function updateSettingsView() {
  const own = field("where").value === "root";
  field("root").disabled = !own;
  field("root").closest(".row").querySelector("button").disabled = !own;
  const model = field("model_path").value.trim();
  $("#model-options").classList.toggle("disabled", !model);
  $("#journal-options").classList.toggle("disabled", !field("journal_save").checked);
  field("journal_conf_below").disabled = !field("journal_conf_on").checked;
  updateModelChip();
  $("#model-hint").textContent = model
    ? "С моделью кадр сохраняется по её уверенности; класс подставляется из ответа модели."
    : "Без модели автонарезка берёт кадр раз в заданное время, без дублей; класс не известен — кадры попадают в «_unsorted».";
  renderSequenceDemo();
}

// Схема проверки последовательности: ряд кадров, какой из них уйдёт в «_suspect».
function renderSequenceDemo() {
  const context = Number(field("cons_context").value) || 5;
  const maxRun = Number(field("cons_max_run").value) || 2;
  const run = Number($("#demo-run").value) || 2;
  const side = 6;
  $("#cons-context-value").textContent = context;
  $("#cons-run-value").textContent = maxRun;
  $("#demo-run-value").textContent = run === 1 ? "1 кадра" : `${run} кадров`;
  const flagged = field("cons_enabled").checked && run <= maxRun && context <= side;
  const cells = [];
  for (let i = 0; i < side; i++) cells.push({ label: "FULL", ctx: i >= side - context });
  for (let i = 0; i < run; i++) cells.push({ label: "EMPTY", odd: true });
  for (let i = 0; i < side; i++) cells.push({ label: "FULL", ctx: i < context });
  const reason = !field("cons_enabled").checked ? "проверка выключена"
    : run > maxRun ? `выброс длиннее ${maxRun} — считается сменой состояния, кадры сохраняются как обычно`
      : context > side ? `нужно ${context} устойчивых кадров с каждой стороны, а их ${side} — не выброс`
        : "EMPTY среди FULL — похоже на ошибку модели: эти кадры уйдут в «_suspect» на проверку";
  $("#sequence-demo").innerHTML = `<div class="cells">${cells.map((c) =>
    `<span class="cell ${c.odd ? "odd" : ""} ${c.ctx ? "ctx" : ""} ${c.odd && flagged ? "flag" : ""}" title="${c.label}">${c.label[0]}</span>`).join("")}</div>
    <div class="${flagged ? "bad" : "muted"}">${reason}</div>`;
}

function collectSettings() {
  const c = structuredClone(state.config);
  const v = (name) => String(field(name).value).trim();
  const n = (name) => Number(field(name).value);
  const on = (name) => field(name).checked;
  c.catalog.next_to_video = v("where") === "video";
  c.catalog.root = v("root") || c.catalog.root || "datasets";
  c.catalog.collection = v("collection");
  c.catalog.dedup_scope = v("dedup_scope");
  c.storage.keep_source_frame = on("full_frame");
  c.storage.jpeg_quality = n("jpeg_quality");
  c.storage.filename = v("filename");
  c.regions.mask_outside = on("mask_outside");
  c.regions.padding = Math.max(0, n("padding") || 0);
  c.regions.search_dir = v("regions_search_dir") || null;
  c.manual.on_duplicate = v("on_duplicate");
  c.model.path = v("model_path") || null;
  const withModel = !!c.model.path;
  c.auto.enabled = true;
  c.auto.check_every_seconds = withModel ? (n("check_every") > 0 ? n("check_every") : null) : null;
  // Без модели повод сохранить — каждый проверенный кадр; с моделью — её уверенность и контрольная доля.
  c.auto.triggers = withModel
    ? [{ type: "confidence", min: n("conf_min"), max: n("conf_max"), include_empty: true }]
      .concat(n("audit") > 0 ? [{ type: "audit", rate: Math.min(1, n("audit") / 100) }] : [])
    : [{ type: "always" }];
  const limits = [{ type: "interval", seconds: n("interval") > 0 ? n("interval") : 1, frames: null, per: ["source", "region", "label"] }];
  if (v("scene")) limits.push({ type: "scene_change", min_diff: n("scene") });
  limits.push({ type: "dedup", threshold: n("dedup") });
  c.auto.limits = limits;
  c.consistency.enabled = on("cons_enabled");
  c.consistency.context = n("cons_context") || 5;
  c.consistency.max_run = n("cons_max_run") || 2;
  c.consistency.review_always = $$("#review-always input:checked").map((el) => el.value);
  c.journal.save = on("journal_save");
  c.journal.conf_below = on("journal_conf_on") ? Math.min(1, Math.max(0, n("journal_conf_below"))) : null;
  c.journal.flips = on("journal_flips");
  c.journal.labels = $$("#journal-labels input:checked").map((el) => el.value);
  return c;
}

function applyConfig(data) {
  state.config = data.config;
  $("#yaml").value = data.yaml;
  $("#config-name").textContent = fileName(data.path);
  $("#config-name").title = data.path;
  $("#settings-errors").textContent = "";
  $("#step").value = String(data.config.source.step);
  if (!$("#step").value) $("#step").value = "5";
  renderLegend();
}

async function loadConfig() {
  const data = await api("GET", "/api/config");
  applyConfig(data);
  fillSettings(data.config);
}

// Записывает настройки на сервер; вызывается при любом изменении поля (с небольшой задержкой).
async function putConfig(config, quiet) {
  try {
    applyConfig(await api("PUT", "/api/config", config));
    if (!quiet) {
      $("#settings-status").textContent = "сохранено ✓";
      setTimeout(() => ($("#settings-status").textContent = ""), 2000);
    }
    await refreshVideo();
    return true;
  } catch (error) {
    $("#settings-errors").textContent = error.message;
    return false;
  }
}

function scheduleSettingsSave() {
  updateSettingsView();
  clearTimeout(settingsTimer.id);
  settingsTimer.id = setTimeout(() => putConfig(collectSettings()), 500);
}

async function applyYaml() {
  try {
    applyConfig(await api("PUT", "/api/config/yaml", { text: $("#yaml").value }));
    fillSettings(state.config);
    toast("Настройки применены", "ok");
    await refreshVideo();
  } catch (error) {
    $("#settings-errors").textContent = error.message;
  }
}

async function openConfigFile() {
  const path = await pickPath("file", "yaml", $("#config-name").title, "Открыть файл настроек");
  if (!path) return;
  try {
    const data = await api("POST", "/api/config/open", { path });
    applyConfig(data);
    fillSettings(data.config);
    toast(`Настройки: ${path}`, "ok");
    await refreshVideo();
  } catch (error) {
    toast(error.message, "bad");
  }
}

async function saveConfigAs() {
  const path = await pickPath("save", "yaml", $("#config-name").title, "Сохранить настройки в файл");
  if (!path) return;
  try {
    applyConfig(await api("POST", "/api/config/save-as", { path }));
    toast(`Настройки сохранены: ${path}`, "ok");
  } catch (error) {
    toast(error.message, "bad");
  }
}

// ---------------------------------------------------------------- классы и клавиши

function classRow(name = "", key = "") {
  const tr = document.createElement("tr");
  tr.innerHTML = `<td><input class="name-input" value="${escapeHtml(name)}" placeholder="ИМЯ_КЛАССА"></td>
    <td><input class="key-input short key" maxlength="2" value="${escapeHtml(key)}" title="Нажмите клавишу"></td>
    <td><button type="button" class="remove" title="Удалить">−</button></td>`;
  const input = tr.querySelector(".name-input");
  input.addEventListener("input", () => {
    const pos = input.selectionStart, before = input.value.length;
    input.value = cleanName(input.value);
    const at = Math.max(0, pos - (before - input.value.length));
    input.setSelectionRange(at, at);
  });
  bindKeyInput(tr.querySelector(".key-input"));
  tr.querySelector(".remove").onclick = () => tr.remove();
  return tr;
}

function openClassesDialog() {
  const config = state.config;
  const byClass = {};
  Object.entries(config.manual.keys || {}).forEach(([key, cls]) => (byClass[cls] = key));
  const body = $("#classes-body");
  body.innerHTML = "";
  config.classes.forEach((cls) => body.appendChild(classRow(cls, byClass[cls] || "")));
  if (!config.classes.length) body.appendChild(classRow());
  $("#save-all-key").value = config.manual.save_all_key;
  $("#classes-errors").textContent = "";
  $("#classes-dialog").showModal();
}

async function saveClasses() {
  const rows = $$("#classes-body tr").map((tr) => ({
    tr, name: tr.querySelector(".name-input").value.trim(), key: tr.querySelector(".key-input").value.trim(),
  }));
  const saveAll = $("#save-all-key").value.trim() || "s";
  const errors = [];
  $$("#classes-body input").forEach((el) => el.classList.remove("invalid"));
  rows.forEach((row, i) => {
    const mark = (cls) => row.tr.querySelector(cls).classList.add("invalid");
    if (!row.name && row.key) { mark(".name-input"); errors.push(`Строка ${i + 1}: пустое имя класса`); }
    if (row.name && rows.findIndex((r) => r.name === row.name) !== i) { mark(".name-input"); errors.push(`Класс ${row.name} указан дважды`); }
    if (row.key && rows.findIndex((r) => r.key === row.key) !== i) { mark(".key-input"); errors.push(`Клавиша «${row.key}» назначена дважды`); }
    if (row.key && row.key === saveAll) { mark(".key-input"); errors.push(`Клавиша «${row.key}» уже занята под «без класса»`); }
  });
  if (errors.length) { $("#classes-errors").textContent = errors.join("\n"); return; }
  const c = structuredClone(state.config);
  c.classes = rows.filter((r) => r.name).map((r) => r.name);
  c.manual.keys = Object.fromEntries(rows.filter((r) => r.name && r.key).map((r) => [r.key, r.name]));
  c.manual.save_all_key = saveAll;
  // Правила, которые ссылались на удалённые классы, убираются.
  const known = (name) => c.classes.includes(name);
  c.auto.per_label = Object.fromEntries(Object.entries(c.auto.per_label || {}).filter(([k]) => known(k) || k === c.unsorted_label));
  c.consistency.review_always = c.consistency.review_always.filter(known);
  c.consistency.trusted_flips = Object.fromEntries(Object.entries(c.consistency.trusted_flips || {})
    .filter(([k]) => known(k)).map(([k, list]) => [k, list.filter(known)]));
  try {
    applyConfig(await api("PUT", "/api/config", c));
    fillSettings(state.config);
    $("#classes-dialog").close();
    toast("Классы сохранены", "ok");
    loadCounts();
  } catch (error) {
    $("#classes-errors").textContent = error.message;
  }
}

function bindSettings() {
  form().addEventListener("change", (event) => { if (event.target.id !== "yaml" && event.target.id !== "demo-run") scheduleSettingsSave(); });
  form().addEventListener("input", (event) => {
    if (event.target.type === "range") updateSettingsView();
  });
  form().addEventListener("submit", (event) => event.preventDefault());
  $("#demo-run").addEventListener("input", renderSequenceDemo);
  $("#apply-yaml").onclick = applyYaml;
  $("#open-config").onclick = openConfigFile;
  $("#save-config-as").onclick = saveConfigAs;
  $("#edit-classes").onclick = openClassesDialog;
  $("#add-class").onclick = () => $("#classes-body").appendChild(classRow()).querySelector("input").focus();
  $("#classes-save").onclick = saveClasses;
  $("#classes-cancel").onclick = () => $("#classes-dialog").close();
  bindKeyInput($("#save-all-key"));
  $("#step").addEventListener("change", () => {
    const c = structuredClone(state.config);
    c.source.step = Number($("#step").value) || 1;
    putConfig(c, true);
  });
}
