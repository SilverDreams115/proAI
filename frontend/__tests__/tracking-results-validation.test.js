import { describe, it, expect } from "vitest";
import { JSDOM } from "jsdom";
import { renderTrackingResultsValidationPanel } from "../tracking-results-validation.js";

function dom(html) {
  return new JSDOM(`<!doctype html><body>${html}</body>`).window.document;
}

function slate(extra = {}) {
  return {
    draw_code: "PG-2337", week_type: "weekend", match_count: 14,
    predictions_count: 14, local_results_count: 0, provider_results_count: 0,
    coverage: 0, conflicts: 0, hits: 0, ready_to_apply: false,
    ...extra,
  };
}

const REPORT = {
  mode: "completed_slate_results_validation_all",
  slate_count: 2, ready_count: 0,
  slates: [
    slate({ draw_code: "PG-2337", match_count: 14, predictions_count: 14 }),
    slate({ draw_code: "PGM-800", week_type: "midweek", match_count: 9, predictions_count: 9 }),
  ],
  write_safety: { writes_performed: false },
};

describe("renderTrackingResultsValidationPanel", () => {
  it("5 — shows PG-2337 with pending status and what is missing", () => {
    const doc = dom(renderTrackingResultsValidationPanel(REPORT));
    const card = [...doc.querySelectorAll(".trv-card")].find((c) => c.textContent.includes("PG-2337"));
    expect(card).toBeTruthy();
    expect(card.textContent).toContain("pendiente");
    expect(card.textContent).toContain("Predicciones:");
    expect(card.textContent).toContain("14/14");
  });

  it("6 — shows PGM-800 with pending status", () => {
    const doc = dom(renderTrackingResultsValidationPanel(REPORT));
    const card = [...doc.querySelectorAll(".trv-card")].find((c) => c.textContent.includes("PGM-800"));
    expect(card).toBeTruthy();
    expect(card.textContent).toContain("pendiente");
  });

  it("7 — explains exactly what is missing (resultados faltantes)", () => {
    const doc = dom(renderTrackingResultsValidationPanel(REPORT));
    const text = doc.body.textContent;
    expect(text).toContain("Falta:");
    expect(text).toContain("resultados locales 0/14");
    expect(text).toContain("Ejecutar provider dry-run");
  });

  it("marks a fully-covered slate as comparable", () => {
    const doc = dom(
      renderTrackingResultsValidationPanel({
        ...REPORT,
        slates: [slate({ draw_code: "PG-9999", local_results_count: 14, provider_results_count: 14, coverage: 1, hits: 9, ready_to_apply: true })],
      }),
    );
    const card = doc.querySelector(".trv-card");
    expect(card.textContent).toContain("comparable");
    expect(card.textContent).toContain("Aciertos: 9/14");
  });

  it("flags conflicts", () => {
    const doc = dom(
      renderTrackingResultsValidationPanel({
        ...REPORT,
        slates: [slate({ coverage: 0.5, conflicts: 2 })],
      }),
    );
    expect(doc.querySelector(".trv-card").textContent).toContain("conflicto");
  });

  it("renders an empty state when there are no completed slates", () => {
    const doc = dom(renderTrackingResultsValidationPanel({ slates: [], slate_count: 0, ready_count: 0 }));
    expect(doc.body.textContent).toContain("No hay slates terminadas");
  });

  it("renders an empty state when there is no report", () => {
    const doc = dom(renderTrackingResultsValidationPanel(null));
    expect(doc.querySelector(".empty-state")).not.toBeNull();
  });
});
