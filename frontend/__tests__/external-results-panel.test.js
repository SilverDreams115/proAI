import { describe, it, expect } from "vitest";
import { JSDOM } from "jsdom";
import { renderExternalResultsPanel } from "../external-results.js";

function dom(html) {
  return new JSDOM(`<!doctype html><body>${html}</body>`).window.document;
}

describe("renderExternalResultsPanel", () => {
  it("shows a clear missing-key state with the env var to configure", () => {
    const doc = dom(
      renderExternalResultsPanel({
        provider: "football_data_org",
        enabled: true,
        status: "unavailable_missing_key",
        coverage: { matched: 0, total: 14, rate: 0 },
        matches: [],
        write_safety: { writes_performed: false },
      }),
    );
    expect(doc.body.textContent).toContain("Fuente gratuita no configurada");
    expect(doc.body.textContent).toContain("PROAI_FOOTBALL_DATA_API_KEY");
  });

  it("shows a disabled state (dry-run informativo)", () => {
    const doc = dom(
      renderExternalResultsPanel({
        provider: "football_data_org",
        enabled: false,
        status: "disabled",
        coverage: { matched: 0, total: 14, rate: 0 },
        matches: [],
        write_safety: { writes_performed: false },
      }),
    );
    expect(doc.body.textContent.toLowerCase()).toContain("deshabilitado");
    expect(doc.querySelector(".shadow-badge").textContent).toContain("SOLO LECTURA");
  });

  it("renders coverage and per-match rows when provider data is available", () => {
    const doc = dom(
      renderExternalResultsPanel({
        provider: "football_data_org",
        enabled: true,
        status: "ok",
        coverage: { matched: 1, total: 2, rate: 0.5 },
        matches: [
          { position: 1, local_match: "Czech Republic vs México", provider_match: "Czech Republic vs Mexico", status: "finished", score: "2-1", confidence: "high" },
          { position: 2, local_match: "Norway vs France", provider_match: null, status: "unmatched", score: null, confidence: "none" },
        ],
        write_safety: { writes_performed: false },
      }),
    );
    expect(doc.body.textContent).toContain("1 / 2 (50%)");
    const rows = [...doc.querySelectorAll("tbody tr")];
    expect(rows.length).toBe(2);
    expect(rows[0].textContent).toContain("2-1");
    expect(rows[0].textContent).toContain("Finalizado");
    expect(rows[1].textContent).toContain("Sin emparejar");
  });

  it("always states it is read-only / no writes", () => {
    const doc = dom(
      renderExternalResultsPanel({
        provider: "football_data_org", enabled: true, status: "ok",
        coverage: { matched: 0, total: 1, rate: 0 }, matches: [],
        write_safety: { writes_performed: false },
      }),
    );
    expect(doc.body.textContent.toLowerCase()).toContain("solo lectura");
    expect(doc.body.textContent).toContain("--apply --confirm");
  });

  it("renders an empty state when there is no report", () => {
    const doc = dom(renderExternalResultsPanel(null));
    expect(doc.querySelector(".empty-state")).not.toBeNull();
  });
});
