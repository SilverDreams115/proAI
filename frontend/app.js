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
} from "./helpers.js";

function currentSlate() {
  return state.slates.find((item) => item.id === state.activeSlateId) || null;
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

function doublesModelDecision(prediction, matchId = null) {
  const outcomes = sortedOutcomes(prediction);
  const best = outcomes[0];
  const second = outcomes[1];
  const bestGap = best.value - second.value;
  const confidence = prediction.confidence_band || "low";
  const allowDouble = matchId && state.modelDoubleMatchIds.has(matchId);

  if (best.value >= 0.58 && bestGap >= 0.12 && confidence !== "low") {
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
  const confidence = match.prediction.confidence_band || "low";
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
    reasons.push(`benchmark ${readinessLabel(profile.readiness)}`);
  } else {
    reasons.push(`benchmark ${readinessLabel(profile.readiness)}; no se trata como fijo seguro`);
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
  let label = "Fijo defendible";
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
  const blocked = prediction.confidence_band === "blocked" || readiness === "unclassified";
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
  const review = blocked || validation.level === "high";
  const caution =
    !review &&
    (benchmarkWeak || cautionOnly || thin || !anchored || validation.level === "medium");
  const reasons = [];

  if (blocked) reasons.push("sin benchmark confiable");
  if (benchmarkWeak) reasons.push("benchmark bajo");
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

function buildMatchCard(match) {
  const decision = effectiveDecision(match);
  const validation = validationProfile(match);
  const issue = qualityIssueProfile(match);
  const activeClass = state.selectedMatchId === match.match_id ? " active" : "";
  const manualClass = decision.source === "manual" ? " manual" : "";
  const reviewClass = issue.review ? " review" : "";
  const matchId = escapeHtml(match.match_id);
  const options = [
    ["1", match.prediction.home_probability],
    ["X", match.prediction.draw_probability],
    ["2", match.prediction.away_probability],
  ];

  const optionsMarkup = options
    .map(([key, value]) => {
      const classes = [
        "option-pill",
        match.prediction.recommended_outcome === key ? "active" : "",
        decision.picks.includes(key) && decision.source === "model" && decision.type !== "fixed" ? "secondary" : "",
        decision.picks.includes(key) && decision.source === "manual" ? "manual-choice" : "",
      ]
        .filter(Boolean)
        .join(" ");

      return `
        <button class="${classes}" data-option-pick="${key}" data-option-match="${matchId}">
          <strong>${displayOutcome(key)}</strong>
          <span>${formatPercent(value)}</span>
        </button>
      `;
    })
    .join("");

  const decisionLabel =
    decision.type === "double" ? "Doble" : decision.type === "triple" ? "Triple" : "Fijo";

  return `
    <article class="pick-row${activeClass}${manualClass}${reviewClass}" data-match-card="${matchId}">
      <div class="pick-index">
        <span class="mini-copy">Partido</span>
        <strong>${escapeHtml(match.position)}</strong>
        <small class="quality-mini ${issue.tone}">${issue.score !== null ? escapeHtml(issue.score) : "SD"}</small>
      </div>
      <div class="pick-meta">
        <div class="pick-tags">
          <span class="tag ${decision.type}">
            ${decisionLabel}
          </span>
          ${decision.source === "manual" ? `<span class="tag manual">Manual</span>` : ""}
          ${match.prediction.is_knockout ? `<span class="tag knockout" title="Eliminatoria: empate descartado">Eliminatoria</span>` : ""}
          <span class="tag">${escapeHtml(match.prediction.competition_name)}</span>
          <span class="tag ${validation.className}">${escapeHtml(validation.label)}</span>
          <span class="tag quality-${issue.tone}">${escapeHtml(issue.label)}</span>
        </div>
        <h3>${escapeHtml(match.prediction.home_team_name)} vs ${escapeHtml(match.prediction.away_team_name)}</h3>
        <p class="pick-sub">${escapeHtml(formatDate(match.kickoff_at))} ${match.venue ? `· ${escapeHtml(match.venue)}` : ""}<span class="freshness-tag" title="Cuándo se calculó esta probabilidad">Actualizado ${escapeHtml(formatRelativeAge(match.prediction.generated_at))}</span></p>
      </div>
      <div class="pick-options">
        <div class="options-grid">${optionsMarkup}</div>
        <div class="double-row">
          <div class="double-tag">
            ${decision.source === "manual" ? "Selección manual" : "Sugerencia del modelo"}:
            ${escapeHtml(displayPicks(decision.picks))}
          </div>
        </div>
      </div>
    </article>
  `;
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
  return state.slates.length
    ? state.slates.map((slate) => `
        <button class="aside-slate ${slate.id === state.activeSlateId ? "active" : ""}" data-slate-id="${escapeHtml(slate.id)}">
          <span>${escapeHtml(slate.week_type)}</span>
          <strong>${escapeHtml(slate.draw_code)}</strong>
          <small>${escapeHtml((slate.matches || []).length)} partidos</small>
        </button>
      `).join("")
    : "";
}

function renderValidationSummary() {
  if (!state.authenticated || !state.matches.length) return "";
  const issues = state.matches.map((match) => ({match, issue: qualityIssueProfile(match), decision: effectiveDecision(match)}));
  const review = issues.filter((item) => item.issue.review);
  const caution = issues.filter((item) => item.issue.caution);
  const thin = issues.filter((item) => item.issue.thin);
  const blocked = issues.filter((item) => item.issue.blocked);
  const manual = issues.filter((item) => item.decision.source === "manual");
  const doubles = issues.filter((item) => item.decision.type === "double");
  const triples = issues.filter((item) => item.decision.type === "triple");
  const fixed = issues.filter((item) => item.decision.type === "fixed");
  const topReview = review.slice(0, 4).map(({match, issue}) => `
    <button class="review-item ${issue.tone}" data-match-menu-id="${escapeHtml(match.match_id)}">
      <span>${escapeHtml(match.position)}</span>
      <strong>${escapeHtml(match.prediction.home_team_name)} vs ${escapeHtml(match.prediction.away_team_name)}</strong>
      <small>${escapeHtml(issue.reasons.slice(0, 2).join(" · ") || issue.label)}</small>
    </button>
  `).join("");

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
        <strong>${escapeHtml(fixed.length)}F / ${escapeHtml(doubles.length)}D / ${escapeHtml(triples.length)}T</strong>
      </div>
      <div class="${jackpotTone}">
        <span>P(${slateSize}/${slateSize}) jackpot</span>
        <strong>${escapeHtml(jackpotValue)}</strong>
      </div>
      <div class="${ticketsHalf != null && ticketsHalf <= 50 ? "ok" : (ticketsHalf != null && ticketsHalf <= 500 ? "warn" : "bad")}">
        <span>Para 50% acumulado</span>
        <strong>${escapeHtml(ticketsLabel)}</strong>
      </div>
      <div class="${review.length ? "bad" : "ok"}">
        <span>Revisar</span>
        <strong>${escapeHtml(review.length)}</strong>
      </div>
      <div class="${caution.length ? "warn" : "ok"}">
        <span>Cautela</span>
        <strong>${escapeHtml(caution.length)}</strong>
      </div>
      <div class="${blocked.length ? "bad" : "ok"}">
        <span>Bloqueados</span>
        <strong>${escapeHtml(blocked.length)}</strong>
      </div>
      <div class="${manual.length ? "ok" : ""}">
        <span>Manual</span>
        <strong>${escapeHtml(manual.length)}</strong>
      </div>
    </div>
    ${topReview ? `<div class="review-list">${topReview}</div>` : `<div class="empty-state compact">No hay partidos críticos en este filtro.</div>`}
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
  const readyStatus = state.ready?.status || "sin dato";
  const healthStatus = state.health?.status || "sin dato";
  const schemaCopy = state.health
    ? `schema ${state.health.schema_version}${state.health.schema_up_to_date ? " al día" : " pendiente"}`
    : "schema sin dato";

  node.innerHTML = `
    <div class="ops-head">
      <div>
        <p class="ticket-label">Estado de producción</p>
        <h2>${escapeHtml(activeSlate?.draw_code || "Sin papeleta activa")}</h2>
        <p class="meta-copy">${escapeHtml(state.authStatusMessage || "Validando sesión.")}</p>
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
        <span>Readiness</span>
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
        <small>${escapeHtml(thinMatches)} delgada(s) · hover para desglose</small>
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
    `H2H: ${quality.head_to_head_results_count ?? featurePayload.head_to_head_results_count ?? 0}`,
    `Disponibilidad: ${quality.availability_count ?? 0}`,
    `Benchmark: ${readinessLabel(quality.competition_readiness || match.prediction.competition_readiness)}`,
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
          <div class="mono">${escapeHtml(item.competition_name || "")} · ${escapeHtml(formatDate(item.played_at))}</div>
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
        <h3>${escapeHtml(match.prediction.home_team_name)} vs ${escapeHtml(match.prediction.away_team_name)}</h3>
        <p class="meta-copy">
          Quiniela activa: <strong>${escapeHtml(displayPicks(decision.picks))}</strong>
          · ${decision.source === "manual" ? "ajustada manualmente" : "sugerida por el modelo"}
        </p>
      </div>
      <div class="analysis-grid">
        <section class="analysis-block">
          <h4>Probabilidades</h4>
          <div class="facts-grid">
            <div class="fact"><strong>L</strong><span>${formatPercent(match.prediction.home_probability)}</span></div>
            <div class="fact"><strong>E</strong><span>${formatPercent(match.prediction.draw_probability)}</span></div>
            <div class="fact"><strong>V</strong><span>${formatPercent(match.prediction.away_probability)}</span></div>
            <div class="fact"><strong>Ticket</strong><span>${escapeHtml(displayPicks(decision.picks))}</span></div>
          </div>
        </section>
        <section class="analysis-block validation-block ${validation.className}">
          <h4>Validación Codex</h4>
          <div class="facts-grid">
            <div class="fact"><strong>Recomendación</strong><span>${escapeHtml(validation.label)}</span></div>
            <div class="fact"><strong>Acción</strong><span>${escapeHtml(validation.recommendation)}</span></div>
            <div class="fact"><strong>Brecha top 2</strong><span>${escapeHtml(displayOutcome(validation.profile.bestOutcome))}/${escapeHtml(displayOutcome(validation.profile.secondOutcome))} · ${formatPercent(validation.profile.topGap)}</span></div>
            <div class="fact"><strong>Entropía</strong><span>${Math.round(validation.profile.entropy * 100)}%</span></div>
            <div class="fact"><strong>Confianza</strong><span>${escapeHtml(confidenceLabel(validation.profile.confidence))}</span></div>
            <div class="fact"><strong>Benchmark</strong><span>${escapeHtml(readinessLabel(validation.profile.readiness))}</span></div>
            <div class="fact"><strong>Evidencia ligada</strong><span>${escapeHtml(evidenceCount)}</span></div>
            <div class="fact"><strong>3er resultado</strong><span>${escapeHtml(displayOutcome(validation.profile.thirdOutcome))} · ${formatPercent(validation.profile.thirdProbability)}</span></div>
          </div>
          <p class="validation-copy">${validation.reasons.map(escapeHtml).join(" · ")}</p>
        </section>
        <section class="analysis-block">
          <h4>Calidad de datos</h4>
          ${renderDataQuality(match, featurePayload, evidenceCount, resultCount)}
        </section>
        <section class="analysis-block">
          <h4>Señales del modelo</h4>
          <div class="signal-grid">${signalItems.map((item) => `<span class="signal-pill">${escapeHtml(item)}</span>`).join("")}</div>
        </section>
        <section class="analysis-block">
          <h4>Por qué tomó esta decisión</h4>
          <div>${decisionReasons.map((item) => `<p>${escapeHtml(item)}</p>`).join("")}</div>
        </section>
        <section class="analysis-block">
          <h4>Disponibilidad</h4>
          <div class="signal-grid">${availabilityMarkup}</div>
        </section>
        <section class="analysis-block">
          <h4>Evidencia</h4>
          ${evidenceMarkup}
        </section>
        <section class="analysis-block">
          <h4>Resultados ligados</h4>
          ${resultMarkup}
        </section>
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
        <p class="mini-copy">Cambia el filtro o carga una demo para revisar la interfaz.</p>
        <button id="empty-load-demo" class="ghost-button">Cargar demo</button>
      </div>
    `;
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
    if (analysisNode) analysisNode.innerHTML = renderEmpty("Calculando vista de análisis.");
    return;
  }

  if (!activeSlate || !state.matches.length) {
    if (labelNode) labelNode.textContent = "Sin quiniela activa";
    if (codeNode) codeNode.textContent = "Carga una papeleta";
    if (slateSwitcherNode) slateSwitcherNode.innerHTML = "";
    if (tabsNode) tabsNode.innerHTML = renderTicketTabs();
    if (summaryNode) summaryNode.innerHTML = renderEmpty("No hay pronósticos disponibles.");
    if (validationSummaryNode) validationSummaryNode.innerHTML = "";
    if (gridNode) gridNode.innerHTML = renderEmpty("No hay partidos listos para mostrar.");
    if (analysisNode) analysisNode.innerHTML = renderEmpty("Selecciona una quiniela para empezar.");
    return;
  }

  if (labelNode) labelNode.textContent = activeSlate.label;
  if (codeNode) codeNode.textContent = activeSlate.draw_code;
  if (slateSwitcherNode) slateSwitcherNode.innerHTML = renderSlateSwitcher();
  if (tabsNode) tabsNode.innerHTML = renderTicketTabs();
  if (summaryNode) {
    const doubles = state.matches.filter((item) => effectiveDecision(item).type === "double").length;
    const triples = state.matches.filter((item) => effectiveDecision(item).type === "triple").length;
    const rule = multipleRuleForSlate(activeSlate, state.matches.length);
    const doubleLimit = doubleLimitForSlate(activeSlate, state.matches.length);
    const doubleLabel = state.ticketMode === "full"
      ? `dobles activos / límite ${rule.combinedDoubleMax}`
      : `dobles activos / límite ${doubleLimit}`;
    const tripleLabel = state.ticketMode === "full"
      ? `triples activos / límite ${rule.combinedTripleMax}`
      : "triples activos";
    summaryNode.innerHTML = `
      <div class="summary-chip"><strong>${state.matches.length}</strong><div class="mono">partidos cargados</div></div>
      <div class="summary-chip"><strong>${doubles}</strong><div class="mono">${escapeHtml(doubleLabel)}</div></div>
      <div class="summary-chip"><strong>${triples}</strong><div class="mono">${escapeHtml(tripleLabel)}</div></div>
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
    if (slateId) {
      state.activeSlateId = slateId;
      await loadSlateDetails(slateId);
      renderSidebar();
      renderBoard();
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
  }
}

function attachEvents() {
  if (_eventsAttached) return;
  document.addEventListener("click", _handleDelegatedClick);
  _eventsAttached = true;
}

function attachStaticEvents() {
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
  if (loginButton) {
    loginButton.disabled = state.authenticated || state.isLoading;
    loginButton.textContent = state.isLoading && !state.authenticated ? "Entrando" : "Entrar";
  }
  if (logoutButton) logoutButton.disabled = !state.authenticated;
  if (passwordInput) passwordInput.disabled = state.authenticated;
  if (refreshButton) refreshButton.disabled = !state.authenticated || state.isLoading;
  if (demoButton) demoButton.disabled = !state.authenticated || state.isLoading;
  if (workerButton) workerButton.disabled = !state.authenticated || !state.worker || state.isLoading;
  if (copyButton) copyButton.disabled = !state.authenticated || !state.matches.length || state.isLoading;
  if (downloadButton) downloadButton.disabled = !state.authenticated || !state.matches.length || state.isLoading;
  if (resetButton) resetButton.disabled = !state.authenticated || !Object.keys(state.manualSelections).length;
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
  // Seven parallel fetches replace ~50 sequential round-trips. The three
  // batch endpoints (`/evidence/slates`, `/availability/slates`,
  // `/results/slates/{id}/context`) each return a {match_id: [...]}
  // mapping so the per-match loop below is a dict lookup, not a fetch.
  const [predictions, features, ticketPlan, quality, evidenceBySlate, availabilityBySlate, resultsBySlate] = await Promise.all([
    safeFetch(`/predictions/slates/${slateId}`),
    safeFetch(`/predictions/slates/${slateId}/features`),
    safeFetch(`/predictions/slates/${slateId}/ticket`, {optional: true}),
    safeFetch(`/predictions/slates/${slateId}/quality`, {optional: true}),
    safeFetch(`/evidence/slates/${slateId}`, {optional: true}),
    safeFetch(`/availability/slates/${slateId}`, {optional: true}),
    safeFetch(`/results/slates/${slateId}/context`, {optional: true}),
  ]);
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

  const [slates, providers, worker] = await Promise.all([
    safeFetch("/slates"),
    safeFetch("/sources/providers"),
    safeFetch("/worker/scheduler/status", {optional: true}),
  ]);

  state.slates = Array.isArray(slates) ? slates : [];
  state.providers = Array.isArray(providers) ? providers : [];
  state.worker = worker && !Array.isArray(worker) ? worker : null;
  const workerButton = getById("run-worker");
  if (workerButton) {
    workerButton.disabled = !state.worker;
    workerButton.title = state.worker
      ? "Ejecuta una iteración del scheduler"
      : "Las rutas HTTP del worker están cerradas en modo producción";
  }

  if (!state.slates.length && !demoLoadAttempted) {
    demoLoadAttempted = true;
  }

  state.activeSlateId = state.slates[0]?.id || null;

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

  state.authStatusMessage = "Sesión activa.";
  state.isLoading = false;
  updateAuthControls();
  renderSidebar();
  renderBoard();
  renderProductionStatus();
  attachEvents();
}

async function pollActiveSlate() {
  // Auto-transition heart-beat. /slates/active is authoritative: when the
  // worker archives the closing slate, the next call returns whatever's
  // open with the closest cierre. The frontend reacts by loading details
  // for the new id (if changed) and showing a transient banner.
  if (!state.authenticated) return;
  const meta = await safeFetch("/slates/active", {optional: true});
  if (!meta || !meta.slate) {
    state.activeMeta = null;
    state.closesAtMs = null;
    tickCountdown();
    return;
  }
  const previousId = state.activeSlateId;
  const previousCode = previousId
    ? (state.slates.find((slate) => slate.id === previousId)?.draw_code || previousId)
    : null;
  state.activeMeta = meta;
  if (meta.slate.registration_closes_at) {
    state.closesAtMs = new Date(meta.slate.registration_closes_at).getTime();
    state.serverSkewMs = Date.now() - new Date(meta.server_time).getTime();
  } else {
    state.closesAtMs = null;
  }
  if (previousId && previousId !== meta.slate.id) {
    state.transitionBanner = `Concurso ${previousCode} cerrado. Cargando ${meta.slate.draw_code}…`;
    renderTransitionBanner();
    setTimeout(() => {
      state.transitionBanner = null;
      renderTransitionBanner();
    }, 8000);
    const slates = await safeFetch("/slates");
    if (Array.isArray(slates)) state.slates = slates;
    state.activeSlateId = meta.slate.id;
    state.selectedMatchId = null;
    await loadSlateDetails(meta.slate.id);
    renderSidebar();
    renderBoard();
    attachEvents();
  }
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
  const promoteDisabled = state.proposalPromoting ? "disabled" : "";
  node.innerHTML = `
    <div class="panel-head">
      <h2>Próximo concurso</h2>
      <p class="meta-copy">Validado por dual-time scrape de la GUÍA LN — listo para promover.</p>
    </div>
    <div class="next-contest-summary">
      <div class="next-contest-head">
        <span class="next-contest-code">Concurso ${escapeHtml(proposal.draw_code)}</span>
        <span class="next-contest-meta">${escapeHtml(proposal.week_type)} · cierra ${escapeHtml(closesAt)}</span>
        <span class="next-contest-source">Fuente: ${escapeHtml(sourceHost)} · ${proposal.observations} observación(es)</span>
      </div>
      <button id="promote-proposal-btn" type="button" class="primary-button" data-proposal-id="${escapeHtml(proposal.id)}" ${promoteDisabled}>
        ${state.proposalPromoting ? "Promoviendo…" : "Promover a slate"}
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
  if (result && result.id) {
    state.transitionBanner = `Concurso ${result.draw_code} promovido a slate activo.`;
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
  });
});
// 1 Hz local countdown ticker (cheap, only touches one DOM node).
setInterval(tickCountdown, 1000);
// 60 s authoritative re-fetch of the active slate (and transition trigger).
setInterval(pollActiveSlate, 60000);
// 5 min refresh of staged proposals — the LN PDF only changes a few
// times per week so anything more frequent is wasted polling.
setInterval(pollProposals, 5 * 60 * 1000);
