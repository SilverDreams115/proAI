// R6.2 — cross-cutting executive UI contract for the Money Mode surfaces.
// Asserts the operator can tell, without scrolling into technical tables,
// whether today is JUGAR or NO JUGAR — and that NO JUGAR is never hidden.
import { describe, it, expect } from "vitest";
import { JSDOM } from "jsdom";
import { renderOperationalMoneyModeStatusPanel } from "../operational-money-mode-status.js";
import { renderMoneyModePanel } from "../money-mode.js";

function dom(html) {
  return new JSDOM(`<!doctype html><body>${html}</body>`).window.document;
}

const OPS_STATUS = {
  mode: "money_mode_operational_status",
  generated_at: "2026-06-24T05:00:00+00:00",
  scope: "active_upcoming",
  active_slate_count: 2,
  playable_slate_count: 0,
  blocked_slate_count: 2,
  slates: [
    {
      draw_code: "PG-2338", week_type: "weekend", match_count: 14, decision: "NO_JUGAR",
      reason: "6/14 posiciones siguen como fijo forzado.", confidence: "cautious",
      recommended_ticket: null, recommended_action: "No comprar boleto",
      critical_uncovered_count: 6, prediction_status: "persisted", money_mode_ready: true,
      warnings: [], playable: false,
    },
    {
      draw_code: "PGM-801", week_type: "midweek", match_count: 9, decision: "NO_JUGAR",
      reason: "4/9 posiciones siguen como fijo forzado.", confidence: "cautious",
      recommended_ticket: null, recommended_action: "No comprar boleto",
      critical_uncovered_count: 4, prediction_status: "live_available", money_mode_ready: true,
      warnings: ["live_predictions_only"], playable: false,
    },
  ],
  write_safety: { read_only: true },
};

function mmTicket(extra = {}) {
  return {
    recommended: false, covers_all_no_simple: false, uncovered_no_simple_positions: [4, 6, 7],
    simple_count: 0, no_simple_count: 6, double_count: 8, triple_count: 0,
    estimated_combinations: 256, estimated_cost: null, cost_note: "n/d", risk_level: "very_high",
    coverage_estimate: { expected_correct: 9.9, jackpot_probability: 0.0065, target_met: false },
    selections: [], ...extra,
  };
}

const MM_REPORT = {
  mode: "money_mode_release_candidate",
  slate: { draw_code: "PG-2338", week_type: "weekend", match_count: 14 },
  validation: { prediction_status: "persisted", warnings: [], data_blockers: [] },
  decision: { status: "NO_JUGAR", reason: "6/14 posiciones siguen como fijo forzado.", confidence: "cautious", recommended_ticket: null },
  tickets: { aggressive: mmTicket(), balanced: mmTicket(), conservative: mmTicket() },
  do_not_simple_positions: [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14],
  must_review_positions: [4, 6, 7],
  canary_influence_positions: [1, 2, 3, 5, 8, 11],
  matches: [],
  write_safety: { writes_performed: false, snapshots_created: false },
};

describe("Money Mode executive UI", () => {
  it("1 — playable=0 surfaces HOY: NO JUGAR at the top", () => {
    const doc = dom(renderOperationalMoneyModeStatusPanel(OPS_STATUS));
    expect(doc.querySelector(".ops-hero-headline").textContent).toContain("HOY: NO JUGAR");
  });

  it("2 — shows 'No comprar boleto hoy'", () => {
    const doc = dom(renderOperationalMoneyModeStatusPanel(OPS_STATUS));
    expect(doc.body.textContent.toLowerCase()).toContain("no comprar boleto hoy");
  });

  it("3 + 4 — both slates show NO JUGAR with a no-comprar action", () => {
    const doc = dom(renderOperationalMoneyModeStatusPanel(OPS_STATUS));
    for (const code of ["PG-2338", "PGM-801"]) {
      const card = [...doc.querySelectorAll(".ops-slate-card")].find((c) => c.textContent.includes(code));
      expect(card.textContent).toContain("NO JUGAR");
      expect(card.textContent).toContain("No comprar boleto");
    }
  });

  it("5 — Money Mode RC shows 'Boleto recomendado: ninguno' on NO JUGAR", () => {
    const doc = dom(renderMoneyModePanel(MM_REPORT));
    expect(doc.querySelector(".mm-recommended").textContent).toContain("ninguno");
  });

  it("6 — tickets shown as non-recommended simulations on NO JUGAR", () => {
    const doc = dom(renderMoneyModePanel(MM_REPORT));
    const cards = [...doc.querySelectorAll(".money-ticket")];
    expect(cards.length).toBe(3);
    expect(cards.every((c) => /simulaci/i.test(c.textContent))).toBe(true);
    expect(doc.body.textContent).not.toContain("RECOMENDADO");
  });

  it("7 — 'fijo forzado' never appears in either surface", () => {
    const ops = dom(renderOperationalMoneyModeStatusPanel(OPS_STATUS));
    const mm = dom(renderMoneyModePanel(MM_REPORT));
    expect(ops.body.textContent.toLowerCase()).not.toContain("fijo forzado");
    expect(mm.body.textContent.toLowerCase()).not.toContain("fijo forzado");
  });

  it("8 — raw acronym counts are not primary ticket-card text", () => {
    const doc = dom(renderMoneyModePanel(MM_REPORT));
    const card = doc.querySelector(".money-ticket");
    expect(card.textContent).toContain("Simples");
    expect(card.textContent).not.toContain("NS");
  });

  it("9 — NO JUGAR is not hidden (appears in hero and cards)", () => {
    const ops = dom(renderOperationalMoneyModeStatusPanel(OPS_STATUS));
    const mm = dom(renderMoneyModePanel(MM_REPORT));
    expect(ops.body.textContent).toContain("NO JUGAR");
    expect(mm.body.textContent).toContain("NO JUGAR");
  });

  it("10 — slate selector stays stable when panels render to their own bodies", () => {
    const doc = new JSDOM(
      `<!doctype html><body>
        <select id="slate-switcher"><option value="a">A</option></select>
        <div id="operational-money-mode-status-body"></div>
        <div id="money-mode-body"></div>
      </body>`,
    ).window.document;
    const before = doc.getElementById("slate-switcher").outerHTML;
    doc.getElementById("operational-money-mode-status-body").innerHTML =
      renderOperationalMoneyModeStatusPanel(OPS_STATUS);
    doc.getElementById("money-mode-body").innerHTML = renderMoneyModePanel(MM_REPORT);
    expect(doc.getElementById("slate-switcher").outerHTML).toBe(before);
  });
});
