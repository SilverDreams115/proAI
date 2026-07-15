import { escapeHtml, formatDate } from "./helpers.js";

const WARNING_COPY = {
  live_results_observer_disabled: "Observer apagado",
  live_results_fetch_disabled: "Pull LN apagado",
  live_results_source_url_missing: "Falta URL LN",
  existing_results_source_missing: "Fuente LN no existe",
  no_active_slates_to_observe: "Sin slates activas",
  no_active_slate_results_seen_yet: "Activas esperando resultados",
  some_results_missing_source_marker: "Hay resultados sin fuente",
};

const PULL_STATE_COPY = {
  complete: "Completa",
  receiving_live: "Recibiendo en vivo",
  receiving_results: "Recibiendo resultados",
  waiting_results: "Esperando LN",
};

export function snapshotLnResultsObserver(report) {
  const active = Array.isArray(report?.active_slates) ? report.active_slates : [];
  return active.map((slate) => ({
    draw_code: slate.draw_code,
    pull_state: slate.pull_state,
    completed_count: Number(slate.completed_count || 0),
    live_count: Number(slate.live_count || 0),
    last_updated_at: slate.last_updated_at || null,
  }));
}

export function deriveLnResultsObserverAlert(report, previousSnapshot) {
  const previousByCode = new Map((previousSnapshot || []).map((item) => [item.draw_code, item]));
  const changed = snapshotLnResultsObserver(report).filter((item) => {
    const prev = previousByCode.get(item.draw_code);
    if (!prev) return item.completed_count > 0 || item.live_count > 0;
    return (
      item.completed_count > Number(prev.completed_count || 0) ||
      item.live_count > Number(prev.live_count || 0) ||
      (prev.pull_state === "waiting_results" && item.pull_state !== "waiting_results")
    );
  });
  return changed.length ? { kind: "new_results", slates: changed } : null;
}

export function renderLiveResultsObserverPanel(report, alert = null) {
  if (!report) {
    return `<div class="empty-state">Sin status LN todavía.</div>`;
  }
  const tone = report.pull_ready ? "ok" : "warn";
  const warnings = (report.warnings || []).map((w) => WARNING_COPY[w] || w);
  const sourceNames = (report.sources || []).map((s) => s.name).join(", ") || "—";
  const latest = report.latest_ingestion;
  const alertHtml = alert?.slates?.length
    ? `<div class="shadow-alert tone-ok">Resultados nuevos detectados: ${alert.slates.map((s) => escapeHtml(s.draw_code)).join(", ")}</div>`
    : "";
  const latestHtml = latest
    ? `
      <div class="shadow-positions">
        <div class="shadow-positions-item"><span class="shadow-card-label">Última ingesta LN</span><span class="shadow-positions-value">${escapeHtml(formatDate(latest.last_success_at))}</span></div>
        <div class="shadow-positions-item"><span class="shadow-card-label">Resultados guardados</span><span class="shadow-positions-value">${escapeHtml(latest.result_rows)} en ${escapeHtml(latest.slate_count)} slate(s)</span></div>
      </div>
      <div class="ln-mini-list">
        ${latest.draws.map((d) => `<span class="signal-pill">${escapeHtml(d.draw_code)} · ${escapeHtml(d.result_rows)} resultado(s) · ${escapeHtml(formatDate(d.last_updated_at))}</span>`).join("")}
      </div>`
    : `<p class="meta-copy">Aún no hay ingestas LN registradas en la base.</p>`;
  const rows = (report.active_slates || []).map((slate) => `
    <tr>
      <td class="mono">${escapeHtml(slate.draw_code)}</td>
      <td>${escapeHtml(slate.week_type)}</td>
      <td><span class="status-pill status-${slate.pull_state === "complete" ? "hit" : slate.pull_state === "waiting_results" ? "pending" : "live"}">${escapeHtml(PULL_STATE_COPY[slate.pull_state] || slate.pull_state)}</span></td>
      <td>${escapeHtml(slate.completed_count)} / ${escapeHtml(slate.match_count)}</td>
      <td>${escapeHtml((slate.sources || []).join(", ") || "—")}</td>
      <td>${escapeHtml(formatDate(slate.last_updated_at))}</td>
    </tr>`).join("");
  return `
    <div class="shadow-panel ln-results-panel">
      <div class="shadow-toprow"><span class="shadow-badge badge-canary">LN RESULTADOS · TIEMPO REAL</span></div>
      ${alertHtml}
      <div class="ext-results-head">
        <span class="ext-results-provider">Lotería Nacional</span>
        <span class="badge-risk tone-${tone}">${report.pull_ready ? "Pull listo" : "Revisar configuración"}</span>
        <span class="badge-muted">${report.fetch_enabled ? "fetch ON" : "fetch OFF"}</span>
      </div>
      <div class="shadow-positions">
        <div class="shadow-positions-item"><span class="shadow-card-label">Fuente</span><span class="shadow-positions-value">${escapeHtml(sourceNames)}</span></div>
        <div class="shadow-positions-item"><span class="shadow-card-label">Intervalo</span><span class="shadow-positions-value">${escapeHtml(report.observe_interval_minutes)} min</span></div>
      </div>
      ${warnings.length ? `<div class="shadow-alert">${warnings.map(escapeHtml).join(" · ")}</div>` : ""}
      ${latestHtml}
      <table class="dryrun-table ln-results-table">
        <thead><tr><th>Slate</th><th>Tipo</th><th>Estado</th><th>Finales</th><th>Fuentes</th><th>Actualizado</th></tr></thead>
        <tbody>${rows || `<tr><td colspan="6">Sin slates activas para observar.</td></tr>`}</tbody>
      </table>
    </div>
  `;
}
