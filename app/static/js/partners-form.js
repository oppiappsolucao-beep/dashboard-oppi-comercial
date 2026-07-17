(function () {
  function parseCount(value) {
    const count = parseInt(String(value || ""), 10);
    return Number.isFinite(count) && count > 0 ? count : 0;
  }

  function updatePartnersForm(container, clearHidden) {
    const select = container.querySelector("#partners-count");
    if (!select) {
      return;
    }

    const count = parseCount(select.value);
    container.querySelectorAll(".partner-block").forEach((block) => {
      const partnerIndex = parseInt(block.dataset.partner || "0", 10);
      const visible = count >= partnerIndex;
      block.classList.toggle("is-hidden", !visible);
      block.querySelectorAll("input, select, textarea").forEach((field) => {
        field.disabled = !visible;
        if (!visible && clearHidden) {
          field.value = "";
        }
      });
    });
  }

  function initPartnersForm(container) {
    const select = container.querySelector("#partners-count");
    if (!select) {
      return;
    }

    const initialCount = parseCount(container.dataset.initialCount);
    if (initialCount) {
      select.value = String(initialCount);
    }

    updatePartnersForm(container, false);

    select.addEventListener("change", () => {
      updatePartnersForm(container, true);
    });
  }

  document.addEventListener("DOMContentLoaded", () => {
    document.querySelectorAll(".partners-form").forEach(initPartnersForm);
  });
})();
