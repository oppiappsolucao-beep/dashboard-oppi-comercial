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
        if (select.value === "Sem interesse" || select.value === "Contato inválido") {
          extra.classList.add("is-open");
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
    initKanbanDragDrop(document.getElementById("activities-root") || document);
  }

  function initKanbanDragDrop(root) {
    if (!root) return;

    let draggedCard = null;
    let dragMoved = false;
    let suppressClickUntil = 0;

    root.querySelectorAll(".activities-kanban-card[draggable='true']").forEach(function (card) {
      if (card.dataset.kanbanBound === "1") return;
      card.dataset.kanbanBound = "1";

      card.addEventListener("dragstart", function (event) {
        draggedCard = card;
        dragMoved = true;
        card.classList.add("is-dragging");
        card.setAttribute("aria-grabbed", "true");
        if (event.dataTransfer) {
          event.dataTransfer.effectAllowed = "move";
          event.dataTransfer.setData("text/plain", card.getAttribute("data-activity-id") || "");
        }
      });

      card.addEventListener("dragend", function () {
        card.classList.remove("is-dragging");
        card.setAttribute("aria-grabbed", "false");
        root.querySelectorAll(".activities-kanban-column-body.is-drop-target").forEach(function (column) {
          column.classList.remove("is-drop-target");
        });
        if (dragMoved) {
          suppressClickUntil = Date.now() + 250;
        }
        dragMoved = false;
        draggedCard = null;
      });

      card.addEventListener("click", function (event) {
        if (Date.now() < suppressClickUntil) {
          event.preventDefault();
          return;
        }
        activitySelectCard(card);
        if (typeof htmx === "undefined") return;
        htmx.ajax("GET", "/atividades/" + encodeURIComponent(card.getAttribute("data-activity-id")) + "/painel", {
          target: "#activity-drawer-root",
          swap: "innerHTML",
        });
      });

      card.addEventListener("keydown", function (event) {
        if (event.key !== "Enter" && event.key !== " ") return;
        event.preventDefault();
        card.click();
      });
    });

    root.querySelectorAll(".activities-kanban-column-body[data-drop-stage]").forEach(function (column) {
      if (column.dataset.dropBound === "1") return;
      column.dataset.dropBound = "1";

      column.addEventListener("dragover", function (event) {
        event.preventDefault();
        column.classList.add("is-drop-target");
        if (event.dataTransfer) {
          event.dataTransfer.dropEffect = "move";
        }
      });

      column.addEventListener("dragleave", function (event) {
        if (event.currentTarget.contains(event.relatedTarget)) return;
        column.classList.remove("is-drop-target");
      });

      column.addEventListener("drop", function (event) {
        event.preventDefault();
        column.classList.remove("is-drop-target");

        const activityId = (event.dataTransfer && event.dataTransfer.getData("text/plain"))
          || (draggedCard && draggedCard.getAttribute("data-activity-id"))
          || "";
        const newStage = column.getAttribute("data-drop-stage") || "";
        const currentStage = draggedCard && draggedCard.getAttribute("data-current-stage");

        if (!activityId || !newStage || currentStage === newStage) return;

        dragMoved = true;
        suppressClickUntil = Date.now() + 250;

        const filtersForm = document.getElementById("activities-filters");
        const formData = filtersForm ? new FormData(filtersForm) : new FormData();
        formData.set("stage_target", newStage);

        fetch("/atividades/" + encodeURIComponent(activityId) + "/mover-etapa", {
          method: "POST",
          body: formData,
          headers: { "HX-Request": "true" },
        }).then(function (response) {
          return response.text();
        }).then(function (html) {
          const boardRoot = document.getElementById("activities-root");
          if (!boardRoot) return;
          boardRoot.innerHTML = html;
          if (typeof htmx !== "undefined") {
            htmx.process(boardRoot);
          }
          initActivitiesInline();
        }).catch(function () {
          window.alert("Não foi possível mover a atividade. Tente novamente.");
        });
      });
    });
  }

  function addDays(baseDate, days, businessOnly) {
    const date = new Date(baseDate.getTime());
    date.setDate(date.getDate() + days);
    if (businessOnly) {
      while (date.getDay() === 0 || date.getDay() === 6) {
        date.setDate(date.getDate() + 1);
      }
    }
    return date.toISOString().slice(0, 10);
  }

  function fillSelect(select, items, placeholder) {
    if (!select) return;
    const current = select.value;
    select.innerHTML = "";
    const empty = document.createElement("option");
    empty.value = "";
    empty.textContent = placeholder || "Selecione";
    select.appendChild(empty);
    items.forEach(function (item) {
      const option = document.createElement("option");
      option.value = item;
      option.textContent = item;
      select.appendChild(option);
    });
    if (current && items.indexOf(current) >= 0) {
      select.value = current;
    }
  }

  async function fetchStageActions(stage) {
    const response = await fetch("/atividades/api/acoes?stage=" + encodeURIComponent(stage || "Novo Lead"));
    const data = await response.json();
    return data.items || [];
  }

  async function fetchResultSuggestion(result, stage) {
    const response = await fetch(
      "/atividades/api/sugerir-resultado?result=" + encodeURIComponent(result || "") +
      "&stage=" + encodeURIComponent(stage || "")
    );
    return response.json();
  }

  function toggleHidden(el, show) {
    if (!el) return;
    el.classList.toggle("is-hidden", !show);
  }

  function initNewActivityModal() {
    const modalRoot = document.getElementById("activity-modal-root");
    if (!modalRoot || !modalRoot.querySelector("#activity-new-form")) return;

    const form = document.getElementById("activity-new-form");
    const searchInput = document.getElementById("new-activity-lead-search");
    const resultsBox = document.getElementById("new-activity-lead-results");
    const selectedBox = document.getElementById("new-activity-lead-selected");
    const stageSelect = document.getElementById("new-activity-stage");
    const typeSelect = document.getElementById("new-activity-type");
    const actionSelect = document.getElementById("new-activity-action");
    const nextActionSelect = document.getElementById("new-activity-next-action");
    const channelSelect = document.getElementById("new-activity-channel");
    const channelOther = document.getElementById("new-activity-channel-other");
    const statusSelect = document.getElementById("new-activity-status");
    const resultSelect = document.getElementById("new-activity-result");
    const complement = document.getElementById("new-activity-complement");
    const moveStage = document.getElementById("new-activity-move-stage");
    const moveHint = document.getElementById("new-activity-move-hint");
    const moveConfirmWrap = document.getElementById("new-activity-move-confirm-wrap");
    const moveCheckbox = document.getElementById("new-activity-move-checkbox");
    const moveConfirmHidden = document.getElementById("new-activity-move-confirm");
    const description = document.getElementById("new-activity-description");
    const descriptionCount = document.getElementById("new-activity-description-count");
    const lostReasonWrap = document.getElementById("new-activity-lost-reason-wrap");
    const closeValueWrap = document.getElementById("new-activity-close-value-wrap");
    const closePaymentWrap = document.getElementById("new-activity-close-payment-wrap");

    let searchTimer = null;

    async function refreshActions() {
      const stage = stageSelect ? stageSelect.value : "Novo Lead";
      const actions = await fetchStageActions(stage);
      fillSelect(actionSelect, actions, "Selecione a atividade");
    }

    fillSelect(
      nextActionSelect,
      window.ACTIVITY_MODAL_NEXT_ACTIONS || [],
      "Selecione a próxima ação"
    );

    if (stageSelect) {
      stageSelect.addEventListener("change", refreshActions);
      refreshActions();
    }

    if (typeSelect) {
      typeSelect.addEventListener("change", function () {
        const type = typeSelect.value;
        const defaults = window.ACTIVITY_MODAL_DEFAULTS || {};
        const hints = window.ACTIVITY_MODAL_STAGE_HINTS || {};
        if (hints[type] && stageSelect) {
          stageSelect.value = hints[type];
          refreshActions();
        }
        if (defaults[type] && actionSelect) {
          actionSelect.value = defaults[type];
        }
      });
    }

    if (channelSelect) {
      channelSelect.addEventListener("change", function () {
        toggleHidden(channelOther, channelSelect.value === "Outro");
      });
    }

    if (statusSelect && complement) {
      statusSelect.addEventListener("change", function () {
        if (statusSelect.value === "concluida") {
          complement.open = true;
        }
      });
    }

    if (description && descriptionCount) {
      description.addEventListener("input", function () {
        descriptionCount.textContent = String(description.value.length);
      });
    }

    document.querySelectorAll("#new-activity-quick-dates button").forEach(function (button) {
      button.addEventListener("click", function () {
        const days = parseInt(button.getAttribute("data-days") || "0", 10);
        const business = button.hasAttribute("data-business");
        const dateInput = document.getElementById("new-activity-date");
        if (dateInput) {
          dateInput.value = addDays(new Date(), days, business);
        }
      });
    });

    if (searchInput && resultsBox) {
      searchInput.addEventListener("input", function () {
        clearTimeout(searchTimer);
        const query = searchInput.value.trim();
        if (query.length < 2) {
          resultsBox.innerHTML = "";
          resultsBox.classList.remove("is-open");
          return;
        }
        searchTimer = setTimeout(async function () {
          const response = await fetch("/atividades/api/leads?q=" + encodeURIComponent(query));
          const data = await response.json();
          resultsBox.innerHTML = "";
          (data.items || []).forEach(function (item) {
            const button = document.createElement("button");
            button.type = "button";
            button.className = "activity-lead-result-item";
            button.innerHTML =
              "<strong>" + item.empresa + "</strong>" +
              "<span>" + item.contato + " · " + item.phone + "</span>" +
              "<span class='muted'>" + item.stage + " · " + item.vendedor + "</span>";
            button.addEventListener("click", function () {
              document.getElementById("new-activity-sheet-row").value = item.sheet_row;
              document.getElementById("new-activity-empresa").value = item.empresa;
              document.getElementById("new-activity-contato").value = item.contato;
              if (stageSelect) stageSelect.value = item.stage;
              if (document.getElementById("new-activity-responsible")) {
                document.getElementById("new-activity-responsible").value = item.vendedor;
              }
              selectedBox.textContent = item.empresa + " · " + item.contato + " · " + item.stage;
              searchInput.value = item.empresa;
              resultsBox.classList.remove("is-open");
              refreshActions().then(function () {
                if (actionSelect && item.suggested_action) {
                  actionSelect.value = item.suggested_action;
                }
              });
            });
            resultsBox.appendChild(button);
          });
          resultsBox.classList.toggle("is-open", (data.items || []).length > 0);
        }, 250);
      });
    }

    if (resultSelect) {
      resultSelect.addEventListener("change", async function () {
        const suggestion = await fetchResultSuggestion(resultSelect.value, stageSelect ? stageSelect.value : "");
        if (suggestion.next_action && nextActionSelect) {
          nextActionSelect.value = suggestion.next_action;
        }
        if (suggestion.next_action_date && document.getElementById("new-activity-next-date")) {
          document.getElementById("new-activity-next-date").value = suggestion.next_action_date;
        }
        if (suggestion.channel && document.getElementById("new-activity-next-channel")) {
          document.getElementById("new-activity-next-channel").value = suggestion.channel;
        }
        if (suggestion.to_stage && moveStage) {
          moveStage.value = suggestion.to_stage;
        }
        if (moveHint) {
          moveHint.textContent = suggestion.move_text || "";
        }
        toggleHidden(moveConfirmWrap, Boolean(suggestion.move_text));
        toggleHidden(lostReasonWrap, resultSelect.value === "Sem interesse");
        if (complement) complement.open = true;
      });
    }

    if (moveCheckbox && moveConfirmHidden) {
      moveCheckbox.addEventListener("change", function () {
        moveConfirmHidden.value = moveCheckbox.checked ? "1" : "";
      });
    }

    if (form) {
      form.addEventListener("submit", function () {
        if (moveConfirmHidden && moveCheckbox) {
          moveConfirmHidden.value = moveCheckbox.checked ? "1" : "";
        }
      });
    }
  }

  window.activityCloseModal = function (event) {
    if (event && event.target && event.target.id !== "activity-modal-backdrop") return;
    const root = document.getElementById("activity-modal-root");
    if (root) root.innerHTML = "";
    document.body.classList.remove("activity-modal-open");
  };

  window.activityCloseDrawer = function (event) {
    if (event && event.target && !event.target.classList.contains("activity-drawer-backdrop")) return;
    const root = document.getElementById("activity-drawer-root");
    if (root) root.innerHTML = "";
    document.body.classList.remove("activity-drawer-open");
    document.querySelectorAll(".activities-kanban-card.is-selected").forEach(function (card) {
      card.classList.remove("is-selected");
    });
  };

  window.activitySelectCard = function (button) {
    document.querySelectorAll(".activities-kanban-card.is-selected").forEach(function (card) {
      card.classList.remove("is-selected");
    });
    if (button) button.classList.add("is-selected");
  };

  window.activityToggleCompleteForm = function () {
    const form = document.getElementById("activity-complete-form");
    const reschedule = document.getElementById("activity-reschedule-form");
    if (!form) return;
    if (reschedule) reschedule.classList.add("is-hidden");
    form.classList.toggle("is-hidden");
  };

  window.activityToggleRescheduleForm = function () {
    const form = document.getElementById("activity-reschedule-form");
    const complete = document.getElementById("activity-complete-form");
    if (!form) return;
    if (complete) complete.classList.add("is-hidden");
    form.classList.toggle("is-hidden");
  };

  function initActivityDrawer() {
    const drawerRoot = document.getElementById("activity-drawer-root");
    if (drawerRoot && drawerRoot.querySelector(".activity-drawer")) {
      document.body.classList.add("activity-drawer-open");
    }
  }

  window.activityHandleCreateResponse = function (event) {
    if (event.detail.successful) {
      window.activityCloseModal();
      initActivitiesInline();
      return;
    }
    initNewActivityModal();
  };

  document.addEventListener("DOMContentLoaded", function () {
    initActivitiesInline();
    initActivityDrawer();
  });

  document.body.addEventListener("htmx:afterSwap", function (event) {
    if (event.target && event.target.id === "activities-root") {
      initActivitiesInline();
      activityCloseDrawer();
    }
    if (event.target && event.target.id === "activity-modal-root") {
      document.body.classList.add("activity-modal-open");
      initNewActivityModal();
    }
    if (event.target && event.target.id === "activity-drawer-root") {
      initActivityDrawer();
    }
  });

  document.body.addEventListener("activityModalClose", function () {
    window.activityCloseModal();
  });
})();
