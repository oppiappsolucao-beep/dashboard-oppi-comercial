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
    el.style.height = Math.min(Math.max(el.scrollHeight, 40), 120) + "px";
  }

  // Digitação sintética: div NÃO é contenteditable — Windows não tem corretor onde agir.
  var composerRaw = new WeakMap();

  function composerForm(el) {
    return el && el.closest ? el.closest(".att-composer") : null;
  }

  function composerInput(form) {
    return form ? form.querySelector(".att-composer-input") : null;
  }

  function getComposerRaw(form) {
    if (!form) return "";
    return composerRaw.has(form) ? composerRaw.get(form) || "" : "";
  }

  function renderComposer(form) {
    if (!form) return "";
    var el = composerInput(form);
    var text = getComposerRaw(form);
    var snap = form.querySelector(".att-text-snap");
    if (snap) snap.value = text;
    if (!el) return text;

    el.textContent = "";
    if (text) {
      el.appendChild(document.createTextNode(text));
    }
    if (document.activeElement === el) {
      var caret = document.createElement("span");
      caret.className = "att-caret";
      caret.setAttribute("aria-hidden", "true");
      el.appendChild(caret);
    }
    autoGrow(el);
    return text;
  }

  function setComposerRaw(form, value) {
    if (!form) return "";
    var text = value == null ? "" : String(value);
    // Limite defensivo
    if (text.length > 4000) text = text.slice(0, 4000);
    composerRaw.set(form, text);
    return renderComposer(form);
  }

  function textForSend(form) {
    return setComposerRaw(form, getComposerRaw(form));
  }

  function insertComposerText(form, chunk) {
    if (!form) return;
    chunk = String(chunk || "").replace(/\r\n/g, "\n").replace(/\r/g, "\n");
    if (!chunk) return;
    setComposerRaw(form, getComposerRaw(form) + chunk);
  }

  function bindSyntheticComposer(root) {
    var scope = root || document;
    scope.querySelectorAll(".att-composer-input[data-synthetic='1']").forEach(function (el) {
      if (el.dataset.attGuard === "1") return;
      el.dataset.attGuard = "1";
      // Garante: nunca contenteditable (mata corretor)
      el.removeAttribute("contenteditable");
      el.setAttribute("contenteditable", "false");
      el.setAttribute("spellcheck", "false");

      var form = composerForm(el);
      if (form && !composerRaw.has(form)) setComposerRaw(form, "");

      el.addEventListener("focus", function () {
        renderComposer(composerForm(el));
      });

      el.addEventListener("blur", function () {
        renderComposer(composerForm(el));
      });

      el.addEventListener("keydown", function (ev) {
        var f = composerForm(el);
        if (!f || el.getAttribute("aria-disabled") === "true") return;

        if (ev.key === "Enter" && !ev.shiftKey) {
          ev.preventDefault();
          submitComposer(f);
          return;
        }
        if (ev.key === "Enter" && ev.shiftKey) {
          ev.preventDefault();
          insertComposerText(f, "\n");
          return;
        }
        if (ev.key === "Backspace") {
          ev.preventDefault();
          setComposerRaw(f, getComposerRaw(f).slice(0, -1));
          return;
        }
        if (ev.key === "Delete") {
          ev.preventDefault();
          return;
        }
        if (ev.ctrlKey || ev.metaKey || ev.altKey) {
          // Ctrl+V tratado no paste; Ctrl+A/C etc. ok
          if (ev.key === "v" || ev.key === "V") return; // paste event
          if (ev.key === "a" || ev.key === "A" || ev.key === "c" || ev.key === "C") return;
          return;
        }
        if (ev.key === "Tab") return;
        if (ev.key.length === 1) {
          ev.preventDefault();
          insertComposerText(f, ev.key);
        }
      });

      el.addEventListener("paste", function (ev) {
        ev.preventDefault();
        var f = composerForm(el);
        var clip = ev.clipboardData || window.clipboardData;
        var text = clip ? clip.getData("text/plain") : "";
        insertComposerText(f, text);
      });

      el.addEventListener("beforeinput", function (ev) {
        ev.preventDefault();
      });
    });
  }

  bindSyntheticComposer(document);

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
      bindSyntheticComposer(ev.target);
      autoGrow($(".att-composer-input"));
      var thread = $("[data-conversation-id]", $("#att-chat-root"));
      var shell = $("#att-shell");
      if (shell && thread) {
        shell.setAttribute("data-selected", thread.getAttribute("data-conversation-id") || "");
      }
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

  function submitComposer(form) {
    if (!form) return;
    var value = textForSend(form);
    if (!String(value).trim()) return;
    if (window.htmx) {
      window.htmx.trigger(form, "submit");
    }
  }

  document.addEventListener(
    "click",
    function (ev) {
      var btn = ev.target && ev.target.closest ? ev.target.closest(".att-send-btn") : null;
      if (!btn || btn.disabled) return;
      ev.preventDefault();
      submitComposer(btn.closest("form"));
    },
    true
  );

  document.body.addEventListener("htmx:configRequest", function (ev) {
    var form = ev.target && ev.target.closest ? ev.target.closest(".att-composer") : null;
    if (!form) return;
    var path = (ev.detail && ev.detail.path) || "";
    if (path.indexOf("/enviar") === -1) return;
    var value = textForSend(form);
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
    var caption = form ? textForSend(form) : "";
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
        bindSyntheticComposer(root || document);
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
