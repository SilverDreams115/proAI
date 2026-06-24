// R6.0/R6.2 — Money Mode Release Candidate panel (executive-first).
//
// Pure render helper (returns an HTML string, no DOM/fetch) so it can be locked
// with Vitest. It leads with the play/don't-play decision and a plain action,
// then shows the aggressive/balanced/conservative tickets the system would build
// IN MEMORY, then a collapsible technical detail. When the decision is NO JUGAR
// the tickets are shown as non-recommended simulations — never as playable
// options. It never activates or changes the real ticket and writes nothing.
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
  no_simple: "Sin cobertura",
  double: "Doble",
  triple: "Triple",
  unknown: "—",
};

// Plain operator action derived from the decision (no logic change).
function decisionAction(decision) {
  if (decision.status === "NO_JUGAR") return "No comprar boleto";
  const label = TICKET_LABEL[decision.recommended_ticket] || decision.recommended_ticket;
  return label ? `Jugar boleto ${label.toLowerCase()}` : "Revisar boleto recomendado";
}

// Critical matches the most protective ticket still cannot cover.
function criticalCount(report) {
  const cons = (report.tickets || {}).conservative || {};
  return (cons.uncovered_no_simple_positions || []).length;
}

// Short, operator-friendly motivo. Never echoes raw backend wording such as
// "fijo forzado"; derived from the critical-match count.
function shortReason(report) {
  const d = report.decision;
  const n = criticalCount(report);
  if (d.status === "NO_JUGAR") {
    if (n > 0) {
      return `${n} partido${n === 1 ? "" : "s"} sin cobertura suficiente, incluso en el boleto conservador. El riesgo no es cubrible con las reglas actuales.`;
    }
    return "Riesgo no cubrible con las reglas actuales.";
  }
  return d.reason ? humanizeReason(d.reason) : "Boleto recomendado disponible.";
}

// Defensive copy sanitiser: a technical phrase never reaches the operator view.
function humanizeReason(text) {
  return String(text || "").replace(/fijo[s]? forzado[s]?/gi, "sin cobertura suficiente");
}

function countsTitle(ticket) {
  return `S${ticket.simple_count} · NS${ticket.no_simple_count} · D${ticket.double_count} · T${ticket.triple_count}`;
}

function ticketCard(key, ticket, noPlay) {
  if (!ticket) return "";
  let badge;
  if (noPlay) {
    badge = `<span class="badge-muted mm-sim-badge">Simulación · no recomendada</span>`;
  } else if (ticket.recommended) {
    badge = `<span class="badge-risk tone-ok">RECOMENDADO</span>`;
  } else {
    badge = `<span class="badge-muted">alternativa</span>`;
  }
  const cost =
    ticket.estimated_cost == null
      ? `no configurado`
      : `$${escapeHtml(ticket.estimated_cost)}`;
  const blockedClass = noPlay ? " mm-ticket-sim" : "";
  // Expanded, plain-language counts (siglas only as a tooltip).
  const counts = `
    <div class="mm-counts-grid" title="${escapeHtml(countsTitle(ticket))}">
      <div class="mm-count"><span class="mm-count-label">Simples</span><span class="mm-count-value">${escapeHtml(ticket.simple_count)}</span></div>
      <div class="mm-count"><span class="mm-count-label">Sin cobertura</span><span class="mm-count-value">${escapeHtml(ticket.no_simple_count)}</span></div>
      <div class="mm-count"><span class="mm-count-label">Dobles</span><span class="mm-count-value">${escapeHtml(ticket.double_count)}</span></div>
      <div class="mm-count"><span class="mm-count-label">Triples</span><span class="mm-count-value">${escapeHtml(ticket.triple_count)}</span></div>
    </div>`;
  return `
    <div class="shadow-card money-ticket money-ticket-${escapeHtml(key)}${blockedClass}">
      <div class="money-ticket-head">
        <span class="shadow-card-label">${escapeHtml(TICKET_LABEL[key] || key)}</span>
        ${badge}
      </div>
      ${counts}
      <div class="money-ticket-meta meta-copy">Combinaciones: ${escapeHtml(ticket.estimated_combinations)} · Costo: ${cost}</div>
    </div>`;
}

function ticketTechRow(key, ticket) {
  if (!ticket) return "";
  const cov = ticket.coverage_estimate || {};
  const uncovered = (ticket.uncovered_no_simple_positions || []).join(", ") || "ninguna";
  return `<tr>
    <td>${escapeHtml(TICKET_LABEL[key] || key)}</td>
    <td class="mono">${escapeHtml(countsTitle(ticket))}</td>
    <td>${escapeHtml(ticket.estimated_combinations)}</td>
    <td>${escapeHtml(cov.expected_correct ?? "—")}</td>
    <td>${escapeHtml(cov.jackpot_probability ?? "—")}</td>
    <td>${cov.target_met ? "sí" : "no"}</td>
    <td>${ticket.covers_all_no_simple ? "sí" : "no"}</td>
    <td class="meta-copy">${escapeHtml(uncovered)}</td>
  </tr>`;
}

export function renderMoneyModePanel(report) {
  if (!report || !report.slate || !report.decision) {
    return `<div class="empty-state">Sin Money Mode para la papeleta activa.</div>`;
  }
  const slate = report.slate;
  const d = report.decision;
  const tone = DECISION_TONE[d.status] || "muted";
  const label = DECISION_LABEL[d.status] || d.status;
  const noPlay = d.status === "NO_JUGAR";
  const badge = `<span class="shadow-badge badge-canary">MONEY MODE · SOLO LECTURA</span>`;

  const validation = report.validation || {};
  const livePredictions =
    validation.prediction_status === "live_available" ||
    (validation.warnings || []).includes("live_predictions_only");
  const liveNote = livePredictions
    ? `<p class="meta-copy money-live-note">Sin ticket persistido · Money Mode calculado en vivo.</p>`
    : "";

  // --- Executive hero --------------------------------------------------------
  const hero = `
    <div class="mm-hero mm-hero-${escapeHtml(tone)}">
      <div class="mm-hero-top">
        <span class="mm-hero-slate">${escapeHtml(slate.draw_code)} · ${escapeHtml(slate.match_count)} partidos</span>
      </div>
      <div class="mm-hero-decision">${escapeHtml(label)}</div>
      <div class="mm-hero-action"><strong>Acción:</strong> ${escapeHtml(decisionAction(d))}</div>
      <div class="mm-hero-reason">${escapeHtml(shortReason(report))}</div>
    </div>`;

  // --- Recommended ticket line ----------------------------------------------
  const recommended = d.recommended_ticket
    ? escapeHtml(TICKET_LABEL[d.recommended_ticket] || d.recommended_ticket)
    : "ninguno";
  const recommendedLine = noPlay
    ? `<div class="mm-recommended mm-recommended-none">Boleto recomendado: <strong>ninguno</strong> · Motivo: riesgo no cubrible.</div>`
    : `<div class="mm-recommended">Boleto recomendado: <strong>${recommended}</strong></div>`;

  // --- Ticket simulations ----------------------------------------------------
  const tickets = report.tickets || {};
  const ticketsHeading = noPlay
    ? `<div class="mm-tickets-heading">Simulaciones de boleto <span class="meta-copy">(ninguna recomendada hoy)</span></div>`
    : `<div class="mm-tickets-heading">Boletos</div>`;
  const cards = ["aggressive", "balanced", "conservative"]
    .map((k) => ticketCard(k, tickets[k], noPlay))
    .join("");

  // --- Collapsible technical detail -----------------------------------------
  const techRows = ["aggressive", "balanced", "conservative"]
    .map((k) => ticketTechRow(k, tickets[k]))
    .join("");
  const matchRows = (report.matches || [])
    .map((m) => {
      const reason = (m.reason || []).join(", ");
      const pick = (m.money_mode_pick || []).join("/") || "—";
      const noSimple = !m.simple_allowed
        ? `<span class="badge-risk tone-danger">Sin cobertura</span>`
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

  const technical = `
    <details class="mm-technical">
      <summary>Detalles técnicos</summary>
      <div class="shadow-positions">
        <div class="shadow-positions-item"><span class="shadow-card-label">Partidos sin cobertura permitida</span><span class="shadow-positions-value">${escapeHtml((report.do_not_simple_positions || []).join(", ") || "ninguno")}</span></div>
        <div class="shadow-positions-item"><span class="shadow-card-label">Revisión obligatoria</span><span class="shadow-positions-value">${escapeHtml((report.must_review_positions || []).join(", ") || "ninguna")}</span></div>
        <div class="shadow-positions-item"><span class="shadow-card-label">Canary influye en</span><span class="shadow-positions-value">${escapeHtml((report.canary_influence_positions || []).join(", ") || "ninguna")}</span></div>
        <div class="shadow-positions-item"><span class="shadow-card-label">Warnings</span><span class="shadow-positions-value">${escapeHtml((validation.warnings || []).join(", ") || "ninguno")}</span></div>
      </div>
      <table class="dryrun-table mm-tech-table">
        <thead><tr><th>Boleto</th><th>Conteos</th><th>Comb.</th><th>E[aciertos]</th><th>Jackpot</th><th>Target</th><th>Cubre</th><th>No cubiertos</th></tr></thead>
        <tbody>${techRows}</tbody>
      </table>
      <table class="dryrun-table money-mode-table">
        <thead><tr><th>#</th><th>Partido</th><th>Señal</th><th>Money pick</th><th>Riesgo</th><th>Justificación</th></tr></thead>
        <tbody>${matchRows}</tbody>
      </table>
      <p class="meta-copy">Motivo técnico completo: ${escapeHtml(humanizeReason(d.reason))}</p>
    </details>`;

  return `
    <div class="shadow-panel money-mode-panel">
      <div class="shadow-toprow">${badge}</div>
      ${hero}
      ${liveNote}
      ${recommendedLine}
      ${ticketsHeading}
      <div class="shadow-cards money-tickets">${cards}</div>
      ${technical}
      <div class="shadow-alert">Solo lectura · no activa ni cambia el ticket real · no escribe predicciones ni snapshots.</div>
    </div>
  `;
}
