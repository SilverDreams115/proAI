// R5.4 — Team Rating Shadow diagnostic panel (read-only, shadow-only).
import { describe, it, expect } from "vitest";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";

import {
  formatPositionRanges,
  ratingBlockedPositions,
  renderTeamRatingShadowPanel,
} from "../team-rating-shadow.js";

const here = dirname(fileURLToPath(import.meta.url));
const root = join(here, "..");
const indexHtml = readFileSync(join(root, "index.html"), "utf8");
const configJs = readFileSync(join(root, "config.js"), "utf8");
const appJs = readFileSync(join(root, "app.js"), "utf8");

const SHADOW = {
  slate_id: "s-1",
  draw_code: "PG-2338",
  mode: "shadow_only",
  production_active: false,
  feature_flag_enabled: false,
  gate_flag_enabled: false,
  routing_policy: "rating_replaces_fallback",
  active_rating_run: {
    run_id: "r1",
    algorithm_version: "elo_v1",
    status: "active",
    snapshot_count: 729,
  },
  calibrator_candidate: {
    id: "international_friendlies_temperature_v1",
    competition: "International Friendlies",
    temperature: 2.22,
    routing_policy: "rating_replaces_fallback",
    productive_available: false,
    compatible: true,
    compatibility_blockers: [],
  },
  summary: {
    total_matches: 14,
    eligible_current: 0,
    eligible_if_enabled: 13,
    would_use_rating_model_current: 0,
    would_use_rating_model_if_enabled: 6,
    would_remain_fallback: 8,
    blocked_by_flag: 14,
    blocked_by_competition: 0,
    blocked_by_rating: 1,
    blocked_by_calibrator: 0,
    blocked_by_sanity: 7,
    warnings: 0,
    positions_eligible_if_enabled: [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 14],
    positions_would_route: [1, 2, 3, 5, 8, 11],
    positions_blocked: [4, 6, 7, 9, 10, 12, 13, 14],
  },
  matches: [
    {
      position: 1,
      match_id: "m1",
      home_team: "Argentina",
      away_team: "Australia",
      competition: "International Friendlies",
      rating_status: "full_rating",
      rating_diff: 12.3,
      both_medium_plus: true,
      eligible_current: false,
      eligible_if_enabled: true,
      would_use_rating_model_if_enabled: true,
      blockers: [],
      warnings: [],
    },
    {
      position: 13,
      match_id: "m13",
      home_team: "República Del Congo",
      away_team: "Uzbekistan",
      competition: "International Friendlies",
      rating_status: "partial_rating",
      rating_diff: null,
      both_medium_plus: false,
      eligible_current: false,
      eligible_if_enabled: false,
      would_use_rating_model_if_enabled: false,
      blockers: ["rating_not_present", "not_both_medium_plus"],
      warnings: [],
    },
  ],
};

describe("formatPositionRanges", () => {
  it("collapses consecutive runs into ranges", () => {
    expect(formatPositionRanges([1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 14])).toBe("1–12, 14");
  });
  it("handles singletons and gaps", () => {
    expect(formatPositionRanges([1, 3, 5])).toBe("1, 3, 5");
    expect(formatPositionRanges([])).toBe("—");
  });
});

describe("ratingBlockedPositions", () => {
  it("finds matches blocked by a rating quality blocker", () => {
    expect(ratingBlockedPositions(SHADOW)).toEqual([13]);
  });
});

describe("renderTeamRatingShadowPanel", () => {
  const html = renderTeamRatingShadowPanel(SHADOW);

  it("shows shadow-only / OFF state", () => {
    expect(html).toContain("Solo sombra · OFF");
    expect(html).toContain("shadow-off");
  });
  it("shows the active run, calibrator candidate and routing policy", () => {
    expect(html).toContain("elo_v1");
    expect(html).toContain("international_friendlies_temperature_v1");
    expect(html).toContain("rating_replaces_fallback");
  });
  it("shows eligibility breakdown 0 now / 13 if enabled / 6 routing", () => {
    expect(html).toContain("0/14");
    expect(html).toContain("13/14");
    expect(html).toContain("6/14");
  });
  it("flags pos13 as blocked by partial/no rating", () => {
    expect(html).toContain("Pos 13 bloqueada");
    expect(html).toContain("República Del Congo");
  });
  it("states it does not change prediction/pick/ticket", () => {
    expect(html).toMatch(/no modifica la predicci[oó]n actual, el pick ni el ticket/i);
  });
  it("renders an empty state when there is no shadow data", () => {
    expect(renderTeamRatingShadowPanel(null)).toContain("Sin datos de team rating shadow");
  });
});

describe("wiring", () => {
  it("index.html has the diagnostic shadow container", () => {
    expect(indexHtml).toContain('id="team-rating-shadow-body"');
    expect(indexHtml).toContain(">Team Rating Shadow<");
  });
  it("config.js tracks teamRatingShadow state", () => {
    expect(configJs).toContain("teamRatingShadow");
  });
  it("app.js fetches the read-only shadow endpoint and renders it", () => {
    expect(appJs).toContain("/team-rating-shadow");
    expect(appJs).toContain("renderTeamRatingShadowPanel");
    expect(appJs).toContain("renderTeamRatingShadow()");
  });
});
