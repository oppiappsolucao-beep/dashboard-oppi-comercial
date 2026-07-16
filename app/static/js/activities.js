(function () {
  function enableSaveButtons(root) {
    const scope = root || document;
    scope.querySelectorAll("[data-activity-field]").forEach(function (field) {
      field.addEventListener("change", function () {
        const formId = field.getAttribute("data-activity-form");
        if (!formId) return;
        scope.querySelectorAll('.activity-save-btn[data-activity-form="' + formId + '"]').forEach(function (btn) {
          btn.disabled = false;
        });
      });
      field.addEventListener("input", function () {
        const formId = field.getAttribute("data-activity-form");
        if (!formId) return;
        scope.querySelectorAll('.activity-save-btn[data-activity-form="' + formId + '"]').forEach(function (btn) {
          btn.disabled = false;
        });
      });
    });
  }

  function bindStatusExpand(root) {
    const scope = root || document;
    scope.querySelectorAll("[data-activity-status-select]").forEach(function (select) {
      select.addEventListener("change", function () {
        const formId = select.getAttribute("data-activity-form");
        const extra = scope.querySelector("#activity-extra-" + formId);
        if (!extra) return;
        if (select.value === "concluida" || select.value === "reagendada") {
          extra.classList.add("is-open");
        }
      });
    });

    scope.querySelectorAll('select[name="result"]').forEach(function (select) {
      select.addEventListener("change", function () {
        const formId = select.getAttribute("data-activity-form");
        const extra = scope.querySelector("#activity-extra-" + formId);
        if (!extra) return;
        const other = extra.querySelector(".activity-result-other");
        if (!other) return;
        if (select.value === "Outro") {
          other.classList.remove("is-hidden");
          extra.classList.add("is-open");
        } else {
          other.classList.add("is-hidden");
        }
      });
    });
  }

  window.activityToggleExtra = function (activityId) {
    const extra = document.getElementById("activity-extra-" + activityId);
    if (extra) extra.classList.toggle("is-open");
  };

  function initActivitiesInline() {
    enableSaveButtons(document.getElementById("activities-root") || document);
    bindStatusExpand(document.getElementById("activities-root") || document);
  }

  document.addEventListener("DOMContentLoaded", initActivitiesInline);
  document.body.addEventListener("htmx:afterSwap", function (event) {
    if (event.target && event.target.id === "activities-root") {
      initActivitiesInline();
    }
  });
})();
