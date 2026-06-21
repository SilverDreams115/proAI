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

export function renderTeamRatingShadowPanel(shadow) {
  if (!shadow || !shadow.summary) {
    return `<div class="empty-state">Sin datos de team rating shadow para la papeleta activa.</div>`;
  }
  const s = shadow.summary;
  const total = s.total_matches;
  const run = shadow.active_rating_run || {};
  const candidate = shadow.calibrator_candidate;
  const gateOn = Boolean(shadow.gate_flag_enabled);

  const rows = [
    ["Modo", `${shadow.mode === "shadow_only" ? "Solo sombra" : shadow.mode} · gate ${gateOn ? "ON" : "OFF"}`],
    ["Active rating run", `${escapeHtml(run.algorithm_version || "—")} · ${run.snapshot_count ?? 0} snapshots`],
    [
      "Calibrator candidate",
      candidate
        ? `${escapeHtml(candidate.id)} · T=${candidate.temperature} · ${candidate.compatible ? "compatible" : "incompatible"}`
        : "—",
    ],
    ["Routing policy", escapeHtml(shadow.routing_policy || "—")],
    ["Eligible ahora", `${s.eligible_current}/${total}`],
    ["Eligible si se activa", `${s.eligible_if_enabled}/${total}`],
    ["Rutearía si se activa", `${s.would_use_rating_model_if_enabled}/${total}`],
    ["Bloqueadas por rating", `${s.blocked_by_rating}`],
    ["Bloqueadas por sanity", `${s.blocked_by_sanity}`],
    ["Bloqueadas por flag (OFF)", `${s.blocked_by_flag}`],
    ["Posiciones elegibles", formatPositionRanges(s.positions_eligible_if_enabled)],
    ["Posiciones que rutearían", formatPositionRanges(s.positions_would_route)],
  ];
  const grid = rows
    .map(
      ([k, v]) =>
        `<div class="shadow-row"><span class="shadow-key">${escapeHtml(k)}</span><span class="shadow-val">${v}</span></div>`,
    )
    .join("");

  const blockedPositions = ratingBlockedPositions(shadow);
  const ratingNote = blockedPositions.length
    ? `<p class="shadow-flag">Pos ${formatPositionRanges(blockedPositions)} bloqueada(s) por rating parcial / no-rating.</p>`
    : "";

  const matchRows = (shadow.matches || [])
    .map((m) => {
      const blockers = (m.blockers || []).length ? escapeHtml((m.blockers || []).join(", ")) : "—";
      return `<tr>
        <td>${escapeHtml(m.position)}</td>
        <td>${escapeHtml(m.home_team)} vs ${escapeHtml(m.away_team)}</td>
        <td>${escapeHtml(m.rating_status)}</td>
        <td><span class="shadow-tag ${matchTag(m)}">${escapeHtml(matchStateLabel(m))}</span></td>
        <td class="shadow-blockers">${blockers}</td>
      </tr>`;
    })
    .join("");

  return `
    <div class="shadow-summary">
      <span class="shadow-badge ${gateOn ? "shadow-on" : "shadow-off"}">Solo sombra · ${gateOn ? "ON" : "OFF"}</span>
      <div class="shadow-grid">${grid}</div>
      ${ratingNote}
    </div>
    <table class="shadow-table">
      <thead><tr><th>#</th><th>Partido</th><th>Rating</th><th>Estado</th><th>Blockers</th></tr></thead>
      <tbody>${matchRows}</tbody>
    </table>
    <p class="meta-copy">Diagnóstico shadow-only: no modifica la predicción actual, el pick ni el ticket. El gate productivo sigue <strong>OFF</strong>.</p>
  `;
}
