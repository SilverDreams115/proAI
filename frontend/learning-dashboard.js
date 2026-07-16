// R7.0 — Seguimiento y aprendizaje (completed-slate learning loop, read-only).
//
// Pure render helper (returns an HTML string, no DOM/fetch) so it can be locked
// with Vitest. It shows, per completed slate, its learning state
// (comparable / pendiente / conflicto), predictions, results, hits/errors when
// comparable, and the next action — never hiding blockers and never auto-switching
// the active slate. It writes nothing.
import { escapeHtml } from "./helpers.js";

const STATE_META = {
  closed_comparable: { label: "comparable", tone: "ok" },
  closed_partial_results: { label: "parcial", tone: "warn" },
  closed_pending_results: { label: "pendiente de resultados oficiales", tone: "warn" },
  closed_conflict: { label: "conflicto", tone: "danger" },
  archived_no_predictions: { label: "sin predicciones", tone: "muted" },
  active: { label: "activa", tone: "muted" },
  upcoming: { label: "próxima", tone: "muted" },
};

function metaOf(slate) {
  return STATE_META[slate.state] || { label: escapeHtml(slate.state || "?"), tone: "muted" };
}

function nextActionOf(slate) {
  switch (slate.state) {
    case "closed_comparable":
      return "Listo para aprender: scoring y atribución de errores disponibles.";
    case "closed_partial_results":
      return "Faltan resultados oficiales (parcial); completar antes de aprender.";
    case "closed_pending_results":
      return "Pendiente de resultados oficiales — aprendizaje bloqueado.";
    case "closed_conflict":
      return "Resolver conflicto de resultados entre fuentes.";
    case "archived_no_predictions":
      return "Sin predicciones: no aporta al aprendizaje.";
    default:
      return "Sin acción de aprendizaje.";
  }
}

function slateCard(slate) {
  const m = metaOf(slate);
  const comparable = slate.comparable ? "sí" : "no";
  const blockers =
    Array.isArray(slate.blockers) && slate.blockers.length
      ? slate.blockers.join(" · ")
      : "ninguno";
  return `
    <div class="trv-card trv-${escapeHtml(m.tone)}">
      <div class="trv-top">
        <span class="trv-code">${escapeHtml(slate.draw_code)}</span>
        <span class="badge-risk tone-${escapeHtml(m.tone)}">${escapeHtml(m.label)}</span>
      </div>
      <div class="trv-line">Predicciones: <strong>${escapeHtml(slate.prediction_count)}/${escapeHtml(slate.match_count)}</strong></div>
      <div class="trv-line">Resultados canónicos: <strong>${escapeHtml(slate.canonical_result_count)}/${escapeHtml(slate.match_count)}</strong> · conflictos: ${escapeHtml(slate.conflicts)}</div>
      <div class="trv-line">Comparable: <strong>${escapeHtml(comparable)}</strong> · lineage: ${escapeHtml(slate.classification)}</div>
      ${
        slate.hits != null && slate.total != null
          ? `<div class="trv-line">Aciertos: <strong>${escapeHtml(slate.hits)}/${escapeHtml(slate.total)}</strong></div>`
          : ""
      }
      <div class="trv-missing meta-copy"><strong>Bloqueos:</strong> ${escapeHtml(blockers)}</div>
      <div class="trv-action meta-copy"><strong>Acción:</strong> ${escapeHtml(nextActionOf(slate))}</div>
    </div>`;
}

function renderEconomicShadow(scores) {
  const summary = scores?.economic_shadow_summary?.strategies || {};
  const rows = ["model_top1", "model_top2", "full_cover"]
    .map((key) => {
      const item = summary[key];
      if (!item) return "";
      const roi = item.simulated_roi == null ? "—" : `${Math.round(Number(item.simulated_roi) * 100)}%`;
      return `
        <tr>
          <td>${escapeHtml(key)}</td>
          <td>${escapeHtml(item.perfect_count || 0)}/${escapeHtml(item.complete_count || 0)}</td>
          <td>${escapeHtml(item.total_cost_units || 0)}</td>
          <td>${escapeHtml(roi)}</td>
        </tr>`;
    })
    .join("");
  if (!rows) {
    return `<p class="meta-copy">Economía shadow disponible cuando haya slates comparables scoreadas.</p>`;
  }
  return `
    <div class="shadow-table-wrap">
      <table class="shadow-table">
        <thead><tr><th>Estrategia</th><th>Perfectas</th><th>Costo unidades</th><th>ROI simulado</th></tr></thead>
        <tbody>${rows}</tbody>
      </table>
    </div>
    <p class="meta-copy subtle">ROI solo aparece si hay payout configurado; sin payout real, esto mide costo y break-even.</p>`;
}

function renderTicketStrategyBacktest(scores) {
  const best = scores?.ticket_strategy_backtest_summary?.best_strategy;
  const strategies = scores?.ticket_strategy_backtest_summary?.strategies || {};
  if (!best || !Object.keys(strategies).length) {
    return `<p class="meta-copy">Backtest de estrategias disponible cuando haya slates comparables scoreadas.</p>`;
  }
  const rows = Object.entries(strategies)
    .sort((a, b) => {
      const av = a[1];
      const bv = b[1];
      return (
        Number(bv.perfect_count || 0) - Number(av.perfect_count || 0) ||
        Number(bv.coverage_rate || 0) - Number(av.coverage_rate || 0) ||
        Number(av.total_cost_units || 0) - Number(bv.total_cost_units || 0)
      );
    })
    .slice(0, 5)
    .map(([key, item]) => `
      <tr>
        <td>${escapeHtml(item.strategy || key)}</td>
        <td>${escapeHtml(item.perfect_count || 0)}/${escapeHtml(item.complete_count || 0)}</td>
        <td>${escapeHtml(item.coverage_rate == null ? "—" : `${Math.round(Number(item.coverage_rate) * 100)}%`)}</td>
        <td>${escapeHtml(item.total_cost_units || 0)}</td>
      </tr>`)
    .join("");
  return `
    <p class="meta-copy"><strong>Mejor estrategia histórica:</strong> ${escapeHtml(best.strategy)} · perfectas ${escapeHtml(best.perfect_count || 0)}/${escapeHtml(best.complete_count || 0)} · costo ${escapeHtml(best.total_cost_units || 0)} unidades.</p>
    <div class="shadow-table-wrap">
      <table class="shadow-table">
        <thead><tr><th>Estrategia</th><th>Perfectas</th><th>Cobertura</th><th>Costo unidades</th></tr></thead>
        <tbody>${rows}</tbody>
      </table>
    </div>`;
}

export function renderLearningDashboard(inventory, readiness, scores = null) {
  if (!inventory || !Array.isArray(inventory.slates)) {
    return `<div class="empty-state">Sin inventario de aprendizaje.</div>`;
  }
  const badge = `<span class="shadow-badge badge-canary">SEGUIMIENTO Y APRENDIZAJE · SOLO LECTURA</span>`;
  const trainingReady = readiness && readiness.training_ready ? "sí" : "no";
  const reason = readiness && readiness.reason ? readiness.reason : "—";
  const cards = inventory.slates.map(slateCard).join("");
  return `
    <div class="shadow-panel trv-panel">
      <div class="shadow-toprow">${badge}</div>
      <p class="dryrun-lead">Slates: ${escapeHtml(inventory.slate_count)} · comparables: <strong>${escapeHtml(inventory.comparable_count)}</strong> · training ready: <strong>${escapeHtml(trainingReady)}</strong></p>
      <p class="meta-copy">Readiness: ${escapeHtml(reason)}</p>
      ${renderEconomicShadow(scores)}
      ${renderTicketStrategyBacktest(scores)}
      <div class="trv-grid">${cards}</div>
      <div class="shadow-alert">Aprendizaje de solo lectura · no entrena, no escribe predicciones ni resultados. Aplicar resultados oficiales requiere <span class="mono">--apply --confirm APPLY-COMPLETED-SLATE-RESULTS</span>.</div>
    </div>
  `;
}

export function renderLearningSummary(summary) {
  if (!summary) {
    return `<p class="meta-copy">Disponible cuando haya learning rows suficientes.</p>`;
  }
  const rows = Number(summary.total_rows || 0);
  if (rows <= 0) {
    return `<p class="meta-copy">Disponible cuando haya learning rows suficientes. Los resultados oficiales de Progol son solo-signo (sin marcador) y no aportan filas de entrenamiento; el aprendizaje necesita marcadores de una fuente deportiva. (${escapeHtml(summary.total_slates_scored || 0)} jornadas scoreadas, ${escapeHtml(summary.total_slates_complete || 0)} completas.)</p>`;
  }
  const hitRate = summary.hit_rate == null ? "—" : `${Math.round(Number(summary.hit_rate) * 100)}%`;
  return `
    <div class="learn-summary">
      <div class="ls-cell"><strong>${escapeHtml(rows)}</strong><span>learning rows</span></div>
      <div class="ls-cell"><strong>${escapeHtml(summary.rows_with_canonical_result || 0)}</strong><span>con resultado canónico</span></div>
      <div class="ls-cell"><strong>${escapeHtml(summary.rows_with_conflict || 0)}</strong><span>en conflicto (excluidas)</span></div>
      <div class="ls-cell"><strong>${hitRate}</strong><span>hit rate</span></div>
    </div>
    <p class="meta-copy subtle">Solo lectura. No se entrena ni se promueve ningún modelo desde esta vista.</p>`;
}
