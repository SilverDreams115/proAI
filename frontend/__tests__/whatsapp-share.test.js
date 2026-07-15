import { describe, expect, it } from "vitest";
import { buildWhatsAppShareUrl, buildWhatsAppTicketText } from "../whatsapp-share.js";

describe("buildWhatsAppShareUrl", () => {
  it("builds a wa.me URL with the ticket text encoded", () => {
    const text = "PGM-804 · Progol\n1. México vs Francia: L/X\nRevisar: señal baja";
    const url = buildWhatsAppShareUrl(text);
    const parsed = new URL(url);

    expect(parsed.origin).toBe("https://wa.me");
    expect(parsed.searchParams.get("text")).toBe(text);
  });

  it("trims empty padding before sharing", () => {
    const url = buildWhatsAppShareUrl("  PG-2342  ");
    expect(new URL(url).searchParams.get("text")).toBe("PG-2342");
  });

  it("formats the WhatsApp message as quiniela, partidos and selected picks only", () => {
    const text = buildWhatsAppTicketText({
      slate: { draw_code: "PGM-804", label: "Progol 804" },
      modeLabel: "Balanceado",
      rows: [
        { position: 1, home_team_name: "México", away_team_name: "Francia", pick: "L/X" },
        { position: 2, home_team_name: "Brasil", away_team_name: "Italia", pick: "V" },
      ],
    });

    expect(text).toBe(
      [
        "Quiniela: PGM-804 · Progol 804",
        "Modo: Balanceado",
        "",
        "Partidos y jugada:",
        "1. México vs Francia: L/X",
        "2. Brasil vs Italia: V",
      ].join("\n"),
    );
    expect(text).not.toContain("calidad");
    expect(text).not.toContain("Revisar");
  });

  it("adds name warnings when a ticket row needs manual review", () => {
    const text = buildWhatsAppTicketText({
      slate: { draw_code: "PGM-804" },
      modeLabel: "Simple",
      rows: [
        { position: 1, home_team_name: "TBD", away_team_name: "Francia", pick: "L" },
      ],
      warnings: [
        { position: 1, message: "nombre de equipo incompleto o provisional" },
      ],
    });

    expect(text).toContain("Revisar nombres:");
    expect(text).toContain("- 1. nombre de equipo incompleto o provisional");
  });
});
