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

  function moveCard(card, queue, body) {
    var previous = card.parentElement;
    if (!previous || !body || previous === body) return;
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

  var pressTimer = null;
  var pressCard = null;
  var pressId = null;
  var pressX = 0;
  var pressY = 0;
  var lastX = 0;
  var lastY = 0;
  var draggingTouch = false;
  var ghost = null;
  var ghostOffsetX = 0;
  var ghostOffsetY = 0;
  var scrollFrame = 0;

  function columnBodyAt(x, y) {
    if (ghost) ghost.hidden = true;
    var under = document.elementFromPoint(x, y);
    if (ghost) ghost.hidden = false;
    if (!under) return null;
    var body = under.closest("[data-drop-queue]");
    if (body && board.contains(body)) return body;
    var column = under.closest(".activities-kanban-column");
    if (column && board.contains(column)) return column.querySelector("[data-drop-queue]");
    return null;
  }

  function markDrop(x, y) {
    board.querySelectorAll(".is-drop-target").forEach(function (item) {
      item.classList.remove("is-drop-target");
    });
    var body = columnBodyAt(x, y);
    if (body) body.classList.add("is-drop-target");
    return body;
  }

  function placeGhost(x, y) {
    if (!ghost) return;
    ghost.style.left = (x - ghostOffsetX) + "px";
    ghost.style.top = (y - ghostOffsetY) + "px";
  }

  function autoScrollBoard(x) {
    var rect = board.getBoundingClientRect();
    var edge = 56;
    if (x < rect.left + edge) board.scrollLeft -= 14;
    else if (x > rect.right - edge) board.scrollLeft += 14;
  }

  function scrollWhileDragging() {
    if (!draggingTouch) return;
    autoScrollBoard(lastX);
    markDrop(lastX, lastY);
    scrollFrame = window.requestAnimationFrame(scrollWhileDragging);
  }

  function beginTouchDrag(card, x, y, pointerId) {
    draggingTouch = true;
    suppressClick = true;
    card.classList.remove("is-pressing");
    card.classList.add("is-touch-dragging");
    try { card.setPointerCapture(pointerId); } catch (error) { /* ignore */ }
    var rect = card.getBoundingClientRect();
    ghost = card.cloneNode(true);
    ghost.removeAttribute("id");
    ghost.classList.remove("is-touch-dragging", "is-pressing");
    ghost.classList.add("activities-kanban-card-ghost");
    ghost.style.position = "fixed";
    ghost.style.width = rect.width + "px";
    ghost.style.zIndex = "1300";
    ghost.style.margin = "0";
    ghostOffsetX = Math.min(Math.max(x - rect.left, 12), rect.width - 12);
    ghostOffsetY = Math.min(Math.max(y - rect.top, 12), rect.height - 12);
    document.body.appendChild(ghost);
    placeGhost(x, y);
    markDrop(x, y);
    scrollFrame = window.requestAnimationFrame(scrollWhileDragging);
    if (navigator.vibrate) {
      try { navigator.vibrate(15); } catch (error) { /* ignore */ }
    }
  }

  function endTouchDrag(x, y, dropped) {
    var card = pressCard;
    var wasDragging = draggingTouch;
    clearTimeout(pressTimer);
    pressTimer = null;
    draggingTouch = false;
    if (scrollFrame) window.cancelAnimationFrame(scrollFrame);
    scrollFrame = 0;
    if (card) card.classList.remove("is-pressing", "is-touch-dragging");
    var body = wasDragging && dropped ? columnBodyAt(x, y) : null;
    if (ghost) {
      ghost.remove();
      ghost = null;
    }
    board.querySelectorAll(".is-drop-target").forEach(function (item) {
      item.classList.remove("is-drop-target");
    });
    pressCard = null;
    pressId = null;
    if (!wasDragging || !dropped || !card || !body) return;
    suppressClick = true;
    window.setTimeout(function () { suppressClick = false; }, 400);
    moveCard(card, body.getAttribute("data-drop-queue") || "", body);
  }

  if (coarse) {
    board.addEventListener("contextmenu", function (event) {
      if (event.target.closest(".activities-kanban-card")) event.preventDefault();
    });
    board.addEventListener("pointerdown", function (event) {
      if (event.pointerType === "mouse") return;
      var card = event.target.closest(".activities-kanban-card");
      if (!card || event.target.closest("a, button, textarea, input, select, label")) return;
      pressCard = card;
      pressId = event.pointerId;
      pressX = event.clientX;
      pressY = event.clientY;
      lastX = pressX;
      lastY = pressY;
      card.classList.add("is-pressing");
      clearTimeout(pressTimer);
      pressTimer = setTimeout(function () {
        if (!pressCard || draggingTouch) return;
        beginTouchDrag(pressCard, lastX, lastY, pressId);
      }, 280);
    });
    board.addEventListener("pointermove", function (event) {
      if (!pressCard || event.pointerId !== pressId) return;
      lastX = event.clientX;
      lastY = event.clientY;
      if (!draggingTouch) {
        if (Math.abs(event.clientX - pressX) + Math.abs(event.clientY - pressY) > 12) {
          clearTimeout(pressTimer);
          pressCard.classList.remove("is-pressing");
          pressCard = null;
          pressId = null;
        }
        return;
      }
      event.preventDefault();
      placeGhost(event.clientX, event.clientY);
      markDrop(event.clientX, event.clientY);
      autoScrollBoard(event.clientX);
    }, { passive: false });
    board.addEventListener("pointerup", function (event) {
      if (!pressCard || event.pointerId !== pressId) return;
      endTouchDrag(event.clientX, event.clientY, true);
    });
    board.addEventListener("pointercancel", function (event) {
      if (!pressCard || event.pointerId !== pressId) return;
      endTouchDrag(event.clientX, event.clientY, false);
    });
    document.addEventListener("touchmove", function (event) {
      if (draggingTouch) event.preventDefault();
    }, { passive: false });
  }

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
