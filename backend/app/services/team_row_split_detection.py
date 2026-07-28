"""Detect club rows that two ingestion sources split in two.

The Progol guide writes club names in short form. When a short form
matches no team row and no alias, ``EntityResolutionService.resolve_team``
mints a brand-new row with zero history, and the slate position is then
scored as if the club had never played. PG-2344 alone produced eight of
these — six folded back by migration 0027, two more by 0030, where
"Manchester United" (3 matches) sat beside "Man United" (114) and
"Seattle" (3) beside "Seattle Sounders" (87). Both positions came out
blocked on insufficient_data_anchors with the history sitting one row
away.

Nothing surfaced any of that. The slate reported the position as blocked
without distinguishing a club we genuinely have no data for (Aberdeen —
no configured source ingests Scottish football) from one whose data is
right there under another name. This module draws that line.

It reports; it never merges. Three rules keep the reports trustworthy,
each one earned from a false positive:

* **Shared competition.** Comparing names alone proposed "Real Sociedad"
  -> "Real Madrid", "Braga" -> "Bragantino" and "FC Kobenhavn" ->
  "FC Juarez". This is the same constraint the note above
  ``_CONTINENTAL_SPLIT_MERGES`` requires before any merge.
* **Word-boundary prefix.** The short name must be a whole leading word
  of the long one: "Seattle Sounders" starts with "Seattle" + a space.
  A bare prefix would accept "Bragantino" for "Braga".
* **Same gender.** "Barcelona Femenino" is "Barcelona" plus a word, and
  the two rows were separated deliberately in v23-v26. A women's row is
  never a candidate to fold into a men's one, or the reverse.

Even with all three, a real reserve side ("Cruz Azul Hidalgo") can look
like a split. That is why the output is a review queue and the merge
stays a hand-written migration.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.tables import CompetitionModel, MatchModel, TeamModel
from app.services.team_name_quality_service import normalized_team_label

# A row at or below this many matches is too thin to score a position on.
THIN_HISTORY_MAX = 10
# The other row must hold at least this much for the split to be worth
# reporting — otherwise both rows are thin and merging fixes nothing.
RICH_HISTORY_MIN = 30

_WOMENS_WORDS = frozenset({"femenil", "femenino", "femenina", "women", "womens", "w"})


def _is_womens(name: str) -> bool:
    """Token-based on purpose. Matching " w" as a substring made every
    club whose second word starts with a W read as a women's side —
    "Vancouver Whitecaps" among them, which silently excluded it from
    detection until a run against production data exposed it.
    """
    return bool(_WOMENS_WORDS & set(normalized_team_label(name).split()))


def _is_leading_word_of(short: str, long: str) -> bool:
    """True when `short` is a whole leading word sequence of `long`.

    "Seattle" is a leading word of "Seattle Sounders"; "Braga" is not a
    leading word of "Bragantino", because the prefix has to end on a
    word boundary.
    """
    short_label = normalized_team_label(short)
    long_label = normalized_team_label(long)
    if not short_label or not long_label or short_label == long_label:
        return False
    return long_label.startswith(short_label + " ")


def _is_abbreviation_of(a: str, b: str) -> bool:
    """True when two names differ only by abbreviated words.

    "Man United" and "Manchester United" are the same club written by
    two sources, but neither is a prefix of the other, so the rule above
    misses them — and that is the exact pair that motivated this module.
    Token-wise they line up: "man" abbreviates "manchester", "united"
    matches outright.

    Deliberately strict, because this rule is the loose one:

    * same number of tokens, at least two — a single token would accept
      "Braga" for "Bragantino"
    * every token pair equal, or one a prefix of the other (>= 3 chars,
      so "FC" cannot stand in for "Fluminense")
    * at least one pair equal outright, which is the anchor that stops
      "Real Sociedad" from matching "Real Madrid"
    """
    tokens_a = normalized_team_label(a).split()
    tokens_b = normalized_team_label(b).split()
    if len(tokens_a) != len(tokens_b) or len(tokens_a) < 2:
        return False
    if tokens_a == tokens_b:
        return False

    exact_matches = 0
    for left, right in zip(tokens_a, tokens_b):
        if left == right:
            exact_matches += 1
            continue
        shorter, longer = sorted((left, right), key=len)
        if len(shorter) < 3 or not longer.startswith(shorter):
            return False
    return exact_matches >= 1


def names_look_split(thin_name: str, rich_name: str) -> bool:
    """Whether two rows look like the same club under two spellings.

    The gender guard lives here rather than at the call site so the
    helper is safe on its own: "Barcelona Femenino" is "Barcelona" plus
    a word and satisfies the prefix rule, but v23-v26 separated those
    rows deliberately and folding them back would undo that work.
    """
    if _is_womens(thin_name) != _is_womens(rich_name):
        return False
    return _is_leading_word_of(thin_name, rich_name) or _is_abbreviation_of(
        thin_name, rich_name
    )


@dataclass(frozen=True)
class SplitCandidate:
    """A thin team row that looks like a split of a richer one."""

    thin_team_id: str
    thin_team_name: str
    thin_match_count: int
    rich_team_id: str
    rich_team_name: str
    rich_match_count: int
    shared_competition: str

    def as_dict(self) -> dict[str, object]:
        return {
            "thin_team": self.thin_team_name,
            "thin_match_count": self.thin_match_count,
            "candidate_team": self.rich_team_name,
            "candidate_match_count": self.rich_match_count,
            "shared_competition": self.shared_competition,
        }


def _match_counts(session: Session, team_ids: list[str]) -> dict[str, int]:
    if not team_ids:
        return {}
    counts: dict[str, int] = {team_id: 0 for team_id in team_ids}
    for side in (MatchModel.home_team_id, MatchModel.away_team_id):
        rows = session.execute(
            select(side, func.count(MatchModel.id)).where(side.in_(team_ids)).group_by(side)
        ).all()
        for team_id, count in rows:
            counts[team_id] = counts.get(team_id, 0) + int(count)
    return counts


def _competition_ids(session: Session, team_id: str) -> set[str]:
    rows = session.scalars(
        select(MatchModel.competition_id)
        .where(
            (MatchModel.home_team_id == team_id) | (MatchModel.away_team_id == team_id)
        )
        .distinct()
    ).all()
    return set(rows)


def find_split_candidates(
    session: Session,
    team_ids: list[str],
) -> dict[str, SplitCandidate]:
    """Map team_id -> the richer row it looks like a split of.

    Only thin rows are examined, so this stays cheap enough to run while
    building a slate report. Teams with no candidate are absent from the
    result rather than mapped to None.
    """
    unique_ids = list(dict.fromkeys(team_ids))
    if not unique_ids:
        return {}

    counts = _match_counts(session, unique_ids)
    thin_ids = [team_id for team_id in unique_ids if counts.get(team_id, 0) <= THIN_HISTORY_MAX]
    if not thin_ids:
        return {}

    thin_teams = list(session.scalars(select(TeamModel).where(TeamModel.id.in_(thin_ids))).all())
    results: dict[str, SplitCandidate] = {}

    for thin in thin_teams:
        thin_competitions = _competition_ids(session, thin.id)
        if not thin_competitions:
            # No fixtures at all means no competition to agree on, and
            # the gender/name rules alone are not enough to propose a
            # merge. A genuinely unknown club (Aberdeen) lands here.
            continue

        # Only rows sharing one of this team's competitions can qualify.
        siblings = list(
            session.scalars(
                select(TeamModel)
                .join(
                    MatchModel,
                    (MatchModel.home_team_id == TeamModel.id)
                    | (MatchModel.away_team_id == TeamModel.id),
                )
                .where(
                    MatchModel.competition_id.in_(thin_competitions),
                    TeamModel.id != thin.id,
                )
                .distinct()
            ).all()
        )
        if not siblings:
            continue

        sibling_counts = _match_counts(session, [team.id for team in siblings])
        best: SplitCandidate | None = None
        for sibling in siblings:
            sibling_total = sibling_counts.get(sibling.id, 0)
            if sibling_total < RICH_HISTORY_MIN:
                continue
            if not names_look_split(thin.name, sibling.name):
                continue
            if best is not None and sibling_total <= best.rich_match_count:
                continue
            shared = thin_competitions & _competition_ids(session, sibling.id)
            if not shared:
                continue
            shared_competition = session.scalar(
                select(CompetitionModel.name)
                .where(CompetitionModel.id.in_(shared))
                .order_by(CompetitionModel.name)
                .limit(1)
            )
            best = SplitCandidate(
                thin_team_id=thin.id,
                thin_team_name=thin.name,
                thin_match_count=counts.get(thin.id, 0),
                rich_team_id=sibling.id,
                rich_team_name=sibling.name,
                rich_match_count=sibling_total,
                shared_competition=str(shared_competition or ""),
            )
        if best is not None:
            results[thin.id] = best

    return results
