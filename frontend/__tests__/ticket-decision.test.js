// The boleta decision rules — which signs each position plays, and how
// many multiples the slate allows. This was the largest untested block
// in app.js, and it is the one that decides what money goes on.

import { describe, expect, it } from "vitest";

import {
  decisionFromTicket,
  doubleLimitForSlate,
  doublesModelDecision,
  fixedModelDecision,
  fullCoverageDecision,
  modelDecision,
  multipleRuleForSlate,
  ticketRecommendationFor,
} from "../ticket-decision.js";

const MULTIPLE_RULES = {
  weekend: { doublesOnlyMax: 4 },
  midweek: { doublesOnlyMax: 3 },
  revancha: { doublesOnlyMax: 2 },
  fallback: { doublesOnlyMax: 1 },
};

const OUTCOME_ORDER = ["1", "X", "2"];

/**
 * A prediction shaped the way sortedOutcomes wants it: the explicit
 * L/E/V vector from the backend sanity layer, which is what production
 * payloads carry and what the helper prefers over the legacy fields.
 */
function prediction({ home = 0.4, draw = 0.3, away = 0.3, ...rest } = {}) {
  return {
    probabilities: { L: home, E: draw, V: away },
    ticket_strategy: "SIMPLE",
    ...rest,
  };
}

function deps(overrides = {}) {
  return {
    ticketPlan: null,
    modelDoubleMatchIds: new Set(),
    modelTripleMatchIds: new Set(),
    modelFullDoubleMatchIds: new Set(),
    outcomeOrder: OUTCOME_ORDER,
    ...overrides,
  };
}

describe("multipleRuleForSlate", () => {
  it("uses the declared week type when there is one", () => {
    expect(multipleRuleForSlate(MULTIPLE_RULES, { week_type: "midweek" }, 9))
      .toBe(MULTIPLE_RULES.midweek);
  });

  it("falls back on match count for an unresolved slate", () => {
    expect(multipleRuleForSlate(MULTIPLE_RULES, null, 14)).toBe(MULTIPLE_RULES.weekend);
    expect(multipleRuleForSlate(MULTIPLE_RULES, null, 7)).toBe(MULTIPLE_RULES.revancha);
    expect(multipleRuleForSlate(MULTIPLE_RULES, null, 9)).toBe(MULTIPLE_RULES.fallback);
  });

  it("prefers an unknown declared type over the count", () => {
    // A week_type we have no rule for must not silently become weekend.
    expect(multipleRuleForSlate(MULTIPLE_RULES, { week_type: "mystery" }, 14))
      .toBe(MULTIPLE_RULES.weekend);
  });

  it("exposes the double limit", () => {
    expect(doubleLimitForSlate(MULTIPLE_RULES, { week_type: "weekend" })).toBe(4);
  });
});

describe("fixedModelDecision", () => {
  it("plays the single most likely sign", () => {
    const decision = fixedModelDecision(prediction({ home: 0.2, draw: 0.5, away: 0.3 }));
    expect(decision).toEqual({ type: "fixed", picks: ["X"], source: "model" });
  });
});

describe("decisionFromTicket", () => {
  const plan = {
    recommendations: [
      {
        match_id: "m1",
        decisions: {
          doubles: { pick_type: "double", picks: ["1", "X"], source: "optimizer" },
          simple: { pick_type: "fixed", picks: ["1"] },
        },
      },
    ],
  };

  it("returns the backend decision for the requested mode", () => {
    expect(decisionFromTicket(plan, "m1", "doubles")).toEqual({
      type: "double",
      picks: ["1", "X"],
      source: "optimizer",
    });
  });

  it("defaults the source to model when the backend omits it", () => {
    expect(decisionFromTicket(plan, "m1", "simple").source).toBe("model");
  });

  it("returns null for an unknown match or mode", () => {
    expect(decisionFromTicket(plan, "missing", "doubles")).toBeNull();
    expect(decisionFromTicket(plan, "m1", "full")).toBeNull();
    expect(decisionFromTicket(null, "m1", "doubles")).toBeNull();
  });

  it("finds the recommendation for a match", () => {
    expect(ticketRecommendationFor(plan, "m1").match_id).toBe("m1");
    expect(ticketRecommendationFor(plan, "nope")).toBeNull();
  });
});

describe("doublesModelDecision", () => {
  it("plays a confident single when it is clear and strategy allows it", () => {
    const decision = doublesModelDecision(
      prediction({ home: 0.7, draw: 0.15, away: 0.15 }),
      "m1",
      new Set(["m1"]),
    );
    expect(decision.type).toBe("fixed");
    expect(decision.picks).toEqual(["1"]);
  });

  it("does not shortcut to a single when the strategy forbids it", () => {
    // The guardrail that keeps a band-high friendly off a lone sign.
    const decision = doublesModelDecision(
      prediction({ home: 0.7, draw: 0.15, away: 0.15, ticket_strategy: "NO_DEJAR_SIMPLE" }),
      "m1",
      new Set(["m1"]),
    );
    expect(decision.type).toBe("double");
    expect(decision.picks).toEqual(["1", "X"]);
  });

  it("doubles the top two when the optimizer selected the position", () => {
    const decision = doublesModelDecision(
      prediction({ home: 0.4, draw: 0.35, away: 0.25 }),
      "m1",
      new Set(["m1"]),
    );
    expect(decision).toEqual({ type: "double", picks: ["1", "X"], source: "model" });
  });

  it("stays fixed when the optimizer did not select the position", () => {
    const decision = doublesModelDecision(
      prediction({ home: 0.4, draw: 0.35, away: 0.25 }),
      "m1",
      new Set(),
    );
    expect(decision.type).toBe("fixed");
  });

  it("stays fixed without a match id, whatever the set contains", () => {
    const decision = doublesModelDecision(
      prediction({ home: 0.4, draw: 0.35, away: 0.25 }),
      null,
      new Set(["m1"]),
    );
    expect(decision.type).toBe("fixed");
  });

  it("needs both a high probability and a wide gap for a confident single", () => {
    // 0.58 clears the probability bar but the gap is only 0.06.
    const decision = doublesModelDecision(
      prediction({ home: 0.58, draw: 0.52, away: 0.1 }),
      "m1",
      new Set(["m1"]),
    );
    expect(decision.type).toBe("double");
  });
});

describe("fullCoverageDecision", () => {
  it("triples a position the optimizer marked as a triple", () => {
    const decision = fullCoverageDecision(
      prediction(),
      "m1",
      deps({ modelTripleMatchIds: new Set(["m1"]) }),
    );
    expect(decision).toEqual({ type: "triple", picks: OUTCOME_ORDER, source: "model" });
  });

  it("prefers a triple over a double when a position is in both sets", () => {
    const decision = fullCoverageDecision(
      prediction(),
      "m1",
      deps({
        modelTripleMatchIds: new Set(["m1"]),
        modelFullDoubleMatchIds: new Set(["m1"]),
      }),
    );
    expect(decision.type).toBe("triple");
  });

  it("doubles the top two when only the double set has it", () => {
    const decision = fullCoverageDecision(
      prediction({ home: 0.5, draw: 0.3, away: 0.2 }),
      "m1",
      deps({ modelFullDoubleMatchIds: new Set(["m1"]) }),
    );
    expect(decision).toEqual({ type: "double", picks: ["1", "X"], source: "model" });
  });

  it("stays fixed when the position is in neither set", () => {
    expect(fullCoverageDecision(prediction(), "m1", deps()).type).toBe("fixed");
  });
});

describe("modelDecision", () => {
  const plan = {
    recommendations: [
      {
        match_id: "m1",
        decisions: {
          doubles: { pick_type: "double", picks: ["X", "2"], source: "optimizer" },
        },
      },
    ],
  };

  it("lets the backend recommendation win over the local rule", () => {
    // Locally this would be a confident single on "1"; the optimizer
    // sees the whole ticket, so its answer outranks ours.
    const decision = modelDecision(
      prediction({ home: 0.7, draw: 0.15, away: 0.15 }),
      "m1",
      "doubles",
      deps({ ticketPlan: plan }),
    );
    expect(decision).toEqual({ type: "double", picks: ["X", "2"], source: "optimizer" });
  });

  it("falls back to the local rule when the backend has no say", () => {
    const decision = modelDecision(
      prediction({ home: 0.7, draw: 0.15, away: 0.15 }),
      "m2",
      "doubles",
      deps({ ticketPlan: plan }),
    );
    expect(decision).toEqual({ type: "fixed", picks: ["1"], source: "model" });
  });

  it("ignores the backend plan entirely in simple mode", () => {
    const decision = modelDecision(
      prediction({ home: 0.2, draw: 0.3, away: 0.5 }),
      "m1",
      "simple",
      deps({ ticketPlan: plan }),
    );
    expect(decision).toEqual({ type: "fixed", picks: ["2"], source: "model" });
  });

  it("routes full mode to the coverage rule", () => {
    const decision = modelDecision(
      prediction(),
      "m1",
      "full",
      deps({ modelTripleMatchIds: new Set(["m1"]) }),
    );
    expect(decision.type).toBe("triple");
  });
});
