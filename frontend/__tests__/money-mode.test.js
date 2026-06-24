import { describe, it, expect } from "vitest";
import { JSDOM } from "jsdom";
import { renderMoneyModePanel } from "../money-mode.js";

function dom(html) {
  return new JSDOM(`<!doctype html><body>${html}</body>`).window.document;
}

function ticket(extra = {}) {
  return {
    label: "balanced",
    playable: false,
    recommended: false,
    covers_all_no_simple: false,
    uncovered_no_simple_positions: [4, 6, 7],
    simple_count: 0,
    no_simple_count: 6,
    double_count: 8,
    triple_count: 0,
    estimated_combinations: 256,
    estimated_cost: null,
    cost_note: "costo unitario por combinacion no configurado",
    risk_level: "very_high",
    coverage_estimate: { expected_correct: 9.9, jackpot_probability: 0.0065, target_met: false },
    selections: [
      { position: 7, pick: ["V", "E"], type: "no_simple" },
      { position: 2, pick: ["L", "E"], type: "double" },
    ],
    ...extra,
  };
}

const PG_REPORT = {
  mode: "money_mode_release_candidate",
  slate: { draw_code: "PG-2338", week_type: "weekend", match_count: 14 },
  validation: { prediction_status: "persisted", warnings: [], data_blockers: [] },
  decision: {
    status: "NO_JUGAR",
    reason: "Demasiados NO SIMPLE sin cobertura posible: 6/14 posiciones siguen como fijo forzado.",
    confidence: "cautious",
    recommended_ticket: null,
  },
  tickets: {
    aggressive: ticket({ risk_level: "very_high" }),
    balanced: ticket({ recommended: false }),
    conservative: ticket({ double_count: 4, triple_count: 4, estimated_combinations: 1296 }),
  },
  do_not_simple_positions: [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14],
  must_review_positions: [4, 6, 7],
  canary_influence_positions: [1, 2, 3, 5, 8, 11],
  matches: [
    {
      position: 7,
      match: "Norway vs France",
      primary_signal: "V",
      recommendation: "NO SIMPLE",
      money_mode_pick: ["V", "E"],
      money_mode_pick_type: "double",
      reason: ["risk_high", "no_dejar_simple", "suspicious_class"],
      canary_active: false,
      risk: "high",
      simple_allowed: false,
    },
  ],
  write_safety: { writes_performed: false, snapshots_created: false },
};

describe("renderMoneyModePanel", () => {
  it("1 — renders the Money Mode panel with the read-only badge", () => {
    const doc = dom(renderMoneyModePanel(PG_REPORT));
    expect(doc.querySelector(".money-mode-panel")).not.toBeNull();
    expect(doc.querySelector(".shadow-badge").textContent).toContain("MONEY MODE");
    expect(doc.querySelector(".shadow-badge").textContent).toContain("READ-ONLY");
  });

  it("2 — shows the JUGAR / NO JUGAR decision", () => {
    const doc = dom(renderMoneyModePanel(PG_REPORT));
    expect(doc.querySelector(".money-decision-label").textContent).toContain("NO JUGAR");
  });

  it("3 — shows the recommended ticket", () => {
    const playable = {
      ...PG_REPORT,
      decision: { ...PG_REPORT.decision, status: "JUGAR_BALANCEADO", recommended_ticket: "balanced" },
      tickets: {
        ...PG_REPORT.tickets,
        balanced: ticket({ recommended: true, covers_all_no_simple: true, risk_level: "medium" }),
      },
    };
    const doc = dom(renderMoneyModePanel(playable));
    expect(doc.body.textContent).toContain("Boleto recomendado");
    expect(doc.body.textContent).toContain("Balanceado");
    expect(doc.querySelector(".money-ticket-balanced").textContent).toContain("RECOMENDADO");
  });

  it("4 — shows aggressive / balanced / conservative tickets", () => {
    const doc = dom(renderMoneyModePanel(PG_REPORT));
    const text = doc.body.textContent;
    expect(text).toContain("Agresivo");
    expect(text).toContain("Balanceado");
    expect(text).toContain("Conservador");
    // combination counts surfaced for cost/coverage awareness.
    expect(text).toContain("256");
    expect(text).toContain("1296");
  });

  it("5 — shows the NO SIMPLE positions", () => {
    const doc = dom(renderMoneyModePanel(PG_REPORT));
    expect(doc.body.textContent).toContain("Partidos NO SIMPLE");
    const row = doc.querySelector("tbody tr");
    expect(row.textContent).toContain("NO SIMPLE");
  });

  it("6 — Norway vs France never renders as a simple pick", () => {
    const doc = dom(renderMoneyModePanel(PG_REPORT));
    const row = doc.querySelector("tbody tr");
    expect(row.textContent).toContain("Norway vs France");
    expect(row.textContent).not.toContain("Simple "); // never a confident simple
    expect(row.textContent).toContain("no_dejar_simple");
  });

  it("7 — PGM-801 with live predictions shows Money Mode + live note", () => {
    const report = {
      ...PG_REPORT,
      slate: { draw_code: "PGM-801", week_type: "midweek", match_count: 9 },
      validation: { prediction_status: "live_available", warnings: ["live_predictions_only"], data_blockers: [] },
    };
    const doc = dom(renderMoneyModePanel(report));
    expect(doc.querySelector(".money-mode-panel")).not.toBeNull();
    expect(doc.body.textContent.toLowerCase()).toContain("sin ticket persistido");
    expect(doc.body.textContent.toLowerCase()).toContain("calculado en vivo");
  });

  it("8 — rendering Money Mode does not mutate the real ticket-grid", () => {
    const doc = new JSDOM(
      `<!doctype html><body><div id="ticket-grid" data-keep="1">REAL TICKET</div><div id="money-mode-body"></div></body>`,
    ).window.document;
    const grid = doc.getElementById("ticket-grid");
    const before = grid.outerHTML;
    // Pure render: assign only to the money-mode body, like app.js does.
    doc.getElementById("money-mode-body").innerHTML = renderMoneyModePanel(PG_REPORT);
    expect(doc.getElementById("ticket-grid").outerHTML).toBe(before);
    expect(doc.getElementById("ticket-grid").textContent).toBe("REAL TICKET");
  });

  it("9 — slate selector element is left untouched by the panel render", () => {
    const doc = new JSDOM(
      `<!doctype html><body><select id="slate-switcher"><option value="a">A</option></select><div id="money-mode-body"></div></body>`,
    ).window.document;
    const before = doc.getElementById("slate-switcher").outerHTML;
    doc.getElementById("money-mode-body").innerHTML = renderMoneyModePanel(PG_REPORT);
    expect(doc.getElementById("slate-switcher").outerHTML).toBe(before);
  });

  it("renders an empty state when there is no report", () => {
    const doc = dom(renderMoneyModePanel(null));
    expect(doc.querySelector(".empty-state")).not.toBeNull();
  });
});
