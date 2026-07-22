function copyPhone(button, phone) {
  if (!phone) {
    button.textContent = "Sem número";
    setTimeout(() => { button.textContent = "Copiar"; }, 1400);
    return;
  }
  navigator.clipboard.writeText(phone).then(() => {
    button.textContent = "Copiado!";
    button.classList.add("copied");
    setTimeout(() => {
      button.textContent = "Copiar";
      button.classList.remove("copied");
    }, 1400);
  }).catch(() => {
    const textarea = document.createElement("textarea");
    textarea.value = phone;
    document.body.appendChild(textarea);
    textarea.select();
    document.execCommand("copy");
    document.body.removeChild(textarea);
    button.textContent = "Copiado!";
  });
}

function renderOverviewCharts() {
  renderPlotlyChart("overview-conversion-donut");
}

function renderFunnelCharts() {
  renderPlotlyChart("funnel-process-chart");
  renderPlotlyChart("funnel-value-chart");
}

function renderPlotlyChart(elementId) {
  const el = document.getElementById(elementId);
  if (!el || !el.dataset.chart) return;
  const figure = JSON.parse(el.dataset.chart);
  Plotly.newPlot(el, figure.data, figure.layout, { responsive: true, displayModeBar: false });
}

function renderGoalsCharts() {
  renderPlotlyChart("goals-revenue-chart");
  renderPlotlyChart("goals-seller-chart");
}

document.addEventListener("DOMContentLoaded", () => {
  renderPlotlyChart("weekly-chart");
  renderOverviewCharts();
  renderGoalsCharts();
  renderFunnelCharts();
  initMobileNavigation();
  initPageBackButtons();
});

document.body.addEventListener("htmx:afterSwap", (event) => {
  if (event.detail.target?.id === "goals-root") {
    renderGoalsCharts();
  }
  if (event.detail.target?.id === "overview-root") {
    renderOverviewCharts();
  }
  if (event.detail.target?.id === "funnel-root") {
    renderFunnelCharts();
  }
});

function initMobileNavigation() {
  const body = document.body;
  const menuBtn = document.getElementById("mobile-menu-btn");
  const closeBtn = document.getElementById("sidebar-close-btn");
  const overlay = document.getElementById("sidebar-overlay");
  const sidebar = document.getElementById("app-sidebar");
  if (!menuBtn || !sidebar) return;

  const setOpen = (open) => {
    body.classList.toggle("sidebar-open", open);
    menuBtn.setAttribute("aria-expanded", open ? "true" : "false");
    if (overlay) {
      overlay.setAttribute("aria-hidden", open ? "false" : "true");
    }
  };

  menuBtn.addEventListener("click", () => {
    setOpen(!body.classList.contains("sidebar-open"));
  });

  closeBtn?.addEventListener("click", () => setOpen(false));
  overlay?.addEventListener("click", () => setOpen(false));

  sidebar.querySelectorAll(".sidebar-link, .sidebar-link-logout").forEach((link) => {
    link.addEventListener("click", () => setOpen(false));
  });

  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape") {
      setOpen(false);
    }
  });

  window.addEventListener("resize", () => {
    if (window.innerWidth > 900) {
      setOpen(false);
    }
  });
}

function initPageBackButtons() {
  document.querySelectorAll(".page-back-btn[data-back-fallback]").forEach((button) => {
    button.addEventListener("click", () => {
      const fallback = button.getAttribute("data-back-fallback") || "/visao-geral";
      if (window.history.length > 1) {
        history.back();
        return;
      }
      window.location.href = fallback;
    });
  });
}
