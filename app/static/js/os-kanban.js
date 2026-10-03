(function () {
  var board = document.getElementById("os-kanban-board");
  if (!board) return;
  var dragged = null;

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

  board.querySelectorAll(".activities-kanban-card").forEach(function (card) {
    card.addEventListener("dragstart", function () {
      dragged = card;
      card.classList.add("is-dragging");
    });
    card.addEventListener("dragend", function () {
      card.classList.remove("is-dragging");
      dragged = null;
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
      if (!queueId || !orderId || dragged.getAttribute("data-current-queue") === queueId) return;
      var previous = dragged.parentElement;
      var empty = body.querySelector(".activities-kanban-empty");
      if (empty) empty.remove();
      body.appendChild(dragged);
      dragged.setAttribute("data-current-queue", queueId);
      if (previous) countColumn(previous.closest(".activities-kanban-column"));
      countColumn(body.closest(".activities-kanban-column"));
      var data = new FormData();
      data.set("queue_id", queueId);
      var sector = document.getElementById("os-board-sector");
      if (sector) data.set("sector_id", sector.value);
      fetch("/atividades/os/" + encodeURIComponent(orderId) + "/fila", {
        method: "POST",
        body: data,
      }).then(function (response) {
        if (!response.ok) window.location.reload();
      }).catch(function () {
        window.location.reload();
      });
    });
  });
})();
