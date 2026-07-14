import { describe, it, expect } from "vitest";
import { JSDOM } from "jsdom";
import { renderLearningDashboard } from "../learning-dashboard.js";

function dom(html) {
  return new JSDOM(`<!doctype html><body>${html}</body>`).window.document;
}

const PENDING = {
  draw_code: "PG-2337",
  state: "closed_pending_results",
  comparable: false,
  match_count: 14,
  prediction_count: 14,
  canonical_result_count: 0,
  conflicts: 0,
  classification: "official_but_no_results_yet",
  blockers: ["missing_local_results", "incomplete_coverage"],
};

const PENDING_MS = { ...PENDING, draw_code: "PGM-800", match_count: 9, prediction_count: 9 };

const COMPARABLE = {
  draw_code: "PG-DONE",
  state: "closed_comparable",
  comparable: true,
  match_count: 4,
  prediction_count: 4,
  canonical_result_count: 4,
  conflicts: 0,
  classification: "official_real",
  blockers: [],
  hits: 3,
  total: 4,
};

const CONFLICT = {
  draw_code: "PG-CONF",
  state: "closed_conflict",
  comparable: false,
  match_count: 4,
  prediction_count: 4,
  canonical_result_count: 3,
  conflicts: 1,
  classification: "official_real",
  blockers: ["result_conflict"],
};

function inventory(slates) {
  return { slate_count: slates.length, comparable_count: slates.filter((s) => s.comparable).length, slates };
}

describe("renderLearningDashboard", () => {
  it("1+2 — shows PG-2337 and PGM-800 in the learning dashboard", () => {
    const doc = dom(renderLearningDashboard(inventory([PENDING, PENDING_MS]), null));
    expect(doc.body.textContent).toContain("PG-2337");
    expect(doc.body.textContent).toContain("PGM-800");
  });

  it("3 — pending slates clearly say they await official results", () => {
    const doc = dom(renderLearningDashboard(inventory([PENDING]), null));
    expect(doc.body.textContent.toLowerCase()).toContain("pendiente de resultados oficiales");
  });

  it("4 — a comparable slate shows its hits", () => {
    const doc = dom(renderLearningDashboard(inventory([COMPARABLE]), null));
    expect(doc.body.textContent).toContain("comparable");
    expect(doc.body.textContent).toContain("Aciertos: 3/4");
  });

  it("5 — conflicts are surfaced, not hidden", () => {
    const doc = dom(renderLearningDashboard(inventory([CONFLICT]), null));
    expect(doc.body.textContent).toContain("conflicto");
    expect(doc.body.textContent).toContain("result_conflict");
  });

  it("6 — blockers are shown for blocked slates", () => {
    const doc = dom(renderLearningDashboard(inventory([PENDING]), null));
    expect(doc.body.textContent).toContain("missing_local_results");
  });

  it("7 — read-only badge present; nothing touches Money Mode / activation", () => {
    const html = renderLearningDashboard(inventory([PENDING, COMPARABLE]), { training_ready: false, reason: "x" });
    expect(html).toContain("SOLO LECTURA");
    expect(html.toLowerCase()).not.toContain("activar");
  });

  it("8 — render is pure (no auto-switch / side effects)", () => {
    const inv = inventory([PENDING, COMPARABLE]);
    const a = renderLearningDashboard(inv, null);
    const b = renderLearningDashboard(inv, null);
    expect(a).toBe(b);
  });

  it("reflects training_ready from the readiness report", () => {
    const doc = dom(renderLearningDashboard(inventory([COMPARABLE]), { training_ready: false, reason: "blocked" }));
    expect(doc.body.textContent).toContain("training ready:");
    expect(doc.body.textContent).toContain("blocked");
  });
});
