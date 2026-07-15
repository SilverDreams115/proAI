import { escapeHtml, formatDate, formatPercent } from "./helpers.js";

function metric(label, value) {
  return `
    <div class="shadow-positions-item">
      <span class="shadow-card-label">${escapeHtml(label)}</span>
      <span class="shadow-positions-value">${escapeHtml(value)}</span>
    </div>`;
}

function renderGate(gate) {
  const allowed = gate?.allowed === true;
  const blocked = gate?.blocked_positions || [];
  const warnings = gate?.warnings || [];
  const rows = blocked.slice(0, 8).map((item) => `
    <tr>
      <td class="mono">${escapeHtml(item.draw_code)} · ${escapeHtml(item.position)}</td>
      <td>${escapeHtml(item.match)}</td>
      <td>${escapeHtml((item.reasons || []).join(", "))}</td>
    </tr>
  `).join("");
  return `
    <div class="shadow-alert ${allowed ? "tone-ok" : "tone-bad"}">
      ${allowed ? "Publicación habilitada" : "Publicación bloqueada"} · ${escapeHtml(blocked.length)} bloqueo(s) · ${escapeHtml(warnings.length)} advertencia(s)
    </div>
    ${blocked.length ? `
      <table class="dryrun-table opa-table">
        <thead><tr><th>Posición</th><th>Partido</th><th>Motivo</th></tr></thead>
        <tbody>${rows}</tbody>
      </table>` : ""}`;
}

function renderSegments(segments) {
  const confidence = segments?.confidence || [];
  const rows = confidence.slice(0, 6).map((item) => `
    <tr>
      <td>${escapeHtml(item.label)}</td>
      <td>${escapeHtml(item.total)}</td>
      <td>${item.accuracy == null ? "—" : formatPercent(item.accuracy)}</td>
    </tr>
  `).join("");
  return rows
    ? `<table class="dryrun-table opa-table compact"><thead><tr><th>Confianza</th><th>Casos</th><th>Acierto</th></tr></thead><tbody>${rows}</tbody></table>`
    : `<div class="empty-state">Sin resultados finalizados suficientes para segmentar.</div>`;
}

function renderPlaceholderQueue(queue) {
  const items = queue?.items || [];
  if (!items.length) return `<div class="shadow-alert tone-ok">Sin placeholders accionables en esta slate.</div>`;
  const rows = items.slice(0, 8).map((item) => {
    const suggestions = Object.entries(item.suggestions || {})
      .flatMap(([side, values]) => (values || []).map((v) => `${side}: ${v.name}`))
      .join(", ") || "sin candidato interno";
    return `
      <tr>
        <td class="mono">${escapeHtml(item.draw_code)} · ${escapeHtml(item.position)}</td>
        <td>${escapeHtml(item.match)}</td>
        <td>${escapeHtml(suggestions)}</td>
      </tr>`;
  }).join("");
  return `
    <table class="dryrun-table opa-table compact">
      <thead><tr><th>Posición</th><th>Partido</th><th>Candidatos internos</th></tr></thead>
      <tbody>${rows}</tbody>
    </table>`;
}

function renderFreshness(freshness) {
  const rows = (freshness?.slates || []).map((slate) => `
    <tr>
      <td class="mono">${escapeHtml(slate.draw_code)}</td>
      <td>${escapeHtml(slate.pull_state || "—")}</td>
      <td>${escapeHtml(slate.completed_count)} / ${escapeHtml(slate.match_count)}</td>
      <td>${escapeHtml((slate.sources || []).join(", ") || "—")}</td>
      <td>${escapeHtml(slate.age_minutes == null ? "—" : `${slate.age_minutes} min`)}</td>
    </tr>
  `).join("");
  return rows
    ? `<table class="dryrun-table opa-table compact"><thead><tr><th>Slate</th><th>Estado</th><th>Finales</th><th>Fuente</th><th>Edad</th></tr></thead><tbody>${rows}</tbody></table>`
    : `<div class="empty-state">Sin slates activas para monitorear resultados.</div>`;
}

function renderConfidence(explainer) {
  const rows = (explainer?.matches || []).slice(0, 8).map((match) => {
    const components = match.components || {};
    return `
      <tr>
        <td class="mono">${escapeHtml(match.draw_code)} · ${escapeHtml(match.position)}</td>
        <td>${escapeHtml(match.match)}</td>
        <td>${escapeHtml(match.status)} · ${escapeHtml(match.pick || "—")}</td>
        <td>${escapeHtml(components.evidence_coverage?.level || "—")} · ${escapeHtml(components.data_quality?.level || "—")} · ${escapeHtml(components.model_provenance?.level || "—")}</td>
      </tr>`;
  }).join("");
  return rows
    ? `<table class="dryrun-table opa-table compact"><thead><tr><th>Posición</th><th>Partido</th><th>Pick</th><th>Componentes</th></tr></thead><tbody>${rows}</tbody></table>`
    : `<div class="empty-state">Sin explicación de confianza disponible.</div>`;
}

export function renderOperationalPredictionAuditPanel(report) {
  if (!report) return `<div class="empty-state">Sin auditoría operativa cargada.</div>`;
  const summary = report.prediction_audit?.summary || {};
  return `
    <div class="shadow-panel opa-panel">
      <div class="shadow-head">
        <span class="shadow-badge">AUDITORÍA · SOLO LECTURA</span>
        <h3>Motor de predicción y publicación</h3>
        <p class="meta-copy">Generado ${escapeHtml(formatDate(report.generated_at))} · fuentes existentes solamente.</p>
      </div>
      <div class="shadow-positions-grid">
        ${metric("Slates evaluadas", summary.slate_count ?? 0)}
        ${metric("Partidos con resultado", summary.scored_matches ?? 0)}
        ${metric("Aciertos", summary.hits ?? 0)}
        ${metric("Accuracy", summary.accuracy == null ? "—" : formatPercent(summary.accuracy))}
      </div>
      ${renderGate(report.publish_gate)}
      <div class="opa-grid">
        <section>
          <h4>Segmentos</h4>
          ${renderSegments(report.prediction_audit?.segments)}
        </section>
        <section>
          <h4>Placeholders</h4>
          ${renderPlaceholderQueue(report.placeholder_queue)}
        </section>
        <section>
          <h4>Confianza explicable</h4>
          ${renderConfidence(report.confidence_explainer)}
        </section>
        <section>
          <h4>Frescura</h4>
          ${renderFreshness(report.freshness_monitor)}
        </section>
      </div>
    </div>`;
}
