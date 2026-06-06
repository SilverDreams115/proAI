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
