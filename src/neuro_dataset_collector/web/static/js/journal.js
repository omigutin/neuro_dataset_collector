"use strict";
// Журнал ответов модели (виртуальный датасет) и участки на проверку: вырезы из видео по кадру и зоне,
// просмотр кадра целиком, сохранение выделенного выреза клавишей или кнопкой класса.

const journal = { list: [], path: null, data: null, items: [], filterTimer: null };
const review = { path: null, data: null, segment: null, items: [], show: "open" };
// Вырезы, на которые сейчас действуют клавиши классов: журнала или участка.
const strip = { kind: null, video: null, journal: null, items: [], selected: -1 };

// ---------------------------------------------------------------- общий показ вырезов

const stripGrid = (kind) => (kind === "journal" ? $("#journal-grid") : $("#review-grid"));

function activateStrip(kind) {
  const source = kind === "journal" ? journal : review;
  const items = kind === "journal" ? journal.items : review.items;
  if (strip.kind !== kind || strip.items !== items) strip.selected = -1;
  strip.kind = kind;
  strip.items = items;
  strip.journal = kind === "journal" ? journal.path : source.data && source.data.journal;
  strip.video = source.data && source.data.video;
  renderPreview();
}

function cropUrl(video, item, journalPath, kind = "crop") {
  const query = new URLSearchParams({ video, frame: item.frame, zone: item.zone });
  if (journalPath) query.set("journal", journalPath);
  return `api/journal/${kind}?${query}`;
}

function cropHtml(item, i, video, journalPath) {
  const conf = item.conf !== null && item.conf !== undefined ? `<span class="muted">${Number(item.conf).toFixed(2)}</span>` : "";
  const saved = item.saved ? `<span class="saved">сохранён: ${escapeHtml(item.saved)}</span>` : "";
  const time = journalPath && journal.data && journal.data.fps ? fmtTime(item.frame / journal.data.fps) : `к. ${item.frame}`;
  return `<figure class="crop${item.event ? " event" : ""}${item.saved ? " done" : ""}" data-i="${i}" style="--c:${classColor(item.label)}">
    <img src="${cropUrl(video, item, journalPath)}" loading="lazy" alt="Кадр ${item.frame}, зона ${escapeHtml(item.zone)}">
    <figcaption><b>${escapeHtml(item.label || "нет ответа")}</b>${conf}${saved}
      <span class="where">${time} · зона ${escapeHtml(item.zone)}${item.state ? ` · ${escapeHtml(item.state)}` : ""}</span></figcaption></figure>`;
}

function renderStrip(kind, video, journalPath, items, append) {
  const container = stripGrid(kind);
  const offset = append ? container.querySelectorAll("figure.crop").length : 0;
  const html = items.map((item, i) => cropHtml(item, i + offset, video, journalPath)).join("");
  if (append) container.insertAdjacentHTML("beforeend", html);
  else container.innerHTML = html || `<p class="muted">Ничего не подходит — измените выборку</p>`;
  markSelected();
}

function markSelected() {
  $$(".strip figure.crop.selected").forEach((el) => el.classList.remove("selected"));
  const container = strip.kind && stripGrid(strip.kind);
  const el = container && container.querySelector(`figure[data-i="${strip.selected}"]`);
  if (!el) return;
  el.classList.add("selected");
  // Прокручивается только лента вырезов, а не вся страница.
  const top = el.offsetTop, bottom = top + el.offsetHeight;
  if (top < container.scrollTop) container.scrollTop = top - 4;
  else if (bottom > container.scrollTop + container.clientHeight) container.scrollTop = bottom - container.clientHeight + 4;
}

// Видео журнала или участка на этом компьютере (журнал мог быть записан на другом).
async function localVideo(video) {
  const data = await api("GET", `api/resolve-video?${new URLSearchParams({ path: video })}`);
  return data.path;
}

function selectItem(index) {
  if (index < 0 || index >= strip.items.length) return;
  strip.selected = index;
  markSelected();
  renderPreview();
}

// Кадр целиком с обведённой зоной и сведения о выбранном вырезе.
function renderPreview() {
  const item = strip.items[strip.selected];
  if (strip.kind === "journal") {
    $("#jp-empty").hidden = !!item;
    $("#jp-image").hidden = !item;
    if (!item) { $("#jp-info").innerHTML = ""; return; }
    $("#jp-image").src = cropUrl(strip.video, item, strip.journal, "frame");
    const fps = journal.data && journal.data.fps;
    const prev = strip.items[strip.selected - 1];
    $("#jp-info").innerHTML = `<div><b>Кадр ${item.frame}</b>${fps ? ` · ${fmtTime(item.frame / fps)}` : ""} · зона ${escapeHtml(item.zone)}</div>
      <div class="muted">Модель: <b style="color:${classColor(item.label)}">${escapeHtml(item.label || "нет ответа")}</b>
        ${item.conf !== null && item.conf !== undefined ? Number(item.conf).toFixed(2) : ""}
        ${prev && prev.zone === item.zone ? ` · в выборке перед ним: ${escapeHtml(prev.label || "нет ответа")}` : ""}</div>
      ${item.state ? `<div class="muted">Машина состояний: ${escapeHtml(item.state)}</div>` : ""}
      ${item.saved ? `<div style="color:var(--ok)">Сохранён: ${escapeHtml(item.saved)}</div>` : ""}`;
  } else if (strip.kind === "review") {
    $("#rv-image").hidden = !item;
    if (!item) { $("#rv-info").textContent = ""; return; }
    $("#rv-image").src = cropUrl(strip.video, item, strip.journal, "frame");
    $("#rv-info").innerHTML = `Кадр ${item.frame}${item.event ? " — <b>кадр события</b>" : ""} · зона ${escapeHtml(item.zone)}
      ${item.label ? ` · модель: <b style="color:${classColor(item.label)}">${escapeHtml(item.label)}</b> ${item.conf !== null ? Number(item.conf).toFixed(2) : ""}` : ""}
      ${item.state ? ` · ${escapeHtml(item.state)}` : ""}`;
  }
}

function onStripClick(kind, event) {
  const fig = event.target.closest("figure.crop");
  if (!fig) return;
  if (strip.kind !== kind) activateStrip(kind);
  selectItem(Number(fig.dataset.i));
}

async function openStripInCut() {
  const item = strip.items[strip.selected];
  if (!item) return;
  try {
    await goToFrame(await localVideo(strip.video), item.frame);
  } catch (error) {
    toast(error.message, "bad");
  }
}

// Клавиши в журнале и на участках: ← → — соседний вырез, Enter — кадр в нарезке, клавиша класса — сохранить.
function stripKeyEvent(event) {
  if (!strip.kind || state.view !== strip.kind) return;
  if (event.key === "ArrowRight" || event.key === "ArrowDown") { event.preventDefault(); selectItem(strip.selected + 1); return; }
  if (event.key === "ArrowLeft" || event.key === "ArrowUp") { event.preventDefault(); selectItem(Math.max(0, strip.selected - 1)); return; }
  if (event.key === "Enter") { event.preventDefault(); openStripInCut(); return; }
  const label = classOfKey(event);
  if (label === undefined) return;
  event.preventDefault();
  saveStripItem(label, event.shiftKey);
}

async function saveStripItem(label, force) {
  const item = strip.items[strip.selected];
  if (!item) { toast("Сначала выберите вырез", "bad"); return; }
  try {
    const data = await api("POST", "api/journal/save", {
      video: strip.video, frame: item.frame, zone: item.zone, label, journal: strip.journal || null, force,
    });
    data.results.forEach((r) => toast(r.message, r.ok ? "ok" : "bad"));
    if (data.results.some((r) => r.ok)) {
      item.saved = label || (state.config.task === "classify" ? "без класса" : "сохранён");
      const fig = stripGrid(strip.kind).querySelector(`figure[data-i="${strip.selected}"]`);
      if (fig) fig.outerHTML = cropHtml(item, strip.selected, strip.video, strip.journal);
      if (strip.selected + 1 < strip.items.length) selectItem(strip.selected + 1); else { markSelected(); renderPreview(); }
    }
    loadCounts().then(renderPlaylist);
    if (hasVideo()) { loadGroup(state.info.video); loadRecent(); loadTimeline(); }
  } catch (error) {
    toast(error.message, "bad");
  }
}

// Кнопки классов в журнале и на участках.
function renderStripClasses() {
  const html = classButtons(false);
  $("#jp-classes").innerHTML = html;
  $("#rv-classes").innerHTML = html;
}

function onStripClassClick(event) {
  const button = event.target.closest(".cls");
  if (button) saveStripItem(button.dataset.label || null, event.shiftKey);
}

// ---------------------------------------------------------------- журнал

async function loadJournals(select) {
  try {
    const data = await api("GET", "api/journals");
    journal.list = data.items;
    renderJournalSelect(select);
    loadTimeline();
  } catch (error) {
    toast(error.message, "bad");
  }
}

function journalTitle(j) {
  const source = j.source === "collector" ? "сборщик" : j.source === "regression" ? `прогон ${j.run_id || ""}`.trim() : j.source;
  return `${j.name}${source ? ` · ${source}` : ""}`;
}

function renderJournalSelect(select) {
  const current = select || journal.path || matchingJournal();
  $("#journal-select").innerHTML = journal.list.length
    ? journal.list.map((j) => `<option value="${escapeHtml(j.path)}" title="${escapeHtml(j.path)}">${escapeHtml(journalTitle(j))}</option>`).join("")
    : `<option value="">журналов нет</option>`;
  if (current && journal.list.some((j) => j.path === current)) $("#journal-select").value = current;
  const chosen = $("#journal-select").value || null;
  if (chosen !== journal.path || !journal.data) openJournalPath(chosen);
}

// Журнал открытого видео (по имени видео), если он есть.
function matchingJournal() {
  const name = hasVideo() ? fileName(state.info.video) : null;
  const found = journal.list.find((j) => j.video && fileName(j.video) === name);
  return found ? found.path : null;
}

async function openJournalPath(path) {
  journal.path = path;
  journal.data = null;
  journal.items = [];
  $("#jf-zone").innerHTML = `<option value="">все зоны</option>`;
  $("#jf-labels").innerHTML = "";
  if (!path) {
    $("#journal-meta").textContent = "";
    $("#journal-grid").innerHTML = `<p class="muted">Журналов пока нет. Запустите «Задачи → Прогнать моделью» или откройте файл журнала (….predictions.csv), например из регрессионного прогона.</p>`;
    $("#journal-count").textContent = "Журнал не выбран";
    $("#journal-counts").textContent = "";
    if (state.view === "journal") activateStrip("journal");
    return;
  }
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
    const data = await api("POST", "api/journal/query", { path: journal.path, filter: journalFilter(), offset, limit: 60 });
    journal.data = data;
    if (first) renderJournalFilters(data);
    journal.items = more ? journal.items.concat(data.items) : data.items;
    const parts = [fileName(data.video), data.model && `модель ${data.model}`, `${data.rows} ответов`].filter(Boolean);
    $("#journal-meta").textContent = parts.join(" · ");
    $("#journal-meta").title = data.video;
    $("#journal-count").textContent = `Подходит ${data.total}`;
    $("#journal-counts").textContent = Object.entries(data.counts).map(([k, v]) => `${k || "нет ответа"}: ${v}`).join(" · ");
    const selected = more ? strip.selected : -1;
    renderStrip("journal", data.video, data.path, more ? data.items : journal.items, more);
    $("#journal-more").hidden = journal.items.length >= data.total;
    if (state.view === "journal") {
      activateStrip("journal");
      selectItem(selected >= 0 ? selected : 0);
    }
  } catch (error) {
    $("#journal-meta").textContent = error.message;
  }
}

function renderJournalFilters(data) {
  $("#jf-zone").innerHTML = `<option value="">все зоны</option>` + data.zones.map((z) => `<option value="${escapeHtml(z)}">зона ${escapeHtml(z)}</option>`).join("");
  const labels = data.labels.concat([""]);
  $("#jf-labels").innerHTML = labels.map((l) =>
    `<label><input type="checkbox" value="${escapeHtml(l)}"><span class="swatch" style="background:${classColor(l)}"></span>${escapeHtml(l || "нет ответа")}<span class="n">${data.counts[l] || 0}</span></label>`).join("")
    + `<p class="hint">Ничего не отмечено — все ответы.</p>`;
}

function scheduleJournalQuery() {
  clearTimeout(journal.filterTimer);
  journal.filterTimer = setTimeout(() => queryJournal(false), 300);
}

async function pickJournal() {
  const start = journal.path || state.playlist.dir || (state.info && state.info.video);
  const path = await pickPath("file", "journal", start, "Журнал ответов модели (….predictions.csv)");
  if (path) await openJournal(path);
}

async function openJournal(path) {
  try {
    const data = await api("POST", "api/journals/open", { path });
    journal.list = data.items;
    journal.data = null;
    setView("journal");
    renderJournalSelect(data.path);
  } catch (error) {
    toast(error.message, "bad");
  }
}

// Прогон моделью закончился — перечитать журналы и показать его.
function journalsChanged(path) {
  loadJournals(path ? path.replace(/\.predictions\.(csv|json)$/, "") : undefined);
}

// ---------------------------------------------------------------- участки

async function pickReview() {
  const path = await pickPath("file", "review", review.path || state.playlist.dir, "Участки на проверку (….review.json)");
  if (path) openReview(path);
}

async function openReview(path) {
  try {
    review.data = await api("GET", `api/review?${new URLSearchParams({ path })}`);
    review.path = path;
    review.segment = null;
    review.items = [];
    $("#review-name").textContent = path;
    $("#review-grid").innerHTML = "";
    setView("review");
    renderReview();
    loadTimeline();
    const first = review.data.segments.find((s) => !s.done) || review.data.segments[0];
    if (first) selectSegment(first.index);
  } catch (error) {
    toast(error.message, "bad");
  }
}

function segTime(frame) {
  const fps = state.info && state.info.fps && fileName(state.info.video) === fileName(review.data.video) ? state.info.fps : null;
  return fps ? fmtTime(frame / fps) : `к. ${frame}`;
}

function renderReview() {
  const data = review.data;
  if (!data) return;
  const total = data.segments.length, left = data.segments.filter((s) => !s.done).length;
  $("#review-badge").hidden = !left;
  $("#review-badge").textContent = left;
  $("#review-progress").hidden = false;
  $("#review-progress .progress > div").style.width = `${total ? (100 * (total - left)) / total : 0}%`;
  $("#review-progress span").textContent = `Разобрано ${total - left} из ${total} · ${fileName(data.video)}${data.journal ? "" : " · журнала нет — ответов модели не будет"}`;
  const shown = data.segments.filter((s) => review.show === "all" || !s.done || s.index === review.segment);
  $("#review-list").innerHTML = shown.map((s) => `<div class="seg-card${s.done ? " done" : ""}${review.segment === s.index ? " active" : ""}" data-i="${s.index}" role="button" tabindex="0">
      <div class="seg-top"><span class="kind ${escapeHtml(s.kind)}">${escapeHtml(s.kind_name || s.kind)}</span><b>Зона ${escapeHtml(s.zone)}</b>
        <span class="spacer"></span><span class="mono muted small">${segTime(s.start_frame)}–${segTime(s.end_frame)}</span></div>
      ${s.expected || s.actual ? `<div>${escapeHtml(s.expected || "—")} → ${escapeHtml(s.actual || "—")}</div>` : ""}
      ${s.comment ? `<div class="muted small">${escapeHtml(s.comment)}</div>` : ""}
      ${s.done ? `<div class="small" style="color:var(--ok)">разобран</div>` : ""}</div>`).join("")
    || `<p class="muted">${total ? "Все участки разобраны" : "Участков нет"}</p>`;
  renderSegmentHead();
}

function renderSegmentHead() {
  const s = review.data && review.data.segments[review.segment];
  if (!s) { $("#rv-head").innerHTML = `<div class="video-title muted">Выберите участок слева</div>`; return; }
  $("#rv-head").innerHTML = `<span class="kind ${escapeHtml(s.kind)}">${escapeHtml(s.kind_name || s.kind)}</span>
    <div><div class="video-title">Зона ${escapeHtml(s.zone)} · ${segTime(s.start_frame)}–${segTime(s.end_frame)}</div>
      <div class="muted small">${escapeHtml(s.expected || "—")} → ${escapeHtml(s.actual || "—")}${s.comment ? ` · ${escapeHtml(s.comment)}` : ""}</div></div>
    <span class="spacer"></span>
    <button class="btn" type="button" data-strip-open>Открыть в нарезке</button>
    <button class="btn ${s.done ? "" : "primary"}" type="button" data-seg-done>${s.done ? "Вернуть в работу" : "Отметить разобранным"}</button>`;
}

async function selectSegment(index) {
  review.segment = index;
  renderReview();
  try {
    const data = await api("GET", `api/review/items?${new URLSearchParams({ path: review.path, index })}`);
    review.items = data.items;
    activateStrip("review");
    renderStrip("review", review.data.video, review.data.journal, review.items);
    const eventIndex = review.items.findIndex((item) => item.event);
    selectItem(eventIndex >= 0 ? eventIndex : 0);
  } catch (error) {
    toast(error.message, "bad");
  }
}

async function setSegmentDone(done) {
  try {
    review.data = await api("POST", "api/review/done", { path: review.path, index: review.segment, done });
    const next = done ? review.data.segments.find((s) => !s.done && s.index > review.segment) || review.data.segments.find((s) => !s.done) : null;
    renderReview();
    loadTimeline();
    if (next) selectSegment(next.index);
  } catch (error) {
    toast(error.message, "bad");
  }
}

function onReviewClick(event) {
  const row = event.target.closest(".seg-card");
  if (row) selectSegment(Number(row.dataset.i));
}

// ---------------------------------------------------------------- запуск

// Ссылка с параметрами (так открывает Production Counter): ?review=… — участки, ?journal=… — журнал,
// ?video=…&frame=… — видео на кадре; &back=адрес&back_title=текст — кнопка возврата в верхней строке.
async function openFromLink() {
  const params = new URLSearchParams(location.search);
  if (params.get("back")) {
    $("#back-link").href = params.get("back");
    $("#back-link").textContent = `← ${params.get("back_title") || "Назад"}`;
    $("#back-link").hidden = false;
  }
  if (params.get("video")) {
    try {
      await goToFrame(await localVideo(params.get("video")), Number(params.get("frame")) || 0);
    } catch (error) {
      toast(error.message, "bad");
    }
  }
  if (params.get("review")) await openReview(params.get("review"));
  else if (params.get("journal")) await openJournal(params.get("journal"));
}

function bindJournal() {
  viewHandlers.journal = () => {
    activateStrip("journal");
    if (!journal.list.length) loadJournals();
    else if (strip.selected < 0 && journal.items.length) selectItem(0);
  };
  viewHandlers.review = () => {
    activateStrip("review");
    if (strip.selected < 0 && review.items.length) selectItem(0);
  };
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
  $("#jp-classes").addEventListener("click", onStripClassClick);
  $("#rv-classes").addEventListener("click", onStripClassClick);
  $("#review-list").addEventListener("click", onReviewClick);
  $("#review-open").onclick = pickReview;
  $$("#review-filter button").forEach((b) => (b.onclick = () => {
    review.show = b.dataset.review;
    $$("#review-filter button").forEach((x) => x.classList.toggle("active", x === b));
    renderReview();
  }));
  document.addEventListener("click", (event) => {
    const move = event.target.closest("[data-strip-move]");
    if (move) selectItem(Math.max(0, strip.selected + Number(move.dataset.stripMove)));
    if (event.target.closest("[data-strip-open]")) openStripInCut();
    const done = event.target.closest("[data-seg-done]");
    if (done) setSegmentDone(!(review.data.segments[review.segment] || {}).done);
  });
}
