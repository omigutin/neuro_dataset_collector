"use strict";
// История по видео: сохранённые кадры (из каталога) и отказы этого запуска; перенос и удаление.

// Группы истории по видео: загруженные записи, сколько всего, тип задачи и классы.
const historyStore = { groups: new Map() };

const REJECT_NAMES = { duplicate: "похож на сохранённый", disk_full: "мало места на диске", empty_region: "зона вне кадра" };
const MODE_NAMES = { manual: "вручную", auto: "авто" };

// Видео, у которых есть что показать: сохранённые кадры, отказы, открытое и нарезаемое сейчас.
function historyVideos() {
  const current = state.info && state.info.video;
  const autoVideo = autoRunning() ? state.auto.video_path : null;
  const list = state.playlist.items
    .filter((item) => item.saved || item.rejected || item.path === current || item.path === autoVideo)
    .map((item) => ({ path: item.path, name: item.name, saved: item.saved, rejected: item.rejected }));
  if (current && !list.some((item) => item.path === current)) list.unshift({ path: current, name: current.split(/[\\/]/).pop() });
  // Сверху — видео, которое режется сейчас, затем открытое.
  const rank = (item) => (item.path === autoVideo ? 0 : item.path === current ? 1 : 2);
  return list.map((item, i) => [rank(item), i, item]).sort((a, b) => a[0] - b[0] || a[1] - b[1]).map((x) => x[2]);
}

function renderHistory() {
  const box = $("#history");
  const videos = historyVideos();
  const keep = new Set(videos.map((v) => v.path));
  $$("#history details").forEach((el) => { if (!keep.has(el.dataset.video)) el.remove(); });
  videos.forEach((video, index) => {
    let el = $$("#history details").find((d) => d.dataset.video === video.path);
    if (!el) {
      el = document.createElement("details");
      el.dataset.video = video.path;
      el.innerHTML = `<summary></summary><div class="h-list"></div><button class="h-more" hidden>Показать ещё</button>`;
      el.addEventListener("toggle", () => { if (el.open && !historyStore.groups.has(video.path)) loadGroup(video.path); });
      el.querySelector(".h-more").onclick = () => loadGroup(video.path, true);
      if (video.path === (state.info && state.info.video) || video.path === (autoRunning() && state.auto.video_path)) el.open = true;
    }
    if (autoRunning() && video.path === state.auto.video_path && !el.dataset.autoOpened) {
      el.dataset.autoOpened = "1";
      el.open = true;
    }
    if (box.children[index] !== el) box.insertBefore(el, box.children[index] || null);
    const group = historyStore.groups.get(video.path);
    const saved = group ? group.total : video.saved || 0;
    const rejected = group ? group.rejected : video.rejected || 0;
    el.querySelector("summary").innerHTML = `${escapeHtml(video.name)} <span class="muted">— ${saved} сохр.${rejected ? `, ${rejected} отказ.` : ""}</span>`;
    if (el.open && !group) loadGroup(video.path);
  });
  if (!videos.length) box.innerHTML = `<p class="muted">Пока ничего не сохранено</p>`;
  else $$("#history > p").forEach((el) => el.remove());
}

async function loadGroup(video, more) {
  if (!video) return;
  const old = historyStore.groups.get(video);
  const offset = more && old ? old.items.filter((i) => i.kind === "saved").length : 0;
  try {
    const data = await api("GET", `api/history?${new URLSearchParams({ video, offset, limit: 100 })}`);
    if (more && old) data.items = old.items.concat(data.items.filter((i) => i.kind === "saved"));
    if (more && old) data.rejected = old.rejected;
    historyStore.groups.set(video, data);
    renderGroup(video);
  } catch (error) {
    toast(error.message, "bad");
  }
}

function renderGroup(video) {
  const el = $$("#history details").find((d) => d.dataset.video === video);
  const group = historyStore.groups.get(video);
  if (!el || !group) { renderHistory(); return; }
  const current = state.info && state.info.video === video ? state.frame : null;
  el.querySelector(".h-list").innerHTML = group.items.map((item, i) => historyItem(group, item, i, current)).join("")
    || `<p class="muted">нет записей</p>`;
  const loaded = group.items.filter((i) => i.kind === "saved").length;
  el.querySelector(".h-more").hidden = loaded >= group.total;
  renderHistory();
}

function historyItem(group, item, index, currentFrame) {
  const time = item.time ? new Date(item.time * 1000).toLocaleTimeString() : "";
  const where = [`кадр ${item.frame}`, item.region ? `зона ${escapeHtml(item.region)}` : "", MODE_NAMES[item.mode] || "", time]
    .filter(Boolean).join(" · ");
  const here = currentFrame === item.frame ? " current" : "";
  if (item.kind === "rejected") {
    const similar = item.similar_to
      ? `<figure><a href="${fileUrl(item.similar_to, group.video)}" target="_blank"><img src="${fileUrl(item.similar_to, group.video)}" loading="lazy" alt=""></a>похож</figure>` : "";
    return `<div class="h-item rejected${here}" data-index="${index}">
      <div class="h-thumbs"><figure><a href="${item.image}" target="_blank"><img src="${item.image}" alt=""></a>не сохранён</figure>${similar}</div>
      <div><div class="h-title">Не сохранён${item.label ? ` (${escapeHtml(item.label)})` : ""}: ${escapeHtml(REJECT_NAMES[item.reason] || item.reason || "")}</div>
        <div class="muted">${where}</div>
        <div class="h-actions"><button data-act="force">Сохранить всё равно</button></div></div></div>`;
  }
  const url = fileUrl(item.path, group.video);
  const moves = group.task === "classify"
    ? group.classes.filter((c) => c !== item.label).map((c) => `<button data-act="relabel" data-label="${escapeHtml(c)}">→ ${escapeHtml(c)}</button>`).join("") : "";
  const status = item.status === "verified" ? " ✓" : item.status === "suspect" ? " ⚠" : "";
  const model = item.model_label && item.model_label !== item.label
    ? `<div class="muted">модель: ${escapeHtml(item.model_label)}${item.confidence != null ? ` ${Number(item.confidence).toFixed(2)}` : ""}</div>` : "";
  return `<div class="h-item${here}" data-index="${index}">
    <div class="h-thumbs"><a href="${url}" target="_blank"><img src="${url}" loading="lazy" alt=""></a></div>
    <div><div class="h-title">${escapeHtml(item.label || (group.task === "classify" ? "без класса" : "кадр"))}${status}</div>
      <div class="muted">${where}</div>${model}
      <div class="h-actions">${moves}<button data-act="delete" class="del" title="В корзину">🗑</button></div></div></div>`;
}

async function goToFrame(video, frame) {
  if (!state.info || state.info.video !== video) await openVideo(video);
  if (!state.info || state.info.video !== video) return;
  pause();
  showFrame(frame);
  renderGroup(video);
}

async function onHistoryClick(event) {
  const itemEl = event.target.closest(".h-item");
  if (!itemEl || event.target.closest("a")) return;
  const video = itemEl.closest("details").dataset.video;
  const item = historyStore.groups.get(video).items[Number(itemEl.dataset.index)];
  const button = event.target.closest("button[data-act]");
  if (!button) { goToFrame(video, item.frame); return; }
  try {
    if (button.dataset.act === "relabel") {
      await api("POST", `api/sample/${item.id}/relabel`, { video, label: button.dataset.label });
      toast(`Кадр перенесён в «${button.dataset.label}»`, "ok");
    } else if (button.dataset.act === "delete") {
      if (!confirm("Удалить этот кадр в корзину?")) return;
      await api("POST", `api/sample/${item.id}/delete`, { video });
      toast("Кадр удалён в корзину", "ok");
    } else if (button.dataset.act === "force") {
      await goToFrame(video, item.frame);
      await saveCurrent(item.label, true, item.region ? [item.region] : null);
    }
    await loadGroup(video);
    loadPlaylist();
    loadCounts();
  } catch (error) {
    toast(error.message, "bad");
  }
}

function bindHistory() {
  $("#history").addEventListener("click", onHistoryClick);
}
