// R6.3 — performance/deferred-loading contract for the heavy diagnostic panels.
// The prediction board must render without the diagnostics; they are fetched
// lazily, cached per slate_id, and render into their own bodies without
// touching the board, the slate selector, or each other.
import { describe, it, expect, beforeEach } from "vitest";
import { JSDOM } from "jsdom";
import {
  getCachedDiagnostics,
  setCachedDiagnostics,
  hasCachedDiagnostics,
  clearDiagnosticsCache,
} from "../slate-panel-cache.js";
import { renderMoneyModePanel } from "../money-mode.js";
import { renderExternalResultsPanel } from "../external-results.js";

describe("slate-panel-cache (per-slate diagnostics cache)", () => {
  beforeEach(() => clearDiagnosticsCache());

  it("caches and returns a slate's diagnostics payload (instant re-open)", () => {
    const payload = [{ a: 1 }, null, null, null, null, { mode: "money_mode" }, null, null];
    expect(hasCachedDiagnostics("pg")).toBe(false);
    setCachedDiagnostics("pg", payload);
    expect(hasCachedDiagnostics("pg")).toBe(true);
    expect(getCachedDiagnostics("pg")).toBe(payload);
  });

  it("isolates cache entries per slate_id", () => {
    setCachedDiagnostics("pg", ["pg-data"]);
    setCachedDiagnostics("ms", ["ms-data"]);
    expect(getCachedDiagnostics("pg")).toEqual(["pg-data"]);
    expect(getCachedDiagnostics("ms")).toEqual(["ms-data"]);
  });

  it("clears a single slate or the whole cache", () => {
    setCachedDiagnostics("pg", [1]);
    setCachedDiagnostics("ms", [2]);
    clearDiagnosticsCache("pg");
    expect(hasCachedDiagnostics("pg")).toBe(false);
    expect(hasCachedDiagnostics("ms")).toBe(true);
    clearDiagnosticsCache();
    expect(hasCachedDiagnostics("ms")).toBe(false);
  });

  it("returns null for an empty/missing slate id", () => {
    expect(getCachedDiagnostics(null)).toBeNull();
    expect(getCachedDiagnostics("unknown")).toBeNull();
  });
});

describe("deferred panels render independently of the board", () => {
  function makeDoc() {
    return new JSDOM(
      `<!doctype html><body>
        <div id="ticket-grid">REAL TICKET BOARD</div>
        <select id="slate-switcher"><option>PG-2338</option></select>
        <div id="money-mode-body"></div>
        <div id="external-results-body"></div>
      </body>`,
    ).window.document;
  }

  const MM = {
    mode: "money_mode_release_candidate",
    slate: { draw_code: "PG-2338", week_type: "weekend", match_count: 14 },
    validation: { prediction_status: "persisted", warnings: [], data_blockers: [] },
    decision: { status: "NO_JUGAR", reason: "x", confidence: "cautious", recommended_ticket: null },
    tickets: { aggressive: t(), balanced: t(), conservative: t() },
    do_not_simple_positions: [1], must_review_positions: [], canary_influence_positions: [],
    matches: [], write_safety: { writes_performed: false, snapshots_created: false },
  };
  function t() {
    return { recommended: false, covers_all_no_simple: false, uncovered_no_simple_positions: [],
      simple_count: 0, no_simple_count: 6, double_count: 8, triple_count: 0,
      estimated_combinations: 256, estimated_cost: null, cost_note: "n/d", risk_level: "very_high",
      coverage_estimate: {}, selections: [] };
  }

  it("rendering a diagnostic panel never mutates the board or the slate selector", () => {
    const doc = makeDoc();
    const boardBefore = doc.getElementById("ticket-grid").outerHTML;
    const selBefore = doc.getElementById("slate-switcher").outerHTML;
    doc.getElementById("money-mode-body").innerHTML = renderMoneyModePanel(MM);
    doc.getElementById("external-results-body").innerHTML = renderExternalResultsPanel({
      provider: "football_data_org", enabled: false, status: "disabled",
      coverage: { matched: 0, total: 14, rate: 0 }, matches: [],
      write_safety: { writes_performed: false },
    });
    expect(doc.getElementById("ticket-grid").outerHTML).toBe(boardBefore);
    expect(doc.getElementById("slate-switcher").outerHTML).toBe(selBefore);
  });

  it("an empty/null diagnostics payload renders a harmless empty state (no crash)", () => {
    const doc = makeDoc();
    doc.getElementById("money-mode-body").innerHTML = renderMoneyModePanel(null);
    expect(doc.querySelector("#money-mode-body .empty-state")).not.toBeNull();
  });
});
