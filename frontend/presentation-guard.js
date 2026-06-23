// R5.6-D presentation guard (pure, testable).
//
// Decides whether a prediction may be shown as a simple, playable suggestion.
// Prefers the backend `presentation_guard`; falls back to deriving the same
// contract from the sanity fields when an older payload lacks it. Returns a
// plain object (no DOM/HTML) so it can be locked with Vitest and reused by
// app.js to render a non-contradictory recommendation.

export const SIGNAL_LABEL = { L: "L · Local", E: "E · Empate", V: "V · Visitante" };

export function presentationGuardOf(pred) {
  if (pred && pred.presentation_guard && typeof pred.presentation_guard === "object") {
    return pred.presentation_guard;
  }
  const strategy = String((pred && pred.ticket_strategy) || "").toUpperCase();
  const risk = String((pred && pred.risk_level) || "high").toLowerCase();
  const status = String((pred && pred.final_status) || "").toUpperCase();
  const flags = Array.isArray(pred && pred.flags) ? pred.flags : [];
  const simpleAllowed =
    strategy === "SIMPLE" &&
    risk !== "high" &&
    status !== "REVISAR" &&
    status !== "BLOQUEADO" &&
    !flags.includes("SUSPICIOUS_CLASS_PROBABILITY");
  const code = String((pred && pred.recommended_outcome) || "");
  const primary = ({ "1": "L", X: "E", "2": "V" })[code] || code;
  return {
    simple_allowed: simpleAllowed,
    primary_signal: primary,
    recommendation_label:
      status === "BLOQUEADO" ? "BLOQUEADO" : simpleAllowed ? "SIMPLE" : "NO SIMPLE",
    risk_level: risk,
    confidence: (pred && pred.visible_confidence) || "baja",
    reason: [],
  };
}

export function signalLabel(letter) {
  return SIGNAL_LABEL[letter] || letter || "—";
}
