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
      reason: "Demasiados NO SIMPLE sin cobertura posible: 6/14 posiciones siguen como fijo forzado.",
      confidence: "cautious",
      recommended_ticket: null,
      recommended_action: "No comprar boleto",
      critical_uncovered_count: 6,
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
      reason: "Demasiados NO SIMPLE sin cobertura posible: 4/9 posiciones siguen como fijo forzado.",
      confidence: "cautious",
      recommended_ticket: null,
      recommended_action: "No comprar boleto",
      critical_uncovered_count: 4,
      prediction_status: "live_available",
      money_mode_ready: true,
      warnings: ["live_predictions_only"],
      playable: false,
    },
  ],
  write_safety: { read_only: true },
};

describe("renderOperationalMoneyModeStatusPanel (executive)", () => {
  it("leads with HOY: NO JUGAR when no slate is playable", () => {
    const doc = dom(renderOperationalMoneyModeStatusPanel(STATUS));
    expect(doc.querySelector(".ops-money-panel")).not.toBeNull();
    expect(doc.querySelector(".ops-hero-headline").textContent).toContain("HOY: NO JUGAR");
  });

  it("shows the 'no comprar boleto hoy' action", () => {
    const doc = dom(renderOperationalMoneyModeStatusPanel(STATUS));
    expect(doc.querySelector(".ops-hero-action").textContent.toLowerCase()).toContain(
      "no comprar boleto hoy",
    );
  });

  it("shows 0 playable / 2 blocked without hiding the blocked count", () => {
    const doc = dom(renderOperationalMoneyModeStatusPanel(STATUS));
    const counts = doc.querySelector(".ops-hero-counts").textContent;
    expect(counts).toContain("0 de 2");
    expect(counts).toContain("2 bloqueadas");
  });

  it("PG-2338 card shows NO JUGAR + no-comprar action + short reason", () => {
    const doc = dom(renderOperationalMoneyModeStatusPanel(STATUS));
    const card = [...doc.querySelectorAll(".ops-slate-card")].find((c) => c.textContent.includes("PG-2338"));
    expect(card).toBeTruthy();
    expect(card.textContent).toContain("NO JUGAR");
    expect(card.textContent).toContain("No comprar boleto");
    expect(card.textContent).toContain("6 partidos sin cobertura suficiente");
  });

  it("PGM-801 card shows NO JUGAR + no-comprar action", () => {
    const doc = dom(renderOperationalMoneyModeStatusPanel(STATUS));
    const card = [...doc.querySelectorAll(".ops-slate-card")].find((c) => c.textContent.includes("PGM-801"));
    expect(card).toBeTruthy();
    expect(card.textContent).toContain("NO JUGAR");
    expect(card.textContent).toContain("No comprar boleto");
  });

  it("never shows the phrase 'fijo forzado'", () => {
    const doc = dom(renderOperationalMoneyModeStatusPanel(STATUS));
    expect(doc.body.textContent.toLowerCase()).not.toContain("fijo forzado");
  });

  it("shows a playable headline when at least one slate is playable", () => {
    const playable = {
      ...STATUS,
      playable_slate_count: 1,
      blocked_slate_count: 1,
      slates: [
        { ...STATUS.slates[0], decision: "JUGAR_BALANCEADO", recommended_ticket: "balanced", recommended_action: "Jugar boleto balanced", critical_uncovered_count: 0, playable: true },
        STATUS.slates[1],
      ],
    };
    const doc = dom(renderOperationalMoneyModeStatusPanel(playable));
    expect(doc.querySelector(".ops-hero-headline").textContent).toContain("JUGABLES");
    expect(doc.querySelector(".ops-hero-action").textContent.toLowerCase()).toContain("revisar el boleto");
  });

  it("does not touch a slate selector element when rendered to its own body", () => {
    const doc = new JSDOM(
      `<!doctype html><body><select id="slate-switcher"><option value="a">A</option></select><div id="operational-money-mode-status-body"></div></body>`,
    ).window.document;
    const before = doc.getElementById("slate-switcher").outerHTML;
    doc.getElementById("operational-money-mode-status-body").innerHTML =
      renderOperationalMoneyModeStatusPanel(STATUS);
    expect(doc.getElementById("slate-switcher").outerHTML).toBe(before);
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
    expect(doc.body.textContent).toContain("SIN QUINIELA ACTIVA");
  });

  it("renders an empty state when there is no status", () => {
    const doc = dom(renderOperationalMoneyModeStatusPanel(null));
    expect(doc.querySelector(".empty-state")).not.toBeNull();
  });
});
