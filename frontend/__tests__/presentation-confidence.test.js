// Presentation confidence headline — never "Alta confianza" on capped/flagged picks.
import { describe, it, expect } from "vitest";
import {
  presentationConfidenceLabel,
  presentationConfidenceTone,
  headlineConfidence,
} from "../helpers.js";

describe("presentationConfidenceLabel", () => {
  it("maps bands to UI labels", () => {
    expect(presentationConfidenceLabel("high")).toBe("Alta");
    expect(presentationConfidenceLabel("medium")).toBe("Media");
    expect(presentationConfidenceLabel("review")).toContain("Revisar");
    expect(presentationConfidenceLabel("blocked")).toBe("Bloqueado");
    expect(presentationConfidenceLabel("unreliable")).toContain("No firmar");
  });

  it("review/blocked/unreliable are not 'Alta'", () => {
    for (const b of ["review", "blocked", "unreliable", "low"]) {
      expect(presentationConfidenceLabel(b)).not.toBe("Alta");
      expect(presentationConfidenceTone(b)).not.toBe("ok");
    }
  });
});

describe("headlineConfidence", () => {
  it("prefers the degraded presentation band from the guard", () => {
    const pred = {
      visible_confidence: "baja",
      presentation_guard: { presentation_confidence_band: "review" },
    };
    const h = headlineConfidence(pred);
    expect(h.kind).toBe("presentation");
    expect(h.label).toContain("Revisar");
    expect(h.tone).toBe("bad");
  });

  it("PG-2338 pos4 (Japan-Sweden, capped 0.96, REVISAR) is NOT 'Alta'", () => {
    const pos4 = {
      home_team_name: "Japan",
      away_team_name: "Sweden",
      confidence_band: "high", // model band still high (internal)
      visible_confidence: "baja",
      flags: ["EXTREME_PROBABILITY_CAPPED", "EXTREME_PROBABILITY_WITHOUT_EVIDENCE"],
      presentation_guard: {
        simple_allowed: false,
        presentation_confidence_band: "review",
      },
    };
    const h = headlineConfidence(pos4);
    expect(h.label).not.toBe("Alta");
    expect(h.label).toContain("Revisar");
  });

  it("falls back to visible_confidence when no presentation band present", () => {
    const pred = { visible_confidence: "alta" };
    const h = headlineConfidence(pred);
    expect(h.kind).toBe("visible");
    expect(h.label).toBe("Alta");
  });

  it("a clean high+simple pick still reads Alta", () => {
    const pred = {
      visible_confidence: "alta",
      presentation_guard: { simple_allowed: true, presentation_confidence_band: "high" },
    };
    expect(headlineConfidence(pred).label).toBe("Alta");
  });
});
