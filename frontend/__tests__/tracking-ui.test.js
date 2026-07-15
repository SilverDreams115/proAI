// Fase A — Seguimiento UI: tabs, dashboard, comparison hit/miss/pending +
// learning column, fijo cleanup and main-panel integrity.
import { describe, it, expect } from "vitest";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";

import {
  renderComparisonRow,
  renderComparisonDetail,
  renderLiveDashboard,
  renderDashboardEntry,
  renderNoComparableResults,
  learningBadge,
} from "../live-tracking.js";

const here = dirname(fileURLToPath(import.meta.url));
const root = join(here, "..");
const indexHtml = readFileSync(join(root, "index.html"), "utf8");
const configJs = readFileSync(join(root, "config.js"), "utf8");

describe("main tabs", () => {
  it("renders the four view tabs with matching containers", () => {
    for (const v of ["prediccion", "seguimiento", "aprendizaje", "diagnostico"]) {
      expect(indexHtml).toContain(`data-view="${v}"`);
      expect(indexHtml).toContain(`id="view-${v}"`);
    }
    expect(indexHtml).toContain(">Predicción actual<");
    expect(indexHtml).toContain(">Seguimiento<");
    expect(indexHtml).toContain(">Aprendizaje<");
    expect(indexHtml).toContain(">Diagnóstico<");
  });
});

describe("Seguimiento dashboard", () => {
  const entry = (over = {}) => ({
    slate_id: "s1",
    draw_code: "PGM-800",
    week_type: "midweek",
    status_label: "Completa",
    classification: "official_real",
    comparable: true,
    completed_count: 9,
    match_count: 9,
    live_count: 0,
    pending_count: 0,
    simple_hits: 6,
    doubles_hits: 7,
    full_hits: 9,
    empates_reales: 2,
    empates_esperados: 1.8,
    current_hit_rate: 0.667,
    ...over,
  });

  it("lists closed + partial slates", () => {
    const html = renderLiveDashboard({
      closed: [entry()],
      open: [entry({ slate_id: "s2", draw_code: "PG-2336", status_label: "Cerrada", completed_count: 4, match_count: 14, simple_hits: 0, doubles_hits: 0, full_hits: 0, pending_count: 10 })],
    });
    expect(html).toContain("Seguimiento de quinielas");
    expect(html).toContain("PGM-800");
    expect(html).toContain(">9/9</strong> finalizados");
  });

  it("shows a partial slate's finalized count", () => {
    const html = renderDashboardEntry(entry({ draw_code: "PG-2336", completed_count: 4, match_count: 14 }));
    expect(html).toContain(">4/14</strong> finalizados");
  });
});

describe("comparison: acierto / fallo / pendiente + learning", () => {
  const cmp = (over = {}) => ({
    position: 1,
    home_team_name: "México",
    away_team_name: "Corea",
    predicted_outcome: "1",
    result_code: "1",
    is_final: true,
    is_live: false,
    is_pending: false,
    prediction_hit: true,
    simple_hit: true,
    doubles_hit: true,
    full_hit: true,
    diagnosis: "acierto",
    learning_status: "ready",
    ...over,
  });

  it("shows acierto for a hit", () => {
    expect(renderComparisonRow(cmp())).toContain("acierto");
  });
  it("shows fallo for a miss", () => {
    expect(renderComparisonRow(cmp({ prediction_hit: false, result_code: "X", diagnosis: "fallo por empate" }))).toContain("fallo por empate");
  });
  it("shows pendiente for a pending match", () => {
    const html = renderComparisonRow(cmp({ is_final: false, is_pending: true, result_code: null, prediction_hit: null, diagnosis: "pendiente", learning_status: "waiting_result" }));
    expect(html).toContain("pendiente");
  });
  it("renders the learning column states", () => {
    expect(renderComparisonRow(cmp())).toContain("Ready");
    expect(renderComparisonRow(cmp({ learning_status: "classification_ready" }))).toContain("Clasificación");
    expect(renderComparisonRow(cmp({ learning_status: "excluded" }))).toContain("Excluido");
    expect(renderComparisonRow(cmp({ learning_status: "waiting_result" }))).toContain("Pendiente");
  });
  it("degrades gracefully when tracking learning_status is missing", () => {
    expect(learningBadge(undefined)).toContain("—");
    // a comparison row still renders without a learning_status (tracking 401/blip)
    expect(() => renderComparisonRow(cmp({ learning_status: undefined }))).not.toThrow();
  });
  it("renders sign-only classification rows as training-ready", () => {
    expect(learningBadge("classification_ready")).toContain("Clasificación");
    expect(learningBadge("classification_ready")).toContain("learn-hit");
    expect(learningBadge("ready")).not.toContain("Clasificación");
  });

  it("shows classification-only row count in comparison detail", () => {
    const html = renderComparisonDetail({
      slate_id: "s1",
      draw_code: "PG-2336",
      week_type: "weekend",
      comparable: true,
      results_ingested: true,
      completed_count: 14,
      match_count: 14,
      live_count: 0,
      pending_count: 0,
      is_complete: true,
      learning_rows_sign_only: 14,
      original_snapshot: {},
      score: { simple_hits: 7, doubles_hits: 7, full_hits: 7, max_possible_hits: 7 },
      matches: [cmp({ learning_status: "classification_ready" })],
    });
    expect(html).toContain("14</strong> filas listas para clasificación");
    expect(html).toContain("sin marcador canónico");
  });
});

describe("empty-state when no comparable results", () => {
  const noResultEntry = (over = {}) => ({
    slate_id: "s1",
    draw_code: "PG-2337",
    week_type: "weekend",
    status_label: "Archivada",
    classification: "official_but_no_results_yet",
    comparable: true,
    completed_count: 0,
    match_count: 14,
    live_count: 0,
    pending_count: 14,
    simple_hits: 0,
    doubles_hits: 0,
    full_hits: 0,
    empates_reales: 0,
    empates_esperados: 0,
    current_hit_rate: null,
    ...over,
  });

  it("shows the explicit empty-state when 0 slates have results", () => {
    const html = renderLiveDashboard({ closed: [noResultEntry()], open: [noResultEntry({ slate_id: "s2", draw_code: "PGM-800", week_type: "midweek", match_count: 9, pending_count: 9 })] });
    expect(html).toContain("Aún no hay resultados comparables");
    expect(html).toContain("no hay resultados reales ingeridos");
    expect(html).toContain("ingest-results");
  });

  it("does not look like a fatal error (no error-copy), still lists slates", () => {
    const html = renderLiveDashboard({ closed: [noResultEntry()], open: [] });
    expect(html).not.toContain("error-copy");
    expect(html).toContain("Seguimiento de quinielas");
    expect(html).toContain("PG-2337"); // the slate still renders
  });

  it("no learning-ready signal when there are no results", () => {
    // A slate with no results shows 'Sin resultados aún', never a hit tally.
    const entry = renderDashboardEntry(noResultEntry());
    expect(entry).toContain("Sin resultados aún");
  });

  it("Seguimiento still renders when results overlap is absent", () => {
    expect(() => renderLiveDashboard({ closed: [noResultEntry()], open: [] })).not.toThrow();
    expect(renderNoComparableResults()).toContain("Aún no hay resultados comparables");
  });

  it("hides the empty-state once any slate has results", () => {
    const withResult = renderLiveDashboard({ closed: [noResultEntry({ completed_count: 9, match_count: 9 })], open: [] });
    expect(withResult).not.toContain("Aún no hay resultados comparables");
  });
});

describe("Seguimiento shows finalizados + hits/misses after results", () => {
  it("dashboard entry surfaces finalized count and hit tally", () => {
    const html = renderDashboardEntry({
      slate_id: "s1", draw_code: "PGM-800", week_type: "midweek", status_label: "Completa",
      classification: "official_real", comparable: true,
      completed_count: 6, match_count: 9, live_count: 0, pending_count: 3,
      simple_hits: 4, doubles_hits: 5, full_hits: 6, empates_reales: 1, empates_esperados: 1.2,
      current_hit_rate: 0.667,
    });
    expect(html).toContain(">6/9</strong> finalizados");
    expect(html).toContain("S 4 · D 5 · F 6"); // hit tally visible
    expect(html).not.toContain("Sin resultados aún");
  });

  it("comparison rows show acierto and fallo side by side", () => {
    const base = {
      position: 1, home_team_name: "A", away_team_name: "B", predicted_outcome: "1",
      is_final: true, is_live: false, is_pending: false,
      simple_hit: true, doubles_hit: true, full_hit: true,
    };
    const hit = renderComparisonRow({ ...base, result_code: "1", prediction_hit: true, diagnosis: "acierto", learning_status: "ready" });
    const miss = renderComparisonRow({ ...base, position: 2, result_code: "X", prediction_hit: false, diagnosis: "fallo por empate", learning_status: "ready" });
    expect(hit).toContain("acierto");
    expect(miss).toContain("fallo por empate");
  });
});

describe("fijo cleanup + main-panel integrity", () => {
  it("no 'Fijo' legend chip remains in the main UI", () => {
    expect(indexHtml).not.toContain(">Fijo<");
    expect(indexHtml).toContain(">Simple<");
  });
  it("no giant 'Solo fijos' tooltip in config", () => {
    expect(configJs).not.toContain("Solo fijos");
    expect(configJs).not.toContain("firmaría si fuera una quiniela sencilla");
    expect(configJs).toContain("Jugada base: tu pick simple por partido.");
  });
  it("right panel (Explicación del partido) is intact", () => {
    expect(indexHtml).toContain('id="analysis"');
    expect(indexHtml).toContain("Explicación del partido");
  });
  it("Seguimiento lives in a DOM subtree isolated from Predicción", () => {
    expect(indexHtml).toContain('id="view-prediccion"');
    expect(indexHtml).toContain('id="view-seguimiento"');
    expect(indexHtml).toContain('id="live-tracking-panel"');
  });
});
