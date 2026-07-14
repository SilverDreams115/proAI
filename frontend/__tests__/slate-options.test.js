import { describe, it, expect } from "vitest";
import { JSDOM } from "jsdom";
import { renderSlateOptionsPanel } from "../slate-options.js";

function dom(html) {
  return new JSDOM(`<!doctype html><body>${html}</body>`).window.document;
}

function opt(extra = {}) {
  return {
    key: "balanced", name: "Balanceada", recommended: false, playable: false,
    risk_level: "very_high", reason: "Simulación no recomendada · Money Mode bloquea la slate.",
    simple_count: 0, no_simple_count: 6, double_count: 8, triple_count: 0,
    combinations: 256, estimated_cost: null, price_status: "unverified",
    base_price_mxn: null, currency: "MXN", pricing_source: "pending_validation", selections: [],
    ...extra,
  };
}

const NO_JUGAR = {
  mode: "slate_options", draw_code: "PG-2338", week_type: "weekend",
  money_mode_decision: "NO_JUGAR", recommended_action: "NO_COMPRAR",
  decision_reason: "x",
  options: [
    opt({ key: "aggressive", name: "Agresiva" }),
    opt({ key: "balanced", name: "Balanceada" }),
    opt({ key: "conservative", name: "Conservadora", double_count: 4, triple_count: 4, combinations: 1296 }),
    opt({ key: "manual", name: "Manual / no recomendada", combinations: 1 }),
  ],
  pricing_verified: false,
  pricing_note: "Precio no verificado.",
  write_safety: { writes_performed: false },
};

describe("renderSlateOptionsPanel", () => {
  it("1 — always shows aggressive/balanced/conservative options", () => {
    const doc = dom(renderSlateOptionsPanel(NO_JUGAR));
    const text = doc.body.textContent;
    expect(text).toContain("Agresiva");
    expect(text).toContain("Balanceada");
    expect(text).toContain("Conservadora");
    expect(doc.querySelectorAll(".opt-card").length).toBe(4);
  });

  it("2 — when NO_JUGAR the options read as non-recommended simulations", () => {
    const doc = dom(renderSlateOptionsPanel(NO_JUGAR));
    expect(doc.querySelector(".opt-action").textContent).toContain("No comprar boleto");
    expect(doc.body.textContent).toContain("NO JUGAR");
    expect(doc.body.textContent).not.toContain("RECOMENDADA");
    for (const card of doc.querySelectorAll(".opt-card")) {
      expect(card.className).toContain("opt-card-sim");
    }
  });

  it("3 + 4 — shows combinations and 'precio no verificado' (never $0)", () => {
    const doc = dom(renderSlateOptionsPanel(NO_JUGAR));
    expect(doc.body.textContent).toContain("256");
    expect(doc.body.textContent).toContain("1296");
    expect(doc.body.textContent).toContain("precio no verificado");
    expect(doc.body.textContent).not.toContain("$0");
  });

  it("shows verified cost when the price is verified", () => {
    const verified = {
      ...NO_JUGAR,
      money_mode_decision: "JUGAR_BALANCEADO", recommended_action: "COMPRAR_BALANCEADA",
      pricing_verified: true,
      options: [
        opt({ key: "balanced", name: "Balanceada", recommended: true, playable: true,
          price_status: "verified", estimated_cost: 240, base_price_mxn: 15 }),
      ],
    };
    const doc = dom(renderSlateOptionsPanel(verified));
    expect(doc.body.textContent).toContain("$240 MXN");
    expect(doc.body.textContent).toContain("RECOMENDADA");
  });

  it("does not mutate a ticket-grid rendered alongside", () => {
    const doc = new JSDOM(
      `<!doctype html><body><div id="ticket-grid">REAL</div><div id="slate-options-body"></div></body>`,
    ).window.document;
    const before = doc.getElementById("ticket-grid").outerHTML;
    doc.getElementById("slate-options-body").innerHTML = renderSlateOptionsPanel(NO_JUGAR);
    expect(doc.getElementById("ticket-grid").outerHTML).toBe(before);
  });

  it("renders an empty state when there is no report", () => {
    const doc = dom(renderSlateOptionsPanel(null));
    expect(doc.querySelector(".empty-state")).not.toBeNull();
  });
});
