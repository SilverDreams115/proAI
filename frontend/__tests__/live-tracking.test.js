import { describe, it, expect } from "vitest";

import {
  liveStatusLabel,
  liveMatchTone,
  formatScore,
  hitLabel,
  coverageCell,
  renderLiveMatchRow,
  renderLiveSlateDetail,
  renderDashboardEntry,
  renderLiveDashboard,
} from "../live-tracking.js";

const baseMatch = (over = {}) => ({
  position: 1,
  home_team_name: "México",
  away_team_name: "South Africa",
  predicted_outcome: "1",
  draw_probability: 0.25,
  status: "scheduled",
  is_final: false,
  is_live: false,
  is_pending: true,
  prediction_hit: null,
  home_goals: null,
  away_goals: null,
  result_code: null,
  draw_was_real: null,
  draw_was_covered: false,
  ticket_modes: {
    simple: { picks: ["1"] },
    doubles: { picks: ["1", "X"] },
    full: { picks: ["1", "X", "2"] },
  },
  draw_risk: { is_live_draw: true, is_strong_draw: false },
  ...over,
});

describe("liveStatusLabel", () => {
  it("maps every status", () => {
    expect(liveStatusLabel("scheduled")).toBe("Pendiente");
    expect(liveStatusLabel("live")).toBe("En vivo");
    expect(liveStatusLabel("halftime")).toBe("Medio tiempo");
    expect(liveStatusLabel("full_time")).toBe("Final");
    expect(liveStatusLabel("postponed")).toBe("Pospuesto");
    expect(liveStatusLabel("cancelled")).toBe("Cancelado");
    expect(liveStatusLabel("nope")).toBe("—");
  });
});

describe("liveMatchTone", () => {
  it("pending when not started", () => {
    expect(liveMatchTone(baseMatch())).toBe("pending");
  });
  it("live when in progress", () => {
    expect(liveMatchTone(baseMatch({ is_live: true, is_pending: false, status: "live" }))).toBe("live");
  });
  it("hit when final and prediction correct", () => {
    expect(liveMatchTone(baseMatch({ is_final: true, prediction_hit: true }))).toBe("hit");
  });
  it("miss when final and prediction wrong", () => {
    expect(liveMatchTone(baseMatch({ is_final: true, prediction_hit: false }))).toBe("miss");
  });
  it("draw overrides hit/miss when a real draw occurred", () => {
    expect(
      liveMatchTone(baseMatch({ is_final: true, prediction_hit: false, draw_was_real: true })),
    ).toBe("draw");
  });
});

describe("formatScore + hitLabel", () => {
  it("dash when no goals", () => {
    expect(formatScore(baseMatch())).toBe("—");
    expect(hitLabel(baseMatch())).toBe("—");
  });
  it("score and result when final", () => {
    const m = baseMatch({ is_final: true, home_goals: 2, away_goals: 1, prediction_hit: true });
    expect(formatScore(m)).toBe("2-1");
    expect(hitLabel(m)).toBe("Acierto");
  });
  it("fallo when missed", () => {
    const m = baseMatch({ is_final: true, home_goals: 0, away_goals: 1, prediction_hit: false });
    expect(hitLabel(m)).toBe("Fallo");
  });
});

describe("coverageCell", () => {
  it("renders Sí/No", () => {
    expect(coverageCell(true)).toContain("Sí");
    expect(coverageCell(false)).toContain("No");
  });
});

describe("renderLiveMatchRow", () => {
  it("renders a pending row with the empate vivo chip and X coverage", () => {
    const html = renderLiveMatchRow(baseMatch());
    expect(html).toContain("tone-pending");
    expect(html).toContain("Pendiente");
    expect(html).toContain("Empate vivo");
    // Simple does not cover X, doubles + full do.
    expect(html).toMatch(/S <span class="cover-no">No<\/span>/);
    expect(html).toMatch(/D <span class="cover-yes">Sí<\/span>/);
  });

  it("renders a live row with minute", () => {
    const html = renderLiveMatchRow(
      baseMatch({ is_live: true, is_pending: false, status: "live", minute: 63, home_goals: 1, away_goals: 0 }),
    );
    expect(html).toContain("tone-live");
    expect(html).toContain("63'");
    expect(html).toContain("1-0");
  });

  it("renders a real draw badge with coverage state", () => {
    const html = renderLiveMatchRow(
      baseMatch({
        is_final: true, status: "full_time", prediction_hit: false,
        home_goals: 1, away_goals: 1, result_code: "X",
        draw_was_real: true, draw_was_covered: true,
        draw_risk: { is_live_draw: true, is_strong_draw: true },
      }),
    );
    expect(html).toContain("tone-draw");
    expect(html).toContain("Empate real · X cubierto");
    expect(html).toContain("Empate fuerte");
    expect(html).toContain("Fallo");
  });
});

describe("renderLiveSlateDetail", () => {
  it("renders header counts and one row per match", () => {
    const data = {
      draw_code: "PG-2336",
      week_type: "weekend",
      match_count: 2,
      completed_count: 1,
      live_count: 1,
      pending_count: 0,
      is_complete: false,
      matches: [
        baseMatch({ position: 1, is_final: true, prediction_hit: true, home_goals: 2, away_goals: 0 }),
        baseMatch({ position: 2, is_live: true, is_pending: false, status: "live" }),
      ],
    };
    const html = renderLiveSlateDetail(data);
    expect(html).toContain("PG-2336");
    expect(html).toContain("1/2 finalizados");
    expect(html).toContain("1 en vivo");
    expect((html.match(/live-row/g) || []).length).toBe(2);
  });
});

describe("renderLiveDashboard", () => {
  const entry = (over = {}) => ({
    slate_id: "s1",
    draw_code: "PG-2336",
    week_type: "weekend",
    status_label: "Cerrada",
    match_count: 14,
    completed_count: 14,
    live_count: 0,
    pending_count: 0,
    simple_hits: 7,
    doubles_hits: 9,
    full_hits: 11,
    empates_reales: 3,
    empates_esperados: 3.1,
    max_possible_hits: 7,
    current_hit_rate: 0.5,
    is_complete: true,
    ...over,
  });

  it("separates real concursos from demo and renders all four", () => {
    const data = {
      closed: [
        entry({ draw_code: "PG-2336", classification: "official_real", comparable: true }),
        entry({ draw_code: "PGM-799", week_type: "midweek", classification: "synthetic_demo", comparable: false }),
      ],
      open: [
        entry({ draw_code: "PG-2337", status_label: "Abierta", is_complete: false, classification: "synthetic_demo", comparable: false }),
        entry({ draw_code: "PGM-800", week_type: "midweek", status_label: "Abierta", is_complete: false, classification: "official_but_no_results_yet", comparable: true }),
      ],
    };
    const html = renderLiveDashboard(data);
    expect(html).toContain("Seguimiento de quinielas");
    expect(html).toContain("Quinielas reales");
    expect(html).toContain("Demo / no comparable");
    for (const code of ["PG-2336", "PGM-799", "PG-2337", "PGM-800"]) {
      expect(html).toContain(code);
    }
    expect((html.match(/track-card/g) || []).length).toBe(4);
    expect((html.match(/Ver comparación/g) || []).length).toBe(4);
    // Demo cards carry the "Demo — no comparable" badge.
    expect((html.match(/Demo — no comparable/g) || []).length).toBe(2);
    expect(html).toContain("is-demo");
  });

  it("shows empty copy when a group has no slates", () => {
    const html = renderLiveDashboard({ closed: [], open: [] });
    expect(html).toContain("Sin quinielas.");
  });
});

import { initLiveTracking } from "../live-tracking.js";

describe("initLiveTracking isolation", () => {
  const mockContainer = () => ({ innerHTML: "", querySelectorAll: () => [] });

  it("no-ops safely when the container is missing", () => {
    expect(initLiveTracking({ container: null, fetchJson: () => {} })).toBeUndefined();
  });

  it("no-ops when fetchJson is not a function", () => {
    expect(initLiveTracking({ container: mockContainer(), fetchJson: null })).toBeUndefined();
  });

  it("renders a controlled error state when the dashboard fetch fails (never throws)", async () => {
    const container = mockContainer();
    const api = initLiveTracking({
      container,
      detailContainer: null,
      fetchJson: () => Promise.reject(new Error("boom")),
    });
    await api.refresh();
    expect(container.innerHTML).toContain("No se pudo cargar el seguimiento");
  });

  it("renders the dashboard when the fetch succeeds", async () => {
    const container = mockContainer();
    const api = initLiveTracking({
      container,
      detailContainer: null,
      fetchJson: () => Promise.resolve({ closed: [], open: [] }),
    });
    await api.refresh();
    expect(container.innerHTML).toContain("Seguimiento de quinielas");
  });

  it("shows a controlled error in the detail pane when live-results fails", async () => {
    const container = mockContainer();
    const detailContainer = { innerHTML: "", scrollIntoView: () => {} };
    const api = initLiveTracking({
      container,
      detailContainer,
      fetchJson: () => Promise.reject(new Error("boom")),
    });
    await api.showDetail("slate-1");
    expect(detailContainer.innerHTML).toContain("No se pudo cargar el detalle");
  });
});

import {
  renderComparisonRow,
  renderComparisonDetail,
  renderSummaryBar,
  predictionChips,
  diagnosisBadge,
  renderEmptyResults,
} from "../live-tracking.js";

const cmpMatch = (over = {}) => ({
  position: 1,
  home_team_name: "México",
  away_team_name: "South Africa",
  predicted_outcome: "1",
  draw_probability: 0.33,
  home_goals: null, away_goals: null, result_code: null,
  status: "scheduled", is_final: false, is_live: false, is_pending: true,
  prediction_hit: null, simple_hit: null, doubles_hit: null, full_hit: null,
  draw_was_real: null, draw_was_covered: false,
  draw_risk: { is_live_draw: true, is_strong_draw: true },
  diagnosis: "pendiente",
  ...over,
});

describe("predictionChips", () => {
  it("outlines the original pick and fills the real result", () => {
    const html = predictionChips(cmpMatch({ is_final: true, is_pending: false, result_code: "X", prediction_hit: false }));
    expect(html).toContain("oc-pick");        // pick = 1 outlined
    expect(html).toContain("oc-real-miss");   // real = X, missed
  });
  it("marks a correct result as hit", () => {
    const html = predictionChips(cmpMatch({ is_final: true, is_pending: false, predicted_outcome: "2", result_code: "2", prediction_hit: true }));
    expect(html).toContain("oc-real-hit");
  });
});

describe("diagnosisBadge", () => {
  it("colors each diagnosis", () => {
    expect(diagnosisBadge("acierto")).toContain("diag-hit");
    expect(diagnosisBadge("fallo por empate")).toContain("diag-draw");
    expect(diagnosisBadge("fallo (salió visitante)")).toContain("diag-miss");
    expect(diagnosisBadge("pendiente")).toContain("diag-pending");
    expect(diagnosisBadge("en vivo")).toContain("diag-live");
  });
});

describe("renderComparisonRow", () => {
  it("renders a final draw miss with diagnosis", () => {
    const html = renderComparisonRow(cmpMatch({
      is_final: true, is_pending: false, status: "full_time",
      home_goals: 1, away_goals: 1, result_code: "X",
      prediction_hit: false, simple_hit: false, doubles_hit: true, full_hit: true,
      draw_was_real: true, draw_was_covered: true, diagnosis: "fallo por empate",
    }));
    expect(html).toContain("tone-draw");
    expect(html).toContain("1-1");
    expect(html).toContain("diag-draw");
    expect(html).toContain("mode-hit");   // doubles/full hit
    expect(html).toContain("mode-miss");  // simple miss
  });
  it("renders a pending row", () => {
    const html = renderComparisonRow(cmpMatch());
    expect(html).toContain("tone-pending");
    expect(html).toContain("Pendiente");
  });
});

describe("renderComparisonDetail", () => {
  const base = (over = {}) => ({
    slate_id: "s1", draw_code: "PGM-799", week_type: "midweek",
    match_count: 2, completed_count: 1, live_count: 0, pending_count: 1,
    is_complete: false, results_ingested: true,
    original_snapshot: { snapshot_id: "abcdef12-x", generated_at: "2026-06-10T06:37:00Z", model_version: "ticket-optimizer-v2" },
    score: { simple_hits: 0, doubles_hits: 1, full_hits: 1, max_possible_hits: 1, empates_reales_hasta_ahora: 1, empates_esperados: 2.0 },
    matches: [
      cmpMatch({ position: 1, is_final: true, is_pending: false, status: "full_time", home_goals: 1, away_goals: 1, result_code: "X", prediction_hit: false, simple_hit: false, doubles_hit: true, full_hit: true, draw_was_real: true, draw_was_covered: true, diagnosis: "fallo por empate" }),
      cmpMatch({ position: 2 }),
    ],
    ...over,
  });

  it("shows the original snapshot, scoreline, and one row per match", () => {
    const html = renderComparisonDetail(base());
    expect(html).toContain("PGM-799");
    expect(html).toContain("ticket original");
    expect(html).toContain("abcdef12");           // truncated snapshot id
    expect(html).toContain("Empates reales 1 vs esperados 2.0");
    expect((html.match(/cmp-row/g) || []).length).toBe(2);
  });

  it("shows a useful empty state when no results are ingested", () => {
    const html = renderComparisonDetail(base({ results_ingested: false, completed_count: 0, matches: [] }));
    expect(html).toContain("Sin resultados ingeridos");
    expect(html).toContain("Fuente revisada");
    expect(html).toContain("ingest-results");
    expect(html).not.toContain("cmp-row");
  });
});

describe("renderSummaryBar", () => {
  it("aggregates closed-with-results, open count, pending, draws", () => {
    const data = {
      closed: [
        { completed_count: 9, live_count: 0, pending_count: 0, empates_reales: 2 },
        { completed_count: 0, live_count: 0, pending_count: 14, empates_reales: 0 },
      ],
      open: [{ completed_count: 0, live_count: 0, pending_count: 9, empates_reales: 0 }],
    };
    const html = renderSummaryBar(data);
    expect(html).toContain("cerradas con resultados");
    expect(html).toContain("abiertas en seguimiento");
    // 1 closed has results, 1 open, 23 pending, 2 draws
    expect(html).toMatch(/<strong>1<\/strong><span>cerradas/);
    expect(html).toMatch(/<strong>23<\/strong><span>resultados pendientes/);
    expect(html).toMatch(/<strong>2<\/strong><span>empates detectados/);
  });
});

describe("renderEmptyResults", () => {
  it("names the source and the ingest action without inventing scores", () => {
    const html = renderEmptyResults({ slate_id: "s9", match_count: 9, completed_count: 0 });
    expect(html).toContain("Lotería Nacional");
    expect(html).toContain("/api/slates/s9/ingest-results");
    expect(html).toContain("No se inventan marcadores");
  });
});

import { classificationLabel, classificationBadge } from "../live-tracking.js";

describe("classification badges", () => {
  it("labels each classification", () => {
    expect(classificationLabel("official_real")).toBe("Oficial");
    expect(classificationLabel("synthetic_demo")).toBe("Demo — no comparable");
    expect(classificationLabel("unverified")).toBe("Sin fuente oficial");
  });
  it("colors official vs demo vs unverified", () => {
    expect(classificationBadge("official_real", true)).toContain("class-official");
    expect(classificationBadge("synthetic_demo", false)).toContain("class-demo");
    expect(classificationBadge("unverified", false)).toContain("class-unverified");
  });
});

describe("renderComparisonDetail — not comparable", () => {
  it("blocks scoring and explains why for a demo slate", () => {
    const data = {
      slate_id: "s1", draw_code: "PG-2336", week_type: "weekend",
      classification: "synthetic_demo", comparable: false,
      classification_reasons: ["sin proposal oficial asociada (no vino del pipeline LN)", "todos los partidos son competencias demo (International Friendlies)"],
      competitions: ["International Friendlies"],
      results_ingested: false, completed_count: 0, match_count: 14, pending_count: 14,
      is_complete: false, original_snapshot: {}, score: {}, matches: [],
    };
    const html = renderComparisonDetail(data);
    expect(html).toContain("No comparable: slate demo / sintética");
    expect(html).toContain("No se calcula score oficial");
    expect(html).toContain("International Friendlies");
    expect(html).not.toContain("cmp-row");
    expect(html).not.toContain("cmp-scoreline");
  });
});
