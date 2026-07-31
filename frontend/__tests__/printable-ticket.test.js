import { describe, expect, it } from "vitest";
import { JSDOM } from "jsdom";
import {
  buildPrintableTicketHtml,
  mountPrintableTicket,
  PRINTABLE_TICKET_STYLES,
} from "../printable-ticket.js";

const doc = (html) => new JSDOM(html).window.document;
const printRules = PRINTABLE_TICKET_STYLES.slice(PRINTABLE_TICKET_STYLES.indexOf("@media print"));

const ROWS = [
  { position: 1, home_team_name: "San Luis", away_team_name: "Tijuana", picks: ["1"], pick: "L" },
  { position: 2, home_team_name: "Juarez", away_team_name: "Pumas", picks: ["1", "X"], pick: "L / E" },
  { position: 3, home_team_name: "Atlas", away_team_name: "Monterrey", picks: ["1", "X", "2"], pick: "L / E / V" },
  { position: 4, home_team_name: "Toluca", away_team_name: "Necaxa", picks: ["X"], pick: "E" },
  { position: 5, home_team_name: "Oaxaca", away_team_name: "Sinaloa", picks: ["2"], pick: "V" },
  { position: 6, home_team_name: "Inter", away_team_name: "Atalanta", picks: ["X", "2"], pick: "E / V" },
];

const BASE = {
  slate: { draw_code: "PG-2344", label: "Progol Fin de Semana 2344" },
  modeLabel: "Balanceado",
  generatedAt: "2026-07-31T14:00:00Z",
  rows: ROWS,
};

/** The signs a row shows as marked, read back off the rendered boxes. */
function markedSigns(row) {
  return [...row.querySelectorAll(".box.on")].map((box) => box.textContent.trim());
}

describe("buildPrintableTicketHtml grid", () => {
  it("prints every position, in the order it was handed", () => {
    const rows = [...doc(buildPrintableTicketHtml(BASE)).querySelectorAll("tbody tr")];

    expect(rows).toHaveLength(6);
    expect(rows.map((row) => row.querySelector(".pos").textContent.trim()))
      .toEqual(["1", "2", "3", "4", "5", "6"]);
    expect(rows[0].textContent).toContain("San Luis");
    expect(rows[0].textContent).toContain("Tijuana");
  });

  it("marks exactly the signs the play holds, simple, double and triple alike", () => {
    const rows = [...doc(buildPrintableTicketHtml(BASE)).querySelectorAll("tbody tr")];

    // A double is two lit boxes, not a "L / E" string — the sheet is marked
    // the way the papeleta is marked.
    expect(markedSigns(rows[0])).toEqual(["L"]);
    expect(markedSigns(rows[1])).toEqual(["L", "E"]);
    expect(markedSigns(rows[2])).toEqual(["L", "E", "V"]);
    expect(markedSigns(rows[3])).toEqual(["E"]);
    expect(markedSigns(rows[4])).toEqual(["V"]);
    expect(markedSigns(rows[5])).toEqual(["E", "V"]);
    // Every position always shows all three boxes, lit or not.
    rows.forEach((row) => expect(row.querySelectorAll(".box")).toHaveLength(3));
  });

  it("keeps L, E and V readable as letters when they are selected", () => {
    const rows = [...doc(buildPrintableTicketHtml(BASE)).querySelectorAll("tbody tr")];
    rows[2].querySelectorAll(".box").forEach((box) => {
      expect(["L", "E", "V"]).toContain(box.textContent.trim());
    });
  });

  it("heads the columns the way the papeleta reads", () => {
    const heads = [...doc(buildPrintableTicketHtml(BASE)).querySelectorAll("thead th")];
    expect(heads.map((th) => th.textContent.trim())).toEqual(["#", "Local", "Empate", "Visitante"]);
  });

  it("escapes team names inside the printable html", () => {
    const html = buildPrintableTicketHtml({
      ...BASE,
      rows: [{ position: 1, home_team_name: "<script>", away_team_name: "A&B", picks: ["1"], pick: "L" }],
    });

    expect(html).not.toContain("<script>");
    expect(html).toContain("&lt;script&gt;");
    expect(html).toContain("A&amp;B");
  });
});

describe("buildPrintableTicketHtml crest slot", () => {
  it("stands in with the club's initials when no crest exists", () => {
    // No team in the model carries a crest today, so this is every row.
    const rows = [...doc(buildPrintableTicketHtml(BASE)).querySelectorAll("tbody tr")];
    const monos = [...rows[0].querySelectorAll(".crest-mono")].map((node) => node.textContent);

    expect(monos).toEqual(["SL", "T"]);
    expect(rows[0].querySelectorAll(".crest")).toHaveLength(2);
    expect(rows[0].querySelector(".crest img")).toBeNull();
  });

  it("shows an image only when a resolved url is handed in, with the monogram behind it", () => {
    const row = doc(buildPrintableTicketHtml({
      ...BASE,
      rows: [{ ...ROWS[0], home_team_crest: "https://cdn.example/sl.png" }],
    })).querySelector("tbody tr");
    const home = row.querySelectorAll(".crest")[0];

    expect(home.querySelector("img").getAttribute("src")).toBe("https://cdn.example/sl.png");
    // The monogram stays in the DOM, so a crest that cannot load leaves a
    // circle with initials rather than a broken-image icon.
    expect(home.querySelector(".crest-mono").textContent).toBe("SL");
    // The fallback is a listener wired at mount, never an inline attribute —
    // the CSP would drop it.
    expect(home.querySelector("img").getAttribute("onerror")).toBeNull();
  });

  it("never reaches for a remote asset on its own", () => {
    const html = buildPrintableTicketHtml(BASE);
    expect(html).not.toMatch(/<link|@import|@font-face/);
    expect(html).not.toMatch(/https?:\/\//);
  });
});

describe("buildPrintableTicketHtml header and summary", () => {
  it("labels the shape of the play from the picks it was given", () => {
    const shapeOf = (picks) =>
      doc(buildPrintableTicketHtml({ ...BASE, rows: ROWS.map((row) => ({ ...row, picks })) }))
        .querySelector(".chip.accent").textContent.trim();

    expect(shapeOf(["1"])).toBe("Simple");
    expect(shapeOf(["1", "X"])).toBe("Doble");
    expect(shapeOf(["1", "X", "2"])).toBe("Triple");
    // The mixed slate is the normal one.
    expect(doc(buildPrintableTicketHtml(BASE)).querySelector(".chip.accent").textContent.trim()).toBe("Mixto");
  });

  it("carries the identity, the count and the generation stamp", () => {
    const page = doc(buildPrintableTicketHtml(BASE));

    expect(page.querySelector(".draw-code").textContent).toBe("PG-2344");
    expect(page.querySelector("h1").textContent).toBe("Progol Fin de Semana 2344");
    expect(page.querySelector(".chips").textContent).toContain("6 partidos");
    expect(page.querySelector(".chips").textContent).toContain("Modo Balanceado");
    expect(page.querySelector(".head-meta").textContent).toContain("Generada");
    expect(page.querySelector(".foot").textContent).toContain("Verifica la jugada contra la papeleta oficial");
  });

  it("takes the combination count from the payload, and counts it in Spanish", () => {
    const chipText = (combinations) =>
      doc(buildPrintableTicketHtml({
        ...BASE,
        slateOptions: { options: [{ name: "X", recommended: true, combinations }] },
      })).querySelector(".chips").textContent;

    expect(chipText(3888)).toContain("3888 combinaciones");
    expect(chipText(1)).toContain("1 combinación");
    // Nothing is invented when the payload carries no options.
    expect(doc(buildPrintableTicketHtml(BASE)).querySelector(".chips").textContent)
      .not.toContain("combinaci");
  });

  it("names the recommended ticket the way the panel does", () => {
    const html = buildPrintableTicketHtml({
      ...BASE,
      moneyMode: { decision: { status: "JUGAR_SOLO_CONSERVADOR", recommended_ticket: "conservative", reason: "acota el riesgo" } },
    });
    const card = doc(html).querySelector(".verdict");

    expect(card.textContent).toContain("JUGAR SOLO CONSERVADOR");
    expect(card.textContent).toContain("Conservador");
    expect(html).not.toContain("conservative");
  });

  it("shows the stop verdict as its own state", () => {
    const card = doc(buildPrintableTicketHtml({
      ...BASE,
      moneyMode: { decision: { status: "NO_JUGAR", recommended_ticket: null, reason: "riesgo no cubrible" } },
    })).querySelector(".verdict");

    expect(card.className).toContain("stop");
    expect(card.textContent).toContain("NO JUGAR");
    expect(card.textContent).toContain("No comprar boleto");
    expect(card.textContent).toContain("ninguno");
  });

  it("summarises price, coverage and action as badges", () => {
    const page = doc(buildPrintableTicketHtml({
      ...BASE,
      moneyMode: {
        decision: { status: "JUGAR_SOLO_CONSERVADOR", recommended_ticket: "conservative", reason: "r" },
        tickets: { conservative: { uncovered_no_simple_positions: [10, 14] } },
      },
      slateOptions: {
        recommended_action: "COMPRAR_CONSERVADORA",
        options: [{ name: "Conservadora", recommended: true, combinations: 3888, price_status: "verified", estimated_cost: 583, currency: "MXN" }],
      },
    }));
    const summary = page.querySelector(".summary").textContent;

    expect(summary).toContain("Conservadora");
    expect(summary).toContain("3888");
    expect(summary).toContain("$583 MXN");
    expect(summary).toContain("#10 #14");
    expect(summary).toContain("Comprar boleto conservador");
  });

  it("flags an unverified price instead of printing a number nobody checked", () => {
    const page = doc(buildPrintableTicketHtml({
      ...BASE,
      slateOptions: {
        recommended_action: "REVISAR",
        options: [{ name: "Conservadora", recommended: true, combinations: 3888, price_status: "unverified", estimated_cost: 583 }],
      },
    }));
    const price = [...page.querySelectorAll(".badge")].find((node) => node.textContent.includes("Precio"));

    expect(price.className).toContain("warn");
    expect(price.textContent).toContain("no verificado");
    expect(page.querySelector(".summary").textContent).not.toContain("583");
  });

  it("keeps the name-review warning", () => {
    const notice = doc(buildPrintableTicketHtml({
      ...BASE,
      nameWarnings: [{ position: 14, message: "nombre de equipo incompleto o provisional" }],
    })).querySelector(".notice");

    expect(notice.textContent).toContain("Revisar nombres antes de comprar");
    expect(notice.textContent).toContain("#14");
  });
});

describe("buildPrintableTicketHtml as a document that leaves the building", () => {
  it("stamps a blocked boleta so it cannot pass for a cleared one", () => {
    const page = doc(buildPrintableTicketHtml({
      ...BASE,
      publishGate: {
        whatsapp_allowed: false,
        blocked_count: 2,
        blocked_positions: [
          { position: 3, match: "Atlas vs Monterrey", reasons: ["equipo placeholder/sospechoso o datos insuficientes"] },
          { position: 10, match: "Barracas vs Riestra", reasons: ["predicción bloqueada"] },
        ],
      },
      moneyMode: { decision: { status: "NO_JUGAR", reason: "r" } },
    }));
    const banner = page.querySelector(".gate-banner");

    expect(banner).not.toBeNull();
    expect(banner.textContent).toContain("No apto para envío");
    expect(banner.textContent).toContain("2 posición(es)");
    expect(banner.textContent).toContain("#10");
    expect(banner.textContent).toContain("predicción bloqueada");
    // Above the Money Mode verdict — the first thing read on the sheet.
    expect(banner.compareDocumentPosition(page.querySelector(".verdict")))
      .toBe(4 /* DOCUMENT_POSITION_FOLLOWING */);
  });

  it("lists only the first six blocked positions and counts the rest", () => {
    const page = doc(buildPrintableTicketHtml({
      ...BASE,
      publishGate: {
        whatsapp_allowed: false,
        blocked_count: 9,
        blocked_positions: Array.from({ length: 9 }, (_, i) => ({
          position: i + 1, match: `A${i} vs B${i}`, reasons: ["predicción bloqueada"],
        })),
      },
    }));

    expect(page.querySelectorAll(".gate-list li")).toHaveLength(7);
    expect(page.querySelector(".gate-banner").textContent).toContain("y 3 más");
  });

  it("prints no banner when the gate allows publishing, or has nothing to say", () => {
    const bannerIn = (options) => doc(buildPrintableTicketHtml(options)).querySelector(".gate-banner");

    expect(bannerIn({ ...BASE, publishGate: { whatsapp_allowed: true, blocked_count: 0 } })).toBeNull();
    expect(bannerIn(BASE)).toBeNull();
  });
});

describe("buildPrintableTicketHtml under the app's Content-Security-Policy", () => {
  // The popup window.open("") hands back inherits this page's CSP —
  // `style-src 'self'; script-src 'self'`, neither with `unsafe-inline`. Every
  // inline style and handler in the document is therefore dropped, which is how
  // the boleta ended up printing as bare HTML with a dead print button. These
  // are the regression guards for that.
  it("carries no inline stylesheet and no style attributes", () => {
    const html = buildPrintableTicketHtml(BASE);

    expect(html).not.toContain("<style");
    expect(html).not.toMatch(/\sstyle="/);
  });

  it("carries no inline event handlers", () => {
    const html = buildPrintableTicketHtml({
      ...BASE,
      rows: [{ ...ROWS[0], home_team_crest: "https://cdn.example/sl.png" }],
    });

    expect(html).not.toMatch(/\son(click|error|load)=/);
    expect(doc(html).querySelector("[data-print]")).not.toBeNull();
  });

  it("sizes the columns from the stylesheet, where the policy cannot reach them", () => {
    const cols = [...doc(buildPrintableTicketHtml(BASE)).querySelectorAll("col")];

    expect(cols.map((col) => col.className))
      .toEqual(["col-pos", "col-box", "col-team", "col-draw", "col-team", "col-box"]);
    cols.forEach((col) => expect(col.getAttribute("style")).toBeNull());
    expect(PRINTABLE_TICKET_STYLES).toContain(".col-pos { width:");
    expect(PRINTABLE_TICKET_STYLES).toContain(".col-draw { width:");
  });
});

describe("mountPrintableTicket", () => {
  function popup() {
    const dom = new JSDOM("<!doctype html><html><body></body></html>", { pretendToBeVisual: true });
    dom.window.print = () => {
      dom.window.__printed = (dom.window.__printed || 0) + 1;
    };
    return dom.window;
  }

  it("writes the sheet and gives it the stylesheet the document cannot carry", () => {
    const win = popup();
    const mounted = mountPrintableTicket(win, buildPrintableTicketHtml(BASE));

    expect(mounted.querySelectorAll("tbody tr")).toHaveLength(6);
    // Either route is fine as long as the rules land: constructable stylesheet
    // where available, a <style> element where it is not.
    const adopted = (mounted.adoptedStyleSheets || []).length > 0;
    const styleTag = mounted.querySelector("style")?.textContent || "";
    expect(adopted || styleTag.includes("@page")).toBe(true);
  });

  it("wires the print button that no longer carries an onclick", () => {
    const win = popup();
    const mounted = mountPrintableTicket(win, buildPrintableTicketHtml(BASE));

    mounted.querySelector("[data-print]").dispatchEvent(new win.Event("click"));

    expect(win.__printed).toBe(1);
  });

  it("drops a crest that fails to load so the monogram shows through", () => {
    const win = popup();
    const mounted = mountPrintableTicket(win, buildPrintableTicketHtml({
      ...BASE,
      rows: [{ ...ROWS[0], home_team_crest: "https://cdn.example/never.png" }],
    }));
    const img = mounted.querySelector(".crest img");

    img.dispatchEvent(new win.Event("error"));

    expect(mounted.querySelector(".crest img")).toBeNull();
    expect(mounted.querySelector(".crest-mono").textContent).toBe("SL");
  });
});

describe("buildPrintableTicketHtml legibility and framing", () => {
  it("declares itself a light document so no browser force-darkens it", () => {
    // Opera GX repaints undeclared pages dark, which turned the grey framing
    // the sheet into a black band above it.
    const page = doc(buildPrintableTicketHtml(BASE));

    expect(page.querySelector('meta[name="color-scheme"]').getAttribute("content")).toBe("light");
    expect(PRINTABLE_TICKET_STYLES).toMatch(/:root\s*{[^}]*color-scheme:\s*light/);
  });

  it("opens with the print control, with nothing above it", () => {
    const body = doc(buildPrintableTicketHtml(BASE)).body;

    expect(body.firstElementChild.className).toBe("actions");
    expect(body.firstElementChild.querySelector("[data-print]")).not.toBeNull();
  });

  it("keeps secondary text dark enough to read", () => {
    expect(PRINTABLE_TICKET_STYLES).toContain("--ink-soft: #52616b");
  });

  it("separates the marked play from the ticket it recommends buying", () => {
    const page = doc(buildPrintableTicketHtml({
      ...BASE,
      slateOptions: {
        recommended_action: "COMPRAR_CONSERVADORA",
        options: [{ name: "Conservadora", recommended: true, combinations: 3888, price_status: "unverified" }],
      },
    }));
    const captions = [...page.querySelectorAll(".section-caption")].map((node) => node.textContent.trim());

    expect(captions[0]).toContain("Jugada base");
    expect(captions[1]).toContain("Boleto recomendado");
    // The recommendation carries the name of the ticket its numbers belong to.
    expect(captions[1]).toContain("Conservadora");
  });

  it("makes the unverified price stand out without taking the page over", () => {
    const page = doc(buildPrintableTicketHtml({
      ...BASE,
      slateOptions: { options: [{ name: "Conservadora", recommended: true, combinations: 3888, price_status: "unverified" }] },
    }));
    const warn = [...page.querySelectorAll(".badge.warn")];

    expect(warn).toHaveLength(1);
    expect(warn[0].textContent).toContain("no verificado");
    // It is a badge among badges, not a banner.
    expect(page.querySelectorAll(".summary .badge").length).toBeGreaterThan(1);
    expect(PRINTABLE_TICKET_STYLES).toMatch(/\.badge\.warn\s*{[^}]*border:\s*1\.5px solid var\(--warn\)/);
    expect(PRINTABLE_TICKET_STYLES).toContain("--warn-ink: #7a4d0c");
  });
});

describe("buildPrintableTicketHtml print rules", () => {
  it("targets a single A4 portrait page", () => {
    expect(PRINTABLE_TICKET_STYLES).toContain("@page { size: A4 portrait; margin: 12mm; }");
  });

  it("keeps the fills the marks are made of", () => {
    // Chromium prints with "Background graphics" off by default; without this
    // the green boxes would come out blank on paper.
    const rules = printRules;
    expect(rules).toContain("print-color-adjust: exact");
    expect(rules).toContain("-webkit-print-color-adjust: exact");
    // And the letter stays dark, so a marked sign reads even if a viewer
    // drops the fill anyway.
    expect(rules).toMatch(/\.box\.on\s*{[^}]*color:\s*var\(--green-ink\)/);
  });

  it("never prints the web controls", () => {
    expect(printRules).toMatch(/\.actions\s*{\s*display:\s*none/);
  });

  it("keeps a row and a card whole when the sheet spills onto a second page", () => {
    const rules = printRules;
    expect(rules).toContain("break-inside: avoid");
    expect(rules).toContain("page-break-inside: avoid");
    expect(rules).toContain("table-header-group");
  });
});
