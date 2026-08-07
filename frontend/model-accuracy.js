// Acertividad comparada — sistema en producción vs neural experimental.
//
// Pure render helper (returns an HTML string, no DOM/fetch) so it can be
// locked with Vitest, same shape as learning-dashboard.js and
// neural-shadow-panel.js.
//
// The panel exists to answer one question — "¿cuál acierta más?" — without
// letting a flattering number pass as proof. The two accuracies the backend
// publishes are NOT earned on equal footing:
//
//   * the baseline figure is what production actually predicted, live, on
//     rows it had never seen;
//   * the neural figure is measured on the same rows the candidate trained
//     on, so it is in-sample and optimistic by construction.
//
// Rendering both arcs identically would state a 11-point win the evidence
// does not support — the exact inflation this system refuses everywhere else
// (a match without anchors is `blocked`, never dressed up). So the in-sample
// arc is drawn hatched instead of solid: same geometry, visibly not the same
// kind of number. The hatch is the honest rendering, not decoration.
import { escapeHtml } from "./helpers.js";

// Donut geometry. r is chosen so the 2πr circumference lands near 327px,
// which keeps stroke-dasharray arithmetic readable in the DOM for tests.
const RADIUS = 52;
const CIRCUMFERENCE = 2 * Math.PI * RADIUS;

// Sistema keeps the structural sky accent it already owns across the UI;
// neural takes amber, which is this codebase's own signal for
// experimental / caution. The pairing is semantic, not palette-shopping.
const SERIES = {
  sistema: { label: "Sistema", accent: "#7dd3fc", patternId: null },
  neural: { label: "Neural", accent: "#ffd166", patternId: "acc-hatch-neural" },
};

function formatAccuracy(value) {
  if (value === null || value === undefined || Number.isNaN(Number(value))) return "—";
  return `${(Number(value) * 100).toFixed(1)}%`;
}

function formatScore(value) {
  if (value === null || value === undefined || Number.isNaN(Number(value))) return "—";
  return Number(value).toFixed(4);
}

// Correct-row count implied by an accuracy over a known denominator. The
// backend publishes the rate, not the tally; showing "45 / 106" makes the
// sample size impossible to overlook, which a bare percentage hides.
function hitsFrom(accuracy, rows) {
  if (accuracy === null || accuracy === undefined || !rows) return null;
  return Math.round(Number(accuracy) * Number(rows));
}

function donut({ key, accuracy, rows, inSample }) {
  const series = SERIES[key];
  const safeAccuracy = Math.max(0, Math.min(1, Number(accuracy) || 0));
  const filled = (safeAccuracy * CIRCUMFERENCE).toFixed(2);
  const gap = (CIRCUMFERENCE - safeAccuracy * CIRCUMFERENCE).toFixed(2);
  const hits = hitsFrom(accuracy, rows);
  // SVG paint fallback: if the pattern IRI ever fails to resolve, the arc
  // still paints in the flat accent instead of vanishing. Losing the hatch
  // costs a nuance; losing the arc would silently drop half the comparison.
  const stroke = inSample && series.patternId
    ? `url(#${series.patternId}) ${series.accent}`
    : series.accent;
  const tally = hits === null
    ? "sin filas evaluadas"
    : `${escapeHtml(hits)} de ${escapeHtml(rows)} aciertos`;
  return `
    <figure class="accuracy-dial accuracy-dial-${escapeHtml(key)}">
      <svg class="accuracy-dial-svg" viewBox="0 0 128 128" role="img"
           aria-label="${escapeHtml(series.label)}: ${escapeHtml(formatAccuracy(accuracy))} de acierto sobre ${escapeHtml(rows || 0)} filas">
        <circle class="accuracy-track" cx="64" cy="64" r="${RADIUS}" />
        <circle class="accuracy-arc" cx="64" cy="64" r="${RADIUS}"
                stroke="${stroke}"
                stroke-dasharray="${filled} ${gap}"
                transform="rotate(-90 64 64)" />
      </svg>
      <figcaption class="accuracy-dial-caption">
        <span class="accuracy-dial-name">${escapeHtml(series.label)}</span>
        <span class="mono accuracy-dial-value">${escapeHtml(formatAccuracy(accuracy))}</span>
        <span class="mono accuracy-dial-tally">${tally}</span>
        <span class="accuracy-dial-basis accuracy-basis-${inSample ? "insample" : "holdout"}">
          ${inSample ? "medido sobre las filas que entrenó" : "medido en vivo, sobre filas no vistas"}
        </span>
      </figcaption>
    </figure>`;
}

function metricRow(label, baselineValue, neuralValue, lowerIsBetter) {
  const baseline = Number(baselineValue);
  const neural = Number(neuralValue);
  let leader = "";
  if (Number.isFinite(baseline) && Number.isFinite(neural) && baseline !== neural) {
    const neuralLeads = lowerIsBetter ? neural < baseline : neural > baseline;
    leader = neuralLeads ? "neural" : "sistema";
  }
  return `
    <tr>
      <th scope="row">${escapeHtml(label)}</th>
      <td class="mono ${leader === "sistema" ? "accuracy-leads" : ""}">${escapeHtml(formatScore(baselineValue))}</td>
      <td class="mono ${leader === "neural" ? "accuracy-leads" : ""}">${escapeHtml(formatScore(neuralValue))}</td>
    </tr>`;
}

function emptyState(title, detail) {
  return `
    <div class="accuracy-panel">
      <div class="accuracy-toprow"><span class="shadow-badge badge-readonly">ACERTIVIDAD COMPARADA · SOLO LECTURA</span></div>
      <div class="empty-state">
        <strong>${escapeHtml(title)}</strong>
        <p class="meta-copy">${escapeHtml(detail)}</p>
      </div>
    </div>`;
}

export function renderModelAccuracy(payload) {
  if (!payload || payload.available === false || payload.status === "no_active_model") {
    return emptyState(
      "Sin modelo neural activo",
      "Ningún candidato ha sido promovido al slot de shadow, así que no hay una segunda acertividad que comparar contra el sistema.",
    );
  }
  const comparison = payload.comparison;
  if (!comparison || comparison.status !== "ok") {
    return emptyState(
      "Comparación no disponible",
      "El modelo activo existe pero el backend no publicó una comparación contra el baseline. Sin ese objeto no hay dos números que enfrentar.",
    );
  }

  const rows = Number(comparison.evaluated_rows) || 0;
  const trainedOn = Number(payload.training_sample_size) || 0;
  // The candidate is scored on the rows it was fitted on whenever the
  // training sample covers the evaluated set — the backend does not flag
  // this, so we derive it rather than assume either way.
  const neuralInSample = trainedOn > 0 && rows > 0 && trainedOn >= rows;
  const slates = Number(payload?.dataset?.slates) || 0;
  const runId = String(payload.run_id || "—").slice(0, 8);

  const accuracyDelta = Number(comparison.accuracy_delta);
  const deltaLabel = Number.isFinite(accuracyDelta)
    ? `${accuracyDelta > 0 ? "+" : ""}${(accuracyDelta * 100).toFixed(1)} pts`
    : "—";

  const verdict = neuralInSample
    ? {
        tone: "warn",
        title: "Los dos números no son comparables todavía",
        detail:
          `El sistema acertó sobre filas que nunca vio: son predicciones que ya había servido en vivo. ` +
          `El neural se midió sobre las mismas ${rows} filas con las que se entrenó, así que su ventaja de ` +
          `${deltaLabel} incluye lo que memorizó. Para un veredicto real hace falta la evaluación ` +
          `leave-one-slate-out, que entrena por fold y deja fuera la jornada que califica.`,
      }
    : {
        tone: "ok",
        title: "Ambos números vienen de filas no vistas",
        detail:
          `El candidato se evaluó sobre ${rows} filas fuera de su muestra de entrenamiento, ` +
          `así que la diferencia de ${deltaLabel} se sostiene sobre la misma base que la del sistema.`,
      };

  return `
    <div class="accuracy-panel">
      <div class="accuracy-toprow">
        <span class="shadow-badge badge-readonly">ACERTIVIDAD COMPARADA · SOLO LECTURA</span>
        <span class="meta-copy accuracy-scope">${escapeHtml(rows)} filas calificadas${slates ? ` · ${escapeHtml(slates)} slates` : ""} · run <span class="mono">${escapeHtml(runId)}</span></span>
      </div>

      <div class="accuracy-dials">
        <svg class="accuracy-defs" aria-hidden="true" focusable="false">
          <defs>
            <pattern id="acc-hatch-neural" patternUnits="userSpaceOnUse" width="7" height="7" patternTransform="rotate(45)">
              <rect width="7" height="7" fill="transparent" />
              <line x1="0" y1="0" x2="0" y2="7" stroke="#ffd166" stroke-width="4" />
            </pattern>
          </defs>
        </svg>
        ${donut({ key: "sistema", accuracy: comparison?.baseline?.accuracy, rows, inSample: false })}
        ${donut({ key: "neural", accuracy: comparison?.neural?.accuracy, rows, inSample: neuralInSample })}
      </div>

      <div class="accuracy-verdict accuracy-verdict-${escapeHtml(verdict.tone)}">
        <strong>${escapeHtml(verdict.title)}</strong>
        <p class="meta-copy">${escapeHtml(verdict.detail)}</p>
      </div>

      <table class="accuracy-metrics">
        <caption class="meta-copy">Métricas sobre el mismo conjunto. Brier y entropía cruzada premian la probabilidad, no sólo el signo: en ambas, menos es mejor.</caption>
        <thead>
          <tr><th scope="col">Métrica</th><th scope="col">Sistema</th><th scope="col">Neural</th></tr>
        </thead>
        <tbody>
          ${metricRow("Acertividad", comparison?.baseline?.accuracy, comparison?.neural?.accuracy, false)}
          ${metricRow("Brier (menos es mejor)", comparison?.baseline?.brier_score, comparison?.neural?.brier_score, true)}
          ${metricRow("Entropía cruzada (menos es mejor)", comparison?.baseline?.cross_entropy, comparison?.neural?.cross_entropy, true)}
        </tbody>
      </table>

      <p class="meta-copy accuracy-foot">El modelo neural es experimental y corre en shadow: no reemplaza probabilidades, pick ni boleta.</p>
    </div>`;
}
