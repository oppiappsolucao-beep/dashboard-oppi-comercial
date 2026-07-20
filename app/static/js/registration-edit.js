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

  document.addEventListener("DOMContentLoaded", function () {
    document.querySelectorAll(".registration-tipo-switch").forEach(initTipoSwitch);
  });
})();
