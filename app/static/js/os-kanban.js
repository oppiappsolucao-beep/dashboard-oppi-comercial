(function () {
  var board = document.getElementById("os-kanban-board");
  var modal = document.getElementById("os-order-modal");
  var modalBody = document.getElementById("os-order-modal-body");
  if (!board) return;
  var dragged = null;
  var suppressClick = false;

  function countColumn(column) {
    var body = column.querySelector(".activities-kanban-column-body");
    var countEl = column.querySelector("[data-kanban-count]");
    if (!body || !countEl) return;
    var count = body.querySelectorAll(".activities-kanban-card").length;
    countEl.textContent = String(count);
    var empty = body.querySelector(".activities-kanban-empty");
    if (count === 0 && !empty) {
      var note = document.createElement("div");
      note.className = "activities-kanban-empty";
      note.textContent = "Nenhuma ordem";
      body.appendChild(note);
    } else if (count > 0 && empty) {
      empty.remove();
    }
  }

  function closeModal() {
    if (!modal) return;
    modal.hidden = true;
    if (modalBody) modalBody.innerHTML = "";
  }

  function openOrder(orderId) {
    if (!modal || !modalBody) return;
    modal.hidden = false;
    modalBody.textContent = "Carregando…";
    fetch("/atividades/os/" + encodeURIComponent(orderId))
      .then(function (response) {
        if (!response.ok) throw new Error("fail");
        return response.text();
      })
      .then(function (html) {
        modalBody.innerHTML = html;
      })
      .catch(function () {
        modalBody.textContent = "Não consegui abrir esta ordem.";
      });
  }

  board.querySelectorAll(".activities-kanban-card").forEach(function (card) {
    card.addEventListener("dragstart", function () {
      suppressClick = true;
      dragged = card;
      card.classList.add("is-dragging");
    });
    card.addEventListener("dragend", function () {
      card.classList.remove("is-dragging");
      dragged = null;
    });
    card.addEventListener("click", function (event) {
      if (suppressClick) {
        suppressClick = false;
        return;
      }
      if (event.target.closest("a, button, textarea, input")) return;
      openOrder(card.getAttribute("data-order-id"));
    });
  });

  board.querySelectorAll(".activities-kanban-column-body").forEach(function (body) {
    body.addEventListener("dragover", function (event) {
      event.preventDefault();
      body.classList.add("is-drop-target");
    });
    body.addEventListener("dragleave", function () {
      body.classList.remove("is-drop-target");
    });
    body.addEventListener("drop", function (event) {
      event.preventDefault();
      body.classList.remove("is-drop-target");
      if (!dragged) return;
      var queueId = body.getAttribute("data-drop-queue");
      var orderId = dragged.getAttribute("data-order-id");
      var fromQueue = dragged.getAttribute("data-current-queue");
      if (!queueId || !orderId || fromQueue === queueId) return;
      var reopen = false;
      if (fromQueue === "concluida") {
        reopen = window.confirm("Esta ordem já foi concluída. Deseja realmente reabrir?");
        if (!reopen) return;
      }
      var previous = dragged.parentElement;
      var empty = body.querySelector(".activities-kanban-empty");
      if (empty) empty.remove();
      body.appendChild(dragged);
      dragged.setAttribute("data-current-queue", queueId);
      if (previous) countColumn(previous.closest(".activities-kanban-column"));
      countColumn(body.closest(".activities-kanban-column"));
      var data = new FormData();
      data.set("queue_id", queueId);
      if (reopen) data.set("reopen", "1");
      var sector = document.getElementById("os-board-sector");
      if (sector) data.set("sector_id", sector.value);
      var cadastroUrl = dragged.getAttribute("data-cadastro-url") || "";
      fetch("/atividades/os/" + encodeURIComponent(orderId) + "/fila", {
        method: "POST",
        body: data,
      }).then(function (response) {
        if (!response.ok) {
          window.location.reload();
          return;
        }
        if (queueId === "concluida" && cadastroUrl) window.location.href = cadastroUrl;
      }).catch(function () {
        window.location.reload();
      });
    });
  });

  if (modal) {
    modal.addEventListener("click", function (event) {
      if (event.target.closest("[data-os-close]")) closeModal();
    });
    modal.addEventListener("submit", function (event) {
      var form = event.target;
      if (!form || form.id !== "os-update-form") return;
      event.preventDefault();
      fetch(form.action, { method: "POST", body: new FormData(form) })
        .then(function (response) {
          if (!response.ok) throw new Error("fail");
          return response.text();
        })
        .then(function (html) {
          modalBody.innerHTML = html;
        })
        .catch(function () {
          window.location.reload();
        });
    });
    document.addEventListener("keydown", function (event) {
      if (event.key === "Escape" && !modal.hidden) closeModal();
    });
  }
})();
