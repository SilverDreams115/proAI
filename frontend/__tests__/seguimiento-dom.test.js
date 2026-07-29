// Seguimiento, end to end through the DOM: the tab wiring, the card list and
// what "Ver comparación" actually puts on screen.
//
// The other Seguimiento suites call the pure render helpers directly, which is
// how two user-visible breakages got through with every test green: the active
// concurso was filtered out of the card list, and opening a slate with no
// results yet returned zero rows instead of the predictions. Both were in the
// wiring and the branch selection, not in the markup helpers.
//
// So this suite drives the real `initLiveTracking` against jsdom, using
// payloads captured verbatim from the running production API (see
// ./fixtures). No hand-written shapes — a fixture that drifts from the
// backend contract is itself a finding.
import { describe, it, expect, beforeEach } from "vitest";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";

import { initLiveTracking } from "../live-tracking.js";

const here = dirname(fileURLToPath(import.meta.url));
const dashboard = JSON.parse(
  readFileSync(join(here, "fixtures", "live-dashboard.json"), "utf8"),
);
const comparison = JSON.parse(
  readFileSync(join(here, "fixtures", "result-comparison-pg2344.json"), "utf8"),
);

const ACTIVE_SLATE_ID = comparison.slate_id;

function mount() {
  document.body.innerHTML = `
    <div id="live-tracking-panel"></div>
    <div id="live-tracking-detail"></div>`;
  return {
    container: document.getElementById("live-tracking-panel"),
    detailContainer: document.getElementById("live-tracking-detail"),
  };
}

// Mirrors what app.js passes in: a fetch that resolves the tracking endpoints
// and returns null for anything unavailable.
function fetchJson(path) {
  if (path === "/slates/live/dashboard") return Promise.resolve(dashboard);
  if (path === `/slates/${ACTIVE_SLATE_ID}/result-comparison`) {
    return Promise.resolve(comparison);
  }
  // The two additive overlays app.js fires alongside the comparison.
  return Promise.resolve(null);
}

describe("Seguimiento panel (real payloads, real DOM)", () => {
  let dom;

  beforeEach(() => {
    dom = mount();
  });

  it("lists every slate the API returns, including the active concurso", async () => {
    const api = initLiveTracking({ ...dom, fetchJson });
    await api.refresh();

    const cards = dom.container.querySelectorAll(".track-card");
    const expected = (dashboard.open || []).length + (dashboard.closed || []).length;
    expect(cards.length).toBe(expected);

    const codes = [...dom.container.querySelectorAll(".track-code")].map((n) => n.textContent);
    for (const entry of (dashboard.open || []).concat(dashboard.closed || [])) {
      expect(codes).toContain(entry.draw_code);
    }
    // The regression that started this: the open concurso must be on screen.
    expect(codes).toContain("PG-2344");
  });

  it("gives every card a working 'Ver comparación' button", async () => {
    const api = initLiveTracking({ ...dom, fetchJson });
    await api.refresh();

    const buttons = dom.container.querySelectorAll(".track-detail-btn");
    expect(buttons.length).toBe(dom.container.querySelectorAll(".track-card").length);
    for (const button of buttons) {
      expect(button.dataset.slate).toBeTruthy();
    }
  });

  it("clicking 'Ver comparación' renders one row per match with its 1/X/2 and result", async () => {
    const api = initLiveTracking({ ...dom, fetchJson });
    await api.refresh();

    const button = [...dom.container.querySelectorAll(".track-detail-btn")].find(
      (node) => node.dataset.slate === ACTIVE_SLATE_ID,
    );
    expect(button).toBeTruthy();

    button.click();
    await api.showDetail(ACTIVE_SLATE_ID);

    const rows = dom.detailContainer.querySelectorAll(".cmp-row");
    expect(rows.length).toBe(comparison.matches.length);

    // Every row carries the three outcome chips, and exactly one is the pick.
    for (const row of rows) {
      expect(row.querySelectorAll(".oc-chip").length).toBe(3);
      expect(row.querySelectorAll(".oc-pick").length).toBe(1);
    }

    // Teams and the visible probability vector are on screen, not just in the
    // payload — this is what the operator reads.
    const text = dom.detailContainer.textContent;
    for (const match of comparison.matches) {
      expect(text).toContain(match.home_team_name);
      expect(text).toContain(match.away_team_name);
    }
    expect(dom.detailContainer.querySelectorAll(".vp-l").length).toBe(rows.length);
    expect(dom.detailContainer.querySelectorAll(".vp-e").length).toBe(rows.length);
    expect(dom.detailContainer.querySelectorAll(".vp-v").length).toBe(rows.length);

    // PG-2344 has not been played, so every result cell reads as pending and
    // the missing-acta notice rides above the table rather than replacing it.
    expect(dom.detailContainer.querySelectorAll(".status-pending").length).toBe(rows.length);
    expect(dom.detailContainer.querySelector(".cmp-table")).not.toBeNull();
    expect(dom.detailContainer.querySelector(".empty-results")).not.toBeNull();
  });

  it("the fixtures still match the API contract the renderer reads", () => {
    // Cheap drift alarm: if the backend stops sending one of these the suite
    // above would keep passing against a stale shape.
    for (const key of ["open", "closed"]) {
      expect(Array.isArray(dashboard[key])).toBe(true);
    }
    expect(dashboard.open.concat(dashboard.closed).every((e) => e.draw_code && e.slate_id)).toBe(true);
    for (const match of comparison.matches) {
      expect(match).toHaveProperty("position");
      expect(match).toHaveProperty("predicted_outcome");
      expect(match).toHaveProperty("home_probability");
      expect(match).toHaveProperty("draw_probability");
      expect(match).toHaveProperty("away_probability");
      expect(match).toHaveProperty("result_code");
    }
  });
});
