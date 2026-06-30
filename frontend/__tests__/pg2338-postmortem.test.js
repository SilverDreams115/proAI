// PG-2338 postmortem UI contract (visible-as-principal, raw-only-in-details).
//
// Locks the read-path/UX guarantees surfaced by the PG-2338 audit:
//   * the visible/decision probabilities are the headline number;
//   * raw model output appears ONLY inside the technical <details>, never as
//     the principal — a 96%/81% raw favourite is never sold as certainty;
//   * a capped match shows the "baja evidencia" note;
//   * real draws render "Empate real · X cubierto/no cubierto" + diagnosis
//     "fallo por empate"; the dashboard shows PG-2338 as Completa with 6 draws;
//   * low/blocked evidence is never shown as "Fijo" (presentation guard).
import { describe, it, expect } from "vitest";
import {
  renderComparisonRow,
  renderComparisonDetail,
  renderDashboardEntry,
  visibleProbCell,
  rawProbDetail,
  rawWasCapped,
} from "../live-tracking.js";
import { presentationGuardOf } from "../presentation-guard.js";

// Real PG-2338 shape (visible == decision/capped, raw == uncapped model out).
function jpnSwe(extra = {}) {
  // pos 4 Japan-Sweden: raw 0.96/0.02/0.02 -> visible 0.60/0.20/0.20, real X.
  return {
    position: 4,
    home_team_name: "Japan",
    away_team_name: "Sweden",
    predicted_outcome: "1",
    confidence_band: "low",
    home_probability: 0.6,
    draw_probability: 0.2,
    away_probability: 0.2,
    raw_probabilities: { L: 0.96, E: 0.02, V: 0.02 },
    result_code: "X",
    prediction_hit: false,
    is_final: true,
    is_pending: false,
    is_live: false,
    simple_hit: false,
    doubles_hit: false,
    full_hit: false,
    draw_was_real: true,
    draw_was_covered: false,
    diagnosis: "fallo por empate",
    learning_status: "ready",
    ticket_modes: { simple: { picks: ["1"] }, doubles: { picks: ["1", "2"] }, full: { picks: ["1"] } },
    ...extra,
  };
}

describe("PG-2338 visible vs raw probabilities", () => {
  it("shows the visible/decision vector as the principal number", () => {
    const html = visibleProbCell(jpnSwe());
    expect(html).toContain("L 60%");
    expect(html).toContain("X 20%");
    expect(html).toContain("V 20%");
    // The raw 96% must NOT be the headline.
    expect(html).not.toContain("96%");
  });

  it("keeps raw probabilities only inside the technical <details>", () => {
    const html = rawProbDetail(jpnSwe());
    expect(html).toContain("<details");
    expect(html).toContain("raw");
    expect(html).toContain("L 96%"); // raw present, but inside details
  });

  it("flags a capped match with the baja-evidencia note", () => {
    expect(rawWasCapped(jpnSwe())).toBe(true);
    const html = rawProbDetail(jpnSwe());
    expect(html).toContain('data-capped="true"');
    expect(html).toContain("Probabilidad ajustada por baja evidencia");
  });

  it("does not flag a non-capped match (visible == raw)", () => {
    const m = jpnSwe({
      home_probability: 0.44,
      draw_probability: 0.21,
      away_probability: 0.35,
      raw_probabilities: { L: 0.44, E: 0.21, V: 0.35 },
    });
    expect(rawWasCapped(m)).toBe(false);
    expect(rawProbDetail(m)).not.toContain("data-capped");
  });

  it("comparison row renders visible as principal and raw inside details", () => {
    const html = renderComparisonRow(jpnSwe());
    // Principal cell carries the visible value, the details carry the raw.
    expect(html).toContain('class="cmp-probs"');
    expect(html).toContain("L 60%");
    expect(html).toContain("<details");
    expect(html).toContain("L 96%");
    // The headline (outside details) must not be the raw 96 — assert the
    // substring before <details> has no 96%.
    const head = html.slice(0, html.indexOf("<details"));
    expect(head).not.toContain("96%");
    expect(html).toContain("fallo por empate");
  });

  it("never shows raw 0.81 as principal either", () => {
    const m = jpnSwe({
      position: 5,
      home_probability: 0.6,
      raw_probabilities: { L: 0.81, E: 0.1, V: 0.09 },
    });
    const head = renderComparisonRow(m);
    const beforeDetails = head.slice(0, head.indexOf("<details"));
    expect(beforeDetails).toContain("L 60%");
    expect(beforeDetails).not.toContain("81%");
  });
});

describe("PG-2338 draws + coverage rendering", () => {
  it("renders 'fallo por empate' for a real draw missed", () => {
    expect(renderComparisonRow(jpnSwe())).toContain("fallo por empate");
  });

  it("dashboard shows PG-2338 Completa with 6 empates", () => {
    const html = renderDashboardEntry({
      slate_id: "30146702",
      draw_code: "PG-2338",
      week_type: "weekend",
      status_label: "Completa",
      classification: "official_real",
      comparable: true,
      completed_count: 14,
      match_count: 14,
      live_count: 0,
      pending_count: 0,
      simple_hits: 5,
      doubles_hits: 8,
      full_hits: 9,
      empates_reales: 6,
      empates_esperados: 3.54,
      is_complete: true,
    });
    expect(html).toContain("PG-2338");
    expect(html).toContain("Completa");
    expect(html).toContain("6 emp");
  });

  it("renders X cubierto / no cubierto for real draws (live detail row)", async () => {
    const { renderLiveMatchRow } = await import("../live-tracking.js");
    const covered = renderLiveMatchRow(jpnSwe({ draw_was_covered: true }));
    expect(covered).toContain("Empate real · X cubierto");
    const notCovered = renderLiveMatchRow(jpnSwe({ draw_was_covered: false }));
    expect(notCovered).toContain("Empate real · X no cubierto");
  });
});

describe("low/blocked evidence is never 'Fijo'", () => {
  it("blocked status is not simple-allowed", () => {
    const g = presentationGuardOf({
      final_status: "BLOQUEADO",
      ticket_strategy: "SIMPLE",
      risk_level: "high",
      recommended_outcome: "1",
    });
    expect(g.simple_allowed).toBe(false);
    expect(g.recommendation_label).toBe("BLOQUEADO");
  });

  it("low-evidence REVISAR is not simple-allowed", () => {
    const g = presentationGuardOf({
      final_status: "REVISAR",
      ticket_strategy: "SIMPLE",
      risk_level: "high",
      recommended_outcome: "1",
      flags: ["LOW_EVIDENCE", "EXTREME_PROBABILITY_CAPPED"],
    });
    expect(g.simple_allowed).toBe(false);
    expect(g.recommendation_label).not.toBe("SIMPLE");
  });
});
