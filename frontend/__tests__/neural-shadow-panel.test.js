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
