"use strict";
// Запуск страницы: привязка кнопок и загрузка прошлого сеанса.

function bindPickButtons() {
  $$("[data-pick]").forEach((button) => {
    button.addEventListener("click", async () => {
      const target = button.dataset.target ? field(button.dataset.target) : $(`#${button.dataset.targetId}`);
      const path = await pickPath(button.dataset.pick, button.dataset.filter, target.value);
      if (!path) return;
      target.value = path;
      // Поле настроек — сохранить сразу, как при вводе вручную.
      target.dispatchEvent(new Event("change", { bubbles: true }));
    });
  });
}

function bindDrawers() {
  $("#open-settings").onclick = () => openDrawer("settings-drawer");
  $("#open-summary").onclick = openSummary;
  $("#open-help").onclick = () => $("#help-dialog").showModal();
  $("#drawer-shade").onclick = closeDrawers;
  $$("[data-close]").forEach((b) => (b.onclick = closeDrawers));
  $$("[data-close-dialog]").forEach((b) => (b.onclick = () => b.closest("dialog").close()));
  $("#picker-choose-dir").onclick = () => finishPick(picker.path);
  $("#picker-cancel").onclick = () => finishPick(null);
}

function init() {
  bindDrawers();
  bindPickButtons();
  bindSettings();
  bindPlaylist();
  bindPlayer();
  bindEditor();
  bindHistory();
  bindAuto();
  bindSummary();
  bindJournal();
  loadConfig()
    .then(() => api("GET", "/api/video"))
    .then((info) => applyVideo(info, info.frame))
    .then(loadPlaylist)
    .then(pollAuto)
    .then(openFromLink)
    .catch((error) => toast(error.message, "bad"));
}

init();
