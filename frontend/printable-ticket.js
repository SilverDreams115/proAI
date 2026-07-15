import { escapeHtml } from "./helpers.js";

const OUTCOME_LABELS = { "1": "L", X: "E", "2": "V" };
const OUTCOME_ORDER = ["1", "X", "2"];

function selectedCell(picks, outcome) {
  const selected = Array.isArray(picks) && picks.includes(outcome);
  return `<td class="${selected ? "selected" : ""}">${selected ? "X" : ""}</td>`;
}

function rowHtml(row) {
  return `
    <tr>
      <td class="pos">${escapeHtml(row.position)}</td>
      <td class="match">${escapeHtml(row.home_team_name)} vs ${escapeHtml(row.away_team_name)}</td>
      ${OUTCOME_ORDER.map((outcome) => selectedCell(row.picks, outcome)).join("")}
      <td class="pick">${escapeHtml(row.pick)}</td>
    </tr>`;
}

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

function moneyModeSummary(report) {
  const decision = report?.decision;
  if (!decision) return "";
  const status = decision.status || "";
  const label = MONEY_MODE_LABELS[status] || status || "sin decisión";
  const recommended = decision.recommended_ticket || "ninguno";
  const reason = decision.reason || "Sin motivo técnico disponible.";
  return `
    <section class="context-box ${status === "NO_JUGAR" ? "danger" : "ok"}">
      <strong>Money Mode: ${escapeHtml(label)}</strong>
      <p>Recomendado: ${escapeHtml(recommended)} · ${escapeHtml(reason)}</p>
      ${status === "NO_JUGAR" ? `<p class="operator-warning">NO COMPRAR BOLETO</p>` : ""}
    </section>`;
}

function optionCost(option) {
  if (!option || option.price_status !== "verified" || option.estimated_cost == null) {
    return "precio no verificado";
  }
  return `$${option.estimated_cost} ${option.currency || "MXN"}`;
}

function slateOptionsSummary(report) {
  if (!report || !Array.isArray(report.options)) return "";
  const recommended = report.options.find((option) => option.recommended) || null;
  const action = OPTION_ACTIONS[report.recommended_action] || report.recommended_action || "Revisar";
  const optionLine = recommended
    ? `${recommended.name}: ${recommended.combinations} combinaciones · ${optionCost(recommended)}`
    : "Sin opción recomendada";
  return `
    <section class="context-box">
      <strong>Opciones de boleto</strong>
      <p>Acción: ${escapeHtml(action)} · ${escapeHtml(optionLine)}</p>
      ${report.pricing_verified ? "" : `<p>Precio no verificado: validar contra fuente oficial antes de comprar.</p>`}
    </section>`;
}

function nameWarningsSummary(warnings) {
  if (!warnings?.length) return "";
  return `
    <section class="context-box warn">
      <strong>Revisar nombres antes de comprar</strong>
      <ul>${warnings.map((warning) => `<li>#${escapeHtml(warning.position)} · ${escapeHtml(warning.message)}</li>`).join("")}</ul>
    </section>`;
}

export function buildPrintableTicketHtml({ slate, modeLabel, rows, generatedAt, moneyMode = null, slateOptions = null, nameWarnings = [] }) {
  const drawCode = slate?.draw_code || "Quiniela";
  const label = slate?.label && slate.label !== drawCode ? slate.label : "";
  const generated = generatedAt ? new Date(generatedAt).toLocaleString("es-MX") : "";
  return `<!doctype html>
<html lang="es">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>${escapeHtml(drawCode)} boleta</title>
  <style>
    :root { color: #111827; font-family: Arial, Helvetica, sans-serif; }
    body { margin: 0; background: #f3f4f6; }
    .sheet { width: min(920px, calc(100% - 32px)); margin: 24px auto; background: #fff; padding: 28px; border: 1px solid #d1d5db; }
    .top { display: flex; justify-content: space-between; gap: 16px; align-items: flex-start; border-bottom: 2px solid #111827; padding-bottom: 14px; }
    h1 { margin: 0; font-size: 28px; letter-spacing: 0; }
    .meta { margin: 6px 0 0; color: #4b5563; font-size: 14px; }
    .actions { display: flex; gap: 8px; }
    button { border: 1px solid #111827; background: #111827; color: #fff; padding: 9px 12px; font-weight: 700; cursor: pointer; }
    table { width: 100%; border-collapse: collapse; margin-top: 18px; font-size: 14px; }
    th, td { border: 1px solid #9ca3af; padding: 8px; text-align: center; }
    th { background: #e5e7eb; font-size: 12px; text-transform: uppercase; }
    .pos { width: 42px; font-weight: 700; }
    .match { text-align: left; font-weight: 600; }
    .selected { background: #111827; color: #fff; font-weight: 800; }
    .pick { width: 72px; font-weight: 700; }
    .context-box { margin-top: 14px; border: 1px solid #9ca3af; padding: 10px 12px; font-size: 13px; }
    .context-box p { margin: 6px 0 0; }
    .context-box ul { margin: 8px 0 0 18px; padding: 0; }
    .context-box.ok { border-color: #15803d; }
    .context-box.warn { border-color: #b45309; }
    .context-box.danger { border-color: #b91c1c; background: #fef2f2; }
    .operator-warning { color: #b91c1c; font-weight: 800; text-transform: uppercase; }
    .foot { margin-top: 14px; color: #4b5563; font-size: 12px; }
    @media print {
      body { background: #fff; }
      .sheet { width: auto; margin: 0; border: 0; padding: 0; }
      .actions { display: none; }
      th, td { padding: 6px; }
    }
  </style>
</head>
<body>
  <main class="sheet">
    <div class="top">
      <div>
        <h1>${escapeHtml(drawCode)}${label ? ` · ${escapeHtml(label)}` : ""}</h1>
        <p class="meta">Modo: ${escapeHtml(modeLabel || "Jugada seleccionada")} · Partidos: ${escapeHtml((rows || []).length)}</p>
        ${generated ? `<p class="meta">Generado: ${escapeHtml(generated)}</p>` : ""}
      </div>
      <div class="actions">
        <button type="button" onclick="window.print()">Imprimir / Guardar PDF</button>
      </div>
    </div>
    ${moneyModeSummary(moneyMode)}
    ${slateOptionsSummary(slateOptions)}
    ${nameWarningsSummary(nameWarnings)}
    <table>
      <thead>
        <tr>
          <th>#</th>
          <th>Partido</th>
          <th>${OUTCOME_LABELS["1"]}</th>
          <th>${OUTCOME_LABELS.X}</th>
          <th>${OUTCOME_LABELS["2"]}</th>
          <th>Jugada</th>
        </tr>
      </thead>
      <tbody>${(rows || []).map(rowHtml).join("")}</tbody>
    </table>
    <p class="foot">Boleta generada desde proAI. Revisa manualmente contra la papeleta oficial antes de comprar.</p>
  </main>
</body>
</html>`;
}
