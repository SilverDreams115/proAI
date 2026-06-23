// R5.7 — Ticket Canary Dry-run panel (Diagnóstico).
//
// Pure render helper (returns an HTML string, no DOM/fetch) so it can be locked
// with Vitest. Shows the current ticket vs the ticket the optimizer WOULD
// produce from the canary effective probabilities. It is strictly a dry-run:
// it never activates or changes the real ticket and writes nothing.
import { escapeHtml } from "./helpers.js";

const RISK_TONE = { lower: "ok", same: "muted", mixed: "warn", higher: "warn" };
const PICK_LABEL = {
  simple: "Simple",
  no_simple: "NO SIMPLE",
  double: "Doble",
  triple: "Triple",
};

function pickLabel(t) {
  return PICK_LABEL[t] || t || "—";
}

function counts(t) {
  const ns = t.no_simple_count ? ` · NS ${t.no_simple_count}` : "";
  return `S ${t.simple_count}${ns} · D ${t.double_count} · T ${t.triple_count}`;
}

export function renderTicketCanaryDryRunPanel(report) {
  if (!report || !report.slate) {
    return `<div class="empty-state">Sin dry-run de ticket canary para la papeleta activa.</div>`;
  }
  const s = report.summary || {};
  const cur = s.current_ticket || {};
  const can = s.canary_ticket || {};
  const risk = s.risk_delta || "same";
  const badge = `<span class="shadow-badge badge-canary">DRY-RUN · TICKET NO ACTIVO</span>`;

  const hasPersisted = (cur.simple_count + (cur.no_simple_count || 0) + cur.double_count + cur.triple_count) > 0;
  const noTicketNote = report.slate.match_count && !hasPersisted
    ? `<p class="meta-copy">Current ticket: sin ticket persistido · Canary simulated ticket: disponible desde predicciones live.</p>`
    : "";

  const cards = [
    ["Simples actuales", String(cur.simple_count ?? "—")],
    ["Simples canary", String(can.simple_count ?? "—")],
    ["Dobles actuales", String(cur.double_count ?? "—")],
    ["Dobles canary", String(can.double_count ?? "—")],
    ["Triples actuales", String(cur.triple_count ?? "—")],
    ["Triples canary", String(can.triple_count ?? "—")],
  ]
    .map(
      ([k, v]) =>
        `<div class="shadow-card"><span class="shadow-card-label">${escapeHtml(k)}</span><span class="shadow-card-value">${escapeHtml(v)}</span></div>`,
    )
    .join("");

  const rows = (report.matches || [])
    .map((m) => {
      const changed = m.changed
        ? `<span class="badge-risk tone-warn">cambia</span>`
        : `<span class="badge-muted">igual</span>`;
      const reason = (m.reason || []).join(", ");
      const curSel = (m.current_selection || []).join("/");
      const canSel = (m.canary_selection || []).join("/");
      return `<tr${m.changed ? ' class="row-changed"' : ""}>
        <td>${escapeHtml(m.position)}</td>
        <td>${escapeHtml(m.match)}</td>
        <td>${escapeHtml(pickLabel(m.current_pick_type))} <span class="mono">${escapeHtml(curSel)}</span></td>
        <td>${escapeHtml(pickLabel(m.canary_pick_type))} <span class="mono">${escapeHtml(canSel)}</span></td>
        <td>${changed}</td>
        <td class="meta-copy">${escapeHtml(reason)}</td>
      </tr>`;
    })
    .join("");

  return `
    <div class="shadow-panel canary-panel">
      <div class="shadow-toprow">${badge}</div>
      <p class="dryrun-lead">Ticket actual vs ticket canary (in-memory). No modifica el ticket real ni escribe snapshots.</p>
      ${noTicketNote}
      <div class="shadow-cards">${cards}</div>
      <div class="shadow-positions">
        <div class="shadow-positions-item"><span class="shadow-card-label">Ticket actual</span><span class="shadow-positions-value">${escapeHtml(counts(cur))}</span></div>
        <div class="shadow-positions-item"><span class="shadow-card-label">Ticket canary</span><span class="shadow-positions-value">${escapeHtml(counts(can))}</span></div>
        <div class="shadow-positions-item"><span class="shadow-card-label">Posiciones que cambian</span><span class="shadow-positions-value">${escapeHtml((s.changed_positions || []).join(", ") || "ninguna")}</span></div>
        <div class="shadow-positions-item"><span class="shadow-card-label">Simples removidos</span><span class="shadow-positions-value">${escapeHtml((s.simple_removed_positions || []).join(", ") || "ninguno")}</span></div>
        <div class="shadow-positions-item"><span class="shadow-card-label">Nuevos dobles</span><span class="shadow-positions-value">${escapeHtml((s.new_double_positions || []).join(", ") || "ninguno")}</span></div>
        <div class="shadow-positions-item"><span class="shadow-card-label">Nuevos triples</span><span class="shadow-positions-value">${escapeHtml((s.new_triple_positions || []).join(", ") || "ninguno")}</span></div>
        <div class="shadow-positions-item"><span class="shadow-card-label">Riesgo</span><span class="shadow-positions-value badge-risk tone-${RISK_TONE[risk] || "muted"}">${escapeHtml(risk)}</span></div>
      </div>
      <table class="dryrun-table">
        <thead><tr><th>#</th><th>Partido</th><th>Actual</th><th>Canary</th><th>Cambio</th><th>Razón</th></tr></thead>
        <tbody>${rows}</tbody>
      </table>
      <div class="shadow-alert">DRY-RUN: el optimizer/ticket real NO usa canary · No se escriben snapshots · Full activation OFF.</div>
    </div>
  `;
}
