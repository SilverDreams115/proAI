// R6.0 — Money Mode Release Candidate panel (Diagnóstico / final).
//
// Pure render helper (returns an HTML string, no DOM/fetch) so it can be locked
// with Vitest. It surfaces the operational play/don't-play decision plus the
// aggressive/balanced/conservative tickets the system would build IN MEMORY.
// It never activates or changes the real ticket and writes nothing.
import { escapeHtml } from "./helpers.js";

const DECISION_TONE = {
  JUGAR_BALANCEADO: "ok",
  JUGAR_SOLO_BALANCEADO: "ok",
  JUGAR_SOLO_CONSERVADOR: "warn",
  JUGAR_SOLO_AGRESIVO: "warn",
  JUGAR_CON_CAUTELA: "warn",
  NO_JUGAR: "danger",
};

const DECISION_LABEL = {
  JUGAR_BALANCEADO: "JUGAR · BALANCEADO",
  JUGAR_SOLO_BALANCEADO: "JUGAR SOLO BALANCEADO",
  JUGAR_SOLO_CONSERVADOR: "JUGAR SOLO CONSERVADOR",
  JUGAR_SOLO_AGRESIVO: "JUGAR SOLO AGRESIVO",
  JUGAR_CON_CAUTELA: "JUGAR CON CAUTELA",
  NO_JUGAR: "NO JUGAR",
};

const TICKET_LABEL = {
  aggressive: "Agresivo",
  balanced: "Balanceado",
  conservative: "Conservador",
};

const PICK_TYPE_LABEL = {
  simple: "Simple",
  no_simple: "NO SIMPLE",
  double: "Doble",
  triple: "Triple",
  unknown: "—",
};

function ticketCard(key, ticket) {
  if (!ticket) return "";
  const star = ticket.recommended
    ? `<span class="badge-risk tone-ok">RECOMENDADO</span>`
    : "";
  const cov = ticket.coverage_estimate || {};
  const cost =
    ticket.estimated_cost == null
      ? `n/d <span class="meta-copy">(${escapeHtml(ticket.cost_note || "costo no configurado")})</span>`
      : `$${escapeHtml(ticket.estimated_cost)}`;
  const uncovered = (ticket.uncovered_no_simple_positions || []).join(", ") || "ninguna";
  return `
    <div class="shadow-card money-ticket money-ticket-${escapeHtml(key)}">
      <div class="money-ticket-head">
        <span class="shadow-card-label">${escapeHtml(TICKET_LABEL[key] || key)}</span>
        ${star}
        <span class="badge-risk tone-${escapeHtml(ticket.risk_level === "low" ? "ok" : ticket.risk_level === "medium" ? "warn" : "danger")}">riesgo ${escapeHtml(ticket.risk_level)}</span>
      </div>
      <div class="money-ticket-counts">S ${escapeHtml(ticket.simple_count)} · NS ${escapeHtml(ticket.no_simple_count)} · D ${escapeHtml(ticket.double_count)} · T ${escapeHtml(ticket.triple_count)}</div>
      <div class="money-ticket-meta">Combinaciones: <strong>${escapeHtml(ticket.estimated_combinations)}</strong> · Costo: ${cost}</div>
      <div class="money-ticket-meta">Cubre NO SIMPLE: <strong>${ticket.covers_all_no_simple ? "sí" : "no"}</strong> · No cubiertos: ${escapeHtml(uncovered)}</div>
      <div class="money-ticket-meta meta-copy">E[aciertos] ${escapeHtml(cov.expected_correct ?? "—")} · jackpot ${escapeHtml(cov.jackpot_probability ?? "—")} · target ${cov.target_met ? "sí" : "no"}</div>
    </div>`;
}

export function renderMoneyModePanel(report) {
  if (!report || !report.slate || !report.decision) {
    return `<div class="empty-state">Sin Money Mode para la papeleta activa.</div>`;
  }
  const slate = report.slate;
  const d = report.decision;
  const tone = DECISION_TONE[d.status] || "muted";
  const label = DECISION_LABEL[d.status] || d.status;
  const badge = `<span class="shadow-badge badge-canary">MONEY MODE RC · READ-ONLY</span>`;

  const validation = report.validation || {};
  const livePredictions =
    validation.prediction_status === "live_available" ||
    (validation.warnings || []).includes("live_predictions_only");
  const liveNote = livePredictions
    ? `<p class="meta-copy money-live-note">Sin ticket persistido · Money Mode calculado en vivo (predicciones live).</p>`
    : "";

  const tickets = report.tickets || {};
  const cards = ["aggressive", "balanced", "conservative"]
    .map((k) => ticketCard(k, tickets[k]))
    .join("");

  const rows = (report.matches || [])
    .map((m) => {
      const reason = (m.reason || []).join(", ");
      const pick = (m.money_mode_pick || []).join("/") || "—";
      const noSimple = !m.simple_allowed
        ? `<span class="badge-risk tone-danger">NO SIMPLE</span>`
        : `<span class="badge-muted">simple ok</span>`;
      const canary = m.canary_active ? `<span class="badge-canary">canary</span>` : "";
      return `<tr${!m.simple_allowed ? ' class="row-changed"' : ""}>
        <td>${escapeHtml(m.position)}</td>
        <td>${escapeHtml(m.match)}</td>
        <td>${noSimple} ${canary}</td>
        <td>${escapeHtml(PICK_TYPE_LABEL[m.money_mode_pick_type] || m.money_mode_pick_type)} <span class="mono">${escapeHtml(pick)}</span></td>
        <td>${escapeHtml(m.risk)}</td>
        <td class="meta-copy">${escapeHtml(reason)}</td>
      </tr>`;
    })
    .join("");

  const recommended = d.recommended_ticket
    ? escapeHtml(TICKET_LABEL[d.recommended_ticket] || d.recommended_ticket)
    : "ninguno";

  return `
    <div class="shadow-panel money-mode-panel">
      <div class="shadow-toprow">${badge}</div>
      <div class="money-decision money-decision-${escapeHtml(tone)}">
        <span class="money-decision-label badge-risk tone-${escapeHtml(tone)}">${escapeHtml(label)}</span>
        <span class="money-decision-confidence">confianza: ${escapeHtml(d.confidence)}</span>
      </div>
      <p class="money-decision-reason">${escapeHtml(d.reason)}</p>
      ${liveNote}
      <div class="shadow-positions">
        <div class="shadow-positions-item"><span class="shadow-card-label">Boleto recomendado</span><span class="shadow-positions-value">${recommended}</span></div>
        <div class="shadow-positions-item"><span class="shadow-card-label">Partidos NO SIMPLE</span><span class="shadow-positions-value">${escapeHtml((report.do_not_simple_positions || []).join(", ") || "ninguno")}</span></div>
        <div class="shadow-positions-item"><span class="shadow-card-label">Revisión obligatoria</span><span class="shadow-positions-value">${escapeHtml((report.must_review_positions || []).join(", ") || "ninguna")}</span></div>
        <div class="shadow-positions-item"><span class="shadow-card-label">Canary influye en</span><span class="shadow-positions-value">${escapeHtml((report.canary_influence_positions || []).join(", ") || "ninguna")}</span></div>
      </div>
      <div class="shadow-cards money-tickets">${cards}</div>
      <table class="dryrun-table money-mode-table">
        <thead><tr><th>#</th><th>Partido</th><th>Señal</th><th>Money pick</th><th>Riesgo</th><th>Justificación</th></tr></thead>
        <tbody>${rows}</tbody>
      </table>
      <div class="shadow-alert">MONEY MODE RC: no activa el ticket real · no escribe snapshots ni predicciones · full activation OFF · ticket integration OFF.</div>
    </div>
  `;
}
