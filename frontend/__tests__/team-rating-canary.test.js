// R5.6-B — Team Rating Canary: Diagnóstico status panel + per-card badge.
import { describe, it, expect } from "vitest";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";

import { renderTeamRatingCanaryPanel } from "../team-rating-canary.js";

const here = dirname(fileURLToPath(import.meta.url));
const root = join(here, "..");
const indexHtml = readFileSync(join(root, "index.html"), "utf8");
const configJs = readFileSync(join(root, "config.js"), "utf8");
const appJs = readFileSync(join(root, "app.js"), "utf8");

const STATUS = {
  canary_enabled: true,
  scope: "PG-2338",
  in_scope: true,
  competition_allowlist: ["International Friendlies"],
  routing_policy: "rating_replaces_fallback",
  calibrator_id: "international_friendlies_temperature_v1",
  temperature: 2.22,
  allowed_positions: [1, 2, 3, 5, 8, 11],
  active_positions: [1, 2, 3, 5, 8, 11],
  blocked_positions: [4, 6, 7, 9, 10, 12, 13, 14],
  full_activation: false,
  ticket_integration: false,
  rollback_available: true,
};

describe("renderTeamRatingCanaryPanel", () => {
  const html = renderTeamRatingCanaryPanel(STATUS);

  it("shows CANARY ACTIVO when enabled and in scope", () => {
    expect(html).toContain("CANARY ACTIVO");
  });
  it("shows active positions and scope", () => {
    expect(html).toContain("1–3, 5, 8, 11");
    expect(html).toContain("PG-2338");
    expect(html).toContain("Canary active positions");
  });
  it("shows full activation NO and ticket integration NO", () => {
    expect(html).toContain("Full activation");
    expect(html).toContain("Ticket integration");
    expect(html).toMatch(/Ticket recommendation not using canary yet/i);
  });
  it("shows CANARY INACTIVO when disabled", () => {
    const off = renderTeamRatingCanaryPanel({ ...STATUS, canary_enabled: false, in_scope: false, active_positions: [] });
    expect(off).toContain("CANARY INACTIVO");
  });
  it("renders an empty state without data", () => {
    expect(renderTeamRatingCanaryPanel(null)).toContain("Sin datos de canary");
  });
});

describe("per-card CANARY badge wiring", () => {
  it("app.js renders a CANARY badge gated on pred.canary.active", () => {
    expect(appJs).toContain("function renderCanaryBadge");
    expect(appJs).toContain("renderCanaryBadge(pred)");
    expect(appJs).toContain("c.active");
    expect(appJs).toContain(">CANARY<");
  });
  it("badge is only emitted when canary is active (no active -> empty string)", () => {
    // Structural: the function returns "" before producing the badge markup.
    const fn = appJs.slice(appJs.indexOf("function renderCanaryBadge"));
    const body = fn.slice(0, fn.indexOf("\n}"));
    expect(body).toMatch(/if\s*\(!c\s*\|\|\s*!c\.active\)\s*return\s*""/);
  });
});

describe("wiring", () => {
  it("index.html has the canary container after the readiness panel", () => {
    expect(indexHtml).toContain('id="team-rating-canary-body"');
    expect(indexHtml).toContain(">Team Rating Canary<");
    expect(indexHtml.indexOf("team-rating-readiness-body")).toBeLessThan(
      indexHtml.indexOf("team-rating-canary-body"),
    );
  });
  it("does not remove the existing diagnostic panels or ticket grid", () => {
    expect(indexHtml).toContain('id="team-rating-shadow-body"');
    expect(indexHtml).toContain('id="team-rating-dry-run-body"');
    expect(indexHtml).toContain('id="team-rating-readiness-body"');
    expect(indexHtml).toContain('id="ticket-grid"');
  });
  it("config.js tracks teamRatingCanary state", () => {
    expect(configJs).toContain("teamRatingCanary");
  });
  it("app.js fetches the canary status endpoint and renders it", () => {
    expect(appJs).toContain("/team-rating-canary-status");
    expect(appJs).toContain("renderTeamRatingCanaryPanel");
    expect(appJs).toContain("renderTeamRatingCanary()");
  });
});
