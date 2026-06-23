import { describe, it, expect } from "vitest";
import { presentationGuardOf, signalLabel } from "../presentation-guard.js";

describe("presentationGuardOf", () => {
  it("uses the backend presentation_guard when present", () => {
    const pred = {
      presentation_guard: {
        simple_allowed: false,
        primary_signal: "V",
        recommendation_label: "NO SIMPLE",
        risk_level: "high",
        confidence: "media-baja",
        reason: ["risk_high", "no_dejar_simple"],
      },
    };
    const g = presentationGuardOf(pred);
    expect(g.simple_allowed).toBe(false);
    expect(g.primary_signal).toBe("V");
    expect(g.recommendation_label).toBe("NO SIMPLE");
  });

  it("Norway vs France: high risk / no_dejar_simple is NOT simple even though signal is V", () => {
    // Older payload without presentation_guard -> derived fallback.
    const pred = {
      recommended_outcome: "2",
      final_status: "REVISAR",
      risk_level: "high",
      ticket_strategy: "NO_DEJAR_SIMPLE",
      flags: ["SUSPICIOUS_CLASS_PROBABILITY", "FALLBACK_USED"],
      visible_confidence: "media-baja",
    };
    const g = presentationGuardOf(pred);
    expect(g.simple_allowed).toBe(false);
    expect(g.primary_signal).toBe("V");
    expect(g.recommendation_label).toBe("NO SIMPLE");
  });

  it("a clean FIJO/SIMPLE/low-risk pick is simple-playable", () => {
    const pred = {
      recommended_outcome: "1",
      final_status: "FIJO",
      risk_level: "low",
      ticket_strategy: "SIMPLE",
      flags: [],
      visible_confidence: "alta",
    };
    const g = presentationGuardOf(pred);
    expect(g.simple_allowed).toBe(true);
    expect(g.recommendation_label).toBe("SIMPLE");
    expect(g.primary_signal).toBe("L");
  });

  it("blocked status reads BLOQUEADO", () => {
    const g = presentationGuardOf({
      recommended_outcome: "1",
      final_status: "BLOQUEADO",
      risk_level: "high",
      ticket_strategy: "EVITAR",
      flags: [],
    });
    expect(g.simple_allowed).toBe(false);
    expect(g.recommendation_label).toBe("BLOQUEADO");
  });

  it("signalLabel maps letters to readable labels", () => {
    expect(signalLabel("V")).toContain("Visitante");
    expect(signalLabel("L")).toContain("Local");
    expect(signalLabel("E")).toContain("Empate");
  });
});
