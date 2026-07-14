// MS PDF watcher diagnostics — UI surfacing of LN refresh status.
import { describe, it, expect } from "vitest";
import { msPdfWatchStatus, resolveVisibleSelection } from "../slate-selection.js";

describe("msPdfWatchStatus", () => {
  it("returns null when no watcher data present", () => {
    expect(msPdfWatchStatus({ discovery: {} })).toBe(null);
    expect(msPdfWatchStatus({})).toBe(null);
  });

  it("shows 'PDF sin cambios' on unchanged", () => {
    const w = msPdfWatchStatus({
      discovery: {
        last_ms_pdf_checked_at: "2026-06-30T00:00:00Z",
        last_ms_pdf_sha256: "fc934103b18ffe5b",
        last_ms_pdf_status: "unchanged",
        current_ms_candidate: { draw_code: "PGM-802", date_status: "source_invalid", activation_status: "blocked" },
        ms_pdf_recommended_action: "Esperar PDF corregido de LN (cierre válido del concurso correcto)",
      },
    });
    expect(w.status_label).toBe("PDF sin cambios");
    expect(w.sha_short).toBe("fc934103");
    expect(w.detail).toContain("concurso 800");
    expect(w.candidate).toBe("PGM-802");
    expect(w.activation_status).toBe("blocked");
  });

  it("shows persistent source_invalid (cierre del concurso 800)", () => {
    const w = msPdfWatchStatus({
      discovery: { last_ms_pdf_status: "changed_invalid", current_ms_candidate: { date_status: "source_invalid" } },
    });
    expect(w.status_label).toContain("cierre aún inválido");
    expect(w.detail).toContain("800");
  });

  it("shows 'MS activada' when PDF valid", () => {
    const w = msPdfWatchStatus({
      discovery: {
        last_ms_pdf_status: "changed_valid",
        current_ms_candidate: { draw_code: "PGM-802", date_status: "date_valid", activation_status: "open" },
      },
    });
    expect(w.status_label).toContain("MS activada");
    expect(w.detail).toContain("activada desde PDF oficial");
    expect(w.activation_status).toBe("open");
  });

  it("an activated valid MS would appear as an open slate (selector reflects backend)", () => {
    // When the backend opens PGM-802, it moves to open_slates; the selector
    // simply surfaces it — source_invalid never appears as open.
    const r = resolveVisibleSelection({
      visible: {
        open_slates: [{ id: "ms", draw_code: "PGM-802", week_type: "midweek", is_closed: false, is_archived: false, read_only: false, date_status: "date_valid" }],
        recent_slates: [],
        reason: "open_slate",
        selected_default_slate_id: "ms",
      },
    });
    expect(r.reason).toBe("open_slate");
    expect(r.selectedId).toBe("ms");
  });
});
