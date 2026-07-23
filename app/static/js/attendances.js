(function () {
  "use strict";

  var POLL_MS = 20000;
  var es = null;
  var pollTimer = null;
  var lastUnread = 0;
  var soundEnabled = true;

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

  function refreshList() {
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
      refreshList();
      return;
    }
    if (data.type === "message" || data.type === "conversation_upsert" || data.type === "conversation_read") {
      refreshList();
      if (data.conversation_id && data.conversation_id === selectedId()) {
        refreshThread();
      }
      fetch("/atendimentos/unread", { credentials: "same-origin" })
        .then(function (r) { return r.json(); })
        .then(function (j) { updateUnreadBadge(j.unread); })
        .catch(function () {});
    }
  }

  function startSSE() {
    if (!window.EventSource) {
      startPoll();
      return;
    }
    try {
      es = new EventSource("/atendimentos/stream");
      es.onmessage = function (ev) {
        try {
          handleEvent(JSON.parse(ev.data));
        } catch (e) { /* ignore */ }
      };
      es.onerror = function () {
        if (es) {
          es.close();
          es = null;
        }
        startPoll();
        setTimeout(startSSE, 8000);
      };
    } catch (e) {
      startPoll();
    }
  }

  function startPoll() {
    if (pollTimer) return;
    pollTimer = setInterval(function () {
      fetch("/atendimentos/unread", { credentials: "same-origin" })
        .then(function (r) { return r.json(); })
        .then(function (j) {
          var prev = lastUnread;
          updateUnreadBadge(j.unread);
          if (j.unread !== prev) {
            refreshList();
            if (selectedId()) refreshThread();
          }
        })
        .catch(function () {});
    }, POLL_MS);
  }

  function autoGrow(el) {
    if (!el) return;
    el.style.height = "auto";
    el.style.height = Math.min(el.scrollHeight, 120) + "px";
  }

  document.body.addEventListener("htmx:afterSwap", function (ev) {
    if (ev && ev.target && (ev.target.id === "att-chat-root" || ev.target.id === "att-messages")) {
      scrollMessages();
      autoGrow($(".att-composer-input"));
      var thread = $("[data-conversation-id]", $("#att-chat-root"));
      var shell = $("#att-shell");
      if (shell && thread) {
        shell.setAttribute("data-selected", thread.getAttribute("data-conversation-id") || "");
      }
    }
  });

  document.addEventListener("input", function (ev) {
    if (ev.target && ev.target.classList.contains("att-composer-input")) {
      autoGrow(ev.target);
    }
  });

  document.addEventListener("change", function (ev) {
    var input = ev.target;
    if (!input || input.id !== "att-media-input" || !input.files || !input.files[0]) return;
    var id = selectedId();
    if (!id) return;
    var fd = new FormData();
    fd.append("file", input.files[0]);
    var captionEl = $(".att-composer-input");
    if (captionEl && captionEl.value) fd.append("caption", captionEl.value);
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
        refreshList();
      })
      .catch(function () {});
    input.value = "";
  });

  // Enable sound after first user gesture
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

  if ($("#att-shell")) {
    var pill = $("#att-unread-pill");
    lastUnread = pill ? Number(pill.getAttribute("data-count") || 0) : 0;
    scrollMessages();
    startSSE();
    startPoll();
  }
})();
