"use strict";
// Итог: сколько собрано (это видео или весь плейлист), сверка и общая выгрузка.

const summary = { scope: "video" };
const STATUS_NAMES = { pending: "ждут проверки", verified: "проверены", suspect: "подозрит.", rejected: "удалены" };

function openSummary() {
  openDrawer("summary-drawer");
  loadStats();
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

function bindSummary() {
  $$("#summary-drawer .segmented button").forEach((b) => (b.onclick = () => setSummaryScope(b.dataset.scope)));
  $("#sync").onclick = runSync;
  $("#export").onclick = runExport;
}
