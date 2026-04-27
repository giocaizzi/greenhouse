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
        renderIrrigationChart(c, data);
      } catch (e) {}
    });
  }

  window.renderIrrigationChart = renderIrrigationChart;
  window.__rerenderCharts = rerenderAllCharts;
})();

/* 2026 UI primitives — toasts, top-of-page progress, command-K, sheet helpers. */
(function () {
  const progress = () => document.getElementById("page-progress");
  document.addEventListener("htmx:beforeRequest", () => {
    const p = progress();
    if (p) { p.classList.remove("is-done"); p.classList.add("is-active"); }
  });
  document.addEventListener("htmx:afterOnLoad", () => {
    const p = progress();
    if (!p) return;
    p.classList.remove("is-active"); p.classList.add("is-done");
    setTimeout(() => p.classList.remove("is-done"), 250);
  });
  document.addEventListener("htmx:responseError", () => {
    const p = progress();
    if (p) p.classList.remove("is-active");
    showToast({ severity: "danger", title: "Request failed", message: "Server returned an error." });
  });

  function showToast({ severity = "info", title = "", message = "", duration = 4000, action } = {}) {
    const host = document.getElementById("toasts");
    if (!host) return;
    const node = document.createElement("div");
    node.className = "toast toast--" + severity;
    node.setAttribute("role", severity === "danger" ? "alert" : "status");
    const iconName = { info: "i-info", warning: "i-warning", danger: "i-x-circle", success: "i-check" }[severity] || "i-info";
    node.innerHTML = `
      <span class="toast__icon"><svg class="icon"><use href="/static/icons/sprite.svg#${iconName}"></use></svg></span>
      <div>
        ${title ? `<div class="toast__title"></div>` : ""}
        <div class="toast__msg"></div>
        ${action ? `<button type="button" class="toast__action" data-toast-action></button>` : ""}
      </div>
      <button type="button" class="toast__close" aria-label="Dismiss">×</button>
    `;
    if (title) node.querySelector(".toast__title").textContent = title;
    node.querySelector(".toast__msg").textContent = message;
    if (action) node.querySelector("[data-toast-action]").textContent = action.label;
    host.appendChild(node);
    let timer = null;
    const close = () => {
      if (timer) clearTimeout(timer);
      node.classList.add("is-leaving");
      node.addEventListener("animationend", () => node.remove(), { once: true });
    };
    node.querySelector(".toast__close").addEventListener("click", close);
    if (action && typeof action.onClick === "function") {
      node.querySelector("[data-toast-action]").addEventListener("click", () => {
        try { action.onClick(); } catch (e) {}
        close();
      });
    }
    if (duration > 0) timer = setTimeout(close, duration);
  }
  window.showToast = showToast;
  document.body.addEventListener("toast", (ev) => { if (ev.detail) showToast(ev.detail); });
  document.body.addEventListener("htmx:afterRequest", (ev) => {
    const xhr = ev.detail.xhr;
    if (!xhr) return;
    const toastHdr = xhr.getResponseHeader("HX-Toast");
    if (toastHdr) { try { showToast(JSON.parse(toastHdr)); } catch (e) {} }
  });

  function getPalette() { return document.getElementById("cmdk-dialog"); }
  function openPalette() {
    const dlg = getPalette();
    if (!dlg) return;
    dlg.showModal();
    const input = dlg.querySelector("input");
    if (input) { input.value = ""; input.focus(); }
    const list = dlg.querySelector(".cmdk__list");
    if (list) list.innerHTML = "";
  }
  function closePalette() {
    const dlg = getPalette();
    if (dlg && dlg.open) dlg.close();
  }
  document.addEventListener("keydown", (e) => {
    if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "k") {
      e.preventDefault();
      const dlg = getPalette();
      if (dlg && dlg.open) closePalette(); else openPalette();
    }
  });
  window.openCmdK = openPalette;
  window.closeCmdK = closePalette;

  document.addEventListener("click", (e) => {
    const opener = e.target.closest("[data-sheet-open]");
    if (opener) {
      const id = opener.getAttribute("data-sheet-open");
      const dlg = document.getElementById(id);
      if (dlg && typeof dlg.showModal === "function") dlg.showModal();
      return;
    }
    const closer = e.target.closest("[data-sheet-close]");
    if (closer) {
      const dlg = closer.closest("dialog");
      if (dlg) dlg.close();
    }
  });
})();
