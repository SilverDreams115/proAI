from __future__ import annotations

import argparse
import hashlib
import json
from dataclasses import asdict, dataclass
from datetime import timezone
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db.session import SessionLocal
from app.models.tables import MatchModel
from app.models.tables import ProgolSlateMatchModel
from app.models.tables import ProgolSlateModel
from app.models.tables import TeamAliasModel
from app.models.tables import TeamModel
from app.services.normalization_service import NormalizationService


_CONGO_KEYS = {"republica del congo", "rep del congo", "republic of congo"}
_CONGO_CANDIDATE_NAMES = {
    "congo",
    "dr congo",
    "democratic republic of congo",
    "democratic republic of the congo",
}


@dataclass(frozen=True)
class TeamCandidate:
    team_id: str
    name: str
    normalized_alias: str | None


@dataclass(frozen=True)
class MappingDryRunRow:
    position: int
    current_home: str
    current_away: str
    current_placeholder: bool
    proposed_home_canonical: str | None
    proposed_away_canonical: str | None
    confidence: str
    reason: str
    would_change_team_id: bool
    would_change_match_id: bool
    would_change_composition_hash: bool
    safe_to_apply: bool
    status: str
    current_match_id: str
    replacement_match_id: str | None


def _alias_key(value: str) -> str:
    return NormalizationService()._alias_key(value)  # noqa: SLF001 - diagnostic parity


def _find_candidates(session: Session, team_name: str) -> list[TeamCandidate]:
    normalizer = NormalizationService()
    normalized = normalizer.normalize_team_name(team_name)
    rows = session.execute(
        select(TeamModel.id, TeamModel.name, TeamAliasModel.normalized_alias)
        .outerjoin(TeamAliasModel, TeamAliasModel.team_id == TeamModel.id)
        .where(
            TeamModel.is_placeholder.is_(False),
            (TeamModel.name == team_name) | (TeamAliasModel.normalized_alias == normalized),
        )
        .order_by(TeamModel.name.asc(), TeamModel.id.asc())
    ).all()
    candidates = {
        row.id: TeamCandidate(row.id, row.name, row.normalized_alias)
        for row in rows
    }
    if candidates:
        return list(candidates.values())

    if _alias_key(team_name) in _CONGO_KEYS:
        congo_rows = session.execute(
            select(TeamModel.id, TeamModel.name, TeamAliasModel.normalized_alias)
            .outerjoin(TeamAliasModel, TeamAliasModel.team_id == TeamModel.id)
            .where(
                TeamModel.is_placeholder.is_(False),
                func.lower(TeamModel.name).in_(_CONGO_CANDIDATE_NAMES),
            )
            .order_by(TeamModel.name.asc(), TeamModel.id.asc())
        ).all()
        return [
            TeamCandidate(row.id, row.name, row.normalized_alias)
            for row in congo_rows
        ]
    return []


def _unique_candidate(candidates: list[TeamCandidate]) -> TeamCandidate | None:
    candidate_ids = {candidate.team_id for candidate in candidates}
    if len(candidate_ids) != 1:
        return None
    return candidates[0]


def _composition_hash_for_rows(
    *,
    draw_code: str,
    week_type: str,
    rows: list[dict[str, Any]],
) -> str:
    fixtures = []
    for row in sorted(rows, key=lambda item: int(item["position"])):
        kickoff = row["kickoff_at"]
        if kickoff.tzinfo is None:
            kickoff = kickoff.replace(tzinfo=timezone.utc)
        fixtures.append(
            {
                "position": row["position"],
                "home_team": row["home_team"].strip().lower(),
                "away_team": row["away_team"].strip().lower(),
                "kickoff_at": kickoff.isoformat(),
                "competition": row["competition"].strip().lower(),
            }
        )
    content = json.dumps(
        {"draw_code": draw_code, "week_type": week_type, "fixtures": fixtures},
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(content.encode()).hexdigest()


def _slate_rows(slate: ProgolSlateModel) -> list[dict[str, Any]]:
    return [
        {
            "position": link.position,
            "home_team": link.match.home_team.name,
            "away_team": link.match.away_team.name,
            "competition": link.match.competition.name,
            "kickoff_at": link.match.kickoff_at,
            "match_id": link.match_id,
        }
        for link in sorted(slate.matches, key=lambda item: item.position)
    ]


def _replacement_match_id(
    session: Session,
    *,
    match: MatchModel,
    home_team_id: str,
    away_team_id: str,
) -> str | None:
    return session.scalar(
        select(MatchModel.id).where(
            MatchModel.competition_id == match.competition_id,
            MatchModel.home_team_id == home_team_id,
            MatchModel.away_team_id == away_team_id,
            MatchModel.kickoff_at == match.kickoff_at,
        )
    )


def _slate_link_count(session: Session, match_id: str) -> int:
    return int(
        session.scalar(
            select(func.count()).select_from(ProgolSlateMatchModel).where(
                ProgolSlateMatchModel.match_id == match_id
            )
        )
        or 0
    )


def build_mapping_dry_run(session: Session, draw_code: str = "PG-2338") -> dict[str, Any]:
    slate = session.scalar(select(ProgolSlateModel).where(ProgolSlateModel.draw_code == draw_code))
    if slate is None:
        raise ValueError(f"Slate {draw_code!r} not found")

    base_rows = _slate_rows(slate)
    current_hash = _composition_hash_for_rows(
        draw_code=slate.draw_code,
        week_type=slate.week_type,
        rows=base_rows,
    )
    rows: list[MappingDryRunRow] = []
    proposed_rows = [dict(row) for row in base_rows]

    for link in sorted(slate.matches, key=lambda item: item.position):
        match = link.match
        home = match.home_team
        away = match.away_team
        home_candidates = _find_candidates(session, home.name)
        away_candidates = _find_candidates(session, away.name)
        home_candidate = _unique_candidate(home_candidates)
        away_candidate = _unique_candidate(away_candidates)

        proposed_home_id = home_candidate.team_id if home.is_placeholder and home_candidate else home.id
        proposed_away_id = away_candidate.team_id if away.is_placeholder and away_candidate else away.id
        proposed_home_name = home_candidate.name if home.is_placeholder and home_candidate else None
        proposed_away_name = away_candidate.name if away.is_placeholder and away_candidate else None
        ambiguous = (
            (home.is_placeholder and home_candidates and home_candidate is None)
            or (away.is_placeholder and away_candidates and away_candidate is None)
        )
        unresolved_placeholder = (
            (home.is_placeholder and not home_candidates)
            or (away.is_placeholder and not away_candidates)
        )
        would_change_team_id = proposed_home_id != home.id or proposed_away_id != away.id
        replacement_id = None
        would_change_match_id = False
        if would_change_team_id:
            replacement_id = _replacement_match_id(
                session,
                match=match,
                home_team_id=proposed_home_id,
                away_team_id=proposed_away_id,
            )
            would_change_match_id = bool(replacement_id and replacement_id != match.id)

        changed_rows = [dict(row) for row in base_rows]
        for row in changed_rows:
            if row["position"] == link.position:
                if proposed_home_name:
                    row["home_team"] = proposed_home_name
                if proposed_away_name:
                    row["away_team"] = proposed_away_name
        changed_hash = _composition_hash_for_rows(
            draw_code=slate.draw_code,
            week_type=slate.week_type,
            rows=changed_rows,
        )
        would_change_hash = changed_hash != current_hash

        shared_match = _slate_link_count(session, match.id) > 1
        safe = bool(would_change_team_id and not ambiguous and not unresolved_placeholder and not shared_match)
        status = "safe_to_apply" if safe else "no_change"
        confidence = "high" if safe else "none"
        reason = "no placeholder side needs relink"
        if ambiguous:
            status = "needs_review_mapping"
            confidence = "low"
            reason = "placeholder has multiple plausible canonical candidates"
        elif unresolved_placeholder:
            status = "provider_missing"
            confidence = "low"
            reason = "placeholder has no canonical candidate"
        elif shared_match and would_change_team_id:
            status = "blocked_shared_match"
            confidence = "medium"
            reason = "current match is linked by more than one slate"
        elif safe:
            reason = "unique non-placeholder canonical candidate"
            for row in proposed_rows:
                if row["position"] == link.position:
                    if proposed_home_name:
                        row["home_team"] = proposed_home_name
                    if proposed_away_name:
                        row["away_team"] = proposed_away_name

        if _alias_key(home.name) in _CONGO_KEYS or _alias_key(away.name) in _CONGO_KEYS:
            status = "needs_review_mapping"
            confidence = "low"
            safe = False
            reason = (
                "República Del Congo has Congo and DR Congo candidates; "
                "no local official proposal/source document distinguishes them"
            )

        rows.append(
            MappingDryRunRow(
                position=link.position,
                current_home=home.name,
                current_away=away.name,
                current_placeholder=bool(home.is_placeholder or away.is_placeholder),
                proposed_home_canonical=proposed_home_name,
                proposed_away_canonical=proposed_away_name,
                confidence=confidence,
                reason=reason,
                would_change_team_id=would_change_team_id,
                would_change_match_id=would_change_match_id,
                would_change_composition_hash=would_change_hash if would_change_team_id else False,
                safe_to_apply=safe,
                status=status,
                current_match_id=match.id,
                replacement_match_id=replacement_id,
            )
        )

    proposed_hash = _composition_hash_for_rows(
        draw_code=slate.draw_code,
        week_type=slate.week_type,
        rows=proposed_rows,
    )
    return {
        "slate": {
            "slate_id": slate.id,
            "draw_code": slate.draw_code,
            "slate_version": slate.slate_version,
            "composition_hash": slate.composition_hash,
            "computed_current_hash": current_hash,
            "proposed_safe_hash": proposed_hash,
            "stored_hash_matches_model": slate.composition_hash == current_hash,
            "proposed_safe_hash_matches_stored": slate.composition_hash == proposed_hash,
            "registration_closes_at": slate.registration_closes_at.isoformat()
            if slate.registration_closes_at
            else None,
            "is_archived": slate.is_archived,
        },
        "rows": [asdict(row) for row in rows],
        "safe_positions": [row.position for row in rows if row.safe_to_apply],
        "needs_review_positions": [
            row.position for row in rows if row.status == "needs_review_mapping"
        ],
        "would_change_composition_hash": proposed_hash != current_hash,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Dry-run PG slate mapping relink diagnosis.")
    parser.add_argument("--draw-code", default="PG-2338")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    with SessionLocal() as session:
        report = build_mapping_dry_run(session, draw_code=args.draw_code)
    if args.json:
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        print(json.dumps(report, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
