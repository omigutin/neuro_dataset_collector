"use strict";
// Шкала времени под кадром: ответы модели по зоне (из журнала), сохранённые кадры, участки на проверку.

const timeline = { data: null, zone: null, timer: null };

// Журнал открытого видео: выбранный во вкладке «Журнал» или найденный по имени видео; иначе — журнал сборщика.
function videoJournal() {
  if (!hasVideo() || typeof journal === "undefined") return null;
  const name = fileName(state.info.video);
  if (journal.data && fileName(journal.data.video) === name) return journal.path;
  const found = journal.list.find((j) => j.video && fileName(j.video) === name);
  return found ? found.path : null;
}

function loadTimeline() {
  clearTimeout(timeline.timer);
  timeline.timer = setTimeout(fetchTimeline, 150);
}

async function fetchTimeline() {
  if (!hasVideo()) return;
  const query = new URLSearchParams();
  const path = videoJournal();
  if (path) query.set("journal", path);
  if (timeline.zone) query.set("zone", timeline.zone);
  try {
    timeline.data = await api("GET", `api/video/timeline?${query}`);
    if (timeline.data.video !== (state.info && state.info.video)) return;
  } catch (error) {
    timeline.data = null;
  }
  renderTimeline();
}

const pct = (frame, total) => `${Math.min(100, Math.max(0, (frame / Math.max(1, total)) * 100)).toFixed(3)}%`;

function renderTimeline() {
  const data = timeline.data;
  if (!data) return;
  const total = data.frames || (state.info && state.info.frames) || 1;
  const model = data.model;
  const select = $("#tl-zone");
  if (model) {
    select.innerHTML = model.zones.map((z) => `<option value="${escapeHtml(z)}">зона ${escapeHtml(z)}</option>`).join("");
    select.value = model.zone || "";
    select.hidden = false;
    $("#tl-model").innerHTML = model.runs.map(([s, e, label]) =>
      `<span style="left:${pct(s, total)};width:${pct(e - s + 1, total)};background:${classColor(label)}" title="${escapeHtml(label || "нет ответа")}: кадры ${s}–${e}"></span>`).join("");
  } else {
    select.hidden = true;
    $("#tl-model").innerHTML = `<span class="empty">журнала нет — «Задачи → Прогнать моделью»</span>`;
  }
  $("#tl-saved").innerHTML = data.saved.length
    ? data.saved.map(([frame, label]) => `<span class="tick" style="left:${pct(frame, total)};background:${classColor(label)}" title="кадр ${frame}: ${escapeHtml(label || "без класса")}"></span>`).join("")
    : `<span class="empty">ничего не сохранено</span>`;
  const segments = reviewSegmentsForVideo();
  $("#tl-review-row").hidden = !segments.length;
  $("#tl-review").innerHTML = segments.map((s) =>
    `<span class="seg" style="left:${pct(s.start_frame, total)};width:${pct(s.end_frame - s.start_frame + 1, total)}" title="${escapeHtml(s.kind_name)}, зона ${escapeHtml(s.zone)}"></span>`).join("");
  const labels = new Set((model ? model.runs.map((r) => r[2]) : []).concat(data.saved.map((s) => s[1])));
  $("#tl-legend").innerHTML = [...labels].map((l) => `<span><span class="swatch" style="background:${classColor(l)}"></span>${escapeHtml(l || "нет ответа / без класса")}</span>`).join("");
  ["#tl-model", "#tl-saved", "#tl-review"].forEach((id) => {
    if (!$(id).querySelector(".tl-cursor")) $(id).insertAdjacentHTML("beforeend", `<i class="tl-cursor"></i>`);
  });
  moveTimelineCursor();
}

// Участки открытого файла участков, если он про это видео.
function reviewSegmentsForVideo() {
  if (typeof review === "undefined" || !review.data || !hasVideo()) return [];
  return fileName(review.data.video) === fileName(state.info.video) ? review.data.segments : [];
}

function moveTimelineCursor() {
  if (!hasVideo()) return;
  const left = pct(state.frame, state.info.frames);
  $$(".tl-cursor").forEach((el) => (el.style.left = left));
}

// Щелчок по дорожке — перейти к этому месту видео.
function onTimelineClick(event) {
  const track = event.target.closest(".tl-track");
  if (!track || !hasVideo()) return;
  const rect = track.getBoundingClientRect();
  pause();
  showFrame(Math.round(((event.clientX - rect.left) / rect.width) * (state.info.frames - 1)));
}

function bindTimeline() {
  $("#timeline").addEventListener("click", onTimelineClick);
  $("#tl-zone").onchange = () => { timeline.zone = $("#tl-zone").value; fetchTimeline(); };
}
