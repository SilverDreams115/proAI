// The dashboard used to render its tab bar and empty panels to anyone who
// opened the page, logged in or not — an application-shaped outline with no
// data behind it, and a cramped password field wedged into the masthead
// toolbar beside it. These tests pin the three-screen rule that replaced it:
// gate, first-paint loader, dashboard — exactly one at a time.
//
// The markup and the state rule are asserted separately because they fail
// for different reasons: the first breaks when someone edits index.html, the
// second when someone edits the toggle in app.js.

import { describe, expect, it } from "vitest";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";

import {
  SCREEN_DASHBOARD,
  SCREEN_GATE,
  SCREEN_LOADING,
  selectAuthScreen,
} from "../auth-screens.js";

const html = readFileSync(resolve(process.cwd(), "index.html"), "utf-8");

// The gate as a browser would build it, so assertions can read rendered text
// and real attributes instead of matching substrings in the source.
function parseGate() {
  const doc = new DOMParser().parseFromString(html, "text/html");
  const gate = doc.getElementById("auth-gate");
  if (!gate) throw new Error("index.html has no #auth-gate");
  return gate;
}
const appSource = readFileSync(resolve(process.cwd(), "app.js"), "utf-8");
const clientSource = readFileSync(resolve(process.cwd(), "api-client.js"), "utf-8");

describe("auth gate markup", () => {
  it("ships a dedicated gate, loader and collapsible body", () => {
    expect(html).toContain('id="auth-gate"');
    expect(html).toContain('id="app-loading"');
    expect(html).toContain('id="app-body"');
  });

  it("keeps the login form inside the gate, not the masthead toolbar", () => {
    const gateStart = html.indexOf('id="auth-gate"');
    const gateEnd = html.indexOf('id="app-loading"');
    const gate = html.slice(gateStart, gateEnd);
    expect(gate).toContain('id="login-form"');
    expect(gate).toContain('id="auth-password"');
    expect(gate).toContain('id="auth-error"');
  });

  it("names nothing on the gate — no product, no tagline, no purpose", () => {
    // Asserted on rendered text, not on markup: comments explain the intent
    // and class names describe the artifact, and neither is on screen. An
    // earlier version of this test read the raw HTML and failed the moment
    // the decorative grid arrived carrying `boleta` in its class names.
    const gate = parseGate();
    const visible = gate.textContent.replace(/\s+/g, " ").trim();
    for (const word of ["ProAI", "proAI", "Quiniela", "quiniela", "Progol", "boleta", "predicci"]) {
      expect(visible).not.toContain(word);
    }
    // What is left: the card's three marks (adjacent spans, so no space
    // between them), the field label, and the submit verb. The placeholder
    // is an attribute, not text content.
    expect(visible).toBe("1X2 Password Entrar");
  });

  it("keeps the decorative grid out of the accessibility tree", () => {
    const svg = parseGate().querySelector(".auth-boleta");
    expect(svg).not.toBeNull();
    expect(svg.getAttribute("aria-hidden")).toBe("true");
    expect(svg.getAttribute("focusable")).toBe("false");
  });

  it("draws every mark on the boleta lattice", () => {
    // 27px pitch, 0.5 offset, three columns, fourteen rows. A mark off the
    // grid reads as a rendering bug rather than a filled-in position.
    const marks = [...parseGate().querySelectorAll(".boleta-mark")];
    expect(marks.length).toBeGreaterThan(0);
    for (const mark of marks) {
      const x = Number(mark.getAttribute("x"));
      const y = Number(mark.getAttribute("y"));
      expect((x - 0.5) % 27).toBe(0);
      expect((y - 0.5) % 27).toBe(0);
      expect(x).toBeLessThanOrEqual(54.5);
      expect(y).toBeLessThanOrEqual(351.5);
    }
  });

  it("still gives the login form an accessible name", () => {
    // Removing every visible label must not leave the form unnamed to a
    // screen reader.
    expect(html).toMatch(/id="login-form"[^>]*aria-label=/);
  });

  it("hides the masthead until a session exists", () => {
    expect(html).toMatch(/class="masthead" id="masthead" hidden/);
    expect(appSource).toMatch(/masthead\.hidden = screen === SCREEN_GATE/);
  });

  it("announces the loader to assistive tech and errors as alerts", () => {
    expect(html).toMatch(/id="app-loading"[^>]*aria-live="polite"/);
    expect(html).toMatch(/id="auth-error"[^>]*role="alert"/);
  });
});

describe("header controls", () => {
  it("no longer offers a manual refresh button", () => {
    expect(html).not.toContain('id="refresh"');
    expect(html).not.toContain(">Actualizar<");
    expect(appSource).not.toContain('getById("refresh")');
  });

  it("labels the session exit Logoff", () => {
    expect(html).toContain('id="logout-button"');
    expect(html).toContain(">Logoff<");
    expect(html).not.toContain(">Salir<");
  });

  it("never pins a standing Conectado badge", () => {
    // A steady state restated forever is not information. Asserted on the
    // assignment rather than the bare word, so the comment explaining the
    // removal does not itself trip the check.
    expect(appSource).not.toMatch(/authStatusMessage\s*=\s*"Conectado"/);
  });
});

describe("screen selection", () => {
  it("gates an anonymous visitor, whatever else is in state", () => {
    expect(selectAuthScreen({authenticated: false})).toBe(SCREEN_GATE);
    expect(
      selectAuthScreen({authenticated: false, isLoading: true, slates: [{id: "a"}]}),
    ).toBe(SCREEN_GATE);
  });

  it("loads while authenticated with nothing to draw yet", () => {
    expect(selectAuthScreen({authenticated: true, isLoading: true, slates: []})).toBe(
      SCREEN_LOADING,
    );
  });

  it("keeps the dashboard up through an in-place refresh", () => {
    // The regression this prevents: blanking a board the operator is
    // reading every time the 60 s poll refetches.
    expect(
      selectAuthScreen({authenticated: true, isLoading: true, slates: [{id: "a"}]}),
    ).toBe(SCREEN_DASHBOARD);
  });

  it("shows the dashboard once loading settles", () => {
    expect(
      selectAuthScreen({authenticated: true, isLoading: false, slates: [{id: "a"}]}),
    ).toBe(SCREEN_DASHBOARD);
  });

  it("does not strand an authenticated operator with no slates", () => {
    // No open concurso is a real state the dashboard has copy for; it must
    // not read as a permanent spinner.
    expect(
      selectAuthScreen({authenticated: true, isLoading: false, slates: []}),
    ).toBe(SCREEN_DASHBOARD);
  });

  it("survives a malformed or partial state without throwing", () => {
    expect(selectAuthScreen({})).toBe(SCREEN_GATE);
    expect(selectAuthScreen(null)).toBe(SCREEN_GATE);
    expect(selectAuthScreen({authenticated: true, isLoading: true})).toBe(SCREEN_LOADING);
    expect(selectAuthScreen({authenticated: true, slates: "nope"})).toBe(SCREEN_DASHBOARD);
  });

  it("is what app.js actually applies to the three containers", () => {
    expect(appSource).toContain("selectAuthScreen(state)");
    expect(appSource).toMatch(/gate\.hidden = screen !== SCREEN_GATE/);
    expect(appSource).toMatch(/loading\.hidden = screen !== SCREEN_LOADING/);
    expect(appSource).toMatch(/body\.hidden = screen !== SCREEN_DASHBOARD/);
  });
});

describe("login feedback", () => {
  it("routes a bad password to the gate, not the global banner", () => {
    expect(clientSource).toContain("Password incorrecto");
    expect(clientSource).toContain("authErrorMessage");
  });

  it("explains a throttled login instead of repeating 'incorrecto'", () => {
    expect(clientSource).toContain("Demasiados intentos fallidos");
  });

  it("tells the operator why an expired session bounced them back", () => {
    expect(clientSource).toContain("Tu sesión expiró");
  });

  it("clears the previous failure before a new attempt", () => {
    expect(appSource).toMatch(/state\.authErrorMessage = "";[\s\S]{0,200}loginWithPassword/);
  });
});

describe("logoff", () => {
  it("drops session data so no slate can flash behind the gate", () => {
    const handlerStart = appSource.indexOf('getById("logout-button")');
    const handler = appSource.slice(handlerStart, handlerStart + 700);
    expect(handler).toContain("logoutSession()");
    expect(handler).toContain("state.slates = []");
    expect(handler).toContain("state.activeSlateId = null");
    expect(handler).toContain("state.isLoading = false");
  });
});
