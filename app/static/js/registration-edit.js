(function () {
  function initTipoSwitch(root) {
    if (!root) return;
    root.querySelectorAll(".registration-tipo-option input").forEach(function (input) {
      input.addEventListener("change", function () {
        root.querySelectorAll(".registration-tipo-option").forEach(function (option) {
          option.classList.toggle("is-active", option.contains(input) && input.checked);
        });
        document.dispatchEvent(new CustomEvent("registration-tipo-changed", {
          detail: { value: input.value },
        }));

        var form = input.form;
        var action = form && form.action;
        if (action && action.indexOf("/editar") !== -1) {
          var tipoForm = document.createElement("form");
          tipoForm.method = "post";
          tipoForm.action = action.replace("/editar", "/tipo");
          var field = document.createElement("input");
          field.type = "hidden";
          field.name = "cadastro_tipo";
          field.value = input.value;
          tipoForm.appendChild(field);
          document.body.appendChild(tipoForm);
          tipoForm.submit();
        }
      });
    });
  }

  function initClosedServices() {
    var root = document.getElementById("client-closed-services");
    if (!root) return;

    var track = document.getElementById("closed-services-track");
    var counter = document.getElementById("closed-services-counter");
    var addButton = document.getElementById("closed-services-add");
    var template = document.getElementById("closed-services-slide-template");
    var prevButton = root.querySelector(".client-closed-services-nav.prev");
    var nextButton = root.querySelector(".client-closed-services-nav.next");
    var index = 0;

    function slides() {
      return track ? Array.prototype.slice.call(track.querySelectorAll(".client-closed-services-slide")) : [];
    }

    function total() {
      return slides().length;
    }

    function updateView() {
      var count = total();
      if (!count) return;
      if (index >= count) index = count - 1;
      if (index < 0) index = 0;
      track.style.transform = "translateX(-" + (index * 100) + "%)";
      if (counter) counter.textContent = (index + 1) + " / " + count;
      if (prevButton) prevButton.disabled = index <= 0;
      if (nextButton) nextButton.disabled = index >= count - 1;
    }

    if (prevButton) {
      prevButton.addEventListener("click", function () {
        if (index > 0) {
          index -= 1;
          updateView();
        }
      });
    }

    if (nextButton) {
      nextButton.addEventListener("click", function () {
        if (index < total() - 1) {
          index += 1;
          updateView();
        }
      });
    }

    if (addButton && template && track) {
      addButton.addEventListener("click", function () {
        var clone = template.content.firstElementChild.cloneNode(true);
        track.appendChild(clone);
        index = total() - 1;
        updateView();
      });
    }

    updateView();
  }

  function initDeleteModal() {
    var modal = document.getElementById("client-delete-modal");
    if (!modal) return;

    var input = document.getElementById("client-delete-confirm-input");
    var submit = document.getElementById("client-delete-submit");
    var openButtons = document.querySelectorAll("#open-delete-modal, .client-edit-delete-btn");

    function closeModal() {
      modal.hidden = true;
      modal.setAttribute("aria-hidden", "true");
      if (input) input.value = "";
      if (submit) submit.disabled = true;
    }

    function openModal() {
      modal.hidden = false;
      modal.setAttribute("aria-hidden", "false");
      if (input) {
        input.value = "";
        input.focus();
      }
      if (submit) submit.disabled = true;
    }

    openButtons.forEach(function (button) {
      button.addEventListener("click", openModal);
    });

    modal.querySelectorAll("[data-close-delete-modal]").forEach(function (element) {
      element.addEventListener("click", closeModal);
    });

    if (input && submit) {
      input.addEventListener("input", function () {
        submit.disabled = input.value.trim().toLowerCase() !== "excluir";
      });
    }

    document.addEventListener("keydown", function (event) {
      if (event.key === "Escape" && !modal.hidden) {
        closeModal();
      }
    });
  }

  function initFilialMatriz() {
    var toggle = document.querySelector("[data-filial-toggle]");
    var block = document.querySelector("[data-filial-matriz-block]");
    var searchInput = document.querySelector("[data-filial-matriz-search]");
    var hiddenRow = document.querySelector("[data-filial-matriz-row]");
    var resultsBox = document.querySelector("[data-filial-matriz-results]");
    var selectedHint = document.querySelector("[data-filial-matriz-selected]");
    if (!toggle || !block || !searchInput || !hiddenRow || !resultsBox) return;

    var searchTimer = null;

    function setSelected(sheetRow, name) {
      hiddenRow.value = sheetRow ? String(sheetRow) : "";
      if (name) searchInput.value = name;
      if (selectedHint) {
        if (name) {
          selectedHint.textContent = "Selecionada: " + name;
          selectedHint.hidden = false;
        } else {
          selectedHint.textContent = "";
          selectedHint.hidden = true;
        }
      }
    }

    function clearSelected() {
      setSelected("", "");
    }

    function syncBlock() {
      var enabled = !!toggle.checked;
      block.hidden = !enabled;
      if (!enabled) {
        clearSelected();
        resultsBox.hidden = true;
        resultsBox.innerHTML = "";
      }
    }

    function renderResults(items) {
      resultsBox.innerHTML = "";
      if (!items || !items.length) {
        resultsBox.hidden = true;
        return;
      }
      items.forEach(function (item) {
        var button = document.createElement("button");
        button.type = "button";
        button.className = "registration-filial-matriz-item";
        button.textContent = item.empresa || ("Empresa #" + item.sheet_row);
        if (item.cnpj) {
          var meta = document.createElement("small");
          meta.textContent = item.cnpj;
          button.appendChild(meta);
        }
        button.addEventListener("click", function () {
          setSelected(item.sheet_row, item.empresa || "");
          resultsBox.hidden = true;
          resultsBox.innerHTML = "";
        });
        resultsBox.appendChild(button);
      });
      resultsBox.hidden = false;
    }

    async function searchMatriz(query) {
      var params = new URLSearchParams();
      params.set("q", query || "");
      var exclude = searchInput.getAttribute("data-exclude-sheet-row");
      if (exclude) params.set("exclude", exclude);
      var response = await fetch("/cadastro/api/empresas-matriz?" + params.toString());
      var data = await response.json();
      return data.items || [];
    }

    toggle.addEventListener("change", syncBlock);
    syncBlock();

    searchInput.addEventListener("input", function () {
      hiddenRow.value = "";
      if (selectedHint) {
        selectedHint.hidden = true;
        selectedHint.textContent = "";
      }
      clearTimeout(searchTimer);
      var query = searchInput.value.trim();
      searchTimer = setTimeout(function () {
        if (!toggle.checked) return;
        searchMatriz(query).then(renderResults).catch(function () {
          resultsBox.hidden = true;
        });
      }, 250);
    });

    searchInput.addEventListener("focus", function () {
      if (!toggle.checked) return;
      searchMatriz(searchInput.value.trim()).then(renderResults).catch(function () {
        resultsBox.hidden = true;
      });
    });

    document.addEventListener("click", function (event) {
      if (block.contains(event.target)) return;
      resultsBox.hidden = true;
    });
  }

  document.addEventListener("DOMContentLoaded", function () {
    document.querySelectorAll(".registration-tipo-switch").forEach(initTipoSwitch);
    initClosedServices();
    initDeleteModal();
    initFilialMatriz();
  });
})();
