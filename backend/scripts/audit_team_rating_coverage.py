"""Read-only Elo / internal-rating coverage audit (DIAGNOSTIC ONLY).

Prototypes an in-memory Elo rating from the EXISTING canonical
``match_results`` and reports how much of PG-2338 and the historical
slates a ``rating_diff`` feature would cover. It NEVER persists a rating,
never writes the DB, never trains and never touches predictions.

Deterministic: results ordered by ``played_at`` asc, tie-broken by
``match_id`` asc; conflicting matches (sources disagree) are excluded via
``CanonicalResultRepository``; rows without a valid score are ignored.

Usage::

    python backend/scripts/audit_team_rating_coverage.py --base
    python backend/scripts/audit_team_rating_coverage.py --draw-code PG-2338
    python backend/scripts/audit_team_rating_coverage.py --historical
"""

from __future__ import annotations

import argparse
import json
import math
from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.session import SessionLocal
from app.models.tables import CompetitionModel
from app.models.tables import MatchModel
from app.models.tables import MatchResultModel
from app.models.tables import TeamModel
from app.repositories.canonical_result_repository import CanonicalResultRepository
from app.repositories.slate_repository import SlateRepository

# National-team competition fingerprints (lower-cased substring match).
_NATIONAL_KEYWORDS = (
    "friendl", "amistos", "qualif", "world cup", "copa america", "copa américa",
    "euro", "nations league", "international", "concacaf", "gold cup", "afcon",
    "africa cup", "asian cup", "conmebol",
)

# Confidence buckets by number of rated matches.
CONF_NO_RATING = "no_rating"      # 0
CONF_WEAK = "weak"                # 1-3
CONF_MEDIUM = "medium"            # 4-9
CONF_STRONG = "strong"           # 10+


def _confidence_bucket(matches: int) -> str:
    if matches <= 0:
        return CONF_NO_RATING
    if matches <= 3:
        return CONF_WEAK
    if matches <= 9:
        return CONF_MEDIUM
    return CONF_STRONG


@dataclass(frozen=True)
class EloParams:
    initial_rating: float = 1500.0
    k_base: float = 32.0
    # Default 0: venues are neutral/unknown for the friendlies that dominate
    # Progol, so we do not assume a home edge until evidence says otherwise.
    home_advantage: float = 0.0
    # Goal-difference K multiplier. DISABLED by default and reported only —
    # a thin national-team sample shouldn't let a 5-0 swing the rating 5x a
    # 1-0. When enabled the multiplier is hard-capped.
    goal_diff_enabled: bool = False
    goal_diff_cap: float = 1.75
    # Recency decay (pull idle teams toward the mean). Reported, not active.
    recency_decay_enabled: bool = False
    min_matches_for_confident_rating: int = 5


@dataclass
class EloMatch:
    played_at: datetime
    match_id: str
    home_id: str
    away_id: str
    home_goals: int
    away_goals: int
    competition: str


@dataclass
class TeamRating:
    team_id: str
    team_name: str
    is_placeholder: bool
    country: str | None
    namespace: str  # club | national | unknown
    matches_count: int = 0
    wins: int = 0
    draws: int = 0
    losses: int = 0
    goals_for: int = 0
    goals_against: int = 0
    rating: float = 1500.0
    rating_delta: float = 0.0  # last update delta
    confidence_bucket: str = CONF_NO_RATING
    last_result_at: str | None = None
    competitions_seen: list[str] = field(default_factory=list)


def _goal_diff_multiplier(home_goals: int, away_goals: int, params: EloParams) -> float:
    if not params.goal_diff_enabled:
        return 1.0
    gd = abs(home_goals - away_goals)
    return min(1.0 + math.log1p(gd), params.goal_diff_cap)


def compute_ratings(
    matches: list[EloMatch], teams: dict[str, TeamRating], params: EloParams
) -> dict[str, TeamRating]:
    """Deterministic single-pass Elo over the given matches, updating the
    supplied ``teams`` metadata in place. Pure (no DB)."""
    ratings: dict[str, float] = {}

    def _ensure(team_id: str) -> None:
        if team_id not in ratings:
            ratings[team_id] = params.initial_rating

    ordered = sorted(matches, key=lambda m: (m.played_at, m.match_id))
    for m in ordered:
        _ensure(m.home_id)
        _ensure(m.away_id)
        rh, ra = ratings[m.home_id], ratings[m.away_id]
        exp_home = 1.0 / (1.0 + 10 ** ((ra - rh - params.home_advantage) / 400.0))
        if m.home_goals > m.away_goals:
            score_home = 1.0
        elif m.home_goals == m.away_goals:
            score_home = 0.5
        else:
            score_home = 0.0
        k_eff = params.k_base * _goal_diff_multiplier(m.home_goals, m.away_goals, params)
        delta = k_eff * (score_home - exp_home)
        ratings[m.home_id] = rh + delta
        ratings[m.away_id] = ra - delta  # zero-sum

        for team_id, gf, ga, is_home in (
            (m.home_id, m.home_goals, m.away_goals, True),
            (m.away_id, m.away_goals, m.home_goals, False),
        ):
            t = teams.get(team_id)
            if t is None:
                continue  # metadata not requested for this team
            t.matches_count += 1
            t.goals_for += gf
            t.goals_against += ga
            if gf > ga:
                t.wins += 1
            elif gf == ga:
                t.draws += 1
            else:
                t.losses += 1
            t.rating = ratings[team_id]
            t.rating_delta = round(delta if is_home else -delta, 2)
            t.last_result_at = m.played_at.isoformat()
            if m.competition not in t.competitions_seen:
                t.competitions_seen.append(m.competition)

    for team_id, t in teams.items():
        t.rating = round(ratings.get(team_id, params.initial_rating), 1)
        t.confidence_bucket = _confidence_bucket(t.matches_count)
    return teams


# --- DB loading (read-only) -------------------------------------------------


def _namespace_for(comp_names: list[str]) -> str:
    if not comp_names:
        return "unknown"
    national = sum(
        1 for name in comp_names
        if any(kw in name.lower() for kw in _NATIONAL_KEYWORDS)
    )
    if national == 0:
        return "club"
    if national >= len(comp_names) / 2:
        return "national"
    return "club"


def _load_matches_and_teams(session: Session) -> tuple[list[EloMatch], dict[str, TeamRating]]:
    """Load every canonically-resolved result into EloMatch rows plus team
    metadata. Read-only."""
    all_match_ids = [
        row[0] for row in session.execute(select(MatchResultModel.match_id).distinct())
    ]
    canonical = CanonicalResultRepository(session).get_canonical_for_matches(all_match_ids)

    # Bulk-load the matches we need with their teams/competition.
    match_rows = session.execute(
        select(MatchModel, CompetitionModel.name)
        .join(CompetitionModel, MatchModel.competition_id == CompetitionModel.id)
        .where(MatchModel.id.in_(list(canonical.keys())))
    ).all()
    match_by_id = {m.id: (m, comp_name) for m, comp_name in match_rows}

    elo_matches: list[EloMatch] = []
    team_comps: dict[str, list[str]] = {}
    for match_id, result in canonical.items():
        entry = match_by_id.get(match_id)
        if entry is None:
            continue
        match, comp_name = entry
        elo_matches.append(EloMatch(
            played_at=result.played_at,
            match_id=match_id,
            home_id=match.home_team_id,
            away_id=match.away_team_id,
            home_goals=result.home_goals,
            away_goals=result.away_goals,
            competition=comp_name,
        ))
        for tid in (match.home_team_id, match.away_team_id):
            team_comps.setdefault(tid, [])
            if comp_name not in team_comps[tid]:
                team_comps[tid].append(comp_name)

    # Team metadata for every team that appears in a rated match.
    team_ids = list(team_comps.keys())
    teams: dict[str, TeamRating] = {}
    if team_ids:
        for t in session.scalars(select(TeamModel).where(TeamModel.id.in_(team_ids))):
            teams[t.id] = TeamRating(
                team_id=t.id, team_name=t.name, is_placeholder=t.is_placeholder,
                country=t.country, namespace=_namespace_for(team_comps.get(t.id, [])),
            )
    return elo_matches, teams


# --- Report builders --------------------------------------------------------


def base_data_summary(session: Session, teams: dict[str, TeamRating]) -> dict[str, Any]:
    total_results = session.query(MatchResultModel).count()
    distinct_matches = session.query(MatchResultModel.match_id).distinct().count()
    buckets = {CONF_NO_RATING: 0, CONF_WEAK: 0, CONF_MEDIUM: 0, CONF_STRONG: 0}
    national = club = 0
    for t in teams.values():
        buckets[t.confidence_bucket] += 1
        if t.namespace == "national":
            national += 1
        elif t.namespace == "club":
            club += 1
    # Teams with zero rated matches don't appear in `teams`; count globally.
    all_non_placeholder = session.query(TeamModel).filter(TeamModel.is_placeholder.is_(False)).count()
    return {
        "total_results": total_results,
        "distinct_matches_with_results": distinct_matches,
        "teams_with_rating": len(teams),
        "teams_national": national,
        "teams_club": club,
        "non_placeholder_teams_total": all_non_placeholder,
        "teams_without_rating": all_non_placeholder - len(teams),
        "confidence_buckets": buckets,
    }


def _medium_plus(t: TeamRating | None) -> bool:
    return t is not None and t.matches_count >= 4


@dataclass
class Pg2338Row:
    position: int
    home: str
    away: str
    home_rating: float | None
    away_rating: float | None
    home_matches_count: int
    away_matches_count: int
    home_confidence: str
    away_confidence: str
    rating_diff: float | None
    both_have_rating: bool
    both_medium_plus: bool
    would_help_fallback: bool
    still_missing_reason: str


def pg2338_coverage(session: Session, teams: dict[str, TeamRating], draw_code: str) -> dict[str, Any]:
    repo = SlateRepository(session)
    found = repo.find_by_draw_code(draw_code)
    slate = repo.get_slate(found.id) if found is not None else None
    if slate is None:
        raise ValueError(f"draw_code {draw_code!r} not found")
    rows: list[Pg2338Row] = []
    for link in sorted(slate.matches, key=lambda link: link.position):
        m = link.match
        th = teams.get(m.home_team_id)
        ta = teams.get(m.away_team_id)
        hc = th.matches_count if th else 0
        ac = ta.matches_count if ta else 0
        both = hc > 0 and ac > 0
        both_mp = _medium_plus(th) and _medium_plus(ta)
        if not both:
            missing = "home_no_rating" if hc == 0 else ("away_no_rating" if ac == 0 else "")
            if hc == 0 and ac == 0:
                missing = "both_no_rating"
        elif not both_mp:
            missing = "thin_rating_one_side"
        else:
            missing = ""
        rows.append(Pg2338Row(
            position=link.position, home=m.home_team.name, away=m.away_team.name,
            home_rating=th.rating if th else None, away_rating=ta.rating if ta else None,
            home_matches_count=hc, away_matches_count=ac,
            home_confidence=_confidence_bucket(hc), away_confidence=_confidence_bucket(ac),
            rating_diff=round(th.rating - ta.rating, 1) if (th and ta) else None,
            both_have_rating=both, both_medium_plus=both_mp,
            would_help_fallback=both,  # provides a strength signal where there was none
            still_missing_reason=missing,
        ))
    helped = [r.position for r in rows if r.would_help_fallback]
    summary = {
        "pg2338_matches": len(rows),
        "both_have_rating_count": sum(1 for r in rows if r.both_have_rating),
        "both_medium_plus_count": sum(1 for r in rows if r.both_medium_plus),
        "one_side_missing_count": sum(1 for r in rows if not r.both_have_rating),
        "no_rating_count": sum(1 for r in rows if r.home_matches_count == 0 and r.away_matches_count == 0),
        "positions_helped": helped,
        "positions_not_helped": [r.position for r in rows if not r.would_help_fallback],
    }
    return {"summary": summary, "rows": [asdict(r) for r in rows]}


def historical_by_competition(session: Session, teams: dict[str, TeamRating]) -> list[dict[str, Any]]:
    """For every slate match, evaluate rating coverage, grouped by competition."""
    repo = SlateRepository(session)
    by_comp: dict[str, dict[str, Any]] = {}
    for slate in repo.list_slates():
        for link in slate.matches:
            m = link.match
            comp = m.competition.name
            th = teams.get(m.home_team_id)
            ta = teams.get(m.away_team_id)
            agg = by_comp.setdefault(comp, {
                "competition": comp, "matches": 0, "both_have_rating": 0,
                "both_medium_plus": 0, "home_conf_sum": 0, "away_conf_sum": 0,
                "teams_without_rating": set(),
            })
            agg["matches"] += 1
            hc = th.matches_count if th else 0
            ac = ta.matches_count if ta else 0
            if hc > 0 and ac > 0:
                agg["both_have_rating"] += 1
            if _medium_plus(th) and _medium_plus(ta):
                agg["both_medium_plus"] += 1
            agg["home_conf_sum"] += hc
            agg["away_conf_sum"] += ac
            if hc == 0:
                agg["teams_without_rating"].add(m.home_team.name)
            if ac == 0:
                agg["teams_without_rating"].add(m.away_team.name)
    table = []
    for comp, agg in by_comp.items():
        n = agg["matches"]
        table.append({
            "competition": comp,
            "matches": n,
            "both_have_rating_rate": round(agg["both_have_rating"] / n, 3),
            "both_medium_plus_rate": round(agg["both_medium_plus"] / n, 3),
            "avg_home_rating_matches": round(agg["home_conf_sum"] / n, 2),
            "avg_away_rating_matches": round(agg["away_conf_sum"] / n, 2),
            "teams_without_rating": sorted(agg["teams_without_rating"]),
            # Strength signal newly available where it was previously absent.
            "would_reduce_zero_strength_count_estimate": agg["both_have_rating"],
        })
    return sorted(table, key=lambda d: (-d["both_medium_plus_rate"], -d["matches"]))


def build_report(
    session: Session, *, base: bool, draw_code: str | None, historical: bool
) -> dict[str, Any]:
    elo_matches, teams = _load_matches_and_teams(session)
    params = EloParams()
    compute_ratings(elo_matches, teams, params)  # mutates `teams` in place

    report: dict[str, Any] = {"elo_params": asdict(params), "rated_matches_used": len(elo_matches)}
    if base or not (draw_code or historical):
        summary = base_data_summary(session, teams)
        summary["rated_matches_used"] = len(elo_matches)
        report["base"] = summary
    if draw_code:
        report["pg2338"] = pg2338_coverage(session, teams, draw_code)
    if historical:
        report["historical_by_competition"] = historical_by_competition(session, teams)
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description="Read-only Elo coverage diagnostic.")
    parser.add_argument("--base", action="store_true")
    parser.add_argument("--draw-code")
    parser.add_argument("--historical", action="store_true")
    args = parser.parse_args()
    with SessionLocal() as session:
        report = build_report(
            session, base=args.base, draw_code=args.draw_code, historical=args.historical
        )
        session.rollback()  # hard read-only guarantee
    print(json.dumps(report, indent=2, sort_keys=True, default=str))


if __name__ == "__main__":
    main()
