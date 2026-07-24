function drawNumber(value) {
  const matches = String(value || "").match(/\d+/g);
  return matches?.length ? Number(matches[matches.length - 1]) : null;
}

export function pickNextProposal(proposals = [], slates = []) {
  const activeDrawByWeekType = new Map();
  for (const slate of slates) {
    if (slate?.is_archived || slate?.is_closed) continue;
    const number = drawNumber(slate?.draw_code);
    if (number === null) continue;
    const current = activeDrawByWeekType.get(slate.week_type);
    if (current === undefined || number > current) {
      activeDrawByWeekType.set(slate.week_type, number);
    }
  }

  return proposals
    .filter((proposal) => proposal?.status === "validated" && !proposal.promoted_slate_id)
    .filter((proposal) => {
      const activeDraw = activeDrawByWeekType.get(proposal.week_type);
      const proposalDraw = drawNumber(proposal.draw_code);
      return activeDraw === undefined || proposalDraw === null || proposalDraw > activeDraw;
    })
    .slice()
    .sort((left, right) => {
      const drawDelta = (drawNumber(right.draw_code) ?? -1) - (drawNumber(left.draw_code) ?? -1);
      if (drawDelta !== 0) return drawDelta;
      return new Date(right.last_seen_at).getTime() - new Date(left.last_seen_at).getTime();
    })[0] || null;
}
