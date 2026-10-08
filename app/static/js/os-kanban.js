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
    var count = body.querySelectorAll(".activities-kanban-card:not([hidden])").length;
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
    var summary = document.querySelector(".os-summary.is-columns");
    if (summary) {
      var totals = summary.querySelectorAll("strong");
      board.querySelectorAll("[data-kanban-count]").forEach(function (el, index) {
        if (totals[index]) totals[index].textContent = el.textContent;
      });
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
    card.addEventListener("dragstart", function (event) {
      if (event.target.closest("select, label")) {
        event.preventDefault();
        return;
      }
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
      if (event.target.closest("a, button, textarea, input, select, label")) return;
      openOrder(card.getAttribute("data-order-id"));
    });
  });

  function placeCard(card, body) {
    if (!card || !body) return;
    var queueId = body.getAttribute("data-drop-queue");
    var orderId = card.getAttribute("data-order-id");
    var fromQueue = card.getAttribute("data-current-queue");
    if (!queueId || !orderId || fromQueue === queueId) return;
    var reopen = false;
    if (fromQueue === "concluida") {
      reopen = window.confirm("Esta ordem já foi concluída. Deseja realmente reabrir?");
      if (!reopen) return;
    }
    var previous = card.parentElement;
    var empty = body.querySelector(".activities-kanban-empty");
    if (empty) empty.remove();
    body.appendChild(card);
    card.setAttribute("data-current-queue", queueId);
    if (previous) countColumn(previous.closest(".activities-kanban-column"));
    countColumn(body.closest(".activities-kanban-column"));
    postMove(card, queueId, reopen);
  }

  var touchCard = null;
  var touchId = null;
  var touchX = 0;
  var touchY = 0;
  var touchMoved = false;

  board.addEventListener("pointerdown", function (event) {
    if (event.pointerType === "mouse") return;
    var card = event.target.closest(".activities-kanban-card");
    if (!card || event.target.closest("a, button, textarea, input, select, label")) return;
    touchCard = card;
    touchId = event.pointerId;
    touchX = event.clientX;
    touchY = event.clientY;
    touchMoved = false;
  });

  board.addEventListener("pointermove", function (event) {
    if (!touchCard || event.pointerId !== touchId) return;
    var dx = event.clientX - touchX;
    var dy = event.clientY - touchY;
    if (!touchMoved && Math.abs(dx) + Math.abs(dy) < 10) return;
    if (!touchMoved) {
      touchMoved = true;
      suppressClick = true;
      touchCard.classList.add("is-dragging");
      document.body.classList.add("activity-kanban-dragging");
      try { touchCard.setPointerCapture(event.pointerId); } catch (error) { /* ignore */ }
    }
    event.preventDefault();
    board.querySelectorAll(".is-drop-target").forEach(function (item) {
      item.classList.remove("is-drop-target");
    });
    var under = document.elementFromPoint(event.clientX, event.clientY);
    var body = under && under.closest(".activities-kanban-column-body");
    if (body) body.classList.add("is-drop-target");
  }, { passive: false });

  function endTouch(event) {
    if (!touchCard || event.pointerId !== touchId) return;
    var card = touchCard;
    var moved = touchMoved;
    touchCard = null;
    touchId = null;
    touchMoved = false;
    var under = moved ? document.elementFromPoint(event.clientX, event.clientY) : null;
    var body = under && under.closest(".activities-kanban-column-body");
    card.classList.remove("is-dragging");
    document.body.classList.remove("activity-kanban-dragging");
    board.querySelectorAll(".is-drop-target").forEach(function (item) {
      item.classList.remove("is-drop-target");
    });
    if (!moved) return;
    suppressClick = true;
    window.setTimeout(function () { suppressClick = false; }, 350);
    if (body) placeCard(card, body);
  }

  board.addEventListener("pointerup", endTouch);
  board.addEventListener("pointercancel", endTouch);

  var search = document.getElementById("os-card-filter");
  if (search) {
    search.addEventListener("input", function () {
      var query = search.value.trim().toLowerCase();
      var queryDigits = query.replace(/\D/g, "");
      board.querySelectorAll(".activities-kanban-card").forEach(function (card) {
        var hay = (card.getAttribute("data-search") || "").toLowerCase();
        var hayDigits = hay.replace(/\D/g, "");
        var match = !query
          || hay.indexOf(query) !== -1
          || (queryDigits.length >= 2 && hayDigits.indexOf(queryDigits) !== -1);
        card.hidden = !match;
      });
      board.querySelectorAll(".activities-kanban-column").forEach(countColumn);
    });
  }

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
      placeCard(dragged, body);
    });
  });

  function postMove(card, queueId, reopen) {
    var orderId = card.getAttribute("data-order-id");
    var data = new FormData();
    data.set("queue_id", queueId);
    if (reopen) data.set("reopen", "1");
    var sector = document.getElementById("os-board-sector");
    if (sector) data.set("sector_id", sector.value);
    var cadastroUrl = card.getAttribute("data-cadastro-url") || "";
    var column = card.closest(".activities-kanban-column");
    var titleEl = column && column.querySelector(".activities-kanban-column-title");
    var isProposal = titleEl && titleEl.textContent.toLowerCase().indexOf("proposta") !== -1;
    fetch("/atividades/os/" + encodeURIComponent(orderId) + "/fila", {
      method: "POST",
      body: data,
    }).then(function (response) {
      if (!response.ok) {
        window.location.reload();
        return "";
      }
      return response.text();
    }).then(function (payload) {
      var target = "";
      if (payload && payload.charAt(0) === "/") target = payload.trim();
      if (!target && queueId === "concluida") target = cadastroUrl;
      if (!target && isProposal) target = "/atividades/os/" + encodeURIComponent(orderId) + "/proposta";
      if (target) window.location.href = target;
    }).catch(function () {
      window.location.reload();
    });
  }

  board.querySelectorAll("[data-os-status]").forEach(function (select) {
    select.addEventListener("mousedown", function (event) { event.stopPropagation(); });
    select.addEventListener("click", function (event) { event.stopPropagation(); });
    select.addEventListener("change", function () {
      var card = select.closest(".activities-kanban-card");
      if (!card) return;
      var queueId = select.value;
      var fromQueue = card.getAttribute("data-current-queue");
      if (!queueId || fromQueue === queueId) return;
      var reopen = false;
      if (fromQueue === "concluida") {
        reopen = window.confirm("Esta ordem já foi concluída. Deseja realmente reabrir?");
        if (!reopen) {
          select.value = fromQueue;
          return;
        }
      }
      var body = select.closest(".activities-kanban-column-body");
      var targetBody = board.querySelector('[data-drop-queue="' + queueId + '"]');
      if (targetBody) {
        var empty = targetBody.querySelector(".activities-kanban-empty");
        if (empty) empty.remove();
        targetBody.appendChild(card);
        if (body) countColumn(body.closest(".activities-kanban-column"));
        countColumn(targetBody.closest(".activities-kanban-column"));
      }
      card.setAttribute("data-current-queue", queueId);
      postMove(card, queueId, reopen);
    });
  });

  if (modal) {
    modal.addEventListener("click", function (event) {
      var copy = event.target.closest("[data-copy-phone]");
      if (copy) {
        event.preventDefault();
        var value = copy.getAttribute("data-copy-phone") || "";
        var mark = function () { copy.textContent = "Copiado"; };
        if (navigator.clipboard && navigator.clipboard.writeText) {
          navigator.clipboard.writeText(value).then(mark).catch(function () {
            window.prompt("Copie o número:", value);
          });
        } else {
          window.prompt("Copie o número:", value);
        }
        return;
      }
      if (event.target.closest("[data-os-close]")) closeModal();
    });
    modal.addEventListener("submit", function (event) {
      var form = event.target;
      if (!form || (form.id !== "os-update-form" && form.id !== "os-sector-form" && form.id !== "os-queue-form")) return;
      event.preventDefault();
      if (form.id === "os-queue-form") {
        var queueSelect = form.querySelector("[name=queue_id]");
        var fromQueue = form.getAttribute("data-current-queue") || "";
        var data = new FormData(form);
        if (fromQueue === "concluida" && queueSelect && queueSelect.value !== "concluida") {
          if (!window.confirm("Esta ordem já foi concluída. Deseja realmente reabrir?")) {
            queueSelect.value = fromQueue;
            return;
          }
          data.set("reopen", "1");
        }
        fetch(form.action, { method: "POST", body: data })
          .then(function (response) {
            if (!response.ok) throw new Error("fail");
            return response.text();
          })
          .then(function (payload) {
            var target = payload && payload.charAt(0) === "/" ? payload.trim() : "";
            window.location.href = target || window.location.href;
          })
          .catch(function () {
            window.location.reload();
          });
        return;
      }
      var reloadBoard = form.id === "os-sector-form";
      fetch(form.action, { method: "POST", body: new FormData(form) })
        .then(function (response) {
          if (!response.ok) throw new Error("fail");
          return response.text();
        })
        .then(function (html) {
          if (reloadBoard) {
            window.location.reload();
            return;
          }
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
