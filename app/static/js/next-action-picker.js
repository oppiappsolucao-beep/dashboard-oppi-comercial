(function () {
  function parseOptions(picker) {
    try {
      return JSON.parse(picker.dataset.options || "[]");
    } catch (_error) {
      return [];
    }
  }

  function currentIndex(options, current) {
    var index = options.indexOf(current);
    return index >= 0 ? index : 0;
  }

  function setLabel(picker, value) {
    var label = picker.querySelector(".next-action-picker-label");
    var select = picker.querySelector(".next-action-picker-select");
    if (label) label.textContent = value;
    if (select) select.value = value;
    picker.dataset.current = value;
  }

  function setSaving(picker, saving) {
    picker.classList.toggle("is-saving", saving);
    picker.querySelectorAll("button, select").forEach(function (el) {
      el.disabled = saving;
    });
  }

  function renderTimeline(timeline) {
    var container = document.getElementById("activity-drawer-timeline");
    if (!container || !Array.isArray(timeline)) return;
    container.innerHTML = timeline.map(function (step) {
      var meta = step.meta
        ? '<span class="activity-drawer-timeline-meta">' + step.meta + "</span>"
        : "";
      return (
        '<div class="activity-drawer-timeline-step state-' + (step.state || "done") + '">' +
          '<span class="activity-drawer-timeline-dot" aria-hidden="true"></span>' +
          '<div class="activity-drawer-timeline-body">' +
            "<strong>" + step.label + "</strong>" +
            "<span>" + step.at + "</span>" +
            meta +
          "</div>" +
        "</div>"
      );
    }).join("");
  }

  function saveNextAction(picker, value) {
    var url = picker.dataset.saveUrl;
    if (!url || !value) return;

    setSaving(picker, true);
    var body = new URLSearchParams();
    body.set("next_action", value);

    fetch(url, {
      method: "POST",
      headers: {
        "Content-Type": "application/x-www-form-urlencoded",
        "Accept": "application/json",
      },
      body: body.toString(),
    })
      .then(function (response) {
        return response.json().then(function (data) {
          if (!response.ok) {
            throw new Error(data.error || "Não foi possível salvar a próxima ação.");
          }
          return data;
        });
      })
      .then(function (data) {
        if (data.next_action) {
          setLabel(picker, data.next_action);
        }
        if (data.timeline) {
          renderTimeline(data.timeline);
        }
      })
      .catch(function (error) {
        window.alert(error.message || "Não foi possível salvar a próxima ação.");
      })
      .finally(function () {
        setSaving(picker, false);
      });
  }

  function bindPicker(picker) {
    if (!picker || picker.dataset.bound === "1") return;
    picker.dataset.bound = "1";

    var options = parseOptions(picker);
    if (!options.length) return;

    var prevBtn = picker.querySelector(".next-action-picker-nav.prev");
    var nextBtn = picker.querySelector(".next-action-picker-nav.next");
    var pillBtn = picker.querySelector(".next-action-picker-pill");
    var select = picker.querySelector(".next-action-picker-select");

    function cycle(direction) {
      var index = currentIndex(options, picker.dataset.current || options[0]);
      index = (index + direction + options.length) % options.length;
      var value = options[index];
      setLabel(picker, value);
      saveNextAction(picker, value);
    }

    if (prevBtn) {
      prevBtn.addEventListener("click", function (event) {
        event.preventDefault();
        event.stopPropagation();
        cycle(-1);
      });
    }

    if (nextBtn) {
      nextBtn.addEventListener("click", function (event) {
        event.preventDefault();
        event.stopPropagation();
        cycle(1);
      });
    }

    if (pillBtn && select) {
      pillBtn.addEventListener("click", function (event) {
        event.preventDefault();
        event.stopPropagation();
        select.focus();
        select.click();
      });
    }

    if (select) {
      select.addEventListener("change", function () {
        var value = select.value;
        setLabel(picker, value);
        saveNextAction(picker, value);
      });
    }
  }

  function initPickers(root) {
    (root || document).querySelectorAll(".next-action-picker").forEach(bindPicker);
  }

  document.addEventListener("DOMContentLoaded", function () {
    initPickers(document);
  });

  document.body.addEventListener("htmx:afterSwap", function (event) {
    if (!event.detail || !event.detail.target) return;
    initPickers(event.detail.target);
  });

  window.initNextActionPickers = initPickers;
})();
