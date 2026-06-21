// R5.5 — Team Rating Activation Dry-run panel (read-only, diagnostic only).
import { describe, it, expect } from "vitest";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";

import { renderTeamRatingActivationDryRunPanel } from "../team-rating-activation-dry-run.js";

const here = dirname(fileURLToPath(import.meta.url));
const root = join(here, "..");
const indexHtml = readFileSync(join(root, "index.html"), "utf8");
const configJs = readFileSync(join(root, "config.js"), "utf8");
const appJs = readFileSync(join(root, "app.js"), "utf8");

const DRY_RUN = {
  slate_id: "s-1",
  draw_code: "PG-2338",
  mode: "activation_dry_run",
  production_active: false,
  safe_to_activate: false,
  dry_run_probability_model: "international_friendlies_temperature_v1",
  activation_policy: {
    competition_allowlist: ["International Friendlies"],
    routing_policy: "rating_replaces_fallback",
    calibrator_candidate: "international_friendlies_temperature_v1",
    temperature: 2.22,
    require_both_medium_plus: true,
    require_calibrator_compatible: true,
    review_blocks: true,
  },
  calibrator: {
    id: "international_friendlies_temperature_v1",
    temperature: 2.22,
    productive_available: false,
    compatible: true,
    compatibility_blockers: [],
  },
  summary: {
    total_matches: 14,
    eligible_if_enabled: 13,
    would_route: 6,
    would_keep_current: 8,
    blocked_by_rating: 1,
    blocked_by_review: 6,
    blocked_by_hard_sanity: 1,
    changed_top_pick_count: 0,
    changed_confidence_bucket_count: 3,
    max_probability_delta: 0.118,
    positions_would_route: [1, 2, 3, 5, 8, 11],
    positions_changed_pick: [],
  },
  matches: [
    {
      position: 1, match_id: "m1", home_team: "Argentina", away_team: "Australia",
      competition: "International Friendlies", current_engine: "xgboost",
      dry_run_engine: "team_rating_calibrated", would_route: true,
      current_probabilities: { "1": 0.6, X: 0.25, "2": 0.15 },
      dry_run_probabilities: { "1": 0.5, X: 0.28, "2": 0.22 },
      probability_delta: { "1": -0.1, X: 0.03, "2": 0.07 },
      max_abs_delta: 0.1, current_top_pick: "1", dry_run_top_pick: "1",
      top_pick_changed: false, current_confidence_bucket: "alta",
      dry_run_confidence_bucket: "media", confidence_bucket_changed: true,
      blockers: [], warnings: ["dry_run_only"],
    },
    {
      position: 13, match_id: "m13", home_team: "República Del Congo",
      away_team: "Uzbekistan", competition: "International Friendlies",
      current_engine: "fallback", dry_run_engine: "fallback", would_route: false,
      current_probabilities: { "1": 0.4, X: 0.3, "2": 0.3 },
      dry_run_probabilities: { "1": 0.4, X: 0.3, "2": 0.3 },
      probability_delta: { "1": 0, X: 0, "2": 0 }, max_abs_delta: 0,
      current_top_pick: "1", dry_run_top_pick: "1", top_pick_changed: false,
      current_confidence_bucket: "media", dry_run_confidence_bucket: "media",
      confidence_bucket_changed: false,
      blockers: ["rating_not_present", "not_both_medium_plus"], warnings: ["dry_run_only"],
    },
  ],
  activation_blockers: ["feature_flag_off", "calibrator_productive_available_false"],
};

describe("renderTeamRatingActivationDryRunPanel", () => {
  const html = renderTeamRatingActivationDryRunPanel(DRY_RUN);

  it("shows the DRY-RUN · NO ACTIVO badge", () => {
    expect(html).toContain("DRY-RUN · NO ACTIVO");
    expect(html).toContain("dryrun-badge");
  });
  it("states it does not change prediction/pick/ticket", () => {
    expect(html).toMatch(/No modifica predicci[oó]n, pick ni ticket/i);
  });
  it("shows would_route 6/14 and would keep current 8/14", () => {
    expect(html).toContain("6/14");
    expect(html).toContain("8/14");
  });
  it("shows safe to activate NO", () => {
    expect(html).toContain("Safe to activate");
    expect(html).toMatch(/Safe to activate: NO/);
  });
  it("lists the activation blockers", () => {
    expect(html).toContain("feature_flag_off");
    expect(html).toContain("calibrator_productive_available_false");
  });
  it("renders a per-match table with current vs dry-run engine", () => {
    expect(html).toContain("Motor actual");
    expect(html).toContain("Motor dry-run");
    expect(html).toContain("rating calibrado");
    expect(html).toContain("Argentina");
    expect(html).toContain("República Del Congo");
  });
  it("flags positions that would route", () => {
    expect(html).toContain("1–3, 5, 8, 11");
  });
  it("renders an empty state without data", () => {
    expect(renderTeamRatingActivationDryRunPanel(null)).toContain(
      "Sin datos de activation dry-run",
    );
  });
});

describe("changed top pick warning", () => {
  it("highlights a flipped pick when changed_top_pick_count > 0", () => {
    const flipped = {
      ...DRY_RUN,
      summary: { ...DRY_RUN.summary, changed_top_pick_count: 1, positions_changed_pick: [1] },
      matches: [
        { ...DRY_RUN.matches[0], top_pick_changed: true, dry_run_top_pick: "2" },
        DRY_RUN.matches[1],
      ],
    };
    const html = renderTeamRatingActivationDryRunPanel(flipped);
    expect(html).toContain("dryrun-pick-changed");
    expect(html).toContain("dryrun-stat-warn");
  });
});

describe("wiring", () => {
  it("index.html has the dry-run container after the shadow panel", () => {
    expect(indexHtml).toContain('id="team-rating-dry-run-body"');
    expect(indexHtml).toContain(">Team Rating Activation Dry-run<");
    expect(indexHtml.indexOf("team-rating-shadow-body")).toBeLessThan(
      indexHtml.indexOf("team-rating-dry-run-body"),
    );
  });
  it("config.js tracks teamRatingDryRun state", () => {
    expect(configJs).toContain("teamRatingDryRun");
  });
  it("app.js fetches the dry-run endpoint and renders it", () => {
    expect(appJs).toContain("/team-rating-activation-dry-run");
    expect(appJs).toContain("renderTeamRatingActivationDryRunPanel");
    expect(appJs).toContain("renderTeamRatingDryRun()");
  });
});
