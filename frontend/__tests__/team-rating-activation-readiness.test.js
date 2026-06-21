// R5.6-A — Team Rating Activation Readiness panel (read-only, diagnostic only).
import { describe, it, expect } from "vitest";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";

import { renderTeamRatingActivationReadinessPanel } from "../team-rating-activation-readiness.js";

const here = dirname(fileURLToPath(import.meta.url));
const root = join(here, "..");
const indexHtml = readFileSync(join(root, "index.html"), "utf8");
const configJs = readFileSync(join(root, "config.js"), "utf8");
const appJs = readFileSync(join(root, "app.js"), "utf8");

const READINESS = {
  slate_id: "s-1",
  draw_code: "PG-2338",
  mode: "activation_readiness",
  production_active: false,
  ready_for_canary: false,
  ready_for_full_activation: false,
  target_activation: {
    scope: "minimal_canary",
    competition_allowlist: ["International Friendlies"],
    routing_policy: "rating_replaces_fallback",
    calibrator_id: "international_friendlies_temperature_v1",
    temperature: 2.22,
    require_both_medium_plus: true,
    review_blocks: true,
    hard_blockers_block: true,
  },
  calibrator: {
    id: "international_friendlies_temperature_v1",
    approval_status: "approved_inactive",
    approved_for_canary: true,
    productive_available: false,
    active: false,
  },
  dry_run_summary: {
    total_matches: 14,
    would_route: 6,
    would_keep_current: 8,
    changed_top_pick_count: 0,
    max_probability_delta: 0.1739,
  },
  readiness_checks: [
    { check: "feature_flag_off", status: "blocking_until_canary", details: "flip on in R5.6-B", count: null },
    { check: "calibrator_approved_inactive", status: "pass", details: "approved for canary", count: null },
    { check: "calibrator_productive_available", status: "blocking_until_full_activation", details: "needed for full", count: null },
    { check: "hard_sanity_blockers_present", status: "block_for_affected_matches", details: "hard blockers", count: 2 },
    { check: "review_blockers_present", status: "block_for_affected_matches", details: "REVISAR", count: 6 },
    { check: "rating_coverage", status: "partial_pass", details: "partial rating", count: 1 },
    { check: "read_only_guards", status: "pass", details: "read-only", count: null },
  ],
  canary_plan: {
    canary_allowed_matches: [1, 2, 3, 5, 8, 11],
    blocked_matches: [4, 6, 7, 9, 10, 12, 13, 14],
    rollback: [
      "set team_rating_gate_enabled=false",
      "set team_rating_feature_enabled=false",
      "restart proai and worker",
      "verify counts unchanged",
    ],
  },
};

describe("renderTeamRatingActivationReadinessPanel", () => {
  const html = renderTeamRatingActivationReadinessPanel(READINESS);

  it("shows the READINESS · NO ACTIVO badge", () => {
    expect(html).toContain("READINESS · NO ACTIVO");
    expect(html).toContain("readiness-badge");
  });
  it("states it does not activate or change prediction/pick/ticket", () => {
    expect(html).toMatch(/No activa el gate/i);
    expect(html).toMatch(/No modifica predicci[oó]n, pick ni ticket/i);
  });
  it("shows ready_for_canary NO and approved_inactive", () => {
    expect(html).toContain("Ready for canary");
    expect(html).toContain("approved_inactive");
    expect(html).toMatch(/Ready for canary: NO/);
  });
  it("shows would route 6/14 and changed top pick 0/14", () => {
    expect(html).toContain("6/14");
    expect(html).toContain("0/14");
  });
  it("shows canary allowed and blocked positions", () => {
    expect(html).toContain("1–3, 5, 8, 11");
    expect(html).toContain("Canary allowed positions");
    expect(html).toContain("Blocked positions");
  });
  it("renders the readiness checks table with statuses", () => {
    expect(html).toContain("feature_flag_off");
    expect(html).toContain("blocking_until_canary");
    expect(html).toContain("calibrator_approved_inactive");
    expect(html).toContain("read_only_guards");
  });
  it("renders the rollback plan", () => {
    expect(html).toContain("Rollback plan");
    expect(html).toContain("team_rating_gate_enabled=false");
  });
  it("renders an empty state without data", () => {
    expect(renderTeamRatingActivationReadinessPanel(null)).toContain(
      "Sin datos de activation readiness",
    );
  });
});

describe("wiring", () => {
  it("index.html has the readiness container after the dry-run panel", () => {
    expect(indexHtml).toContain('id="team-rating-readiness-body"');
    expect(indexHtml).toContain(">Team Rating Activation Readiness<");
    expect(indexHtml.indexOf("team-rating-dry-run-body")).toBeLessThan(
      indexHtml.indexOf("team-rating-readiness-body"),
    );
  });
  it("config.js tracks teamRatingReadiness state", () => {
    expect(configJs).toContain("teamRatingReadiness");
  });
  it("app.js fetches the readiness endpoint and renders it", () => {
    expect(appJs).toContain("/team-rating-activation-readiness");
    expect(appJs).toContain("renderTeamRatingActivationReadinessPanel");
    expect(appJs).toContain("renderTeamRatingReadiness()");
  });
});
