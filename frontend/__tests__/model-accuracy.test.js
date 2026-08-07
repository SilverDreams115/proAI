import { describe, it, expect } from "vitest";
import { JSDOM } from "jsdom";
import { renderModelAccuracy } from "../model-accuracy.js";

function dom(html) {
  return new JSDOM(`<!doctype html><body>${html}</body>`).window.document;
}

// Shape and values copied from a live GET /api/training/neural/active so the
// panel is locked against what the backend actually publishes, not against a
// convenient invention.
function activePayload(overrides = {}) {
  return {
    status: "ok",
    available: true,
    run_id: "f31f5fd6-29aa-4c6b-8374-cbc879b24859",
    model_name: "neural_baseline_active",
    training_sample_size: 106,
    comparison: {
      status: "ok",
      evaluated_rows: 106,
      baseline: { accuracy: 0.4245, brier_score: 0.6938, cross_entropy: 1.1868 },
      neural: { accuracy: 0.5377, brier_score: 0.5904, cross_entropy: 0.9784 },
      accuracy_delta: 0.1132,
      brier_delta: 0.1034,
      neural_better_accuracy: true,
      neural_better_brier: true,
    },
    dataset: { rows: 106, slates: 9, canonical_rows: 106, sign_only_rows: 0 },
    ...overrides,
  };
}

describe("renderModelAccuracy", () => {
  it("draws one arc per model with the published accuracies", () => {
    const doc = dom(renderModelAccuracy(activePayload()));
    const values = [...doc.querySelectorAll(".accuracy-dial-value")].map((n) => n.textContent.trim());
    // 0.4245 -> "42.4%": 42.45 is not exactly representable and lands just
    // below the midpoint, so toFixed(1) truncates rather than rounding up.
    expect(values).toEqual(["42.4%", "53.8%"]);
    expect(doc.querySelectorAll(".accuracy-arc")).toHaveLength(2);
  });

  it("shows the hit tally so the sample size cannot be missed", () => {
    const doc = dom(renderModelAccuracy(activePayload()));
    const tallies = [...doc.querySelectorAll(".accuracy-dial-tally")].map((n) => n.textContent.trim());
    expect(tallies).toEqual(["45 de 106 aciertos", "57 de 106 aciertos"]);
  });

  it("hatches the neural arc when it was scored on the rows it trained on", () => {
    const doc = dom(renderModelAccuracy(activePayload()));
    const [sistema, neural] = doc.querySelectorAll(".accuracy-arc");
    // Solid: production earned this live, on rows it had never seen.
    expect(sistema.getAttribute("stroke")).toBe("#7dd3fc");
    // Hatched: same geometry, different kind of number. The trailing colour
    // is the SVG paint fallback, so a pattern that fails to resolve degrades
    // to a flat arc rather than to no arc at all.
    expect(neural.getAttribute("stroke")).toBe("url(#acc-hatch-neural) #ffd166");
    expect(doc.querySelector("#acc-hatch-neural")).not.toBeNull();
    expect(doc.querySelector(".accuracy-verdict-warn")).not.toBeNull();
    expect(doc.body.textContent).toContain("no son comparables todavía");
  });

  it("drops the hatch once the candidate is evaluated out of sample", () => {
    // Fewer training rows than evaluated rows means the evaluation set was
    // not fully memorised, so the two numbers stand on the same footing.
    const doc = dom(renderModelAccuracy(activePayload({ training_sample_size: 70 })));
    const [, neural] = doc.querySelectorAll(".accuracy-arc");
    expect(neural.getAttribute("stroke")).toBe("#ffd166");
    expect(doc.querySelector(".accuracy-verdict-ok")).not.toBeNull();
    expect(doc.body.textContent).toContain("filas no vistas");
  });

  it("scales each arc to its own accuracy", () => {
    const doc = dom(renderModelAccuracy(activePayload()));
    const [sistema, neural] = doc.querySelectorAll(".accuracy-arc");
    const filled = (node) => Number(node.getAttribute("stroke-dasharray").split(" ")[0]);
    const circumference = 2 * Math.PI * 52;
    expect(filled(sistema)).toBeCloseTo(0.4245 * circumference, 1);
    expect(filled(neural)).toBeCloseTo(0.5377 * circumference, 1);
    expect(filled(neural)).toBeGreaterThan(filled(sistema));
  });

  it("marks the better side of each metric row, lower-is-better included", () => {
    const doc = dom(renderModelAccuracy(activePayload()));
    const rows = [...doc.querySelectorAll(".accuracy-metrics tbody tr")];
    const leaderOf = (row) => {
      const cells = [...row.querySelectorAll("td")];
      return cells.findIndex((cell) => cell.classList.contains("accuracy-leads"));
    };
    // Accuracy: higher wins, so neural (index 1) leads.
    expect(leaderOf(rows[0])).toBe(1);
    // Brier and cross-entropy: lower wins, and neural is lower on both.
    expect(leaderOf(rows[1])).toBe(1);
    expect(leaderOf(rows[2])).toBe(1);
  });

  it("credits the system when it wins a lower-is-better metric", () => {
    const payload = activePayload();
    payload.comparison.baseline.brier_score = 0.4;
    const doc = dom(renderModelAccuracy(payload));
    const brierRow = [...doc.querySelectorAll(".accuracy-metrics tbody tr")][1];
    const cells = [...brierRow.querySelectorAll("td")];
    expect(cells[0].classList.contains("accuracy-leads")).toBe(true);
    expect(cells[1].classList.contains("accuracy-leads")).toBe(false);
  });

  it("explains an absent neural model instead of drawing an empty circle", () => {
    const doc = dom(renderModelAccuracy({ status: "no_active_model", available: false }));
    expect(doc.querySelectorAll(".accuracy-arc")).toHaveLength(0);
    expect(doc.body.textContent).toContain("Sin modelo neural activo");
  });

  it("explains a missing comparison rather than inventing one", () => {
    const doc = dom(renderModelAccuracy(activePayload({ comparison: { status: "not_enough_data" } })));
    expect(doc.querySelectorAll(".accuracy-arc")).toHaveLength(0);
    expect(doc.body.textContent).toContain("Comparación no disponible");
  });

  it("escapes untrusted payload text", () => {
    const doc = dom(renderModelAccuracy(activePayload({ run_id: "<img src=x onerror=alert(1)>" })));
    expect(doc.querySelector("img")).toBeNull();
  });
});
