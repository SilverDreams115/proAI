// R6.1 — Operational Money Mode Status panel.
//
// Pure render helper (returns an HTML string, no DOM/fetch) so it can be locked
// with Vitest. It shows, for every active/upcoming slate, the operational
// JUGAR / NO JUGAR decision and whether the slate is Money-Mode-ready. It NEVER
// hides a NO JUGAR or the blocked-slate count, and changes nothing — strictly a
// read-only status surface.
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

function decisionLabel(status) {
  return DECISION_LABEL[status] || status || "—";
}

export function renderOperationalMoneyModeStatusPanel(status) {
  if (!status || !Array.isArray(status.slates)) {
    return `<div class="empty-state">Sin estado operativo de Money Mode.</div>`;
  }
  const badge = `<span class="shadow-badge badge-canary">OPERATIONAL · READ-ONLY</span>`;

  if (status.active_slate_count === 0) {
    return `
      <div class="shadow-panel ops-money-panel">
        <div class="shadow-toprow">${badge}</div>
        <div class="empty-state">No hay slates activas/próximas en este momento.</div>
        <div class="shadow-alert">Read-only · sin escrituras · esperando una nueva quiniela activa/próxima.</div>
      </div>`;
  }

  const summary = `
    <div class="shadow-positions ops-summary">
      <div class="shadow-positions-item"><span class="shadow-card-label">Slates activas</span><span class="shadow-positions-value">${escapeHtml(status.active_slate_count)}</span></div>
      <div class="shadow-positions-item"><span class="shadow-card-label">Jugables</span><span class="shadow-positions-value">${escapeHtml(status.playable_slate_count)}</span></div>
      <div class="shadow-positions-item"><span class="shadow-card-label">Bloqueadas (NO JUGAR)</span><span class="shadow-positions-value badge-risk tone-danger">${escapeHtml(status.blocked_slate_count)}</span></div>
    </div>`;

  const rows = status.slates
    .map((s) => {
      const tone = DECISION_TONE[s.decision] || "muted";
      const ready = s.money_mode_ready
        ? `<span class="badge-risk tone-ok">ready</span>`
        : `<span class="badge-risk tone-danger">no ready</span>`;
      const warnings = (s.warnings || []).join(", ") || "—";
      return `<tr class="ops-row ops-row-${escapeHtml(tone)}">
        <td><strong>${escapeHtml(s.draw_code)}</strong><br><span class="meta-copy">${escapeHtml(s.week_type)} · ${escapeHtml(s.match_count)} partidos</span></td>
        <td><span class="badge-risk tone-${escapeHtml(tone)} ops-decision">${escapeHtml(decisionLabel(s.decision))}</span></td>
        <td class="meta-copy">${escapeHtml(s.reason)}</td>
        <td>${ready}<br><span class="meta-copy">${escapeHtml(s.prediction_status || "—")}</span></td>
        <td class="meta-copy">${escapeHtml(warnings)}</td>
      </tr>`;
    })
    .join("");

  const generated = status.generated_at
    ? `<p class="meta-copy">Última validación: ${escapeHtml(status.generated_at)}</p>`
    : "";

  return `
    <div class="shadow-panel ops-money-panel">
      <div class="shadow-toprow">${badge}</div>
      <p class="dryrun-lead">Decisión operativa por slate activa/próxima. JUGAR solo si Money Mode lo permite; un NO JUGAR nunca se oculta.</p>
      ${generated}
      ${summary}
      <table class="dryrun-table ops-money-table">
        <thead><tr><th>Slate</th><th>Decisión</th><th>Motivo</th><th>Money Mode</th><th>Warnings</th></tr></thead>
        <tbody>${rows}</tbody>
      </table>
      <div class="shadow-alert">OPERATIONAL STATUS: solo lectura · no activa ni cambia el ticket real · no escribe snapshots ni predicciones.</div>
    </div>
  `;
}
