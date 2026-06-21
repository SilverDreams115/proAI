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
  const on = Boolean(status.canary_enabled && status.in_scope);
  const badge = on
    ? `<span class="shadow-badge badge-canary">CANARY ACTIVO</span>`
    : `<span class="shadow-badge shadow-off">CANARY INACTIVO</span>`;

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
      <div class="shadow-alert">Ticket recommendation not using canary yet · Full activation OFF.</div>
      <p class="meta-copy shadow-foot">Canary controlado scope-limitado: no activa full slate, no integra ticket, no escribe DB. Rollback = flag OFF.</p>
    </div>
  `;
}
