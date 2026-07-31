import { escapeHtml, formatPercent } from "./helpers.js";

function neuralTone(shadow) {
  if (!shadow) return "muted";
  if (shadow.active && shadow.status === "ok") return shadow.top_pick_changed ? "warn" : "ok";
  return "warn";
}

// The backend always attaches a `neural_shadow` object, even when it has
// nothing to say — it carries a `status` explaining why. Rendering the table
// regardless produced a grid of em-dashes that read as a broken panel; these
// are the same states spelled out.
const INACTIVE_COPY = {
  no_active_model: {
    title: "Sin modelo neural activo",
    detail:
      "Ningún candidato ha sido promovido al slot de shadow, así que no hay nada que comparar contra el baseline.",
  },
  incompatible_artifact: {
    title: "Artefacto neural incompatible",
    detail:
      "El modelo activo no es pre-match shadow safe, o su vector de features ya no coincide. El shadow se apaga en vez de comparar contra otro esquema.",
  },
  error: {
    title: "El shadow neural falló",
    detail:
      "El modelo activo no pudo puntuar esta slate. El diagnóstico se apaga solo; las predicciones servidas no se ven afectadas.",
  },
};

function renderInactiveState(rows) {
  const shadows = rows.map((match) => match.prediction.neural_shadow);
  const status = shadows.find((shadow) => shadow?.status)?.status || "no_active_model";
  const copy = INACTIVE_COPY[status] || {
    title: "Neural shadow inactivo",
    detail: "El backend no reportó una comparación para esta slate.",
  };
  const reason = shadows.find((shadow) => shadow?.reason)?.reason;
  const runId = shadows.find((shadow) => shadow?.run_id)?.run_id;
  const meta = [
    `Estado <span class="mono">${escapeHtml(status)}</span>`,
    runId ? `run <span class="mono">${escapeHtml(String(runId).slice(0, 8))}</span>` : null,
    `${escapeHtml(rows.length)} posición(es) sin comparación`,
  ].filter(Boolean).join(" · ");
  return `
    <div class="shadow-panel neural-shadow-panel">
      <div class="shadow-toprow"><span class="shadow-badge badge-readonly">NEURAL SHADOW · SOLO LECTURA</span></div>
      <div class="empty-state">
        <strong>${escapeHtml(copy.title)}</strong>
        <p class="meta-copy">${escapeHtml(copy.detail)}</p>
        ${reason ? `<p class="meta-copy meta-copy-warn">${escapeHtml(reason)}</p>` : ""}
      </div>
      <p class="meta-copy">${meta}</p>
    </div>`;
}

export function renderNeuralShadowPanel(matches) {
  const rows = (matches || []).filter((match) => match?.prediction?.neural_shadow);
  if (!rows.length) {
    return `<div class="empty-state">Sin neural shadow para la slate seleccionada.</div>`;
  }
  const active = rows.filter((match) => match.prediction.neural_shadow.active).length;
  // Nothing active means every row would print em-dashes; say why instead.
  if (!active) {
    return renderInactiveState(rows);
  }
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
      <div class="shadow-toprow"><span class="shadow-badge badge-readonly">NEURAL SHADOW · SOLO LECTURA</span></div>
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
