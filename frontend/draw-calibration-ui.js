// Conservative draw (X) calibration — UI helpers (pure, testable).
//
// Surfaces, when the backend nudged p_draw up on a low-evidence match:
//   * a "Empate ajustado por calibración" note;
//   * the p_draw before/after for the technical detail;
//   * a "X recomendada en cobertura" label when X is in doubles/full.
// It NEVER presents the draw as a fixed pick — the calibrated p_draw is always
// below the top outcome, so the simple pick is unaffected.

function _pct(value) {
  if (value === null || value === undefined || Number.isNaN(value)) return "—";
  return `${Math.round((value || 0) * 100)}%`;
}

export function drawCalibrationApplied(match) {
  return Boolean(match && match.draw_calibration_applied);
}

// Note text shown when calibration applied; "" otherwise (so callers can skip).
export function drawCalibrationNote(match) {
  if (!drawCalibrationApplied(match)) return "";
  return "Empate ajustado por calibración";
}

// { before, after } visible p_draw for the technical detail. Returns null when
// calibration did not apply (nothing to compare).
export function drawCalibrationDelta(match) {
  if (!drawCalibrationApplied(match)) return null;
  const pre = match.pre_draw_calibration_probabilities || {};
  const before = pre.E;
  const after = match.draw_probability;
  if (before === undefined || before === null) return null;
  return { before, after, beforeLabel: _pct(before), afterLabel: _pct(after) };
}

// X covered in any ticket mode (reads draw_was_covered or ticket_modes picks).
export function drawCovered(match) {
  if (!match) return false;
  if (match.draw_was_covered) return true;
  const modes = match.ticket_modes || {};
  return ["simple", "doubles", "full"].some((m) =>
    ((modes[m] && modes[m].picks) || []).map(String).includes("X"),
  );
}

export function drawCoverageLabel(match) {
  return drawCovered(match) ? "X recomendada en cobertura" : "";
}

// Detail HTML block (note + before/after) — empty string when not applied.
export function drawCalibrationDetail(match, escape = (s) => s) {
  if (!drawCalibrationApplied(match)) return "";
  const delta = drawCalibrationDelta(match);
  const ba = delta
    ? `<span class="draw-cal-delta">p(X) ${escape(delta.beforeLabel)} → ${escape(delta.afterLabel)}</span>`
    : "";
  const cov = drawCovered(match)
    ? `<span class="draw-cal-cov">${escape(drawCoverageLabel(match))}</span>`
    : "";
  return `<div class="draw-cal-note" data-draw-calibrated="true"><span class="draw-cal-tag">${escape(drawCalibrationNote(match))}</span>${ba}${cov}</div>`;
}
