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

    function moneyAmount(value) {
      var text = normalizeText(value).replace(/R\$/g, "").replace(/\s/g, "");
      if (!text) return 0;
      if (text.indexOf(",") >= 0) text = text.replace(/\./g, "").replace(",", ".");
      var amount = Number(text);
      return isNaN(amount) ? 0 : amount;
    }

    function contractedServiceProblem() {
      var rows = form.querySelectorAll("#closed-services-track .contracted-service-row");
      var complete = 0;
      var problem = null;
      rows.forEach(function (row) {
        var service = row.querySelector('[name="closed_servico"]');
        var quantity = row.querySelector('[name="closed_quantidade"]');
        var amount = row.querySelector('[name="closed_valor"]');
        var payment = row.querySelector('[name="closed_forma_pagamento"]');
        var serviceValue = normalizeText(service && service.value);
        var amountValue = normalizeText(amount && amount.value);
        if (!serviceValue && moneyAmount(amountValue) <= 0) return;
        if (!serviceValue) {
          problem = problem || { field: service, message: "Selecione o serviço contratado na aba Financeiro." };
          return;
        }
        var qty = Number(normalizeText(quantity && quantity.value).replace(",", "."));
        if (!qty || qty < 1) {
          problem = problem || { field: quantity, message: "Informe a quantidade do serviço contratado." };
          return;
        }
        if (moneyAmount(amountValue) <= 0) {
          problem = problem || { field: amount, message: "Informe o valor do serviço contratado." };
          return;
        }
        if (!normalizeText(payment && payment.value)) {
          problem = problem || { field: payment, message: "Selecione a forma de pagamento do serviço contratado." };
          return;
        }
        complete += 1;
      });
      if (problem) return problem;
      if (!complete) {
        var firstService = form.querySelector('#closed-services-track [name="closed_servico"]');
        return {
          field: firstService,
          message: "Preencha os serviços contratados na aba Financeiro antes de salvar o cadastro.",
        };
      }
      return null;
    }

    function showRegistrationPanel(name) {
      var tabs = document.querySelectorAll("[data-registration-tab]");
      var panels = document.querySelectorAll("[data-registration-panel]");
      tabs.forEach(function (item) {
        item.classList.toggle("is-active", item.getAttribute("data-registration-tab") === name);
      });
      panels.forEach(function (panel) {
        panel.classList.toggle("is-hidden", panel.getAttribute("data-registration-panel") !== name);
      });
    }

    form.addEventListener("submit", function (event) {
      var submitter = event.submitter;
      var bypass = submitter && (submitter.hasAttribute("formnovalidate") || submitter.getAttribute("formaction"));
      if (!bypass) {
        var problem = contractedServiceProblem();
        if (problem) {
          event.preventDefault();
          showRegistrationPanel("financeiro");
          var note = document.getElementById("contracted-services-required-note");
          if (note) {
            note.hidden = false;
            note.textContent = problem.message;
          }
          if (problem.field) {
            problem.field.focus();
            if (problem.field.setCustomValidity) {
              problem.field.setCustomValidity(problem.message);
              problem.field.reportValidity();
              problem.field.setCustomValidity("");
            }
          }
          return;
        }
      }
      if (form.dataset.saving === "1") {
        event.preventDefault();
        return;
      }
      form.dataset.saving = "1";
      form.querySelectorAll('button[type="submit"]').forEach(function (button) {
        button.disabled = true;
      });
    });
  }

  document.addEventListener("DOMContentLoaded", initRegistrationNewPage);
})();
