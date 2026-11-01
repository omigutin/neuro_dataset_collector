"use strict";
// Правка зон прямо на кадре: перенос, размер, точки, сохранение разметки рядом с видео.

const HANDLE = 7;      // половина размера маркера точки, пикс. экрана
const PLUS = 5;        // радиус маркера «+» на середине стороны
const MIN_POINTS = 3;

function startEdit() {
  if (!state.info || !state.info.regions_editable) return;
  pause();
  state.edit = {
    regions: (state.info.shapes || []).map((r) => ({ id: r.id, shape: r.shape, points: r.polygon.map(([x, y]) => [x, y]) })),
    active: 0,
    vertex: null,      // выбранная точка многоугольника (для Delete)
    drag: null,
    history: [],       // для отмены (Ctrl+Z)
  };
  $("#editor").hidden = false;
  $("#edit-bar").hidden = false;
  updateFrameTools();
  showFrame(state.frame);
  drawEditor();
}

function stopEdit() {
  state.edit = null;
  $("#editor").hidden = true;
  $("#edit-bar").hidden = true;
  updateFrameTools();
}

function cancelEdit() {
  if (state.edit && state.edit.history.length && !confirm("Выйти без сохранения изменений зон?")) return;
  stopEdit();
  showFrame(state.frame);
}

// Кнопки поверх кадра: ✏️ — правка, в правке 💾 — сохранить, ✖ — выйти без сохранения, ↶ — отменить.
function updateFrameTools() {
  const info = state.info, editing = !!state.edit;
  $("#frame-tools").hidden = !(info && info.video) || autoRunning();
  if (!info) return;
  const toggle = $("#edit-toggle");
  toggle.textContent = editing ? "💾" : "✏️";
  toggle.title = editing ? "Сохранить зоны и выйти из правки (Enter)" : "Править зоны";
  toggle.disabled = !editing && !info.regions_editable;
  toggle.classList.toggle("active", editing);
  $("#frame-tools").classList.toggle("editing", editing);
  $("#edit-cancel").hidden = !editing;
  $("#edit-undo").hidden = !editing;
  $("#edit-undo").disabled = !editing || !state.edit.history.length;
  $("#save-regions-as").hidden = editing;
  $("#zones").classList.toggle("disabled", editing);
  $("#save-regions-as").disabled = !(info.regions_source === "file" && info.regions_file);
}

function toggleEdit() {
  state.edit ? applyEdit() : startEdit();
}

async function applyEdit() {
  if (!state.edit) return;
  if (!state.edit.history.length) { stopEdit(); showFrame(state.frame); return; }
  const round = (v) => Math.round(v * 10) / 10;
  const regions = Object.fromEntries(state.edit.regions.map((r) => [r.id, { points: r.points.map(([x, y]) => [round(x), round(y)]), shape: r.shape }]));
  try {
    const info = await api("POST", "api/video/regions/edit", { regions });
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
    const target = await api("GET", "api/video/regions/save-target");
    const path = await pickPath("save", "json", target.path, "Сохранить файл разметки");
    if (!path) return;
    const saved = await api("POST", "api/video/regions/save-as", { path });
    toast(`Разметка сохранена: ${saved.path}`, "ok");
    loadPlaylist();
  } catch (error) {
    toast(error.message, "bad");
  }
}

// Запоминает зоны перед изменением — для Ctrl+Z.
function remember() {
  state.edit.history.push(state.edit.regions.map((r) => ({ ...r, points: r.points.map((p) => [...p]) })));
  if (state.edit.history.length > 100) state.edit.history.shift();
  updateFrameTools();
}

function undo() {
  const previous = state.edit.history.pop();
  if (!previous) return;
  state.edit.regions = previous;
  state.edit.vertex = null;
  updateFrameTools();
  drawEditor();
}

// Масштаб: координаты кадра → пиксели картинки на экране.
function editScale() {
  return $("#frame").clientWidth / (state.info.width || 1);
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
    const b = bbox(region.points);
    ctx.fillText(region.id, b.x1 * k + 5, b.y1 * k + 18);
    if (!active) return;
    handles(region).forEach((h) => {
      if (h.plus != null) {
        ctx.beginPath();
        ctx.arc(h.x * k, h.y * k, PLUS, 0, Math.PI * 2);
        ctx.fillStyle = "rgba(17, 17, 17, 0.75)";
        ctx.fill();
        ctx.lineWidth = 1.5;
        ctx.strokeStyle = color;
        ctx.stroke();
        ctx.beginPath();
        ctx.moveTo(h.x * k - 3, h.y * k); ctx.lineTo(h.x * k + 3, h.y * k);
        ctx.moveTo(h.x * k, h.y * k - 3); ctx.lineTo(h.x * k, h.y * k + 3);
        ctx.stroke();
        return;
      }
      const selected = h.vertex != null && h.vertex === state.edit.vertex;
      ctx.fillStyle = "#111";
      ctx.fillRect(h.x * k - HANDLE, h.y * k - HANDLE, HANDLE * 2, HANDLE * 2);
      ctx.fillStyle = selected ? "#ef4444" : color;
      ctx.fillRect(h.x * k - HANDLE + 2, h.y * k - HANDLE + 2, HANDLE * 2 - 4, HANDLE * 2 - 4);
    });
  });
}

function bbox(points) {
  const xs = points.map((p) => p[0]), ys = points.map((p) => p[1]);
  return { x1: Math.min(...xs), y1: Math.min(...ys), x2: Math.max(...xs), y2: Math.max(...ys) };
}

// Маркеры: у прямоугольника — углы и середины сторон (размер), у многоугольника — точки и «+» на сторонах.
function handles(region) {
  if (region.shape !== "rectangle") {
    const n = region.points.length;
    const vertices = region.points.map(([x, y], i) => ({ x, y, vertex: i }));
    const pluses = region.points.map(([x, y], i) => {
      const [nx, ny] = region.points[(i + 1) % n];
      return { x: (x + nx) / 2, y: (y + ny) / 2, plus: i };
    });
    return [...vertices, ...pluses];
  }
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

// Ближайшая к точке сторона: номер её начала и точка на ней.
function nearestSide(points, x, y) {
  let best = null;
  points.forEach(([ax, ay], i) => {
    const [bx, by] = points[(i + 1) % points.length];
    const dx = bx - ax, dy = by - ay;
    const t = Math.max(0, Math.min(1, ((x - ax) * dx + (y - ay) * dy) / (dx * dx + dy * dy || 1)));
    const px = ax + t * dx, py = ay + t * dy;
    const dist = Math.hypot(x - px, y - py);
    if (!best || dist < best.dist) best = { index: i, point: [px, py], dist };
  });
  return best;
}

const clampX = (x) => Math.min(Math.max(0, x), state.info.width);
const clampY = (y) => Math.min(Math.max(0, y), state.info.height);

function pointerPos(event) {
  const rect = $("#editor").getBoundingClientRect();
  const k = editScale();
  return [(event.clientX - rect.left) / k, (event.clientY - rect.top) / k];
}

function hitHandle(region, x, y) {
  const tolerance = (HANDLE + 3) / editScale();
  const all = handles(region);
  // Точки важнее «+»: при перекрытии берётся точка.
  return all.find((h) => h.plus == null && Math.abs(h.x - x) <= tolerance && Math.abs(h.y - y) <= tolerance)
    || all.find((h) => h.plus != null && Math.hypot(h.x - x, h.y - y) <= (PLUS + 3) / editScale());
}

// Добавляет точку на стороне index (прямоугольник становится многоугольником).
function insertPoint(region, index, point) {
  remember();
  region.shape = "polygon";
  region.points.splice(index + 1, 0, [clampX(point[0]), clampY(point[1])]);
  state.edit.vertex = index + 1;
}

function deletePoint(region, index) {
  if (region.points.length <= MIN_POINTS) { toast(`У зоны должно быть не меньше ${MIN_POINTS} точек`, "bad"); return; }
  remember();
  region.points.splice(index, 1);
  state.edit.vertex = null;
}

function bindEditor() {
  const canvas = $("#editor");
  canvas.addEventListener("pointerdown", (event) => {
    if (!state.edit || event.button !== 0) return;
    const [x, y] = pointerPos(event);
    const order = [state.edit.active, ...state.edit.regions.keys()].filter((v, i, a) => a.indexOf(v) === i);
    for (const i of order) {
      const region = state.edit.regions[i];
      let handle = i === state.edit.active ? hitHandle(region, x, y) : null;
      if (!handle && !inside(region.points, x, y)) continue;
      state.edit.active = i;
      state.edit.vertex = handle && handle.vertex != null ? handle.vertex : null;
      state.edit.drag = { handle, start: [x, y], points: region.points.map((p) => [...p]), moved: false };
      canvas.setPointerCapture(event.pointerId);
      drawEditor();
      return;
    }
    state.edit.vertex = null;
    drawEditor();
  });
  canvas.addEventListener("pointermove", (event) => {
    if (!state.edit) return;
    const [x, y] = pointerPos(event);
    const drag = state.edit.drag;
    const region = state.edit.regions[state.edit.active];
    if (!drag) {
      const h = region && hitHandle(region, x, y);
      canvas.style.cursor = h ? (h.plus != null ? "copy" : "pointer")
        : state.edit.regions.some((r) => inside(r.points, x, y)) ? "move" : "crosshair";
      return;
    }
    if (!drag.moved) {
      if (Math.hypot(x - drag.start[0], y - drag.start[1]) * editScale() < 2) return;
      drag.moved = true;
      if (drag.handle && drag.handle.plus != null) {
        // Потянули «+» — появилась новая точка, дальше тянем её.
        insertPoint(region, drag.handle.plus, [drag.handle.x, drag.handle.y]);
        drag.handle = { vertex: drag.handle.plus + 1 };
        drag.points = region.points.map((p) => [...p]);
      } else {
        remember();
      }
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
      region.points = rectPoints({ x1: Math.min(b.x1, b.x2), x2: Math.max(b.x1, b.x2), y1: Math.min(b.y1, b.y2), y2: Math.max(b.y1, b.y2) });
    } else {
      region.points = drag.points.map((p, i) => (i === h.vertex ? [clampX(x), clampY(y)] : p));
    }
    drawEditor();
  });
  const end = () => { if (state.edit && state.edit.drag) { state.edit.drag = null; drawEditor(); } };
  canvas.addEventListener("pointerup", end);
  canvas.addEventListener("pointercancel", end);
  // Двойной щелчок: по точке — удалить её, по стороне — добавить точку.
  canvas.addEventListener("dblclick", (event) => {
    if (!state.edit) return;
    const [x, y] = pointerPos(event);
    const region = state.edit.regions[state.edit.active];
    const handle = hitHandle(region, x, y);
    if (handle && handle.vertex != null) { deletePoint(region, handle.vertex); drawEditor(); return; }
    const points = region.shape === "rectangle" ? rectPoints(bbox(region.points)) : region.points;
    const side = nearestSide(points, x, y);
    if (side && side.dist * editScale() <= HANDLE + 6) {
      region.points = points.map((p) => [...p]);
      insertPoint(region, side.index, side.point);
      drawEditor();
    }
  });
  window.addEventListener("resize", drawEditor);
  $("#frame").addEventListener("load", drawEditor);
  $("#edit-toggle").onclick = toggleEdit;
  $("#edit-cancel").onclick = cancelEdit;
  $("#edit-undo").onclick = undo;
  $("#save-regions-as").onclick = saveRegionsAs;
  $("#save-regions-as-menu").onclick = () => { closeRegionsMenu(); saveRegionsAs(); };
}

function editKey(event) {
  const moves = { ArrowLeft: [-1, 0], ArrowRight: [1, 0], ArrowUp: [0, -1], ArrowDown: [0, 1] };
  if (event.key === "Escape") { event.preventDefault(); cancelEdit(); return; }
  if (event.key === "Enter") { event.preventDefault(); applyEdit(); return; }
  if ((event.ctrlKey || event.metaKey) && event.code === "KeyZ") { event.preventDefault(); undo(); return; }
  const region = state.edit.regions[state.edit.active];
  if ((event.key === "Delete" || event.key === "Backspace") && state.edit.vertex != null && region.shape !== "rectangle") {
    event.preventDefault();
    deletePoint(region, state.edit.vertex);
    drawEditor();
    return;
  }
  if (event.key === "Tab") {
    event.preventDefault();
    state.edit.active = (state.edit.active + 1) % state.edit.regions.length;
    state.edit.vertex = null;
    drawEditor();
    return;
  }
  const move = moves[event.key];
  if (!move) return;
  event.preventDefault();
  remember();
  const b = bbox(region.points);
  const size = event.shiftKey ? 10 : 1;
  const dx = Math.min(Math.max(move[0] * size, -b.x1), state.info.width - b.x2);
  const dy = Math.min(Math.max(move[1] * size, -b.y1), state.info.height - b.y2);
  region.points = region.points.map(([x, y]) => [x + dx, y + dy]);
  drawEditor();
}

