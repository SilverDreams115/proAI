// Seguimiento de quinielas — professional live dashboard + per-match
// postmortem comparison (prediction original vs real result).
//
// Pure render helpers (return HTML strings, no DOM/fetch) live up top so
// they can be locked with Vitest. The DOM/fetch wiring (initLiveTracking)
// sits at the bottom and is the only part that needs the browser.
import { formatPercent, escapeHtml } from "./helpers.js";

const STATUS_LABELS = {
  scheduled: "Pendiente",
  live: "En vivo",
  halftime: "Medio tiempo",
  full_time: "Final",
  postponed: "Pospuesto",
  cancelled: "Cancelado",
  unknown: "—",
};

export function liveStatusLabel(status) {
  return STATUS_LABELS[status] || "—";
}

// Color tone for a match row. A real draw is highlighted regardless of
// hit/miss; otherwise final → hit/miss, live → live, else → pending.
export function liveMatchTone(match) {
  if (!match) return "pending";
  if (match.is_final) {
    if (match.draw_was_real) return "draw";
    if (match.prediction_hit === true) return "hit";
    if (match.prediction_hit === false) return "miss";
    return "final";
  }
  if (match.is_live) return "live";
  return "pending";
}

export function formatScore(match) {
  if (match && match.home_goals != null && match.away_goals != null) {
    return `${match.home_goals}-${match.away_goals}`;
  }
  return "—";
}

export function hitLabel(match) {
  if (!match || !match.is_final || match.prediction_hit == null) return "—";
  return match.prediction_hit ? "Acierto" : "Fallo";
}

export function coverageCell(covered) {
  return covered
    ? '<span class="cover-yes">Sí</span>'
    : '<span class="cover-no">No</span>';
}

// Hit/miss/pending glyph for a ticket mode (simple/doubles/full).
function modeGlyph(hit) {
  if (hit === true) return '<span class="mode-hit">✓</span>';
  if (hit === false) return '<span class="mode-miss">✗</span>';
  return '<span class="mode-pending">·</span>';
}

const DIAGNOSIS_TONE = {
  "acierto": "hit",
  "fallo por empate": "draw",
  "fallo (salió local)": "miss",
  "fallo (salió visitante)": "miss",
  "fallo": "miss",
  "pendiente": "pending",
  "en vivo": "live",
};

export function diagnosisBadge(diagnosis) {
  const tone = DIAGNOSIS_TONE[diagnosis] || "pending";
  return `<span class="diag-badge diag-${tone}">${escapeHtml(diagnosis || "—")}</span>`;
}

// Three outcome chips (1/X/2): the original pick is outlined, the real
// result is filled (green if it matched the pick, red otherwise).
export function predictionChips(match) {
  const pick = match.predicted_outcome;
  const real = match.result_code;
  return ["1", "X", "2"]
    .map((code) => {
      const classes = ["oc-chip"];
      if (code === pick) classes.push("oc-pick");
      if (real && code === real) classes.push(match.prediction_hit ? "oc-real-hit" : "oc-real-miss");
      return `<span class="${classes.join(" ")}">${code}</span>`;
    })
    .join("");
}

function drawChips(match) {
  const risk = match.draw_risk;
  if (!risk) return "";
  if (risk.is_strong_draw) return '<span class="chip chip-draw-strong">Empate fuerte</span>';
  if (risk.is_live_draw) return '<span class="chip chip-draw-live">Empate vivo</span>';
  return "";
}

// Legacy compact row (kept for the live-results detail view + tests).
export function renderLiveMatchRow(match) {
  const tone = liveMatchTone(match);
  const pick = match.predicted_outcome ? escapeHtml(match.predicted_outcome) : "—";
  const minute = match.is_live && match.minute != null ? ` ${match.minute}'` : "";
  const realDraw = match.draw_was_real
    ? `<span class="badge badge-draw">Empate real · X ${match.draw_was_covered ? "cubierto" : "no cubierto"}</span>`
    : "";
  const modes = match.ticket_modes || {};
  const cover = (m) => coverageCell((modes[m]?.picks || []).includes("X"));
  return `
    <tr class="live-row tone-${tone}">
      <td class="lr-pos">${match.position}</td>
      <td class="lr-teams">
        ${escapeHtml(match.home_team_name)} vs ${escapeHtml(match.away_team_name)}
        ${drawChips(match)} ${realDraw}
      </td>
      <td class="lr-pick">${pick}</td>
      <td class="lr-pdraw">${formatPercent(match.draw_probability)}</td>
      <td class="lr-status"><span class="status-pill status-${tone}">${liveStatusLabel(match.status)}${minute}</span></td>
      <td class="lr-score">${formatScore(match)}</td>
      <td class="lr-hit">${hitLabel(match)}</td>
      <td class="lr-cov">S ${cover("simple")} · D ${cover("doubles")} · F ${cover("full")}</td>
    </tr>`;
}

export function renderLiveSlateDetail(data) {
  if (!data) return "";
  const counts = `${data.completed_count}/${data.match_count} finalizados` +
    (data.live_count ? ` · ${data.live_count} en vivo` : "") +
    (data.pending_count ? ` · ${data.pending_count} pendientes` : "");
  const rows = (data.matches || []).map(renderLiveMatchRow).join("");
  return `
    <div class="live-detail">
      <div class="live-detail-head">
        <h3>${escapeHtml(data.draw_code)} · ${data.week_type === "midweek" ? "Media Semana" : "Weekend"}</h3>
        <p class="meta-copy">${counts}</p>
      </div>
      <table class="live-table">
        <thead><tr>
          <th>#</th><th>Partido</th><th>Pred</th><th>p(X)</th>
          <th>Estado</th><th>Marcador</th><th>Resultado</th><th>Cobertura X</th>
        </tr></thead>
        <tbody>${rows}</tbody>
      </table>
    </div>`;
}

// ---- Postmortem comparison (prediction original vs real) -----------------

export function renderComparisonRow(match) {
  const tone = liveMatchTone(match);
  const realChip = (() => {
    if (match.is_pending) return '<span class="status-pill status-pending">Pendiente</span>';
    if (match.is_live) {
      const min = match.minute != null ? ` ${match.minute}'` : "";
      return `<span class="status-pill status-live">En vivo${min}</span>`;
    }
    return match.result_code ? `<span class="oc-chip oc-real-${match.prediction_hit ? "hit" : "miss"}">${escapeHtml(match.result_code)}</span>` : "—";
  })();
  return `
    <tr class="cmp-row tone-${tone}">
      <td class="mono">${match.position}</td>
      <td class="cmp-teams">${escapeHtml(match.home_team_name)} <span class="vs">vs</span> ${escapeHtml(match.away_team_name)}${drawChips(match)}</td>
      <td class="cmp-pred">${predictionChips(match)}</td>
      <td class="cmp-score mono">${formatScore(match)} ${realChip}</td>
      <td class="cmp-mode">${modeGlyph(match.simple_hit)}</td>
      <td class="cmp-mode">${modeGlyph(match.doubles_hit)}</td>
      <td class="cmp-mode">${modeGlyph(match.full_hit)}</td>
      <td>${diagnosisBadge(match.diagnosis)}</td>
    </tr>`;
}

export function renderComparisonDetail(data) {
  if (!data) return "";
  const typeLabel = data.week_type === "midweek" ? "Media Semana" : "Weekend";
  const s = data.score || {};
  const snap = data.original_snapshot || {};
  const snapLine = snap.snapshot_id
    ? `Comparado contra el ticket original <span class="mono">${escapeHtml(String(snap.snapshot_id).slice(0, 8))}</span>` +
      (snap.generated_at ? ` · ${escapeHtml(String(snap.generated_at).slice(0, 16).replace("T", " "))}` : "")
    : "Sin snapshot original";

  // Demo / unverified slates are never scored as real concursos.
  if (data.comparable === false) {
    return `
      <div class="cmp-detail">
        <div class="cmp-head">
          <h3>${escapeHtml(data.draw_code)} · ${typeLabel} ${classificationBadge(data.classification, data.comparable)}</h3>
        </div>
        <div class="not-comparable">
          <p class="nc-title">No comparable: slate ${data.classification === "synthetic_demo" ? "demo / sintética" : "sin fuente oficial"}</p>
          <ul class="er-list">
            ${(data.classification_reasons || []).map((r) => `<li>${escapeHtml(r)}</li>`).join("")}
            ${data.competitions && data.competitions.length ? `<li>Competencias: ${escapeHtml(data.competitions.join(", "))}</li>` : ""}
          </ul>
          <p class="meta-copy subtle">No se calcula score oficial para datos demo. Promueve una boleta real desde la guía oficial LN para comparar.</p>
        </div>
      </div>`;
  }

  if (!data.results_ingested) {
    return `
      <div class="cmp-detail">
        <div class="cmp-head">
          <h3>${escapeHtml(data.draw_code)} · ${typeLabel}</h3>
          <p class="meta-copy">${snapLine}</p>
        </div>
        ${renderEmptyResults(data)}
      </div>`;
  }

  const counts = `${data.completed_count}/${data.match_count} finalizados` +
    (data.live_count ? ` · ${data.live_count} en vivo` : "") +
    (data.pending_count ? ` · ${data.pending_count} pendientes` : "");
  const drawLine = `Empates reales ${s.empates_reales_hasta_ahora ?? 0} vs esperados ${(s.empates_esperados ?? 0).toFixed ? (s.empates_esperados).toFixed(1) : s.empates_esperados}`;
  const rows = (data.matches || []).map(renderComparisonRow).join("");
  return `
    <div class="cmp-detail">
      <div class="cmp-head">
        <h3>${escapeHtml(data.draw_code)} · ${typeLabel}</h3>
        <p class="meta-copy">${counts}${data.is_complete ? " · Completa" : ""} · ${drawLine}</p>
        <p class="meta-copy subtle">${snapLine}</p>
      </div>
      <div class="cmp-scoreline">
        <span><strong>${s.simple_hits ?? 0}</strong> simple</span>
        <span><strong>${s.doubles_hits ?? 0}</strong> dobles</span>
        <span><strong>${s.full_hits ?? 0}</strong> full</span>
        <span><strong>${s.max_possible_hits ?? 0}</strong> máx posible</span>
      </div>
      <table class="cmp-table">
        <thead><tr>
          <th>#</th><th>Partido</th><th>Predicción</th><th>Resultado</th>
          <th title="Simple">S</th><th title="Dobles">D</th><th title="Full">F</th><th>Diagnóstico</th>
        </tr></thead>
        <tbody>${rows}</tbody>
      </table>
    </div>`;
}

export function renderEmptyResults(data) {
  const source = "Lotería Nacional · Progol Resultados";
  return `
    <div class="empty-results">
      <p class="er-title">Sin resultados ingeridos todavía</p>
      <ul class="er-list">
        <li>Fuente revisada: <strong>${escapeHtml(source)}</strong></li>
        <li>Partidos: <span class="mono">${data.completed_count || 0}/${data.match_count}</span> finalizados</li>
        <li>Acción: pegar el acta oficial en <span class="mono">POST /api/slates/${escapeHtml(data.slate_id)}/ingest-results</span></li>
      </ul>
      <p class="meta-copy subtle">No se inventan marcadores: la comparación aparece en cuanto se ingieren resultados reales.</p>
    </div>`;
}

// ---- Classification (official vs demo) -----------------------------------

const CLASSIFICATION_LABELS = {
  official_real: "Oficial",
  official_but_no_results_yet: "Oficial · sin resultados",
  synthetic_demo: "Demo — no comparable",
  stale_archived: "Archivada",
  unverified: "Sin fuente oficial",
};

export function classificationLabel(classification) {
  return CLASSIFICATION_LABELS[classification] || "Sin verificar";
}

export function classificationBadge(classification, comparable) {
  if (classification === "official_real" || classification === "official_but_no_results_yet") {
    return `<span class="class-badge class-official">${escapeHtml(classificationLabel(classification))}</span>`;
  }
  const tone = classification === "synthetic_demo" ? "demo" : "unverified";
  return `<span class="class-badge class-${tone}">${escapeHtml(classificationLabel(classification))}</span>`;
}

// ---- Dashboard -----------------------------------------------------------

function statusClass(label) {
  return "st-" + String(label || "").toLowerCase().normalize("NFD").replace(/[^a-z]/g, "");
}

export function renderDashboardEntry(entry) {
  const hitRate = entry.current_hit_rate == null ? "—" : formatPercent(entry.current_hit_rate);
  const hasResults = entry.completed_count > 0 || entry.live_count > 0;
  const stats = hasResults
    ? `<span class="tc-pill">S ${entry.simple_hits} · D ${entry.doubles_hits} · F ${entry.full_hits}</span>
       <span class="tc-draws">${entry.empates_reales} emp · esp ${entry.empates_esperados.toFixed(1)}</span>`
    : `<span class="tc-pending">Sin resultados aún</span>`;
  const demo = entry.comparable === false;
  return `
    <article class="track-card ${statusClass(entry.status_label)} ${demo ? "is-demo" : ""}" data-slate="${escapeHtml(entry.slate_id)}">
      <div class="tc-top">
        <span class="track-code mono">${escapeHtml(entry.draw_code)}</span>
        <span class="track-type">${entry.week_type === "midweek" ? "MS" : "WK"}</span>
        <span class="track-status">${escapeHtml(entry.status_label)}</span>
      </div>
      <div class="tc-class">${classificationBadge(entry.classification, entry.comparable)}</div>
      <div class="tc-progress">
        <strong>${entry.completed_count}/${entry.match_count}</strong> finalizados
        ${entry.live_count ? `· <span class="tc-live">${entry.live_count} en vivo</span>` : ""}
      </div>
      <div class="tc-stats">${stats}</div>
      <button class="track-detail-btn" data-slate="${escapeHtml(entry.slate_id)}">Ver comparación</button>
    </article>`;
}

function fmtUpdated(value) {
  if (!value) return "—";
  return String(value).slice(11, 16) || String(value).slice(0, 16);
}

export function renderSummaryBar(data) {
  const closed = data.closed || [];
  const open = data.open || [];
  const all = closed.concat(open);
  const closedWithResults = closed.filter((e) => (e.completed_count || 0) + (e.live_count || 0) > 0).length;
  const pending = all.reduce((t, e) => t + (e.pending_count || 0), 0);
  const draws = all.reduce((t, e) => t + (e.empates_reales || 0), 0);
  const lastUpdated = all.map((e) => e.last_updated_at).filter(Boolean).sort().slice(-1)[0];
  const cell = (value, label) => `<div class="ts-cell"><strong>${value}</strong><span>${label}</span></div>`;
  return `
    <div class="track-summary">
      ${cell(closedWithResults, "cerradas con resultados")}
      ${cell(open.length, "abiertas en seguimiento")}
      ${cell(pending, "resultados pendientes")}
      ${cell(draws, "empates detectados")}
      ${cell(fmtUpdated(lastUpdated), "última actualización")}
    </div>`;
}

export function renderLiveDashboard(data) {
  if (!data) return "";
  // Separate real concursos from demo/synthetic ones so demo data is
  // never presented as a real quiniela. Cerrada/Abierta is still conveyed
  // by each card's status badge.
  const all = (data.closed || []).concat(data.open || []);
  const real = all.filter((e) => e.comparable === true);
  const demo = all.filter((e) => e.comparable !== true);
  const group = (title, entries, demoGroup) => `
    <div class="track-group ${demoGroup ? "track-group-demo" : ""}">
      <h3>${title}</h3>
      <div class="track-grid">${(entries || []).map(renderDashboardEntry).join("") || '<p class="meta-copy">Sin quinielas.</p>'}</div>
    </div>`;
  return `
    <div class="live-tracking">
      <div class="lt-header"><h2>Seguimiento de quinielas</h2></div>
      ${renderSummaryBar(data)}
      ${group("Quinielas reales", real, false)}
      ${group("Demo / no comparable", demo, true)}
    </div>`;
}

// ---- DOM wiring (browser only) -------------------------------------------

export function initLiveTracking({ container, detailContainer, fetchJson }) {
  if (!container || typeof fetchJson !== "function") return;

  // Tracking is a SECONDARY, best-effort feature. `fetchJson` returns null
  // on any non-OK response — most commonly a 401 when the session lapses, or
  // a transient network blip — not a real outage. So a null result must NOT
  // render as a hard "error-copy" that looks like the whole app crashed.
  // Instead we show a small, dismissible notice with a Reintentar action.
  function softNotice(targetContainer, label, onRetry) {
    targetContainer.innerHTML = `
      <div class="soft-notice" role="status">
        <span>${label}</span>
        <button type="button" class="track-retry">Reintentar</button>
      </div>`;
    const button =
      typeof targetContainer.querySelector === "function"
        ? targetContainer.querySelector(".track-retry")
        : null;
    if (button) button.addEventListener("click", onRetry);
  }

  async function showDetail(slateId) {
    if (!detailContainer) return;
    try {
      const data = await fetchJson(`/slates/${slateId}/result-comparison`);
      if (!data) throw new Error("empty");
      detailContainer.innerHTML = renderComparisonDetail(data);
      detailContainer.scrollIntoView({ behavior: "smooth", block: "nearest" });
    } catch (err) {
      softNotice(detailContainer, "Detalle de seguimiento no disponible", () => showDetail(slateId));
    }
  }

  async function refresh() {
    try {
      const data = await fetchJson("/slates/live/dashboard");
      if (!data) throw new Error("unavailable");
      const open = Array.isArray(data.open) ? data.open : [];
      const closed = Array.isArray(data.closed) ? data.closed : [];
      if (open.length === 0 && closed.length === 0) {
        // OK response, just nothing to track yet — not an error.
        container.innerHTML = `<p class="mini-copy">Sin partidos en seguimiento por ahora.</p>`;
        return;
      }
      container.innerHTML = renderLiveDashboard(data);
      container.querySelectorAll(".track-detail-btn").forEach((btn) => {
        btn.addEventListener("click", () => showDetail(btn.dataset.slate));
      });
    } catch (err) {
      softNotice(container, "Seguimiento no disponible", () => refresh());
    }
  }

  refresh();
  return { refresh, showDetail };
}
