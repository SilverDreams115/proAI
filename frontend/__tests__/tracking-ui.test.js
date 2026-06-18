// Fase A — Seguimiento UI: tabs, dashboard, comparison hit/miss/pending +
// learning column, fijo cleanup and main-panel integrity.
import { describe, it, expect } from "vitest";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";

import {
  renderComparisonRow,
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
    expect(renderComparisonRow(cmp({ learning_status: "excluded" }))).toContain("Excluido");
    expect(renderComparisonRow(cmp({ learning_status: "waiting_result" }))).toContain("Pendiente");
  });
  it("degrades gracefully when tracking learning_status is missing", () => {
    expect(learningBadge(undefined)).toContain("—");
    // a comparison row still renders without a learning_status (tracking 401/blip)
    expect(() => renderComparisonRow(cmp({ learning_status: undefined }))).not.toThrow();
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
