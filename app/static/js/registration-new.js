(function () {
  function normalizeText(value) {
    return String(value || "").trim();
  }

  function initRegistrationTabs() {
    var tabs = document.querySelectorAll("[data-registration-tab]");
    var panels = document.querySelectorAll("[data-registration-panel]");
    if (!tabs.length || !panels.length) return;

    tabs.forEach(function (tab) {
      tab.addEventListener("click", function () {
        var target = tab.getAttribute("data-registration-tab");
        tabs.forEach(function (item) {
          item.classList.toggle("is-active", item === tab);
        });
        panels.forEach(function (panel) {
          panel.classList.toggle("is-hidden", panel.getAttribute("data-registration-panel") !== target);
        });
      });
    });
  }

  function initRegistrationNewPage() {
    var form = document.getElementById("registration-new-form");
    if (!form) return;

    initRegistrationTabs();

    var toggle = document.getElementById("create-first-activity-toggle");
    var hiddenFlag = document.getElementById("create-first-activity");
    var activityFields = document.getElementById("registration-activity-fields");
    var vendedorSelect = form.querySelector('select[name="vendedor"]');
    var responsibleSelect = document.getElementById("registration-activity-responsible");
    var empresaInput = form.querySelector('input[name="empresa"]');
    var titleNode = document.getElementById("registration-client-title");

    function setActivityEnabled(enabled) {
      if (hiddenFlag) hiddenFlag.value = enabled ? "1" : "";
      if (activityFields) {
        activityFields.classList.toggle("is-disabled", !enabled);
        activityFields.querySelectorAll("input, select, textarea").forEach(function (field) {
          field.disabled = !enabled;
        });
      }
    }

    function updateTitle() {
      if (!titleNode || !empresaInput) return;
      var empresa = normalizeText(empresaInput.value);
      titleNode.textContent = empresa || "Novo cadastro";
    }

    if (toggle) {
      toggle.addEventListener("change", function () {
        setActivityEnabled(toggle.checked);
      });
      setActivityEnabled(toggle.checked);
    }

    if (vendedorSelect && responsibleSelect) {
      vendedorSelect.addEventListener("change", function () {
        if (!normalizeText(responsibleSelect.value)) {
          responsibleSelect.value = vendedorSelect.value;
        }
      });
      if (vendedorSelect.value && !normalizeText(responsibleSelect.value)) {
        responsibleSelect.value = vendedorSelect.value;
      }
    }

    if (empresaInput) {
      empresaInput.addEventListener("input", updateTitle);
      updateTitle();
    }
  }

  document.addEventListener("DOMContentLoaded", initRegistrationNewPage);
  document.addEventListener("DOMContentLoaded", function () {
    var select = document.getElementById("nicho-select");
    var wrap = document.getElementById("nicho-outro-wrap");
    var input = document.getElementById("nicho-outro");
    if (!select || !wrap) return;
    function sync() {
      var isOutros = String(select.value || "").trim().toLowerCase() === "outros";
      wrap.style.display = isOutros ? "" : "none";
      if (input) input.required = isOutros;
      if (!isOutros && input) input.value = "";
    }
    select.addEventListener("change", sync);
    sync();
  });
})();
