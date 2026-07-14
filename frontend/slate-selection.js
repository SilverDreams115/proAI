// Slate selection stability (R5.6 hotfix) — pure, testable.
//
// With multiple active slates, the heart-beat must NOT pull the user off their
// manual selection just because /slates/active reports a different (more
// urgent) slate. Selection only changes when the selected slate disappears
// from the active list (closed/archived).

export function resolveActiveSelection({ selectedId, slates, activeMeta } = {}) {
  const list = Array.isArray(slates) ? slates : [];
  const stillActive = Boolean(selectedId) && list.some((s) => s && s.id === selectedId);
  if (stillActive) {
    return { selectedId, switched: false, next: list.find((s) => s.id === selectedId) || null };
  }
  const metaId = activeMeta && activeMeta.slate ? activeMeta.slate.id : null;
  const next = (metaId && list.find((s) => s && s.id === metaId)) || list[0] || null;
  return { selectedId: next ? next.id : null, switched: true, next };
}

// A slate is only *truly* missing predictions when nothing can be shown: no
// persisted rows, no live availability and no matches to score. An active slate
// with matches is "live_available", never a false "Sin predicción".
export function isTrulyMissingPrediction(slate) {
  if (!slate) return true;
  if (slate.has_predictions) return false;
  if (slate.live_prediction_available) return false;
  return Number(slate.match_count || 0) === 0;
}

export function selectedSlateCountdownMs(slates, selectedId) {
  const list = Array.isArray(slates) ? slates : [];
  const selected = list.find((s) => s && s.id === selectedId);
  if (selected && selected.registration_closes_at) {
    return new Date(selected.registration_closes_at).getTime();
  }
  return null;
}

// Selector source of truth from GET /api/slates/visible. Open official slates
// drive the list; when none are open we fall back to the recent (read-only)
// ones so the UI is never empty. A saved manual selection wins only if it is
// still in the visible set. Pure + testable: no DOM, no fetch.
export function resolveVisibleSelection({ visible, savedId } = {}) {
  const v = visible || {};
  const open = Array.isArray(v.open_slates) ? v.open_slates : [];
  const recent = Array.isArray(v.recent_slates) ? v.recent_slates : [];
  const slates = [...open, ...recent];
  const reason = v.reason
    || (open.length ? "open_slate" : recent.length ? "fallback_recent" : "no_official_slates");
  const savedStillVisible = Boolean(savedId) && slates.some((s) => s && s.id === savedId);
  const selectedId = savedStillVisible
    ? savedId
    : (v.selected_default_slate_id || slates[0]?.id || null);
  const selected = slates.find((s) => s && s.id === selectedId) || null;
  return {
    slates,
    selectedId,
    reason,
    readOnly: Boolean(selected && selected.read_only),
    message: visibleSelectionMessage(reason),
    isEmpty: slates.length === 0,
  };
}

export function visibleSelectionMessage(reason) {
  if (reason === "fallback_recent") {
    return "No hay boleta abierta. Mostrando la más reciente en solo lectura.";
  }
  if (reason === "no_official_slates") {
    return "No hay boletas oficiales cargadas.";
  }
  return "";
}

// Maps a slate's backend flags to the operator-facing badges, in priority
// order. "Solo lectura" is appended for any closed/archived slate.
export function slateBadges(slate) {
  if (!slate) return [];
  const badges = [];
  if (!slate.is_closed && !slate.is_archived) {
    badges.push("Abierta");
  } else if (slate.classification === "official_real" || slate.has_results) {
    badges.push("Completa");
  } else {
    badges.push("Cerrada");
  }
  if ((slate.is_closed || slate.is_archived) && !slate.has_results) {
    badges.push("Sin resultados");
  }
  if (slate.read_only) badges.push("Solo lectura");
  // Date Sanity Gate: a suspect/stale date is flagged distinctly so an
  // operator never mistakes it for a playable boleta.
  if (slate.date_suspect || (slate.date_status && slate.date_status !== "date_valid")) {
    badges.push("Fecha sospechosa");
  }
  return badges;
}

// Discovery diagnostics: slates held back by the date gate, for the empty/
// diagnostics view. Returns a list of { draw_code, date_status, reason }.
export function suspectSlateDiagnostics(visible) {
  const disc = (visible && visible.discovery) || {};
  const suspect = Array.isArray(disc.suspect_slates) ? disc.suspect_slates : [];
  return suspect.map((s) => ({
    draw_code: s.draw_code,
    week_type: s.week_type,
    date_status: s.date_status,
    reason: (s.reasons && s.reasons[0]) || "",
    // PDF provenance + why the cierre was rejected (source of truth audit).
    fixture_draw_code: s.extracted_fixture_draw_code || null,
    match_count: s.match_count ?? null,
    rejected_close_block_draw_code: s.rejected_close_block_draw_code || null,
    rejected_close_year: s.rejected_close_year || null,
    source_url: s.source_url || null,
    pdf_sha256: s.pdf_sha256 || null,
    playable: false,
    action:
      s.recommended_action ||
      "Esperar PDF corregido de LN o confirmar fecha oficial con evidencia.",
  }));
}

// MS PDF watcher status line for diagnostics. Returns null when no watcher
// data is present yet.
export function msPdfWatchStatus(visible) {
  const d = (visible && visible.discovery) || {};
  if (!d.last_ms_pdf_checked_at && !d.last_ms_pdf_status) return null;
  const STATUS_LABEL = {
    unchanged: "PDF sin cambios",
    changed_invalid: "PDF actualizado · cierre aún inválido",
    changed_valid: "PDF actualizado · MS activada",
    parse_error: "PDF ilegible",
  };
  const cand = d.current_ms_candidate || {};
  const shaShort = d.last_ms_pdf_sha256 ? String(d.last_ms_pdf_sha256).slice(0, 8) : null;
  let detail = "";
  if (d.last_ms_pdf_status === "changed_invalid" || cand.date_status === "source_invalid") {
    detail = "LN aún publica el cierre del concurso 800.";
  } else if (d.last_ms_pdf_status === "changed_valid" || cand.date_status === "date_valid") {
    detail = "MS activada desde PDF oficial.";
  }
  return {
    checked_at: d.last_ms_pdf_checked_at || null,
    status: d.last_ms_pdf_status || null,
    status_label: STATUS_LABEL[d.last_ms_pdf_status] || "Sin revisión registrada",
    sha_short: shaShort,
    candidate: cand.draw_code || null,
    candidate_status: cand.date_status || null,
    activation_status: cand.activation_status || null,
    detail,
    action: d.ms_pdf_recommended_action || null,
  };
}

// Human lines for the "detected but not playable" PDF-source case, e.g. PGM-802.
export function pdfSourceDiagnosticLines(entry) {
  if (!entry) return [];
  const lines = [];
  if (entry.fixture_draw_code) {
    lines.push(`Detectada desde PDF oficial (concurso ${entry.fixture_draw_code})`);
  }
  if (entry.match_count) {
    lines.push(`Fixtures válidos (${entry.match_count} partidos)`);
  }
  if (entry.date_status === "source_invalid" && entry.rejected_close_block_draw_code) {
    lines.push("Cierre de venta no válido en PDF");
    lines.push(
      `Bloque de cierre detectado pertenece al Concurso ${entry.rejected_close_block_draw_code}` +
        (entry.rejected_close_year ? ` (${entry.rejected_close_year})` : ""),
    );
  } else if (entry.date_status === "needs_official_pdf_date") {
    lines.push("PDF sin bloque de cierre del concurso correcto");
  }
  lines.push("No jugable hasta que LN publique la fecha correcta");
  return lines;
}
