// R6.4 — pricing display contract across the options surface.
import { describe, it, expect } from "vitest";
import { JSDOM } from "jsdom";
import { renderSlateOptionsPanel } from "../slate-options.js";

function dom(html) {
  return new JSDOM(`<!doctype html><body>${html}</body>`).window.document;
}

function report(priceStatus, cost) {
  return {
    mode: "slate_options", draw_code: "PG-2338", week_type: "weekend",
    money_mode_decision: "NO_JUGAR", recommended_action: "NO_COMPRAR", decision_reason: "x",
    pricing_verified: priceStatus === "verified",
    options: [
      {
        key: "balanced", name: "Balanceada", recommended: false, playable: false,
        risk_level: "medium", reason: "x", simple_count: 0, no_simple_count: 0,
        double_count: 8, triple_count: 0, combinations: 256,
        estimated_cost: cost, price_status: priceStatus, base_price_mxn: cost ? 15 : null,
        currency: "MXN", pricing_source: "x", selections: [],
      },
    ],
    pricing_note: "x", write_safety: { writes_performed: false },
  };
}

describe("pricing display", () => {
  it("shows combinations always", () => {
    const doc = dom(renderSlateOptionsPanel(report("unverified", null)));
    expect(doc.body.textContent).toContain("Combinaciones:");
    expect(doc.body.textContent).toContain("256");
  });

  it("unverified -> 'precio no verificado', never $0 and never a number", () => {
    const doc = dom(renderSlateOptionsPanel(report("unverified", null)));
    const text = doc.body.textContent;
    expect(text).toContain("precio no verificado");
    expect(text).not.toContain("$0");
    expect(text).not.toMatch(/\$\d/);
  });

  it("verified -> shows the peso amount", () => {
    const doc = dom(renderSlateOptionsPanel(report("verified", 3840)));
    expect(doc.body.textContent).toContain("$3840 MXN");
  });

  it("never renders a $0 cost even if estimated_cost is 0-like but unverified", () => {
    const doc = dom(renderSlateOptionsPanel(report("unverified", 0)));
    // unverified path ignores any cost value and shows the unverified label.
    expect(doc.body.textContent).toContain("precio no verificado");
    expect(doc.body.textContent).not.toContain("$0");
  });
});
