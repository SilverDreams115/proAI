// R5.5 — Team Rating Activation Dry-run (read-only, diagnostic only).
//
// Pure render helpers (return HTML strings, no DOM/fetch) so they can be locked
// with Vitest. This panel simulates what enabling the controlled team-rating
// gate would do; it never changes real predictions, picks, tickets or
// probabilities.
import { escapeHtml } from "./helpers.js";
import { formatPositionRanges } from "./team-rating-shadow.js";

const PICK_LABEL = { "1": "L", X: "E", "2": "V" };

function pickLabel(pick) {
  if (pick == null) return "—";
  return PICK_LABEL[pick] || pick;
}

function fmtDelta(value) {
  if (typeof value !== "number") return "—";
  return value.toFixed(3);
}

function engineLabel(engine) {
  const map = {
    team_rating_calibrated: "rating calibrado",
    fallback: "fallback",
    xgboost: "xgboost",
  };
  return map[engine] || engine || "—";
}

function statCard(label, value, tone = "") {
  return `<div class="shadow-stat${tone ? " " + tone : ""}">
    <span class="shadow-stat-value">${escapeHtml(value)}</span>
    <span class="shadow-stat-label">${escapeHtml(label)}</span>
  </div>`;
}

function blockerChips(blockers, okText = "—") {
  if (!blockers || !blockers.length) {
    return `<span class="shadow-chip shadow-chip-ok">${escapeHtml(okText)}</span>`;
  }
  return blockers
    .map((b) => `<span class="shadow-chip">${escapeHtml(b)}</span>`)
    .join("");
}

export function renderTeamRatingActivationDryRunPanel(dryRun) {
  if (!dryRun || !dryRun.summary) {
    return `<div class="empty-state">Sin datos de activation dry-run para la papeleta activa.</div>`;
  }
  const s = dryRun.summary;
  const total = s.total_matches;
  const safe = Boolean(dryRun.safe_to_activate);

  const stats = [
    statCard("Would route", `${s.would_route}/${total}`, "shadow-stat-good"),
    statCard("Would keep current", `${s.would_keep_current}/${total}`),
    statCard("Changed top pick", `${s.changed_top_pick_count}/${total}`, s.changed_top_pick_count ? "dryrun-stat-warn" : ""),
    statCard("Δ prob máx", `${fmtDelta(s.max_probability_delta)}`),
    statCard("Bloq. por rating", `${s.blocked_by_rating}`),
    statCard("Bloq. por review", `${s.blocked_by_review}`),
  ].join("");

  const safeChip = `<span class="shadow-chip ${safe ? "shadow-chip-ok" : "shadow-chip-warn"}">${safe ? "SÍ" : "NO"}</span>`;
  const blockersList = (dryRun.activation_blockers || []).length
    ? `<ul class="dryrun-blockers">${(dryRun.activation_blockers || [])
        .map((b) => `<li><span class="shadow-chip shadow-chip-warn">${escapeHtml(b)}</span></li>`)
        .join("")}</ul>`
    : `<span class="shadow-chip shadow-chip-ok">ninguno</span>`;

  const rows = (dryRun.matches || [])
    .map((m) => {
      const routeTag = m.would_route
        ? `<span class="shadow-tag shadow-tag-route">rutearía</span>`
        : `<span class="shadow-tag shadow-tag-block">mantiene actual</span>`;
      const pickCell = m.top_pick_changed
        ? `<span class="dryrun-pick-changed">${escapeHtml(pickLabel(m.dry_run_top_pick))}</span>`
        : escapeHtml(pickLabel(m.dry_run_top_pick));
      return `<tr>
        <td class="shadow-col-pos">${escapeHtml(m.position)}</td>
        <td class="shadow-col-match">${escapeHtml(m.home_team)} <span class="shadow-vs">vs</span> ${escapeHtml(m.away_team)}</td>
        <td><span class="shadow-rating">${escapeHtml(engineLabel(m.current_engine))}</span></td>
        <td><span class="shadow-rating">${escapeHtml(engineLabel(m.dry_run_engine))}</span></td>
        <td>${escapeHtml(pickLabel(m.current_top_pick))}</td>
        <td>${pickCell}</td>
        <td>${fmtDelta(m.max_abs_delta)}</td>
        <td>${routeTag}</td>
        <td class="shadow-blockers">${blockerChips(m.blockers)}</td>
      </tr>`;
    })
    .join("");

  return `
    <div class="shadow-panel dryrun-panel">
      <div class="shadow-toprow">
        <span class="shadow-badge dryrun-badge">DRY-RUN · NO ACTIVO</span>
      </div>
      <p class="dryrun-lead">Simula la activación controlada del gate. <strong>No modifica predicción, pick ni ticket.</strong></p>
      <div class="shadow-cards">
        <div class="shadow-card"><span class="shadow-card-label">Routing policy</span><span class="shadow-card-value mono">${escapeHtml(dryRun.activation_policy?.routing_policy || "—")}</span></div>
        <div class="shadow-card"><span class="shadow-card-label">Calibrator</span><span class="shadow-card-value mono">${escapeHtml(dryRun.activation_policy?.calibrator_candidate || "—")}</span></div>
        <div class="shadow-card"><span class="shadow-card-label">Temperature</span><span class="shadow-card-value">T=${escapeHtml(dryRun.activation_policy?.temperature ?? "—")}</span></div>
        <div class="shadow-card"><span class="shadow-card-label">Modelo dry-run</span><span class="shadow-card-value mono">${escapeHtml(dryRun.dry_run_probability_model || "—")}</span></div>
        <div class="shadow-card"><span class="shadow-card-label">Safe to activate</span><span class="shadow-card-value">${safeChip}</span></div>
        <div class="shadow-card"><span class="shadow-card-label">Posiciones que rutearían</span><span class="shadow-card-value">${formatPositionRanges(s.positions_would_route)}</span></div>
      </div>
      <div class="shadow-stats">${stats}</div>
      <div class="dryrun-activation">
        <span class="shadow-card-label">Activation blockers</span>
        ${blockersList}
      </div>
      <div class="shadow-table-wrap">
        <table class="shadow-table">
          <thead><tr><th>#</th><th>Partido</th><th>Motor actual</th><th>Motor dry-run</th><th>Pick actual</th><th>Pick dry-run</th><th>Δ prob máx</th><th>Estado</th><th>Blockers</th></tr></thead>
          <tbody>${rows}</tbody>
        </table>
      </div>
      <p class="meta-copy shadow-foot">Dry-run de activación: simulación en memoria con el calibrator candidate. No activa el gate, no cambia probabilidades reales, picks ni tickets. <strong>Safe to activate: ${safe ? "SÍ" : "NO"}</strong>.</p>
    </div>
  `;
}
