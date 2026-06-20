from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy.orm import Session

from app.models.tables import CompetitionModel
from app.models.tables import MatchModel
from app.models.tables import MatchFeatureSnapshotModel
from app.models.tables import ProgolSlateMatchModel
from app.models.tables import ProgolSlateModel
from app.models.tables import TeamModel
from scripts.audit_feature_coverage import (
    analyze_match_coverage,
    build_feature_coverage,
    summarize,
    _competition_table,
)


def _vec(*, home_recent, away_recent, h2h, evidence):
    return {
        "home_recent_matches": float(home_recent),
        "away_recent_matches": float(away_recent),
        "head_to_head_matches": float(h2h),
        "evidence_count": float(evidence),
        "form_gap": 0.0,
        "home_advantage": 1.0,
    }


def _cov(**kw):
    base = dict(
        position=1, home="H", away="A", competition_name="International Friendlies",
        readiness="context_only", engine="heuristic_blend", competition_approved=False,
        insufficient_data=False, has_prediction=True, has_result=False,
    )
    base.update(kw)
    return analyze_match_coverage(**base)


# 1. Summarize coverage.
def test_summarize_counts(db_unused=None) -> None:
    rows = [
        _cov(feature_vector=_vec(home_recent=3, away_recent=3, h2h=0, evidence=0)),
        _cov(feature_vector=_vec(home_recent=0, away_recent=0, h2h=0, evidence=0), insufficient_data=True),
        _cov(feature_vector=_vec(home_recent=5, away_recent=5, h2h=4, evidence=2),
             engine="xgboost", competition_approved=True, competition_name="Brasileirao", readiness="ready"),
    ]
    s = summarize(rows)
    assert s["total_matches"] == 3
    assert s["fallback_count"] == 2  # two heuristic
    assert s["fallback_rate"] == round(2 / 3, 3)
    assert s["usable_model_count"] == 1  # only the approved + sufficient one


# 2. Detect default/null features.
def test_detects_null_and_default_features() -> None:
    cov = _cov(feature_vector=_vec(home_recent=0, away_recent=2, h2h=0, evidence=0))
    assert set(cov.null_features) == {"home_recent_matches", "head_to_head_matches", "evidence_count"}
    # default_features = all non-constant features at 0.0 (home_advantage excluded)
    assert "home_advantage" not in cov.default_features
    assert "form_gap" in cov.default_features


# 3. Detect fallback reasons.
def test_detects_fallback_reasons() -> None:
    cov = _cov(
        feature_vector=_vec(home_recent=0, away_recent=2, h2h=0, evidence=0),
        engine="heuristic_blend", competition_approved=False, insufficient_data=True,
    )
    assert "engine_is_heuristic_fallback" in cov.fallback_reasons
    assert "competition_not_xgboost_approved" in cov.fallback_reasons
    assert "insufficient_data_anchors" in cov.fallback_reasons
    assert "team_without_recent_results" in cov.fallback_reasons
    assert "no_head_to_head" in cov.fallback_reasons
    assert "no_contextual_evidence" in cov.fallback_reasons
    assert "data_missing_ratings" in cov.data_gap_tags
    assert cov.usable_for_trained_model is False


def test_usable_when_approved_and_sufficient() -> None:
    cov = _cov(
        feature_vector=_vec(home_recent=5, away_recent=5, h2h=4, evidence=2),
        engine="xgboost", competition_approved=True, insufficient_data=False,
        competition_name="Brasileirao", readiness="ready",
    )
    assert cov.usable_for_trained_model is True
    assert "engine_is_heuristic_fallback" not in cov.fallback_reasons


# 4. Group by competition.
def test_competition_table_groups_and_sorts() -> None:
    rows = [
        _cov(competition_name="International Friendlies",
             feature_vector=_vec(home_recent=1, away_recent=1, h2h=0, evidence=0),
             insufficient_data=True, has_result=False),
        _cov(competition_name="Brasileirao", engine="xgboost", competition_approved=True,
             readiness="ready", has_result=True,
             feature_vector=_vec(home_recent=5, away_recent=5, h2h=4, evidence=3)),
    ]
    table = _competition_table(rows)
    assert table[0]["competition"] == "Brasileirao"  # higher usable_model_rate first
    assert table[0]["usable_model_rate"] == 1.0
    assert table[0]["complete_results"] == 1
    fr = next(t for t in table if t["competition"] == "International Friendlies")
    assert fr["usable_model_rate"] == 0.0
    assert fr["fallback_rate"] == 1.0


# --- DB-backed integration (no writes; slate without results) ---------------

@pytest.fixture
def db(tmp_path):
    from app.db import session as db_mod
    from app.db.migrations import run_migrations
    from app.db.session import configure_session

    configure_session(f"sqlite:///{tmp_path / 'cov.db'}")
    run_migrations(db_mod.engine)
    with Session(db_mod.engine) as session:
        yield session


def _slate(session: Session) -> ProgolSlateModel:
    comp = CompetitionModel(name="International Friendlies", is_placeholder=False)
    session.add(comp)
    session.flush()
    slate = ProgolSlateModel(label="PG-T", draw_code="PG-T", week_type="weekend",
                             slate_version=1, composition_hash="h")
    session.add(slate)
    session.flush()
    for pos in (1, 2):
        h = TeamModel(name=f"H{pos}", is_placeholder=False)
        a = TeamModel(name=f"A{pos}", is_placeholder=False)
        session.add_all([h, a])
        session.flush()
        m = MatchModel(competition_id=comp.id, home_team_id=h.id, away_team_id=a.id,
                       kickoff_at=datetime(2026, 6, 25, 7, tzinfo=timezone.utc) + timedelta(hours=pos))
        session.add(m)
        session.flush()
        session.add(ProgolSlateMatchModel(slate_id=slate.id, match_id=m.id, position=pos))
    session.flush()
    return slate


# 5 & 6. No DB writes; works with a slate that has no results.
def test_build_coverage_no_writes_without_results(db) -> None:
    _slate(db)
    db.commit()
    before = (
        db.query(MatchFeatureSnapshotModel).count(),
        db.query(MatchModel).count(),
    )
    report = build_feature_coverage(db, draw_code="PG-T")
    after = (
        db.query(MatchFeatureSnapshotModel).count(),
        db.query(MatchModel).count(),
    )
    assert before == after  # no feature snapshots persisted, no rows added
    assert report["historical"]["total_matches"] == 2
    assert report["historical"]["matches_with_results"] == 0
    # No model artifact + no data -> everything falls back.
    assert report["global_summary"]["fallback_rate"] == 1.0
    assert report["global_summary"]["usable_model_count"] == 0


# 7. Works with "historical" slates simulated (multiple slates aggregated).
def test_all_slates_aggregates(db) -> None:
    _slate(db)
    second = ProgolSlateModel(label="PG-U", draw_code="PG-U", week_type="weekend",
                              slate_version=1, composition_hash="h2")
    db.add(second)
    db.flush()
    comp = db.query(CompetitionModel).first()
    h = TeamModel(name="HX", is_placeholder=False)
    a = TeamModel(name="AX", is_placeholder=False)
    db.add_all([h, a])
    db.flush()
    m = MatchModel(competition_id=comp.id, home_team_id=h.id, away_team_id=a.id,
                   kickoff_at=datetime(2026, 7, 1, 7, tzinfo=timezone.utc))
    db.add(m)
    db.flush()
    db.add(ProgolSlateMatchModel(slate_id=second.id, match_id=m.id, position=1))
    db.commit()

    report = build_feature_coverage(db, all_slates=True)
    assert report["historical"]["total_slates"] == 2
    assert report["historical"]["total_matches"] == 3
    assert any(c["competition"] == "International Friendlies" for c in report["competition_table"])
