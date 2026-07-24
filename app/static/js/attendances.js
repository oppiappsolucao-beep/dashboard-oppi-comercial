(function () {
  "use strict";

  // Poll no SQLite (leve). SSE desligado: costuma travar/estressar o proxy do painel.
  var POLL_MS = 4000;
  var pollTimer = null;
  var lastUnread = 0;
  var lastInboxToken = "";
  var lastConversationToken = "";
  var soundEnabled = true;
  var syncInFlight = false;

  function $(sel, root) {
    return (root || document).querySelector(sel);
  }

  function selectedId() {
    var shell = $("#att-shell");
    if (!shell) return "";
    var thread = $("[data-conversation-id]", $("#att-chat-root") || document);
    if (thread && thread.getAttribute("data-conversation-id")) {
      return thread.getAttribute("data-conversation-id");
    }
    return shell.getAttribute("data-selected") || "";
  }

  function scrollMessages() {
    var box = $("#att-messages");
    if (box) box.scrollTop = box.scrollHeight;
  }

  function playNotify() {
    if (!soundEnabled) return;
    try {
      var Ctx = window.AudioContext || window.webkitAudioContext;
      if (!Ctx) return;
      var ctx = playNotify._ctx || (playNotify._ctx = new Ctx());
      var osc = ctx.createOscillator();
      var gain = ctx.createGain();
      osc.type = "sine";
      osc.frequency.value = 880;
      gain.gain.value = 0.04;
      osc.connect(gain);
      gain.connect(ctx.destination);
      osc.start();
      gain.gain.exponentialRampToValueAtTime(0.0001, ctx.currentTime + 0.18);
      osc.stop(ctx.currentTime + 0.2);
    } catch (e) { /* ignore */ }
  }

  function updateUnreadBadge(count) {
    count = Number(count) || 0;
    var pill = $("#att-unread-pill");
    if (pill) {
      pill.setAttribute("data-count", String(count));
      if (count > 0) {
        pill.hidden = false;
        pill.textContent = count + (count === 1 ? " nova" : " novas");
      } else {
        pill.hidden = true;
      }
    }
    var side = $("#att-sidebar-badge");
    if (side) {
      if (count > 0) {
        side.hidden = false;
        side.textContent = String(count > 99 ? "99+" : count);
      } else {
        side.hidden = true;
      }
    }
    if (count > lastUnread && lastUnread >= 0) {
      playNotify();
    }
    lastUnread = count;
  }

  function bumpConversationToTop(conversationId) {
    var list = $("#att-conversation-list");
    if (!list || !conversationId) return;
    var item = list.querySelector(
      '[data-conversation-id="' + conversationId.replace(/"/g, "") + '"]'
    );
    if (!item) return;
    if (list.firstElementChild !== item) {
      item.classList.add("att-conv-bump");
      list.insertBefore(item, list.firstChild);
    }
    list.scrollTop = 0;
  }

  function refreshList(opts) {
    opts = opts || {};
    var list = $("#att-conversation-list");
    var form = $(".att-filters");
    if (!list || !window.htmx || !form) return;
    window.htmx.ajax("POST", "/atendimentos/filtros", {
      target: "#att-conversation-list",
      swap: "innerHTML",
      source: form,
      values: {
        search: ($("#att-search") || {}).value || "",
        status: ($("#att-status") || {}).value || "todos",
        conversation_id: selectedId(),
      },
    });
    if (opts.bumpId) {
      setTimeout(function () {
        bumpConversationToTop(opts.bumpId);
      }, 120);
    } else if (list) {
      list.scrollTop = 0;
    }
  }

  function refreshThread() {
    var id = selectedId();
    if (!id || !window.htmx) return;
    var search = ($("#att-search") || {}).value || "";
    var status = ($("#att-status") || {}).value || "todos";
    window.htmx.ajax(
      "GET",
      "/atendimentos/conversa/" + encodeURIComponent(id) +
        "?search=" + encodeURIComponent(search) +
        "&status=" + encodeURIComponent(status),
      { target: "#att-chat-root", swap: "innerHTML" }
    );
  }

  function handleEvent(data) {
    if (!data || !data.type) return;
    if (data.type === "ping" || data.type === "connected") {
      if (typeof data.unread !== "undefined") updateUnreadBadge(data.unread);
      return;
    }
    if (data.type === "typing") {
      var el = document.getElementById("att-typing-" + data.conversation_id);
      if (el) el.hidden = !data.typing;
      if (data.typing) bumpConversationToTop(data.conversation_id);
      return;
    }
    if (data.type === "message" || data.type === "conversation_upsert" || data.type === "conversation_read") {
      if (data.type === "message" || data.type === "conversation_upsert") {
        bumpConversationToTop(data.conversation_id);
      }
      refreshList({ bumpId: data.conversation_id });
      if (data.conversation_id && data.conversation_id === selectedId()) {
        refreshThread();
      }
      // força próximo poll a detectar o estado novo
      lastInboxToken = "";
      lastConversationToken = "";
      fetch("/atendimentos/unread", { credentials: "same-origin" })
        .then(function (r) { return r.json(); })
        .then(function (j) { updateUnreadBadge(j.unread); })
        .catch(function () {});
    }
  }

  function pollSync() {
    if (syncInFlight || document.hidden) return;
    syncInFlight = true;
    var id = selectedId();
    var url = "/atendimentos/sync";
    if (id) url += "?conversation_id=" + encodeURIComponent(id);

    fetch(url, { credentials: "same-origin", cache: "no-store" })
      .then(function (r) {
        if (!r.ok) throw new Error("sync " + r.status);
        return r.json();
      })
      .then(function (j) {
        updateUnreadBadge(j.unread);

        var inboxChanged = lastInboxToken && j.inbox_token && j.inbox_token !== lastInboxToken;
        var convChanged =
          id &&
          lastConversationToken &&
          j.conversation_token &&
          j.conversation_token !== lastConversationToken;

        if (inboxChanged) {
          refreshList();
        }
        if (convChanged) {
          refreshThread();
        }

        if (j.inbox_token) lastInboxToken = j.inbox_token;
        if (id && j.conversation_token) {
          lastConversationToken = j.conversation_token;
        } else if (!id) {
          lastConversationToken = "";
        }
      })
      .catch(function () { /* ignore transient errors */ })
      .finally(function () {
        syncInFlight = false;
      });
  }

  function startPoll() {
    if (pollTimer) return;
    // snapshot inicial sem disparar refresh
    pollSync();
    pollTimer = setInterval(pollSync, POLL_MS);
  }

  function autoGrow(el) {
    if (!el) return;
    el.style.height = "auto";
    el.style.height = Math.min(el.scrollHeight, 120) + "px";
  }

  document.body.addEventListener("htmx:afterSwap", function (ev) {
    if (!ev || !ev.target) return;
    if (ev.target.id === "att-conversation-list") {
      var first = ev.target.querySelector(".att-conv-item");
      if (first) {
        first.classList.add("att-conv-bump");
        ev.target.scrollTop = 0;
      }
      return;
    }
    if (ev.target.id === "att-chat-root" || ev.target.id === "att-messages") {
      scrollMessages();
      bindComposerGuards(ev.target);
      autoGrow($(".att-composer-input"));
      var thread = $("[data-conversation-id]", $("#att-chat-root"));
      var shell = $("#att-shell");
      if (shell && thread) {
        shell.setAttribute("data-selected", thread.getAttribute("data-conversation-id") || "");
      }
      // reset token da conversa aberta para o próximo poll
      lastConversationToken = "";
    }
  });

  document.body.addEventListener("htmx:afterRequest", function (ev) {
    var path = (ev.detail && ev.detail.pathInfo && ev.detail.pathInfo.requestPath) || "";
    if (path.indexOf("/atendimentos/conversa/") === -1) return;
    if (path.indexOf("/enviar") === -1 && path.indexOf("/midia") === -1) return;
    if (ev.detail && ev.detail.successful === false) return;
    var id = selectedId();
    if (id) {
      bumpConversationToTop(id);
      refreshList({ bumpId: id });
      lastInboxToken = "";
      lastConversationToken = "";
    }
  });

  // Fonte da verdade do texto digitado (NÃO usar ta.value no envio —
  // Windows/Chrome troca "olá"→"óleo" no blur e no insertReplacementText).
  var composerRaw = new WeakMap();

  function composerForm(el) {
    return el && el.closest ? el.closest(".att-composer") : null;
  }

  function getComposerRaw(form) {
    if (!form) return "";
    if (composerRaw.has(form)) return composerRaw.get(form) || "";
    var ta = form.querySelector(".att-composer-input");
    return (ta && ta.value) || "";
  }

  function setComposerRaw(form, value) {
    if (!form) return "";
    var text = value == null ? "" : String(value);
    composerRaw.set(form, text);
    var snap = form.querySelector(".att-text-snap");
    if (snap) snap.value = text;
    return text;
  }

  function syncSnapFromRaw(form) {
    return setComposerRaw(form, getComposerRaw(form));
  }

  function bindComposerGuards(root) {
    var scope = root || document;
    scope.querySelectorAll(".att-composer-input").forEach(function (ta) {
      if (ta.dataset.attGuard === "1") return;
      ta.dataset.attGuard = "1";
      ta.setAttribute("spellcheck", "false");
      ta.setAttribute("autocorrect", "off");
      ta.setAttribute("autocapitalize", "off");
      ta.setAttribute("autocomplete", "off");
      ta.setAttribute("lang", "zxx");

      var form = composerForm(ta);
      if (form && !composerRaw.has(form)) {
        setComposerRaw(form, ta.value || "");
      }

      // Bloqueia autocorreção do SO/navegador (olá → óleo)
      ta.addEventListener("beforeinput", function (ev) {
        var type = ev.inputType || "";
        if (type === "insertReplacementText") {
          ev.preventDefault();
        }
      });

      ta.addEventListener("input", function (ev) {
        autoGrow(ta);
        var f = composerForm(ta);
        if (!f) return;
        var type = (ev && ev.inputType) || "";
        if (type === "insertReplacementText") {
          ta.value = getComposerRaw(f);
          return;
        }
        // Correção do Windows no blur: o campo já perdeu o foco — ignora e reverte
        if (document.activeElement !== ta) {
          ta.value = getComposerRaw(f);
          return;
        }
        setComposerRaw(f, ta.value || "");
      });

      // Se o SO corrigir no blur, reverte para o que foi digitado
      ta.addEventListener("blur", function () {
        var f = composerForm(ta);
        if (!f) return;
        var raw = getComposerRaw(f);
        if (ta.value !== raw) {
          ta.value = raw;
        }
      });
    });
  }

  bindComposerGuards(document);

  // Evita blur no textarea ao clicar Enviar (blur dispara autocorrect do Windows)
  document.addEventListener(
    "mousedown",
    function (ev) {
      var btn = ev.target && ev.target.closest ? ev.target.closest(".att-send-btn") : null;
      if (!btn) return;
      ev.preventDefault();
      var form = btn.closest("form");
      syncSnapFromRaw(form);
    },
    true
  );

  document.addEventListener(
    "keydown",
    function (ev) {
      var ta = ev.target;
      if (!ta || !ta.classList || !ta.classList.contains("att-composer-input")) return;
      if (ev.key !== "Enter" || ev.shiftKey) return;
      ev.preventDefault();
      var form = ta.closest("form");
      var value = syncSnapFromRaw(form);
      if (!String(value).trim()) return;
      if (form && window.htmx) {
        window.htmx.trigger(form, "submit");
      } else if (form) {
        form.requestSubmit();
      }
    },
    true
  );

  document.body.addEventListener("htmx:configRequest", function (ev) {
    var form = ev.target && ev.target.closest ? ev.target.closest(".att-composer") : null;
    if (!form) return;
    var path = (ev.detail && ev.detail.path) || "";
    if (path.indexOf("/enviar") === -1) return;
    var value = syncSnapFromRaw(form);
    if (ev.detail && ev.detail.parameters) {
      ev.detail.parameters.text = value;
    }
  });

  document.addEventListener("change", function (ev) {
    var input = ev.target;
    if (!input || input.id !== "att-media-input" || !input.files || !input.files[0]) return;
    var id = selectedId();
    if (!id) return;
    var fd = new FormData();
    fd.append("file", input.files[0]);
    var form = $(".att-composer");
    var caption = form ? getComposerRaw(form) : "";
    if (caption) fd.append("caption", caption);
    fetch("/atendimentos/conversa/" + encodeURIComponent(id) + "/midia", {
      method: "POST",
      body: fd,
      credentials: "same-origin",
      headers: { "HX-Request": "true" },
    })
      .then(function (r) { return r.text(); })
      .then(function (html) {
        var root = $("#att-chat-root");
        if (root) {
          root.innerHTML = html;
          if (window.htmx) window.htmx.process(root);
        }
        scrollMessages();
        bindComposerGuards(root || document);
        refreshList({ bumpId: id });
        lastInboxToken = "";
        lastConversationToken = "";
      })
      .catch(function () {});
    input.value = "";
  });

  document.addEventListener(
    "click",
    function () {
      soundEnabled = true;
      try {
        var Ctx = window.AudioContext || window.webkitAudioContext;
        if (Ctx) {
          playNotify._ctx = playNotify._ctx || new Ctx();
          if (playNotify._ctx.state === "suspended") playNotify._ctx.resume();
        }
      } catch (e) { /* ignore */ }
    },
    { once: true }
  );

  document.addEventListener("visibilitychange", function () {
    if (!document.hidden) pollSync();
  });

  if ($("#att-shell")) {
    var pill = $("#att-unread-pill");
    lastUnread = pill ? Number(pill.getAttribute("data-count") || 0) : 0;
    scrollMessages();
    startPoll();
  }
})();
