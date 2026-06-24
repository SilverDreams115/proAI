// R6.1/R6.2 — Operational Money Mode Status panel (executive-first).
//
// Pure render helper (returns an HTML string, no DOM/fetch) so it can be locked
// with Vitest. It leads with the single thing an operator needs — play or don't
// play TODAY — and then a per-slate executive card. It NEVER hides a NO JUGAR or
// the blocked-slate count, and changes nothing: strictly a read-only surface.
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

const PREDICTION_LABEL = {
  persisted: "Predicciones listas",
  live_available: "Predicción en vivo",
  pending: "Sin predicción",
  missing: "Sin datos",
};

function decisionLabel(status) {
  return DECISION_LABEL[status] || status || "—";
}

// Short, operator-friendly motivo. Never echoes raw backend wording such as
// "fijo forzado"; it is derived from the critical-match count.
function shortReason(slate) {
  const n = Number(slate.critical_uncovered_count || 0);
  if (slate.decision === "NO_JUGAR") {
    if (n > 0) {
      return `${n} partido${n === 1 ? "" : "s"} sin cobertura suficiente.`;
    }
    return "Riesgo no cubrible con las reglas actuales.";
  }
  return slate.recommended_ticket
    ? `Boleto recomendado: ${slate.recommended_ticket}.`
    : "Revisar detalle de la slate.";
}

function slateCard(slate) {
  const tone = DECISION_TONE[slate.decision] || "muted";
  const action = slate.recommended_action || (slate.decision === "NO_JUGAR" ? "No comprar boleto" : "Revisar");
  const ready = slate.money_mode_ready
    ? `<span class="badge-risk tone-ok">datos listos</span>`
    : `<span class="badge-risk tone-danger">datos incompletos</span>`;
  const prediction = PREDICTION_LABEL[slate.prediction_status] || slate.prediction_status || "—";
  return `
    <div class="ops-slate-card ops-slate-${escapeHtml(tone)}">
      <div class="ops-slate-top">
        <span class="ops-slate-code">${escapeHtml(slate.draw_code)}</span>
        <span class="meta-copy">${escapeHtml(slate.week_type)} · ${escapeHtml(slate.match_count)} partidos</span>
      </div>
      <div class="ops-slate-decision badge-risk tone-${escapeHtml(tone)}">${escapeHtml(decisionLabel(slate.decision))}</div>
      <div class="ops-slate-action"><strong>Acción:</strong> ${escapeHtml(action)}</div>
      <div class="ops-slate-reason">${escapeHtml(shortReason(slate))}</div>
      <div class="ops-slate-foot meta-copy">${ready} · ${escapeHtml(prediction)}</div>
    </div>`;
}

export function renderOperationalMoneyModeStatusPanel(status) {
  if (!status || !Array.isArray(status.slates)) {
    return `<div class="empty-state">Sin estado operativo de Money Mode.</div>`;
  }
  const badge = `<span class="shadow-badge badge-canary">OPERATIVO · SOLO LECTURA</span>`;

  if (status.active_slate_count === 0) {
    return `
      <div class="shadow-panel ops-money-panel">
        <div class="shadow-toprow">${badge}</div>
        <div class="ops-hero ops-hero-muted">
          <div class="ops-hero-headline">HOY: SIN QUINIELA ACTIVA</div>
          <div class="ops-hero-action">No hay nada que decidir ahora mismo.</div>
        </div>
        <div class="shadow-alert">Solo lectura · sin escrituras · esperando una nueva quiniela activa/próxima.</div>
      </div>`;
  }

  const playable = Number(status.playable_slate_count || 0);
  const active = Number(status.active_slate_count || 0);
  const blocked = Number(status.blocked_slate_count ?? active - playable);
  const noPlay = playable === 0;

  const headline = noPlay ? "HOY: NO JUGAR" : "HOY: HAY SLATES JUGABLES";
  const action = noPlay
    ? "Acción recomendada: no comprar boleto hoy."
    : "Acción recomendada: revisar el boleto recomendado por slate.";
  const heroTone = noPlay ? "danger" : "ok";

  const hero = `
    <div class="ops-hero ops-hero-${heroTone}">
      <div class="ops-hero-headline">${escapeHtml(headline)}</div>
      <div class="ops-hero-counts">${escapeHtml(playable)} de ${escapeHtml(active)} slates jugables · <strong>${escapeHtml(blocked)} bloqueada${blocked === 1 ? "" : "s"}</strong></div>
      <div class="ops-hero-action">${escapeHtml(action)}</div>
    </div>`;

  const cards = status.slates.map(slateCard).join("");

  const generated = status.generated_at
    ? `<p class="meta-copy ops-updated">Última validación: ${escapeHtml(status.generated_at)}</p>`
    : "";

  return `
    <div class="shadow-panel ops-money-panel">
      <div class="shadow-toprow">${badge}</div>
      ${hero}
      <div class="ops-slate-grid">${cards}</div>
      ${generated}
      <div class="shadow-alert">Estado operativo de solo lectura · no activa ni cambia el ticket real · no escribe nada. Un NO JUGAR nunca se oculta.</div>
    </div>
  `;
}
