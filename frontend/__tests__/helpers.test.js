import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";

import {
  formatPercent,
  formatDate,
  formatRelativeAge,
  availabilityStatusLabel,
  availabilityCategoryLabel,
  confidenceLabel,
  readinessLabel,
  dataQualityLabel,
  statusTone,
  sortedOutcomes,
  linkedEvidenceCount,
  buildQualityTooltip,
  drawRiskSummary,
  flagLabel,
  basePickBadge,
  ticketStrategyFrom,
  resolveTicketStrategy,
  ticketStrategyToneFromKey,
  riskLevelLabel,
  visibleConfidenceLabel,
  confidenceTone,
  limitChips,
  isTechAccordionTarget,
  effectiveConfidenceTier,
  predictionAllowsConfidentSingle,
  probBarWidthClass,
} from "../helpers.js";

describe("formatPercent", () => {
  it("renders integer percent", () => {
    expect(formatPercent(0.4)).toBe("40%");
    expect(formatPercent(0.732)).toBe("73%");
    expect(formatPercent(1)).toBe("100%");
  });

  it("returns an em-dash on null/NaN/undefined", () => {
    expect(formatPercent(null)).toBe("—");
    expect(formatPercent(undefined)).toBe("—");
    expect(formatPercent(Number.NaN)).toBe("—");
  });
});

describe("formatDate", () => {
  it("formats ISO into es-MX style", () => {
    // Locale output is sensitive to the runtime tz; we just assert
    // that something non-empty came back and it isn't the fallback.
    const out = formatDate("2026-05-30T15:00:00Z");
    expect(out).toBeTruthy();
    expect(out).not.toBe("sin fecha");
  });

  it("falls back gracefully on garbage input", () => {
    expect(formatDate(null)).toBe("sin fecha");
    expect(formatDate("not-a-date")).toBeTruthy();
  });
});

describe("formatRelativeAge", () => {
  const FROZEN_NOW = new Date("2026-05-29T07:00:00Z").getTime();

  beforeEach(() => {
    vi.useFakeTimers();
    vi.setSystemTime(FROZEN_NOW);
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it("returns seconds inside the first minute", () => {
    expect(formatRelativeAge("2026-05-29T06:59:30Z")).toBe("hace 30s");
  });

  it("returns minutes once past 60s", () => {
    expect(formatRelativeAge("2026-05-29T06:55:00Z")).toBe("hace 5m");
  });

  it("returns hours once past 60m", () => {
    expect(formatRelativeAge("2026-05-29T03:00:00Z")).toBe("hace 4h");
  });

  it("returns days once past 24h", () => {
    expect(formatRelativeAge("2026-05-26T07:00:00Z")).toBe("hace 3d");
  });

  it("guards against missing/garbage input", () => {
    expect(formatRelativeAge(null)).toBe("sin timestamp");
    expect(formatRelativeAge("nope")).toBe("sin timestamp");
  });
});

describe("availabilityStatusLabel", () => {
  it("maps known statuses to Spanish", () => {
    expect(availabilityStatusLabel("out")).toBe("Baja");
    expect(availabilityStatusLabel("doubtful")).toBe("En duda");
  });

  it("returns the raw value for unknown statuses", () => {
    expect(availabilityStatusLabel("custom-status")).toBe("custom-status");
    expect(availabilityStatusLabel(null)).toBe("Sin estado");
  });
});

describe("availabilityCategoryLabel", () => {
  it("maps known categories", () => {
    expect(availabilityCategoryLabel("injury")).toBe("lesión");
    expect(availabilityCategoryLabel("rotation")).toBe("alineación");
  });

  it("falls back on unknown", () => {
    expect(availabilityCategoryLabel(undefined)).toBe("contexto");
  });
});

describe("confidenceLabel", () => {
  it("maps the three primary bands", () => {
    expect(confidenceLabel("high")).toBe("alta");
    expect(confidenceLabel("medium")).toBe("media");
    expect(confidenceLabel("low")).toBe("baja");
  });

  it("maps blocked to its Spanish label too", () => {
    expect(confidenceLabel("blocked")).toBe("bloqueada");
  });

  it("passes through unknown bands so backend changes don't blank the UI", () => {
    expect(confidenceLabel("future-band")).toBe("future-band");
  });
});

describe("readinessLabel", () => {
  it("maps every readiness state we surface", () => {
    expect(readinessLabel("ready")).toBe("listo");
    expect(readinessLabel("covered")).toBe("cubierto");
    expect(readinessLabel("not_ready")).toBe("sin benchmark");
    expect(readinessLabel("context_only")).toBe("solo contexto");
    expect(readinessLabel("unclassified")).toBe("sin clasificar");
  });
});

describe("dataQualityLabel", () => {
  it("maps the three quality levels", () => {
    expect(dataQualityLabel("good")).toBe("buena");
    expect(dataQualityLabel("partial")).toBe("parcial");
    expect(dataQualityLabel("thin")).toBe("delgada");
  });
});

describe("statusTone", () => {
  it("maps positive signals to ok", () => {
    expect(statusTone("ok")).toBe("ok");
    expect(statusTone("ready")).toBe("ok");
    expect(statusTone(true)).toBe("ok");
  });

  it("maps negative signals to bad", () => {
    expect(statusTone("blocked")).toBe("bad");
    expect(statusTone("not_ready")).toBe("bad");
    expect(statusTone(false)).toBe("bad");
  });

  it("treats unknown signals as warn", () => {
    expect(statusTone("degraded")).toBe("warn");
  });
});

describe("sortedOutcomes", () => {
  it("returns outcomes ranked high to low", () => {
    const prediction = {
      home_probability: 0.4,
      draw_probability: 0.35,
      away_probability: 0.25,
    };
    expect(sortedOutcomes(prediction).map((o) => o.key)).toEqual(["1", "X", "2"]);
  });

  it("coerces missing probabilities to zero rather than NaN", () => {
    const out = sortedOutcomes({});
    expect(out.map((o) => o.value)).toEqual([0, 0, 0]);
    // Order is stable when all values tie.
    expect(out.map((o) => o.key)).toEqual(["1", "X", "2"]);
  });

  it("prefers the explicit sanity-layer L/E/V vector over legacy fields", () => {
    // Raw model said V=0.79; the sanity layer capped the displayed V to
    // 0.65. The renderer must show the FINAL (capped) value.
    const prediction = {
      home_probability: 0.13,
      draw_probability: 0.08,
      away_probability: 0.79,
      probabilities: { L: 0.2, E: 0.15, V: 0.65 },
    };
    const out = sortedOutcomes(prediction);
    expect(out[0].key).toBe("2");
    expect(out[0].value).toBe(0.65);
  });

  it("falls back to legacy fields when the explicit vector is absent", () => {
    const out = sortedOutcomes({ home_probability: 0.4, draw_probability: 0.35, away_probability: 0.25 });
    expect(out.map((o) => o.key)).toEqual(["1", "X", "2"]);
  });
});

describe("flagLabel", () => {
  it("maps known sanity flags to Spanish labels", () => {
    expect(flagLabel("LOW_EVIDENCE")).toBe("evidencia baja");
    expect(flagLabel("INTERNATIONAL_FRIENDLY")).toBe("amistoso internacional");
    expect(flagLabel("EXTREME_PROBABILITY_WITHOUT_EVIDENCE")).toBe(
      "probabilidad extrema sin evidencia",
    );
  });

  it("degrades gracefully for unknown flags", () => {
    expect(flagLabel("SOME_NEW_FLAG")).toBe("some new flag");
  });
});

describe("linkedEvidenceCount", () => {
  it("returns the max across the three known sources", () => {
    const match = {
      quality: { evidence_count: 2 },
      features: { payload: { evidence_items: 4 } },
      evidence: [{ id: "a" }, { id: "b" }, { id: "c" }],
    };
    expect(linkedEvidenceCount(match)).toBe(4);
  });

  it("returns zero when nothing is linked", () => {
    expect(linkedEvidenceCount({})).toBe(0);
  });
});

describe("buildQualityTooltip", () => {
  it("returns the empty marker when there are no matches", () => {
    expect(buildQualityTooltip([])).toBe("Sin partidos.");
    expect(buildQualityTooltip(null)).toBe("Sin partidos.");
  });

  it("renders one row per position sorted by position", () => {
    const matches = [
      {
        position: 2,
        prediction: { home_team_name: "PSG", away_team_name: "Arsenal" },
        quality: { quality_score: 90, quality_level: "good", missing: [] },
      },
      {
        position: 1,
        prediction: { home_team_name: "México", away_team_name: "Australia" },
        quality: {
          quality_score: 70,
          quality_level: "good",
          missing: ["historial directo"],
        },
      },
    ];
    const tooltip = buildQualityTooltip(matches);
    const lines = tooltip.split("\n");
    expect(lines).toHaveLength(2);
    expect(lines[0]).toMatch(/^\s*1\s+70\/100/);
    expect(lines[0]).toContain("México vs Australia");
    expect(lines[0]).toContain("falta historial directo");
    expect(lines[1]).toMatch(/^\s*2\s+90\/100/);
    expect(lines[1]).toContain("datos completos");
  });

  it("derives the level from the score when quality_level is absent", () => {
    const matches = [
      {
        position: 1,
        prediction: { home_team_name: "A", away_team_name: "B" },
        quality: { quality_score: 30 },
      },
    ];
    expect(buildQualityTooltip(matches)).toContain("delgada");
  });
});

describe("drawRiskSummary", () => {
  const pred = (home, draw, away) => ({
    home_probability: home,
    draw_probability: draw,
    away_probability: away,
  });

  it("flags empate vivo at p_draw >= 0.25 (not fuerte)", () => {
    const risk = drawRiskSummary(pred(0.5, 0.25, 0.25), {});
    expect(risk.isLive).toBe(true);
    expect(risk.isStrong).toBe(false);
  });

  it("flags empate fuerte at p_draw >= 0.30", () => {
    const risk = drawRiskSummary(pred(0.03, 0.33, 0.64), {});
    expect(risk.isLive).toBe(true);
    expect(risk.isStrong).toBe(true);
    expect(risk.drawRank).toBe(2); // behind away (0.64)
  });

  it("does not flag below the live threshold", () => {
    const risk = drawRiskSummary(pred(0.52, 0.24, 0.24), {});
    expect(risk.isLive).toBe(false);
    expect(risk.isStrong).toBe(false);
  });

  it("ranks the draw third when it is least likely", () => {
    const risk = drawRiskSummary(pred(0.52, 0.12, 0.37), {});
    expect(risk.drawRank).toBe(3);
  });

  it("reads X coverage from the computed coverage map", () => {
    const risk = drawRiskSummary(pred(0.59, 0.25, 0.16), {
      simple: false,
      doubles: true,
      full: true,
    });
    expect(risk.coveredSimple).toBe(false);
    expect(risk.coveredDoubles).toBe(true);
    expect(risk.coveredFull).toBe(true);
  });

  it("prefers the backend-provided draw_risk block when present", () => {
    const provided = {
      p_draw: 0.27,
      draw_rank: 2,
      is_live_draw: true,
      is_strong_draw: false,
      covered_simple: false,
      covered_doubles: true,
      covered_full: true,
    };
    const risk = drawRiskSummary(pred(0.41, 0.27, 0.32), { full: false }, provided);
    expect(risk.pDraw).toBe(0.27);
    expect(risk.coveredFull).toBe(true); // backend wins over fallback
  });
});

describe("semantic separation (Fase 3 UI/UX)", () => {
  it("base pick badge renders 'Señal X', never 'Fijo'", () => {
    expect(basePickBadge("1")).toEqual({ letter: "L", label: "Señal L" });
    expect(basePickBadge("X")).toEqual({ letter: "E", label: "Señal E" });
    expect(basePickBadge("2")).toEqual({ letter: "V", label: "Señal V" });
    expect(basePickBadge("1").label).not.toContain("Fijo");
  });

  it("ticket strategy never returns 'Fijo' and maps coverage correctly", () => {
    // Test 1: strategy != SIMPLE must not be a plain single labelled "Fijo".
    const blocked = ticketStrategyFrom({ finalStatus: "BLOQUEADO" });
    expect(blocked.key).toBe("EVITAR");
    const revisar = ticketStrategyFrom({ finalStatus: "REVISAR", validationLevel: "low", decisionType: "fixed" });
    expect(revisar.key).toBe("NO_SIMPLE");
    expect(revisar.label).toBe("No dejar simple");
    const high = ticketStrategyFrom({ finalStatus: "LISTO", validationLevel: "high", decisionType: "fixed" });
    expect(high.key).toBe("NO_SIMPLE");
    expect(ticketStrategyFrom({ validationLevel: "low", decisionType: "double" }).key).toBe("DOBLE");
    expect(ticketStrategyFrom({ validationLevel: "low", decisionType: "triple" }).key).toBe("TRIPLE");
    // Only a clean simple becomes SIMPLE.
    const simple = ticketStrategyFrom({ finalStatus: "FIJO", validationLevel: "low", decisionType: "fixed" });
    expect(simple.key).toBe("SIMPLE");
    expect(simple.label).toBe("Simple");
    // None of the labels is ever the ambiguous word "Fijo".
    for (const status of ["FIJO", "LISTO", "REVISAR", "BLOQUEADO"]) {
      for (const level of ["low", "medium", "high"]) {
        for (const type of ["fixed", "double", "triple"]) {
          const s = ticketStrategyFrom({ finalStatus: status, validationLevel: level, decisionType: type });
          expect(s.label).not.toContain("Fijo");
        }
      }
    }
  });

  it("visible confidence is rendered from backend, capped tones", () => {
    expect(visibleConfidenceLabel("alta")).toBe("Alta");
    expect(visibleConfidenceLabel("media-baja")).toBe("Media-baja");
    expect(visibleConfidenceLabel("baja")).toBe("Baja");
    expect(confidenceTone("alta")).toBe("ok");
    expect(confidenceTone("media")).toBe("warn");
    expect(confidenceTone("media-baja")).toBe("bad");
    expect(confidenceTone("baja")).toBe("bad");
  });

  it("risk label maps low/medium/high to Spanish", () => {
    expect(riskLevelLabel("low")).toBe("Bajo");
    expect(riskLevelLabel("medium")).toBe("Medio");
    expect(riskLevelLabel("high")).toBe("Alto");
  });

  it("limitChips caps at 3 and reports hidden count (Test 3)", () => {
    const eight = ["a", "b", "c", "d", "e", "f", "g", "h"];
    const { visible, hiddenCount } = limitChips(eight, 3);
    expect(visible).toHaveLength(3);
    expect(hiddenCount).toBe(5);
    // Fewer than max -> nothing hidden.
    expect(limitChips(["x"], 3)).toEqual({ visible: ["x"], hiddenCount: 0 });
    // Non-array safe.
    expect(limitChips(null, 3)).toEqual({ visible: [], hiddenCount: 0 });
  });
});

describe("resolveTicketStrategy (backend-authoritative, Fase 3.1)", () => {
  it("renders strategy from the backend field, not the client derivation", () => {
    const s = resolveTicketStrategy({
      prediction: { ticket_strategy: "NO_DEJAR_SIMPLE", ticket_strategy_label: "No dejar simple", final_status: "REVISAR" },
      validationLevel: "low",
      decisionType: "fixed",
    });
    expect(s.key).toBe("NO_DEJAR_SIMPLE");
    expect(s.label).toBe("No dejar simple");
    expect(s.tone).toBe("bad");
  });

  it("upgrades to TRIPLE only when the optimizer allocates a triple (coverage refinement)", () => {
    const s = resolveTicketStrategy({
      prediction: { ticket_strategy: "DOBLE_RECOMENDADO" },
      decisionType: "triple",
    });
    expect(s.key).toBe("TRIPLE_RECOMENDADO");
    expect(s.label).toBe("Triple recomendado");
  });

  it("never downgrades EVITAR even if optimizer says triple", () => {
    const s = resolveTicketStrategy({ prediction: { ticket_strategy: "EVITAR" }, decisionType: "triple" });
    expect(s.key).toBe("EVITAR");
  });

  it("falls back to client derivation for old responses without the field", () => {
    const s = resolveTicketStrategy({
      prediction: { final_status: "REVISAR" },
      validationLevel: "low",
      decisionType: "fixed",
    });
    // Legacy ticketStrategyFrom maps REVISAR -> NO_SIMPLE label "No dejar simple".
    expect(s.label).toBe("No dejar simple");
  });

  it("never produces the word 'Fijo' in any label", () => {
    for (const key of ["SIMPLE", "DOBLE_RECOMENDADO", "TRIPLE_RECOMENDADO", "NO_DEJAR_SIMPLE", "EVITAR"]) {
      const s = resolveTicketStrategy({ prediction: { ticket_strategy: key } });
      expect(s.label).not.toContain("Fijo");
    }
  });

  it("ticketStrategyToneFromKey: SIMPLE ok, doble/triple warn, no-simple/evitar bad", () => {
    expect(ticketStrategyToneFromKey("SIMPLE")).toBe("ok");
    expect(ticketStrategyToneFromKey("DOBLE_RECOMENDADO")).toBe("warn");
    expect(ticketStrategyToneFromKey("TRIPLE_RECOMENDADO")).toBe("warn");
    expect(ticketStrategyToneFromKey("NO_DEJAR_SIMPLE")).toBe("bad");
    expect(ticketStrategyToneFromKey("EVITAR")).toBe("bad");
  });
});

describe("isTechAccordionTarget (accordion must not select card)", () => {
  it("returns true when the click is inside a .card-tech accordion", () => {
    document.body.innerHTML = `
      <article data-match-card="m1">
        <details class="card-tech"><summary id="sum">+3 detalles</summary><ul><li id="li">x</li></ul></details>
      </article>`;
    expect(isTechAccordionTarget(document.getElementById("sum"))).toBe(true);
    expect(isTechAccordionTarget(document.getElementById("li"))).toBe(true);
  });

  it("returns false for a normal card click outside the accordion", () => {
    document.body.innerHTML = `<article data-match-card="m1"><h3 id="title">A vs B</h3></article>`;
    expect(isTechAccordionTarget(document.getElementById("title"))).toBe(false);
  });

  it("is null-safe", () => {
    expect(isTechAccordionTarget(null)).toBe(false);
    expect(isTechAccordionTarget({})).toBe(false);
  });
});

describe("product-first signals (Fase 3.2 — no raw confidence_band in decisions)", () => {
  it("predictionAllowsConfidentSingle: ticket_strategy beats confidence_band=high", () => {
    // confidence_band high but strategy NO_DEJAR_SIMPLE -> NOT a confident single.
    expect(
      predictionAllowsConfidentSingle({ confidence_band: "high", ticket_strategy: "NO_DEJAR_SIMPLE" }),
    ).toBe(false);
    // SIMPLE -> allowed.
    expect(
      predictionAllowsConfidentSingle({ confidence_band: "low", ticket_strategy: "SIMPLE" }),
    ).toBe(true);
  });

  it("predictionAllowsConfidentSingle: LOW_EVIDENCE+FALLBACK (via DOBLE strategy) beats band high", () => {
    // The sanity layer turns flags into a non-SIMPLE strategy; a band-high
    // friendly with those flags must not be a confident single.
    expect(
      predictionAllowsConfidentSingle({ confidence_band: "high", ticket_strategy: "DOBLE_RECOMENDADO" }),
    ).toBe(false);
  });

  it("predictionAllowsConfidentSingle: legacy fallback when no ticket_strategy", () => {
    expect(predictionAllowsConfidentSingle({ confidence_band: "high" })).toBe(true);
    expect(predictionAllowsConfidentSingle({ confidence_band: "low" })).toBe(false);
    expect(predictionAllowsConfidentSingle({ confidence_band: "blocked" })).toBe(false);
    expect(predictionAllowsConfidentSingle({})).toBe(false); // safest default
  });

  it("effectiveConfidenceTier: final_status overrides confidence_band", () => {
    // Band high but REVISAR -> low tier (product wins).
    expect(effectiveConfidenceTier({ confidence_band: "high", final_status: "REVISAR" })).toBe("low");
    expect(effectiveConfidenceTier({ confidence_band: "high", final_status: "BLOQUEADO" })).toBe("blocked");
    expect(effectiveConfidenceTier({ confidence_band: "low", final_status: "FIJO" })).toBe("high");
    expect(effectiveConfidenceTier({ confidence_band: "low", final_status: "LISTO" })).toBe("medium");
  });

  it("effectiveConfidenceTier: legacy fallback to confidence_band when no final_status", () => {
    expect(effectiveConfidenceTier({ confidence_band: "high" })).toBe("high");
    expect(effectiveConfidenceTier({ confidence_band: "blocked" })).toBe("blocked");
    expect(effectiveConfidenceTier({})).toBe("low");
  });
});

describe("probBarWidthClass (CSP-safe prob bars — no inline style)", () => {
  it("maps a percent to a discrete w-N class rounded to nearest 5", () => {
    expect(probBarWidthClass(15)).toBe("w-15");
    expect(probBarWidthClass(60)).toBe("w-60");
    expect(probBarWidthClass(62)).toBe("w-60");
    expect(probBarWidthClass(63)).toBe("w-65");
    expect(probBarWidthClass(0)).toBe("w-0");
    expect(probBarWidthClass(100)).toBe("w-100");
  });

  it("clamps out-of-range and coerces garbage", () => {
    expect(probBarWidthClass(-10)).toBe("w-0");
    expect(probBarWidthClass(150)).toBe("w-100");
    expect(probBarWidthClass(null)).toBe("w-0");
    expect(probBarWidthClass(undefined)).toBe("w-0");
    expect(probBarWidthClass(NaN)).toBe("w-0");
  });
});
