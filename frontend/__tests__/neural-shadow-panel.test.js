import { describe, it, expect } from "vitest";
import { JSDOM } from "jsdom";
import { renderNeuralShadowPanel } from "../neural-shadow-panel.js";

function dom(html) {
  return new JSDOM(`<!doctype html><body>${html}</body>`).window.document;
}

describe("renderNeuralShadowPanel", () => {
  it("renders neural shadow summary and per-match deltas", () => {
    const doc = dom(renderNeuralShadowPanel([
      {
        position: 1,
        prediction: {
          home_team_name: "Home",
          away_team_name: "Away",
          neural_shadow: {
            active: true,
            status: "ok",
            run_id: "c34824b9-7fe5-4c07-be1b-cfa343bd85b2",
            probabilities: { L: 0.6, E: 0.25, V: 0.15 },
            top_pick: "L",
            baseline_top_pick: "L",
            top_pick_changed: false,
            probability_delta: { L: 0.04, E: -0.01, V: -0.03 },
            max_abs_delta: 0.04,
          },
        },
      },
    ]));
    expect(doc.body.textContent).toContain("NEURAL SHADOW");
    expect(doc.body.textContent).toContain("1 / 1");
    expect(doc.body.textContent).toContain("Home vs Away");
    expect(doc.body.textContent).toContain("ΔL 0.04");
  });

  it("renders empty state without shadows", () => {
    const doc = dom(renderNeuralShadowPanel([]));
    expect(doc.querySelector(".empty-state")).not.toBeNull();
  });
});

describe("renderNeuralShadowPanel inactive states", () => {
  function inactiveMatches(shadow, count = 3) {
    return Array.from({ length: count }, (_, i) => ({
      position: i + 1,
      prediction: {
        home_team_name: `Home ${i + 1}`,
        away_team_name: `Away ${i + 1}`,
        neural_shadow: { active: false, max_abs_delta: 0, ...shadow },
      },
    }));
  }

  it("explains a missing active model instead of printing a table of dashes", () => {
    const doc = dom(renderNeuralShadowPanel(inactiveMatches({ status: "no_active_model" })));
    expect(doc.body.textContent).toContain("Sin modelo neural activo");
    expect(doc.body.textContent).toContain("3 posición(es) sin comparación");
    // The dash grid is exactly what this state replaces.
    expect(doc.querySelector(".neural-shadow-table")).toBeNull();
    expect(doc.body.textContent).not.toContain("—");
    // It still identifies itself as the read-only shadow panel.
    expect(doc.body.textContent).toContain("NEURAL SHADOW");
  });

  it("surfaces the backend reason for an incompatible artifact", () => {
    const doc = dom(renderNeuralShadowPanel(inactiveMatches({
      status: "incompatible_artifact",
      run_id: "b23d9160-8497-400c-87b5-6faa10e34478",
      reason: "active neural artifact is not pre-match shadow safe",
    })));
    expect(doc.body.textContent).toContain("Artefacto neural incompatible");
    expect(doc.body.textContent).toContain("not pre-match shadow safe");
    expect(doc.body.textContent).toContain("b23d9160");
  });

  it("falls back to a generic message on an unknown status", () => {
    const doc = dom(renderNeuralShadowPanel(inactiveMatches({ status: "something_new" })));
    expect(doc.body.textContent).toContain("Neural shadow inactivo");
    expect(doc.body.textContent).toContain("something_new");
  });

  it("still renders the comparison table when at least one row is active", () => {
    const matches = inactiveMatches({ status: "ok" }, 2);
    matches[0].prediction.neural_shadow = {
      active: true,
      status: "ok",
      run_id: "c34824b9",
      probabilities: { L: 0.6, E: 0.25, V: 0.15 },
      top_pick: "L",
      baseline_top_pick: "L",
      top_pick_changed: false,
      probability_delta: { L: 0.04, E: -0.01, V: -0.03 },
      max_abs_delta: 0.04,
    };
    const doc = dom(renderNeuralShadowPanel(matches));
    expect(doc.querySelector(".neural-shadow-table")).not.toBeNull();
    expect(doc.body.textContent).toContain("1 / 2");
  });
});
