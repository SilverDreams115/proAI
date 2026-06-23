import { describe, it, expect } from "vitest";
import {
  resolveActiveSelection,
  isTrulyMissingPrediction,
  selectedSlateCountdownMs,
} from "../slate-selection.js";

const PG = { id: "pg", draw_code: "PG-2338", registration_closes_at: "2026-06-24T19:00:00Z" };
const MS = { id: "ms", draw_code: "PGM-801", registration_closes_at: "2026-06-24T18:55:00Z" };

describe("resolveActiveSelection — manual selection is sticky", () => {
  it("keeps PG-2338 even when /slates/active reports the more-urgent PGM-801", () => {
    // The exact reported bug: heart-beat must NOT pull the user to PGM-801.
    const r = resolveActiveSelection({
      selectedId: "pg",
      slates: [MS, PG],
      activeMeta: { slate: { id: "ms" } },
    });
    expect(r.switched).toBe(false);
    expect(r.selectedId).toBe("pg");
  });

  it("keeps PGM-801 selected across a refresh", () => {
    const r = resolveActiveSelection({
      selectedId: "ms",
      slates: [MS, PG],
      activeMeta: { slate: { id: "ms" } },
    });
    expect(r.switched).toBe(false);
    expect(r.selectedId).toBe("ms");
  });

  it("switches only when the selected slate disappeared (closed/archived)", () => {
    const r = resolveActiveSelection({
      selectedId: "gone",
      slates: [MS, PG],
      activeMeta: { slate: { id: "ms" } },
    });
    expect(r.switched).toBe(true);
    expect(r.selectedId).toBe("ms"); // prefers /slates/active
  });

  it("falls back to the first active slate when meta is missing", () => {
    const r = resolveActiveSelection({ selectedId: "gone", slates: [MS, PG], activeMeta: null });
    expect(r.switched).toBe(true);
    expect(r.selectedId).toBe("ms");
  });

  it("auto-selects on first load when nothing is selected", () => {
    const r = resolveActiveSelection({ selectedId: null, slates: [PG, MS], activeMeta: null });
    expect(r.switched).toBe(true);
    expect(r.selectedId).toBe("pg");
  });

  it("selects null when there are no active slates", () => {
    const r = resolveActiveSelection({ selectedId: "pg", slates: [], activeMeta: null });
    expect(r.switched).toBe(true);
    expect(r.selectedId).toBe(null);
  });
});

describe("isTrulyMissingPrediction", () => {
  it("active MS with live predictions is NOT a false 'sin predicción'", () => {
    expect(
      isTrulyMissingPrediction({ has_predictions: false, live_prediction_available: true, match_count: 9 }),
    ).toBe(false);
  });
  it("persisted predictions are not missing", () => {
    expect(isTrulyMissingPrediction({ has_predictions: true, match_count: 14 })).toBe(false);
  });
  it("a slate with no matches at all is truly missing", () => {
    expect(
      isTrulyMissingPrediction({ has_predictions: false, live_prediction_available: false, match_count: 0 }),
    ).toBe(true);
  });
});

describe("selectedSlateCountdownMs", () => {
  it("uses the SELECTED slate's cierre, not the most-urgent one", () => {
    const ms = selectedSlateCountdownMs([MS, PG], "pg");
    expect(ms).toBe(new Date("2026-06-24T19:00:00Z").getTime());
  });
});
