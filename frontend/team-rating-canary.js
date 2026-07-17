// R5.6-B — Team Rating Canary status (Diagnóstico panel).
//
// Pure render helper (returns an HTML string, no DOM/fetch) so it can be locked
// with Vitest. Shows the controlled-canary scope and active positions. The
// canary changes only the served effective probabilities for the scoped
// positions; it never touches the ticket optimizer.
import { escapeHtml } from "./helpers.js";
import { formatPositionRanges } from "./team-rating-shadow.js";

function boolChip(value) {
  return `<span class="shadow-chip ${value ? "shadow-chip-ok" : "shadow-chip-warn"}">${value ? "SÍ" : "NO"}</span>`;
}

export function renderTeamRatingCanaryPanel(status) {
  if (!status) {
    return `<div class="empty-state">Sin datos de canary para la papeleta activa.</div>`;
  }
  const inScope = Boolean(status.canary_enabled && status.in_scope);
  const activeCount = (status.active_positions || []).length;
  // "ACTIVO" only when at least one position actually routes: an enabled
  // canary with 0 active positions must never present itself as acting.
  const badge = inScope && activeCount > 0
    ? `<span class="shadow-badge badge-canary">CANARY ACTIVO</span>`
    : inScope
      ? `<span class="shadow-badge shadow-chip-warn">CANARY SIN POSICIONES ACTIVAS</span>`
      : `<span class="shadow-badge shadow-off">CANARY INACTIVO</span>`;
  const compatBlockers = status.calibrator_compatibility_blockers || [];
  const blockersNote = inScope && activeCount === 0
    ? `<div class="shadow-alert">Habilitado pero ninguna posición rutea${
        compatBlockers.length
          ? ` · blockers de calibrador: <span class="mono">${escapeHtml(compatBlockers.join(", "))}</span>`
          : ""
      }.</div>`
    : compatBlockers.length
      ? `<p class="meta-copy">Compatibilidad de calibrador (scope de slate): <span class="mono">${escapeHtml(compatBlockers.join(", "))}</span> — las posiciones se evalúan por competencia.</p>`
      : "";

  const cards = [
    ["Canary enabled", boolChip(status.canary_enabled)],
    ["In scope", boolChip(status.in_scope)],
    ["Scope", `<span class="mono">${escapeHtml(status.scope || "—")}</span>`],
    ["Routing policy", `<span class="mono">${escapeHtml(status.routing_policy || "—")}</span>`],
    ["Calibrator", `<span class="mono">${escapeHtml(status.calibrator_id || "—")}</span>`],
    ["Full activation", boolChip(status.full_activation)],
    ["Ticket integration", boolChip(status.ticket_integration)],
    ["Rollback available", boolChip(status.rollback_available)],
  ]
    .map(
      ([k, v]) =>
        `<div class="shadow-card"><span class="shadow-card-label">${escapeHtml(k)}</span><span class="shadow-card-value">${v}</span></div>`,
    )
    .join("");

  return `
    <div class="shadow-panel canary-panel">
      <div class="shadow-toprow">${badge}</div>
      <p class="dryrun-lead">Canary controlado: cambia solo las probabilidades efectivas servidas para las posiciones activas. <strong>El ticket aún NO usa canary.</strong></p>
      <div class="shadow-cards">${cards}</div>
      <div class="shadow-positions">
        <div class="shadow-positions-item"><span class="shadow-card-label">Allowed positions</span><span class="shadow-positions-value">${formatPositionRanges(status.allowed_positions)}</span></div>
        <div class="shadow-positions-item"><span class="shadow-card-label">Canary active positions</span><span class="shadow-positions-value">${formatPositionRanges(status.active_positions)}</span></div>
        <div class="shadow-positions-item"><span class="shadow-card-label">Blocked positions</span><span class="shadow-positions-value">${formatPositionRanges(status.blocked_positions)}</span></div>
      </div>
      ${blockersNote}
      <div class="shadow-alert">Ticket recommendation not using canary yet · Full activation OFF.</div>
      <p class="meta-copy shadow-foot">Canary controlado scope-limitado: no activa full slate, no integra ticket, no escribe DB. Rollback = flag OFF.</p>
    </div>
  `;
}
