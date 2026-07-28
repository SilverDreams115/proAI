// S5.2: the boleta decision logic, lifted out of app.js so it can be
// tested without a browser. This is the money path — it decides which
// signs each position plays and how many doubles/triples the ticket is
// allowed — and it was the largest untested block left in app.js.
//
// config.js is a classic script, so `state`, `multipleRules` and
// `outcomeOrder` are globals there, not importable bindings. Every
// function here therefore takes what it needs as an argument instead of
// reaching for a global. app.js keeps same-named wrappers that pass the
// globals in, so its call sites are untouched and behaviour is
// unchanged; this module just makes the rules addressable.

import { sortedOutcomes, predictionAllowsConfidentSingle } from "./helpers.js";

/**
 * Which multiple-bet rule applies to a slate.
 *
 * The declared week_type wins when we have one. Falling back on the
 * match count matters for slates whose type has not been resolved yet:
 * 14 positions is a weekend concurso, 7 or fewer a revancha.
 */
export function multipleRuleForSlate(multipleRules, slate, matchCount = 0) {
  const weekType = slate?.week_type || "";
  if (multipleRules[weekType]) return multipleRules[weekType];
  if (matchCount >= 14) return multipleRules.weekend;
  if (matchCount <= 7) return multipleRules.revancha;
  return multipleRules.fallback;
}

export function doubleLimitForSlate(multipleRules, slate, matchCount = 0) {
  return multipleRuleForSlate(multipleRules, slate, matchCount).doublesOnlyMax;
}

/** Highest-probability sign, played alone. */
export function fixedModelDecision(prediction) {
  const best = sortedOutcomes(prediction)[0];
  return { type: "fixed", picks: [best.key], source: "model" };
}

export function ticketRecommendationFor(ticketPlan, matchId) {
  return ticketPlan?.recommendations?.find((item) => item.match_id === matchId) || null;
}

/**
 * The backend's own decision for this position, when it has one. It
 * outranks anything computed here — the optimizer sees the whole
 * ticket, these helpers only see one match at a time.
 */
export function decisionFromTicket(ticketPlan, matchId, mode) {
  const recommendation = ticketRecommendationFor(ticketPlan, matchId);
  const decision = recommendation?.decisions?.[mode];
  if (!decision) return null;
  return {
    type: decision.pick_type,
    picks: decision.picks,
    source: decision.source || "model",
  };
}

/**
 * Doubles mode. A position only goes double if the optimizer put it in
 * modelDoubleMatchIds — the local probability test can promote a
 * position to a confident single, never to a double.
 */
export function doublesModelDecision(prediction, matchId, modelDoubleMatchIds) {
  const outcomes = sortedOutcomes(prediction);
  const best = outcomes[0];
  const second = outcomes[1];
  const bestGap = best.value - second.value;
  const allowDouble = Boolean(matchId && modelDoubleMatchIds?.has(matchId));

  // Confident single only when the backend ticket_strategy is SIMPLE (a
  // product field). A band-high friendly carrying NO_DEJAR_SIMPLE must
  // not shortcut to a fixed. The legacy fallback lives in the helper.
  if (best.value >= 0.58 && bestGap >= 0.12 && predictionAllowsConfidentSingle(prediction)) {
    return { type: "fixed", picks: [best.key], source: "model" };
  }
  if (allowDouble) {
    return { type: "double", picks: [best.key, second.key], source: "model" };
  }
  return { type: "fixed", picks: [best.key], source: "model" };
}

/** Full-coverage mode: triple beats double beats fixed. */
export function fullCoverageDecision(
  prediction,
  matchId,
  { modelTripleMatchIds, modelFullDoubleMatchIds, outcomeOrder },
) {
  const outcomes = sortedOutcomes(prediction);
  const best = outcomes[0];
  const second = outcomes[1];
  if (matchId && modelTripleMatchIds?.has(matchId)) {
    return { type: "triple", picks: outcomeOrder, source: "model" };
  }
  if (matchId && modelFullDoubleMatchIds?.has(matchId)) {
    return { type: "double", picks: [best.key, second.key], source: "model" };
  }
  return { type: "fixed", picks: [best.key], source: "model" };
}

/**
 * Entry point: the decision for one position in one ticket mode.
 * Backend recommendation first, then the per-mode local rule.
 */
export function modelDecision(prediction, matchId, mode, deps) {
  if (matchId) {
    const ticketDecision = decisionFromTicket(deps.ticketPlan, matchId, mode);
    if (ticketDecision) return ticketDecision;
  }
  if (mode === "simple") return fixedModelDecision(prediction);
  if (mode === "full") return fullCoverageDecision(prediction, matchId, deps);
  return doublesModelDecision(prediction, matchId, deps.modelDoubleMatchIds);
}
