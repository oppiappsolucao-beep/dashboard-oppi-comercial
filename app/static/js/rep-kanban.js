(function () {
  var board = document.getElementById("rep-kanban-board");
  var modal = document.getElementById("os-order-modal");
  var modalBody = document.getElementById("os-order-modal-body");
  if (!board) return;
  var coarse = window.matchMedia("(pointer: coarse)").matches || window.matchMedia("(max-width: 900px)").matches;
  if (coarse) {
    board.querySelectorAll(".activities-kanban-card").forEach(function (card) {
      card.setAttribute("draggable", "false");
    });
  }
  var dragged = null;
  var origin = null;
  var suppressClick = false;

  function closeModal() {
    if (!modal) return;
    modal.hidden = true;
    if (modalBody) modalBody.innerHTML = "";
  }

  function line(label, value) {
    var paragraph = document.createElement("p");
    var strong = document.createElement("strong");
    strong.textContent = label + ": ";
    paragraph.appendChild(strong);
    paragraph.appendChild(document.createTextNode(value || "Não informado"));
    return paragraph;
  }

  function showCompany(card) {
    if (!modal || !modalBody || !card) return;
    modal.hidden = false;
    modalBody.textContent = "";
    var detail = document.createElement("div");
    detail.className = "os-detail";
    var title = document.createElement("h2");
    title.id = "os-modal-title";
    title.textContent = card.getAttribute("data-empresa") || "Empresa";
    detail.appendChild(title);
    var block = document.createElement("section");
    block.className = "os-detail-block";
    var heading = document.createElement("h3");
    heading.textContent = "Cliente";
    block.appendChild(heading);
    block.appendChild(line("CNPJ", card.getAttribute("data-cnpj")));
    block.appendChild(line("Telefone", card.getAttribute("data-telefone")));
    block.appendChild(line("UF", card.getAttribute("data-uf")));
    detail.appendChild(block);
    modalBody.appendChild(detail);
  }

  function openOrder(card) {
    if (!modal || !modalBody || !card) return;
    var orderId = card.getAttribute("data-order-id") || "";
    if (!orderId) {
      showCompany(card);
      return;
    }
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
        showCompany(card);
      });
  }

  function countColumn(column) {
    var body = column.querySelector(".activities-kanban-column-body");
    var countEl = column.querySelector("[data-kanban-count]");
    if (!body || !countEl) return;
    var count = body.querySelectorAll(".activities-kanban-card").length;
    countEl.textContent = String(count);
    var queue = body.getAttribute("data-drop-queue") || "";
    var summary = document.querySelector('[data-summary-count="' + queue.replace(/"/g, "") + '"]');
    if (summary) summary.textContent = String(count);
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

  function closeMoveMenus(except) {
    board.querySelectorAll(".rep-move-menu").forEach(function (menu) {
      if (menu !== except) menu.hidden = true;
    });
  }

  function moveCard(card, queue, body) {
    var previous = card.parentElement;
    if (!previous || !body || previous === body) return;
    var empty = body.querySelector(".activities-kanban-empty");
    if (empty) empty.remove();
    body.appendChild(card);
    closeMoveMenus();
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
          return;
        }
        if (coarse) {
          var column = body.closest(".activities-kanban-column");
          if (column) column.scrollIntoView({ behavior: "smooth", inline: "center", block: "nearest" });
        }
      })
      .catch(function () {
        previous.appendChild(card);
        countColumn(body.closest(".activities-kanban-column"));
        countColumn(previous.closest(".activities-kanban-column"));
      });
  }

  function toggleMoveMenu(card) {
    var menu = card.querySelector(".rep-move-menu");
    if (!menu) return;
    if (!menu.hidden) {
      menu.hidden = true;
      return;
    }
    closeMoveMenus();
    var current = (card.parentElement && card.parentElement.getAttribute("data-drop-queue")) || "";
    menu.textContent = "";
    board.querySelectorAll("[data-drop-queue]").forEach(function (body) {
      var id = body.getAttribute("data-drop-queue") || "";
      if (!id || id === current) return;
      var title = body.closest(".activities-kanban-column").querySelector(".activities-kanban-column-title");
      var choice = document.createElement("button");
      choice.type = "button";
      choice.setAttribute("data-rep-queue", id);
      choice.textContent = title ? title.textContent : id;
      menu.appendChild(choice);
    });
    menu.hidden = false;
  }

  board.querySelectorAll(".activities-kanban-card").forEach(function (card) {
    card.addEventListener("dragstart", function (event) {
      if (event.target.closest("a, button")) {
        event.preventDefault();
        return;
      }
      suppressClick = true;
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
    card.addEventListener("click", function (event) {
      if (suppressClick) {
        suppressClick = false;
        return;
      }
      if (event.target.closest("[data-rep-move]")) {
        event.preventDefault();
        toggleMoveMenu(card);
        return;
      }
      var choice = event.target.closest("[data-rep-queue]");
      if (choice) {
        event.preventDefault();
        var queue = choice.getAttribute("data-rep-queue") || "";
        var body = board.querySelector('[data-drop-queue="' + queue.replace(/"/g, "") + '"]');
        if (body) moveCard(card, queue, body);
        return;
      }
      if (event.target.closest("a, button, textarea, input, select, label")) return;
      openOrder(card);
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
      if (origin === body) return;
      moveCard(card, queue, body);
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
          modalBody.textContent = "Não consegui salvar esta atualização.";
        });
    });
    document.addEventListener("keydown", function (event) {
      if (event.key === "Escape" && !modal.hidden) closeModal();
    });
  }
})();
