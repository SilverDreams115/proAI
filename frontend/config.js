const apiBase = "/api";
const state = {
  slates: [],
  activeSlateId: null,
  matches: [],
  selectedMatchId: null,
  worker: null,
  providers: [],
  manualSelections: {},
  ticketPlan: null,
  health: null,
  ready: null,
  modelDoubleMatchIds: new Set(),
  modelFullDoubleMatchIds: new Set(),
  modelTripleMatchIds: new Set(),
  lastError: null,
  authenticated: false,
  authMethod: null,
  authStatusMessage: "Ingresa el password para cargar la quiniela.",
  isLoading: false,
  ticketMode: "simple",
  // Auto-transition support (Fase 1.3). `activeMeta` is the last
  // /slates/active response; `closesAtMs` is the unix-ms snapshot used by
  // the 1-second countdown ticker so it stays accurate between polls.
  activeMeta: null,
  closesAtMs: null,
  serverSkewMs: 0,
  transitionBanner: null,
  // Fase 2.6: staged proposals from the LN guide PDF. Polled every 5
  // min; the card surfaces the most recent `validated` proposal that
  // hasn't been promoted yet so the operator can preview the next
  // concurso before cierre lands.
  proposals: [],
  proposalPromoting: false,
  // R5.4: read-only Team Rating Shadow diagnostic for the active slate. Pure
  // projection of the inactive gate — never changes predictions, picks or
  // tickets. Rendered in the Diagnóstico tab.
  teamRatingShadow: null,
  // R5.5: read-only controlled-activation dry-run for the active slate. Pure
  // simulation of enabling the gate — never changes real predictions, picks,
  // tickets or probabilities. Rendered in the Diagnóstico tab.
  teamRatingDryRun: null,
  // R5.6-A: read-only activation-readiness report for the active slate. Pure
  // diagnostic — never activates the gate or changes real data. Diagnóstico tab.
  teamRatingReadiness: null,
  // R5.6-B: controlled-canary status for the active slate. The canary changes
  // only the served effective probabilities for scoped positions; the ticket is
  // never affected. Diagnóstico tab + per-card CANARY badge.
  teamRatingCanary: null,
  liveResultsObserver: null,
  liveResultsObserverAlert: null,
  neuralShadowPanel: null,
  moneyMode: null,
  moneyModeOpsStatus: null,
  productFlow: null,
  slateOptions: null,
  slateReadinessReport: null,
  operationalPredictionAudit: null,
  slateReadinessFilter: "all",
  exportStatusBySlate: {},
  diagnosticsPreheatedSlateId: null,
};

let demoLoadAttempted = false;

const outcomeLabel = {
  "1": "L",
  X: "E",
  "2": "V",
};
const outcomeOrder = ["1", "X", "2"];
const ticketModes = [
  {
    key: "simple",
    label: "Simple",
    description: "Jugada base: tu pick simple por partido.",
  },
  {
    key: "doubles",
    label: "Dobles",
    description: "Dobles con presupuesto según tipo de concurso.",
  },
  {
    key: "full",
    label: "Completa",
    description: "Sin tope: todos los dobles y triples donde hay incertidumbre.",
  },
];
const multipleRules = {
  weekend: {
    doublesOnlyMax: 8,
    combinedDoubleMax: 2,
    combinedTripleMax: 4,
  },
  midweek: {
    doublesOnlyMax: 3,
    combinedDoubleMax: 3,
    combinedTripleMax: 2,
  },
  revancha: {
    doublesOnlyMax: 3,
    combinedDoubleMax: 3,
    combinedTripleMax: 2,
  },
  fallback: {
    doublesOnlyMax: 3,
    combinedDoubleMax: 2,
    combinedTripleMax: 2,
  },
};
