import { describe, it, expect } from "vitest";
import { JSDOM } from "jsdom";
import { renderOperationalMoneyModeStatusPanel } from "../operational-money-mode-status.js";

function dom(html) {
  return new JSDOM(`<!doctype html><body>${html}</body>`).window.document;
}

const STATUS = {
  mode: "money_mode_operational_status",
  generated_at: "2026-06-24T05:00:00+00:00",
  scope: "active_upcoming",
  active_slate_count: 2,
  playable_slate_count: 0,
  blocked_slate_count: 2,
  slates: [
    {
      draw_code: "PG-2338",
      week_type: "weekend",
      match_count: 14,
      decision: "NO_JUGAR",
      reason: "Demasiados NO SIMPLE sin cobertura posible.",
      confidence: "cautious",
      recommended_ticket: null,
      prediction_status: "persisted",
      money_mode_ready: true,
      warnings: [],
      playable: false,
    },
    {
      draw_code: "PGM-801",
      week_type: "midweek",
      match_count: 9,
      decision: "NO_JUGAR",
      reason: "Demasiados NO SIMPLE sin cobertura posible.",
      confidence: "cautious",
      recommended_ticket: null,
      prediction_status: "live_available",
      money_mode_ready: true,
      warnings: ["live_predictions_only"],
      playable: false,
    },
  ],
  write_safety: { read_only: true },
};

describe("renderOperationalMoneyModeStatusPanel", () => {
  it("1 — renders the operational panel", () => {
    const doc = dom(renderOperationalMoneyModeStatusPanel(STATUS));
    expect(doc.querySelector(".ops-money-panel")).not.toBeNull();
    expect(doc.querySelector(".shadow-badge").textContent).toContain("OPERATIONAL");
  });

  it("2 — PG-2338 shows NO JUGAR", () => {
    const doc = dom(renderOperationalMoneyModeStatusPanel(STATUS));
    const row = [...doc.querySelectorAll("tbody tr")].find((r) => r.textContent.includes("PG-2338"));
    expect(row).toBeTruthy();
    expect(row.textContent).toContain("NO JUGAR");
  });

  it("3 — PGM-801 shows NO JUGAR", () => {
    const doc = dom(renderOperationalMoneyModeStatusPanel(STATUS));
    const row = [...doc.querySelectorAll("tbody tr")].find((r) => r.textContent.includes("PGM-801"));
    expect(row).toBeTruthy();
    expect(row.textContent).toContain("NO JUGAR");
  });

  it("4 — does not hide blocked_slate_count", () => {
    const doc = dom(renderOperationalMoneyModeStatusPanel(STATUS));
    const text = doc.querySelector(".ops-summary").textContent;
    expect(text).toContain("Bloqueadas");
    expect(text).toContain("2");
  });

  it("5 — does not touch a slate selector element when rendered to its own body", () => {
    const doc = new JSDOM(
      `<!doctype html><body><select id="slate-switcher"><option value="a">A</option></select><div id="operational-money-mode-status-body"></div></body>`,
    ).window.document;
    const before = doc.getElementById("slate-switcher").outerHTML;
    doc.getElementById("operational-money-mode-status-body").innerHTML =
      renderOperationalMoneyModeStatusPanel(STATUS);
    expect(doc.getElementById("slate-switcher").outerHTML).toBe(before);
  });

  it("6 — does not mutate the real ticket-grid", () => {
    const doc = new JSDOM(
      `<!doctype html><body><div id="ticket-grid" data-keep="1">REAL TICKET</div><div id="operational-money-mode-status-body"></div></body>`,
    ).window.document;
    const before = doc.getElementById("ticket-grid").outerHTML;
    doc.getElementById("operational-money-mode-status-body").innerHTML =
      renderOperationalMoneyModeStatusPanel(STATUS);
    expect(doc.getElementById("ticket-grid").outerHTML).toBe(before);
    expect(doc.getElementById("ticket-grid").textContent).toBe("REAL TICKET");
  });

  it("renders an empty state when there are no active slates", () => {
    const doc = dom(
      renderOperationalMoneyModeStatusPanel({
        mode: "money_mode_operational_status",
        active_slate_count: 0,
        playable_slate_count: 0,
        blocked_slate_count: 0,
        slates: [],
        write_safety: { read_only: true },
      }),
    );
    expect(doc.body.textContent).toContain("No hay slates activas");
  });

  it("renders an empty state when there is no status", () => {
    const doc = dom(renderOperationalMoneyModeStatusPanel(null));
    expect(doc.querySelector(".empty-state")).not.toBeNull();
  });
});
