// S5.1: pure formatters + label helpers moved to helpers.js so they
// can be locked with Vitest without spinning up jsdom. The module
// import below replaces 12 inlined definitions that used to live
// here. Anything in app.js that touches DOM, state, or fetches stays
// put — the rule is "if it's not in helpers.js, it needs the
// browser to make sense."
import {
  formatPercent,
  formatDate,
  formatRelativeAge,
  availabilityStatusLabel,
  availabilityCategoryLabel,
  sortedOutcomes,
  linkedEvidenceCount,
  confidenceLabel,
  readinessLabel,
  dataQualityLabel,
  statusTone,
  buildQualityTooltip,
  drawRiskSummary,
  flagLabel,
  basePickBadge,
  resolveTicketStrategy,
  riskLevelLabel,
  riskTone,
  visibleConfidenceLabel,
  headlineConfidence,
  confidenceTone,
  decisionStatusLabel,
  limitChips,
  isTechAccordionTarget,
  effectiveConfidenceTier,
  predictionAllowsConfidentSingle,
  probBarWidthClass,
} from "./helpers.js";
import { renderTeamRatingShadowPanel } from "./team-rating-shadow.js";
import { renderTeamRatingActivationDryRunPanel } from "./team-rating-activation-dry-run.js";
import { renderTeamRatingActivationReadinessPanel } from "./team-rating-activation-readiness.js";
import { renderTeamRatingCanaryPanel } from "./team-rating-canary.js";
import { presentationGuardOf, SIGNAL_LABEL } from "./presentation-guard.js";
import { renderTicketCanaryDryRunPanel } from "./ticket-canary-dry-run.js";
import { renderMoneyModePanel } from "./money-mode.js";
import { renderOperationalMoneyModeStatusPanel } from "./operational-money-mode-status.js";
import { renderExternalResultsPanel } from "./external-results.js";
import { renderSlateOptionsPanel } from "./slate-options.js";
import { renderTrackingResultsValidationPanel } from "./tracking-results-validation.js";
import { renderLearningDashboard } from "./learning-dashboard.js";
import {
  getCachedDiagnostics,
  setCachedDiagnostics,
  clearDiagnosticsCache,
} from "./slate-panel-cache.js";
import { resolveActiveSelection, resolveVisibleSelection, selectedSlateCountdownMs, slateBadges, suspectSlateDiagnostics, pdfSourceDiagnosticLines } from "./slate-selection.js";
// NOTE: live-tracking is loaded via a guarded dynamic import in the
// bootstrap (not a static import), so a failure to load/link that module
// can never abort app.js and blank out the main selector.

function currentSlate() {
  return state.slates.find((item) => item.id === state.activeSlateId) || null;
}

// Useful empty state (never a blank screen): when no official slate is
// visible, explain discovery status + worker state + next action instead of
// a bare "Sin quiniela activa".
function renderNoSlateState() {
  const d = state.discovery || {};
  const workerState = state.worker
    ? (state.worker.running === false ? "detenido" : "activo")
    : "no disponible";
  const lastDiscovery = d.last_observed_at ? formatDate(d.last_observed_at) : "sin registro";
  const wk = d.last_weekend_draw_code
    ? `${escapeHtml(d.last_weekend_draw_code)} (${escapeHtml(d.last_weekend_status || "—")})`
    : "—";
  const ms = d.last_midweek_draw_code
    ? `${escapeHtml(d.last_midweek_draw_code)} (${escapeHtml(d.last_midweek_status || "—")})`
    : "—";
  const suspectDiag = suspectSlateDiagnostics({ discovery: d });
  const suspectBlock = suspectDiag.length
    ? `<div class="no-slate-suspect"><strong>Detectadas desde PDF oficial, no jugables (fecha inválida):</strong>` +
      suspectDiag
        .map(
          (s) =>
            `<div class="suspect-entry" data-suspect="${escapeHtml(s.draw_code)}">` +
            `<div class="suspect-head"><span class="mono">${escapeHtml(s.draw_code)}</span> <span class="suspect-status">${escapeHtml(s.date_status)}</span></div>` +
            `<ul>${pdfSourceDiagnosticLines(s).map((l) => `<li>${escapeHtml(l)}</li>`).join("")}</ul>` +
            `<p class="suspect-action">${escapeHtml(s.action)}</p></div>`,
        )
        .join("") +
      `</div>`
    : "";
  return `
    <div class="empty-state no-slate-state">
      <strong>No hay boletas oficiales cargadas</strong>
      <ul class="no-slate-meta">
        <li>Último intento de discovery: <span>${escapeHtml(lastDiscovery)}</span></li>
        <li>Última Weekend observada: <span>${wk}</span></li>
        <li>Última Media Semana observada: <span>${ms}</span></li>
        <li>Worker: <span>${escapeHtml(workerState)}</span></li>
        <li>Acción: ejecutar Scheduler o revisar la fuente LN.</li>
      </ul>
      ${suspectBlock}
    </div>`;
}

function multipleRuleForSlate(slate, matchCount = 0) {
  const weekType = slate?.week_type || "";
  if (multipleRules[weekType]) return multipleRules[weekType];
  if (matchCount >= 14) return multipleRules.weekend;
  if (matchCount <= 7) return multipleRules.revancha;
  return multipleRules.fallback;
}

function doubleLimitForSlate(slate, matchCount = 0) {
  return multipleRuleForSlate(slate, matchCount).doublesOnlyMax;
}

function fixedModelDecision(prediction) {
  const outcomes = sortedOutcomes(prediction);
  const best = outcomes[0];
  return {type: "fixed", picks: [best.key], source: "model"};
}

function ticketRecommendationFor(matchId) {
  return state.ticketPlan?.recommendations?.find((item) => item.match_id === matchId) || null;
}

function decisionFromTicket(matchId, mode = state.ticketMode) {
  const recommendation = ticketRecommendationFor(matchId);
  const decision = recommendation?.decisions?.[mode];
  if (!decision) return null;
  return {
    type: decision.pick_type,
    picks: decision.picks,
    source: decision.source || "model",
  };
}

function drawRiskFor(match) {
  // Compute X coverage from the three ticket modes, then defer to the
  // pure helper (which prefers the backend draw_risk block when present).
  const matchId = match.match_id;
  const prediction = match.prediction;
  const covers = (mode) => {
    const decision = decisionFromTicket(matchId, mode) || modelDecision(prediction, matchId, mode);
    return Boolean(decision?.picks?.includes("X"));
  };
  const provided = ticketRecommendationFor(matchId)?.draw_risk || null;
  return drawRiskSummary(
    prediction,
    { simple: covers("simple"), doubles: covers("doubles"), full: covers("full") },
    provided,
  );
}

function renderDrawChips(risk) {
  if (risk.isStrong) {
    return '<span class="chip chip-draw-strong" title="p(empate) ≥ 30%">Empate fuerte</span>';
  }
  if (risk.isLive) {
    return '<span class="chip chip-draw-live" title="p(empate) ≥ 25%">Empate vivo</span>';
  }
  return "";
}

function renderDrawCoverageBlock(risk) {
  const cell = (covered) =>
    covered
      ? '<span class="cover-yes">Sí</span>'
      : '<span class="cover-no">No</span>';
  return `
    <section class="analysis-block draw-coverage">
      <h4>Cobertura de X ${renderDrawChips(risk)}</h4>
      <div class="facts-grid">
        <div class="fact"><strong>p(empate)</strong><span>${formatPercent(risk.pDraw)} · ${risk.drawRank}º</span></div>
        <div class="fact"><strong>Simple</strong><span>${cell(risk.coveredSimple)}</span></div>
        <div class="fact"><strong>Dobles</strong><span>${cell(risk.coveredDoubles)}</span></div>
        <div class="fact"><strong>Completa</strong><span>${cell(risk.coveredFull)}</span></div>
      </div>
    </section>`;
}

function doublesModelDecision(prediction, matchId = null) {
  const outcomes = sortedOutcomes(prediction);
  const best = outcomes[0];
  const second = outcomes[1];
  const bestGap = best.value - second.value;
  const allowDouble = matchId && state.modelDoubleMatchIds.has(matchId);

  // Confident single only when the backend ticket_strategy is SIMPLE (product
  // field). A band-high friendly with ticket_strategy NO_DEJAR_SIMPLE no
  // longer shortcuts to a fixed. Legacy fallback lives inside the helper.
  if (best.value >= 0.58 && bestGap >= 0.12 && predictionAllowsConfidentSingle(prediction)) {
    return {type: "fixed", picks: [best.key], source: "model"};
  }
  if (allowDouble) {
    return {type: "double", picks: [best.key, second.key], source: "model"};
  }
  return {type: "fixed", picks: [best.key], source: "model"};
}

function fullCoverageDecision(prediction, matchId = null) {
  const outcomes = sortedOutcomes(prediction);
  const best = outcomes[0];
  const second = outcomes[1];
  if (matchId && state.modelTripleMatchIds.has(matchId)) {
    return {type: "triple", picks: outcomeOrder, source: "model"};
  }
  if (matchId && state.modelFullDoubleMatchIds.has(matchId)) {
    return {type: "double", picks: [best.key, second.key], source: "model"};
  }
  return {type: "fixed", picks: [best.key], source: "model"};
}

function modelDecision(prediction, matchId = null, mode = state.ticketMode) {
  if (matchId) {
    const ticketDecision = decisionFromTicket(matchId, mode);
    if (ticketDecision) return ticketDecision;
  }
  if (mode === "simple") return fixedModelDecision(prediction);
  if (mode === "full") return fullCoverageDecision(prediction, matchId);
  return doublesModelDecision(prediction, matchId);
}

function uncertaintyProfile(match) {
  const outcomes = sortedOutcomes(match.prediction);
  const [best, second, third] = outcomes;
  // Product-first: derive the confidence tier from the guardrailed
  // final_status (which already folds in flags + risk). Legacy band only as
  // fallback. This is the client risk model used when no backend ticket
  // recommendation is loaded yet (validationProfile prefers backend data).
  const confidence = effectiveConfidenceTier(match.prediction);
  const readiness = match.prediction.competition_readiness || "unclassified";
  const evidenceCount = linkedEvidenceCount(match);
  const entropy = outcomes.reduce((total, item) => {
    const probability = Math.max(item.value || 0, 0.001);
    return total - probability * Math.log(probability);
  }, 0) / Math.log(3);
  const topGap = best.value - second.value;
  const secondGap = second.value - third.value;
  const confidenceRisk = {
    high: -0.08,
    medium: 0.02,
    low: 0.14,
    blocked: 0.16,
  }[confidence] ?? 0.08;
  const readinessRisk = {
    ready: -0.06,
    covered: 0.02,
    context_only: 0.08,
    not_ready: 0.12,
    unclassified: 0.12,
  }[readiness] ?? 0.08;
  const evidenceRisk = evidenceCount <= 0 ? 0.08 : evidenceCount < 2 ? 0.03 : -0.02;
  const gapRisk = topGap <= 0.08 ? 0.14 : topGap <= 0.14 ? 0.08 : topGap <= 0.22 ? 0.03 : -0.04;
  const thirdOutcomeRisk = third.value >= 0.24 ? 0.09 : third.value >= 0.20 ? 0.05 : 0;
  const validationRisk = entropy + confidenceRisk + readinessRisk + evidenceRisk + gapRisk + thirdOutcomeRisk;
  return {
    matchId: match.match_id,
    entropy,
    topGap,
    secondGap,
    bestOutcome: best.key,
    secondOutcome: second.key,
    thirdOutcome: third.key,
    bestProbability: best.value,
    secondProbability: second.value,
    thirdProbability: third.value,
    confidence,
    readiness,
    evidenceCount,
    confidenceRisk,
    readinessRisk,
    evidenceRisk,
    gapRisk,
    thirdOutcomeRisk,
    validationRisk,
    doubleScore: validationRisk + second.value * 0.7 - topGap * 0.35,
    tripleScore: validationRisk + third.value * 1.45 - topGap * 0.18 - secondGap * 0.1,
  };
}

function modelDoubleCandidate(match) {
  const profile = uncertaintyProfile(match);
  if (profile.topGap >= 0.18 && profile.validationRisk < 1.02 && !["low", "blocked"].includes(profile.confidence)) {
    return null;
  }
  return {
    matchId: match.match_id,
    score: profile.doubleScore,
  };
}

function chooseModelDoubleMatchIds(matches, slate = currentSlate()) {
  const limit = doubleLimitForSlate(slate, matches.length);
  return new Set(
    matches
      .map(modelDoubleCandidate)
      .filter(Boolean)
      .sort((a, b) => b.score - a.score)
      .slice(0, limit)
      .map((item) => item.matchId)
  );
}

function chooseFullCoverageMatchIds(matches, slate = currentSlate()) {
  const rule = multipleRuleForSlate(slate, matches.length);
  const profiles = matches.map(uncertaintyProfile);
  const tripleMatchIds = new Set(
    profiles
      .filter((item) => item.thirdProbability >= 0.2 || item.topGap <= 0.08 || item.validationRisk >= 1.14)
      .sort((a, b) => b.tripleScore - a.tripleScore)
      .slice(0, rule.combinedTripleMax)
      .map((item) => item.matchId)
  );
  const doubleMatchIds = new Set(
    profiles
      .filter((item) => !tripleMatchIds.has(item.matchId))
      .sort((a, b) => b.doubleScore - a.doubleScore)
      .slice(0, rule.combinedDoubleMax)
      .map((item) => item.matchId)
  );
  return {doubleMatchIds, tripleMatchIds};
}

function validationProfile(match) {
  const ticketRecommendation = ticketRecommendationFor(match.match_id);
  if (ticketRecommendation?.validation) {
    const metrics = ticketRecommendation.validation.metrics || {};
    return {
      level: ticketRecommendation.validation.level,
      label: ticketRecommendation.validation.label,
      recommendation: ticketRecommendation.validation.recommendation,
      className: `risk-${ticketRecommendation.validation.level}`,
      reasons: ticketRecommendation.validation.reasons || [],
      profile: {
        entropy: Number(metrics.entropy || 0),
        topGap: Number(metrics.top_gap || 0),
        secondGap: Number(metrics.second_gap || 0),
        bestOutcome: metrics.best_outcome || "1",
        secondOutcome: metrics.second_outcome || "X",
        thirdOutcome: metrics.third_outcome || "2",
        bestProbability: Number(metrics.best_probability || 0),
        secondProbability: Number(metrics.second_probability || 0),
        thirdProbability: Number(metrics.third_probability || 0),
        confidence: metrics.confidence || "low",
        readiness: metrics.competition_readiness || "unclassified",
        evidenceCount: Number(metrics.evidence_count || 0),
        validationRisk: Number(metrics.validation_risk || 0),
      },
    };
  }
  const profile = uncertaintyProfile(match);
  const reasons = [];

  if (profile.topGap <= 0.08) {
    reasons.push(`brecha muy cerrada entre ${displayOutcome(profile.bestOutcome)} y ${displayOutcome(profile.secondOutcome)} (${formatPercent(profile.topGap)})`);
  } else if (profile.topGap <= 0.18) {
    reasons.push(`brecha moderada entre las dos primeras opciones (${formatPercent(profile.topGap)})`);
  } else {
    reasons.push(`brecha principal defendible (${formatPercent(profile.topGap)})`);
  }

  if (profile.confidence === "high") {
    reasons.push("confianza alta");
  } else if (profile.confidence === "medium") {
    reasons.push("confianza media");
  } else {
    reasons.push(`confianza ${confidenceLabel(profile.confidence)}`);
  }

  if (["ready", "covered"].includes(profile.readiness)) {
    reasons.push(`referencia ${readinessLabel(profile.readiness)}`);
  } else {
    reasons.push(`referencia ${readinessLabel(profile.readiness)}; no se trata como jugada simple segura`);
  }

  if (profile.evidenceCount <= 0) {
    reasons.push("sin evidencia contextual ligada");
  } else {
    reasons.push(`${profile.evidenceCount} evidencia(s) ligada(s)`);
  }

  if (profile.thirdProbability >= 0.22) {
    reasons.push(`el tercer resultado conserva peso (${formatPercent(profile.thirdProbability)})`);
  }

  let level = "low";
  let label = "Defendible (simple)";
  let recommendation = "Puede quedar simple si no hay presupuesto para cobertura.";
  // Backend already issued a calibrated band (post knockout E=0
  // redistribution where applicable). Trust it: if it landed medium
  // or high, a tight L/V topGap is not sufficient grounds to send the
  // match to the "Revisar" tab — that's the model deliberately
  // expressing "close but defensible." Knockouts also skip the gap
  // penalty wholesale: the comparison is binary by construction.
  const isKnockout = Boolean(match.prediction?.is_knockout);
  const backendTrusted = ["medium", "high"].includes(profile.confidence);
  const tightGapPenalty = !isKnockout && !backendTrusted && profile.topGap <= 0.08;
  const moderateGapPenalty = !isKnockout && !backendTrusted && profile.topGap <= 0.18;
  if (
    tightGapPenalty ||
    profile.validationRisk >= 1.16 ||
    profile.confidence === "low" ||
    profile.thirdProbability >= 0.24
  ) {
    level = "high";
    label = "No dejar simple";
    recommendation = "Priorizar doble o triple en completa.";
  } else if (
    moderateGapPenalty ||
    profile.validationRisk >= 1.0 ||
    profile.confidence === "blocked" ||
    !["ready", "covered"].includes(profile.readiness) ||
    (profile.evidenceCount <= 1 && (profile.h2hCount ?? 0) <= 1)
  ) {
    level = "medium";
    label = "Cubrir si hay presupuesto";
    recommendation = "Mantener simple solo si la papeleta ya agotó dobles/triples.";
  }

  return {
    level,
    label,
    recommendation,
    className: `risk-${level}`,
    reasons,
    profile,
  };
}

function qualityIssueProfile(match) {
  const validation = validationProfile(match);
  const quality = match.quality || {};
  const prediction = match.prediction || {};
  const readiness = prediction.competition_readiness || "unclassified";
  const score = Number.isFinite(Number(quality.quality_score)) ? Number(quality.quality_score) : null;
  const evidence = Number(quality.evidence_count ?? linkedEvidenceCount(match) ?? 0);
  const recent = Number(quality.recent_results_count ?? match.features?.payload?.recent_results_count ?? 0);
  const h2h = Number(quality.head_to_head_results_count ?? match.features?.payload?.head_to_head_results_count ?? 0);
  // The backend sanity layer (Fase 3/4) is the source of truth for the
  // guardrailed status when it is present. `final_status` is one of
  // FIJO / LISTO / REVISAR / BLOQUEADO and already folds in low-evidence,
  // international-friendly and extreme-probability rules. We honour it
  // directly and fall back to the legacy heuristics only for older
  // payloads that predate the sanity fields.
  const backendStatus = typeof prediction.final_status === "string" ? prediction.final_status : null;
  const sanityFlags = Array.isArray(prediction.flags) ? prediction.flags : [];
  const blocked =
    backendStatus === "BLOQUEADO" ||
    // Legacy fallback only: when there is no product final_status, fall back
    // to the raw model band for the blocked classification.
    (!backendStatus && prediction.confidence_band === "blocked") ||
    readiness === "unclassified";
  const benchmarkWeak = readiness === "not_ready";
  const cautionOnly = readiness === "covered" || readiness === "context_only" || prediction.live_pick_allowed === false;
  const thin = quality.quality_level === "thin" || (score !== null && score < 40);
  // We don't run a per-match news scraper yet, so `evidence` is 0 on
  // almost every fixture. Treat H2H or recent history as an equivalent
  // anchor — if any of the three is present, the partido has real data
  // behind it and doesn't need to be downgraded automatically.
  const anchored = evidence > 0 || recent > 0 || h2h > 0;

  // `review` aligns with the visual "Revisar" label (tone === "bad"): the
  // user must look at the match before signing the ticket. `caution` is
  // the warn-tone bucket — match is usable but with reduced confidence,
  // shown in its own filter so the "Revisar" tab does not swallow it.
  let review;
  let caution;
  if (backendStatus) {
    review = backendStatus === "REVISAR" || backendStatus === "BLOQUEADO";
    caution = !review && backendStatus === "LISTO" && sanityFlags.length > 0;
  } else {
    review = blocked || validation.level === "high";
    caution =
      !review &&
      (benchmarkWeak || cautionOnly || thin || !anchored || validation.level === "medium");
  }

  const reasons = [];
  // Surface the explicit sanity flags first — they name the precise reason
  // the guardrail degraded the pick (LOW_EVIDENCE, INTERNATIONAL_FRIENDLY,
  // EXTREME_PROBABILITY_WITHOUT_EVIDENCE, FALLBACK_USED, ...).
  for (const flag of sanityFlags) reasons.push(flagLabel(flag));
  if (blocked) reasons.push("sin referencia confiable");
  if (benchmarkWeak) reasons.push("referencia débil");
  if (!blocked && !benchmarkWeak && cautionOnly) reasons.push("usar con cautela");
  if (thin) reasons.push("calidad de datos delgada");
  if (validation.level === "high") reasons.push(validation.label.toLowerCase());
  if (!anchored) reasons.push("sin evidencia, forma reciente o H2H ligados");
  else if (evidence <= 0 && recent <= 0) reasons.push("solo H2H como anclaje");
  else if (evidence <= 0 && h2h <= 0) reasons.push("solo forma reciente como anclaje");

  let tone = "ok";
  let label = "Listo";
  if (review) {
    tone = "bad";
    label = "Revisar";
  } else if (caution) {
    tone = "warn";
    label = "Cautela";
  }

  return {
    blocked,
    benchmarkWeak,
    cautionOnly,
    thin,
    review,
    caution,
    tone,
    label,
    score,
    evidence,
    recent,
    h2h,
    reasons,
    flags: sanityFlags,
    finalStatus: backendStatus,
  };
}

function filteredMatches() {
  if (state.qualityFilter === "all") return state.matches;
  return state.matches.filter((match) => {
    const issue = qualityIssueProfile(match);
    const decision = effectiveDecision(match);
    if (state.qualityFilter === "review") return issue.review;
    if (state.qualityFilter === "caution") return issue.caution;
    if (state.qualityFilter === "thin") return issue.thin;
    if (state.qualityFilter === "blocked") return issue.blocked;
    if (state.qualityFilter === "manual") return decision.source === "manual";
    return true;
  });
}

function effectiveDecision(match) {
  const manual = state.manualSelections[match.match_id];
  if (!manual || !manual.length) return modelDecision(match.prediction, match.match_id);
  const sorted = [...manual].sort((a, b) => outcomeOrder.indexOf(a) - outcomeOrder.indexOf(b));
  const type = sorted.length === 1 ? "fixed" : sorted.length === 2 ? "double" : "triple";
  return {type, picks: sorted, source: "manual"};
}

function setManualSelection(matchId, option) {
  const current = new Set(state.manualSelections[matchId] || []);
  if (current.has(option)) {
    current.delete(option);
  } else {
    current.add(option);
    if (current.size > 3) {
      const list = [...current];
      current.clear();
      list.slice(-3).forEach((item) => current.add(item));
    }
  }

  const next = [...current];
  const model = state.matches.find((item) => item.match_id === matchId);
  if (model) {
    const suggestion = modelDecision(model.prediction, model.match_id, state.ticketMode).picks;
    const normalizedNext = [...next].sort().join(",");
    const normalizedSuggestion = [...suggestion].sort().join(",");
    if (!next.length || normalizedNext === normalizedSuggestion) {
      delete state.manualSelections[matchId];
      return;
    }
  }
  state.manualSelections[matchId] = next;
}

function resetManualSelections() {
  state.manualSelections = {};
}

async function createDemoSlate() {
  const baseDate = new Date("2026-05-20T18:00:00Z");
  const competitions = [
    "Liga MX",
    "Premier League",
    "Serie A",
    "LaLiga",
    "MLS",
    "Brasileirao",
    "Copa Libertadores",
    "Championship",
  ];
  const matches = Array.from({length: 8}, (_, idx) => ({
    position: idx + 1,
    competition: {
      name: competitions[idx],
      country: "Global",
      season: "2026",
    },
    home_team: {name: `Equipo ${idx + 1}A`, country: "Global"},
    away_team: {name: `Equipo ${idx + 1}B`, country: "Global"},
    kickoff_at: new Date(baseDate.getTime() + idx * 90 * 60 * 1000).toISOString(),
    venue: `Estadio ${idx + 1}`,
  }));

  const suffix = Date.now().toString().slice(-6);
  return safePost("/slates", {
    label: `Progol Demo ${suffix}`,
    draw_code: `PG-DEMO-${suffix}`,
    week_type: "weekend",
    matches,
  });
}

const COMPETITION_NAMES = {
  "International Friendlies": "Amistosos internacionales",
  "World Cup": "Copa del Mundo",
  "World Cup Qualifying UEFA": "Eliminatorias UEFA",
  "World Cup Qualifying CONMEBOL": "Eliminatorias CONMEBOL",
  "World Cup Qualifying CAF": "Eliminatorias CAF",
  "World Cup Qualifying AFC": "Eliminatorias AFC",
  "World Cup Qualifying CONCACAF": "Eliminatorias CONCACAF",
  "World Cup Qualifying OFC": "Eliminatorias OFC",
  "UEFA Nations League": "Liga de Naciones UEFA",
  "UEFA Champions League": "Liga de Campeones UEFA",
  "UEFA Europa League": "Liga Europa UEFA",
  "UEFA Conference League": "Conference League UEFA",
};
const translateCompetition = (name) => COMPETITION_NAMES[name] || name;

function buildMatchCard(match) {
  const decision = effectiveDecision(match);
  const validation = validationProfile(match);
  const issue = qualityIssueProfile(match);
  const activeClass = state.selectedMatchId === match.match_id ? " active" : "";
  const manualClass = decision.source === "manual" ? " manual" : "";
  const reviewClass = issue.review ? " review" : "";
  const cautionClass = !issue.review && issue.caution ? " caution" : "";
  const pickOkClass = !issue.review && !issue.caution && issue.tone === "ok" ? " pick-ok" : "";
  const matchId = escapeHtml(match.match_id);
  const options = [
    ["1", match.prediction.home_probability],
    ["X", match.prediction.draw_probability],
    ["2", match.prediction.away_probability],
  ];

  const optionsMarkup = options
    .map(([key, value]) => {
      const barTier = Math.min(90, Math.max(10, Math.round(Number(value) * 10) * 10));
      const classes = [
        "option-pill",
        `bar-${barTier}`,
        match.prediction.recommended_outcome === key ? "active" : "",
        decision.picks.includes(key) && decision.source === "model" && decision.type !== "fixed" ? "secondary" : "",
        decision.picks.includes(key) && decision.source === "manual" ? "manual-choice" : "",
      ]
        .filter(Boolean)
        .join(" ");

      const isSelected = decision.picks.includes(key);
      const outcomeNames = { "1": "Local", X: "Empate", "2": "Visitante" };
      return `
        <button class="${classes}" data-option-pick="${key}" data-option-match="${matchId}"
          title="${outcomeNames[key]}: ${formatPercent(value)}"
          aria-label="${outcomeNames[key]} (${displayOutcome(key)}) ${formatPercent(value)}"
          aria-pressed="${isSelected}">
          <strong>${displayOutcome(key)}</strong>
          <span>${formatPercent(value)}</span>
        </button>
      `;
    })
    .join("");

  // --- Semantic separation (Fase 3): base pick / strategy / risk -----------
  const pred = match.prediction;
  const basePick = basePickBadge(pred.recommended_outcome);
  const strategy = resolveTicketStrategy({
    prediction: pred,
    validationLevel: validation.level,
    decisionType: decision.type,
  });
  const riskLevel = pred.risk_level || "high";
  const visibleConf = pred.visible_confidence || "baja";

  // Visible "Motivos": prefer the backend confidence_explanation (already
  // <=3, already deduped); fall back to flag labels. Hard cap at 3 + "+N".
  const explanation = Array.isArray(pred.confidence_explanation) && pred.confidence_explanation.length
    ? pred.confidence_explanation
    : (Array.isArray(pred.flags) ? pred.flags.map(flagLabel) : []);
  const { visible: reasonChips, hiddenCount } = limitChips(explanation, 3);
  const reasonMarkup = reasonChips
    .map((r) => `<span class="reason-chip">${escapeHtml(r)}</span>`)
    .join("");

  // Full flag list lives in the per-card accordion (technical), never lost.
  const allFlags = Array.isArray(pred.flags) ? pred.flags : [];
  const flagDetailMarkup = allFlags.length
    ? allFlags.map((f) => `<li>${escapeHtml(flagLabel(f))} <span class="mono">${escapeHtml(f)}</span></li>`).join("")
    : "<li>Sin flags de la capa de seguridad.</li>";
  const detailsToggle = (hiddenCount > 0 || allFlags.length > 0)
    ? `<details class="card-tech">
         <summary>${hiddenCount > 0 ? `+${hiddenCount} detalles` : "Detalles técnicos"}</summary>
         <ul class="card-tech-flags">${flagDetailMarkup}</ul>
         <p class="mono card-tech-vectors">raw L/E/V ${fmtVec(pred.raw_probabilities)} · decisión ${fmtVec(pred.decision_probabilities || pred.probabilities)}</p>
       </details>`
    : "";

  return `
    <article class="pick-row${activeClass}${manualClass}${reviewClass}${cautionClass}${pickOkClass}" data-match-card="${matchId}"
      role="button" tabindex="0"
      aria-label="Partido ${escapeHtml(match.position)}: ${escapeHtml(pred.home_team_name)} vs ${escapeHtml(pred.away_team_name)}">
      <div class="pick-index">
        <strong>#${escapeHtml(match.position)}</strong>
        <small class="quality-mini ${issue.tone}">${issue.score !== null ? escapeHtml(issue.score) : "SD"}</small>
      </div>
      <div class="pick-meta">
        <h3>${escapeHtml(pred.home_team_name)} vs ${escapeHtml(pred.away_team_name)}</h3>
        <p class="pick-sub">${escapeHtml(formatDate(match.kickoff_at))} · ${escapeHtml(translateCompetition(pred.competition_name || ""))}<span class="freshness-tag" title="Cuándo se calculó esta probabilidad">Actualizado ${escapeHtml(formatRelativeAge(pred.generated_at))}</span></p>
        <div class="signal-row">
          <span class="badge-signal" title="Señal base del modelo">${escapeHtml(basePick.label)}</span>
          <span class="badge-strategy tone-${strategy.tone}" title="Estrategia de boleta recomendada">${escapeHtml(strategy.label)}</span>
          <span class="badge-risk tone-${riskTone(riskLevel)}" title="Riesgo del partido para la quiniela">Riesgo ${escapeHtml(riskLevelLabel(riskLevel))}</span>
          ${decision.source === "manual" ? `<span class="badge-muted">Manual</span>` : ""}
          ${pred.is_knockout ? `<span class="badge-muted" title="Eliminatoria: empate descartado">Eliminatoria</span>` : ""}
          ${renderCanaryBadge(pred)}
        </div>
        ${reasonMarkup ? `<div class="reason-row">${reasonMarkup}</div>` : ""}
        ${detailsToggle}
      </div>
      <div class="pick-options">
        <div class="options-grid">${optionsMarkup}</div>
        <div class="double-row">
          ${renderRecommendationBlock(pred, decision, visibleConf)}
        </div>
      </div>
    </article>
  `;
}

// The bottom-of-card recommendation block. When the pick is not simple-playable
// the "señal principal" is shown separately from a warning "Recomendación",
// so a high-risk V can never read as "Sugerencia: V".
function renderRecommendationBlock(pred, decision, visibleConf) {
  if (decision && decision.source === "manual") {
    return `<div class="double-tag">Selección manual: ${escapeHtml(displayPicks(decision.picks))} · Confianza ${escapeHtml(visibleConfidenceLabel(visibleConf))}</div>`;
  }
  const g = presentationGuardOf(pred);
  if (g.simple_allowed) {
    return `<div class="double-tag">Sugerencia: ${escapeHtml(displayPicks(decision.picks))} · Confianza ${escapeHtml(visibleConfidenceLabel(visibleConf))}</div>`;
  }
  const signal = SIGNAL_LABEL[g.primary_signal] || g.primary_signal || "—";
  return `
    <div class="double-tag double-tag-warn" data-not-simple="true">
      <span class="reco-signal">Señal principal: <strong>${escapeHtml(signal)}</strong></span>
      <span class="reco-label badge-risk tone-warn">Recomendación: ${escapeHtml(g.recommendation_label)}</span>
      <span class="reco-meta">Confianza ${escapeHtml(visibleConfidenceLabel(visibleConf))} · Riesgo ${escapeHtml(riskLevelLabel(g.risk_level))}</span>
    </div>`;
}

// Compact "55/26/19" formatter for an {L,E,V} vector (accordion display).
function fmtVec(vec) {
  if (!vec || typeof vec !== "object") return "—";
  const pct = (v) => (Number.isFinite(Number(v)) ? Math.round(Number(v) * 100) : "—");
  return `${pct(vec.L)}/${pct(vec.E)}/${pct(vec.V)}`;
}

function buildMatchMenuItem(match) {
  const decision = effectiveDecision(match);
  const issue = qualityIssueProfile(match);
  const activeClass = state.selectedMatchId === match.match_id ? " active" : "";
  return `
    <button class="match-menu-item${activeClass}" data-match-menu-id="${escapeHtml(match.match_id)}">
      <span>${escapeHtml(match.position)}</span>
      <strong>${escapeHtml(displayPicks(decision.picks))}</strong>
      <small class="${issue.tone}">${escapeHtml(issue.label)}</small>
    </button>
  `;
}

function renderTicketTabs() {
  return ticketModes.map((mode) => `
    <button class="ticket-tab ${state.ticketMode === mode.key ? "active" : ""}" data-ticket-mode="${escapeHtml(mode.key)}">
      <strong>${escapeHtml(mode.label)}</strong>
      <span>${escapeHtml(mode.description)}</span>
    </button>
  `).join("");
}

function renderSlateSwitcher() {
  return state.slates.map((slate) => `
    <button class="slate-switch ${slate.id === state.activeSlateId ? "active" : ""}" data-slate-id="${escapeHtml(slate.id)}">
      ${escapeHtml(slate.draw_code)}
    </button>
  `).join("");
}

function renderAsideSlates() {
  const STATUS_CLASS = {
    "Con ticket": "ok",
    "Con predicciones": "ok",
    "Predicción live": "ok",
    "Pendiente de predicción": "warn",
    "Sin predicción": "warn",
    "Sin datos": "warn",
    "Archivada": "muted",
    "Cerrada": "muted",
  };
  const grouped = {};
  for (const slate of state.slates) {
    const wt = slate.week_type || "other";
    if (!grouped[wt]) grouped[wt] = [];
    grouped[wt].push(slate);
  }
  const renderSlateBtn = (slate) => {
    const isActive = slate.id === state.activeSlateId;
    const statusLabel = slate.status_label || (slate.is_archived ? "Archivada" : "Activa");
    const statusCls = STATUS_CLASS[statusLabel] || "";
    const matchCount = (slate.matches || []).length;
    const badges = slateBadges(slate)
      .map((b) => `<span class="aside-badge badge-${b === "Solo lectura" ? "readonly" : b.toLowerCase().replace(/\s+/g, "-")}">${escapeHtml(b)}</span>`)
      .join("");
    return `
      <button class="aside-slate ${isActive ? "active" : ""} ${slate.read_only ? "is-readonly" : ""}" data-slate-id="${escapeHtml(slate.id)}">
        <strong>${escapeHtml(slate.draw_code)}</strong>
        <span class="aside-slate-status ${statusCls}">${escapeHtml(statusLabel)}</span>
        <span class="aside-badges">${badges}</span>
        <small>${matchCount} partidos</small>
      </button>
    `;
  };
  const sections = [];
  // Weekend — always shown.
  const wkSlates = grouped["weekend"] || [];
  const wkContent = wkSlates.length
    ? wkSlates.map(renderSlateBtn).join("")
    : `<p class="aside-empty-label">Sin quiniela fin de semana activa</p>`;
  sections.push(`<div class="aside-week-group"><h3 class="aside-week-label">Fin de semana</h3>${wkContent}</div>`);
  // Midweek/MS — always shown, empty state when no active MS.
  const msSlates = grouped["midweek"] || [];
  const msContent = msSlates.length
    ? msSlates.map(renderSlateBtn).join("")
    : `<p class="aside-empty-label">No hay Progol MS activo<br><span>Esperando guía de media semana</span></p>`;
  sections.push(`<div class="aside-week-group"><h3 class="aside-week-label">Media semana</h3>${msContent}</div>`);
  // Revancha — only when present.
  const revSlates = grouped["revancha"] || [];
  if (revSlates.length) {
    sections.push(`<div class="aside-week-group"><h3 class="aside-week-label">Revancha</h3>${revSlates.map(renderSlateBtn).join("")}</div>`);
  }
  return sections.join("");
}

function renderValidationSummary() {
  if (!state.authenticated || !state.matches.length) return "";
  const issues = state.matches.map((match) => {
    const decision = effectiveDecision(match);
    return {
      match,
      issue: qualityIssueProfile(match),
      decision,
      strategy: resolveTicketStrategy({
        prediction: match.prediction,
        validationLevel: validationProfile(match).level,
        decisionType: decision.type,
      }),
    };
  });
  const review = issues.filter((item) => item.issue.review);
  const caution = issues.filter((item) => item.issue.caution);
  const thin = issues.filter((item) => item.issue.thin);
  const blocked = issues.filter((item) => item.issue.blocked);
  // Counters use PRODUCT fields, never raw confidence_band: a "defendible"
  // is only a match whose backend ticket_strategy is SIMPLE. This stops the
  // summary from calling a risky/flagged friendly "fijo defendible".
  const defensibleSimple = issues.filter((item) => item.strategy.key === "SIMPLE");
  const manual = issues.filter((item) => item.decision.source === "manual");
  const doubles = issues.filter((item) => item.decision.type === "double");
  const triples = issues.filter((item) => item.decision.type === "triple");
  const fixed = issues.filter((item) => item.decision.type === "fixed");
  const reviewStrip = (() => {
    const parts = [];
    if (review.length) {
      parts.push(`<span class="review-strip-label">Revisar:</span>`);
      review.forEach(({match, issue}) => {
        const isActive = state.selectedMatchId === match.match_id;
        parts.push(`<button class="pos-chip ${issue.tone}${isActive ? " selected" : ""}" data-match-menu-id="${escapeHtml(match.match_id)}" title="${escapeHtml(match.prediction.home_team_name)} vs ${escapeHtml(match.prediction.away_team_name)}" aria-label="Partido ${escapeHtml(match.position)}: ${escapeHtml(issue.label)}" aria-pressed="${isActive}">${escapeHtml(match.position)}</button>`);
      });
    }
    if (caution.length) {
      if (review.length) parts.push(`<span class="review-strip-sep">·</span>`);
      parts.push(`<span class="review-strip-label">Cautela:</span>`);
      caution.forEach(({match}) => {
        const isActive = state.selectedMatchId === match.match_id;
        parts.push(`<button class="pos-chip warn${isActive ? " selected" : ""}" data-match-menu-id="${escapeHtml(match.match_id)}" title="${escapeHtml(match.prediction.home_team_name)} vs ${escapeHtml(match.prediction.away_team_name)}" aria-label="Partido ${escapeHtml(match.position)}: con cautela" aria-pressed="${isActive}">${escapeHtml(match.position)}</button>`);
      });
    }
    return parts.length
      ? `<div class="review-strip">${parts.join("")}</div>`
      : `<div class="empty-state compact">Todos los partidos tienen cobertura suficiente.</div>`;
  })();

  const coverage = (state.ticketPlan?.coverage || []).find((mode) => mode.mode === state.ticketMode);
  const slateSize = state.matches.length;
  const jackpotProb = coverage?.jackpot_probability ?? 0;
  const nearJackpotProb = coverage?.near_jackpot_probability ?? 0;
  const ticketsHalf = coverage?.tickets_for_half_chance ?? null;
  // Honest framing: Progol pays N/N only (per user feedback "no progol
  // no paga 8/9"). The chip leads with the jackpot probability; the
  // near-jackpot is informational only.
  const formatProb = (p) => {
    if (!p || p <= 0) return "0%";
    if (p >= 0.01) return `${(p * 100).toFixed(2)}%`;
    if (p >= 0.0001) return `${(p * 100).toFixed(3)}%`;
    if (p >= 0.000001) return `${(p * 100).toFixed(5)}%`;
    const odds = Math.round(1 / p).toLocaleString("es-MX");
    return `1 en ${odds}`;
  };
  const jackpotTone = coverage
    ? jackpotProb >= 0.01 ? "ok" : jackpotProb >= 0.001 ? "warn" : "bad"
    : "";
  const jackpotValue = coverage
    ? `${formatProb(jackpotProb)} · ${formatProb(nearJackpotProb)} ≥${slateSize - 1}/${slateSize}`
    : "n/d";
  const ticketsLabel = ticketsHalf != null
    ? `≈ ${ticketsHalf.toLocaleString("es-MX")} boletas`
    : "n/d";

  return `
    <div class="summary-band">
      <div>
        <span>Jugada actual</span>
        <strong>${fixed.length} simples · ${doubles.length} dobles · ${triples.length} triples</strong>
      </div>
      <div class="${jackpotTone}">
        <span>Jackpot ${slateSize}/${slateSize}</span>
        <strong>${escapeHtml(jackpotValue)}</strong>
      </div>
      <div class="${ticketsHalf != null && ticketsHalf <= 50 ? "ok" : (ticketsHalf != null && ticketsHalf <= 500 ? "warn" : "bad")}">
        <span>Cobertura ~50%</span>
        <strong>${escapeHtml(ticketsLabel)}</strong>
      </div>
      <div class="${defensibleSimple.length ? "ok" : ""}" title="Partidos cuya estrategia de boleta es SIMPLE (señal defendible)">
        <span>Defendible (simple)</span>
        <strong>${escapeHtml(defensibleSimple.length)}</strong>
      </div>
      <div class="${review.length ? "bad" : "ok"}">
        <span>A revisar</span>
        <strong>${escapeHtml(review.length)}</strong>
      </div>
      <div class="${caution.length ? "warn" : "ok"}">
        <span>Con cautela</span>
        <strong>${escapeHtml(caution.length)}</strong>
      </div>
      <div class="${blocked.length ? "bad" : "ok"}">
        <span>Sin datos</span>
        <strong>${escapeHtml(blocked.length)}</strong>
      </div>
      <div class="${manual.length ? "ok" : ""}">
        <span>Ajustados</span>
        <strong>${escapeHtml(manual.length)}</strong>
      </div>
    </div>
    ${reviewStrip}
  `;
}

function renderQualityFilterOptions() {
  return qualityFilters.map((filter) => `
    <option value="${escapeHtml(filter.key)}" ${state.qualityFilter === filter.key ? "selected" : ""}>
      ${escapeHtml(filter.label)}
    </option>
  `).join("");
}

function buildTicketText() {
  const slate = currentSlate();
  const lines = [
    `${slate?.draw_code || "Quiniela"} · ${slate?.label || "sin etiqueta"}`,
    `Modo: ${ticketModes.find((item) => item.key === state.ticketMode)?.label || state.ticketMode}`,
    "",
  ];
  state.matches.forEach((match) => {
    const decision = effectiveDecision(match);
    const issue = qualityIssueProfile(match);
    lines.push(
      `${match.position}. ${match.prediction.home_team_name} vs ${match.prediction.away_team_name}: ${displayPicks(decision.picks)} (${issue.label}${issue.score !== null ? `, calidad ${issue.score}/100` : ""})`
    );
  });
  const review = state.matches.filter((match) => qualityIssueProfile(match).review);
  if (review.length) {
    lines.push("", "Revisar:");
    review.forEach((match) => {
      const issue = qualityIssueProfile(match);
      lines.push(`- ${match.position}. ${issue.reasons.join("; ") || issue.label}`);
    });
  }
  return lines.join("\n");
}

function renderEvidenceFallback(featurePayload, evidenceCount) {
  const summaries = Array.isArray(featurePayload.evidence_summaries)
    ? featurePayload.evidence_summaries
    : [];
  if (summaries.length) {
    return summaries.slice(0, 5).map((item) => `
      <div class="evidence-item">
        <strong>${escapeHtml(item.title || item.source_title || "Fuente contextual")}</strong>
        <div>${escapeHtml(item.context_summary || item.summary || "Contexto enlazado al partido.")}</div>
        <div class="mono">confianza ${Math.round((item.confidence || 0) * 100)}%</div>
      </div>
    `).join("");
  }

  const linkedDocuments = Number(featurePayload.linked_documents || 0);
  const copy = evidenceCount || linkedDocuments
    ? `Hay ${evidenceCount || linkedDocuments} fuente(s) detectada(s), pero el detalle no esta disponible en este panel.`
    : "No hay una nota contextual verificada enlazada a este partido; el analisis usa historico, forma reciente y reglas de riesgo.";
  return renderEmpty(copy);
}

function renderResultFallback(featurePayload, match) {
  const h2hSummaries = Array.isArray(featurePayload.head_to_head_summaries)
    ? featurePayload.head_to_head_summaries
    : [];
  if (h2hSummaries.length) {
    return h2hSummaries.slice(0, 5).map((item) => `
      <div class="result-item">
        <strong>Antecedente directo</strong>
        <div>${escapeHtml(item.home_team_name || match.prediction.home_team_name)} vs ${escapeHtml(item.away_team_name || match.prediction.away_team_name)}</div>
        <div>${escapeHtml(item.home_goals)}-${escapeHtml(item.away_goals)}</div>
        <div class="mono">${escapeHtml(formatDate(item.played_at))}</div>
      </div>
    `).join("");
  }

  const recentCount = Number(featurePayload.recent_results_count || 0);
  if (recentCount > 0) {
    return `
      <div class="result-item">
        <strong>Forma reciente agregada</strong>
        <div>${escapeHtml(match.prediction.home_team_name)}: ${escapeHtml(featurePayload.home_recent_matches || 0)} partido(s), ${escapeHtml(featurePayload.home_recent_points || 0)} punto(s), balance ${escapeHtml(featurePayload.home_recent_goal_balance || 0)}</div>
        <div>${escapeHtml(match.prediction.away_team_name)}: ${escapeHtml(featurePayload.away_recent_matches || 0)} partido(s), ${escapeHtml(featurePayload.away_recent_points || 0)} punto(s), balance ${escapeHtml(featurePayload.away_recent_goal_balance || 0)}</div>
        <div class="mono">${escapeHtml(recentCount)} resultado(s) usados por el modelo</div>
      </div>
    `;
  }

  return renderEmpty("No hay marcadores recientes enlazados; el pronostico queda limitado a probabilidad base y politica de competencia.");
}

// dataQualityLabel, buildQualityTooltip, statusTone moved to
// helpers.js (S5.1) — see the import block at the top of this file.

// Renders the "Por qué revisar" block for low-confidence matches where the
// anchor condition fails due to insufficient recent results or H2H.
// Uses structured feature data — no string parsing of rationale text.
function renderLowConfidenceBlock(prediction, featurePayload) {
  // Product-first: show the "por qué revisar" block when the guardrailed tier
  // is low (final_status REVISAR), falling back to the raw band for old
  // responses. BLOQUEADO maps to "blocked", so it is handled elsewhere.
  if (!prediction || effectiveConfidenceTier(prediction) !== "low") return "";
  const homeRecent = Number(featurePayload.home_recent_matches || 0);
  const awayRecent = Number(featurePayload.away_recent_matches || 0);
  // The features endpoint returns `head_to_head_matches`; the quality
  // endpoint uses `head_to_head_results_count` — accept both.
  const h2hCount = Number(featurePayload.head_to_head_results_count || featurePayload.head_to_head_matches || 0);
  const needsAnchor = homeRecent < 3 || awayRecent < 3 || h2hCount < 2;
  if (!needsAnchor) return "";
  const bullets = [];
  if (homeRecent < 3) {
    bullets.push(`<li><strong>${escapeHtml(prediction.home_team_name)}:</strong> ${homeRecent} resultado(s) reciente(s) en ventana activa — necesita 3</li>`);
  }
  if (awayRecent < 3) {
    bullets.push(`<li><strong>${escapeHtml(prediction.away_team_name)}:</strong> ${awayRecent} resultado(s) reciente(s) en ventana activa — necesita 3</li>`);
  }
  if (h2hCount < 2) {
    bullets.push(`<li><strong>Historial directo:</strong> ${h2hCount} enfrentamiento(s) — necesita 2</li>`);
  }
  return `
    <section class="analysis-block anchor-gap-block">
      <h4>Por qué revisar</h4>
      <p class="anchor-gap-lead">El modelo elige un resultado, pero no lo trata como señal defendible porque falta evidencia reciente suficiente para anclar la confianza.</p>
      ${bullets.length ? `<ul class="anchor-gap-list">${bullets.join("")}</ul>` : ""}
      <p class="mini-copy">Las calificatorias recientes pueden quedar fuera de la ventana activa del modelo. Esto no es un error sino una limitación de cobertura temporal: el pronóstico es válido pero la confianza queda en baja hasta que haya más partidos disponibles.</p>
    </section>
  `;
}

// Renders the full backend rationale list in a collapsible tech-note section.
function renderModelRationale(prediction) {
  const lines = Array.isArray(prediction?.rationale) ? prediction.rationale : [];
  if (!lines.length) return "";
  return `
    <details class="analysis-block">
      <summary><h4>Nota técnica del modelo</h4></summary>
      <ul class="rationale-list">
        ${lines.map((line) => `<li>${escapeHtml(line)}</li>`).join("")}
      </ul>
    </details>
  `;
}

function renderProductionStatus() {
  const node = getById("ops-panel");
  if (!node) return;
  const activeSlate = currentSlate();
  const qualities = state.matches
    .map((match) => Number(match.quality?.quality_score))
    .filter((value) => Number.isFinite(value));
  const averageQuality = qualities.length
    ? Math.round(qualities.reduce((total, value) => total + value, 0) / qualities.length)
    : null;
  const blockedMatches = state.matches.filter((match) => qualityIssueProfile(match).blocked).length;
  const thinMatches = state.matches.filter((match) => match.quality?.quality_level === "thin").length;
  const authState = state.authenticated ? "activa" : "pendiente";
  const readyStatus = { ready: "listo", not_ready: "no listo" }[state.ready?.status] || state.ready?.status || "sin dato";
  const healthStatus = { ok: "ok", degraded: "degradado" }[state.health?.status] || state.health?.status || "sin dato";
  const schemaCopy = state.health
    ? `schema ${state.health.schema_version}${state.health.schema_up_to_date ? " al día" : " pendiente"}`
    : "schema sin dato";

  const apiOk = statusTone(state.health?.status) === "ok";
  const dbOk = statusTone(state.ready?.ready) === "ok";
  const overallOk = apiOk && dbOk && !blockedMatches;
  const dotClass = overallOk ? "ok" : (blockedMatches ? "bad" : "warn");
  const summaryLabel = overallOk ? "Sistema listo" : "Revisar estado";
  const summaryChips = [
    `<span class="ops-chip ${apiOk ? "ok" : "warn"}">API ${apiOk ? "OK" : escapeHtml(healthStatus)}</span>`,
    `<span class="ops-chip ${dbOk ? "ok" : "warn"}">BD ${dbOk ? "OK" : escapeHtml(readyStatus)}</span>`,
    `<span class="ops-chip ${blockedMatches ? "bad" : "ok"}">${blockedMatches} bloqueo${blockedMatches !== 1 ? "s" : ""}</span>`,
  ].join("");

  node.innerHTML = `
    <details class="ops-details" ${!overallOk ? "open" : ""}>
      <summary class="ops-summary" aria-label="${escapeHtml(summaryLabel)} — clic para ver detalles">
        <span class="ops-status-dot ${dotClass}"></span>
        <span class="ops-status-label">${escapeHtml(summaryLabel)}</span>
        <span class="ops-chips">${summaryChips}</span>
      </summary>
      <div class="ops-inner">
        <div class="ops-head">
          <div>
            <p class="ticket-label">Estado de producción</p>
            <h2>${escapeHtml(activeSlate?.draw_code || "Sin papeleta activa")}</h2>
            <p class="meta-copy">${escapeHtml(activeSlate?.label || "sin papeleta activa")}</p>
          </div>
          <span class="ops-badge ${state.authenticated ? "ok" : "warn"}">Auth ${escapeHtml(authState)}</span>
        </div>
        <div class="ops-grid">
          <div class="ops-item ${statusTone(healthStatus)}">
            <span>API</span>
            <strong>${escapeHtml(healthStatus)}</strong>
            <small>${escapeHtml(schemaCopy)}</small>
          </div>
          <div class="ops-item ${statusTone(state.ready?.ready)}">
            <span>Estado BD</span>
            <strong>${escapeHtml(readyStatus)}</strong>
            <small>DB ${state.ready?.database_ok ? "OK" : "sin confirmar"}</small>
          </div>
          <div class="ops-item warn">
            <span>Worker</span>
            <strong>interno</strong>
            <small>rutas HTTP cerradas</small>
          </div>
          <div class="ops-item quality-item ${averageQuality !== null && averageQuality >= 70 ? "ok" : averageQuality !== null && averageQuality >= 40 ? "warn" : "bad"}" title="${escapeHtml(buildQualityTooltip(state.matches))}">
            <span>Calidad</span>
            <strong>${averageQuality !== null ? `${averageQuality}/100` : "sin dato"}</strong>
            <small>${escapeHtml(thinMatches)} delgada(s)</small>
          </div>
          <div class="ops-item ${blockedMatches ? "warn" : "ok"}">
            <span>Bloqueos</span>
            <strong>${escapeHtml(blockedMatches)}</strong>
            <small>${escapeHtml(state.matches.length)} partido(s)</small>
          </div>
          <div class="ops-item">
            <span>Último refresh</span>
            <strong>${escapeHtml(activeSlate ? formatDate(activeSlate.created_at) : "sin dato")}</strong>
            <small>${escapeHtml(activeSlate?.label || "sin papeleta")}</small>
          </div>
        </div>
      </div>
    </details>
  `;
}

function renderDataQuality(match, featurePayload, evidenceCount, resultCount) {
  const quality = match.quality || {};
  const score = Number.isFinite(Number(quality.quality_score)) ? Number(quality.quality_score) : null;
  const missing = Array.isArray(quality.missing) ? quality.missing : [];
  const notes = Array.isArray(quality.notes) ? quality.notes : [];
  const level = quality.quality_level || (score !== null && score >= 70 ? "good" : score !== null && score >= 40 ? "partial" : "thin");
  const items = [
    `Calidad: ${dataQualityLabel(level)}${score !== null ? ` (${score}/100)` : ""}`,
    `Evidencia: ${quality.evidence_count ?? evidenceCount}`,
    `Forma reciente: ${quality.recent_results_count ?? resultCount}`,
    `Antecedentes H2H: ${quality.head_to_head_results_count ?? featurePayload.head_to_head_results_count ?? 0}`,
    `Disponibilidad: ${quality.availability_count ?? 0}`,
    `Referencia: ${readinessLabel(quality.competition_readiness || match.prediction.competition_readiness)}`,
  ];
  const missingCopy = missing.length
    ? `<p class="mini-copy">Falta reforzar: ${escapeHtml(missing.join(", "))}.</p>`
    : `<p class="mini-copy">Cobertura suficiente para explicar la jugada, manteniendo cautela por incertidumbre deportiva.</p>`;
  const notesCopy = notes.length
    ? `<p class="mini-copy">${notes.map(escapeHtml).join(" · ")}</p>`
    : "";
  return `
    <div class="signal-grid">${items.map((item) => `<span class="signal-pill">${escapeHtml(item)}</span>`).join("")}</div>
    ${missingCopy}
    ${notesCopy}
  `;
}

function buildDecisionReasons(match, featurePayload, validation, evidenceCount, resultCount) {
  const decision = effectiveDecision(match);
  const prediction = match.prediction;
  const sorted = sortedOutcomes(prediction);
  const reasons = [
    `El ticket marca ${displayPicks(decision.picks)} porque ${displayOutcome(sorted[0].key)} es el resultado con mayor probabilidad (${formatPercent(sorted[0].value)}).`,
    `La brecha contra la segunda opcion es de ${formatPercent(Math.abs(sorted[0].value - sorted[1].value))}; por eso la validacion lo clasifica como ${validation.label.toLowerCase()}.`,
  ];

  const h2hCount = Number(featurePayload.head_to_head_results_count || 0);
  if (h2hCount > 0) {
    reasons.push(`Hay ${h2hCount} antecedente(s) directo(s): puntos local ${featurePayload.head_to_head_home_points ?? 0}, puntos visita ${featurePayload.head_to_head_away_points ?? 0}, balance local ${featurePayload.head_to_head_goal_balance ?? 0}.`);
  }

  const recentCount = Number(featurePayload.recent_results_count || 0);
  if (recentCount > 0) {
    reasons.push(`Forma reciente: ${prediction.home_team_name} suma ${featurePayload.home_recent_points ?? 0} punto(s) en ${featurePayload.home_recent_matches ?? 0} partido(s); ${prediction.away_team_name} suma ${featurePayload.away_recent_points ?? 0} en ${featurePayload.away_recent_matches ?? 0}.`);
  }

  if (evidenceCount > 0) {
    reasons.push(`El partido tiene ${evidenceCount} evidencia(s) contextual(es) enlazada(s) que respaldan o condicionan la lectura.`);
  } else {
    reasons.push("No hay evidencia contextual directa suficiente; la recomendacion debe tomarse como lectura estadistica y no como pick blindado.");
  }

  if (resultCount <= 0 && recentCount <= 0 && h2hCount <= 0) {
    reasons.push("No se encontraron resultados ligados, asi que aumenta la incertidumbre del pronostico.");
  }

  if (prediction.policy_reason) {
    reasons.push(prediction.policy_reason);
  }

  return reasons;
}

function buildAnalysis(match) {
  if (!match) return renderEmpty("Selecciona un partido para ver la explicación.");

  const featurePayload = match.features?.payload || {};
  const decision = effectiveDecision(match);
  const validation = validationProfile(match);
  const drawRisk = drawRiskFor(match);
  const evidenceList = Array.isArray(match.evidence) ? match.evidence : [];
  const availabilityList = Array.isArray(match.availability) ? match.availability : [];
  const resultList = Array.isArray(match.results) ? match.results : [];
  const evidenceCount = linkedEvidenceCount(match);
  const resultCount = Math.max(
    resultList.length,
    Number(featurePayload.recent_results_count || 0),
    Number(featurePayload.head_to_head_results_count || 0)
  );
  const decisionReasons = buildDecisionReasons(match, featurePayload, validation, evidenceCount, resultCount);
  // Soften "bad" (red) → "warn" (amber) for low-confidence predictions that
  // are not fully blocked — data-gap matches are cautionary, not catastrophic.
  const heroTone = (() => {
    const baseTone = { low: "ok", medium: "warn", high: "bad" }[validation.level] || "ok";
    // Soften red→amber for low-tier (data-gap) matches — product-first, legacy
    // band only as fallback inside effectiveConfidenceTier.
    if (baseTone === "bad" && effectiveConfidenceTier(match.prediction) === "low") return "warn";
    return baseTone;
  })();
  const evidenceMarkup = evidenceList.length
    ? evidenceList.slice(0, 5).map((item) => {
      const title = item.source_title || item.kind;
      const detail = item.context_summary || item.summary;
      return `
        <div class="evidence-item">
          <strong>${escapeHtml(title)}</strong>
          <div>${escapeHtml(detail)}</div>
          <div class="mono">confianza ${Math.round((item.confidence || 0) * 100)}%</div>
        </div>
      `;
    }).join("")
    : renderEvidenceFallback(featurePayload, evidenceCount);

  const availabilityMarkup = availabilityList.length
    ? availabilityList.slice(0, 6).map((item) => `
        <span class="signal-pill">
          ${escapeHtml(item.team_name || "Equipo")} · ${escapeHtml(item.player_name)}:
          ${escapeHtml(availabilityStatusLabel(item.status))}
          (${escapeHtml(availabilityCategoryLabel(item.category))}, impacto ${Math.round((item.impact_score || 0) * 100)}%)
          ${item.detail ? ` · ${escapeHtml(item.detail)}` : ""}
        </span>
      `).join("")
    : `
        <span class="signal-pill">Sin bajas, suspendidos o amonestados confirmados por fuente verificable</span>
        <span class="signal-pill">Fuentes enlazadas: ${escapeHtml(featurePayload.evidence_items ?? evidenceList.length ?? 0)}</span>
        <span class="signal-pill">Lesiones: ${escapeHtml(featurePayload.injury_signal_total ?? 0)}</span>
        <span class="signal-pill">Suspensiones: ${escapeHtml(featurePayload.suspension_signal_total ?? 0)}</span>
        <span class="signal-pill">Rotación/alineación: ${escapeHtml(featurePayload.rotation_signal_total ?? 0)}</span>
      `;

  const resultMarkup = resultList.length
    ? resultList.slice(0, 5).map((item) => `
        <div class="result-item">
          <strong>${escapeHtml(item.context_label || (item.is_head_to_head ? "Antecedente directo" : "Forma reciente"))}: ${escapeHtml(displayOutcome(item.result_code))}</strong>
          <div>${escapeHtml(item.home_team_name || "Local")} vs ${escapeHtml(item.away_team_name || "Visitante")}</div>
          <div>${escapeHtml(item.home_goals)}-${escapeHtml(item.away_goals)}</div>
          <div class="mono">${escapeHtml(translateCompetition(item.competition_name || ""))} · ${escapeHtml(formatDate(item.played_at))}</div>
        </div>
      `).join("")
    : renderResultFallback(featurePayload, match);

  const signalItems = [
    `Puntos local: ${featurePayload.home_recent_points ?? 0}`,
    `Puntos visita: ${featurePayload.away_recent_points ?? 0}`,
    `Balance local: ${featurePayload.home_recent_goal_balance ?? 0}`,
    `Balance visita: ${featurePayload.away_recent_goal_balance ?? 0}`,
    `Descanso local: ${featurePayload.home_days_rest ?? 0}d`,
    `Descanso visita: ${featurePayload.away_days_rest ?? 0}d`,
    `Antecedentes directos: ${featurePayload.head_to_head_results_count ?? 0}`,
    `Puntos H2H local: ${featurePayload.head_to_head_home_points ?? 0}`,
    `Puntos H2H visita: ${featurePayload.head_to_head_away_points ?? 0}`,
    `Balance H2H local: ${featurePayload.head_to_head_goal_balance ?? 0}`,
    `Lesiones local: ${featurePayload.home_injury_signals ?? 0}`,
    `Lesiones visita: ${featurePayload.away_injury_signals ?? 0}`,
    `Suspensiones local: ${featurePayload.home_suspension_signals ?? 0}`,
    `Suspensiones visita: ${featurePayload.away_suspension_signals ?? 0}`,
  ];

  return `
    <div class="analysis-card">
      <div class="analysis-head">
        <h3>${escapeHtml(match.prediction.home_team_name)} vs ${escapeHtml(match.prediction.away_team_name)} ${renderDrawChips(drawRisk)}</h3>
        ${(() => {
          const g = presentationGuardOf(match.prediction);
          if (decision.source === "manual") {
            return `<p class="meta-copy">Jugada: <strong>${escapeHtml(displayPicks(decision.picks))}</strong> · ajustada manualmente</p>`;
          }
          if (g.simple_allowed) {
            return `<p class="meta-copy">Jugada: <strong>${escapeHtml(displayPicks(decision.picks))}</strong> · sugerida por el modelo</p>`;
          }
          const signal = SIGNAL_LABEL[g.primary_signal] || g.primary_signal || "—";
          return `<p class="meta-copy meta-copy-warn" data-not-simple="true">Señal principal: <strong>${escapeHtml(signal)}</strong> · Recomendación: <strong>${escapeHtml(g.recommendation_label)}</strong> · requiere cobertura / revisión</p>`;
        })()}
      </div>
      ${(() => {
        const pred = match.prediction;
        const basePick = basePickBadge(pred.recommended_outcome);
        const strategy = resolveTicketStrategy({
          prediction: pred,
          validationLevel: validation.level,
          decisionType: decision.type,
        });
        const riskLevel = pred.risk_level || "high";
        const visibleConf = pred.visible_confidence || "baja";
        const reasons = (Array.isArray(pred.confidence_explanation) && pred.confidence_explanation.length
          ? pred.confidence_explanation
          : (Array.isArray(pred.flags) ? pred.flags.map(flagLabel) : [])).slice(0, 3);
        const probBars = [
          ["L", pred.home_probability],
          ["E", pred.draw_probability],
          ["V", pred.away_probability],
        ].map(([k, v]) => {
          const pctNum = Math.round((Number(v) || 0) * 100);
          const isPick = basePick.letter === k;
          return `<div class="prob-bar${isPick ? " is-pick" : ""}"><span class="prob-bar-label">${k}</span><span class="prob-bar-track"><span class="prob-bar-fill ${probBarWidthClass(pctNum)}"></span></span><span class="prob-bar-value">${pctNum}%</span></div>`;
        }).join("");
        return `
      <div class="decision-hero tone-${heroTone}">
        <div class="dh-summary">
          <div class="dh-line"><span class="dh-key">Señal base</span><span class="badge-signal">${escapeHtml(basePick.label)}</span></div>
          <div class="dh-line"><span class="dh-key">Estrategia</span><span class="badge-strategy tone-${strategy.tone}">${escapeHtml(strategy.label)}</span></div>
          <div class="dh-line"><span class="dh-key">Riesgo</span><span class="badge-risk tone-${riskTone(riskLevel)}">${escapeHtml(riskLevelLabel(riskLevel))}</span></div>
          <div class="dh-line"><span class="dh-key">Confianza</span><span class="conf-tag tone-${headlineConfidence(pred).tone}">${escapeHtml(headlineConfidence(pred).label)}</span></div>
        </div>
      </div>
      <div class="analysis-grid">
        <section class="analysis-block">
          <h4>Probabilidades</h4>
          <div class="prob-bars">${probBars}</div>
        </section>
        ${renderDrawCoverageBlock(drawRisk)}
        <section class="analysis-block">
          <h4>Por qué</h4>
          ${reasons.length
            ? `<ul class="why-list">${reasons.map((r) => `<li>${escapeHtml(r)}</li>`).join("")}</ul>`
            : `<p class="mini-copy">Sin señales de riesgo destacadas; el modelo no marcó flags para este partido.</p>`}
        </section>
        <section class="analysis-block">
          <h4>Acción recomendada</h4>
          <p class="action-copy"><strong>${escapeHtml(strategy.label)}.</strong> ${escapeHtml(validation.recommendation)}</p>
        </section>
        <details class="analysis-block tech-accordion">
          <summary>Detalles técnicos</summary>
          <div class="facts-grid">
            <div class="fact"><strong>raw L/E/V</strong><span>${fmtVec(pred.raw_probabilities)}</span></div>
            <div class="fact"><strong>display L/E/V</strong><span>${fmtVec(pred.display_probabilities || pred.probabilities)}</span></div>
            <div class="fact"><strong>decisión L/E/V</strong><span>${fmtVec(pred.decision_probabilities || pred.probabilities)}</span></div>
            <div class="fact"><strong>optimizer L/E/V</strong><span>${fmtVec(pred.decision_probabilities || pred.probabilities)}</span></div>
            <div class="fact"><strong>Banda modelo</strong><span>${escapeHtml(confidenceLabel(pred.confidence_band))}</span></div>
            <div class="fact"><strong>Referencia</strong><span>${escapeHtml(readinessLabel(pred.competition_readiness))}</span></div>
            <div class="fact"><strong>Estado</strong><span>${escapeHtml(decisionStatusLabel(pred.final_status))}</span></div>
            <div class="fact"><strong>Fallback</strong><span>${pred.fallback_used ? "sí" : "no"}</span></div>
          </div>
          <p class="mono tech-flags-line">flags: ${(Array.isArray(pred.flags) && pred.flags.length ? pred.flags.map(escapeHtml).join(", ") : "ninguno")}</p>
          <p class="mono tech-flags-line">brecha top2 ${escapeHtml(displayOutcome(validation.profile.bestOutcome))}/${escapeHtml(displayOutcome(validation.profile.secondOutcome))} · ${formatPercent(validation.profile.topGap)} · evidencia ${escapeHtml(evidenceCount)} · incertidumbre ${Math.round(validation.profile.entropy * 100)}%</p>
        </details>`;
      })()}
        ${renderLowConfidenceBlock(match.prediction, featurePayload)}
        <section class="analysis-block">
          <h4>Análisis de la decisión</h4>
          <div>${decisionReasons.map((item) => `<p>${escapeHtml(item)}</p>`).join("")}</div>
        </section>
        <section class="analysis-block">
          <h4>Disponibilidad de jugadores</h4>
          <div class="signal-grid">${availabilityMarkup}</div>
        </section>
        <section class="analysis-block">
          <h4>Evidencia contextual</h4>
          ${evidenceMarkup}
        </section>
        <section class="analysis-block">
          <h4>Historial de resultados</h4>
          ${resultMarkup}
        </section>
        <details class="analysis-block">
          <summary><h4>Calidad de datos</h4></summary>
          ${renderDataQuality(match, featurePayload, evidenceCount, resultCount)}
        </details>
        <details class="analysis-block">
          <summary><h4>Datos estadísticos del modelo</h4></summary>
          <div class="signal-grid">${signalItems.map((item) => `<span class="signal-pill">${escapeHtml(item)}</span>`).join("")}</div>
        </details>
        ${renderModelRationale(match.prediction)}
      </div>
    </div>
  `;
}

function renderSidebar() {
  const errorNode = getById("global-error");
  if (errorNode) {
    errorNode.innerHTML = state.lastError ? `<div class="empty-state alert-state">${escapeHtml(state.lastError)}</div>` : "";
  }
  const asideSubtitleNode = getById("aside-subtitle");
  const asideSlatesNode = getById("aside-slates");
  const menuNode = getById("match-menu");
  if (!menuNode) return;
  const mode = ticketModes.find((item) => item.key === state.ticketMode);
  if (asideSubtitleNode) {
    asideSubtitleNode.textContent = mode ? mode.label : "Lista rápida de la papeleta activa";
  }
  if (asideSlatesNode) {
    asideSlatesNode.innerHTML = renderAsideSlates();
  }
  if (!state.authenticated) {
    menuNode.innerHTML = `
      <div class="empty-state">
        <p>Inicia sesión para ver la boleta activa.</p>
        <p class="mini-copy">El dashboard carga los datos privados después de autenticar la sesión.</p>
      </div>
    `;
    return;
  }
  const visibleMatches = filteredMatches();
  menuNode.innerHTML = visibleMatches.length
    ? visibleMatches.map(buildMatchMenuItem).join("")
    : `
      <div class="empty-state">
        <p>No hay partidos para este filtro.</p>
        <p class="mini-copy">Cambia el filtro de calidad para ver más partidos.</p>
        ${state.health?.environment !== "production" ? `<button id="empty-load-demo" class="ghost-button">Cargar demo</button>` : ""}
      </div>
    `;
}

function renderCanaryBadge(pred) {
  const c = pred && pred.canary;
  if (!c || !c.active) return "";
  const delta = typeof c.max_abs_delta === "number" ? c.max_abs_delta.toFixed(3) : "";
  return `<span class="badge-canary" title="Team Rating Canary activo · Δ prob máx ${escapeHtml(delta)} · El ticket aún NO usa canary (motor: ${escapeHtml(c.engine || "")})">CANARY</span>`;
}

// R6.3: a deferred diagnostic panel shows a lightweight skeleton while its
// (lazy) payload is still loading, instead of an empty/"sin datos" state.
function _diagBody(id, renderFn, data) {
  const node = getById(id);
  if (!node) return;
  if (state.diagnosticsLoading && !data) {
    node.innerHTML = `<div class="panel-skeleton"><div class="skeleton-block"></div><div class="skeleton-block short"></div></div>`;
    return;
  }
  node.innerHTML = renderFn(data);
}

function renderTeamRatingShadow() {
  _diagBody("team-rating-shadow-body", renderTeamRatingShadowPanel, state.teamRatingShadow);
}

function renderTeamRatingCanary() {
  _diagBody("team-rating-canary-body", renderTeamRatingCanaryPanel, state.teamRatingCanary);
}

function renderTicketCanaryDryRun() {
  _diagBody("ticket-canary-dry-run-body", renderTicketCanaryDryRunPanel, state.ticketCanaryDryRun);
}

function renderMoneyMode() {
  _diagBody("money-mode-body", renderMoneyModePanel, state.moneyMode);
}

function renderOperationalMoneyModeStatus() {
  _diagBody("operational-money-mode-status-body", renderOperationalMoneyModeStatusPanel, state.moneyModeOpsStatus);
}

function renderExternalResults() {
  _diagBody("external-results-body", renderExternalResultsPanel, state.externalResults);
}

function renderSlateOptions() {
  _diagBody("slate-options-body", renderSlateOptionsPanel, state.slateOptions);
}

function renderTrackingResultsValidation() {
  _diagBody("tracking-results-validation-body", renderTrackingResultsValidationPanel, state.resultsValidation);
}

function renderTeamRatingDryRun() {
  _diagBody("team-rating-dry-run-body", renderTeamRatingActivationDryRunPanel, state.teamRatingDryRun);
}

function renderTeamRatingReadiness() {
  _diagBody("team-rating-readiness-body", renderTeamRatingActivationReadinessPanel, state.teamRatingReadiness);
}

// Render only the deferred Diagnóstico panels (used when their lazy payload
// arrives), without re-rendering the whole prediction board.
function renderDiagnosticsPanels() {
  renderOperationalMoneyModeStatus();
  renderMoneyMode();
  renderSlateOptions();
  renderTrackingResultsValidation();
  renderTicketCanaryDryRun();
  renderExternalResults();
  renderTeamRatingShadow();
  renderTeamRatingDryRun();
  renderTeamRatingReadiness();
  renderTeamRatingCanary();
}

function renderBoard() {
  const labelNode = getById("ticket-label");
  const codeNode = getById("ticket-code");
  const summaryNode = getById("ticket-summary");
  const tabsNode = getById("ticket-tabs");
  const slateSwitcherNode = getById("slate-switcher");
  const gridNode = getById("ticket-grid");
  const analysisNode = getById("analysis");
  const validationSummaryNode = getById("validation-summary");
  const qualityFilterNode = getById("quality-filter");
  const activeSlate = currentSlate();
  renderProductionStatus();
  renderDiagnosticsPanels();
  if (qualityFilterNode) qualityFilterNode.innerHTML = renderQualityFilterOptions();

  if (!state.authenticated) {
    if (labelNode) labelNode.textContent = "Acceso requerido";
    if (codeNode) codeNode.textContent = "Inicia sesión";
    if (slateSwitcherNode) slateSwitcherNode.innerHTML = "";
    if (tabsNode) tabsNode.innerHTML = renderTicketTabs();
    if (summaryNode) summaryNode.innerHTML = renderEmpty("Ingresa el password para cargar la boleta y sus predicciones.");
    if (validationSummaryNode) validationSummaryNode.innerHTML = "";
    if (gridNode) gridNode.innerHTML = renderEmpty("Los partidos aparecerán aquí cuando la sesión esté activa.");
    if (analysisNode) analysisNode.innerHTML = renderEmpty("La explicación del modelo se desbloquea después del login.");
    return;
  }

  if (state.isLoading) {
    if (labelNode) labelNode.textContent = "Cargando";
    if (codeNode) codeNode.textContent = "Preparando quiniela";
    if (summaryNode) summaryNode.innerHTML = renderEmpty("Consultando API, predicciones y evidencias.");
    if (validationSummaryNode) validationSummaryNode.innerHTML = "";
    if (gridNode) gridNode.innerHTML = renderLoadingRows();
    if (analysisNode) analysisNode.innerHTML = renderEmpty("Cargando análisis del partido…");
    return;
  }

  if (!activeSlate || !state.matches.length) {
    const hasSlatePicked = Boolean(activeSlate);
    const slateCode = activeSlate?.draw_code || "";
    const slateId = activeSlate?.id || "";
    if (labelNode) labelNode.textContent = hasSlatePicked ? escapeHtml(slateCode) : "Sin quiniela activa";
    if (codeNode) codeNode.textContent = hasSlatePicked ? "Sin predicciones" : "Carga una papeleta";
    if (slateSwitcherNode) slateSwitcherNode.innerHTML = hasSlatePicked ? renderSlateSwitcher() : "";
    if (tabsNode) tabsNode.innerHTML = renderTicketTabs();
    if (validationSummaryNode) validationSummaryNode.innerHTML = "";
    const readOnlyPicked = hasSlatePicked && Boolean(activeSlate?.read_only);
    const noPredCopy = hasSlatePicked
      ? `Esta boleta (${escapeHtml(slateCode)}) no tiene predicciones generadas aún.`
      : "El sistema cargará la quiniela activa en la próxima ejecución.";
    // No generate/reset action on a closed/archived (read-only) slate.
    const generateBtn = hasSlatePicked && !readOnlyPicked
      ? `<button class="primary-button generate-cta" id="generate-predictions-btn" data-slate-id="${escapeHtml(slateId)}">Generar predicción</button>`
      : "";
    if (summaryNode) summaryNode.innerHTML = `<div class="empty-state">${noPredCopy}${generateBtn}</div>`;
    if (gridNode) {
      if (hasSlatePicked) {
        gridNode.innerHTML = renderEmpty("Sin predicciones. Usa el botón para generarlas.");
      } else {
        gridNode.innerHTML = renderNoSlateState();
      }
    }
    if (analysisNode) analysisNode.innerHTML = renderEmpty("Selecciona un partido para ver la explicación del modelo.");
    return;
  }

  if (labelNode) labelNode.textContent = activeSlate.label;
  if (codeNode) codeNode.textContent = activeSlate.draw_code;
  if (slateSwitcherNode) slateSwitcherNode.innerHTML = renderSlateSwitcher();
  if (tabsNode) tabsNode.innerHTML = renderTicketTabs();
  if (summaryNode) {
    const allIssues = state.matches.map((m) => {
      const decision = effectiveDecision(m);
      return {
        issue: qualityIssueProfile(m),
        decision,
        strategy: resolveTicketStrategy({
          prediction: m.prediction,
          validationLevel: validationProfile(m).level,
          decisionType: decision.type,
        }),
      };
    });
    const fixedCount = allIssues.filter((i) => i.decision.type === "fixed").length;
    const doublesCount = allIssues.filter((i) => i.decision.type === "double").length;
    const triplesCount = allIssues.filter((i) => i.decision.type === "triple").length;
    // PRODUCT-field buckets (not confidence_band): a "defendible" is a match
    // whose backend ticket_strategy is SIMPLE; review/caution/blocked come
    // from final_status via qualityIssueProfile.
    const defensibleCount = allIssues.filter((i) => i.strategy.key === "SIMPLE").length;
    const reviewCount = allIssues.filter((i) => i.issue.review).length;
    const cautionCount = allIssues.filter((i) => i.issue.caution).length;
    const blockedCount = allIssues.filter((i) => i.issue.blocked).length;
    const weekTypeLabel = { weekend: "Fin de semana", revancha: "Revancha", midweek: "Media semana" }[activeSlate.week_type] || activeSlate.week_type;
    const modeLabel = ticketModes.find((m) => m.key === state.ticketMode)?.label || state.ticketMode;
    const heroChips = [];
    if (blockedCount) heroChips.push(`<span class="hero-chip bad">${blockedCount} sin datos</span>`);
    if (reviewCount) heroChips.push(`<span class="hero-chip bad">${reviewCount} a revisar</span>`);
    if (cautionCount) heroChips.push(`<span class="hero-chip warn">${cautionCount} con cautela</span>`);
    if (defensibleCount) heroChips.push(`<span class="hero-chip ok">${defensibleCount} defendible${defensibleCount !== 1 ? "s" : ""}</span>`);
    if (!blockedCount && !reviewCount && !cautionCount && !defensibleCount) heroChips.push(`<span class="hero-chip ok">Jugada lista</span>`);
    if (doublesCount) heroChips.push(`<span class="hero-chip">${doublesCount} doble${doublesCount !== 1 ? "s" : ""}</span>`);
    if (triplesCount) heroChips.push(`<span class="hero-chip">${triplesCount} triple${triplesCount !== 1 ? "s" : ""}</span>`);
    summaryNode.innerHTML = `
      <div class="ticket-hero">
        <div class="ticket-hero-main">${fixedCount}S · ${doublesCount}D · ${triplesCount}T</div>
        <div class="ticket-hero-meta">${escapeHtml(weekTypeLabel)} · ${state.matches.length} partidos · ${escapeHtml(modeLabel)}</div>
        <div class="ticket-hero-chips">${heroChips.join("")}</div>
      </div>
    `;
  }

  if (validationSummaryNode) {
    validationSummaryNode.innerHTML = renderValidationSummary();
  }

  if (gridNode) {
    const visibleMatches = filteredMatches();
    gridNode.innerHTML = visibleMatches.length
      ? visibleMatches.map(buildMatchCard).join("")
      : renderEmpty("No hay partidos que coincidan con el filtro activo.");
  }

  const visibleMatches = filteredMatches();
  const selected =
    visibleMatches.find((item) => item.match_id === state.selectedMatchId) ||
    visibleMatches[0] ||
    state.matches[0];
  if (analysisNode) analysisNode.innerHTML = buildAnalysis(selected);
}

// S4.2: event delegation. The previous attachEvents re-queried every
// data-attribute node on every render and bound a fresh listener per
// node — innerHTML re-renders destroyed those nodes so JS could GC
// the handlers, but the rebind cost was O(N) per render and the
// pattern made it easy to forget to call attachEvents() after a
// surgical render. A single document-level listener catches every
// click and dispatches by data-attribute, so no per-render bookkeeping
// is needed at all.
let _eventsAttached = false;
// Monotonic token so a late slate-detail fetch can't overwrite a newer switch.
let slateRequestSeq = 0;

const SELECTED_SLATE_KEY = "proai.selectedSlateId";
function readSavedSlateId() {
  try {
    return typeof localStorage !== "undefined" ? localStorage.getItem(SELECTED_SLATE_KEY) : null;
  } catch (_e) {
    return null;
  }
}
function saveSelectedSlateId(slateId) {
  try {
    if (typeof localStorage !== "undefined" && slateId) localStorage.setItem(SELECTED_SLATE_KEY, slateId);
  } catch (_e) {
    /* storage unavailable (private mode) — selection still holds in memory */
  }
}

async function _handleDelegatedClick(event) {
  const target = event.target instanceof Element ? event.target : null;
  if (!target) return;

  // Per-option pick has highest priority: it lives inside a match
  // card and we must stopPropagation before the card handler fires.
  const optionPick = target.closest("[data-option-pick]");
  if (optionPick) {
    event.stopPropagation();
    const option = optionPick.getAttribute("data-option-pick");
    const matchId = optionPick.getAttribute("data-option-match");
    if (option && matchId) {
      setManualSelection(matchId, option);
      state.selectedMatchId = matchId;
      renderSidebar();
      renderBoard();
    }
    return;
  }

  const slateNode = target.closest("[data-slate-id]");
  if (slateNode) {
    const slateId = slateNode.getAttribute("data-slate-id");
    if (slateId && slateId !== state.activeSlateId) {
      // Manual selection is authoritative. Switch immediately to a loading
      // state for the chosen slate (no waiting on ticket/quality/diagnostics),
      // then fill in details when they arrive.
      state.activeSlateId = slateId;
      saveSelectedSlateId(slateId);
      state.selectedMatchId = null;
      state.matches = [];
      state.ticketPlan = null;
      state.isLoading = true;
      renderSidebar();
      renderBoard();
      await loadSlateDetails(slateId);
      // Ignore if another switch happened while loading (stale guard handled it).
      if (state.activeSlateId === slateId) {
        state.isLoading = false;
        renderSidebar();
        renderBoard();
        // If the operator is on Diagnóstico, refresh its panels for the new
        // slate (deferred + cached); otherwise they load when the tab opens.
        if (_isDiagnosticoActive()) loadSlateDiagnostics(slateId);
      }
    }
    return;
  }

  const modeNode = target.closest("[data-ticket-mode]");
  if (modeNode) {
    const mode = modeNode.getAttribute("data-ticket-mode");
    if (mode && ticketModes.some((item) => item.key === mode)) {
      state.ticketMode = mode;
      resetManualSelections();
      renderSidebar();
      renderBoard();
    }
    return;
  }

  const menuNode = target.closest("[data-match-menu-id]");
  if (menuNode) {
    const matchId = menuNode.getAttribute("data-match-menu-id");
    if (matchId) {
      state.selectedMatchId = matchId;
      renderSidebar();
      renderBoard();
    }
    return;
  }

  // The per-card technical accordion lives inside the card. Let the native
  // <details> toggle happen without selecting the card or swapping the
  // right panel — bail before the card-selection branch.
  if (isTechAccordionTarget(target)) {
    return;
  }

  const cardNode = target.closest("[data-match-card]");
  if (cardNode) {
    const matchId = cardNode.getAttribute("data-match-card");
    if (matchId) {
      state.selectedMatchId = matchId;
      renderSidebar();
      renderBoard();
      getById("analysis")?.scrollIntoView({ behavior: "smooth", block: "start" });
    }
    return;
  }

  if (target.closest("#empty-load-demo")) {
    const created = await createDemoSlate();
    if (created) {
      await boot();
    }
    return;
  }

  const genBtn = target.closest("#generate-predictions-btn");
  if (genBtn) {
    const slateId = genBtn.getAttribute("data-slate-id");
    if (slateId) {
      genBtn.disabled = true;
      genBtn.textContent = "Generando…";
      await safePost(`/predictions/slates/${slateId}/refresh`);
      await loadSlateDetails(slateId);
      renderSidebar();
      renderBoard();
    }
    return;
  }
}

function attachEvents() {
  if (_eventsAttached) return;
  document.addEventListener("click", _handleDelegatedClick);
  document.addEventListener("keydown", (event) => {
    if (event.key !== "Enter" && event.key !== " ") return;
    const card = event.target.closest("[data-match-card]");
    if (!card) return;
    event.preventDefault();
    card.click();
  });
  _eventsAttached = true;
}

// Top-level view tabs: Predicción actual | Seguimiento | Aprendizaje |
// Diagnóstico. Pure visibility toggling — switching tabs never refetches or
// re-renders the prediction board, so a failure inside Seguimiento (its own
// isolated module) can never break "Predicción actual".
function activateView(view) {
  document.querySelectorAll(".main-tab").forEach((tab) => {
    const active = tab.dataset.view === view;
    tab.classList.toggle("is-active", active);
    tab.setAttribute("aria-selected", active ? "true" : "false");
  });
  document.querySelectorAll(".view").forEach((node) => {
    node.hidden = node.dataset.view !== view;
  });
  if (view === "aprendizaje") {
    loadLearningDashboard();
    loadLearningSummary();
  }
  // R6.3: the heavy Diagnóstico panels load only when this tab is opened, and
  // only if not already loaded for the active slate (cache makes re-open free).
  if (view === "diagnostico" && state.activeSlateId && state.diagnosticsSlateId !== state.activeSlateId) {
    loadSlateDiagnostics(state.activeSlateId);
  }
}

function _isDiagnosticoActive() {
  const node = document.querySelector('.view[data-view="diagnostico"]');
  return Boolean(node) && !node.hidden;
}

function setupMainTabs() {
  document.querySelectorAll(".main-tab").forEach((tab) => {
    tab.addEventListener("click", () => activateView(tab.dataset.view));
  });
}

let learningDashboardLoaded = false;
async function loadLearningDashboard() {
  if (learningDashboardLoaded) return;
  learningDashboardLoaded = true;
  const body = getById("learning-dashboard-body");
  if (!body) return;
  try {
    const [inventory, readiness] = await Promise.all([
      safeFetch("/learning/completed-slates/inventory", { optional: true }),
      safeFetch("/learning/dataset-readiness", { optional: true }),
    ]);
    if (!inventory) return; // keep the honest "cargando…" placeholder on any non-OK response
    body.innerHTML = renderLearningDashboard(inventory, readiness || null);
  } catch (_err) {
    // Leave the placeholder; the learning dashboard is best-effort and read-only.
  }
}

let learningSummaryLoaded = false;
async function loadLearningSummary() {
  if (learningSummaryLoaded) return;
  learningSummaryLoaded = true;
  const body = getById("learning-body");
  if (!body) return;
  try {
    const summary = await safeFetch("/adaptive-dataset/summary", { optional: true });
    if (!summary) return; // keep the honest placeholder on any non-OK response
    const rows = Number(summary.total_rows || 0);
    if (rows <= 0) {
      body.innerHTML = `<p class="meta-copy">Disponible cuando haya learning rows suficientes. Los resultados oficiales de Progol son solo-signo (sin marcador) y no aportan filas de entrenamiento; el aprendizaje necesita marcadores de una fuente deportiva. (${escapeHtml(summary.total_slates_scored || 0)} jornadas scoreadas, ${escapeHtml(summary.total_slates_complete || 0)} completas.)</p>`;
      return;
    }
    const hitRate = summary.hit_rate == null ? "—" : `${Math.round(Number(summary.hit_rate) * 100)}%`;
    body.innerHTML = `
      <div class="learn-summary">
        <div class="ls-cell"><strong>${escapeHtml(rows)}</strong><span>learning rows</span></div>
        <div class="ls-cell"><strong>${escapeHtml(summary.rows_with_canonical_result || 0)}</strong><span>con resultado canónico</span></div>
        <div class="ls-cell"><strong>${escapeHtml(summary.rows_with_conflict || 0)}</strong><span>en conflicto (excluidas)</span></div>
        <div class="ls-cell"><strong>${hitRate}</strong><span>hit rate</span></div>
      </div>
      <p class="meta-copy subtle">Solo lectura. No se entrena ni se promueve ningún modelo desde esta vista.</p>`;
  } catch (err) {
    // Best-effort: leave the honest placeholder in place.
  }
}

function attachStaticEvents() {
  setupMainTabs();
  getById("login-form")?.addEventListener("submit", async (event) => {
    event.preventDefault();
    const passwordInput = getById("auth-password");
    const password = passwordInput?.value || "";
    if (!password) return;
    state.authStatusMessage = "Validando password.";
    state.lastError = null;
    updateAuthControls();
    const loggedIn = await loginWithPassword(password);
    if (loggedIn && passwordInput) {
      passwordInput.value = "";
    }
    updateAuthControls();
    await boot();
  });

  getById("logout-button")?.addEventListener("click", async () => {
    await logoutSession();
    state.slates = [];
    state.matches = [];
    state.ticketPlan = null;
    state.lastError = null;
    updateAuthControls();
    await boot();
  });

  getById("refresh")?.addEventListener("click", () => {
    boot();
  });

  getById("load-demo")?.addEventListener("click", async () => {
    const created = await createDemoSlate();
    if (created) {
      await boot();
    }
  });

  getById("run-worker")?.addEventListener("click", async () => {
    await safePost("/worker/scheduler/run-once");
    await boot();
  });

  getById("reset-picks")?.addEventListener("click", () => {
    resetManualSelections();
    renderSidebar();
    renderBoard();
    attachEvents();
  });

  getById("quality-filter")?.addEventListener("change", (event) => {
    state.qualityFilter = event.target.value;
    const visible = filteredMatches();
    if (visible.length && !visible.some((match) => match.match_id === state.selectedMatchId)) {
      state.selectedMatchId = visible[0].match_id;
    }
    renderSidebar();
    renderBoard();
    attachEvents();
  });

  getById("copy-ticket")?.addEventListener("click", async () => {
    const text = buildTicketText();
    try {
      await navigator.clipboard.writeText(text);
      state.authStatusMessage = "Jugada copiada al portapapeles.";
    } catch {
      state.authStatusMessage = "No se pudo copiar automáticamente; descarga el TXT.";
    }
    updateAuthControls();
    renderProductionStatus();
  });

  getById("download-ticket")?.addEventListener("click", () => {
    const slate = currentSlate();
    const blob = new Blob([buildTicketText()], {type: "text/plain;charset=utf-8"});
    const url = URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.href = url;
    link.download = `${slate?.draw_code || "proai-ticket"}-${state.ticketMode}.txt`;
    link.click();
    URL.revokeObjectURL(url);
  });

  updateAuthControls();
}

function updateAuthControls() {
  const loginButton = getById("login-button");
  const logoutButton = getById("logout-button");
  const passwordInput = getById("auth-password");
  const refreshButton = getById("refresh");
  const demoButton = getById("load-demo");
  const workerButton = getById("run-worker");
  const copyButton = getById("copy-ticket");
  const downloadButton = getById("download-ticket");
  const resetButton = getById("reset-picks");
  const filterNode = getById("quality-filter");
  const feedbackNode = getById("auth-feedback");
  const secretField = passwordInput?.closest(".secret-field");
  if (loginButton) {
    loginButton.disabled = state.authenticated || state.isLoading;
    loginButton.hidden = Boolean(state.authenticated);
    loginButton.textContent = state.isLoading && !state.authenticated ? "Entrando" : "Entrar";
  }
  if (secretField) secretField.hidden = Boolean(state.authenticated);
  if (logoutButton) logoutButton.disabled = !state.authenticated;
  if (passwordInput) passwordInput.disabled = state.authenticated;
  // Read-only slate (closed/archived): no regenerate/refresh of a closed
  // prediction. The postmortem stays fully viewable.
  const readOnly = Boolean(currentSlate()?.read_only);
  if (refreshButton) refreshButton.disabled = !state.authenticated || state.isLoading || readOnly;
  if (demoButton) {
    const isProduction = state.health?.environment === "production";
    demoButton.hidden = isProduction;
    demoButton.disabled = !state.authenticated || state.isLoading;
  }
  if (workerButton) workerButton.disabled = !state.authenticated || !state.worker || state.isLoading;
  if (copyButton) copyButton.disabled = !state.authenticated || !state.matches.length || state.isLoading;
  if (downloadButton) downloadButton.disabled = !state.authenticated || !state.matches.length || state.isLoading;
  if (resetButton) resetButton.disabled = !state.authenticated || readOnly || !Object.keys(state.manualSelections).length;
  if (filterNode) filterNode.disabled = !state.authenticated || !state.matches.length || state.isLoading;
  if (feedbackNode) {
    feedbackNode.textContent = state.authStatusMessage || "";
    feedbackNode.className = `auth-feedback ${state.authenticated ? "ok" : "warn"}`;
  }
}

function renderLoadingRows() {
  return Array.from({length: 4}, (_, index) => `
    <div class="pick-row loading-row">
      <div class="pick-index"><strong>${index + 1}</strong><span>...</span></div>
      <div class="skeleton-block"></div>
      <div class="skeleton-block short"></div>
    </div>
  `).join("");
}

async function loadSlateDetails(slateId) {
  // R6.3 performance: the prediction board only awaits the CORE fetches it needs
  // to render (predictions, features, ticket, quality + the three batch context
  // maps). The heavy diagnostics (Money Mode, ticket canary dry-run, the four
  // team-rating panels, external results) are deferred to loadSlateDiagnostics
  // so they never block first paint of the board.
  //
  // Stale-response guard: every switch bumps the sequence; if a newer switch
  // started while these fetches were in flight, we drop this (older) result so
  // a late response can never clobber the slate the user is now viewing.
  const seq = ++slateRequestSeq;
  // Switching slate resets the deferred-diagnostic state so panels show a
  // skeleton, not the previous slate's data. The cache makes a re-open instant.
  state.diagnosticsSlateId = null;
  const [predictions, features, ticketPlan, quality, evidenceBySlate, availabilityBySlate, resultsBySlate] = await Promise.all([
    safeFetch(`/predictions/slates/${slateId}`),
    safeFetch(`/predictions/slates/${slateId}/features`),
    safeFetch(`/predictions/slates/${slateId}/ticket`, {optional: true}),
    safeFetch(`/predictions/slates/${slateId}/quality`, {optional: true}),
    safeFetch(`/evidence/slates/${slateId}`, {optional: true}),
    safeFetch(`/availability/slates/${slateId}`, {optional: true}),
    safeFetch(`/results/slates/${slateId}/context`, {optional: true}),
  ]);
  // A newer slate switch superseded this request — discard the stale payload.
  if (seq !== slateRequestSeq) return;
  if (!Array.isArray(predictions) || !Array.isArray(features)) {
    state.matches = [];
    state.ticketPlan = null;
    state.modelDoubleMatchIds = new Set();
    state.modelFullDoubleMatchIds = new Set();
    state.modelTripleMatchIds = new Set();
    return;
  }
  const evidenceMap = (evidenceBySlate && !Array.isArray(evidenceBySlate)) ? evidenceBySlate : {};
  const availabilityMap = (availabilityBySlate && !Array.isArray(availabilityBySlate)) ? availabilityBySlate : {};
  const resultsMap = (resultsBySlate && !Array.isArray(resultsBySlate)) ? resultsBySlate : {};

  const details = predictions.map((prediction) => {
    const feature = features.find((item) => item.match_id === prediction.match_id);
    const qualityItem = Array.isArray(quality)
      ? quality.find((item) => item.match_id === prediction.match_id)
      : null;
    const slateMatch = state.slates
      .flatMap((slate) => slate.matches || [])
      .find((item) => item.match_id === prediction.match_id);
    return {
      ...prediction,
      prediction,
      features: feature,
      quality: qualityItem,
      evidence: Array.isArray(evidenceMap[prediction.match_id]) ? evidenceMap[prediction.match_id] : [],
      availability: Array.isArray(availabilityMap[prediction.match_id]) ? availabilityMap[prediction.match_id] : [],
      results: Array.isArray(resultsMap[prediction.match_id]) ? resultsMap[prediction.match_id] : [],
      kickoff_at: slateMatch?.kickoff_at || prediction.generated_at,
      venue: slateMatch?.venue || null,
      match_id: prediction.match_id,
      position: prediction.position,
    };
  });

  state.matches = details.sort((a, b) => a.position - b.position);
  state.ticketPlan = ticketPlan && !Array.isArray(ticketPlan) ? ticketPlan : null;
  state.modelDoubleMatchIds = chooseModelDoubleMatchIds(state.matches, currentSlate());
  const fullCoverage = chooseFullCoverageMatchIds(state.matches, currentSlate());
  state.modelFullDoubleMatchIds = fullCoverage.doubleMatchIds;
  state.modelTripleMatchIds = fullCoverage.tripleMatchIds;
  state.selectedMatchId = state.matches[0]?.match_id || null;
  state.manualSelections = {};
}

let diagnosticsRequestSeq = 0;

function _applyDiagnostics(payload) {
  const [shadow, dryRun, readiness, canary, ticketCanaryDryRun, moneyMode, opsStatus, externalResults, slateOptions, resultsValidation] = payload;
  state.teamRatingShadow = (shadow && !Array.isArray(shadow)) ? shadow : null;
  state.teamRatingDryRun = (dryRun && !Array.isArray(dryRun)) ? dryRun : null;
  state.teamRatingReadiness = (readiness && !Array.isArray(readiness)) ? readiness : null;
  state.teamRatingCanary = (canary && !Array.isArray(canary)) ? canary : null;
  state.ticketCanaryDryRun = (ticketCanaryDryRun && !Array.isArray(ticketCanaryDryRun)) ? ticketCanaryDryRun : null;
  state.moneyMode = (moneyMode && !Array.isArray(moneyMode)) ? moneyMode : null;
  state.moneyModeOpsStatus = (opsStatus && !Array.isArray(opsStatus)) ? opsStatus : null;
  state.externalResults = (externalResults && !Array.isArray(externalResults)) ? externalResults : null;
  state.slateOptions = (slateOptions && !Array.isArray(slateOptions)) ? slateOptions : null;
  state.resultsValidation = (resultsValidation && !Array.isArray(resultsValidation)) ? resultsValidation : null;
}

// R6.3: deferred, cached, cancellable load of the heavy Diagnóstico panels.
// Never blocks the prediction board — invoked when the Diagnóstico tab is opened
// (or right after a slate's core load if that tab is already active).
async function loadSlateDiagnostics(slateId) {
  if (!slateId || !state.authenticated) return;
  const cached = getCachedDiagnostics(slateId);
  if (cached) {
    state.diagnosticsSlateId = slateId;
    state.diagnosticsLoading = false;
    _applyDiagnostics(cached);
    renderDiagnosticsPanels();
    return;
  }
  const seq = ++diagnosticsRequestSeq;
  state.diagnosticsLoading = true;
  state.diagnosticsSlateId = null;
  renderDiagnosticsPanels(); // immediate skeleton
  const payload = await Promise.all([
    safeFetch(`/predictions/slates/${slateId}/team-rating-shadow`, {optional: true}),
    safeFetch(`/predictions/slates/${slateId}/team-rating-activation-dry-run`, {optional: true}),
    safeFetch(`/predictions/slates/${slateId}/team-rating-activation-readiness`, {optional: true}),
    safeFetch(`/predictions/slates/${slateId}/team-rating-canary-status`, {optional: true}),
    safeFetch(`/predictions/slates/${slateId}/ticket-canary-dry-run`, {optional: true}),
    safeFetch(`/predictions/slates/${slateId}/money-mode`, {optional: true}),
    safeFetch(`/operations/money-mode/status`, {optional: true}),
    safeFetch(`/results/slates/${slateId}/provider-dry-run`, {optional: true}),
    // R6.4: always-present ticket options + completed-slate result validation.
    safeFetch(`/predictions/slates/${slateId}/options`, {optional: true}),
    safeFetch(`/tracking/completed-slates/results-validation`, {optional: true}),
  ]);
  // A newer slate switch / diagnostics load superseded this one — drop it.
  if (seq !== diagnosticsRequestSeq) return;
  setCachedDiagnostics(slateId, payload);
  state.diagnosticsSlateId = slateId;
  state.diagnosticsLoading = false;
  _applyDiagnostics(payload);
  renderDiagnosticsPanels();
}

async function boot() {
  state.isLoading = true;
  updateAuthControls();
  renderProductionStatus();
  const [health, ready] = await Promise.all([
    safeFetch("/health", {optional: true}),
    safeFetch("/ready", {optional: true}),
  ]);

  state.health = health && !Array.isArray(health) ? health : null;
  state.ready = ready && !Array.isArray(ready) ? ready : null;

  if (!state.authenticated) {
    state.slates = [];
    state.providers = [];
    state.worker = null;
    state.matches = [];
    state.ticketPlan = null;
    state.activeSlateId = null;
    state.selectedMatchId = null;
    state.isLoading = false;
    updateAuthControls();
    renderSidebar();
    renderBoard();
    attachEvents();
    return;
  }

  const [visible, providers, worker] = await Promise.all([
    // Selector source of truth: open official slates first, else the most
    // recent official slates in read-only mode, so the UI is never empty
    // when there is no open boleta. Demo/unverified are excluded server-side.
    safeFetch("/slates/visible", {optional: true}),
    safeFetch("/sources/providers"),
    safeFetch("/worker/scheduler/status", {optional: true}),
  ]);

  state.providers = Array.isArray(providers) ? providers : [];
  state.worker = worker && !Array.isArray(worker) ? worker : null;
  const workerButton = getById("run-worker");
  if (workerButton) {
    workerButton.disabled = !state.worker;
    workerButton.title = state.worker
      ? "Ejecuta una iteración del scheduler"
      : "Las rutas HTTP del worker están cerradas en modo producción";
  }

  // Resolve the visible set (open + recent fallback). If /slates/visible is
  // unavailable (older backend), degrade gracefully to /slates (open-only).
  const savedSlateId = readSavedSlateId();
  if (visible && !Array.isArray(visible)) {
    const decision = resolveVisibleSelection({ visible, savedId: savedSlateId });
    state.slates = decision.slates;
    state.activeSlateId = decision.selectedId;
    state.visibleReason = decision.reason;
    state.readOnlySelection = decision.readOnly;
    state.discovery = visible.discovery || null;
    state.visibleMessage = decision.message;
  } else {
    const fallback = await safeFetch("/slates", {optional: true});
    state.slates = Array.isArray(fallback) ? fallback : [];
    state.activeSlateId = (savedSlateId && state.slates.some((s) => s.id === savedSlateId))
      ? savedSlateId
      : (state.slates[0]?.id || null);
    state.visibleReason = state.slates.length ? "open_slate" : "no_official_slates";
    state.readOnlySelection = false;
    state.discovery = null;
    state.visibleMessage = "";
  }

  if (!state.slates.length && !demoLoadAttempted) {
    demoLoadAttempted = true;
  }

  if (state.activeSlateId) {
    await loadSlateDetails(state.activeSlateId);
  } else {
    state.matches = [];
    state.ticketPlan = null;
    state.modelDoubleMatchIds = new Set();
    state.modelFullDoubleMatchIds = new Set();
    state.modelTripleMatchIds = new Set();
    state.selectedMatchId = null;
  }

  // Persistent notice when the selector fell back to a recent read-only slate.
  state.transitionBanner = state.visibleReason === "fallback_recent"
    ? state.visibleMessage
    : state.transitionBanner;

  state.authStatusMessage = "Conectado";
  state.isLoading = false;
  updateAuthControls();
  renderSidebar();
  renderBoard();
  renderTransitionBanner();
  renderProductionStatus();
  attachEvents();
}

async function pollActiveSlate() {
  // Heart-beat. With MULTIPLE active slates (e.g. weekend PG + midweek MS),
  // /slates/active only reports the single most-urgent one — it must NOT drive
  // the user's selection. Manual selection is authoritative: we only auto-switch
  // when the slate the user is viewing has actually disappeared from the active
  // list (closed/archived). Otherwise we just refresh the list + countdown.
  if (!state.authenticated) return;
  const [meta, slates] = await Promise.all([
    safeFetch("/slates/active", {optional: true}),
    safeFetch("/slates", {optional: true}),
  ]);
  state.activeMeta = meta && meta.slate ? meta : null;
  if (meta && meta.server_time) {
    state.serverSkewMs = Date.now() - new Date(meta.server_time).getTime();
  }
  const previousId = state.activeSlateId;
  const previousCode = previousId
    ? (state.slates.find((slate) => slate.id === previousId)?.draw_code || previousId)
    : null;
  // Refresh the active list without clobbering the manual selection.
  if (Array.isArray(slates)) state.slates = slates;

  const decision = resolveActiveSelection({
    selectedId: previousId,
    slates: state.slates,
    activeMeta: meta,
  });

  if (decision.switched) {
    // The viewed slate closed / disappeared → move to the most-urgent active one.
    if (decision.selectedId) {
      const next = decision.next;
      if (previousId && previousCode && previousId !== decision.selectedId) {
        state.transitionBanner = `Concurso ${previousCode} cerrado. Cargando ${next.draw_code}…`;
        renderTransitionBanner();
        setTimeout(() => {
          state.transitionBanner = null;
          renderTransitionBanner();
        }, 8000);
      }
      state.activeSlateId = decision.selectedId;
      state.selectedMatchId = null;
      await loadSlateDetails(decision.selectedId);
    } else {
      state.activeSlateId = null;
    }
    renderSidebar();
    renderBoard();
    attachEvents();
  } else {
    // Selection preserved — refresh the sidebar so status labels stay current.
    renderSidebar();
  }

  // Countdown always reflects the SELECTED slate's cierre, not the most-urgent.
  state.closesAtMs = selectedSlateCountdownMs(state.slates, state.activeSlateId);
  tickCountdown();
}

function tickCountdown() {
  const node = document.getElementById("ticket-countdown");
  if (!node) return;
  node.classList.remove("ticket-countdown--urgent", "ticket-countdown--closed");
  if (state.closesAtMs == null) {
    node.textContent = "";
    return;
  }
  const remainingMs = state.closesAtMs - (Date.now() - state.serverSkewMs);
  if (remainingMs <= 0) {
    node.textContent = "Concurso cerrado · esperando próxima papeleta";
    node.classList.add("ticket-countdown--closed");
    return;
  }
  const hours = Math.floor(remainingMs / 3600000);
  const minutes = Math.floor((remainingMs % 3600000) / 60000);
  const seconds = Math.floor((remainingMs % 60000) / 1000);
  const text = hours > 0
    ? `Cierra en ${hours}h ${String(minutes).padStart(2, "0")}m ${String(seconds).padStart(2, "0")}s`
    : `Cierra en ${minutes}m ${String(seconds).padStart(2, "0")}s`;
  node.textContent = text;
  if (remainingMs < 30 * 60 * 1000) {
    node.classList.add("ticket-countdown--urgent");
  }
}

function renderTransitionBanner() {
  const node = document.getElementById("transition-banner");
  if (!node) return;
  if (state.transitionBanner) {
    node.textContent = state.transitionBanner;
    node.hidden = false;
  } else {
    node.textContent = "";
    node.hidden = true;
  }
}

function pickPreviewProposal() {
  // Show the most recent validated proposal that hasn't already been
  // promoted into a slate. Observed (single-sighting) proposals are
  // intentionally not surfaced — operators only get to act once the
  // dual-time validation cleared.
  const candidates = (state.proposals || [])
    .filter((p) => p.status === "validated" && !p.promoted_slate_id);
  if (candidates.length === 0) return null;
  return candidates.slice().sort((a, b) =>
    new Date(b.last_seen_at).getTime() - new Date(a.last_seen_at).getTime()
  )[0];
}

function renderNextContestCard() {
  const node = document.getElementById("next-contest-card");
  if (!node) return;
  const proposal = pickPreviewProposal();
  if (!proposal) {
    node.hidden = true;
    node.innerHTML = "";
    return;
  }
  const fixturesHtml = (proposal.fixtures || [])
    .slice()
    .sort((a, b) => a.position - b.position)
    .map((f) => `
      <li class="next-contest-fixture">
        <span class="next-contest-pos">#${f.position}</span>
        <span class="next-contest-pair">${escapeHtml(f.home)} <em>vs</em> ${escapeHtml(f.away)}</span>
      </li>
    `)
    .join("");
  const closesAt = proposal.registration_closes_at
    ? formatDate(proposal.registration_closes_at)
    : "sin cierre confirmado";
  const sourceHost = (() => {
    try { return new URL(proposal.source_url).host; } catch { return proposal.source_url; }
  })();
  const weekTypeLabel = { weekend: "Fin de semana", revancha: "Revancha" }[proposal.week_type] || proposal.week_type;
  const fixtureCount = (proposal.fixtures || []).length;
  const promoteDisabled = state.proposalPromoting ? "disabled" : "";
  node.innerHTML = `
    <div class="panel-head">
      <h2>Próximo concurso</h2>
      <p class="meta-copy">Validado y listo para usar como boleta activa.</p>
    </div>
    <div class="next-contest-summary">
      <div class="next-contest-head">
        <span class="next-contest-code">Concurso ${escapeHtml(proposal.draw_code)}</span>
        <div class="next-contest-pills">
          <span class="next-contest-pill accent">${escapeHtml(weekTypeLabel)}</span>
          <span class="next-contest-pill">${fixtureCount} partidos</span>
          <span class="next-contest-pill">cierra ${escapeHtml(closesAt)}</span>
          <span class="next-contest-pill">${escapeHtml(sourceHost)} · ${proposal.observations} obs.</span>
        </div>
      </div>
      <button id="promote-proposal-btn" type="button" class="primary-button" data-proposal-id="${escapeHtml(proposal.id)}" ${promoteDisabled}>
        ${state.proposalPromoting ? "Promoviendo…" : "Usar esta boleta"}
      </button>
    </div>
    <ol class="next-contest-fixtures">${fixturesHtml}</ol>
  `;
  node.hidden = false;
  const button = document.getElementById("promote-proposal-btn");
  if (button) {
    button.addEventListener("click", () => promoteProposal(proposal.id));
  }
}

async function pollProposals() {
  // Polled on a slow cadence — the worker only refreshes proposals
  // hourly so anything tighter than ~5min is wasted traffic.
  if (!state.authenticated) return;
  const proposals = await safeFetch("/slates/proposed", {optional: true});
  if (Array.isArray(proposals)) {
    state.proposals = proposals;
    renderNextContestCard();
  }
}

async function promoteProposal(proposalId) {
  if (!proposalId || state.proposalPromoting) return;
  state.proposalPromoting = true;
  renderNextContestCard();
  const result = await safePost(`/slates/proposed/${proposalId}/promote`);
  state.proposalPromoting = false;
  if (result && result.slate?.id) {
    state.transitionBanner = `Concurso ${result.slate.draw_code} promovido a slate activo.`;
    renderTransitionBanner();
    setTimeout(() => { state.transitionBanner = null; renderTransitionBanner(); }, 6000);
    const slates = await safeFetch("/slates");
    if (Array.isArray(slates)) state.slates = slates;
    renderSidebar();
  }
  await pollProposals();
}

attachStaticEvents();
checkSession().then(() => {
  updateAuthControls();
  boot().then(() => {
    pollActiveSlate();
    pollProposals();
    // Live tracking is best-effort and fully isolated: a load/link error
    // or a failing dashboard fetch must never break the main selector.
    import("./live-tracking.js")
      .then(({ initLiveTracking }) => {
        initLiveTracking({
          container: document.getElementById("live-tracking-panel"),
          detailContainer: document.getElementById("live-tracking-detail"),
          fetchJson: (path) => safeFetch(path, { optional: true }),
        });
      })
      .catch((error) => {
        console.error("live-tracking module failed to load", error);
      });
  }).catch((error) => {
    // Last-resort guard: boot() should never leave the UI stuck on the
    // static "Cargando…" placeholder. Clear loading and render whatever
    // state we have (login prompt or empty selector).
    console.error("boot failed", error);
    state.isLoading = false;
    state.authStatusMessage = "No se pudo cargar. Revisa la conexión o inicia sesión.";
    try {
      updateAuthControls();
      renderSidebar();
      renderBoard();
    } catch (renderError) {
      console.error("render after boot failure also failed", renderError);
    }
  });
});
// 1 Hz local countdown ticker (cheap, only touches one DOM node).
setInterval(tickCountdown, 1000);
// 60 s authoritative re-fetch of the active slate (and transition trigger).
setInterval(pollActiveSlate, 60000);
// 5 min refresh of staged proposals — the LN PDF only changes a few
// times per week so anything more frequent is wasted polling.
setInterval(pollProposals, 5 * 60 * 1000);
