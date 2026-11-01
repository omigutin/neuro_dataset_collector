"use strict";
// Журнал ответов модели (виртуальный датасет) и участки на проверку: вырезы из видео по кадру и зоне,
// сохранение выделенного выреза клавишей класса.

const journal = { list: [], path: null, data: null, items: [], filterTimer: null };
const review = { path: null, data: null, segment: null, items: [] };
// Вырезы, на которые сейчас действуют клавиши классов: журнал или участок.
const strip = { kind: null, video: null, journal: null, items: [], selected: -1 };

const KIND_CLASS = { missing: "bad", extra: "warn", changed: "warn", marked: "accent" };

// ---------------------------------------------------------------- вкладки справа

function setSide(name) {
  $$(".side-tabs button").forEach((b) => b.classList.toggle("active", b.dataset.side === name));
  $$(".history-panel .side").forEach((el) => (el.hidden = el.id !== `side-${name}`));
  $(".workspace").classList.toggle("side-wide", name !== "history");
  state.side = name;
  try { localStorage.setItem("ndc.side", name); } catch (error) { /* хранилище браузера недоступно */ }
  if (name === "journal") { activateStrip("journal"); if (!journal.list.length) loadJournals(); }
  if (name === "review") activateStrip("review");
}

function activateStrip(kind) {
  const source = kind === "journal" ? journal : review;
  strip.kind = kind;
  strip.items = source.items;
  strip.journal = kind === "journal" ? journal.path : review.data && review.data.journal;
  strip.video = kind === "journal" ? journal.data && journal.data.video : review.data && review.data.video;
  strip.selected = -1;
}

// ---------------------------------------------------------------- общий показ вырезов

function cropUrl(video, item, journalPath) {
  const query = new URLSearchParams({ video, frame: item.frame, zone: item.zone });
  if (journalPath) query.set("journal", journalPath);
  return `/api/journal/crop?${query}`;
}

function renderStrip(container, video, journalPath, items, append) {
  const html = items.map((item, i) => {
    const color = classColor(item.label);
    const conf = item.conf !== null && item.conf !== undefined ? ` ${Number(item.conf).toFixed(2)}` : "";
    const saved = item.saved ? `<span class="saved">✓ ${escapeHtml(item.saved)}</span>` : "";
    return `<figure class="crop${item.event ? " event" : ""}${item.saved ? " done" : ""}" data-i="${i}" style="--c:${color}">
      <img src="${cropUrl(video, item, journalPath)}" loading="lazy" alt="">
      <figcaption><b>${escapeHtml(item.label || "—")}</b>${conf}${saved}
        <span class="muted">к. ${item.frame} · з. ${escapeHtml(item.zone)}${item.state ? ` · ${escapeHtml(item.state)}` : ""}</span></figcaption>
    </figure>`;
  }).join("");
  if (append) container.insertAdjacentHTML("beforeend", html.replace(/data-i="(\d+)"/g, (_, i) => `data-i="${Number(i) + container.children.length}"`));
  else container.innerHTML = html || `<p class="muted">Нет кадров</p>`;
  markSelected();
}

function markSelected() {
  const container = strip.kind === "journal" ? $("#journal-grid") : $("#review-grid");
  $$(".strip figure.crop").forEach((el) => el.classList.remove("selected"));
  const el = container && container.querySelector(`figure[data-i="${strip.selected}"]`);
  if (el) { el.classList.add("selected"); el.scrollIntoView({ block: "nearest" }); }
}

// Видео журнала или участка на этом компьютере (журнал мог быть записан на другом).
async function localVideo(video) {
  const data = await api("GET", `/api/resolve-video?${new URLSearchParams({ path: video })}`);
  return data.path;
}

async function selectItem(index) {
  const item = strip.items[index];
  if (!item) return;
  strip.selected = index;
  markSelected();
  try {
    const path = await localVideo(strip.video);
    await goToFrame(path, item.frame);
  } catch (error) {
    toast(error.message, "bad");
  }
}

function onStripClick(kind, event) {
  const fig = event.target.closest("figure.crop");
  if (!fig) return;
  if (strip.kind !== kind) activateStrip(kind);
  selectItem(Number(fig.dataset.i));
}

// Клавиша класса при выделенном вырезе на том же кадре — сохранить этот вырез. true — клавиша обработана.
function stripKey(label, force) {
  if (!strip.kind || state.side !== strip.kind || strip.selected < 0) return false;
  const item = strip.items[strip.selected];
  if (!item || item.frame !== state.frame) return false;
  saveStripItem(item, label, force);
  return true;
}

async function saveStripItem(item, label, force) {
  try {
    const data = await api("POST", "/api/journal/save", {
      video: strip.video, frame: item.frame, zone: item.zone, label, journal: strip.journal || null, force,
    });
    data.results.forEach((r) => toast(r.message, r.ok ? "ok" : "bad"));
    if (data.results.some((r) => r.ok)) {
      item.saved = label || (state.config.task === "classify" ? "без класса" : "сохранён");
      const container = strip.kind === "journal" ? $("#journal-grid") : $("#review-grid");
      const fig = container.querySelector(`figure[data-i="${strip.selected}"]`);
      if (fig) {
        fig.classList.add("done");
        fig.querySelector("figcaption b").insertAdjacentHTML("afterend", `<span class="saved">✓ ${escapeHtml(item.saved)}</span>`);
      }
      if (strip.selected + 1 < strip.items.length) selectItem(strip.selected + 1);
    }
    loadCounts().then(renderPlaylist);
    if (state.info && state.info.video) loadGroup(state.info.video);
  } catch (error) {
    toast(error.message, "bad");
  }
}

// ---------------------------------------------------------------- журнал

async function loadJournals(select) {
  try {
    const data = await api("GET", "/api/journals");
    journal.list = data.items;
    renderJournalSelect(select);
  } catch (error) {
    toast(error.message, "bad");
  }
}

function renderJournalSelect(select) {
  const current = select || journal.path || matchingJournal();
  $("#journal-select").innerHTML = journal.list.length
    ? journal.list.map((j) => `<option value="${escapeHtml(j.path)}" title="${escapeHtml(j.path)}">${escapeHtml(j.name)}${j.run_id ? ` · ${escapeHtml(j.run_id)}` : ""}${j.source === "collector" ? " · нарезчик" : j.source === "regression" ? " · прогон" : ""}</option>`).join("")
    : `<option value="">журналов нет — «Прогнать моделью» или 📂</option>`;
  if (current && journal.list.some((j) => j.path === current)) $("#journal-select").value = current;
  const chosen = $("#journal-select").value || null;
  if (chosen !== journal.path || !journal.data) openJournalPath(chosen);
}

// Журнал открытого видео (по имени видео), если он есть.
function matchingJournal() {
  const name = state.info && state.info.video ? fileName(state.info.video) : null;
  const found = journal.list.find((j) => j.video && fileName(j.video) === name);
  return found ? found.path : null;
}

async function openJournalPath(path) {
  journal.path = path;
  journal.data = null;
  journal.items = [];
  $("#jf-zone").innerHTML = `<option value="">все зоны</option>`;
  $("#jf-labels").innerHTML = "";
  if (!path) { $("#journal-meta").textContent = ""; $("#journal-grid").innerHTML = ""; $("#journal-count").textContent = ""; return; }
  await queryJournal(false, true);
}

function journalFilter() {
  const num = (id) => ($(id).value === "" ? null : Number($(id).value));
  return {
    zones: $("#jf-zone").value ? [$("#jf-zone").value] : [],
    labels: $$("#jf-labels input:checked").map((el) => el.value),
    conf_min: num("#jf-min"),
    conf_max: num("#jf-max"),
    flips: $("#jf-flips").checked,
  };
}

async function queryJournal(more, first) {
  if (!journal.path) return;
  const offset = more ? journal.items.length : 0;
  try {
    const data = await api("POST", "/api/journal/query", { path: journal.path, filter: journalFilter(), offset, limit: 60 });
    journal.data = data;
    if (first) renderJournalFilters(data);
    journal.items = more ? journal.items.concat(data.items) : data.items;
    const parts = [fileName(data.video), data.model && `модель ${data.model}`, data.run_id && `прогон ${data.run_id}`,
      `${data.rows} ответов`].filter(Boolean);
    $("#journal-meta").textContent = parts.join(" · ");
    $("#journal-meta").title = data.video;
    const counts = Object.entries(data.counts).map(([k, v]) => `${k || "—"}: ${v}`).join(", ");
    $("#journal-count").textContent = `Подходит: ${data.total}${counts ? ` (${counts})` : ""}`;
    renderStrip($("#journal-grid"), data.video, data.path, more ? data.items : journal.items, more);
    $("#journal-more").hidden = journal.items.length >= data.total;
    if (strip.kind === "journal" || state.side === "journal") {
      const selected = more ? strip.selected : -1;
      activateStrip("journal");
      strip.selected = selected;
      markSelected();
    }
  } catch (error) {
    $("#journal-meta").textContent = error.message;
  }
}

function renderJournalFilters(data) {
  $("#jf-zone").innerHTML = `<option value="">все зоны</option>` + data.zones.map((z) => `<option value="${escapeHtml(z)}">зона ${escapeHtml(z)}</option>`).join("");
  const labels = data.labels.concat([""]);
  $("#jf-labels").innerHTML = labels.map((l) =>
    `<label class="check chip"><input type="checkbox" value="${escapeHtml(l)}"><span class="swatch" style="background:${classColor(l)}"></span>${escapeHtml(l || "нет ответа")}</label>`).join("");
}

function scheduleJournalQuery() {
  clearTimeout(journal.filterTimer);
  journal.filterTimer = setTimeout(() => queryJournal(false), 300);
}

async function pickJournal() {
  const start = journal.path || state.playlist.dir || (state.info && state.info.video);
  const path = await pickPath("file", "journal", start, "Журнал ответов модели (….predictions.csv)");
  if (!path) return;
  await openJournal(path);
}

async function openJournal(path) {
  try {
    const data = await api("POST", "/api/journals/open", { path });
    journal.list = data.items;
    journal.data = null;
    setSide("journal");
    renderJournalSelect(data.path);
  } catch (error) {
    toast(error.message, "bad");
  }
}

// Прогон моделью закончился — показать его журнал.
function journalsChanged(path) {
  if (state.side === "journal" || path) loadJournals(path ? path.replace(/\.predictions\.(csv|json)$/, "") : undefined);
}

// ---------------------------------------------------------------- участки

async function pickReview() {
  const path = await pickPath("file", "review", review.path || state.playlist.dir, "Участки на проверку (….review.json)");
  if (path) openReview(path);
}

async function openReview(path) {
  try {
    review.data = await api("GET", `/api/review?${new URLSearchParams({ path })}`);
    review.path = path;
    review.segment = null;
    review.items = [];
    $("#review-name").textContent = fileName(path);
    $("#review-name").title = path;
    $("#review-name").classList.remove("muted");
    $("#review-grid").innerHTML = "";
    setSide("review");
    renderReview();
  } catch (error) {
    toast(error.message, "bad");
  }
}

function frameTime(frame) {
  const fps = state.info && state.info.fps;
  if (!fps) return `к. ${frame}`;
  const s = Math.floor(frame / fps);
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`;
}

function renderReview() {
  const data = review.data;
  if (!data) return;
  const left = data.segments.filter((s) => !s.done).length;
  $("#review-list").innerHTML = `<div class="muted small">${escapeHtml(fileName(data.video))}${data.run_id ? ` · прогон ${escapeHtml(data.run_id)}` : ""}
      · осталось ${left} из ${data.segments.length}${data.journal ? "" : " · журнала нет — ответов модели не будет"}</div>`
    + data.segments.map((s) => `<div class="seg${s.done ? " done" : ""}${review.segment === s.index ? " active" : ""}" data-i="${s.index}">
      <input type="checkbox" data-done ${s.done ? "checked" : ""} title="Участок разобран">
      <span class="kind ${KIND_CLASS[s.kind] || ""}">${escapeHtml(s.kind_name || s.kind)}</span>
      <span>зона ${escapeHtml(s.zone)} · ${frameTime(s.start_frame)}–${frameTime(s.end_frame)}</span>
      ${s.expected || s.actual ? `<div class="muted">${escapeHtml(s.expected || "—")} → ${escapeHtml(s.actual || "—")}</div>` : ""}
      ${s.comment ? `<div class="muted">${escapeHtml(s.comment)}</div>` : ""}</div>`).join("");
}

async function onReviewClick(event) {
  const row = event.target.closest(".seg");
  if (!row) return;
  const index = Number(row.dataset.i);
  if (event.target.matches("input[data-done]")) {
    try {
      review.data = await api("POST", "/api/review/done", { path: review.path, index, done: event.target.checked });
      renderReview();
    } catch (error) {
      toast(error.message, "bad");
    }
    return;
  }
  const segment = review.data.segments[index];
  review.segment = index;
  renderReview();
  try {
    const data = await api("GET", `/api/review/items?${new URLSearchParams({ path: review.path, index })}`);
    review.items = data.items;
    activateStrip("review");
    renderStrip($("#review-grid"), review.data.video, review.data.journal, review.items);
    const eventIndex = review.items.findIndex((item) => item.event);
    await selectItem(eventIndex >= 0 ? eventIndex : 0);
    renderReview();
    toast(`Участок: кадры ${segment.start_frame}–${segment.end_frame}, событие — кадр ${segment.event_frame}`);
  } catch (error) {
    toast(error.message, "bad");
  }
}

// ---------------------------------------------------------------- запуск

// Ссылка с параметрами: ?journal=… — открыть журнал, ?review=… — участки (так открывает Production Counter).
async function openFromLink() {
  const params = new URLSearchParams(location.search);
  if (params.get("review")) await openReview(params.get("review"));
  else if (params.get("journal")) await openJournal(params.get("journal"));
}

function bindJournal() {
  $$(".side-tabs button").forEach((b) => (b.onclick = () => setSide(b.dataset.side)));
  $("#journal-select").onchange = () => openJournalPath($("#journal-select").value || null);
  $("#journal-open").onclick = pickJournal;
  $("#journal-refresh").onclick = () => { journal.data = null; loadJournals(); };
  $("#journal-more").onclick = () => queryJournal(true);
  $("#jf-zone").onchange = scheduleJournalQuery;
  $("#jf-flips").onchange = scheduleJournalQuery;
  $("#jf-min").oninput = scheduleJournalQuery;
  $("#jf-max").oninput = scheduleJournalQuery;
  $("#jf-labels").onchange = scheduleJournalQuery;
  $("#journal-grid").addEventListener("click", (e) => onStripClick("journal", e));
  $("#review-grid").addEventListener("click", (e) => onStripClick("review", e));
  $("#review-list").addEventListener("click", onReviewClick);
  $("#review-open").onclick = pickReview;
  let side = "history";
  try { side = localStorage.getItem("ndc.side") || "history"; } catch (error) { /* хранилище браузера недоступно */ }
  setSide(["history", "journal", "review"].includes(side) && side !== "review" ? side : "history");
}
