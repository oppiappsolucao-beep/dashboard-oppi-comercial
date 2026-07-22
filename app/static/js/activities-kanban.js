(function () {
  var KANBAN_BOUND = "kanbanV2Bound";

  function getRoot() {
    return document.getElementById("activities-root");
  }

  function clearDropTargets(root) {
    root.querySelectorAll(".activities-kanban-column-body.is-drop-target").forEach(function (el) {
      el.classList.remove("is-drop-target");
    });
  }

  function refreshBoard(html) {
    var boardRoot = getRoot();
    if (!boardRoot) return;
    boardRoot.innerHTML = html;
    if (typeof htmx !== "undefined") {
      htmx.process(boardRoot);
    }
    ensureCardsDraggable(boardRoot);
    if (typeof window.initActivitiesInline === "function") {
      window.initActivitiesInline();
    }
  }

  function updateColumnCounts(root) {
    root.querySelectorAll(".activities-kanban-column").forEach(function (column) {
      var body = column.querySelector(".activities-kanban-column-body");
      var countEl = column.querySelector("[data-kanban-count]");
      if (!body || !countEl) return;
      var count = body.querySelectorAll(".activities-kanban-card").length;
      countEl.textContent = String(count);
      var empty = body.querySelector(".activities-kanban-empty");
      if (count === 0 && !empty) {
        var note = document.createElement("div");
        note.className = "activities-kanban-empty";
        note.textContent = "Nenhuma atividade";
        body.appendChild(note);
      } else if (count > 0 && empty) {
        empty.remove();
      }
    });
  }

  function moveCardOptimistically(card, column, newStage) {
    if (!card || !column) return;
    var empty = column.querySelector(".activities-kanban-empty");
    if (empty) empty.remove();
    column.appendChild(card);
    card.setAttribute("data-current-stage", newStage);
    card.classList.remove("is-dragging");
    updateColumnCounts(getRoot() || document);
  }

  function moveActivity(activityId, newStage) {
    var filtersForm = document.getElementById("activities-filters");
    var formData = filtersForm ? new FormData(filtersForm) : new FormData();
    formData.set("stage_target", newStage);
    return fetch("/atividades/" + encodeURIComponent(activityId) + "/mover-etapa", {
      method: "POST",
      body: formData,
      headers: { "HX-Request": "true" },
    }).then(function (response) {
      if (!response.ok) {
        throw new Error("move_failed");
      }
      return response.text();
    });
  }

  function openActivityPanel(card) {
    if (typeof window.activitySelectCard === "function") {
      window.activitySelectCard(card);
    }
    if (typeof htmx === "undefined") return;
    htmx.ajax("GET", "/atividades/" + encodeURIComponent(card.getAttribute("data-activity-id")) + "/painel", {
      target: "#activity-drawer-root",
      swap: "innerHTML",
    });
  }

  function ensureCardsDraggable(root) {
    root.querySelectorAll(".activities-kanban-card").forEach(function (card) {
      card.setAttribute("draggable", "true");
    });
  }

  function bindKanbanBoard(root) {
    if (!root || root.dataset[KANBAN_BOUND] === "1") {
      ensureCardsDraggable(root || getRoot() || document);
      return;
    }
    root.dataset[KANBAN_BOUND] = "1";

    var suppressClickUntil = 0;
    var draggedActivityId = "";
    var draggedStage = "";
    var draggedCard = null;

    ensureCardsDraggable(root);

    root.addEventListener("dragstart", function (event) {
      var card = event.target.closest(".activities-kanban-card");
      if (!card || !root.contains(card)) return;

      draggedActivityId = card.getAttribute("data-activity-id") || "";
      draggedStage = card.getAttribute("data-current-stage") || "";
      draggedCard = card;
      if (!draggedActivityId) {
        event.preventDefault();
        return;
      }

      card.classList.add("is-dragging");
      document.body.classList.add("activity-kanban-dragging");
      event.dataTransfer.effectAllowed = "move";
      event.dataTransfer.setData("text/plain", draggedActivityId);
      event.dataTransfer.setData("application/x-activity-stage", draggedStage);
    });

    root.addEventListener("dragend", function (event) {
      var card = event.target.closest(".activities-kanban-card");
      if (card) card.classList.remove("is-dragging");
      document.body.classList.remove("activity-kanban-dragging");
      clearDropTargets(root);
      suppressClickUntil = Date.now() + 250;
      draggedActivityId = "";
      draggedStage = "";
      draggedCard = null;
    });

    root.addEventListener("dragover", function (event) {
      var column = event.target.closest(".activities-kanban-column-body[data-drop-stage]");
      if (!column || !root.contains(column)) return;
      event.preventDefault();
      event.dataTransfer.dropEffect = "move";
      clearDropTargets(root);
      column.classList.add("is-drop-target");
    });

    root.addEventListener("drop", function (event) {
      event.preventDefault();
      clearDropTargets(root);

      var column = event.target.closest(".activities-kanban-column-body[data-drop-stage]");
      if (!column || !root.contains(column)) return;

      var activityId = event.dataTransfer.getData("text/plain") || draggedActivityId;
      var currentStage = event.dataTransfer.getData("application/x-activity-stage") || draggedStage;
      var newStage = column.getAttribute("data-drop-stage") || "";
      var card = draggedCard || root.querySelector('.activities-kanban-card[data-activity-id="' + activityId + '"]');
      if (!activityId || !newStage || newStage === currentStage) return;

      suppressClickUntil = Date.now() + 250;
      // Feedback imediato: card já muda de coluna enquanto o servidor salva.
      moveCardOptimistically(card, column, newStage);
      moveActivity(activityId, newStage).then(refreshBoard).catch(function () {
        window.alert("Não foi possível mover a atividade. Tente novamente.");
        moveActivity(activityId, currentStage).then(refreshBoard).catch(function () {
          window.location.reload();
        });
      });
    });

    root.addEventListener("click", function (event) {
      if (Date.now() < suppressClickUntil) return;
      var card = event.target.closest(".activities-kanban-card");
      if (!card || !root.contains(card)) return;
      openActivityPanel(card);
    });

    root.addEventListener("keydown", function (event) {
      var card = event.target.closest(".activities-kanban-card");
      if (!card || !root.contains(card)) return;
      if (event.key !== "Enter" && event.key !== " ") return;
      event.preventDefault();
      openActivityPanel(card);
    });
  }

  function initKanbanBoard() {
    bindKanbanBoard(getRoot());
  }

  window.initActivitiesKanbanBoard = initKanbanBoard;

  document.addEventListener("DOMContentLoaded", initKanbanBoard);
  document.body.addEventListener("htmx:afterSwap", function (event) {
    if (event.target && event.target.id === "activities-root") {
      bindKanbanBoard(event.target);
    }
  });
})();
