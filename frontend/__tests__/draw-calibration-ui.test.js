// Draw (X) calibration UI helpers — note, before/after, X coverage.
//
// Pins the product contract from the draw-prior phase:
//   * shows "Empate ajustado por calibración" when applied;
//   * shows p_draw before/after in the technical detail;
//   * shows "X recomendada en cobertura" when X is covered;
//   * never presents the draw as a fixed pick (it's a coverage note, not a pick);
//   * shows nothing when calibration did not apply (legacy / solid favourites).
import { describe, it, expect } from "vitest";
import {
  drawCalibrationApplied,
  drawCalibrationNote,
  drawCalibrationDelta,
  drawCoverageLabel,
  drawCalibrationDetail,
} from "../draw-calibration-ui.js";
import { renderComparisonRow } from "../live-tracking.js";

const calibrated = {
  position: 4,
  home_team_name: "Japan",
  away_team_name: "Sweden",
  predicted_outcome: "1",
  confidence_band: "low",
  home_probability: 0.58,
  draw_probability: 0.22,
  away_probability: 0.20,
  pre_draw_calibration_probabilities: { L: 0.60, E: 0.20, V: 0.20 },
  draw_calibration_applied: true,
  draw_calibration_reason: "baja evidencia",
  raw_probabilities: { L: 0.96, E: 0.02, V: 0.02 },
  result_code: "X",
  prediction_hit: false,
  is_final: true,
  is_pending: false,
  is_live: false,
  simple_hit: false,
  doubles_hit: true,
  full_hit: true,
  draw_was_real: true,
  draw_was_covered: true,
  diagnosis: "fallo por empate",
  ticket_modes: { simple: { picks: ["1"] }, doubles: { picks: ["1", "X"] }, full: { picks: ["1", "X", "2"] } },
};

const notCalibrated = { ...calibrated, draw_calibration_applied: false, draw_was_covered: false, ticket_modes: { simple: { picks: ["1"] }, doubles: { picks: ["1", "2"] }, full: { picks: ["1"] } } };

describe("draw calibration helpers", () => {
  it("flags applied calibration", () => {
    expect(drawCalibrationApplied(calibrated)).toBe(true);
    expect(drawCalibrationApplied(notCalibrated)).toBe(false);
  });

  it("shows the calibration note only when applied", () => {
    expect(drawCalibrationNote(calibrated)).toContain("Empate ajustado por calibración");
    expect(drawCalibrationNote(notCalibrated)).toBe("");
  });

  it("reports p_draw before/after", () => {
    const d = drawCalibrationDelta(calibrated);
    expect(d.beforeLabel).toBe("20%");
    expect(d.afterLabel).toBe("22%");
    expect(drawCalibrationDelta(notCalibrated)).toBe(null);
  });

  it("labels X coverage", () => {
    expect(drawCoverageLabel(calibrated)).toContain("X recomendada en cobertura");
    expect(drawCoverageLabel(notCalibrated)).toBe("");
  });

  it("never presents the draw as a fixed pick (note is coverage, not a pick)", () => {
    const html = drawCalibrationDetail(calibrated, (s) => s);
    expect(html).toContain("cobertura");
    expect(html).not.toMatch(/fijo/i);
  });
});

describe("comparison row integration", () => {
  it("renders calibration note + before/after, keeps visible principal and raw in details", () => {
    const html = renderComparisonRow(calibrated);
    expect(html).toContain("Empate ajustado por calibración");
    expect(html).toContain("p(X) 20% → 22%");
    expect(html).toContain("X recomendada en cobertura");
    // Visible principal present; raw 96% only inside the technical <details>.
    expect(html).toContain("L 58%");
    const head = html.slice(0, html.indexOf("<details"));
    expect(head).not.toContain("96%");
  });

  it("omits the calibration note when not applied", () => {
    const html = renderComparisonRow(notCalibrated);
    expect(html).not.toContain("Empate ajustado por calibración");
  });
});
