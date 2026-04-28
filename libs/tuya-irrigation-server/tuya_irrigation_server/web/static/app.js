// Chart helper used by chart fragments. v3-themed (Geist Mono, gradient fill, theme-aware).
//
// Each call destroys the previous chart attached to the canvas (if any) and rebuilds it
// using the current document theme (CSS variables read at call time). Re-call on theme
// toggle via window.__rerenderCharts().

(function () {
  const css = (v) => getComputedStyle(document.documentElement).getPropertyValue(v).trim();

  function renderIrrigationChart(canvasEl, payload) {
    if (!window.Chart || !canvasEl) return;
    if (canvasEl._chart) {
      try { canvasEl._chart.destroy(); } catch (e) {}
    }

    const text = css("--text-muted") || "#9194A0";
    const dim = css("--text-dim") || "#62656E";
    const grid = css("--border") || "#222529";
    const surf = css("--surface") || "#131519";
    const danger = css("--danger") || "#E37E6B";
    const accent = css("--accent") || "#7CC98F";
    const accentSoft = css("--accent-soft") || "rgba(124, 201, 143, 0.10)";

    const ramp = [css("--ch-1"), css("--ch-2"), css("--ch-3"), css("--ch-4")].map(
      (c) => c || accent,
    );

    Chart.defaults.font.family = "'Geist Mono', ui-monospace, monospace";
    Chart.defaults.font.size = 11;
    Chart.defaults.color = text;

    const ctx = canvasEl.getContext("2d");
    const h = canvasEl.parentElement
      ? canvasEl.parentElement.getBoundingClientRect().height || 320
      : 320;

    const datasets = (payload.datasets || []).map((d, i) => {
      const color = ramp[i % ramp.length];
      const data = (d.points || []).map(([x, y]) => ({ x: x * 1000, y }));
      const ds = {
        label: d.sensor_name,
        data,
        borderColor: color,
        borderWidth: i === 0 ? 1.75 : 1.5,
        tension: 0.35,
        pointRadius: 0,
        pointHoverRadius: 5,
        pointHoverBackgroundColor: surf,
        pointHoverBorderColor: color,
        pointHoverBorderWidth: 2,
        fill: false,
      };
      if (i === 0) {
        const grad = ctx.createLinearGradient(0, 0, 0, h);
        grad.addColorStop(0, color + "30");
        grad.addColorStop(1, color + "00");
        ds.backgroundColor = grad;
        ds.fill = true;
      }
      return ds;
    });

    const annotations = {};
    const t = payload.threshold || {};
    if (t.min != null && t.max != null) {
      annotations.band = {
        type: "box",
        yMin: t.min,
        yMax: t.max,
        backgroundColor: accentSoft,
        borderWidth: 0,
        drawTime: "beforeDatasetsDraw",
      };
      annotations.bandTop = {
        type: "line",
        yMin: t.max,
        yMax: t.max,
        borderColor: accent,
        borderWidth: 1,
        borderDash: [3, 3],
        drawTime: "beforeDatasetsDraw",
      };
      annotations.bandBot = {
        type: "line",
        yMin: t.min,
        yMax: t.min,
        borderColor: accent,
        borderWidth: 1,
        borderDash: [3, 3],
        drawTime: "beforeDatasetsDraw",
      };
    }
    (payload.events || []).forEach((ev, i) => {
      const x = ev.timestamp * 1000;
      annotations["event_" + i] = {
        type: "line",
        xMin: x,
        xMax: x,
        borderColor: danger,
        borderWidth: 1.25,
        borderDash: [4, 3],
        label: {
          display: true,
          content:
            ev.duration_minutes != null
              ? ev.duration_minutes + " min"
              : ev.action || "event",
          position: "start",
          color: danger,
          backgroundColor: surf,
          borderColor: grid,
          borderWidth: 1,
          font: {
            family: "'Geist Mono', ui-monospace, monospace",
            size: 10,
            weight: "500",
          },
          padding: 4,
          borderRadius: 4,
          yAdjust: -10,
        },
      };
    });

    canvasEl._chart = new Chart(ctx, {
      type: "line",
      data: { datasets },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        interaction: { mode: "index", intersect: false },
        layout: { padding: { top: 6, right: 4, bottom: 0, left: 4 } },
        scales: {
          x: {
            type: "time",
            time: {
              tooltipFormat: "MMM d HH:mm",
              displayFormats: {
                millisecond: "HH:mm:ss",
                second: "HH:mm:ss",
                minute: "HH:mm",
                hour: "HH:mm",
                day: "MMM d",
                week: "MMM d",
                month: "MMM yyyy",
              },
            },
            grid: { display: false },
            border: { color: grid },
            ticks: {
              color: dim,
              maxRotation: 0,
              autoSkipPadding: 24,
              padding: 6,
              font: {
                size: 10,
                family: "'Geist Mono', ui-monospace, monospace",
              },
            },
          },
          y: {
            grid: { color: grid, lineWidth: 1 },
            border: { display: false },
            ticks: {
              color: dim,
              padding: 6,
              font: {
                size: 10,
                family: "'Geist Mono', ui-monospace, monospace",
              },
            },
          },
        },
        plugins: {
          legend: {
            position: "bottom",
            labels: {
              color: text,
              boxWidth: 10,
              boxHeight: 10,
              usePointStyle: true,
              pointStyle: "circle",
              padding: 14,
              font: {
                size: 11,
                family: "'Geist', system-ui, sans-serif",
              },
            },
          },
          tooltip: {
            backgroundColor: surf,
            borderColor: grid,
            borderWidth: 1,
            titleColor: text,
            bodyColor: text,
            padding: 10,
            displayColors: true,
            boxPadding: 4,
            cornerRadius: 8,
            titleFont: {
              family: "'Geist', system-ui, sans-serif",
              size: 11,
              weight: "500",
            },
            bodyFont: {
              family: "'Geist Mono', ui-monospace, monospace",
              size: 11,
            },
          },
          annotation: { annotations },
        },
      },
    });
  }

  // Re-render every chart attached to a canvas — used on theme toggle
  function rerenderAllCharts() {
    document.querySelectorAll("canvas[id^='chart-']").forEach((c) => {
      const m = c.id.replace(/^chart-/, "");
      const raw = document.getElementById("chart-data-" + m);
      if (!raw) return;
      try {
        const data = JSON.parse(raw.textContent);
        if (c.id === "chart-overlay") {
          renderMultiMetricChart(c, data);
        } else if (c.id.startsWith("chart-health-")) {
          renderHealthTimeline(c, data);
        } else {
          renderIrrigationChart(c, data);
        }
      } catch (e) {}
    });
  }

  // ── Multi-metric overlay chart ─────────────────────────────────────────────
  // Renders 3 normalised series (soil, humidity, light) on a shared 0-100 Y axis
  // with irrigation event vertical lines. Reuses the same CSS-variable colour ramp.
  function renderMultiMetricChart(canvasEl, payload) {
    if (!window.Chart || !canvasEl) return;
    if (canvasEl._chart) {
      try { canvasEl._chart.destroy(); } catch (e) {}
    }

    const text = css("--text-muted") || "#9194A0";
    const dim = css("--text-dim") || "#62656E";
    const grid = css("--border") || "#222529";
    const surf = css("--surface") || "#131519";
    const danger = css("--danger") || "#E37E6B";
    const accent = css("--accent") || "#7CC98F";

    const metricColors = {
      soil: css("--ch-1") || accent,
      humidity: css("--ch-2") || "#6EA8D8",
      light: css("--ch-3") || "#D4A84B",
    };
    const metricLabels = { soil: "Soil moisture %", humidity: "Env humidity %", light: "Light (scaled)" };

    Chart.defaults.font.family = "'Geist Mono', ui-monospace, monospace";
    Chart.defaults.font.size = 11;
    Chart.defaults.color = text;

    const ctx = canvasEl.getContext("2d");

    const datasets = (payload.datasets || []).map((d, i) => {
      const color = metricColors[d.metric] || accent;
      const data = (d.points || []).map(([x, y]) => ({ x: x * 1000, y }));
      return {
        label: metricLabels[d.metric] || d.metric,
        data,
        borderColor: color,
        borderWidth: i === 0 ? 1.75 : 1.5,
        tension: 0.35,
        pointRadius: 0,
        pointHoverRadius: 5,
        pointHoverBackgroundColor: surf,
        pointHoverBorderColor: color,
        pointHoverBorderWidth: 2,
        fill: false,
      };
    });

    const annotations = {};
    (payload.events || []).forEach((ev, i) => {
      const x = ev.timestamp * 1000;
      annotations["event_" + i] = {
        type: "line",
        xMin: x,
        xMax: x,
        borderColor: danger,
        borderWidth: 1.25,
        borderDash: [4, 3],
        label: {
          display: true,
          content: ev.duration_minutes != null ? ev.duration_minutes + " min" : ev.action || "event",
          position: "start",
          color: danger,
          backgroundColor: surf,
          borderColor: grid,
          borderWidth: 1,
          font: { family: "'Geist Mono', ui-monospace, monospace", size: 10, weight: "500" },
          padding: 4,
          borderRadius: 4,
          yAdjust: -10,
        },
      };
    });

    canvasEl._chart = new Chart(ctx, {
      type: "line",
      data: { datasets },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        interaction: { mode: "index", intersect: false },
        layout: { padding: { top: 6, right: 4, bottom: 0, left: 4 } },
        scales: {
          x: {
            type: "time",
            time: {
              tooltipFormat: "MMM d HH:mm",
              displayFormats: { millisecond: "HH:mm:ss", second: "HH:mm:ss", minute: "HH:mm", hour: "HH:mm", day: "MMM d", week: "MMM d", month: "MMM yyyy" },
            },
            grid: { display: false },
            border: { color: grid },
            ticks: { color: dim, maxRotation: 0, autoSkipPadding: 24, padding: 6, font: { size: 10, family: "'Geist Mono', ui-monospace, monospace" } },
          },
          y: {
            min: 0,
            max: 100,
            grid: { color: grid, lineWidth: 1 },
            border: { display: false },
            ticks: { color: dim, padding: 6, font: { size: 10, family: "'Geist Mono', ui-monospace, monospace" }, callback: (v) => v + "%" },
          },
        },
        plugins: {
          legend: {
            position: "bottom",
            labels: { color: text, boxWidth: 10, boxHeight: 10, usePointStyle: true, pointStyle: "circle", padding: 14, font: { size: 11, family: "'Geist', system-ui, sans-serif" } },
          },
          tooltip: {
            backgroundColor: surf,
            borderColor: grid,
            borderWidth: 1,
            titleColor: text,
            bodyColor: text,
            padding: 10,
            displayColors: true,
            boxPadding: 4,
            cornerRadius: 8,
            titleFont: { family: "'Geist', system-ui, sans-serif", size: 11, weight: "500" },
            bodyFont: { family: "'Geist Mono', ui-monospace, monospace", size: 11 },
            callbacks: {
              label: function (ctx) {
                const ds = payload.datasets[ctx.datasetIndex];
                let val = ctx.parsed.y;
                // Back-convert light from scaled value to lux for tooltip
                if (ds && ds.metric === "light" && ds.original_max) {
                  const lux = Math.round(val * ds.original_max / 100);
                  return `${ctx.dataset.label}: ${val.toFixed(1)}% (${lux} lx)`;
                }
                return `${ctx.dataset.label}: ${val.toFixed(1)}%`;
              },
            },
          },
          annotation: { annotations },
        },
      },
    });
  }

  // ── Plant health timeline ──────────────────────────────────────────────────
  // Renders a single daily health score line with coloured background bands:
  //   green ≥ 80, amber 50-80, red < 50. Uses the existing annotation plugin.
  function renderHealthTimeline(canvasEl, payload) {
    if (!window.Chart || !canvasEl) return;
    if (canvasEl._chart) {
      try { canvasEl._chart.destroy(); } catch (e) {}
    }

    const text = css("--text-muted") || "#9194A0";
    const dim = css("--text-dim") || "#62656E";
    const grid = css("--border") || "#222529";
    const surf = css("--surface") || "#131519";
    const accent = css("--accent") || "#7CC98F";

    const thresholds = payload.thresholds || { good: 80, ok: 50 };
    const tGood = thresholds.good ?? 80;
    const tOk = thresholds.ok ?? 50;

    const ctx = canvasEl.getContext("2d");
    const data = (payload.points || []).map(([x, y]) => ({ x: x * 1000, y }));

    Chart.defaults.font.family = "'Geist Mono', ui-monospace, monospace";
    Chart.defaults.font.size = 11;
    Chart.defaults.color = text;

    const annotations = {
      bandGreen: {
        type: "box",
        yMin: tGood,
        yMax: 100,
        backgroundColor: "rgba(124, 201, 143, 0.08)",
        borderWidth: 0,
        drawTime: "beforeDatasetsDraw",
      },
      lineGreen: {
        type: "line",
        yMin: tGood,
        yMax: tGood,
        borderColor: "rgba(124, 201, 143, 0.5)",
        borderWidth: 1,
        borderDash: [3, 3],
        drawTime: "beforeDatasetsDraw",
      },
      bandAmber: {
        type: "box",
        yMin: tOk,
        yMax: tGood,
        backgroundColor: "rgba(212, 168, 75, 0.06)",
        borderWidth: 0,
        drawTime: "beforeDatasetsDraw",
      },
      lineOk: {
        type: "line",
        yMin: tOk,
        yMax: tOk,
        borderColor: "rgba(212, 168, 75, 0.5)",
        borderWidth: 1,
        borderDash: [3, 3],
        drawTime: "beforeDatasetsDraw",
      },
      bandRed: {
        type: "box",
        yMin: 0,
        yMax: tOk,
        backgroundColor: "rgba(227, 126, 107, 0.06)",
        borderWidth: 0,
        drawTime: "beforeDatasetsDraw",
      },
    };

    canvasEl._chart = new Chart(ctx, {
      type: "line",
      data: {
        datasets: [{
          label: "Daily health score",
          data,
          borderColor: accent,
          borderWidth: 1.75,
          tension: 0.35,
          pointRadius: 0,
          pointHoverRadius: 5,
          pointHoverBackgroundColor: surf,
          pointHoverBorderColor: accent,
          pointHoverBorderWidth: 2,
          fill: false,
        }],
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        interaction: { mode: "index", intersect: false },
        layout: { padding: { top: 6, right: 4, bottom: 0, left: 4 } },
        scales: {
          x: {
            type: "time",
            time: {
              tooltipFormat: "MMM d",
              displayFormats: { day: "MMM d", week: "MMM d", month: "MMM yyyy" },
            },
            grid: { display: false },
            border: { color: grid },
            ticks: { color: dim, maxRotation: 0, autoSkipPadding: 24, padding: 6, font: { size: 10, family: "'Geist Mono', ui-monospace, monospace" } },
          },
          y: {
            min: 0,
            max: 100,
            grid: { color: grid, lineWidth: 1 },
            border: { display: false },
            ticks: { color: dim, padding: 6, font: { size: 10, family: "'Geist Mono', ui-monospace, monospace" }, callback: (v) => v + "%" },
          },
        },
        plugins: {
          legend: {
            position: "bottom",
            labels: { color: text, boxWidth: 10, boxHeight: 10, usePointStyle: true, pointStyle: "circle", padding: 14, font: { size: 11, family: "'Geist', system-ui, sans-serif" } },
          },
          tooltip: {
            backgroundColor: surf,
            borderColor: grid,
            borderWidth: 1,
            titleColor: text,
            bodyColor: text,
            padding: 10,
            displayColors: false,
            cornerRadius: 8,
            titleFont: { family: "'Geist', system-ui, sans-serif", size: 11, weight: "500" },
            bodyFont: { family: "'Geist Mono', ui-monospace, monospace", size: 11 },
            callbacks: {
              label: (ctx) => "Health: " + ctx.parsed.y.toFixed(1) + "%",
            },
          },
          annotation: { annotations },
        },
      },
    });
  }

  window.renderIrrigationChart = renderIrrigationChart;
  window.renderMultiMetricChart = renderMultiMetricChart;
  window.renderHealthTimeline = renderHealthTimeline;
  window.__rerenderCharts = rerenderAllCharts;
})();
