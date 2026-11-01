"use strict";
// Общее: состояние страницы, запросы к серверу, сообщения, выбор файлов, боковые панели.

const $ = (sel) => document.querySelector(sel);
const $$ = (sel) => Array.from(document.querySelectorAll(sel));

const state = {
  config: null,        // настройки (ответ /api/config)
  info: null,          // открытое видео (ответ /api/video)
  frame: 0,
  playing: false,
  loading: false,
  positionTimer: null,
  playlist: { dir: null, items: [] },
  counts: {},          // сохранено в открытом видео: класс → число
  auto: null,          // ход автонарезки (ответ /api/auto/status)
  edit: null,          // правка зон: { regions: [{id, shape, points}], active, vertex, drag, history }
  model: false,        // показывать ответы модели на кадре
  showZones: true,     // рисовать зоны на кадре
  answers: null,       // ответы модели на текущем кадре: { frame, answers: [{zone, label, conf}] }
  view: "cut",         // режим: нарезка, журнал, участки, проверка, итог
};

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

const escapeHtml = (text) => String(text).replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
const fileName = (path) => String(path || "").split(/[\\/]/).pop();
const fileUrl = (path, video) => `api/file?path=${encodeURIComponent(path)}&video=${encodeURIComponent(video)}`;
const autoRunning = () => !!(state.auto && state.auto.running);

// Имя класса: заглавная латиница, цифры, «_» и «-»; первый символ — буква или цифра.
function cleanName(value) {
  return value.toUpperCase().replace(/\s+/g, "_").replace(/[^A-Z0-9_-]/g, "").replace(/^[_-]+/, "");
}

// Клавиши, которые можно назначить: код клавиши → символ (раскладка не важна).
const KEY_CODES = {
  Backquote: "`", Minus: "-", Equal: "=", BracketLeft: "[", BracketRight: "]",
  Semicolon: ";", Quote: "'", Comma: ",", Period: ".", Slash: "/",
};
const KEY_CHARS = "0123456789abcdefghijklmnopqrstuvwxyz" + Object.values(KEY_CODES).join("");

// Клавиша из события: цифры и буквы по коду клавиши, чтобы русская раскладка не мешала.
function keyOf(event) {
  if (event.code.startsWith("Digit")) return event.code.slice(5);
  if (event.code.startsWith("Key")) return event.code.slice(3).toLowerCase();
  if (KEY_CODES[event.code]) return KEY_CODES[event.code];
  return event.key.toLowerCase();
}

// Поле «клавиша»: принимает нажатие клавиши, а не набранный текст (тильда и русская раскладка работают).
function bindKeyInput(input) {
  input.addEventListener("keydown", (event) => {
    if (["Tab", "Shift", "Escape"].includes(event.key)) return;
    event.preventDefault();
    if (event.key === "Backspace" || event.key === "Delete") { input.value = ""; return; }
    const key = keyOf(event);
    if (KEY_CHARS.includes(key) && key.length === 1) input.value = key;
  });
}

// ---------------------------------------------------------------- режимы

const VIEWS = ["cut", "journal", "review", "check", "export"];
const viewHandlers = {};   // режим → что сделать при его открытии

function setView(name) {
  if (!VIEWS.includes(name)) name = "cut";
  state.view = name;
  $$(".rail button").forEach((b) => b.classList.toggle("active", b.dataset.view === name));
  VIEWS.forEach((v) => ($(`#view-${v}`).hidden = v !== name));
  if (name !== "cut" && typeof pause === "function") pause();
  try { localStorage.setItem("ndc.view", name); } catch (error) { /* хранилище браузера недоступно */ }
  renderHints();
  if (viewHandlers[name]) viewHandlers[name]();
}

const HINTS = {
  cut: [["Пробел", "пуск / пауза"], ["← →", "шаг"], ["клавиша класса", "сохранить"], ["PgUp PgDn", "видео"], ["Alt+M", "ответы модели"]],
  journal: [["← →", "соседний вырез"], ["клавиша класса", "сохранить и дальше"], ["Enter", "кадр в нарезке"]],
  review: [["← →", "соседний вырез"], ["клавиша класса", "сохранить и дальше"], ["Enter", "кадр в нарезке"]],
  check: [], export: [],
};

function renderHints() {
  const hints = HINTS[state.view] || [];
  const folder = state.info && state.info.dataset_dir ? `<span class="spacer"></span><span>Датасет: ${escapeHtml(state.info.dataset_dir)}</span>` : "";
  $("#hints").innerHTML = hints.map(([k, t]) => `<span><kbd>${escapeHtml(k)}</kbd> ${escapeHtml(t)}</span>`).join("") + folder;
}

// Цвета классов — те же, что рисует сервер на кадре (там BGR, здесь RGB).
const CLASS_COLORS = ["#50c850", "#ff783c", "#28a0ff", "#dc50c8", "#dcdc28", "#a05aff", "#ff9696", "#a0c85a"];
function classColor(label) {
  if (!label) return "#969696";
  const classes = (state.config && state.config.classes) || [];
  const index = classes.includes(label) ? classes.indexOf(label) : [...label].reduce((a, c) => a + c.charCodeAt(0), 0);
  return CLASS_COLORS[index % CLASS_COLORS.length];
}

const fmtTime = (seconds) => {
  const s = Math.max(0, seconds || 0);
  return `${Math.floor(s / 60)}:${(s % 60).toFixed(1).padStart(4, "0")}`;
};

// ---------------------------------------------------------------- боковые панели

function openDrawer(id) {
  $$(".drawer").forEach((d) => (d.hidden = d.id !== id));
  $("#drawer-shade").hidden = false;
}

function closeDrawers() {
  $$(".drawer").forEach((d) => (d.hidden = true));
  $("#drawer-shade").hidden = true;
}

const drawerOpen = () => $$(".drawer").some((d) => !d.hidden);


// ---------------------------------------------------------------- выбор файлов

// Системное окно выбора; запасной выбор в браузере — только если системное окно открыть нельзя.
async function pickPath(kind, filter, initial, title) {
  let data;
  try {
    data = await api("POST", "api/pick", { kind, filter: filter || "any", initial: initial || null, title: title || "" });
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
    const data = await api("GET", `api/browse?${query}`);
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
