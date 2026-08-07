// Which of the three top-level screens the app should be showing.
//
// Pure decision (state in, screen name out) so the rule can be locked with
// Vitest, same shape as ticket-decision.js — app.js only applies the answer
// to `hidden` attributes. The dashboard previously drew its tab bar and empty
// panels for unauthenticated visitors, an application-shaped outline with no
// data behind it; the gate exists to stop that, and this is the single place
// that decides when it lifts.

export const SCREEN_GATE = "gate";
export const SCREEN_LOADING = "loading";
export const SCREEN_DASHBOARD = "dashboard";

/**
 * @param {{authenticated?: boolean, isLoading?: boolean, slates?: unknown[]}} state
 * @returns {"gate" | "loading" | "dashboard"}
 */
export function selectAuthScreen(state) {
  if (!state?.authenticated) return SCREEN_GATE;
  // "Still booting" is authenticated with nothing to draw yet — the slate
  // list is what every panel is built from. Keying off `isLoading` alone
  // would blank a dashboard the operator is already reading every time an
  // ordinary in-place refresh runs.
  const hasSlates = Array.isArray(state.slates) && state.slates.length > 0;
  if (state.isLoading && !hasSlates) return SCREEN_LOADING;
  return SCREEN_DASHBOARD;
}
