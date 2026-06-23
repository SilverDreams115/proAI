import { describe, it, expect } from "vitest";
import { JSDOM } from "jsdom";
import { renderTicketCanaryDryRunPanel } from "../ticket-canary-dry-run.js";

function dom(html) {
  return new JSDOM(`<!doctype html><body>${html}</body>`).window.document;
}

const PG_REPORT = {
  mode: "ticket_canary_dry_run",
  slate: { draw_code: "PG-2338", week_type: "weekend", match_count: 14 },
  summary: {
    current_ticket: { simple_count: 0, double_count: 9, triple_count: 5 },
    canary_ticket: { simple_count: 0, double_count: 11, triple_count: 3 },
    changed_positions: [1, 5],
    simple_removed_positions: [],
    new_double_positions: [1],
    new_triple_positions: [],
    canary_active_positions: [1, 2, 3, 5, 8, 11],
    ticket_changed: true,
    risk_delta: "lower",
  },
  matches: [
    {
      position: 7,
      match: "Norway vs France",
      canary_active: false,
      current_pick_type: "double",
      current_selection: ["V", "E"],
      canary_pick_type: "double",
      canary_selection: ["V", "E"],
      changed: false,
      presentation_guard: { simple_allowed: false, recommendation_label: "NO SIMPLE" },
      reason: ["risk_high", "no_dejar_simple"],
    },
  ],
  write_safety: { writes_performed: false, snapshot_created: false },
};

describe("renderTicketCanaryDryRunPanel", () => {
  it("renders the DRY-RUN badge", () => {
    const doc = dom(renderTicketCanaryDryRunPanel(PG_REPORT));
    expect(doc.querySelector(".shadow-badge").textContent).toContain("DRY-RUN");
    expect(doc.querySelector(".shadow-badge").textContent).toContain("TICKET NO ACTIVO");
  });

  it("shows current vs canary counts for PG-2338", () => {
    const doc = dom(renderTicketCanaryDryRunPanel(PG_REPORT));
    const text = doc.body.textContent;
    expect(text).toContain("PG-2338" === "PG-2338" ? "Ticket actual" : "");
    // counts present
    expect(doc.body.textContent).toContain("D 9");
    expect(doc.body.textContent).toContain("D 11");
    expect(doc.body.textContent.toLowerCase()).toContain("lower");
  });

  it("shows Norway vs France as NO SIMPLE (no confident simple)", () => {
    const doc = dom(renderTicketCanaryDryRunPanel(PG_REPORT));
    const row = doc.querySelector("tbody tr");
    expect(row.textContent).toContain("Norway vs France");
    expect(row.textContent).not.toContain("Simple "); // not rendered as a simple pick
    expect(row.textContent).toContain("no_dejar_simple");
  });

  it("handles PGM-801 with no persisted ticket (live simulated)", () => {
    const report = {
      mode: "ticket_canary_dry_run",
      slate: { draw_code: "PGM-801", week_type: "midweek", match_count: 9 },
      summary: {
        current_ticket: { simple_count: 0, double_count: 0, triple_count: 0 },
        canary_ticket: { simple_count: 0, double_count: 4, triple_count: 5 },
        changed_positions: [1, 2, 3],
        simple_removed_positions: [],
        new_double_positions: [1, 2],
        new_triple_positions: [],
        canary_active_positions: [1, 2, 3, 5, 8],
        ticket_changed: true,
        risk_delta: "mixed",
      },
      matches: [],
      write_safety: { writes_performed: false, snapshot_created: false },
    };
    const doc = dom(renderTicketCanaryDryRunPanel(report));
    expect(doc.body.textContent).toContain("sin ticket persistido");
    expect(doc.body.textContent).toContain("disponible desde predicciones live");
  });

  it("renders an empty state when there is no report", () => {
    const doc = dom(renderTicketCanaryDryRunPanel(null));
    expect(doc.querySelector(".empty-state")).not.toBeNull();
  });
});
