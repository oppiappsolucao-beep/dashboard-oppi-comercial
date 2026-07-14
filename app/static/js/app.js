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

document.addEventListener("DOMContentLoaded", () => {
  const chartEl = document.getElementById("weekly-chart");
  if (chartEl && chartEl.dataset.chart) {
    const figure = JSON.parse(chartEl.dataset.chart);
    Plotly.newPlot(chartEl, figure.data, figure.layout, { responsive: true, displayModeBar: false });
  }
});
