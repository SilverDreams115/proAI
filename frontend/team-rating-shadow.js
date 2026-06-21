// R5.4 — Team Rating Shadow diagnostic (read-only, shadow-only).
//
// Pure render helpers (return HTML strings, no DOM/fetch) so they can be
// locked with Vitest. The DOM wiring lives in app.js and only sets innerHTML.
// This panel is a projection of the inactive team-rating gate: it never
// changes predictions, picks, tickets or probabilities.
import { escapeHtml } from "./helpers.js";

const SHADOW_RATING_BLOCKERS = new Set([
  "rating_not_present",
  "not_both_medium_plus",
  "home_confidence_too_low",
  "away_confidence_too_low",
]);

export function formatPositionRanges(positions) {
  const sorted = [...new Set((positions || []).map(Number))]
    .filter((n) => Number.isFinite(n))
    .sort((a, b) => a - b);
  if (!sorted.length) return "—";
  const parts = [];
  let start = sorted[0];
  let prev = sorted[0];
  for (let i = 1; i <= sorted.length; i += 1) {
    const current = sorted[i];
    if (current === prev + 1) {
      prev = current;
      continue;
    }
    parts.push(start === prev ? `${start}` : `${start}–${prev}`);
    start = current;
    prev = current;
  }
  return parts.join(", ");
}

export function ratingBlockedPositions(shadow) {
  return (shadow?.matches || [])
    .filter(
      (m) =>
        !m.eligible_if_enabled &&
        (m.blockers || []).some((b) => SHADOW_RATING_BLOCKERS.has(b)),
    )
    .map((m) => m.position);
}

function matchStateLabel(m) {
  if (m.eligible_current) return "elegible ahora";
  if (m.eligible_if_enabled) {
    return m.would_use_rating_model_if_enabled ? "rutearía si ON" : "elegible si ON";
  }
  return "bloqueada";
}

function matchTag(m) {
  if (m.eligible_if_enabled) {
    return m.would_use_rating_model_if_enabled ? "shadow-tag-route" : "shadow-tag-elig";
  }
  return "shadow-tag-block";
}

function metaCard(label, value, { mono = false, title = "" } = {}) {
  const valueClass = mono ? "shadow-card-value mono" : "shadow-card-value";
  const titleAttr = title ? ` title="${escapeHtml(title)}"` : "";
  return `<div class="shadow-card">
    <span class="shadow-card-label">${escapeHtml(label)}</span>
    <span class="${valueClass}"${titleAttr}>${value}</span>
  </div>`;
}

function statCard(label, value, tone = "") {
  const toneClass = tone ? ` ${tone}` : "";
  return `<div class="shadow-stat${toneClass}">
    <span class="shadow-stat-value">${escapeHtml(value)}</span>
    <span class="shadow-stat-label">${escapeHtml(label)}</span>
  </div>`;
}

function blockerChips(blockers) {
  if (!blockers || !blockers.length) {
    return `<span class="shadow-chip shadow-chip-ok">—</span>`;
  }
  return blockers
    .map((b) => `<span class="shadow-chip">${escapeHtml(b)}</span>`)
    .join("");
}

export function renderTeamRatingShadowPanel(shadow) {
  if (!shadow || !shadow.summary) {
    return `<div class="empty-state">Sin datos de team rating shadow para la papeleta activa.</div>`;
  }
  const s = shadow.summary;
  const total = s.total_matches;
  const run = shadow.active_rating_run || {};
  const candidate = shadow.calibrator_candidate;
  const gateOn = Boolean(shadow.gate_flag_enabled);

  const statusTone = candidate
    ? (candidate.compatible ? "shadow-chip-ok" : "shadow-chip-warn")
    : "";
  const statusValue = candidate
    ? `<span class="shadow-chip ${statusTone}">${candidate.compatible ? "compatible" : "incompatible"}</span>`
    : "—";

  const metaCards = [
    metaCard("Modo", `${shadow.mode === "shadow_only" ? "Solo sombra" : escapeHtml(shadow.mode)} · gate ${gateOn ? "ON" : "OFF"}`),
    metaCard("Active run", `${escapeHtml(run.algorithm_version || "—")} · ${run.snapshot_count ?? 0} snapshots`),
    metaCard(
      "Calibrator",
      candidate ? escapeHtml(candidate.id) : "—",
      { mono: true, title: candidate ? candidate.id : "" },
    ),
    metaCard("Temperature", candidate ? `T=${escapeHtml(candidate.temperature)}` : "—"),
    metaCard("Calibrator status", statusValue),
    metaCard("Routing policy", escapeHtml(shadow.routing_policy || "—"), {
      mono: true,
      title: shadow.routing_policy || "",
    }),
  ].join("");

  const statCards = [
    statCard("Eligible ahora", `${s.eligible_current}/${total}`),
    statCard("Eligible si se activa", `${s.eligible_if_enabled}/${total}`, "shadow-stat-good"),
    statCard("Rutearía si se activa", `${s.would_use_rating_model_if_enabled}/${total}`, "shadow-stat-good"),
    statCard("Bloqueadas por rating", `${s.blocked_by_rating}`),
    statCard("Bloqueadas por sanity", `${s.blocked_by_sanity}`),
    statCard("Bloqueadas por flag", `${s.blocked_by_flag}`),
  ].join("");

  const positions = `
    <div class="shadow-positions">
      <div class="shadow-positions-item"><span class="shadow-card-label">Posiciones elegibles</span><span class="shadow-positions-value">${formatPositionRanges(s.positions_eligible_if_enabled)}</span></div>
      <div class="shadow-positions-item"><span class="shadow-card-label">Posiciones que rutearían</span><span class="shadow-positions-value">${formatPositionRanges(s.positions_would_route)}</span></div>
    </div>`;

  const blockedPositions = ratingBlockedPositions(shadow);
  const ratingNote = blockedPositions.length
    ? `<div class="shadow-alert" role="status">Pos ${formatPositionRanges(blockedPositions)} bloqueada(s) por rating parcial / no-rating.</div>`
    : "";

  const matchRows = (shadow.matches || [])
    .map((m) => {
      return `<tr>
        <td class="shadow-col-pos">${escapeHtml(m.position)}</td>
        <td class="shadow-col-match">${escapeHtml(m.home_team)} <span class="shadow-vs">vs</span> ${escapeHtml(m.away_team)}</td>
        <td><span class="shadow-rating">${escapeHtml(m.rating_status)}</span></td>
        <td><span class="shadow-tag ${matchTag(m)}">${escapeHtml(matchStateLabel(m))}</span></td>
        <td class="shadow-blockers">${blockerChips(m.blockers)}</td>
      </tr>`;
    })
    .join("");

  return `
    <div class="shadow-panel">
      <div class="shadow-toprow">
        <span class="shadow-badge ${gateOn ? "shadow-on" : "shadow-off"}">Solo sombra · ${gateOn ? "ON" : "OFF"}</span>
      </div>
      <div class="shadow-cards">${metaCards}</div>
      <div class="shadow-stats">${statCards}</div>
      ${positions}
      ${ratingNote}
      <div class="shadow-table-wrap">
        <table class="shadow-table">
          <thead><tr><th>#</th><th>Partido</th><th>Rating</th><th>Estado</th><th>Blockers</th></tr></thead>
          <tbody>${matchRows}</tbody>
        </table>
      </div>
      <p class="meta-copy shadow-foot">Diagnóstico shadow-only: no modifica la predicción actual, el pick ni el ticket. El gate productivo sigue <strong>OFF</strong>.</p>
    </div>
  `;
}
