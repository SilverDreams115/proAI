import { describe, it, expect } from "vitest";
import { JSDOM } from "jsdom";
import { renderSlateReadinessReportPanel } from "../slate-readiness-report.js";

function dom(html) {
  return new JSDOM(`<!doctype html><body>${html}</body>`).window.document;
}

const REPORT = {
  mode: "slate_readiness_report",
  slates: [
    {
      draw_code: "PGM-804",
      match_count: 9,
      status_counts: { LISTO: 1, REVISAR: 2, BLOQUEADO: 6 },
      flag_counts: { LOW_EVIDENCE: 7, FALLBACK_USED: 7, SUSPICIOUS_TEAM_NAME: 1 },
      safe_revisar_to_listo_candidates: [],
      suspicious_team_name_positions: [1],
      matches: [
        {
          position: 1,
          match: "France vs G",
          status: "BLOQUEADO",
          evidence_level: "low",
          flags: ["LOW_EVIDENCE", "SUSPICIOUS_TEAM_NAME", "PLACEHOLDER_TEAM"],
          actionable_blockers: ["team_resolution", "evidence_coverage"],
          pick: "L",
          top_probability: 0.56,
          recent_results_count: 0,
          head_to_head_results_count: 0,
          suspicious_team_names: ["G"],
        },
        {
          position: 4,
          match: "Tijuana vs Tigres",
          status: "LISTO",
          evidence_level: "medium",
          flags: [],
          actionable_blockers: [],
          pick: "V",
          top_probability: 0.53,
          recent_results_count: 1,
          head_to_head_results_count: 0,
          suspicious_team_names: [],
        },
      ],
    },
  ],
};

describe("renderSlateReadinessReportPanel", () => {
  it("renders slate counts and no safe promotion candidates", () => {
    const doc = dom(renderSlateReadinessReportPanel(REPORT));
    expect(doc.querySelector(".slate-readiness-panel")).not.toBeNull();
    expect(doc.body.textContent).toContain("PGM-804");
    expect(doc.body.textContent).toContain("Candidatos seguros REVISAR -> LISTO");
    expect(doc.body.textContent).toContain("ninguno");
  });

  it("surfaces suspicious team names as data reasons", () => {
    const doc = dom(renderSlateReadinessReportPanel(REPORT));
    expect(doc.body.textContent).toContain("France vs G");
    expect(doc.body.textContent).toContain("Nombre de equipo sospechoso");
    expect(doc.body.textContent).toContain("Equipo placeholder");
  });

  it("filters rows by actionable blocker", () => {
    const doc = dom(renderSlateReadinessReportPanel(REPORT, "team_resolution"));
    expect(doc.body.textContent).toContain("France vs G");
    expect(doc.body.textContent).not.toContain("Tijuana vs Tigres");
    expect(doc.querySelector('[data-readiness-filter="team_resolution"]').className).toContain("active");
  });

  it("renders an empty state without a report", () => {
    const doc = dom(renderSlateReadinessReportPanel(null));
    expect(doc.querySelector(".empty-state")).not.toBeNull();
  });
});
