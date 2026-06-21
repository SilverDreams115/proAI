// R5.6-A — Team Rating Activation Readiness (read-only, diagnostic only).
//
// Pure render helpers (return HTML strings, no DOM/fetch) so they can be locked
// with Vitest. Reports whether the technical blockers before a minimal canary
// are cleared. It never activates the gate or changes real predictions, picks,
// tickets or probabilities.
import { escapeHtml } from "./helpers.js";
import { formatPositionRanges } from "./team-rating-shadow.js";

const STATUS_CLASS = {
  pass: "shadow-chip-ok",
  partial_pass: "readiness-chip-partial",
  blocking_until_canary: "readiness-chip-canary",
  blocking_until_full_activation: "readiness-chip-canary",
  block_for_affected_matches: "shadow-chip-warn",
  blocking: "readiness-chip-block",
};

function statusChip(status) {
  const cls = STATUS_CLASS[status] || "";
  return `<span class="shadow-chip ${cls}">${escapeHtml(status)}</span>`;
}

function boolChip(value) {
  return `<span class="shadow-chip ${value ? "shadow-chip-ok" : "shadow-chip-warn"}">${value ? "SÍ" : "NO"}</span>`;
}

function fmtDelta(value) {
  return typeof value === "number" ? value.toFixed(3) : "—";
}

function card(label, valueHtml) {
  return `<div class="shadow-card">
    <span class="shadow-card-label">${escapeHtml(label)}</span>
    <span class="shadow-card-value">${valueHtml}</span>
  </div>`;
}

export function renderTeamRatingActivationReadinessPanel(readiness) {
  if (!readiness || !readiness.dry_run_summary) {
    return `<div class="empty-state">Sin datos de activation readiness para la papeleta activa.</div>`;
  }
  const dr = readiness.dry_run_summary;
  const cal = readiness.calibrator || {};
  const plan = readiness.canary_plan || {};
  const total = dr.total_matches;

  const cards = [
    card("Ready for canary", boolChip(readiness.ready_for_canary)),
    card("Ready for full activation", boolChip(readiness.ready_for_full_activation)),
    card("Calibrator", `<span class="mono">${escapeHtml(cal.approval_status || "—")}</span>`),
    card("Productive available", boolChip(cal.productive_available)),
    card("Would route", `${dr.would_route}/${total}`),
    card("Changed top pick", `${dr.changed_top_pick_count}/${total}`),
    card("Max Δ probability", fmtDelta(dr.max_probability_delta)),
    card("Routing policy", `<span class="mono">${escapeHtml(readiness.target_activation?.routing_policy || "—")}</span>`),
  ].join("");

  const checksRows = (readiness.readiness_checks || [])
    .map((c) => {
      const count = c.count == null ? "" : ` <span class="readiness-count">(${escapeHtml(c.count)})</span>`;
      return `<tr>
        <td class="mono">${escapeHtml(c.check)}</td>
        <td>${statusChip(c.status)}${count}</td>
        <td class="readiness-details">${escapeHtml(c.details || "")}</td>
      </tr>`;
    })
    .join("");

  const rollback = (plan.rollback || [])
    .map((step, i) => `<li><span class="readiness-step">${i + 1}</span> ${escapeHtml(step)}</li>`)
    .join("");

  return `
    <div class="shadow-panel readiness-panel">
      <div class="shadow-toprow">
        <span class="shadow-badge readiness-badge">READINESS · NO ACTIVO</span>
      </div>
      <p class="dryrun-lead">No activa el gate. Solo valida condiciones para un canary mínimo. <strong>No modifica predicción, pick ni ticket.</strong></p>
      <div class="shadow-cards">${cards}</div>
      <div class="shadow-positions">
        <div class="shadow-positions-item"><span class="shadow-card-label">Canary allowed positions</span><span class="shadow-positions-value">${formatPositionRanges(plan.canary_allowed_matches)}</span></div>
        <div class="shadow-positions-item"><span class="shadow-card-label">Blocked positions</span><span class="shadow-positions-value">${formatPositionRanges(plan.blocked_matches)}</span></div>
      </div>
      <div class="shadow-table-wrap">
        <table class="shadow-table">
          <thead><tr><th>Check</th><th>Status</th><th>Details</th></tr></thead>
          <tbody>${checksRows}</tbody>
        </table>
      </div>
      <div class="readiness-rollback">
        <span class="shadow-card-label">Rollback plan</span>
        <ol class="readiness-rollback-list">${rollback}</ol>
      </div>
      <p class="meta-copy shadow-foot">Readiness check: no activa el gate, no cambia probabilidades reales, picks ni tickets. <strong>Ready for canary: ${readiness.ready_for_canary ? "SÍ" : "NO"}</strong>.</p>
    </div>
  `;
}
