"""Slate reality classification: official vs synthetic/demo.

Locks the integrity rule that demo slates (all placeholder competitions,
no official proposal lineage) are never scored as real and are flagged
"no comparable", while slates promoted from an official LN proposal are
official_real and comparable.
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest
from sqlalchemy.orm import Session

from app.domain.entities import MatchResultStatus
from app.models.tables import ProgolSlateProposalModel
from app.services.live_result_service import LiveResultService
from app.services.live_results_service import (
    LiveResultsService,
    finalize_complete_closed_slates,
)
from app.services.slate_classification_service import (
    SlateClassification,
    classify_slate,
)
from tests.test_live_results import _seed_slate, _match_ids, _source, _past  # noqa: E402


@pytest.fixture
def db(tmp_path):
    from app.db import session as db_mod
    from app.db.migrations import run_migrations
    from app.db.session import configure_session

    configure_session(f"sqlite:///{tmp_path / 'classify.db'}")
    run_migrations(db_mod.engine)
    with Session(db_mod.engine) as session:
        yield session


def _add_official_proposal(session, slate):
    p = ProgolSlateProposalModel(
        draw_code=slate.draw_code,
        week_type=slate.week_type,
        source_name="LN Progol Guía",
        source_url="https://www.loterianacional.gob.mx/Progol/Guia.pdf",
        status="promoted",
        promoted_slate_id=slate.id,
    )
    session.add(p)
    session.flush()
    return p


def test_synthetic_demo_classification(db):
    # _seed_slate uses "International Friendlies" with no proposal → demo.
    slate = _seed_slate(db, draw_code="PG-2336", n=14, closes_at=_past())
    reality = classify_slate(db, slate)
    assert reality.classification == SlateClassification.SYNTHETIC_DEMO
    assert reality.comparable_with_results is False
    assert reality.has_official_proposal is False
    assert reality.competitions == ["International Friendlies"]


def test_official_proposal_without_results_is_no_results_yet(db):
    slate = _seed_slate(db, draw_code="PG-9999", n=14, closes_at=_past())
    _add_official_proposal(db, slate)
    reality = classify_slate(db, slate)
    # Real LN lineage but nothing ingested → comparable, just no data yet.
    assert reality.classification == SlateClassification.OFFICIAL_NO_RESULTS
    assert reality.comparable_with_results is True
    assert reality.has_official_proposal is True


def test_official_with_results_is_official_real(db):
    slate = _seed_slate(db, draw_code="PG-9997", n=2, closes_at=_past(), outcomes=["1", "1"])
    _add_official_proposal(db, slate)
    src = _source(db, "LN")
    LiveResultService(db).record_observation(
        match_id=_match_ids(slate)[0], source_id=src.id,
        status=MatchResultStatus.FULL_TIME, home_goals=1, away_goals=0, is_final=True,
    )
    reality = classify_slate(db, slate)
    assert reality.classification == SlateClassification.OFFICIAL_REAL
    assert reality.comparable_with_results is True


def test_demo_not_scored_officially(db):
    from app.repositories.jornada_score_repository import JornadaScoreRepository

    slate = _seed_slate(db, draw_code="PGM-799", week_type="midweek", n=2,
                        closes_at=_past(), outcomes=["1", "1"])
    src = _source(db, "LN")
    for mid in _match_ids(slate):
        LiveResultService(db).record_observation(
            match_id=mid, source_id=src.id, status=MatchResultStatus.FULL_TIME,
            home_goals=1, away_goals=0, is_final=True,
        )
    db.commit()
    summary = finalize_complete_closed_slates(db, now=datetime.now(timezone.utc))
    # Demo slate is skipped for official scoring even though it is all-final.
    assert "PGM-799" in summary["skipped_non_official"]
    assert "PGM-799" not in summary["finalized"]
    assert JornadaScoreRepository(db).get_latest_for_slate(slate.id) is None


def test_official_real_is_scored(db):
    from app.repositories.jornada_score_repository import JornadaScoreRepository

    slate = _seed_slate(db, draw_code="PG-9998", n=2, closes_at=_past(), outcomes=["1", "1"])
    _add_official_proposal(db, slate)
    src = _source(db, "LN")
    for mid in _match_ids(slate):
        LiveResultService(db).record_observation(
            match_id=mid, source_id=src.id, status=MatchResultStatus.FULL_TIME,
            home_goals=1, away_goals=0, is_final=True,
        )
    db.commit()
    summary = finalize_complete_closed_slates(db, now=datetime.now(timezone.utc))
    assert "PG-9998" in summary["finalized"]
    saved = JornadaScoreRepository(db).get_latest_for_slate(slate.id)
    assert saved is not None and saved.is_complete is True


def test_comparison_flags_demo_as_not_comparable(db):
    slate = _seed_slate(db, draw_code="PG-2336", n=3, closes_at=_past())
    comp = LiveResultsService(db).build_result_comparison(slate)
    assert comp["classification"] == "synthetic_demo"
    assert comp["comparable"] is False
    assert any("demo" in r for r in comp["classification_reasons"])


def test_weekend_and_ms_classified_independently(db):
    wk = _seed_slate(db, draw_code="PG-2336", week_type="weekend", n=14, closes_at=_past())
    ms = _seed_slate(db, draw_code="PGM-799", week_type="midweek", n=9, closes_at=_past())
    _add_official_proposal(db, wk)  # only the weekend gets official lineage
    assert classify_slate(db, wk).comparable_with_results is True
    assert classify_slate(db, ms).classification == SlateClassification.SYNTHETIC_DEMO
    assert classify_slate(db, ms).comparable_with_results is False
