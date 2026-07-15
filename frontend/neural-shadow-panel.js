import { escapeHtml, formatPercent } from "./helpers.js";

function neuralTone(shadow) {
  if (!shadow) return "muted";
  if (shadow.active && shadow.status === "ok") return shadow.top_pick_changed ? "warn" : "ok";
  return "warn";
}

export function renderNeuralShadowPanel(matches) {
  const rows = (matches || []).filter((match) => match?.prediction?.neural_shadow);
  if (!rows.length) {
    return `<div class="empty-state">Sin neural shadow para la slate seleccionada.</div>`;
  }
  const active = rows.filter((match) => match.prediction.neural_shadow.active).length;
  const changed = rows.filter((match) => match.prediction.neural_shadow.top_pick_changed).length;
  const maxDelta = rows.reduce((max, match) => Math.max(max, Number(match.prediction.neural_shadow.max_abs_delta || 0)), 0);
  const runId = rows[0]?.prediction?.neural_shadow?.run_id || "—";
  const body = rows.map((match) => {
    const shadow = match.prediction.neural_shadow;
    const probs = shadow.probabilities || {};
    const delta = shadow.probability_delta || {};
    return `
      <tr class="tone-${neuralTone(shadow)}">
        <td class="mono">${escapeHtml(match.position)}</td>
        <td>${escapeHtml(match.prediction.home_team_name)} vs ${escapeHtml(match.prediction.away_team_name)}</td>
        <td>${escapeHtml(shadow.baseline_top_pick || "—")} → <strong>${escapeHtml(shadow.top_pick || "—")}</strong></td>
        <td>L ${formatPercent(probs.L)} · E ${formatPercent(probs.E)} · V ${formatPercent(probs.V)}</td>
        <td class="mono">ΔL ${escapeHtml(delta.L ?? "—")} · ΔE ${escapeHtml(delta.E ?? "—")} · ΔV ${escapeHtml(delta.V ?? "—")}</td>
      </tr>`;
  }).join("");
  return `
    <div class="shadow-panel neural-shadow-panel">
      <div class="shadow-toprow"><span class="shadow-badge badge-canary">NEURAL SHADOW · SOLO LECTURA</span></div>
      <div class="shadow-positions">
        <div class="shadow-positions-item"><span class="shadow-card-label">Activo</span><span class="shadow-positions-value">${escapeHtml(active)} / ${escapeHtml(rows.length)}</span></div>
        <div class="shadow-positions-item"><span class="shadow-card-label">Pick cambiado</span><span class="shadow-positions-value">${escapeHtml(changed)}</span></div>
        <div class="shadow-positions-item"><span class="shadow-card-label">Δ máx</span><span class="shadow-positions-value">${escapeHtml(maxDelta.toFixed(4))}</span></div>
      </div>
      <p class="meta-copy">Run <span class="mono">${escapeHtml(String(runId).slice(0, 8))}</span>. No reemplaza probabilidades, pick ni ticket.</p>
      <table class="dryrun-table neural-shadow-table">
        <thead><tr><th>#</th><th>Partido</th><th>Pick</th><th>Neural L/E/V</th><th>Delta vs baseline</th></tr></thead>
        <tbody>${body}</tbody>
      </table>
    </div>`;
}
