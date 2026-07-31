import { escapeHtml } from "./helpers.js";
import { TICKET_LABEL } from "./money-mode.js";

// The printable boleta. Presentation only: every number, sign and verdict on
// the sheet is handed in by app.js exactly as the model produced it. Nothing
// here decides, orders or recalculates anything.
//
// The renderer is the browser's own print engine (app.js opens a popup and
// calls window.print()), so the layout is plain HTML + CSS with no external
// font, no remote asset and no library — it has to render identically with the
// network unplugged.

// The papeleta's own vocabulary: the model picks "1"/"X"/"2", the sheet is
// marked L/E/V.
const OUTCOME_LABELS = { "1": "L", X: "E", "2": "V" };

// How the play reads at a glance. Derived from the picks already on the rows —
// counting how many signs a position plays is not a decision, it is a label.
const PLAY_SHAPE = { 1: "Simple", 2: "Doble", 3: "Triple" };

const MONEY_MODE_LABELS = {
  JUGAR_BALANCEADO: "JUGAR · BALANCEADO",
  JUGAR_SOLO_BALANCEADO: "JUGAR SOLO BALANCEADO",
  JUGAR_SOLO_CONSERVADOR: "JUGAR SOLO CONSERVADOR",
  JUGAR_SOLO_AGRESIVO: "JUGAR SOLO AGRESIVO",
  JUGAR_CON_CAUTELA: "JUGAR CON CAUTELA",
  NO_JUGAR: "NO JUGAR",
};

const OPTION_ACTIONS = {
  NO_COMPRAR: "No comprar boleto",
  COMPRAR_BALANCEADA: "Comprar boleto balanceado",
  COMPRAR_CONSERVADORA: "Comprar boleto conservador",
  COMPRAR_AGRESIVA: "Comprar boleto agresivo",
  COMPRAR_CON_CAUTELA: "Comprar con cautela",
  REVISAR: "Revisar",
};

const GATE_POSITION_LIMIT = 6;
const LONG_NAME_CHARS = 20;

// --- small pieces -----------------------------------------------------------

function chip(text, tone = "") {
  return `<span class="chip ${tone}">${escapeHtml(text)}</span>`;
}

function badge(label, value, tone = "") {
  return `
    <div class="badge ${tone}">
      <span class="badge-label">${escapeHtml(label)}</span>
      <span class="badge-value">${escapeHtml(value)}</span>
    </div>`;
}

// Connectors carry no identity: without this "Atletico de San Luis" monograms
// as "AD" instead of "AS".
const NAME_CONNECTORS = new Set(["de", "del", "la", "las", "los", "y", "e", "do", "da", "dos", "das", "of", "the"]);

/** Up to two initials, as a typographic stand-in for a crest we do not have. */
function initials(name) {
  const words = String(name || "")
    .split(/[\s.]+/)
    .filter((word) => /[a-zA-ZÀ-ÿ0-9]/.test(word));
  if (!words.length) return "?";
  // The leading word always counts, connector or not: dropping it turns
  // "Los Angeles FC" into "AF".
  const source = [words[0], ...words.slice(1).filter((word) => !NAME_CONNECTORS.has(word.toLowerCase()))];
  return source.slice(0, 2).map((word) => word[0]).join("").toUpperCase();
}

// No team in the model carries a crest today — `teams` is (id, name, country,
// is_placeholder) and no connector ingests badges — so every row renders the
// monogram. `crest` stays an accepted per-row field so that the day a URL the
// system already resolved is passed in, it shows up here; the monogram sits
// underneath as the fallback, which is also what a broken or offline image
// falls back to. Nothing is ever fetched to build this sheet.
function crest(name, url) {
  const mono = `<span class="crest-mono">${escapeHtml(initials(name))}</span>`;
  if (!url) return `<span class="crest">${mono}</span>`;
  // No inline onerror: the CSP that governs this popup forbids it. The fallback
  // is wired in `mountPrintableTicket`.
  return `<span class="crest">${mono}<img src="${escapeHtml(url)}" alt="" /></span>`;
}

function teamName(name) {
  const value = String(name ?? "");
  const long = value.length > LONG_NAME_CHARS ? " long" : "";
  return `<span class="name${long}">${escapeHtml(value)}</span>`;
}

/** One L/E/V box. `on` when the play marks that sign for this position. */
function outcomeBox(picks, outcome) {
  const on = Array.isArray(picks) && picks.includes(outcome);
  return `<td class="box-cell"><span class="box${on ? " on" : ""}">${OUTCOME_LABELS[outcome]}</span></td>`;
}

function rowHtml(row) {
  return `
    <tr>
      <td class="pos">${escapeHtml(row.position)}</td>
      ${outcomeBox(row.picks, "1")}
      <td class="team home">${crest(row.home_team_name, row.home_team_crest)}${teamName(row.home_team_name)}</td>
      ${outcomeBox(row.picks, "X")}
      <td class="team away">${teamName(row.away_team_name)}${crest(row.away_team_name, row.away_team_crest)}</td>
      ${outcomeBox(row.picks, "2")}
    </tr>`;
}

function playShape(rows) {
  const sizes = new Set(
    (rows || []).map((row) => (Array.isArray(row.picks) ? row.picks.length : 0)).filter(Boolean),
  );
  if (!sizes.size) return "";
  if (sizes.size > 1) return "Mixto";
  return PLAY_SHAPE[[...sizes][0]] || "";
}

// --- header and context blocks ----------------------------------------------

function moneyModeCard(report) {
  const decision = report?.decision;
  if (!decision) return "";
  const status = decision.status || "";
  const stop = status === "NO_JUGAR";
  const label = MONEY_MODE_LABELS[status] || status || "sin decisión";
  const ticket = decision.recommended_ticket;
  const recommended = ticket ? TICKET_LABEL[ticket] || ticket : "ninguno";
  return `
    <section class="verdict ${stop ? "stop" : "play"}">
      <div class="verdict-head">
        <span class="verdict-tag">Money Mode</span>
        <strong class="verdict-status">${escapeHtml(label)}</strong>
        <span class="verdict-ticket">Boleto: ${escapeHtml(recommended)}</span>
      </div>
      <p class="verdict-reason">${escapeHtml(decision.reason || "Sin motivo técnico disponible.")}</p>
      ${stop ? `<p class="verdict-stop">No comprar boleto</p>` : ""}
    </section>`;
}

// The same diagnostic that disables the WhatsApp button. It cannot disable
// this one: an operator still needs to print a boleta to review it, and the
// blocked positions are precisely what they are reviewing. So the document
// says so itself — a sheet that leaves the building carries its own verdict
// instead of looking identical to a cleared one.
function publishGateBanner(gate) {
  if (!gate || gate.whatsapp_allowed !== false) return "";
  const positions = Array.isArray(gate.blocked_positions) ? gate.blocked_positions : [];
  const shown = positions.slice(0, GATE_POSITION_LIMIT);
  const rest = positions.length - shown.length;
  const count = gate.blocked_count ?? positions.length;
  return `
    <section class="gate-banner">
      <div class="gate-head">
        <strong>No apto para envío</strong>
        <span>${escapeHtml(count)} posición(es) bloqueada(s) por el diagnóstico</span>
      </div>
      ${shown.length
        ? `<ul class="gate-list">${shown
            .map(
              (item) =>
                `<li><span class="gate-pos">#${escapeHtml(item.position)}</span> ${escapeHtml(
                  item.match || "",
                )} · ${escapeHtml((item.reasons || []).join("; ") || "sin motivo declarado")}</li>`,
            )
            .join("")}${rest > 0 ? `<li>y ${escapeHtml(rest)} más</li>` : ""}</ul>`
        : ""}
    </section>`;
}

// --- summary under the grid -------------------------------------------------

function recommendedOption(report) {
  if (!report || !Array.isArray(report.options)) return null;
  return report.options.find((option) => option.recommended) || null;
}

function verifiedCost(option) {
  if (!option || option.price_status !== "verified" || option.estimated_cost == null) return null;
  return `$${option.estimated_cost} ${option.currency || "MXN"}`;
}

function uncoveredPositions(moneyMode) {
  const ticketKey = moneyMode?.decision?.recommended_ticket;
  const ticket = ticketKey ? (moneyMode.tickets || {})[ticketKey] : null;
  const positions = ticket?.uncovered_no_simple_positions;
  return Array.isArray(positions) ? positions : [];
}

function summarySection(slateOptions, moneyMode) {
  const option = recommendedOption(slateOptions);
  const cost = verifiedCost(option);
  const uncovered = uncoveredPositions(moneyMode);
  const action = slateOptions
    ? OPTION_ACTIONS[slateOptions.recommended_action] || slateOptions.recommended_action || "Revisar"
    : "";
  const badges = [
    option ? badge("Opción recomendada", option.name || "—") : "",
    option?.combinations != null ? badge("Combinaciones", option.combinations) : "",
    cost ? badge("Precio verificado", cost) : "",
    !cost && option ? badge("Precio", "no verificado", "warn") : "",
    uncovered.length
      ? badge("Sin cobertura", uncovered.map((position) => `#${position}`).join(" "), "warn")
      : "",
    action ? badge("Acción", action) : "",
  ].filter(Boolean);
  if (!badges.length) return "";
  // Named after what it describes, so the combination count and the price are
  // read as the recommended ticket's, never as the marked grid's.
  const ticketName = option?.name ? ` · ${option.name}` : "";
  return `
    <p class="section-caption">Boleto recomendado${escapeHtml(ticketName)}</p>
    <section class="summary">${badges.join("")}</section>`;
}

function nameWarningsSummary(warnings) {
  if (!warnings?.length) return "";
  return `
    <section class="notice warn">
      <strong>Revisar nombres antes de comprar</strong>
      <span>${warnings
        .map((warning) => `#${escapeHtml(warning.position)} ${escapeHtml(warning.message)}`)
        .join(" · ")}</span>
    </section>`;
}

// --- styles -----------------------------------------------------------------

// One place for the sheet's look. Print rules live beside the screen rules they
// override so a change to either is made in view of the other.
//
// Exported rather than inlined into the document because the popup inherits the
// app's CSP (`style-src 'self'`, no `unsafe-inline`): a <style> block written
// into it is dropped on the floor, which is why the boleta printed as bare
// unstyled HTML. `mountPrintableTicket` adopts this through CSSOM instead,
// which CSP does not police.
export const PRINTABLE_TICKET_STYLES = `
  :root {
    /* The boleta is a light document and says so. Without this a browser that
       forces dark rendering on undeclared pages — Opera GX does — repaints the
       grey that frames the sheet as near-black, which shows up as a dark band
       above it. Declaring the scheme opts the document out at the source
       instead of covering the band with something. */
    color-scheme: light;
    --green: #28a57a;
    --green-dark: #19785a;
    --green-ink: #06231a;
    --ink: #1f2933;
    /* Secondary text: dark enough to read on paper and on the zebra rows,
       still clearly a step below --ink. */
    --ink-soft: #52616b;
    --line: #dce3e1;
    --zebra: #f5f7f7;
    --warn: #b4741a;
    --warn-ink: #7a4d0c;
    --warn-bg: #fdf4e6;
  }
  * { box-sizing: border-box; }
  html, body {
    margin: 0;
    padding: 0;
    color: var(--ink);
    background: #eef1f0;
    font-family: "Segoe UI", Roboto, "Helvetica Neue", Arial, "Noto Sans", sans-serif;
    font-size: 12px;
    line-height: 1.35;
    -webkit-font-smoothing: antialiased;
  }
  .sheet {
    width: 186mm;
    margin: 16px auto;
    padding: 14px 16px 12px;
    background: #fff;
    border-radius: 12px;
  }

  /* Header */
  .head { display: flex; justify-content: space-between; align-items: flex-start; gap: 16px; }
  .draw-code {
    display: inline-block;
    padding: 2px 8px;
    border-radius: 6px;
    background: var(--green-ink);
    color: #fff;
    font-size: 13px;
    font-weight: 800;
    letter-spacing: 0.06em;
  }
  h1 { margin: 5px 0 0; font-size: 17px; font-weight: 700; letter-spacing: 0.01em; text-transform: uppercase; }
  .head-meta { text-align: right; color: var(--ink-soft); font-size: 10.5px; line-height: 1.5; }
  .head-meta strong { display: block; color: var(--ink); font-size: 11px; }
  .chips { display: flex; flex-wrap: wrap; gap: 6px; margin: 9px 0 0; }
  .chip {
    padding: 3px 9px;
    border: 1px solid var(--line);
    border-radius: 999px;
    background: var(--zebra);
    font-size: 10px;
    font-weight: 700;
    letter-spacing: 0.05em;
    text-transform: uppercase;
  }
  .chip.accent { border-color: var(--green); background: #e8f6f1; color: var(--green-dark); }

  /* Money Mode verdict */
  .verdict {
    margin-top: 9px;
    padding: 7px 10px;
    border: 1px solid var(--line);
    border-left: 4px solid var(--green);
    border-radius: 8px;
    background: #f7fbf9;
  }
  .verdict.stop { border-left-color: #c2410c; background: #fdf4f1; }
  .verdict-head { display: flex; align-items: baseline; gap: 8px; flex-wrap: wrap; }
  .verdict-tag { font-size: 9.5px; font-weight: 700; letter-spacing: 0.08em; text-transform: uppercase; color: var(--ink-soft); }
  .verdict-status { font-size: 13px; letter-spacing: 0.01em; }
  .verdict-ticket { margin-left: auto; font-size: 10.5px; color: var(--ink-soft); }
  .verdict-reason { margin: 2px 0 0; font-size: 10.5px; color: var(--ink-soft); }
  .verdict-stop { margin: 4px 0 0; font-size: 11px; font-weight: 800; text-transform: uppercase; color: #b91c1c; }

  /* Publish gate */
  .gate-banner {
    margin-top: 9px;
    padding: 7px 10px;
    border: 1.5px solid #b91c1c;
    border-radius: 8px;
    background: #fdf2f2;
  }
  .gate-head { display: flex; align-items: baseline; gap: 8px; flex-wrap: wrap; }
  .gate-head strong { font-size: 12px; text-transform: uppercase; letter-spacing: 0.04em; color: #b91c1c; }
  .gate-head span { font-size: 10.5px; color: var(--ink-soft); }
  .gate-list { margin: 4px 0 0; padding-left: 14px; font-size: 10px; color: var(--ink-soft); }
  .gate-pos { font-weight: 700; color: var(--ink); }

  /* Grid */
  .grid-card { border: 1px solid var(--line); border-radius: 10px; overflow: hidden; }
  table { width: 100%; border-collapse: collapse; table-layout: fixed; }
  /* Column widths belong here, not in a style="" attribute on <col>: the CSP
     governing this popup drops inline styles, which left every column an equal
     sixth and truncated the club names. The two team columns share whatever the
     fixed ones leave. */
  .col-pos { width: 22px; }
  .col-box { width: 32px; }
  .col-draw { width: 36px; }
  thead th {
    padding: 5px 8px;
    background: var(--zebra);
    border-bottom: 1px solid var(--line);
    font-size: 9.5px;
    font-weight: 700;
    letter-spacing: 0.09em;
    text-transform: uppercase;
    color: var(--ink-soft);
  }
  thead th.th-home { text-align: left; }
  thead th.th-away { text-align: right; }
  tbody tr { height: 38px; }
  tbody tr:nth-child(even) { background: var(--zebra); }
  tbody td { padding: 4px 6px; border-top: 1px solid var(--line); vertical-align: middle; }
  tbody tr:first-child td { border-top: 0; }
  td.pos { text-align: center; font-size: 10.5px; font-weight: 700; color: var(--ink-soft); }
  td.box-cell { text-align: center; padding: 3px 2px; }
  /* The cells stay table cells — the crest and the name are laid out inline
     inside them, so the fixed column widths keep holding. */
  td.team { overflow: hidden; white-space: nowrap; }
  td.team.away { text-align: right; }
  td.team.home .crest { margin-right: 6px; }
  td.team.away .crest { margin-left: 6px; }
  .name {
    display: inline-block;
    vertical-align: middle;
    max-width: calc(100% - 28px);
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
    font-size: 11.5px;
    font-weight: 600;
  }
  .name.long { font-size: 10px; letter-spacing: -0.01em; }

  /* Crest slot: monogram by default, image on top when one is supplied. */
  .crest {
    position: relative;
    flex: 0 0 auto;
    display: inline-flex;
    align-items: center;
    justify-content: center;
    width: 20px;
    height: 20px;
    /* Filled disc, no outline: an outlined white circle beside the outlined
       white L/E/V boxes reads as a fourth thing to tick. */
    border-radius: 50%;
    background: #eceff0;
    vertical-align: middle;
  }
  .crest-mono { font-size: 8px; font-weight: 800; letter-spacing: 0.02em; color: var(--ink-soft); }
  .crest img { position: absolute; inset: 0; width: 100%; height: 100%; border-radius: 50%; object-fit: contain; background: #fff; }

  /* L / E / V boxes */
  .box {
    display: inline-block;
    min-width: 24px;
    padding: 2px 0;
    border: 1px solid var(--line);
    border-radius: 5px;
    background: #fff;
    font-size: 11px;
    font-weight: 700;
    text-align: center;
    color: #93a5a0;
  }
  .box.on { border: 2px solid var(--green-dark); background: var(--green); color: var(--green-ink); }

  /* Summary + footer */
  /* Two captions, because the grid and the summary answer different questions:
     the grid is the play as marked, the summary is the ticket proAI recommends
     buying — on a slate of fourteen simples sitting beside a 3888-combination
     recommendation, the two were easy to read as one. */
  .section-caption {
    margin: 10px 0 4px;
    font-size: 9px;
    font-weight: 700;
    letter-spacing: 0.11em;
    text-transform: uppercase;
    color: var(--ink-soft);
  }
  .summary { display: flex; flex-wrap: wrap; gap: 6px; }
  .badge { padding: 4px 9px; border: 1px solid var(--line); border-radius: 8px; background: #fff; }
  .badge.warn { border: 1.5px solid var(--warn); background: var(--warn-bg); }
  .badge.warn .badge-label, .badge.warn .badge-value { color: var(--warn-ink); }
  .badge-label { display: block; font-size: 8.5px; font-weight: 700; letter-spacing: 0.07em; text-transform: uppercase; color: var(--ink-soft); }
  .badge-value { font-size: 11.5px; font-weight: 700; }
  .notice { display: flex; gap: 8px; align-items: baseline; margin-top: 8px; padding: 6px 10px; border: 1px solid var(--line); border-radius: 8px; font-size: 10.5px; }
  .notice.warn { border: 1.5px solid var(--warn); background: var(--warn-bg); color: var(--warn-ink); }
  .notice strong { white-space: nowrap; }
  .foot { display: flex; justify-content: space-between; gap: 12px; margin: 10px 0 0; padding-top: 7px; border-top: 1px solid var(--line); font-size: 9.5px; color: var(--ink-soft); }

  /* Screen-only controls */
  .actions { display: flex; gap: 8px; margin: 0 auto 16px; width: 186mm; }
  .actions button {
    padding: 9px 14px;
    border: 0;
    border-radius: 8px;
    background: var(--green-dark);
    color: #fff;
    font-family: inherit;
    font-size: 13px;
    font-weight: 700;
    cursor: pointer;
  }

  @page { size: A4 portrait; margin: 12mm; }
  @media print {
    /* Keep the fills the design uses. Chromium honours this even when the
       "Background graphics" box is unchecked, which is the default. */
    html, body {
      background: #fff;
      -webkit-print-color-adjust: exact;
      print-color-adjust: exact;
    }
    .sheet { width: auto; margin: 0; padding: 0; border-radius: 0; }
    .actions { display: none; }
    /* A marked sign must not depend on the fill surviving: the letter is dark
       green on a 2px dark-green border, so it reads whether or not the green
       background prints. Borders are never dropped as background graphics. */
    .box.on { color: var(--green-ink); }
    .grid-card { border-radius: 0; }
    tr, .verdict, .gate-banner, .badge, .notice { break-inside: avoid; page-break-inside: avoid; }
    thead { display: table-header-group; }
  }
`;

// --- document ---------------------------------------------------------------

export function buildPrintableTicketHtml({
  slate,
  modeLabel,
  rows,
  generatedAt,
  moneyMode = null,
  slateOptions = null,
  nameWarnings = [],
  publishGate = null,
}) {
  const drawCode = slate?.draw_code || "Quiniela";
  const label = slate?.label && slate.label !== drawCode ? slate.label : "Boleta Progol";
  const generated = generatedAt ? new Date(generatedAt).toLocaleString("es-MX") : "";
  const list = rows || [];
  const shape = playShape(list);
  const option = recommendedOption(slateOptions);
  // The count belongs to the RECOMMENDED OPTION, not to the grid above it: on
  // PG-2344 the operator's marked play is fourteen simples while the option
  // proAI recommends buying is the conservative 3888-combination ticket. A bare
  // "3888 combinaciones" chip beside "SIMPLE" reads as a contradiction, so the
  // chip carries the name of the ticket the number describes.
  const combinations = option?.combinations;
  const chips = [
    shape ? chip(shape, "accent") : "",
    chip(`${list.length} partidos`),
    combinations != null
      ? chip(`${option.name || "Opción"} · ${combinations} ${Number(combinations) === 1 ? "combinación" : "combinaciones"}`)
      : "",
    // The ticket mode only earns a chip when it says something the shape did
    // not — "SIMPLE · MODO SIMPLE" is noise.
    modeLabel && modeLabel.toLowerCase() !== shape.toLowerCase() ? chip(`Modo ${modeLabel}`) : "",
  ].filter(Boolean);
  return `<!doctype html>
<html lang="es">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <meta name="color-scheme" content="light" />
  <title>${escapeHtml(drawCode)} boleta</title>
</head>
<body>
  <div class="actions">
    <button type="button" data-print>Imprimir / Guardar PDF</button>
  </div>
  <main class="sheet">
    <header class="head">
      <div>
        <span class="draw-code">${escapeHtml(drawCode)}</span>
        <h1>${escapeHtml(label)}</h1>
      </div>
      <div class="head-meta">
        <strong>Boleta proAI</strong>
        ${generated ? `Generada ${escapeHtml(generated)}` : ""}
      </div>
    </header>
    <div class="chips">${chips.join("")}</div>
    ${publishGateBanner(publishGate)}
    ${moneyModeCard(moneyMode)}
    <p class="section-caption">Jugada base · marca estos signos</p>
    <div class="grid-card">
      <table>
        <colgroup>
          <col class="col-pos" />
          <col class="col-box" />
          <col class="col-team" />
          <col class="col-draw" />
          <col class="col-team" />
          <col class="col-box" />
        </colgroup>
        <thead>
          <tr>
            <th class="pos">#</th>
            <th colspan="2" class="th-home">Local</th>
            <th class="th-draw">Empate</th>
            <th colspan="2" class="th-away">Visitante</th>
          </tr>
        </thead>
        <tbody>${list.map(rowHtml).join("")}</tbody>
      </table>
    </div>
    ${summarySection(slateOptions, moneyMode)}
    ${nameWarningsSummary(nameWarnings)}
    <footer class="foot">
      <span>Boleto generado por proAI. Verifica la jugada contra la papeleta oficial antes de comprar.</span>
      <span>${escapeHtml(drawCode)} · ${escapeHtml(label)}</span>
    </footer>
  </main>
</body>
</html>`;
}

/**
 * Write a built boleta into a popup window and make it look like one.
 *
 * The popup `window.open("")` returns is an about:blank document that inherits
 * the app's Content-Security-Policy — `style-src 'self'; script-src 'self'`,
 * neither with `unsafe-inline`. So a <style> block written into the document is
 * discarded (a <style> element the opener creates is discarded too), and any
 * `onclick`/`onerror` attribute never fires. The sheet came out as unstyled
 * HTML with a dead print button.
 *
 * CSSOM is not subject to those directives, so the stylesheet is adopted
 * instead of embedded, and the handlers are attached as listeners. Falls back
 * to a <style> element where constructable stylesheets are unavailable —
 * harmless when a CSP is not in the way, which is the only case it can help.
 *
 * @param {Window} win  the popup returned by window.open
 * @param {string} html the document from buildPrintableTicketHtml
 */
export function mountPrintableTicket(win, html) {
  win.document.open();
  win.document.write(html);
  win.document.close();
  const doc = win.document;
  try {
    const sheet = new win.CSSStyleSheet();
    sheet.replaceSync(PRINTABLE_TICKET_STYLES);
    doc.adoptedStyleSheets = [...doc.adoptedStyleSheets, sheet];
  } catch {
    const style = doc.createElement("style");
    style.textContent = PRINTABLE_TICKET_STYLES;
    doc.head.appendChild(style);
  }
  doc.querySelector("[data-print]")?.addEventListener("click", () => win.print());
  // A crest that cannot load uncovers the monogram underneath it.
  doc.querySelectorAll(".crest img").forEach((img) => {
    img.addEventListener("error", () => img.remove());
  });
  return doc;
}
