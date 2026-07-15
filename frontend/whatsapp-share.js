export function buildWhatsAppShareUrl(text) {
  const message = String(text || "").trim();
  return `https://wa.me/?text=${encodeURIComponent(message)}`;
}

export function buildWhatsAppTicketText({ slate, modeLabel, rows, warnings = [] }) {
  const drawCode = slate?.draw_code || "Quiniela";
  const label = slate?.label && slate.label !== drawCode ? ` · ${slate.label}` : "";
  const lines = [
    `Quiniela: ${drawCode}${label}`,
    `Modo: ${modeLabel || "Jugada seleccionada"}`,
    "",
    "Partidos y jugada:",
  ];
  (rows || []).forEach((row) => {
    lines.push(`${row.position}. ${row.home_team_name} vs ${row.away_team_name}: ${row.pick}`);
  });
  if (warnings.length) {
    lines.push("", "Revisar nombres:");
    warnings.forEach((warning) => {
      lines.push(`- ${warning.position}. ${warning.message}`);
    });
  }
  return lines.join("\n");
}
