import { describe, expect, it } from "vitest";

import { pickNextProposal } from "../proposal-selection.js";

describe("next contest proposal selection", () => {
  it("hides stale PGM 804 when PGM 805 is already active", () => {
    const proposal = pickNextProposal(
      [{
        id: "pgm-804",
        draw_code: "804",
        week_type: "midweek",
        status: "validated",
        promoted_slate_id: null,
        last_seen_at: "2026-07-20T21:00:00Z",
      }],
      [{
        id: "pgm-805",
        draw_code: "PGM-805",
        week_type: "midweek",
        is_archived: false,
        is_closed: false,
      }],
    );

    expect(proposal).toBeNull();
  });

  it("keeps a genuinely newer validated contest", () => {
    const proposal = pickNextProposal(
      [{
        id: "pgm-806",
        draw_code: "806",
        week_type: "midweek",
        status: "validated",
        promoted_slate_id: null,
        last_seen_at: "2026-07-20T21:00:00Z",
      }],
      [{
        draw_code: "PGM-805",
        week_type: "midweek",
        is_archived: false,
        is_closed: false,
      }],
    );

    expect(proposal?.draw_code).toBe("806");
  });
});
