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
});
