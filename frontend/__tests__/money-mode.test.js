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

// NO_JUGAR fixture — its backend reason intentionally contains "fijo forzado"
// so we can assert that wording NEVER reaches the operator view.
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

describe("renderMoneyModePanel (executive)", () => {
  it("renders the read-only Money Mode panel", () => {
    const doc = dom(renderMoneyModePanel(PG_REPORT));
    expect(doc.querySelector(".money-mode-panel")).not.toBeNull();
    expect(doc.querySelector(".shadow-badge").textContent).toContain("MONEY MODE");
    expect(doc.querySelector(".shadow-badge").textContent.toLowerCase()).toContain("solo lectura");
  });

  it("leads with the NO JUGAR decision and a plain action", () => {
    const doc = dom(renderMoneyModePanel(PG_REPORT));
    const hero = doc.querySelector(".mm-hero-decision");
    expect(hero.textContent).toContain("NO JUGAR");
    expect(doc.querySelector(".mm-hero-action").textContent).toContain("No comprar boleto");
  });

  it("shows 'Boleto recomendado: ninguno' when NO JUGAR", () => {
    const doc = dom(renderMoneyModePanel(PG_REPORT));
    const rec = doc.querySelector(".mm-recommended");
    expect(rec.textContent).toContain("Boleto recomendado");
    expect(rec.textContent).toContain("ninguno");
  });

  it("shows the three tickets as non-recommended simulations when NO JUGAR", () => {
    const doc = dom(renderMoneyModePanel(PG_REPORT));
    const cards = [...doc.querySelectorAll(".money-ticket")];
    expect(cards.length).toBe(3);
    for (const card of cards) {
      expect(card.textContent.toLowerCase()).toContain("simulación");
      // never the green RECOMENDADO emphasis
      expect(card.textContent).not.toContain("RECOMENDADO");
      expect(card.className).toContain("mm-ticket-sim");
    }
  });

  it("uses expanded count labels, not raw acronyms, as the primary card data", () => {
    const doc = dom(renderMoneyModePanel(PG_REPORT));
    const card = doc.querySelector(".money-ticket");
    const text = card.textContent;
    expect(text).toContain("Simples");
    expect(text).toContain("Dobles");
    expect(text).toContain("Triples");
    // the "S0 · NS6 · D8 · T0" acronym string is never primary card text
    expect(text).not.toContain("NS");
    expect(text).not.toMatch(/S0\s*·/);
  });

  it("never shows the phrase 'fijo forzado' anywhere", () => {
    const doc = dom(renderMoneyModePanel(PG_REPORT));
    expect(doc.body.textContent.toLowerCase()).not.toContain("fijo forzado");
    expect(doc.body.textContent).toContain("sin cobertura suficiente");
  });

  it("keeps jackpot / E[aciertos] only inside the collapsible technical detail", () => {
    const doc = dom(renderMoneyModePanel(PG_REPORT));
    const details = doc.querySelector("details.mm-technical");
    expect(details).not.toBeNull();
    expect(details.textContent).toContain("Jackpot");
    expect(details.textContent).toContain("E[aciertos]");
    // not present in the executive hero
    expect(doc.querySelector(".mm-hero").textContent).not.toContain("Jackpot");
  });

  it("Norway vs France never renders as a simple pick", () => {
    const doc = dom(renderMoneyModePanel(PG_REPORT));
    const row = [...doc.querySelectorAll("tbody tr")].find((r) => r.textContent.includes("Norway"));
    expect(row).toBeTruthy();
    expect(row.textContent).not.toContain("Simple ");
    expect(row.textContent).toContain("no_dejar_simple");
  });

  it("marks the recommended ticket only when a JUGAR decision is reached", () => {
    const playable = {
      ...PG_REPORT,
      decision: { status: "JUGAR_BALANCEADO", reason: "ok", confidence: "cautious", recommended_ticket: "balanced" },
      tickets: {
        ...PG_REPORT.tickets,
        balanced: ticket({ recommended: true, covers_all_no_simple: true, risk_level: "medium" }),
      },
    };
    const doc = dom(renderMoneyModePanel(playable));
    expect(doc.querySelector(".mm-hero-decision").textContent).toContain("JUGAR");
    expect(doc.querySelector(".mm-hero-action").textContent.toLowerCase()).toContain("jugar boleto");
    expect(doc.body.textContent).toContain("RECOMENDADO");
    // no ticket should be flagged as a blocked simulation when playable
    expect(doc.querySelector(".mm-ticket-sim")).toBeNull();
  });

  it("does not mutate the real ticket-grid", () => {
    const doc = new JSDOM(
      `<!doctype html><body><div id="ticket-grid">REAL TICKET</div><div id="money-mode-body"></div></body>`,
    ).window.document;
    const before = doc.getElementById("ticket-grid").outerHTML;
    doc.getElementById("money-mode-body").innerHTML = renderMoneyModePanel(PG_REPORT);
    expect(doc.getElementById("ticket-grid").outerHTML).toBe(before);
  });

  it("renders an empty state when there is no report", () => {
    const doc = dom(renderMoneyModePanel(null));
    expect(doc.querySelector(".empty-state")).not.toBeNull();
  });
});
