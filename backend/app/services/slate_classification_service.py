"""Classify a slate as an official Progol concurso vs synthetic/demo data.

Real Progol slates are promoted from an official Lotería Nacional guía
proposal and draw from many competitions. Demo/seed slates are composed
entirely of placeholder competitions (e.g. "International Friendlies")
with no proposal lineage. This service makes that distinction
deterministic and auditable so the dashboard never presents demo data as
a real concurso and official scoring is blocked for non-real slates.

It is read-only: nothing here mutates, archives, or deletes a slate. The
classification is computed on demand.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.tables import (
    CompetitionModel,
    MatchLiveResultModel,
    MatchModel,
    MatchResultModel,
    ProgolSlateMatchModel,
    ProgolSlateModel,
    ProgolSlateProposalModel,
)

# Competitions that only ever appear in the demo/seed dataset. Used only
# as a tie-breaker when there is NO official lineage — a real concurso in a
# FIFA window can legitimately be all international friendlies, so this is
# never enough on its own to override genuine LN lineage.
_DEMO_ONLY_COMPETITIONS = {"international friendlies"}
# Hosts that count as official Progol lineage. Lotería Nacional is the
# primary authority; TuLotero is a licensed Progol reseller whose product
# pages mirror the official concurso, so an operator-captured slate sourced
# from TuLotero is a real concurso (used when LN has not yet published its
# guía). Kept as an explicit allow-list — a loose "progol" substring would
# wrongly trust a local/seeded proposal URL.
_OFFICIAL_SOURCE_HINTS = ("loterianacional.gob.mx", "tulotero.mx")


class SlateClassification(str, Enum):
    OFFICIAL_REAL = "official_real"
    OFFICIAL_NO_RESULTS = "official_but_no_results_yet"
    SYNTHETIC_DEMO = "synthetic_demo"
    STALE_ARCHIVED = "stale_archived"
    UNVERIFIED = "unverified"


@dataclass(frozen=True)
class SlateReality:
    classification: SlateClassification
    comparable_with_results: bool
    has_official_proposal: bool
    competitions: list[str]
    source_name: str | None
    source_url: str | None
    reasons: list[str]


def classify_slate(session: Session, slate: ProgolSlateModel) -> SlateReality:
    proposal = _official_proposal(session, slate)
    competitions = _competitions(session, slate)
    comp_keys = {c.strip().lower() for c in competitions}
    only_demo = bool(comp_keys) and comp_keys.issubset(_DEMO_ONLY_COMPETITIONS)
    has_official = proposal is not None
    has_results = _has_any_result(session, slate)

    reasons: list[str] = []
    if proposal is not None:
        # Genuine LN guía lineage — the slate IS a real concurso even if it
        # has no results yet (e.g. just closed, source not published).
        reasons.append(f"promovida desde guía oficial LN ({proposal.source_name})")
        if has_results:
            classification = SlateClassification.OFFICIAL_REAL
        else:
            classification = SlateClassification.OFFICIAL_NO_RESULTS
            reasons.append("sin resultados oficiales ingeridos todavía")
        comparable = True
    elif only_demo:
        classification = SlateClassification.SYNTHETIC_DEMO
        comparable = False
        reasons.append("sin lineage oficial LN y todos los partidos son demo")
        reasons.append(f"competencias: {', '.join(sorted(competitions))}")
    else:
        classification = SlateClassification.UNVERIFIED
        comparable = False
        reasons.append("sin lineage oficial LN verificable; no se scorea como oficial")

    return SlateReality(
        classification=classification,
        comparable_with_results=comparable,
        has_official_proposal=has_official,
        competitions=competitions,
        source_name=proposal.source_name if proposal else None,
        source_url=proposal.source_url if proposal else None,
        reasons=reasons,
    )


def _has_any_result(session: Session, slate: ProgolSlateModel) -> bool:
    match_ids = [sm.match_id for sm in slate.matches]
    if not match_ids:
        return False
    final = session.scalar(
        select(MatchResultModel.id).where(MatchResultModel.match_id.in_(match_ids)).limit(1)
    )
    if final is not None:
        return True
    live = session.scalar(
        select(MatchLiveResultModel.id)
        .where(MatchLiveResultModel.match_id.in_(match_ids))
        .limit(1)
    )
    return live is not None


def _official_proposal(
    session: Session, slate: ProgolSlateModel
) -> ProgolSlateProposalModel | None:
    """Return an official LN proposal that produced this slate, if any.

    A proposal counts as official lineage when it was promoted into this
    slate (promoted_slate_id) or shares its draw_code, AND its source_url
    points at an official Progol host.
    """
    rows = session.scalars(
        select(ProgolSlateProposalModel).where(
            (ProgolSlateProposalModel.promoted_slate_id == slate.id)
            | (ProgolSlateProposalModel.draw_code == slate.draw_code)
        )
    ).all()
    for proposal in rows:
        url = (proposal.source_url or "").lower()
        if any(hint in url for hint in _OFFICIAL_SOURCE_HINTS):
            return proposal
    return None


def _competitions(session: Session, slate: ProgolSlateModel) -> list[str]:
    names = session.scalars(
        select(CompetitionModel.name)
        .join(MatchModel, MatchModel.competition_id == CompetitionModel.id)
        .join(ProgolSlateMatchModel, ProgolSlateMatchModel.match_id == MatchModel.id)
        .where(ProgolSlateMatchModel.slate_id == slate.id)
        .distinct()
    ).all()
    return sorted(names)
