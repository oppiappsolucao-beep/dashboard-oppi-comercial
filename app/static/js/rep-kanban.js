(function () {
  var board = document.getElementById("rep-kanban-board");
  if (!board) return;
  var dragged = null;
  var origin = null;

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
      note.textContent = "Nenhuma empresa";
      body.appendChild(note);
    } else if (count > 0 && empty) {
      empty.remove();
    }
  }

  board.querySelectorAll(".activities-kanban-card").forEach(function (card) {
    card.addEventListener("dragstart", function (event) {
      if (event.target.closest("a, button")) {
        event.preventDefault();
        return;
      }
      dragged = card;
      origin = card.parentElement;
      card.classList.add("is-dragging");
      if (event.dataTransfer) {
        event.dataTransfer.effectAllowed = "move";
        event.dataTransfer.setData("text/plain", card.getAttribute("data-prospect") || "");
      }
    });
    card.addEventListener("dragend", function () {
      card.classList.remove("is-dragging");
      dragged = null;
    });
  });

  board.querySelectorAll("[data-drop-queue]").forEach(function (body) {
    body.addEventListener("dragover", function (event) {
      if (!dragged) return;
      event.preventDefault();
    });
    body.addEventListener("drop", function (event) {
      if (!dragged || !origin) return;
      event.preventDefault();
      var queue = body.getAttribute("data-drop-queue") || "";
      var card = dragged;
      var previous = origin;
      if (previous === body) return;
      var empty = body.querySelector(".activities-kanban-empty");
      if (empty) empty.remove();
      body.appendChild(card);
      countColumn(body.closest(".activities-kanban-column"));
      countColumn(previous.closest(".activities-kanban-column"));
      var payload = new URLSearchParams();
      payload.set("prospect", card.getAttribute("data-prospect") || "");
      payload.set("queue", queue);
      fetch("/atividades/representante/mover", {
        method: "POST",
        headers: { "Content-Type": "application/x-www-form-urlencoded" },
        body: payload.toString(),
      })
        .then(function (response) {
          return response.json().then(function (data) {
            if (!response.ok || !data.ok) throw new Error((data && data.error) || "fail");
            return data;
          });
        })
        .then(function (data) {
          if (queue === "concluida" && data.cadastro_url) {
            window.location.href = data.cadastro_url;
          }
        })
        .catch(function () {
          previous.appendChild(card);
          countColumn(body.closest(".activities-kanban-column"));
          countColumn(previous.closest(".activities-kanban-column"));
        });
    });
  });
})();
