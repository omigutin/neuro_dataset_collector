"use strict";
// Итог: сколько собрано (это видео или весь плейлист), сверка и общая выгрузка.

const summary = { scope: "video" };
const STATUS_NAMES = { pending: "ждут проверки", verified: "проверены", suspect: "подозрит.", rejected: "удалены" };

function openSummary() {
  openDrawer("summary-drawer");
  loadStats();
  pollCheck();
}

function setSummaryScope(scope) {
  summary.scope = scope;
  $$("#summary-drawer .segmented button").forEach((b) => b.classList.toggle("active", b.dataset.scope === scope));
  loadStats();
}

function sep(path) {
  return String(path).includes("\\") ? "\\" : "/";
}

// Папка выгрузки по умолчанию: «_export» в папке плейлиста или рядом с датасетом видео.
function defaultExportDir(data) {
  if (state.playlist.dir) return `${state.playlist.dir}${sep(state.playlist.dir)}_export`;
  const row = data.rows[0];
  if (!row) return "";
  const parent = row.dataset_dir.replace(/[\\/][^\\/]+[\\/]?$/, "");
  return `${parent}${sep(parent)}_export`;
}

async function loadStats() {
  try {
    const data = await api("GET", `/api/stats?scope=${summary.scope}`);
    const used = new Set();
    data.rows.forEach((row) => Object.keys(row.labels).forEach((k) => used.add(k)));
    const columns = data.classes.concat(used.has("") ? [""] : []).concat([...used].filter((k) => k && !data.classes.includes(k)));
    const statusColumns = ["pending", "verified", "suspect"];
    const head = `<tr><th>Видео</th>${columns.map((c) => `<th>${escapeHtml(c || "без класса")}</th>`).join("")}
      <th>всего</th>${statusColumns.map((s) => `<th class="muted">${STATUS_NAMES[s]}</th>`).join("")}<th></th></tr>`;
    const cell = (value) => `<td class="${value ? "" : "zero"}">${value || 0}</td>`;
    const total = (labels) => Object.values(labels).reduce((a, b) => a + b, 0);
    const rows = data.rows.map((row, i) => `<tr data-row="${i}" class="${row.video === (state.info && state.info.video) ? "current" : ""}">
      <td class="name" title="${escapeHtml(row.video)}">${escapeHtml(row.name)}</td>
      ${columns.map((c) => cell(row.labels[c])).join("")}<td><b>${total(row.labels)}</b></td>
      ${statusColumns.map((s) => cell(row.statuses[s])).join("")}
      <td>${total(row.labels) ? `<button class="icon small" data-folder="${i}" title="Открыть папку кадров">📂</button>` : ""}</td></tr>`).join("");
    const foot = data.rows.length > 1 ? `<tr class="total"><td>Итого</td>${columns.map((c) => cell(data.labels[c])).join("")}
      <td><b>${total(data.labels)}</b></td>${statusColumns.map((s) => cell(data.statuses[s])).join("")}<td></td></tr>` : "";
    $("#stats").innerHTML = `<div class="table-wrap"><table class="stats">${head}${rows}${foot}</table></div>`;
    $$("#stats button[data-folder]").forEach((b) => (b.onclick = () => openFolder(data.rows[Number(b.dataset.folder)].video)));
    $$("#stats tr[data-row] td.name").forEach((td) => (td.onclick = () => {
      closeDrawers();
      openVideo(data.rows[Number(td.parentElement.dataset.row)].video);
    }));
    if (!$("#export-dir").value) $("#export-dir").value = defaultExportDir(data);
  } catch (error) {
    $("#stats").textContent = error.message;
  }
}

const scopeText = () => (summary.scope === "playlist" ? "всех видео плейлиста" : "этого видео");

async function runSync() {
  if (!confirm(`Вы просмотрели кадры ${scopeText()} и разложили их по папкам? Сверка отметит их как проверенные.`)) return;
  try {
    const r = await api("POST", "/api/sync", { scope: summary.scope });
    let text = r.summary;
    if (r.unknown_files.length) text += "\n\nФайлы без истории:\n" + r.unknown_files.join("\n");
    $("#sync-result").textContent = text;
    loadStats();
    loadPlaylist();
    loadCounts();
  } catch (error) {
    $("#sync-result").textContent = error.message;
  }
}

async function runExport() {
  const outDir = $("#export-dir").value.trim();
  if (!outDir) { toast("Укажите папку выгрузки", "bad"); return; }
  try {
    const r = await api("POST", "/api/export", {
      out_dir: outDir,
      scope: summary.scope,
      only_new: !$("#export-all").checked,
      include_pending: $("#export-pending").checked,
      full_frames: $("#export-full-frames").checked,
    });
    $("#export-result").textContent = r.summary;
  } catch (error) {
    $("#export-result").textContent = error.message;
  }
}

// ---------------------------------------------------------------- проверка моделью

const check = { timer: null, data: null, hidden: new Set() };

async function startCheck() {
  if (!modelSet()) { toast("Модель не задана: ⚙ → Автонарезка → Модель", "bad"); return; }
  try {
    check.hidden.clear();
    await api("POST", "/api/check/start", { scope: summary.scope, min_conf: Number($("#check-conf").value) || 0 });
    pollCheck();
  } catch (error) {
    toast(error.message, "bad");
  }
}

async function stopCheck() {
  try { await api("POST", "/api/check/stop"); } catch (error) { toast(error.message, "bad"); }
  pollCheck();
}

async function pollCheck() {
  clearTimeout(check.timer);
  let s;
  try { s = await api("GET", "/api/check/status"); } catch (error) { return; }
  check.data = s;
  $("#check-start").disabled = s.running;
  $("#check-stop").disabled = !s.running;
  $("#check-status").textContent = s.running ? `Проверка: ${s.done} из ${s.total}…` : s.error ? `Ошибка: ${s.error}` : s.summary;
  renderCheck();
  if (s.running) check.timer = setTimeout(pollCheck, 700);
}

function checkCard(item, key, actions) {
  return `<figure class="check-card" data-key="${key}">
    <a href="${fileUrl(item.path, item.video)}" target="_blank"><img src="${fileUrl(item.path, item.video)}" loading="lazy" alt=""></a>
    <figcaption>${actions}</figcaption></figure>`;
}

function moveButtons(item, key, exclude) {
  return state.config.classes.filter((c) => c !== item.label && c !== exclude)
    .map((c) => `<button data-act="relabel" data-key="${key}" data-id="${item.id}" data-label="${escapeHtml(c)}">→ ${escapeHtml(c)}</button>`).join("");
}

function renderCheck() {
  const s = check.data;
  if (!s) return;
  const disagreements = s.disagreements.map((d, i) => [d, `d${i}`]).filter(([, key]) => !check.hidden.has(key));
  const duplicates = s.duplicates.map((d, i) => [d, `p${i}`]).filter(([, key]) => !check.hidden.has(key));
  const where = (item) => `<span class="muted">${escapeHtml(item.source)} · кадр ${item.frame}${item.region ? ` · зона ${escapeHtml(item.region)}` : ""}</span>`;
  const cards = disagreements.map(([d, key]) => {
    const agree = d.model_label && d.model_label !== d.label && state.config.classes.includes(d.model_label)
      ? `<button class="primary" data-act="relabel" data-key="${key}" data-id="${d.id}" data-label="${escapeHtml(d.model_label)}" title="Модель права">→ ${escapeHtml(d.model_label)}</button>` : "";
    return checkCard(d, key, `<div>лежит в <b>${escapeHtml(d.label)}</b>, модель: <b style="color:${classColor(d.model_label)}">${escapeHtml(d.model_label || "—")}</b>
      ${d.conf !== null ? Number(d.conf).toFixed(2) : ""}</div>${where(d)}
      <div class="h-actions">${agree}${moveButtons(d, key, d.model_label)}
        <button data-act="hide" data-key="${key}" title="Кадр верный — убрать из списка">✓</button>
        <button data-act="delete" data-key="${key}" data-id="${d.id}" class="del" title="В корзину">🗑</button></div>`);
  }).join("");
  const pairs = duplicates.map(([p, key]) => `<div class="check-pair" data-key="${key}">
      <div class="muted">почти одинаковые (отличие ${p.distance} из 64), классы разные:</div>
      <div class="pair">${p.items.map((item, n) => checkCard({ ...item, video: p.video }, `${key}-${n}`,
        `<div><b>${escapeHtml(item.label)}</b></div>${where(item)}
        <div class="h-actions">${moveButtons(item, key)}<button data-act="delete" data-key="${key}" data-id="${item.id}" data-video="${escapeHtml(p.video)}" class="del" title="В корзину">🗑</button></div>`)).join("")}
      </div><button data-act="hide" data-key="${key}">✓ оба верны</button></div>`).join("");
  $("#check-results").innerHTML = (cards ? `<h4>Модель не согласна (${disagreements.length})</h4><div class="check-grid">${cards}</div>` : "")
    + (pairs ? `<h4>Похожие кадры в разных классах (${duplicates.length})</h4>${pairs}` : "");
}

function checkItem(key) {
  const [kind, index] = [key[0], Number(key.slice(1))];
  return kind === "d" ? check.data.disagreements[index] : check.data.duplicates[index];
}

async function onCheckClick(event) {
  const button = event.target.closest("button[data-act]");
  if (!button) return;
  const key = button.dataset.key;
  const item = checkItem(key);
  const video = button.dataset.video || item.video;
  try {
    if (button.dataset.act === "relabel") {
      await api("POST", `/api/sample/${button.dataset.id}/relabel`, { video, label: button.dataset.label });
      toast(`Кадр перенесён в «${button.dataset.label}»`, "ok");
    } else if (button.dataset.act === "delete") {
      if (!confirm("Удалить этот кадр в корзину?")) return;
      await api("POST", `/api/sample/${button.dataset.id}/delete`, { video });
      toast("Кадр удалён в корзину", "ok");
    }
    check.hidden.add(key);
    renderCheck();
    if (button.dataset.act !== "hide") { loadStats(); loadPlaylist(); loadCounts(); }
  } catch (error) {
    toast(error.message, "bad");
  }
}

function bindSummary() {
  $("#check-start").onclick = startCheck;
  $("#check-stop").onclick = stopCheck;
  $("#check-results").addEventListener("click", onCheckClick);
  $$("#summary-drawer .segmented button").forEach((b) => (b.onclick = () => setSummaryScope(b.dataset.scope)));
  $("#sync").onclick = runSync;
  $("#export").onclick = runExport;
}
