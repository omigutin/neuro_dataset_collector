"use strict";
// Автонарезка: запуск из верхней строки, ход и показ обрабатываемого кадра.

const autoLoop = { timer: null, ticks: 0 };

const MODE_TITLES = { collect: "Автонарезка", model: "Прогон моделью" };

async function autoStart() {
  const scope = $("#auto-scope").value;
  const mode = $("#auto-mode").value;
  if (mode === "model" && !modelSet()) { toast("Модель не задана: ⚙ → Автонарезка → Модель", "bad"); return; }
  const what = mode === "model" ? "прогон моделью" : "автонарезку";
  if (scope === "playlist" && !confirm(`Запустить ${what} всех видео плейлиста (${state.playlist.items.length})?`)) return;
  if (scope === "video" && !(state.info && state.info.video)) { toast("Сначала откройте видео", "bad"); return; }
  if (state.edit) { toast("Сначала сохраните или отмените правку зон", "bad"); return; }
  try {
    pause();
    await api("POST", "/api/auto/start", { scope, mode });
    pollAuto();
  } catch (error) {
    toast(error.message, "bad");
  }
}

async function autoStop() {
  try {
    await api("POST", "/api/auto/stop");
  } catch (error) {
    toast(error.message, "bad");
  }
  pollAuto();
}

// Доля выполненного: готовые видео плюс часть текущего.
function autoProgress(s) {
  if (!s.total) return 0;
  const inVideo = s.video_frames ? Math.min(1, s.frame / s.video_frames) : 0;
  return Math.min(1, (Math.max(0, s.index - 1) + inVideo) / s.total);
}

async function pollAuto() {
  clearTimeout(autoLoop.timer);
  let s;
  try { s = await api("GET", "/api/auto/status"); } catch (error) { autoLoop.timer = setTimeout(pollAuto, 2000); return; }
  const wasRunning = autoRunning();
  state.auto = s;
  const where = s.total > 1 ? `видео ${s.index} из ${s.total}: ${s.video}` : s.video;
  const done = s.mode === "model" ? `не прошли условия ${s.matched} · сохранено ${s.saved}` : `сохранено ${s.saved}`;
  const status = s.running ? `${where} · кадр ${s.frame} · ${done}` : s.error ? `Ошибка: ${s.error}` : s.summary;
  $("#auto-status").textContent = status || "";
  $("#auto-status").title = status || "";
  $("#auto-overlay").textContent = s.running ? `${MODE_TITLES[s.mode] || ""} · ${where} · кадр ${s.frame} · ${done}` : "";
  if (s.running) $("#auto-mode").value = s.mode;
  $("#auto-progress").style.width = `${Math.round((s.running ? autoProgress(s) : 0) * 100)}%`;
  $("#auto-start").disabled = s.running;
  $("#auto-scope").disabled = s.running;
  $("#auto-mode").disabled = s.running;
  $("#auto-stop").disabled = !s.running;
  showAutoView(s.running);
  if (s.running) {
    autoLoop.ticks += 1;
    if (!wasRunning || autoLoop.ticks % 2 === 0) loadGroup(s.video_path);
    if (!wasRunning || autoLoop.ticks % 4 === 0) loadPlaylist();
    autoLoop.timer = setTimeout(pollAuto, 700);
    return;
  }
  if (wasRunning) {
    if (s.mode === "model" && typeof journalsChanged === "function") journalsChanged(s.journal);
    await refreshVideo();
    await loadPlaylist();
    historyStore.groups.forEach((_, video) => loadGroup(video));
  }
}

// Пока идёт автонарезка, вместо обычного кадра показывается обрабатываемый.
function showAutoView(on) {
  const img = $("#auto-frame");
  const wasOn = !img.hidden;
  img.hidden = !on;
  $("#auto-overlay").hidden = !on;
  const hasVideo = !!(state.info && state.info.video);
  $("#frame").style.display = on || !hasVideo ? "none" : "";
  $("#frame-placeholder").style.display = on || hasVideo ? "none" : "";
  $("#frame-tools").hidden = on || !hasVideo;
  $(".viewer").classList.toggle("auto-running", on);
  if (on && !wasOn) nextAutoFrame();
}

function nextAutoFrame() {
  if (!autoRunning()) return;
  $("#auto-frame").src = `/api/auto/frame?t=${Date.now()}`;
}

function bindAuto() {
  $("#auto-start").onclick = autoStart;
  $("#auto-stop").onclick = autoStop;
  $("#auto-frame").addEventListener("load", () => setTimeout(nextAutoFrame, 250));
  $("#auto-frame").addEventListener("error", () => setTimeout(nextAutoFrame, 500));
}
