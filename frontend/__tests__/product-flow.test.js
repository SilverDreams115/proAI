import { describe, expect, it } from "vitest";

import { renderProductFlowPanel } from "../product-flow.js";

describe("product flow panel", () => {
  it("renders the daily product path and separates recommendation from explanation", () => {
    const html = renderProductFlowPanel({
      mode: "product_flow",
      workflow_steps: [
        { step: "concurso_actual", status: "ready" },
        { step: "recomendacion", status: "jugar" },
      ],
      current_slate: {
        slate: {
          draw_code: "PG-9999",
          week_type: "weekend",
          match_count: 9,
          classification: "official_real",
        },
        data_quality: {
          score: 82,
          level: "good",
          prediction_status: "persisted",
          summary: "calidad good",
        },
        recommendation: {
          final_recommendation: "JUGAR",
          recommended_ticket: "balanced",
          explanation: { primary_reason: "Balanceado cubre riesgos." },
        },
        betting_policy: {
          action: "play_balanced",
          hard_no_play: false,
          max_combinations: 48,
        },
        drift_audit: { status: "clear", signals: [] },
        active_slate_contract: { strict: true, active_count: 1, violations: [] },
      },
      postmortem: {
        completed_slate_count: 2,
        ready_to_apply_count: 1,
        latest_validation: { draw_code: "PG-9998" },
        latest_score: { score: { hits: 6, total: 9 } },
        next_step: "Revisar score.",
      },
      next_actions: ["Ejecutar política: play_balanced."],
    });

    expect(html).toContain("PG-9999");
    expect(html).toContain("Calidad de datos");
    expect(html).toContain("JUGAR");
    expect(html).toContain("Balanceado cubre riesgos.");
    expect(html).toContain("Postmortem y aprendizaje");
    expect(html).toContain("6/9");
  });

  it("renders an empty state when unavailable", () => {
    expect(renderProductFlowPanel(null)).toContain("Sin flujo de producto");
  });
});
