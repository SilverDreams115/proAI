// The ops panel showed "API degradado" long after the backend had recovered.
// Cause: /health and /ready were fetched exactly once, in boot(), so whatever
// the panel read at page load stayed pinned for the whole session — including
// the transient `degraded` the backend reports right after a restart, while
// the worker heartbeat on /data is still older than the poll warning
// threshold.
//
// app.js is the orchestrator and depends on globals that config.js and
// api-client.js install on `window` (`state`, `safeFetch`, `apiBase`), so it
// cannot be imported standalone the way ticket-decision.js or helpers.js can.
// What regressed here is wiring, not logic, so this pins the wiring: the
// module must define a health poll AND register it on an interval. A future
// edit that drops the interval and leaves the panel boot-only fails here.

import { describe, expect, it } from "vitest";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";

// Resolved off the vitest root (frontend/, where vitest.config.js lives)
// rather than import.meta.url: jsdom replaces the URL global, so neither
// fileURLToPath nor .pathname resolves the module path correctly here.
const source = readFileSync(resolve(process.cwd(), "app.js"), "utf-8");

describe("ops panel health freshness", () => {
  it("defines a health poll that refetches /health and /ready", () => {
    const body = source.slice(
      source.indexOf("async function pollHealth("),
      source.indexOf("async function pollActiveSlate("),
    );
    expect(body).not.toHaveLength(0);
    expect(body).toContain('safeFetch("/health"');
    expect(body).toContain('safeFetch("/ready"');
    // Refetching is pointless if the panel is not redrawn from the new state.
    expect(body).toContain("renderProductionStatus()");
  });

  it("registers the health poll on a recurring interval, not just boot()", () => {
    expect(source).toMatch(/setInterval\(\s*pollHealth\s*,\s*\d+\s*\)/);
  });

  it("keeps the health poll at most as slow as the active-slate heartbeat", () => {
    const interval = (name) => {
      const found = source.match(
        new RegExp(`setInterval\\(\\s*${name}\\s*,\\s*(\\d+)\\s*\\)`),
      );
      return found ? Number(found[1]) : null;
    };
    const health = interval("pollHealth");
    const activeSlate = interval("pollActiveSlate");
    expect(health).not.toBeNull();
    expect(activeSlate).not.toBeNull();
    expect(health).toBeLessThanOrEqual(activeSlate);
  });
});
