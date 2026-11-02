"use strict";
// Задачи: автонарезка и прогон моделью в фоне — запуск, ход, показ обрабатываемого кадра.

const autoLoop = { timer: null, ticks: 0 };

const MODE_TITLES = { collect: "Автонарезка", model: "Прогон моделью" };
const MODE_HINTS = {
  collect: "Сохраняет кадры по правилам из настроек (раздел «Автонарезка»): без модели — раз в заданное время без дублей, с моделью — где модель не уверена.",
  model: "Модель отвечает по каждой зоне проверяемых кадров, ответы пишутся в журнал (режим «Журнал»). По настройке вырезы, где модель не прошла условия, сохраняются на проверку.",
};

function updateModeHint() {
  const mode = $("#auto-mode").value;
  let hint = MODE_HINTS[mode];
  if (mode === "model" && !modelSet()) hint += " Модель не выбрана — укажите её в настройках.";
  $("#auto-mode-hint").textContent = hint;
}

async function autoStart() {
  const scope = $("#auto-scope").value;
  const mode = $("#auto-mode").value;
  if (mode === "model" && !modelSet()) { toast("Модель не выбрана: укажите файл модели в настройках", "bad"); openModelSettings(); return; }
  const what = mode === "model" ? "прогон моделью" : "автонарезку";
  if (scope === "playlist" && !confirm(`Запустить ${what} всех видео плейлиста (${state.playlist.items.length})?`)) return;
  if (scope === "video" && !hasVideo()) { toast("Сначала откройте видео", "bad"); return; }
  try {
    pause();
    await api("POST", "api/auto/start", { scope, mode });
    setView("cut");
    pollAuto();
  } catch (error) {
    toast(error.message, "bad");
  }
}

async function autoStop() {
  try {
    await api("POST", "api/auto/stop");
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
  try { s = await api("GET", "api/auto/status"); } catch (error) { autoLoop.timer = setTimeout(pollAuto, 2000); return; }
  const wasRunning = autoRunning();
  state.auto = s;
  const where = s.total > 1 ? `видео ${s.index} из ${s.total}: ${s.video}` : s.video;
  const done = s.mode === "model" ? `не прошли условия ${s.matched} · сохранено ${s.saved}` : `сохранено ${s.saved}`;
  const percent = Math.round((s.running ? autoProgress(s) : 0) * 100);
  $("#task-running").hidden = !s.running;
  $("#task-title").textContent = `${MODE_TITLES[s.mode] || ""} · ${percent}%`;
  $("#auto-status").textContent = s.running ? `${where} · кадр ${s.frame} · ${done}` : "";
  $("#auto-progress").style.width = `${percent}%`;
  $("#tasks-progress").hidden = !s.running;
  $("#tasks-progress span").style.width = `${percent}%`;
  const last = s.error ? `Ошибка: ${s.error}` : s.summary ? `Последний запуск (${(MODE_TITLES[s.mode] || "").toLowerCase()}): ${s.summary}` : "";
  $("#task-last").textContent = s.running ? "" : last;
  $("#task-last").classList.toggle("bad", !!s.error && !s.running);
  $("#auto-overlay").textContent = s.running ? `${MODE_TITLES[s.mode] || ""} · ${where} · кадр ${s.frame} · ${done}` : "";
  $("#auto-start").disabled = s.running;
  $("#auto-scope").disabled = s.running;
  $("#auto-mode").disabled = s.running;
  if (s.running) $("#auto-mode").value = s.mode;
  showAutoView(s.running);
  if (s.running) {
    autoLoop.ticks += 1;
    if (!wasRunning || autoLoop.ticks % 2 === 0) loadGroup(s.video_path);
    if (!wasRunning || autoLoop.ticks % 4 === 0) loadPlaylist();
    autoLoop.timer = setTimeout(pollAuto, 700);
    return;
  }
  if (wasRunning) {
    toast(s.error ? `Ошибка: ${s.error}` : `${MODE_TITLES[s.mode]} закончена: ${s.summary}`, s.error ? "bad" : "ok");
    if (s.mode === "model") journalsChanged(s.journal);
    await refreshVideo();
    await loadPlaylist();
    historyStore.groups.forEach((_, video) => loadGroup(video));
  }
}

// Пока идёт задача, вместо обычного кадра показывается обрабатываемый.
function showAutoView(on) {
  const img = $("#auto-frame");
  const wasOn = !img.hidden;
  img.hidden = !on;
  $("#auto-overlay").hidden = !on;
  $("#frame").hidden = on;
  if (on) { $("#frame-placeholder").hidden = true; $(".view-cut .frame-wrap").hidden = false; }
  $(".view-cut .stage").classList.toggle("auto-running", on);
  $("#edit-markup").disabled = on || !hasVideo();
  if (on && !wasOn) nextAutoFrame();
}

function nextAutoFrame() {
  if (!autoRunning()) return;
  $("#auto-frame").src = `api/auto/frame?t=${Date.now()}`;
}

function bindAuto() {
  $("#tasks-btn").onclick = (event) => { event.stopPropagation(); updateModeHint(); toggleMenu("#tasks-panel"); };
  $("#auto-mode").onchange = updateModeHint;
  $("#auto-start").onclick = () => { closeMenus(); autoStart(); };
  $("#auto-stop").onclick = autoStop;
  $("#auto-frame").addEventListener("load", () => setTimeout(nextAutoFrame, 250));
  $("#auto-frame").addEventListener("error", () => setTimeout(nextAutoFrame, 500));
}
