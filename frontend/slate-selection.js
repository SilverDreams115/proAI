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
