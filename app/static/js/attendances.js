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
        status: ($("#att-status") || {}).value || "abertos",
        sector: ($("#att-sector") || {}).value || "todos",
        line: ($("#att-line") || {}).value || "",
        conversation_id: opts.clearSelection ? "" : selectedId(),
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
    var status = ($("#att-status") || {}).value || "abertos";
    var sector = ($("#att-sector") || {}).value || "todos";
    var line = ($("#att-line") || {}).value || "";
    window.htmx.ajax(
      "GET",
      "/atendimentos/conversa/" + encodeURIComponent(id) +
        "?search=" + encodeURIComponent(search) +
        "&status=" + encodeURIComponent(status) +
        "&sector=" + encodeURIComponent(sector) +
        "&line=" + encodeURIComponent(line) +
        "&soft=1",
      { target: "#att-chat-root", swap: "innerHTML" }
    );
  }

  function switchWhatsappLine(lineId) {
    if (!lineId) return;
    var input = $("#att-line");
    var shell = $("#att-shell");
    if (input) input.value = lineId;
    if (shell) {
      shell.setAttribute("data-line", lineId);
      shell.setAttribute("data-selected", "");
      shell.classList.remove("att-shell--chat-open");
    }
    document.querySelectorAll(".att-line-pill").forEach(function (btn) {
      var active = btn.getAttribute("data-line") === lineId;
      btn.classList.toggle("active", active);
      btn.setAttribute("aria-selected", active ? "true" : "false");
    });
    var chat = $("#att-chat-root");
    if (chat) {
      chat.innerHTML =
        '<div class="att-empty-state"><div class="att-empty-icon">💬</div>' +
        "<h2>Selecione uma conversa</h2>" +
        "<p>Mensagens desta linha WhatsApp aparecem aqui.</p></div>";
    }
    var hiddenConv = document.querySelector('.att-filters input[name="conversation_id"]');
    if (hiddenConv) hiddenConv.remove();
    try {
      var url = new URL(window.location.href);
      url.searchParams.set("line", lineId);
      url.searchParams.delete("c");
      window.history.replaceState({}, "", url.toString());
    } catch (e) {}
    refreshList({ clearSelection: true });
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

  function composerForm(el) {
    return el && el.closest ? el.closest(".att-composer") : null;
  }

  function composerInput(form) {
    return form ? form.querySelector(".att-composer-input") : null;
  }

  function textForSend(form) {
    var el = composerInput(form);
    return el ? String(el.value || "") : "";
  }

  function clearComposer(form) {
    var el = composerInput(form);
    if (!el) return;
    el.value = "";
    autoGrow(el);
  }

  function bindComposer(root) {
    var scope = root || document;
    scope.querySelectorAll("textarea.att-composer-input").forEach(function (el) {
      if (el.dataset.attGuard === "1") return;
      el.dataset.attGuard = "1";
      autoGrow(el);
      el.addEventListener("input", function () {
        autoGrow(el);
      });
      el.addEventListener("keydown", function (ev) {
        if (ev.key !== "Enter" || ev.shiftKey) return;
        // No mobile, Enter do teclado virtual costuma ser "enviar"
        if (window.matchMedia && window.matchMedia("(pointer: coarse)").matches) {
          ev.preventDefault();
          submitComposer(composerForm(el));
          return;
        }
        if (!ev.shiftKey) {
          ev.preventDefault();
          submitComposer(composerForm(el));
        }
      });
    });
  }

  bindComposer(document);

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
      if (ev.target.id === "att-chat-root") {
        ev.target.querySelectorAll("#att-conversation-list, .att-conversation-list").forEach(function (el) {
          el.remove();
        });
      }
      scrollMessages();
      bindComposer(ev.target);
      bindCrmSheet(ev.target);
      setCrmSheetOpen(false);
      unlockComposer($(".att-composer"));
      autoGrow($(".att-composer-input"));
      syncComposerActions($(".att-composer"));
      loadQuickRepliesFromDom();
      var thread = $("[data-conversation-id]", $("#att-chat-root"));
      var shell = $("#att-shell");
      if (shell && thread) {
        shell.setAttribute("data-selected", thread.getAttribute("data-conversation-id") || "");
        shell.classList.add("att-shell--chat-open");
      } else if (shell) {
        shell.setAttribute("data-selected", "");
        shell.classList.remove("att-shell--chat-open");
      }
      lastConversationToken = "";
    }
  });

  document.body.addEventListener("att-conversation-deleted", function () {
    var shell = $("#att-shell");
    if (shell) {
      shell.setAttribute("data-selected", "");
      shell.classList.remove("att-shell--chat-open");
    }
    refreshList({ clearSelection: true });
  });

  // Excluir conversa: confirm + fetch (não depende do HTMX ajax)
  document.body.addEventListener("click", function (ev) {
    var btn = ev.target && ev.target.closest ? ev.target.closest(".att-delete-conv-btn") : null;
    if (!btn) return;
    ev.preventDefault();
    ev.stopPropagation();
    var url = btn.getAttribute("data-delete-url") || "";
    if (!url) return;
    if (
      !window.confirm(
        "Excluir esta conversa do Atendimentos?\n\nRemove só o chat e as mensagens.\nO cadastro no CRM NÃO será apagado."
      )
    ) {
      return;
    }
    btn.disabled = true;
    fetch(url, {
      method: "POST",
      credentials: "same-origin",
      headers: {
        "HX-Request": "true",
        Accept: "text/html",
      },
      cache: "no-store",
    })
      .then(function (r) {
        return r.text().then(function (html) {
          return { ok: r.ok, status: r.status, html: html };
        });
      })
      .then(function (res) {
        var root = $("#att-chat-root");
        if (root && res.html) {
          root.innerHTML = res.html;
          if (window.htmx && window.htmx.process) {
            window.htmx.process(root);
          }
        }
        var shell = $("#att-shell");
        if (shell) {
          shell.setAttribute("data-selected", "");
          shell.classList.remove("att-shell--chat-open");
        }
        lastInboxToken = "";
        lastConversationToken = "";
        refreshList({ clearSelection: true });
        if (!res.ok) {
          window.alert("Não foi possível excluir a conversa (HTTP " + res.status + ").");
        }
      })
      .catch(function () {
        window.alert("Falha de rede ao excluir a conversa. Tente de novo.");
        btn.disabled = false;
      });
  });

  document.body.addEventListener("htmx:afterRequest", function (ev) {
    var path = (ev.detail && ev.detail.pathInfo && ev.detail.pathInfo.requestPath) || "";
    if (path.indexOf("/atendimentos/conversa/") === -1) return;
    if (
      path.indexOf("/enviar") === -1
      && path.indexOf("/midia") === -1
      && path.indexOf("/voz") === -1
      && path.indexOf("/atalho") === -1
      && path.indexOf("/excluir") === -1
      && path.indexOf("/finalizar") === -1
    ) {
      return;
    }
    if (ev.detail && ev.detail.successful === false) return;
    var id = selectedId();
    if (path.indexOf("/excluir") !== -1) {
      var shell = $("#att-shell");
      if (shell) shell.setAttribute("data-selected", "");
      refreshList({});
      lastInboxToken = "";
      lastConversationToken = "";
      return;
    }
    if (id) {
      bumpConversationToTop(id);
      refreshList({ bumpId: id });
      lastInboxToken = "";
      lastConversationToken = "";
    }
  });

  function unlockComposer(form) {
    if (!form) {
      form = $(".att-composer");
    }
    if (!form) return;
    form.dataset.attSending = "";
    form.dataset.attPendingText = "";
    var btn = form.querySelector(".att-send-btn");
    if (btn) btn.disabled = false;
  }

  function submitComposer(form) {
    if (!form) return;
    if (form.dataset.attSending === "1") return;
    var value = String(textForSend(form) || "").trim();
    if (!value) return;
    form.dataset.attSending = "1";
    form.dataset.attPendingText = value;
    clearComposer(form);
    var btn = form.querySelector(".att-send-btn");
    if (btn) btn.disabled = true;
    // Trava no máx. 12s — se a resposta falhar, o chat não fica morto
    window.setTimeout(function () {
      unlockComposer(form);
    }, 12000);
    if (window.htmx) {
      window.htmx.trigger(form, "submit");
    } else {
      unlockComposer(form);
    }
  }

  document.addEventListener(
    "click",
    function (ev) {
      var btn = ev.target && ev.target.closest ? ev.target.closest(".att-send-btn") : null;
      if (!btn || btn.disabled) return;
      ev.preventDefault();
      ev.stopPropagation();
      submitComposer(btn.closest("form"));
    },
    true
  );

  document.body.addEventListener("htmx:configRequest", function (ev) {
    var form = ev.target && ev.target.closest ? ev.target.closest(".att-composer") : null;
    if (!form) return;
    var path = (ev.detail && ev.detail.path) || "";
    if (path.indexOf("/enviar") === -1) return;
    var value = form.dataset.attPendingText || textForSend(form);
    if (ev.detail && ev.detail.parameters) {
      ev.detail.parameters.text = value;
    }
  });

  document.body.addEventListener("htmx:afterRequest", function (ev) {
    var path = (ev.detail && ev.detail.pathInfo && ev.detail.pathInfo.requestPath) || "";
    if (path.indexOf("/enviar") !== -1) {
      unlockComposer(ev.target && ev.target.closest ? ev.target.closest(".att-composer") : null);
    }
  });

  document.body.addEventListener("htmx:sendError", function () {
    unlockComposer(null);
  });

  document.body.addEventListener("htmx:responseError", function () {
    unlockComposer(null);
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
      .then(function (html) { applyChatHtml(html, id); })
      .catch(function () {});
    input.value = "";
  });
  function setCrmSheetOpen(open) {
    var panel = $("#att-crm-panel");
    var backdrop = $("#att-crm-backdrop");
    var toggle = $("#att-crm-toggle");
    if (panel) panel.classList.toggle("is-open", !!open);
    if (backdrop) {
      backdrop.classList.toggle("is-open", !!open);
      if (open) backdrop.removeAttribute("hidden");
      else backdrop.setAttribute("hidden", "hidden");
    }
    if (toggle) toggle.setAttribute("aria-expanded", open ? "true" : "false");
    document.body.classList.toggle("att-crm-sheet-open", !!open);
  }

  function bindCrmSheet(root) {
    var scope = root || document;
    var toggle = $("#att-crm-toggle", scope) || $("#att-crm-toggle");
    var closeBtn = $("#att-crm-close", scope) || $("#att-crm-close");
    var backdrop = $("#att-crm-backdrop", scope) || $("#att-crm-backdrop");
    if (toggle && toggle.dataset.attCrmBound !== "1") {
      toggle.dataset.attCrmBound = "1";
      toggle.addEventListener("click", function () {
        var panel = $("#att-crm-panel");
        setCrmSheetOpen(!(panel && panel.classList.contains("is-open")));
      });
    }
    if (closeBtn && closeBtn.dataset.attCrmBound !== "1") {
      closeBtn.dataset.attCrmBound = "1";
      closeBtn.addEventListener("click", function () {
        setCrmSheetOpen(false);
      });
    }
    if (backdrop && backdrop.dataset.attCrmBound !== "1") {
      backdrop.dataset.attCrmBound = "1";
      backdrop.addEventListener("click", function () {
        setCrmSheetOpen(false);
      });
    }
  }

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
    bindCrmSheet(document);
    startPoll();
  }

  var newCallToggle = $("#att-new-call-toggle");
  var newCallPanel = $("#att-new-call-panel");
  var newCallCancel = $("#att-new-call-cancel");
  if (newCallToggle && newCallPanel) {
    newCallToggle.addEventListener("click", function () {
      var open = newCallPanel.hasAttribute("hidden");
      if (open) newCallPanel.removeAttribute("hidden");
      else newCallPanel.setAttribute("hidden", "hidden");
    });
  }
  if (newCallCancel && newCallPanel) {
    newCallCancel.addEventListener("click", function () {
      newCallPanel.setAttribute("hidden", "hidden");
    });
  }

  var lineSwitcher = $("#att-line-switcher");
  if (lineSwitcher) {
    lineSwitcher.addEventListener("click", function (ev) {
      var btn = ev.target && ev.target.closest ? ev.target.closest(".att-line-pill") : null;
      if (!btn) return;
      var lineId = btn.getAttribute("data-line") || "";
      if (!lineId || btn.classList.contains("active")) return;
      switchWhatsappLine(lineId);
    });
  }

  /* —— Gravação de voz (não altera o fluxo de texto) —— */
  var voiceState = {
    recorder: null,
    chunks: [],
    stream: null,
    timer: null,
    startedAt: 0,
    sending: false,
  };

  function formatVoiceTime(ms) {
    var total = Math.max(0, Math.floor(ms / 1000));
    var m = Math.floor(total / 60);
    var s = total % 60;
    return m + ":" + (s < 10 ? "0" : "") + s;
  }

  function pickAudioMime() {
    var types = [
      "audio/ogg;codecs=opus",
      "audio/webm;codecs=opus",
      "audio/webm",
      "audio/mp4",
    ];
    if (!window.MediaRecorder || !MediaRecorder.isTypeSupported) return "";
    for (var i = 0; i < types.length; i++) {
      if (MediaRecorder.isTypeSupported(types[i])) return types[i];
    }
    return "";
  }

  function voiceExt(mime) {
    var m = String(mime || "").toLowerCase();
    if (m.indexOf("ogg") !== -1) return "ogg";
    if (m.indexOf("mp4") !== -1 || m.indexOf("m4a") !== -1 || m.indexOf("aac") !== -1) return "m4a";
    if (m.indexOf("mpeg") !== -1 || m.indexOf("mp3") !== -1) return "mp3";
    return "webm";
  }

  function syncComposerActions(form) {
    if (!form) form = $(".att-composer");
    if (!form) return;
    var mic = form.querySelector(".att-mic-btn");
    var send = form.querySelector(".att-send-btn");
    var recording = form.classList.contains("is-recording");
    var hasText = String(textForSend(form) || "").trim().length > 0;
    if (mic) mic.classList.toggle("is-hidden", recording || hasText);
    if (send) send.classList.toggle("is-hidden", recording || !hasText);
  }

  function setVoiceUi(form, recording) {
    if (!form) return;
    form.classList.toggle("is-recording", !!recording);
    var bar = form.querySelector("#att-voice-bar") || form.querySelector(".att-voice-bar");
    if (bar) {
      if (recording) bar.removeAttribute("hidden");
      else bar.setAttribute("hidden", "hidden");
    }
    syncComposerActions(form);
  }

  function stopVoiceTracks() {
    if (voiceState.stream) {
      voiceState.stream.getTracks().forEach(function (t) {
        try { t.stop(); } catch (e) { /* ignore */ }
      });
    }
    voiceState.stream = null;
  }

  function clearVoiceTimer() {
    if (voiceState.timer) {
      clearInterval(voiceState.timer);
      voiceState.timer = null;
    }
  }

  function abortVoice(form) {
    clearVoiceTimer();
    try {
      if (voiceState.recorder && voiceState.recorder.state !== "inactive") {
        voiceState.recorder.onstop = null;
        voiceState.recorder.stop();
      }
    } catch (e) { /* ignore */ }
    voiceState.recorder = null;
    voiceState.chunks = [];
    stopVoiceTracks();
    setVoiceUi(form || $(".att-composer"), false);
  }

  function uploadVoiceBlob(blob, mime) {
    var id = selectedId();
    if (!id || !blob) return;
    if (voiceState.sending) return;
    voiceState.sending = true;
    var fd = new FormData();
    var name = "audio." + voiceExt(mime);
    fd.append("file", blob, name);
    fetch("/atendimentos/conversa/" + encodeURIComponent(id) + "/voz", {
      method: "POST",
      body: fd,
      credentials: "same-origin",
      headers: { "HX-Request": "true" },
    })
      .then(function (r) { return r.text(); })
      .then(function (html) { applyChatHtml(html, id); })
      .catch(function () {})
      .finally(function () {
        voiceState.sending = false;
      });
  }

  function finishVoiceAndSend(form) {
    clearVoiceTimer();
    var recorder = voiceState.recorder;
    if (!recorder) {
      setVoiceUi(form, false);
      return;
    }
    recorder.onstop = function () {
      var mime = recorder.mimeType || pickAudioMime() || "audio/webm";
      var blob = new Blob(voiceState.chunks, { type: mime });
      voiceState.chunks = [];
      voiceState.recorder = null;
      stopVoiceTracks();
      setVoiceUi(form, false);
      if (blob.size < 200) return;
      uploadVoiceBlob(blob, mime);
    };
    try {
      if (recorder.state !== "inactive") recorder.stop();
      else recorder.onstop();
    } catch (e) {
      abortVoice(form);
    }
  }

  function startVoice(form) {
    if (!form || form.dataset.attSending === "1" || voiceState.sending) return;
    if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
      window.alert("Seu navegador não permite gravação de áudio.");
      return;
    }
    if (!window.MediaRecorder) {
      window.alert("Seu navegador não suporta gravação de áudio.");
      return;
    }
    abortVoice(form);
    navigator.mediaDevices.getUserMedia({ audio: true }).then(function (stream) {
      voiceState.stream = stream;
      voiceState.chunks = [];
      var mime = pickAudioMime();
      var options = mime ? { mimeType: mime } : undefined;
      var recorder;
      try {
        recorder = options ? new MediaRecorder(stream, options) : new MediaRecorder(stream);
      } catch (e) {
        stopVoiceTracks();
        window.alert("Não foi possível iniciar a gravação.");
        return;
      }
      voiceState.recorder = recorder;
      recorder.ondataavailable = function (ev) {
        if (ev.data && ev.data.size > 0) voiceState.chunks.push(ev.data);
      };
      recorder.start(250);
      voiceState.startedAt = Date.now();
      setVoiceUi(form, true);
      var timerEl = form.querySelector("#att-voice-timer") || form.querySelector(".att-voice-timer");
      if (timerEl) timerEl.textContent = "0:00";
      clearVoiceTimer();
      voiceState.timer = setInterval(function () {
        var elapsed = Date.now() - voiceState.startedAt;
        if (timerEl) timerEl.textContent = formatVoiceTime(elapsed);
        if (elapsed >= 5 * 60 * 1000) finishVoiceAndSend(form);
      }, 250);
    }).catch(function () {
      window.alert("Permita o acesso ao microfone para gravar áudio.");
    });
  }

  document.addEventListener(
    "click",
    function (ev) {
      var target = ev.target && ev.target.closest ? ev.target.closest(".att-mic-btn, .att-voice-cancel, .att-voice-send") : null;
      if (!target) return;
      var form = target.closest(".att-composer");
      if (!form) return;
      ev.preventDefault();
      ev.stopPropagation();
      if (target.classList.contains("att-mic-btn")) {
        if (target.disabled) return;
        startVoice(form);
        return;
      }
      if (target.classList.contains("att-voice-cancel")) {
        abortVoice(form);
        return;
      }
      if (target.classList.contains("att-voice-send")) {
        finishVoiceAndSend(form);
      }
    },
    true
  );

  /* —— Mensagens rápidas (/atalho) —— */
  var quickReplies = [];
  var slashActiveIndex = 0;

  function loadQuickRepliesFromDom() {
    var el = $("#att-quick-replies-data");
    if (!el) return;
    try {
      var parsed = JSON.parse(el.textContent || "[]");
      quickReplies = Array.isArray(parsed) ? parsed : [];
    } catch (e) {
      quickReplies = [];
    }
  }

  function slashQuery(text) {
    var value = String(text || "");
    var match = value.match(/(?:^|\s)\/([a-zA-Z0-9_-]*)$/);
    if (!match) return null;
    return String(match[1] || "").toLowerCase();
  }

  function filterQuickReplies(query) {
    var q = String(query || "").toLowerCase();
    return quickReplies.filter(function (item) {
      var shortcut = String(item.shortcut || "").toLowerCase();
      var title = String(item.title || "").toLowerCase();
      if (!q) return true;
      return shortcut.indexOf(q) === 0 || title.indexOf(q) !== -1;
    }).slice(0, 8);
  }

  function hideSlashMenu(form) {
    var menu = (form && form.querySelector("#att-slash-menu")) || $("#att-slash-menu");
    if (menu) {
      menu.innerHTML = "";
      menu.setAttribute("hidden", "hidden");
    }
    slashActiveIndex = 0;
  }

  function renderSlashMenu(form, items) {
    var menu = form.querySelector("#att-slash-menu") || form.querySelector(".att-slash-menu");
    if (!menu) return;
    if (!items || !items.length) {
      hideSlashMenu(form);
      return;
    }
    menu.innerHTML = items.map(function (item, index) {
      var cmd = item.command || ("/" + item.shortcut);
      var meta = (item.media_type_label || item.media_type || "") + (item.preview ? " · " + item.preview : "");
      return (
        '<button type="button" class="att-slash-item' + (index === slashActiveIndex ? " is-active" : "") + '" data-shortcut="' +
        String(item.shortcut || "").replace(/"/g, "") +
        '"><span class="att-slash-cmd">' + cmd +
        (item.title && item.title !== item.shortcut ? " — " + item.title : "") +
        '</span><span class="att-slash-meta">' + meta + "</span></button>"
      );
    }).join("");
    menu.removeAttribute("hidden");
  }

  function updateSlashMenu(form) {
    if (!form) return;
    var query = slashQuery(textForSend(form));
    if (query === null) {
      hideSlashMenu(form);
      return;
    }
    var items = filterQuickReplies(query);
    if (slashActiveIndex >= items.length) slashActiveIndex = Math.max(0, items.length - 1);
    renderSlashMenu(form, items);
  }

  function stripConversationListFromChatHtml(html) {
    // Evita lista de conversas “grudar” abaixo do composer quando a resposta
    // traz OOB / fragmento de inbox (fetch+innerHTML não processa hx-swap-oob).
    try {
      var wrap = document.createElement("div");
      wrap.innerHTML = String(html || "");
      wrap.querySelectorAll("#att-conversation-list, .att-conversation-list").forEach(function (el) {
        el.remove();
      });
      return wrap.innerHTML;
    } catch (e) {
      return String(html || "");
    }
  }

  function applyChatHtml(html, id) {
    var root = $("#att-chat-root");
    if (root) {
      root.innerHTML = stripConversationListFromChatHtml(html);
      if (window.htmx) window.htmx.process(root);
    }
    scrollMessages();
    bindComposer(root || document);
    bindCrmSheet(root || document);
    setCrmSheetOpen(false);
    syncComposerActions($(".att-composer"));
    loadQuickRepliesFromDom();
    if (id) {
      refreshList({ bumpId: id });
      lastInboxToken = "";
      lastConversationToken = "";
    }
  }

  function sendQuickReply(shortcut) {
    var id = selectedId();
    var key = String(shortcut || "").replace(/^\//, "").trim().toLowerCase();
    if (!id || !key) return;
    var form = $(".att-composer");
    if (form) {
      clearComposer(form);
      hideSlashMenu(form);
      syncComposerActions(form);
    }
    var fd = new FormData();
    fd.append("shortcut", key);
    fetch("/atendimentos/conversa/" + encodeURIComponent(id) + "/atalho", {
      method: "POST",
      body: fd,
      credentials: "same-origin",
      headers: { "HX-Request": "true" },
    })
      .then(function (r) { return r.text(); })
      .then(function (html) { applyChatHtml(html, id); })
      .catch(function () {});
  }

  function exactQuickReply(text) {
    var value = String(text || "").trim();
    var match = value.match(/^\/([a-zA-Z0-9_-]+)$/);
    if (!match) return null;
    var key = String(match[1] || "").toLowerCase();
    for (var i = 0; i < quickReplies.length; i++) {
      if (String(quickReplies[i].shortcut || "").toLowerCase() === key) return quickReplies[i];
    }
    return null;
  }

  document.body.addEventListener("input", function (ev) {
    var el = ev.target;
    if (!el || !el.classList || !el.classList.contains("att-composer-input")) return;
    var form = composerForm(el);
    syncComposerActions(form);
    updateSlashMenu(form);
  });

  document.addEventListener(
    "keydown",
    function (ev) {
      var el = ev.target;
      if (!el || !el.classList || !el.classList.contains("att-composer-input")) return;
      var form = composerForm(el);
      if (!form) return;
      var menu = form.querySelector("#att-slash-menu");
      var menuOpen = menu && !menu.hasAttribute("hidden");
      var items = menuOpen ? menu.querySelectorAll(".att-slash-item") : [];

      if (menuOpen && items.length) {
        if (ev.key === "ArrowDown") {
          ev.preventDefault();
          ev.stopPropagation();
          slashActiveIndex = (slashActiveIndex + 1) % items.length;
          updateSlashMenu(form);
          return;
        }
        if (ev.key === "ArrowUp") {
          ev.preventDefault();
          ev.stopPropagation();
          slashActiveIndex = (slashActiveIndex - 1 + items.length) % items.length;
          updateSlashMenu(form);
          return;
        }
        if (ev.key === "Escape") {
          ev.preventDefault();
          ev.stopPropagation();
          hideSlashMenu(form);
          return;
        }
        if (ev.key === "Tab" || (ev.key === "Enter" && !ev.shiftKey)) {
          var active = items[slashActiveIndex] || items[0];
          if (active) {
            ev.preventDefault();
            ev.stopImmediatePropagation();
            sendQuickReply(active.getAttribute("data-shortcut") || "");
            return;
          }
        }
      }

      if (ev.key === "Enter" && !ev.shiftKey) {
        var hit = exactQuickReply(textForSend(form));
        if (hit) {
          ev.preventDefault();
          ev.stopImmediatePropagation();
          sendQuickReply(hit.shortcut);
        }
      }
    },
    true
  );

  document.addEventListener(
    "click",
    function (ev) {
      var item = ev.target && ev.target.closest ? ev.target.closest(".att-slash-item") : null;
      if (item) {
        ev.preventDefault();
        ev.stopPropagation();
        sendQuickReply(item.getAttribute("data-shortcut") || "");
        return;
      }
      if (!ev.target || !ev.target.closest || !ev.target.closest(".att-composer-main")) {
        hideSlashMenu($(".att-composer"));
      }
    },
    true
  );

  loadQuickRepliesFromDom();
  syncComposerActions($(".att-composer"));
})();
