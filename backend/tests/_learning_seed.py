"""Shared seed helpers for R7.0 learning-loop tests (in-memory SQLite)."""
from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest

from app.models.tables import (
    CompetitionModel,
    MatchModel,
    MatchResultModel,
    PredictionModel,
    ProgolSlateMatchModel,
    ProgolSlateModel,
    ProgolSlateProposalModel,
    SourceModel,
    TeamModel,
)

_BASE = datetime(2026, 2, 1, tzinfo=timezone.utc)
_OFFICIAL_URL = "https://www.loterianacional.gob.mx/Progol/guia"


@pytest.fixture
def learn_db(tmp_path):
    from app.db import session as db_mod
    from app.db.migrations import run_migrations
    from app.db.session import configure_session

    configure_session(f"sqlite:///{tmp_path / 'learning.db'}")
    run_migrations(db_mod.engine)
    return db_mod.SessionLocal()


def seed_official_slate(
    session,
    *,
    draw="PG-LEARN",
    n=4,
    week_type="weekend",
    archived=True,
    with_predictions=True,
    with_results=True,
    friendly=False,
    sanity=True,
    probs_list=None,
    rec_list=None,
    result_list=None,
    conflict_pos=None,
) -> ProgolSlateModel:
    """Seed a slate with official LN lineage (so it is comparable)."""
    comp = CompetitionModel(
        name="International Friendlies" if friendly else "La Liga ES",
        country="ES",
    )
    session.add(comp)
    session.flush()

    slate = ProgolSlateModel(
        label=draw,
        draw_code=draw,
        week_type=week_type,
        composition_hash=f"hash-{draw}",
        slate_version=1,
        is_archived=archived,
    )
    session.add(slate)
    session.flush()

    session.add(
        ProgolSlateProposalModel(
            draw_code=draw,
            week_type=week_type,
            source_name="Lotería Nacional",
            source_url=_OFFICIAL_URL,
            status="promoted",
            promoted_slate_id=slate.id,
        )
    )
    src = SourceModel(
        name=f"res-{draw}", base_url="http://x", kind="manual", result_source_priority=40
    )
    src2 = SourceModel(
        name=f"res2-{draw}", base_url="http://y", kind="manual", result_source_priority=50
    )
    session.add_all([src, src2])
    session.flush()

    for pos in range(1, n + 1):
        home = TeamModel(name=f"{draw}-H{pos}", country="ES")
        away = TeamModel(name=f"{draw}-A{pos}", country="ES")
        session.add_all([home, away])
        session.flush()
        match = MatchModel(
            competition_id=comp.id,
            home_team_id=home.id,
            away_team_id=away.id,
            kickoff_at=_BASE,
        )
        session.add(match)
        session.flush()
        session.add(ProgolSlateMatchModel(slate_id=slate.id, match_id=match.id, position=pos))

        if with_predictions:
            probs = probs_list[pos - 1] if probs_list else (0.6, 0.25, 0.15)
            rec = rec_list[pos - 1] if rec_list else "1"
            audit = None
            if sanity:
                audit = json.dumps(
                    {
                        "raw_probabilities": {"L": probs[0], "E": probs[1], "V": probs[2]},
                        "display_probabilities": {"L": probs[0], "E": probs[1], "V": probs[2]},
                        "decision_probabilities": {"L": probs[0], "E": probs[1], "V": probs[2]},
                        "final_status": "LISTO",
                        "evidence_level": "high",
                        "sanity_flags": ["INTERNATIONAL_FRIENDLY"] if friendly else [],
                        "sanity_policy_version": "test_v1",
                        "model_artifact_id": "test-artifact",
                        "fallback_used": False,
                    }
                )
            session.add(
                PredictionModel(
                    match_id=match.id,
                    slate_id=slate.id,
                    composition_hash=slate.composition_hash,
                    slate_version=slate.slate_version,
                    generated_at=_BASE,
                    home_probability=probs[0],
                    draw_probability=probs[1],
                    away_probability=probs[2],
                    recommended_outcome=rec,
                    confidence_band="high",
                    sanity_audit_json=audit,
                )
            )

        if with_results:
            code = result_list[pos - 1] if result_list else ("1" if pos % 2 == 1 else "2")
            goals = {"1": (2, 0), "2": (0, 2), "X": (1, 1)}[code]
            session.add(
                MatchResultModel(
                    match_id=match.id,
                    source_id=src.id,
                    played_at=_BASE,
                    home_goals=goals[0],
                    away_goals=goals[1],
                    result_code=code,
                )
            )
            if conflict_pos == pos:
                other = "2" if code != "2" else "1"
                ogoals = {"1": (2, 0), "2": (0, 2), "X": (1, 1)}[other]
                session.add(
                    MatchResultModel(
                        match_id=match.id,
                        source_id=src2.id,
                        played_at=_BASE,
                        home_goals=ogoals[0],
                        away_goals=ogoals[1],
                        result_code=other,
                    )
                )

    session.commit()
    return slate


def manual_payload(draw, n, *, complete=True, sign="L", score="2-0"):
    count = n if complete else max(1, n - 2)
    return {
        "draw_code": draw,
        "source": "manual_official",
        "results": [
            {"position": pos, "sign": sign, "score": score, "source_note": "test"}
            for pos in range(1, count + 1)
        ],
    }
