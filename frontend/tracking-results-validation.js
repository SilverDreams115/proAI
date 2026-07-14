// R6.4 — Completed-slate results validation panel (Seguimiento, read-only).
//
// Pure render helper (returns an HTML string, no DOM/fetch) so it can be locked
// with Vitest. It shows, per completed slate (PG-2337, PGM-800, …), whether its
// results are comparable yet: predictions vs local/provider results, coverage,
// conflicts and exactly what is missing. It writes nothing and applies nothing.
import { escapeHtml } from "./helpers.js";

function statusOf(slate) {
  if (slate.conflicts > 0) return { label: "conflicto", tone: "danger" };
  if (slate.coverage >= 1) return { label: "comparable", tone: "ok" };
  if (slate.coverage > 0) return { label: "parcial", tone: "warn" };
  return { label: "pendiente", tone: "warn" };
}

function missingCopy(slate) {
  const bits = [];
  if (slate.local_results_count < slate.match_count) {
    bits.push(`resultados locales ${slate.local_results_count}/${slate.match_count}`);
  }
  if (slate.provider_results_count < slate.match_count) {
    bits.push(`resultados proveedor ${slate.provider_results_count}/${slate.match_count}`);
  }
  if (slate.predictions_count < slate.match_count) {
    bits.push(`predicciones ${slate.predictions_count}/${slate.match_count}`);
  }
  return bits.join(" · ") || "nada (completo)";
}

function slateCard(slate) {
  const st = statusOf(slate);
  const action =
    slate.coverage >= 1
      ? "Listo para comparar aciertos."
      : "Ejecutar provider dry-run o cargar resultados oficiales.";
  return `
    <div class="trv-card trv-${escapeHtml(st.tone)}">
      <div class="trv-top">
        <span class="trv-code">${escapeHtml(slate.draw_code)}</span>
        <span class="badge-risk tone-${escapeHtml(st.tone)}">${escapeHtml(st.label)}</span>
      </div>
      <div class="trv-line">Predicciones: <strong>${escapeHtml(slate.predictions_count)}/${escapeHtml(slate.match_count)}</strong></div>
      <div class="trv-line">Resultados: <strong>${escapeHtml(slate.local_results_count + slate.provider_results_count)}</strong> (local ${escapeHtml(slate.local_results_count)} · proveedor ${escapeHtml(slate.provider_results_count)})</div>
      <div class="trv-line">Coverage: <strong>${Math.round((Number(slate.coverage) || 0) * 100)}%</strong> · conflictos: ${escapeHtml(slate.conflicts)}</div>
      <div class="trv-line">Aciertos: ${escapeHtml(slate.hits)}/${escapeHtml(slate.match_count)} · ready_to_apply: <strong>${slate.ready_to_apply ? "sí" : "no"}</strong></div>
      <div class="trv-missing meta-copy"><strong>Falta:</strong> ${escapeHtml(missingCopy(slate))}</div>
      <div class="trv-action meta-copy"><strong>Acción:</strong> ${escapeHtml(action)}</div>
    </div>`;
}

export function renderTrackingResultsValidationPanel(report) {
  if (!report || !Array.isArray(report.slates)) {
    return `<div class="empty-state">Sin validación de resultados de slates terminadas.</div>`;
  }
  const badge = `<span class="shadow-badge badge-canary">VALIDACIÓN DE RESULTADOS · SOLO LECTURA</span>`;
  if (report.slates.length === 0) {
    return `
      <div class="shadow-panel trv-panel">
        <div class="shadow-toprow">${badge}</div>
        <div class="empty-state">No hay slates terminadas con predicciones para validar.</div>
      </div>`;
  }
  const cards = report.slates.map(slateCard).join("");
  return `
    <div class="shadow-panel trv-panel">
      <div class="shadow-toprow">${badge}</div>
      <p class="dryrun-lead">Slates terminadas: ${escapeHtml(report.slate_count)} · listas para aplicar: ${escapeHtml(report.ready_count)}. Solo lectura; no escribe resultados.</p>
      <div class="trv-grid">${cards}</div>
      <div class="shadow-alert">Validación de solo lectura · aplicar resultados requiere <span class="mono">--apply --confirm APPLY-COMPLETED-SLATE-RESULTS</span> y una validación que devuelva ready_to_apply.</div>
    </div>
  `;
}
