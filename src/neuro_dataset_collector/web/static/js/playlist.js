"use strict";
// Плейлист: видео папки и подпапок, сгруппированные по подпапкам, с числом кадров по классам.

const COLLAPSED_KEY = "ndc.playlist.collapsed";

function loadCollapsed() {
  try { return new Set(JSON.parse(localStorage.getItem(COLLAPSED_KEY) || "[]")); } catch (error) { return new Set(); }
}

function saveCollapsed(set) {
  try { localStorage.setItem(COLLAPSED_KEY, JSON.stringify(Array.from(set))); } catch (error) { /* не критично */ }
}

const collapsedGroups = loadCollapsed();

async function chooseVideo() {
  const path = await pickPath("file", "video", state.info && state.info.video, "Выберите видео");
  if (path) await openVideo(path);
}

async function openVideo(path) {
  if (autoRunning()) { toast("Идёт автонарезка — дождитесь окончания или остановите её", "bad"); return; }
  try {
    pause();
    const info = await api("POST", "api/video/open", { path });
    if (info.markup) toast(`Разметка: ${markupTitle(info.markup)}`, "ok");
    await applyVideo(info, info.frame);
    if (info.playlist_changed) await loadPlaylist(); else renderPlaylist();
  } catch (error) {
    toast(error.message, "bad");
  }
}

async function chooseFolder() {
  const path = await pickPath("dir", null, state.playlist.dir || (state.info && state.info.video), "Папка с видео");
  if (path) await setFolder(path);
}

// Папка с видео: из окна выбора или вставленная в поле «Каталог видео».
async function setFolder(path) {
  try {
    state.playlist = await api("POST", "api/playlist", { path, recursive: $("#playlist-recursive").checked });
    renderPlaylist();
    if (!state.playlist.items.length) { toast("В папке нет видео", "bad"); return; }
    const inList = state.playlist.items.some((item) => item.path === (state.info && state.info.video));
    if (!inList) await openVideo(state.playlist.items[0].path);
  } catch (error) {
    toast(error.message, "bad");
  }
}

async function setRecursive() {
  try {
    state.playlist = await api("POST", "api/playlist", { path: state.playlist.dir, recursive: $("#playlist-recursive").checked });
    renderPlaylist();
  } catch (error) {
    toast(error.message, "bad");
  }
}

async function loadPlaylist() {
  try {
    state.playlist = await api("GET", "api/playlist");
    renderPlaylist();
  } catch (error) {
    toast(error.message, "bad");
  }
}

// Подпапка и имя файла из пути видео относительно папки плейлиста.
function splitName(name) {
  const parts = name.split(/[\\/]/);
  return { group: parts.slice(0, -1).join(" / "), file: parts[parts.length - 1] };
}

const playlistView = { show: "all" };
const SHOW_NOTES = { all: "", new: "только не начатые", done: "только с кадрами" };
const MARK_TITLES = { sidecar: "разметка рядом с видео", production: "боевая разметка", file: "явный файл разметки" };

function renderPlaylist() {
  const current = state.info && state.info.video;
  const autoVideo = autoRunning() ? state.auto.video_path : null;
  const filter = $("#playlist-filter").value.trim().toLowerCase();
  const items = state.playlist.items;
  const input = $("#playlist-path");
  if (document.activeElement !== input) input.value = state.playlist.dir || "";
  input.title = state.playlist.dir || "";
  $("#playlist-recursive").checked = state.playlist.recursive !== false;
  $("#playlist-count").textContent = state.playlist.dir ? `Видео: ${items.length}` : "";
  $("#playlist-filter-note").textContent = SHOW_NOTES[playlistView.show];
  $("#playlist-filter-btn").classList.toggle("on", playlistView.show !== "all");
  const groups = new Map();
  items.forEach((item, index) => {
    if (filter && !item.name.toLowerCase().includes(filter)) return;
    if (playlistView.show === "new" && item.saved) return;
    if (playlistView.show === "done" && !item.saved) return;
    const { group, file } = splitName(item.name);
    if (!groups.has(group)) groups.set(group, []);
    groups.get(group).push({ item, index, file });
  });
  const html = [];
  groups.forEach((entries, group) => {
    const saved = entries.reduce((sum, e) => sum + (e.item.saved || 0), 0);
    const hasCurrent = entries.some((e) => e.item.path === current || e.item.path === autoVideo);
    const collapsed = group && collapsedGroups.has(group) && !hasCurrent && !filter;
    if (group) {
      html.push(`<div class="pl-group" data-group="${escapeHtml(group)}">${collapsed ? "▸" : "▾"} ${escapeHtml(group)}
        <span>· ${entries.length}${saved ? ` · ${saved} кадр.` : ""}</span></div>`);
    }
    if (collapsed) return;
    entries.forEach(({ item, index, file }) => {
      const labels = Object.entries(item.labels || {});
      const total = labels.reduce((sum, [, n]) => sum + n, 0);
      const bar = total ? `<span class="pl-bar">${labels.map(([name, n]) => `<span style="width:${(100 * n) / total}%;background:${classColor(name === state.config.unsorted_label ? "" : name)}"></span>`).join("")}</span>` : "";
      const marks = [
        item.regions ? `<span class="pl-mark ${escapeHtml(item.regions)}" title="${MARK_TITLES[item.regions] || ""}"></span>` : "",
        item.journal ? `<span class="pl-mark journal" title="есть журнал модели"></span>` : "",
        item.rejected ? `<span class="pl-mark rejected" title="отказов: ${item.rejected}"></span>` : "",
      ].join("");
      const detail = labels.map(([name, n]) => `${name}: ${n}`).join(", ");
      const classes = ["pl-item", item.path === current ? "current" : "", item.path === autoVideo ? "auto" : ""].join(" ");
      html.push(`<div class="${classes}" data-index="${index}" title="${escapeHtml(item.path)}${detail ? `\n${escapeHtml(detail)}` : ""}" role="button" tabindex="0">
        <span class="pl-name">${escapeHtml(file)}</span><span class="pl-marks">${marks}</span>${bar}
        <span class="pl-count">${item.saved || ""}</span>
        <button class="pl-folder" type="button" data-folder="${index}" title="Открыть папку с кадрами" aria-label="Открыть папку с кадрами" ${item.saved ? "" : "hidden"}><svg class="ic"><use href="#i-folder"/></svg></button></div>`);
    });
  });
  $("#playlist").innerHTML = html.join("")
    || (state.playlist.dir ? `<p class="muted">${filter || playlistView.show !== "all" ? "ничего не найдено" : "видео не найдено"}</p>`
      : `<p class="muted">Укажите каталог видео вверху</p>`);
  const active = $("#playlist .pl-item.current");
  if (active) active.scrollIntoView({ block: "nearest" });
  renderHistory();
}

function onPlaylistClick(event) {
  const folder = event.target.closest("button[data-folder]");
  if (folder) { openFolder(state.playlist.items[Number(folder.dataset.folder)].path); return; }
  const group = event.target.closest(".pl-group");
  if (group) {
    const name = group.dataset.group;
    collapsedGroups.has(name) ? collapsedGroups.delete(name) : collapsedGroups.add(name);
    saveCollapsed(collapsedGroups);
    renderPlaylist();
    return;
  }
  const item = event.target.closest(".pl-item");
  if (item) openVideo(state.playlist.items[Number(item.dataset.index)].path);
}

async function openFolder(video) {
  try {
    await api("POST", "api/open-folder", { video });
  } catch (error) {
    toast(error.message, "bad");
  }
}

function stepVideo(delta) {
  const items = state.playlist.items;
  if (!items.length) return;
  const current = items.findIndex((item) => item.path === (state.info && state.info.video));
  const next = current < 0 ? 0 : current + delta;
  if (next < 0 || next >= items.length) { toast(delta > 0 ? "Это последнее видео" : "Это первое видео"); return; }
  openVideo(items[next].path);
}

function bindPlaylist() {
  $("#pick-folder").onclick = chooseFolder;
  $("#pick-video").onclick = chooseVideo;
  $("#refresh-playlist").onclick = loadPlaylist;
  $("#playlist-recursive").onchange = setRecursive;
  $("#playlist-path").addEventListener("keydown", (event) => {
    if (event.key === "Enter") { event.preventDefault(); const path = event.target.value.trim(); if (path) setFolder(path); }
  });
  $("#playlist-filter-btn").onclick = (event) => { event.stopPropagation(); toggleMenu("#playlist-show"); };
  $("#playlist").addEventListener("keydown", (event) => {
    if (event.key === "Enter" && event.target.closest(".pl-item")) onPlaylistClick(event);
  });
  $("#playlist").addEventListener("click", onPlaylistClick);
  $("#playlist-filter").addEventListener("input", renderPlaylist);
  $$("#playlist-show button").forEach((b) => (b.onclick = () => {
    playlistView.show = b.dataset.show;
    $$("#playlist-show button").forEach((x) => x.classList.toggle("active", x === b));
    closeMenus();
    renderPlaylist();
  }));
  document.addEventListener("click", (event) => {
    if (event.target.closest("[data-pick-folder]")) chooseFolder();
    if (event.target.closest("[data-pick-video]")) chooseVideo();
  });
}
