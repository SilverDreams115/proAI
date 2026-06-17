/**
 * Pure formatter + label helpers extracted from app.js for testability.
 *
 * Everything in this module is side-effect free and DOM-free — that's
 * what makes it the safe first step in the larger app.js split: we
 * can lock these behaviours behind Vitest without spinning up jsdom
 * for the rendering pipeline. The orchestrator (app.js) imports them
 * via the `<script type="module">` tag in index.html.
 *
 * If you add anything here that needs to touch the DOM, fetch
 * something, or read state, move it back to app.js (or a dedicated
 * module) instead — the test coverage promise of this module is
 * "no I/O, no globals, no surprises."
 */

export function formatPercent(value) {
  // Matches the legacy implementation byte-for-byte: a missing value
  // coerces to 0, the result is rounded to the nearest integer percent.
  // Tests treat null/undefined/NaN as 0% to keep the renderer stable
  // when an upstream field is unset.
  if (value === null || value === undefined || Number.isNaN(value)) return "—";
  return `${Math.round((value || 0) * 100)}%`;
}

export function formatDate(value) {
  // Surfaced by S5.2 tests: `new Date(null)` is the unix epoch, so
  // the previous implementation rendered missing kickoffs as
  // "mié 31 de dic, 06:00 p.m." instead of the intended fallback.
  // Guard the null/undefined path before constructing the Date.
  if (value === null || value === undefined || value === "") return "sin fecha";
  try {
    const parsed = new Date(value);
    if (Number.isNaN(parsed.getTime())) return value || "sin fecha";
    return parsed.toLocaleString("es-MX", {
      weekday: "short",
      day: "2-digit",
      month: "short",
      hour: "2-digit",
      minute: "2-digit",
    });
  } catch {
    return value || "sin fecha";
  }
}

export function formatRelativeAge(value) {
  // Human-readable "actualizado hace X" for a timestamp. Caller passes
  // the predictionResponse.generated_at; we surface staleness so the
  // operator knows whether the probs reflect the latest ingest or a
  // stale cached score.
  if (!value) return "sin timestamp";
  try {
    const then = new Date(value).getTime();
    if (!Number.isFinite(then)) return "sin timestamp";
    const seconds = Math.max(0, Math.round((Date.now() - then) / 1000));
    if (seconds < 60) return `hace ${seconds}s`;
    const minutes = Math.round(seconds / 60);
    if (minutes < 60) return `hace ${minutes}m`;
    const hours = Math.round(minutes / 60);
    if (hours < 24) return `hace ${hours}h`;
    const days = Math.round(hours / 24);
    return `hace ${days}d`;
  } catch {
    return "sin timestamp";
  }
}

export function availabilityStatusLabel(value) {
  return (
    {
      out: "Baja",
      suspended: "Suspendido",
      doubtful: "En duda",
      rotation_risk: "Riesgo de rotación",
      available: "Disponible",
    }[value] ||
    value ||
    "Sin estado"
  );
}

export function availabilityCategoryLabel(value) {
  return (
    {
      injury: "lesión",
      suspension: "sanción/tarjetas",
      rotation: "alineación",
    }[value] ||
    value ||
    "contexto"
  );
}

export function confidenceLabel(value) {
  return (
    {
      high: "alta",
      medium: "media",
      low: "baja",
      blocked: "bloqueada",
    }[value] ||
    value ||
    "sin clasificar"
  );
}

export function readinessLabel(value) {
  return (
    {
      ready: "listo",
      covered: "cubierto",
      context_only: "solo contexto",
      not_ready: "sin benchmark",
      unclassified: "sin clasificar",
    }[value] ||
    value ||
    "sin clasificar"
  );
}

export function dataQualityLabel(level) {
  return (
    {
      good: "buena",
      partial: "parcial",
      thin: "delgada",
    }[level] || "sin clasificar"
  );
}

export function statusTone(value) {
  if (value === "ok" || value === "ready" || value === true) return "ok";
  if (value === "blocked" || value === "not_ready" || value === false) return "bad";
  return "warn";
}

const HTML_ESCAPES = {
  "&": "&amp;",
  "<": "&lt;",
  ">": "&gt;",
  '"': "&quot;",
  "'": "&#39;",
};

export function escapeHtml(value) {
  return String(value ?? "").replace(/[&<>"']/g, (char) => HTML_ESCAPES[char]);
}

// Draw-reporting thresholds, mirrored from the backend
// TicketRecommendationService. Reporting only — they never change picks
// or confidence bands.
export const DRAW_LIVE_THRESHOLD = 0.25;
export const DRAW_STRONG_THRESHOLD = 0.3;

export function drawRiskSummary(prediction, coverage = {}, provided = null) {
  // Surface why draws may be hurting the boleta: how much draw mass the
  // model assigned, its rank among the three outcomes, the empate vivo /
  // fuerte flags, and whether X is actually covered per ticket mode.
  // Prefers the backend-provided `draw_risk` block; falls back to a
  // client computation so older snapshots (pre draw_risk) still render.
  const pDraw = Number.isFinite(prediction?.draw_probability) ? prediction.draw_probability : 0;
  const home = Number.isFinite(prediction?.home_probability) ? prediction.home_probability : 0;
  const away = Number.isFinite(prediction?.away_probability) ? prediction.away_probability : 0;
  const drawRank = provided?.draw_rank ?? 1 + (home > pDraw ? 1 : 0) + (away > pDraw ? 1 : 0);
  return {
    pDraw: provided?.p_draw ?? pDraw,
    drawRank,
    isLive: provided?.is_live_draw ?? pDraw >= DRAW_LIVE_THRESHOLD,
    isStrong: provided?.is_strong_draw ?? pDraw >= DRAW_STRONG_THRESHOLD,
    coveredSimple: provided?.covered_simple ?? Boolean(coverage.simple),
    coveredDoubles: provided?.covered_doubles ?? Boolean(coverage.doubles),
    coveredFull: provided?.covered_full ?? Boolean(coverage.full),
  };
}

export function sortedOutcomes(prediction) {
  // Sort the three outcome probabilities high → low so the renderer
  // can pick "best / second / third" without re-sorting each call.
  //
  // Prefer the explicit, non-positional L/E/V vector emitted by the
  // backend sanity layer (`prediction.probabilities`) — these are the
  // FINAL, guardrailed numbers (e.g. a friendly capped at 65% instead of
  // a raw 79%). Fall back to the legacy positional fields for payloads
  // that predate the sanity layer.
  const explicit = prediction?.probabilities;
  const hasExplicit =
    explicit &&
    ["L", "E", "V"].every((k) => Number.isFinite(Number(explicit[k])));
  if (hasExplicit) {
    return [
      { key: "1", value: Number(explicit.L) || 0 },
      { key: "X", value: Number(explicit.E) || 0 },
      { key: "2", value: Number(explicit.V) || 0 },
    ].sort((a, b) => b.value - a.value);
  }
  return [
    { key: "1", value: Number(prediction.home_probability) || 0 },
    { key: "X", value: Number(prediction.draw_probability) || 0 },
    { key: "2", value: Number(prediction.away_probability) || 0 },
  ].sort((a, b) => b.value - a.value);
}

// Human-readable Spanish labels for the backend sanity flags, used in the
// quality tooltip / reasons so operators see *why* a pick was degraded.
const SANITY_FLAG_LABELS = {
  LOW_EVIDENCE: "evidencia baja",
  INTERNATIONAL_FRIENDLY: "amistoso internacional",
  FRIENDLY_UNCERTAINTY_PENALTY: "penalización por amistoso",
  EXTREME_PROBABILITY_WITHOUT_EVIDENCE: "probabilidad extrema sin evidencia",
  EXTREME_PROBABILITY_CAPPED: "probabilidad recortada",
  SUSPICIOUS_CLASS_PROBABILITY: "clase con probabilidad sospechosa",
  FALLBACK_USED: "modelo heurístico de respaldo",
  BLOCKED_INSUFFICIENT_DATA: "sin datos suficientes",
};

export function flagLabel(flag) {
  return SANITY_FLAG_LABELS[flag] || String(flag).toLowerCase().replace(/_/g, " ");
}

export function linkedEvidenceCount(match) {
  // The legacy implementation reads three independent signals (the
  // raw evidence_items count on the feature payload, the length of
  // the verbose evidence_summaries list, and the length of the
  // match-level evidence array) and returns the max — evidence
  // rows are additive, not exclusive, so the highest wins.
  const payloadCount = Number(match?.features?.payload?.evidence_items ?? 0);
  const summaryCount = Array.isArray(match?.features?.payload?.evidence_summaries)
    ? match.features.payload.evidence_summaries.length
    : 0;
  const evidenceCount = Array.isArray(match?.evidence) ? match.evidence.length : 0;
  return Math.max(
    evidenceCount,
    summaryCount,
    Number.isFinite(payloadCount) ? payloadCount : 0,
  );
}

export function buildQualityTooltip(matches, helpers = { dataQualityLabel }) {
  // Compact per-position breakdown rendered as the `title` of the
  // Calidad ops-item. The user reported the chip's "75/100 · 1
  // delgada(s)" headline didn't tell them which position pulled
  // the average down; hovering now reveals the score + missing
  // signals for every match without needing devtools.
  if (!Array.isArray(matches) || !matches.length) return "Sin partidos.";
  const rows = [...matches]
    .sort((a, b) => (a.position ?? 0) - (b.position ?? 0))
    .map((match) => {
      const q = match.quality || {};
      const score = Number.isFinite(Number(q.quality_score))
        ? Number(q.quality_score)
        : null;
      const level =
        q.quality_level ||
        (score !== null && score >= 70
          ? "good"
          : score !== null && score >= 40
            ? "partial"
            : "thin");
      const levelLabel = helpers.dataQualityLabel(level);
      const home = match.prediction?.home_team_name || match.home_team_name || "";
      const away = match.prediction?.away_team_name || match.away_team_name || "";
      const missing =
        Array.isArray(q.missing) && q.missing.length
          ? `falta ${q.missing.join(", ")}`
          : "datos completos";
      const scoreStr = score !== null ? `${score}/100` : "sin score";
      return `${(match.position ?? "?").toString().padStart(2)}  ${scoreStr.padEnd(7)} ${levelLabel.padEnd(8)} ${home} vs ${away} — ${missing}`;
    });
  return rows.join("\n");
}
