(function () {
  const STAGE_ALIASES = {
    "novo lead": "Novo Lead",
    "chamado whats": "Contato",
    "conversando": "Qualificação",
    "reunião": "Reunião",
    "reuniao": "Reunião",
    "proposta": "Proposta",
    "retornar": "Retorno",
    "retorno": "Retorno",
    "negociação": "Negociação",
    "negociacao": "Negociação",
    "fechado": "Fechado",
  };

  function normalizeText(value) {
    return String(value || "").trim();
  }

  function resolvePipelineStage(statusValue) {
    const normalized = normalizeText(statusValue).toLowerCase();
    if (STAGE_ALIASES[normalized]) return STAGE_ALIASES[normalized];
    for (const key in STAGE_ALIASES) {
      if (normalized.includes(key)) return STAGE_ALIASES[key];
    }
    return "Novo Lead";
  }

  function formatDateBr(isoDate) {
    if (!isoDate) return "";
    const parts = isoDate.split("-");
    if (parts.length !== 3) return isoDate;
    return parts[2] + "/" + parts[1] + "/" + parts[0];
  }

  function initRegistrationNewPage() {
    const form = document.getElementById("registration-new-form");
    if (!form) return;

    const toggle = document.getElementById("create-first-activity-toggle");
    const hiddenFlag = document.getElementById("create-first-activity");
    const activityFields = document.getElementById("registration-activity-fields");
    const vendedorSelect = document.getElementById("registration-vendedor");
    const responsibleSelect = document.getElementById("registration-activity-responsible");
    const statusSelect = document.getElementById("registration-status");
    const empresaInput = form.querySelector('input[name="empresa"]');
    const activityDateInput = document.getElementById("registration-activity-date");
    const activityTimeInput = form.querySelector('input[name="activity_time"]');
    const activityActionSelect = document.getElementById("registration-activity-action");
    const funnel = document.getElementById("registration-funnel");
    const summaryTipo = document.getElementById("summary-tipo");

    function setActivityEnabled(enabled) {
      if (hiddenFlag) hiddenFlag.value = enabled ? "1" : "";
      if (activityFields) {
        activityFields.classList.toggle("is-disabled", !enabled);
        activityFields.querySelectorAll("input, select, textarea").forEach(function (field) {
          if (field.id === "create-first-activity-toggle") return;
          field.disabled = !enabled;
        });
      }
    }

    function updateFunnel(stageName) {
      if (!funnel) return;
      funnel.querySelectorAll(".registration-funnel-step").forEach(function (step) {
        step.classList.toggle("active", step.getAttribute("data-stage") === stageName);
      });
    }

    function updateSummary() {
      const empresa = normalizeText(empresaInput && empresaInput.value);
      const status = normalizeText(statusSelect && statusSelect.value) || "Novo Lead";
      const stage = resolvePipelineStage(status);
      const createActivity = toggle && toggle.checked;
      const dateValue = activityDateInput && activityDateInput.value;
      const timeValue = activityTimeInput && activityTimeInput.value;
      const action = activityActionSelect && activityActionSelect.value;

      const summaryCompany = document.getElementById("summary-company");
      const summaryStage = document.getElementById("summary-stage");
      const summaryActivity = document.getElementById("summary-activity");
      const summaryDeadline = document.getElementById("summary-deadline");

      if (summaryCompany) {
        summaryCompany.textContent = empresa
          ? 'Empresa "' + empresa + '" será cadastrada'
          : "Nova empresa ou lead será cadastrado";
      }
      if (summaryTipo) {
        const checked = form.querySelector('input[name="cadastro_tipo"]:checked');
        summaryTipo.textContent = checked && checked.value === "empresa" ? "Empresa" : "Novo Lead";
      }
      if (summaryStage) {
        summaryStage.textContent = stage + " (entrada no funil)";
      }
      if (summaryActivity) {
        summaryActivity.textContent = createActivity
          ? (action || "Primeira atividade") + " será agendada"
          : "Nenhuma atividade será criada agora";
      }
      if (summaryDeadline) {
        summaryDeadline.textContent = createActivity && dateValue
          ? formatDateBr(dateValue) + (timeValue ? " às " + timeValue : "")
          : "Definido na data e hora da primeira atividade";
      }

      updateFunnel(stage);
    }

    if (toggle) {
      toggle.addEventListener("change", function () {
        setActivityEnabled(toggle.checked);
        updateSummary();
      });
      setActivityEnabled(toggle.checked);
    }

    if (vendedorSelect && responsibleSelect) {
      vendedorSelect.addEventListener("change", function () {
        if (!normalizeText(responsibleSelect.value)) {
          responsibleSelect.value = vendedorSelect.value;
        }
        updateSummary();
      });
      if (vendedorSelect.value) {
        responsibleSelect.value = vendedorSelect.value;
      }
    }

    [empresaInput, statusSelect, activityDateInput, activityTimeInput, activityActionSelect].forEach(function (field) {
      if (!field) return;
      field.addEventListener("input", updateSummary);
      field.addEventListener("change", updateSummary);
    });

    document.addEventListener("registration-tipo-changed", updateSummary);

    updateSummary();
  }

  document.addEventListener("DOMContentLoaded", initRegistrationNewPage);
})();
