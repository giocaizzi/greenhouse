// Chart helper used by chart fragments. Populated in M6.
window.renderIrrigationChart = function (canvasEl, payload) {
  if (!window.Chart || !canvasEl) return;
  if (canvasEl._chart) {
    canvasEl._chart.destroy();
  }
  const datasets = (payload.datasets || []).map((d) => ({
    label: d.sensor_name,
    data: (d.points || []).map(([x, y]) => ({ x: x * 1000, y })),
    borderWidth: 2,
    tension: 0.2,
    pointRadius: 0,
  }));

  const annotations = {};
  const t = payload.threshold || {};
  if (t.min != null && t.max != null) {
    annotations.band = {
      type: "box",
      yMin: t.min,
      yMax: t.max,
      backgroundColor: "rgba(0, 200, 100, 0.08)",
      borderWidth: 0,
    };
  }
  (payload.events || []).forEach((ev, i) => {
    annotations["event_" + i] = {
      type: "line",
      xMin: ev.timestamp * 1000,
      xMax: ev.timestamp * 1000,
      borderColor: "rgba(50, 100, 200, 0.6)",
      borderWidth: 2,
      borderDash: [4, 4],
      label: {
        display: true,
        content: ev.duration_minutes != null ? ev.duration_minutes + "m" : ev.action,
        position: "start",
        backgroundColor: "rgba(50, 100, 200, 0.6)",
        color: "white",
        font: { size: 10 },
      },
    };
  });

  const ctx = canvasEl.getContext("2d");
  canvasEl._chart = new Chart(ctx, {
    type: "line",
    data: { datasets },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      scales: {
        x: { type: "time", time: { tooltipFormat: "MMM d HH:mm" } },
        y: { beginAtZero: false },
      },
      plugins: {
        legend: { position: "bottom" },
        annotation: { annotations },
      },
    },
  });
};
