import { describe, expect, it } from "vitest";
import { JSDOM } from "jsdom";
import { renderOperationalPredictionAuditPanel } from "../operational-prediction-audit.js";

const REPORT = {
  mode: "operational_prediction_audit",
  generated_at: "2026-07-14T05:00:00Z",
  uses_existing_sources_only: true,
  prediction_audit: {
    summary: { slate_count: 1, scored_matches: 3, hits: 2, misses: 1, accuracy: 0.6667 },
    segments: {
      confidence: [
        { label: "LISTO", total: 2, hits: 2, misses: 0, accuracy: 1 },
        { label: "REVISAR", total: 1, hits: 0, misses: 1, accuracy: 0 },
      ],
    },
  },
  placeholder_queue: {
    count: 1,
    items: [
      {
        draw_code: "PGM-804",
        position: 1,
        match: "France vs G",
        suggestions: { away: [] },
      },
    ],
  },
  confidence_explainer: {
    matches: [
      {
        draw_code: "PGM-804",
        position: 1,
        match: "France vs G",
        status: "BLOQUEADO",
        pick: "L",
        components: {
          evidence_coverage: { level: "thin" },
          data_quality: { level: "blocked" },
          model_provenance: { level: "fallback" },
        },
      },
    ],
  },
  publish_gate: {
    allowed: false,
    blocked_positions: [
      { draw_code: "PGM-804", position: 1, match: "France vs G", reasons: ["equipo placeholder"] },
    ],
    warnings: [],
  },
  freshness_monitor: {
    status: "ok",
    slates: [
      { draw_code: "PGM-804", pull_state: "waiting_results", completed_count: 0, match_count: 9, sources: [], age_minutes: null },
    ],
  },
};

describe("renderOperationalPredictionAuditPanel", () => {
  it("renders metrics, publish gate, placeholders and freshness", () => {
    const dom = new JSDOM(renderOperationalPredictionAuditPanel(REPORT));
    const text = dom.window.document.body.textContent;

    expect(text).toContain("Motor de predicción y publicación");
    expect(text).toContain("Publicación bloqueada");
    expect(text).toContain("67%");
    expect(text).toContain("France vs G");
    expect(text).toContain("waiting_results");
  });

  it("shows an empty state without data", () => {
    expect(renderOperationalPredictionAuditPanel(null)).toContain("Sin auditoría operativa");
  });
});
