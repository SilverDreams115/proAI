// R6.3 — External Results panel (free results provider dry-run, read-only).
//
// Pure render helper (returns an HTML string, no DOM/fetch) so it can be locked
// with Vitest. It shows what a free results provider (football-data.org) would
// report for the active slate WITHOUT writing anything: coverage, per-match
// status and score, provider availability. It never applies results — applying
// is a separate, explicitly-confirmed CLI step.
import { escapeHtml } from "./helpers.js";

const STATUS_COPY = {
  ok: "Proveedor disponible",
  disabled: "Proveedor deshabilitado",
  unavailable_missing_key: "Falta API key",
  insufficient_coverage: "Cobertura insuficiente",
  provider_error: "Error del proveedor",
};

const STATUS_TONE = {
  ok: "ok",
  disabled: "muted",
  unavailable_missing_key: "warn",
  insufficient_coverage: "warn",
  provider_error: "danger",
};

const MATCH_STATUS_LABEL = {
  unmatched: "Sin emparejar",
  scheduled: "Programado",
  finished: "Finalizado",
  in_play: "En juego",
  unknown: "—",
};

export function renderExternalResultsPanel(report) {
  if (!report || !report.provider) {
    return `<div class="empty-state">Sin datos de resultados externos para la papeleta activa.</div>`;
  }
  const status = report.status || (report.enabled ? "ok" : "disabled");
  const tone = STATUS_TONE[status] || "muted";
  const badge = `<span class="shadow-badge badge-canary">RESULTADOS EXTERNOS · SOLO LECTURA</span>`;

  // Provider not usable (disabled / missing key): clear call-to-config, no table.
  if (status === "disabled" || status === "unavailable_missing_key") {
    const msg =
      status === "unavailable_missing_key"
        ? `Fuente gratuita no configurada. Configura <span class="mono">PROAI_FOOTBALL_DATA_API_KEY</span> para habilitar el dry-run.`
        : `Proveedor de resultados deshabilitado (<span class="mono">PROAI_RESULTS_PROVIDER_ENABLED=false</span>). Dry-run solo informativo.`;
    return `
      <div class="shadow-panel ext-results-panel">
        <div class="shadow-toprow">${badge}</div>
        <div class="ext-results-head">
          <span class="ext-results-provider">${escapeHtml(report.provider)}</span>
          <span class="badge-risk tone-${escapeHtml(tone)}">${escapeHtml(STATUS_COPY[status] || status)}</span>
        </div>
        <p class="meta-copy">${msg}</p>
        <div class="shadow-alert">Solo lectura · no escribe resultados · apply manual con confirmación explícita.</div>
      </div>`;
  }

  const cov = report.coverage || { matched: 0, total: 0, rate: 0 };
  const ratePct = Math.round((Number(cov.rate) || 0) * 100);

  const rows = (report.matches || [])
    .map((m) => {
      const mStatus = MATCH_STATUS_LABEL[m.status] || m.status || "—";
      const score = m.score == null ? "—" : escapeHtml(m.score);
      const provider = m.provider_match ? escapeHtml(m.provider_match) : `<span class="meta-copy">sin emparejar</span>`;
      const conf = m.confidence && m.confidence !== "none"
        ? `<span class="badge-muted">${escapeHtml(m.confidence)}</span>`
        : `<span class="meta-copy">—</span>`;
      return `<tr>
        <td>${escapeHtml(m.position)}</td>
        <td>${escapeHtml(m.local_match)}</td>
        <td>${provider}</td>
        <td>${escapeHtml(mStatus)}</td>
        <td class="mono">${score}</td>
        <td>${conf}</td>
      </tr>`;
    })
    .join("");

  return `
    <div class="shadow-panel ext-results-panel">
      <div class="shadow-toprow">${badge}</div>
      <div class="ext-results-head">
        <span class="ext-results-provider">${escapeHtml(report.provider)}</span>
        <span class="badge-risk tone-${escapeHtml(tone)}">${escapeHtml(STATUS_COPY[status] || status)}</span>
        ${report.enabled ? "" : `<span class="badge-muted">dry-run</span>`}
      </div>
      <div class="shadow-positions">
        <div class="shadow-positions-item"><span class="shadow-card-label">Cobertura</span><span class="shadow-positions-value">${escapeHtml(cov.matched)} / ${escapeHtml(cov.total)} (${ratePct}%)</span></div>
        <div class="shadow-positions-item"><span class="shadow-card-label">Escritura</span><span class="shadow-positions-value">solo lectura</span></div>
      </div>
      <table class="dryrun-table ext-results-table">
        <thead><tr><th>#</th><th>Partido</th><th>Proveedor</th><th>Estado</th><th>Marcador</th><th>Confianza</th></tr></thead>
        <tbody>${rows}</tbody>
      </table>
      <div class="shadow-alert">Solo lectura · no escribe en match_results · apply manual requiere <span class="mono">--apply --confirm</span>.</div>
    </div>
  `;
}
