import { describe, expect, it } from "vitest";
import { JSDOM } from "jsdom";
import { buildPrintableTicketHtml } from "../printable-ticket.js";

describe("buildPrintableTicketHtml", () => {
  it("renders a compact printable ticket with L/E/V marked picks", () => {
    const html = buildPrintableTicketHtml({
      slate: { draw_code: "PG-2342", label: "Progol 2342" },
      modeLabel: "Balanceado",
      generatedAt: "2026-07-14T02:00:00Z",
      rows: [
        { position: 1, home_team_name: "México", away_team_name: "Francia", picks: ["1", "X"], pick: "L / E" },
        { position: 2, home_team_name: "Brasil", away_team_name: "Italia", picks: ["2"], pick: "V" },
      ],
    });
    const doc = new JSDOM(html).window.document;

    expect(doc.querySelector("h1").textContent).toContain("PG-2342");
    expect(doc.body.textContent).toContain("Modo: Balanceado");
    expect(doc.querySelectorAll("tbody tr")).toHaveLength(2);
    expect(doc.querySelectorAll("tbody tr")[0].querySelectorAll("td.selected")).toHaveLength(2);
    expect(doc.querySelectorAll("tbody tr")[1].querySelectorAll("td.selected")).toHaveLength(1);
    expect(doc.body.textContent).toContain("Imprimir / Guardar PDF");
  });

  it("escapes team names inside the printable html", () => {
    const html = buildPrintableTicketHtml({
      slate: { draw_code: "PG-X" },
      modeLabel: "Simple",
      rows: [
        { position: 1, home_team_name: "<script>", away_team_name: "A&B", picks: ["1"], pick: "L" },
      ],
    });

    expect(html).not.toContain("<script>");
    expect(html).toContain("&lt;script&gt;");
    expect(html).toContain("A&amp;B");
  });

  it("includes money mode, slate option and name warning context when available", () => {
    const html = buildPrintableTicketHtml({
      slate: { draw_code: "PG-X" },
      modeLabel: "Simple",
      rows: [
        { position: 1, home_team_name: "TBD", away_team_name: "A&B", picks: ["1"], pick: "L" },
      ],
      moneyMode: {
        decision: {
          status: "NO_JUGAR",
          recommended_ticket: null,
          reason: "riesgo no cubrible",
        },
      },
      slateOptions: {
        recommended_action: "NO_COMPRAR",
        pricing_verified: false,
        options: [
          { name: "Balanceada", recommended: true, combinations: 24, price_status: "unverified" },
        ],
      },
      nameWarnings: [
        { position: 1, message: "nombre de equipo incompleto o provisional" },
      ],
    });
    const doc = new JSDOM(html).window.document;

    expect(doc.body.textContent).toContain("Money Mode: NO JUGAR");
    expect(doc.body.textContent).toContain("NO COMPRAR BOLETO");
    expect(doc.body.textContent).toContain("Opciones de boleto");
    expect(doc.body.textContent).toContain("precio no verificado");
    expect(doc.body.textContent).toContain("Revisar nombres antes de comprar");
  });
});
