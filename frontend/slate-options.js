// R6.4 — Slate options panel (always-present ticket options + pricing).
//
// Pure render helper (returns an HTML string, no DOM/fetch) so it can be locked
// with Vitest. It ALWAYS shows aggressive/balanced/conservative/manual options
// for a slate, but strictly respects the Money Mode decision: when NO JUGAR the
// options are non-recommended simulations and the action is "no comprar boleto".
// Cost is shown only when the price is verified; otherwise "precio no
// verificado" (never an invented amount, never $0).
import { escapeHtml } from "./helpers.js";

const ACTION_COPY = {
  NO_COMPRAR: "No comprar boleto",
  COMPRAR_BALANCEADA: "Comprar boleto balanceado",
  COMPRAR_CONSERVADORA: "Comprar boleto conservador",
  COMPRAR_AGRESIVA: "Comprar boleto agresivo",
  COMPRAR_CON_CAUTELA: "Comprar con cautela",
  REVISAR: "Revisar",
};

function costText(option) {
  if (option.price_status !== "verified" || option.estimated_cost == null) {
    return `<span class="opt-cost-unverified">precio no verificado</span>`;
  }
  return `$${escapeHtml(option.estimated_cost)} ${escapeHtml(option.currency || "MXN")}`;
}

function optionCard(option, noPlay) {
  const badge = option.recommended
    ? `<span class="badge-risk tone-ok">RECOMENDADA</span>`
    : noPlay
      ? `<span class="badge-muted opt-sim">Simulación · no recomendada</span>`
      : `<span class="badge-muted">alternativa</span>`;
  const blockedClass = option.recommended ? "" : noPlay ? " opt-card-sim" : "";
  return `
    <div class="shadow-card opt-card opt-${escapeHtml(option.key)}${blockedClass}">
      <div class="opt-head">
        <span class="shadow-card-label">${escapeHtml(option.name)}</span>
        ${badge}
      </div>
      <div class="opt-counts">Dobles: ${escapeHtml(option.double_count)} · Triples: ${escapeHtml(option.triple_count)}</div>
      <div class="opt-meta">Combinaciones: <strong>${escapeHtml(option.combinations)}</strong> · Costo: ${costText(option)}</div>
      <div class="opt-meta meta-copy">Riesgo: ${escapeHtml(option.risk_level)} · ${escapeHtml(option.reason)}</div>
    </div>`;
}

export function renderSlateOptionsPanel(report) {
  if (!report || !Array.isArray(report.options)) {
    return `<div class="empty-state">Sin opciones de boleto para la papeleta activa.</div>`;
  }
  const noPlay = report.money_mode_decision === "NO_JUGAR";
  const action = ACTION_COPY[report.recommended_action] || report.recommended_action;
  const badge = `<span class="shadow-badge badge-canary">OPCIONES · SOLO LECTURA</span>`;
  const actionTone = noPlay ? "danger" : "ok";

  const cards = report.options.map((o) => optionCard(o, noPlay)).join("");

  const pricingNote = report.pricing_verified
    ? ""
    : `<p class="meta-copy opt-pricing-note">Precio no verificado: el costo estimado no se muestra hasta validar el precio oficial (TuLotero / Pronósticos).</p>`;

  return `
    <div class="shadow-panel slate-options-panel">
      <div class="shadow-toprow">${badge}</div>
      <div class="opt-action opt-action-${actionTone}">
        <strong>Acción recomendada:</strong> ${escapeHtml(action)}
        ${noPlay ? `<span class="badge-risk tone-danger">NO JUGAR</span>` : ""}
      </div>
      ${pricingNote}
      <div class="shadow-cards opt-cards">${cards}</div>
      <div class="shadow-alert">Opciones de referencia · solo lectura · no activa ni compra ningún boleto.</div>
    </div>
  `;
}
