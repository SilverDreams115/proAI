import { describe, it, expect } from "vitest";
import { JSDOM } from "jsdom";
import {
  deriveLnResultsObserverAlert,
  renderLiveResultsObserverPanel,
  snapshotLnResultsObserver,
} from "../live-results-observer-panel.js";

function dom(html) {
  return new JSDOM(`<!doctype html><body>${html}</body>`).window.document;
}

const REPORT = {
  status: "attention_required",
  observer_enabled: true,
  fetch_enabled: true,
  observe_interval_minutes: 5,
  pull_ready: true,
  warnings: ["no_active_slate_results_seen_yet"],
  sources: [{ name: "LN Progol Resultados" }],
  latest_ingestion: {
    last_success_at: "2026-07-14T01:58:56Z",
    slate_count: 2,
    result_rows: 23,
    draws: [
      { draw_code: "PG-2341", result_rows: 14, last_updated_at: "2026-07-14T01:58:56Z" },
      { draw_code: "PGM-803", result_rows: 9, last_updated_at: "2026-07-14T01:58:56Z" },
    ],
  },
  active_slates: [
    { draw_code: "PGM-804", week_type: "midweek", pull_state: "waiting_results", completed_count: 0, live_count: 0, match_count: 9, sources: [], last_updated_at: null },
    { draw_code: "PG-2342", week_type: "weekend", pull_state: "waiting_results", completed_count: 0, live_count: 0, match_count: 14, sources: [], last_updated_at: null },
  ],
};

describe("renderLiveResultsObserverPanel", () => {
  it("renders pull readiness, source, latest ingestion and active slates", () => {
    const doc = dom(renderLiveResultsObserverPanel(REPORT));
    expect(doc.body.textContent).toContain("Pull listo");
    expect(doc.body.textContent).toContain("LN Progol Resultados");
    expect(doc.body.textContent).toContain("23 en 2 slate");
    expect(doc.body.textContent).toContain("PGM-804");
    expect(doc.body.textContent).toContain("Esperando LN");
  });

  it("derives a new-results alert from a waiting to complete transition", () => {
    const before = snapshotLnResultsObserver(REPORT);
    const after = {
      ...REPORT,
      active_slates: [
        { ...REPORT.active_slates[0], pull_state: "complete", completed_count: 9, sources: ["LN Progol Resultados"], last_updated_at: "2026-07-14T02:10:00Z" },
      ],
    };
    const alert = deriveLnResultsObserverAlert(after, before);
    const doc = dom(renderLiveResultsObserverPanel(after, alert));
    expect(alert.slates[0].draw_code).toBe("PGM-804");
    expect(doc.body.textContent).toContain("Resultados nuevos detectados");
  });

  it("renders empty state without a report", () => {
    const doc = dom(renderLiveResultsObserverPanel(null));
    expect(doc.querySelector(".empty-state")).not.toBeNull();
  });
});
