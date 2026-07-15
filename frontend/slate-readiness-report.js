import { escapeHtml } from "./helpers.js";

const FLAG_LABELS = {
  LOW_EVIDENCE: "Evidencia baja",
  FALLBACK_USED: "Fallback usado",
  EXTREME_PROBABILITY_WITHOUT_EVIDENCE: "Probabilidad extrema sin evidencia",
  SUSPICIOUS_CLASS_PROBABILITY: "Probabilidad de clase sospechosa",
  BLOCKED_INSUFFICIENT_DATA: "Datos insuficientes",
  PLACEHOLDER_TEAM: "Equipo placeholder",
  SUSPICIOUS_TEAM_NAME: "Nombre de equipo sospechoso",
  INTERNATIONAL_FRIENDLY: "Amistoso internacional",
  EXTREME_PROBABILITY_CAPPED: "Probabilidad capada",
  FRIENDLY_UNCERTAINTY_PENALTY: "Penalizacion por amistoso",
};

function flagLabel(flag) {
  return FLAG_LABELS[flag] || String(flag || "Sin detalle");
}

function countValue(counts, key) {
  return Number(counts?.[key] || 0);
}

function renderCountCards(slate) {
  const statuses = slate.status_counts || {};
  const flags = slate.flag_counts || {};
  const suspicious = slate.suspicious_team_name_positions || [];
  return `
    <div class="shadow-grid slate-readiness-counts">
      <div class="shadow-card"><span class="shadow-card-label">Listo</span><strong>${escapeHtml(countValue(statuses, "LISTO"))}</strong></div>
      <div class="shadow-card"><span class="shadow-card-label">Revisar</span><strong>${escapeHtml(countValue(statuses, "REVISAR"))}</strong></div>
      <div class="shadow-card"><span class="shadow-card-label">Bloqueado</span><strong>${escapeHtml(countValue(statuses, "BLOQUEADO"))}</strong></div>
      <div class="shadow-card"><span class="shadow-card-label">Nombres sospechosos</span><strong>${escapeHtml(suspicious.length)}</strong></div>
      <div class="shadow-card"><span class="shadow-card-label">Low evidence</span><strong>${escapeHtml(countValue(flags, "LOW_EVIDENCE"))}</strong></div>
      <div class="shadow-card"><span class="shadow-card-label">Fallback</span><strong>${escapeHtml(countValue(flags, "FALLBACK_USED"))}</strong></div>
    </div>`;
}

const FILTERS = [
  { key: "all", label: "Todos" },
  { key: "team_resolution", label: "Equipos" },
  { key: "evidence_coverage", label: "Evidencia" },
  { key: "model_fallback", label: "Fallback" },
  { key: "pick_review", label: "Revisión" },
];

function renderFilters(activeFilter) {
  return `
    <div class="readiness-filter-row" role="group" aria-label="Filtro readiness">
      ${FILTERS.map((filter) => `
        <button type="button" class="readiness-filter ${activeFilter === filter.key ? "active" : ""}" data-readiness-filter="${escapeHtml(filter.key)}">
          ${escapeHtml(filter.label)}
        </button>
      `).join("")}
    </div>`;
}

function filterMatches(matches, activeFilter) {
  if (activeFilter === "all") return matches || [];
  return (matches || []).filter((match) => (match.actionable_blockers || []).includes(activeFilter));
}

function renderMatchRows(matches, activeFilter) {
  const filtered = filterMatches(matches, activeFilter);
  const rows = (matches || [])
    .filter((match) => filtered.includes(match))
    .filter((match) => match.status !== "LISTO" || (match.data_flags || []).length)
    .map((match) => {
      const flags = (match.flags || []).map(flagLabel).join(", ") || "Sin flags";
      const suspicious = (match.suspicious_team_names || []).join(", ") || "ninguno";
      const blockers = (match.actionable_blockers || []).join(", ") || "none";
      return `<tr>
        <td>${escapeHtml(match.position)}</td>
        <td>${escapeHtml(match.match)}</td>
        <td><span class="badge-risk tone-${match.status === "BLOQUEADO" ? "danger" : "warn"}">${escapeHtml(match.status)}</span></td>
        <td>${escapeHtml(match.evidence_level)}</td>
        <td class="mono">${escapeHtml(match.pick)} · ${escapeHtml(match.top_probability)}</td>
        <td>${escapeHtml(match.recent_results_count)} / ${escapeHtml(match.head_to_head_results_count)}</td>
        <td class="meta-copy">${escapeHtml(blockers)}</td>
        <td class="meta-copy">${escapeHtml(flags)}</td>
        <td class="meta-copy">${escapeHtml(suspicious)}</td>
      </tr>`;
    })
    .join("");
  if (!rows) return `<div class="empty-state">Sin bloqueos para este filtro.</div>`;
  return `
    <table class="dryrun-table slate-readiness-table">
      <thead><tr><th>#</th><th>Partido</th><th>Estado</th><th>Evidencia</th><th>Pick</th><th>Rec/H2H</th><th>Acción</th><th>Motivos</th><th>Nombres</th></tr></thead>
      <tbody>${rows}</tbody>
    </table>`;
}

export function renderSlateReadinessReportPanel(report, activeFilter = "all") {
  const slate = report?.slates?.[0];
  if (!slate) {
    return `<div class="empty-state">Sin reporte de readiness para la papeleta activa.</div>`;
  }
  const safe = slate.safe_revisar_to_listo_candidates || [];
  const safeLine = safe.length
    ? `<div class="mm-recommended">Candidatos seguros REVISAR -> LISTO: <strong>${escapeHtml(safe.join(", "))}</strong></div>`
    : `<div class="mm-recommended mm-recommended-none">Candidatos seguros REVISAR -> LISTO: <strong>ninguno</strong></div>`;
  return `
    <div class="shadow-panel slate-readiness-panel">
      <div class="shadow-head">
        <span class="shadow-badge readiness-badge">READINESS · SOLO LECTURA</span>
        <h3>${escapeHtml(slate.draw_code)} · ${escapeHtml(slate.match_count)} partidos</h3>
      </div>
      ${renderCountCards(slate)}
      ${safeLine}
      ${renderFilters(activeFilter)}
      ${renderMatchRows(slate.matches || [], activeFilter)}
    </div>`;
}
