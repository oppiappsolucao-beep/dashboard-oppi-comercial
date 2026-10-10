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

  function bindServiceSelects(root) {
    if (!root) return;
    root.querySelectorAll("[data-service-select]").forEach(function (select) {
      if (select.dataset.bound === "1") return;
      select.dataset.bound = "1";
      select.addEventListener("change", function () {
        var option = select.options[select.selectedIndex];
        if (!option) return;
        var row = select.closest(".contracted-service-row, .client-closed-services-slide");
        if (!row) return;
        var valor = option.getAttribute("data-valor") || "";
        var quantidade = option.getAttribute("data-quantidade") || "";
        var forma = option.getAttribute("data-forma") || "";
        var valorInput = row.querySelector('input[name="closed_valor"]');
        var qtyInput = row.querySelector('input[name="closed_quantidade"]');
        var formaSelect = row.querySelector('select[name="closed_forma_pagamento"]');
        if (valorInput && valor) valorInput.value = valor;
        if (qtyInput && quantidade) qtyInput.value = quantidade;
        if (formaSelect && forma) formaSelect.value = forma;
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
    var isCarousel = Boolean(root.querySelector(".client-closed-services-viewport"));

    function slides() {
      if (!track) return [];
      return Array.prototype.slice.call(track.querySelectorAll(".client-closed-services-slide, .contracted-service-row"));
    }

    function total() {
      return slides().length;
    }

    function updateView() {
      var count = total();
      if (!count || !isCarousel) {
        bindServiceSelects(root);
        return;
      }
      if (index >= count) index = count - 1;
      if (index < 0) index = 0;
      track.style.transform = "translateX(-" + (index * 100) + "%)";
      if (counter) counter.textContent = (index + 1) + " / " + count;
      if (prevButton) prevButton.disabled = index <= 0;
      if (nextButton) nextButton.disabled = index >= count - 1;
      bindServiceSelects(root);
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

  function initCnpjLookup() {
    var input = document.querySelector('input[name="cnpj"]');
    var form = input && input.form;
    if (!input || !form || input.dataset.cnpjLookupBound === "1") return;
    input.dataset.cnpjLookupBound = "1";

    var status = document.createElement("p");
    status.className = "registration-filial-matriz-hint";
    status.setAttribute("data-cnpj-lookup-status", "");
    status.hidden = true;
    input.insertAdjacentElement("afterend", status);

    var timer = null;
    var requestSeq = 0;

    function digits(value) {
      return String(value || "").replace(/\D/g, "");
    }

    function setStatus(message, visible) {
      status.textContent = message || "";
      status.hidden = !visible;
    }

    function setIfEmpty(name, value) {
      if (!value) return false;
      var fields = form.querySelectorAll('[name="' + name + '"]');
      var applied = false;
      fields.forEach(function (field) {
        if (field.disabled) return;
        if (String(field.value || "").trim()) return;
        field.value = value;
        field.dispatchEvent(new Event("input", { bubbles: true }));
        applied = true;
      });
      return applied;
    }

    function selectNiche(niche) {
      var select = form.querySelector('select[name="nicho"]');
      if (!select || !niche || String(select.value || "").trim()) return false;
      var target = String(niche).trim().toLowerCase();
      for (var i = 0; i < select.options.length; i++) {
        var optionValue = String(select.options[i].value || "").trim().toLowerCase();
        if (optionValue && optionValue === target) {
          select.value = select.options[i].value;
          return true;
        }
      }
      return false;
    }

    function ensureOnePartner() {
      var count = form.querySelector("#partners-count");
      if (!count || String(count.value || "").trim()) return;
      count.value = "1";
      count.dispatchEvent(new Event("change", { bubbles: true }));
    }

    function fillAccess(email, password) {
      ensureOnePartner();
      if (email) {
        setIfEmpty("email", email);
        setIfEmpty("email_socio_1", email);
        setIfEmpty("email_login_gestor", email);
        setIfEmpty("email_confirmacao_admin", email);
        setIfEmpty("email_cobranca", email);
      }
      if (!password) return Boolean(email);
      form.querySelectorAll('input[name="senha_acesso"]').forEach(function (field) {
        if (field.disabled || String(field.value || "").trim()) return;
        field.type = "text";
        field.value = password;
      });
      return true;
    }

    function sellerName() {
      var field = form.querySelector('[name="nome_contato"]');
      return field ? String(field.value || "").trim() : "";
    }

    function applySellerResponsible() {
      var name = sellerName();
      var legal = form.querySelector('[name="responsavel_legal"]');
      if (!legal || legal.dataset.sellerEdited === "1" || !name) return;
      if (String(legal.value || "").trim() && legal.dataset.fromSeller !== "1") return;
      legal.dataset.fromSeller = "1";
      legal.value = name;
      var socio = form.querySelector('input[name="socio_1"]');
      if (socio && socio.type === "hidden" && !String(socio.value || "").trim()) {
        socio.value = name;
      }
    }

    function applyPayload(data) {
      [
        "empresa",
        "nome_fantasia",
        "data_abertura",
        "capital",
        "cep",
        "endereco",
        "endereco_numero",
        "endereco_complemento",
        "bairro",
        "municipio",
        "uf",
      ].forEach(function (name) {
        setIfEmpty(name, data[name] || "");
      });
      setIfEmpty("telefone_b2b", data.telefone || "");
      setIfEmpty("telefone_fixo", data.telefone_2 || "");
      var nicheApplied = selectNiche(data.nicho || "");
      applySellerResponsible();
      var parts = [];
      if (nicheApplied && data.nicho) {
        parts.push("Nicho " + data.nicho + " selecionado pelo CNAE.");
      } else if (data.nicho && form.querySelector('select[name="nicho"]') && form.querySelector('select[name="nicho"]').value) {
        parts.push("Nicho mantido.");
      }
      parts.push("Responsável, e-mails e senha ficam com o que você preencheu.");
      setStatus(parts.join(" ") || "Dados da empresa aplicados.", true);
    }

    function lookup() {
      var cnpj = digits(input.value);
      if (cnpj.length !== 14) {
        if (cnpj.length === 0) setStatus("", false);
        return;
      }
      if (input.dataset.lastCnpjLookup === cnpj) return;
      var seq = ++requestSeq;
      setStatus("Consultando CNPJ…", true);
      fetch("/cadastro/api/cnpj/" + encodeURIComponent(cnpj), { credentials: "same-origin" })
        .then(function (response) {
          return response.json().then(function (body) {
            return { ok: response.ok, body: body };
          });
        })
        .then(function (result) {
          if (seq !== requestSeq) return;
          if (!result.ok || !result.body || !result.body.ok) {
            input.dataset.lastCnpjLookup = "";
            setStatus((result.body && result.body.error) || "Não consegui consultar o CNPJ.", true);
            return;
          }
          input.dataset.lastCnpjLookup = cnpj;
          applyPayload(result.body);
        })
        .catch(function () {
          if (seq !== requestSeq) return;
          input.dataset.lastCnpjLookup = "";
          setStatus("Não consegui consultar o CNPJ agora.", true);
        });
    }

    input.addEventListener("input", function () {
      var cnpj = digits(input.value);
      if (input.dataset.lastCnpjLookup && input.dataset.lastCnpjLookup !== cnpj) {
        input.dataset.lastCnpjLookup = "";
      }
      clearTimeout(timer);
      timer = setTimeout(lookup, 450);
    });
    input.addEventListener("blur", function () {
      clearTimeout(timer);
      lookup();
    });

    var emailInput = form.querySelector('input[name="email"]');
    if (emailInput) {
      emailInput.addEventListener("change", function () {
        if (!form.dataset.generatedPassword) return;
        var email = String(emailInput.value || "").trim();
        if (!email || email.indexOf("@") === -1) return;
        if (fillAccess(email, form.dataset.generatedPassword)) {
          setStatus("1 responsável preenchido com o e-mail informado e a senha.", true);
        }
      });
    }

    var contato = form.querySelector('[name="nome_contato"]');
    var legal = form.querySelector('[name="responsavel_legal"]');
    if (legal) {
      legal.addEventListener("input", function (event) {
        if (event.isTrusted) legal.dataset.sellerEdited = "1";
      });
    }
    if (contato) contato.addEventListener("input", applySellerResponsible);
    applySellerResponsible();

    if (form.id === "registration-new-form" && digits(input.value).length === 14) {
      lookup();
    }
  }

  document.addEventListener("DOMContentLoaded", function () {
    var editForm = document.getElementById("client-edit-form");
    if (editForm) {
      editForm.addEventListener("submit", function (event) {
        if (editForm.dataset.saving === "1") {
          event.preventDefault();
          return;
        }
        editForm.dataset.saving = "1";
        var submitter = event.submitter;
        if (submitter && submitter.name && !editForm.querySelector("input[data-kept-action]")) {
          var kept = document.createElement("input");
          kept.type = "hidden";
          kept.name = submitter.name;
          kept.value = submitter.value;
          kept.setAttribute("data-kept-action", "1");
          editForm.appendChild(kept);
        }
      });
    }
    document.querySelectorAll(".registration-tipo-switch").forEach(initTipoSwitch);
    initClosedServices();
    initDeleteModal();
    initFilialMatriz();
    initCnpjLookup();
  });
})();
