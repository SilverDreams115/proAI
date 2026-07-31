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

describe("Money Mode recommended-ticket selections", () => {
  const PLAY_REPORT = {
    ...MM_REPORT,
    decision: {
      status: "JUGAR_SOLO_CONSERVADOR",
      reason: "Solo el boleto conservador acota el riesgo.",
      confidence: "cautious",
      recommended_ticket: "conservative",
    },
    matches: [
      { position: 1, match: "San Luis vs Tijuana", money_mode_pick: ["V"], money_mode_pick_type: "no_simple", risk: "high", reason: [], simple_allowed: false },
      { position: 2, match: "Juarez vs Pumas", money_mode_pick: ["L"], money_mode_pick_type: "triple", risk: "high", reason: [], simple_allowed: false },
      { position: 3, match: "Atlas vs Monterrey", money_mode_pick: ["L"], money_mode_pick_type: "simple", risk: "medium", reason: [], simple_allowed: true },
    ],
    tickets: {
      aggressive: mmTicket(),
      balanced: mmTicket(),
      conservative: mmTicket({
        recommended: true,
        uncovered_no_simple_positions: [1],
        simple_count: 1, no_simple_count: 1, double_count: 0, triple_count: 1,
        estimated_combinations: 3888,
        selections: [
          { position: 3, pick: ["L"], type: "simple" },
          { position: 1, pick: ["V"], type: "no_simple" },
          { position: 2, pick: ["L", "E", "V"], type: "triple" },
        ],
      }),
    },
  };

  it("lists what the recommended ticket actually marks, in position order", () => {
    const doc = dom(renderMoneyModePanel(PLAY_REPORT));
    const table = doc.querySelector(".mm-selections-table");
    expect(table).not.toBeNull();
    const positions = [...table.querySelectorAll("tbody tr")].map(
      (tr) => tr.querySelector("td").textContent.trim(),
    );
    // Payload order is 3,1,2 — the operator reads a marking sheet top to bottom.
    expect(positions).toEqual(["1", "2", "3"]);
    expect(doc.body.textContent).toContain("San Luis vs Tijuana");
    expect(doc.body.textContent).toContain("Qué marca el boleto conservador");
  });

  it("shows every pick of a triple, not just the top one", () => {
    const doc = dom(renderMoneyModePanel(PLAY_REPORT));
    const rows = [...doc.querySelectorAll(".mm-selections-table tbody tr")];
    const triple = rows.find((tr) => tr.querySelector("td").textContent.trim() === "2");
    const marks = triple.querySelectorAll("td")[3].textContent;
    expect(marks).toContain("L");
    expect(marks).toContain("E");
    expect(marks).toContain("V");
  });

  it("flags the positions the ticket cannot cover", () => {
    const doc = dom(renderMoneyModePanel(PLAY_REPORT));
    const rows = [...doc.querySelectorAll(".mm-selections-table tbody tr")];
    const uncovered = rows.find((tr) => tr.querySelector("td").textContent.trim() === "1");
    expect(uncovered.className).toContain("row-changed");
    expect(uncovered.querySelector(".tone-danger")).not.toBeNull();
    expect(uncovered.textContent).toContain("Sin cobertura");
    // The label is not duplicated by a second badge saying the same thing.
    expect(uncovered.textContent.match(/[Ss]in cobertura/g)).toHaveLength(1);
    // A covered position carries no such warning.
    const covered = rows.find((tr) => tr.querySelector("td").textContent.trim() === "3");
    expect(covered.querySelector(".tone-danger")).toBeNull();
  });

  it("summarises the composition under the list", () => {
    const doc = dom(renderMoneyModePanel(PLAY_REPORT));
    const text = doc.querySelector(".mm-selections").textContent;
    expect(text).toContain("1 simple(s)");
    expect(text).toContain("1 triple(s)");
    expect(text).toContain("3888 combinación(es)");
  });

  it("never prints a marking sheet when the decision is NO JUGAR", () => {
    // Under NO JUGAR every ticket is a simulation; rendering one as a list of
    // marks would invite playing it.
    const doc = dom(renderMoneyModePanel(MM_REPORT));
    expect(doc.querySelector(".mm-selections-table")).toBeNull();
  });
});
