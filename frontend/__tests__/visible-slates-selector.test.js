// Selector visibility contract (never-empty UI) — pure, testable.
//
// Pins the "UI sin boletas" fix:
//   * open official slates drive the selector (reason=open_slate);
//   * when none are open, fall back to recent read-only slates
//     (reason=fallback_recent) with the "solo lectura" message;
//   * a saved manual selection wins only if still visible;
//   * empty input yields no_official_slates (the app then shows the useful
//     discovery empty state, not a blank screen);
//   * badges map flags to Abierta/Cerrada/Completa/Sin resultados/Solo lectura.
import { describe, it, expect } from "vitest";
import {
  resolveVisibleSelection,
  visibleSelectionMessage,
  slateBadges,
  suspectSlateDiagnostics,
  pdfSourceDiagnosticLines,
} from "../slate-selection.js";

const openSlate = {
  id: "open-1",
  draw_code: "PGM-803",
  week_type: "midweek",
  is_closed: false,
  is_archived: false,
  read_only: false,
  classification: "official_but_no_results_yet",
  has_results: false,
};
const recentReal = {
  id: "recent-1",
  draw_code: "PG-2338",
  week_type: "weekend",
  is_closed: true,
  is_archived: true,
  read_only: true,
  classification: "official_real",
  has_results: true,
};
const recentNoResults = {
  id: "recent-2",
  draw_code: "PGM-802",
  week_type: "midweek",
  is_closed: true,
  is_archived: true,
  read_only: true,
  classification: "official_but_no_results_yet",
  has_results: false,
};

describe("resolveVisibleSelection", () => {
  it("selects an open slate first", () => {
    const r = resolveVisibleSelection({
      visible: { open_slates: [openSlate], recent_slates: [recentReal], reason: "open_slate", selected_default_slate_id: "open-1" },
    });
    expect(r.reason).toBe("open_slate");
    expect(r.selectedId).toBe("open-1");
    expect(r.readOnly).toBe(false);
    expect(r.slates.map((s) => s.id)).toEqual(["open-1", "recent-1"]);
    expect(r.isEmpty).toBe(false);
  });

  it("falls back to recent read-only when no open slate", () => {
    const r = resolveVisibleSelection({
      visible: { open_slates: [], recent_slates: [recentReal, recentNoResults], reason: "fallback_recent", selected_default_slate_id: "recent-1" },
    });
    expect(r.reason).toBe("fallback_recent");
    expect(r.selectedId).toBe("recent-1");
    expect(r.readOnly).toBe(true);
    expect(r.message).toContain("solo lectura");
  });

  it("honors a saved selection when still visible", () => {
    const r = resolveVisibleSelection({
      visible: { open_slates: [openSlate], recent_slates: [recentReal], selected_default_slate_id: "open-1" },
      savedId: "recent-1",
    });
    expect(r.selectedId).toBe("recent-1");
  });

  it("ignores a saved selection that disappeared", () => {
    const r = resolveVisibleSelection({
      visible: { open_slates: [openSlate], recent_slates: [], selected_default_slate_id: "open-1" },
      savedId: "ghost",
    });
    expect(r.selectedId).toBe("open-1");
  });

  it("reports no_official_slates on empty input (never blank, app shows empty state)", () => {
    const r = resolveVisibleSelection({ visible: { open_slates: [], recent_slates: [], reason: "no_official_slates" } });
    expect(r.reason).toBe("no_official_slates");
    expect(r.selectedId).toBe(null);
    expect(r.isEmpty).toBe(true);
  });

  it("only ever surfaces the slates the backend returned (no demos injected)", () => {
    const r = resolveVisibleSelection({ visible: { open_slates: [openSlate], recent_slates: [recentReal] } });
    // Demos are filtered server-side; the selector never adds anything.
    expect(r.slates).toHaveLength(2);
  });
});

describe("visibleSelectionMessage", () => {
  it("messages the read-only fallback", () => {
    expect(visibleSelectionMessage("fallback_recent")).toContain("solo lectura");
  });
  it("messages no official slates", () => {
    expect(visibleSelectionMessage("no_official_slates")).toContain("No hay boletas oficiales");
  });
  it("is empty when a slate is open", () => {
    expect(visibleSelectionMessage("open_slate")).toBe("");
  });
});

describe("slateBadges", () => {
  it("open slate => Abierta", () => {
    expect(slateBadges(openSlate)).toEqual(["Abierta"]);
  });
  it("closed official_real => Completa + Solo lectura", () => {
    expect(slateBadges(recentReal)).toEqual(["Completa", "Solo lectura"]);
  });
  it("closed without results => Cerrada + Sin resultados + Solo lectura", () => {
    expect(slateBadges(recentNoResults)).toEqual(["Cerrada", "Sin resultados", "Solo lectura"]);
  });

  it("flags a date-suspect slate with 'Fecha sospechosa'", () => {
    const suspect = { ...openSlate, date_status: "stale_source", date_suspect: true };
    expect(slateBadges(suspect)).toContain("Fecha sospechosa");
  });
});

describe("suspectSlateDiagnostics", () => {
  it("lists date-held-back slates with reason + action", () => {
    const visible = {
      open_slates: [],
      recent_slates: [recentReal],
      reason: "fallback_recent",
      discovery: {
        suspect_slates: [
          {
            draw_code: "PGM-802",
            week_type: "midweek",
            date_status: "stale_source",
            registration_closes_at: "2026-06-16T19:00:00Z",
            reasons: ["cierre 2026-06-16 es anterior a la creación de la slate 2026-06-27"],
          },
        ],
      },
    };
    const diag = suspectSlateDiagnostics(visible);
    expect(diag).toHaveLength(1);
    expect(diag[0].draw_code).toBe("PGM-802");
    expect(diag[0].date_status).toBe("stale_source");
    expect(diag[0].reason).toContain("anterior a la creación");
    // Recommended action favours waiting for the corrected LN PDF, never
    // inventing a date / forcing a manual override as the normal path.
    expect(diag[0].action).toContain("PDF");
    expect(diag[0].playable).toBe(false);
  });

  it("returns [] when there are no suspect slates", () => {
    expect(suspectSlateDiagnostics({ discovery: {} })).toEqual([]);
    expect(suspectSlateDiagnostics({})).toEqual([]);
  });

  it("surfaces PDF provenance + rejected cierre block for PGM-802 (source_invalid)", () => {
    const visible = {
      discovery: {
        suspect_slates: [
          {
            draw_code: "PGM-802",
            week_type: "midweek",
            date_status: "source_invalid",
            activation_status: "blocked",
            visible_as_open: false,
            registration_closes_at: null,
            reasons: ["el PDF oficial trae el bloque de cierre de OTRO concurso; no se aplica"],
            recommended_action: "Esperar PDF corregido de LN o confirmar fecha oficial con evidencia.",
            source_url: "https://www.loterianacional.gob.mx/.../guiamedia.pdf?v=29062026115429",
            pdf_sha256: "fc934103",
            extracted_fixture_draw_code: "802",
            match_count: 9,
            rejected_close_block_draw_code: "800",
            rejected_close_year: "2025",
          },
        ],
      },
    };
    const diag = suspectSlateDiagnostics(visible);
    const e = diag[0];
    expect(e.playable).toBe(false);
    expect(e.fixture_draw_code).toBe("802");
    expect(e.match_count).toBe(9);
    expect(e.rejected_close_block_draw_code).toBe("800");
    expect(e.action).toContain("PDF corregido");

    const lines = pdfSourceDiagnosticLines(e);
    expect(lines.join(" | ")).toContain("Detectada desde PDF oficial");
    expect(lines.join(" | ")).toContain("Fixtures válidos (9 partidos)");
    expect(lines.join(" | ")).toContain("pertenece al Concurso 800");
    expect(lines.join(" | ")).toContain("No jugable");
  });

  it("needs_official_pdf_date case shows missing-block line, not a wrong concurso", () => {
    const lines = pdfSourceDiagnosticLines({
      draw_code: "PGM-803",
      date_status: "needs_official_pdf_date",
      fixture_draw_code: "803",
      match_count: 9,
    });
    expect(lines.join(" | ")).toContain("sin bloque de cierre");
    expect(lines.join(" | ")).not.toContain("Concurso 800");
    expect(lines.join(" | ")).toContain("No jugable");
  });

  it("a date-suspect slate is not in open_slates (gate held it back server-side)", () => {
    // The backend never puts a suspect slate in open_slates; the selector
    // simply reflects that — open stays empty, fallback recent is selected.
    const r = resolveVisibleSelection({
      visible: { open_slates: [], recent_slates: [recentReal], reason: "fallback_recent", selected_default_slate_id: "recent-1" },
    });
    expect(r.open_slates ?? r.slates.filter((s) => !s.read_only)).toEqual([]);
    expect(r.reason).toBe("fallback_recent");
  });
});
