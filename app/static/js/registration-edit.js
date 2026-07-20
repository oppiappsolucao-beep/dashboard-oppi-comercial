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

  document.addEventListener("DOMContentLoaded", function () {
    document.querySelectorAll(".registration-tipo-switch").forEach(initTipoSwitch);
    initClosedServices();
  });
})();
