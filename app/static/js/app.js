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
  renderGoalsCharts();
});

document.body.addEventListener("htmx:afterSwap", (event) => {
  if (event.detail.target?.id === "goals-root") {
    renderGoalsCharts();
  }
});
