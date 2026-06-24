// R6.3 — Per-slate cache for the heavy diagnostic panels.
//
// The prediction board must render without waiting on Money Mode, the ticket
// canary dry-run, the team-rating diagnostics or the external-results probe.
// Those payloads are fetched lazily and cached here keyed by slate_id, so
// switching back to a slate the operator already opened is instant and never
// refetches. Pure in-memory; holds no DOM and writes nothing.
const _cache = new Map();

export function getCachedDiagnostics(slateId) {
  if (!slateId) return null;
  return _cache.get(slateId) || null;
}

export function setCachedDiagnostics(slateId, payload) {
  if (!slateId) return;
  _cache.set(slateId, payload);
}

export function hasCachedDiagnostics(slateId) {
  return Boolean(slateId) && _cache.has(slateId);
}

export function clearDiagnosticsCache(slateId) {
  if (slateId) {
    _cache.delete(slateId);
  } else {
    _cache.clear();
  }
}
