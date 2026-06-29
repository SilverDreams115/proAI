"""Visible/raw probability resolution + PG-2338-shaped postmortem.

Covers the read-path fix surfaced by the PG-2338 postmortem:

  * a *legacy* closed prediction whose probability COLUMNS hold the raw
    (uncapped) model output, while the sanity audit holds the capped
    *decision* vector, must be SCORED and DISPLAYED on the decision vector
    (raw stays visible only as ``raw_probabilities``) — without regenerating
    the closed prediction;
  * a current-engine row with no audit falls back to the columns (no-op);
  * the PG-2338 official sequence (V L L E L E V E V E L E L E) ingests by
    draw_code + position, counts 6 real draws, and scores against the
    pre-close snapshot only.
"""
from __future__ import annotations

import json

import pytest
from sqlalchemy.orm import Session

from app.services.jornada_scoring_service import JornadaScoringService
from app.services.live_result_service import LiveResultService
from app.services.live_results_service import LiveResultsService
from app.services.prediction_probabilities import (
    raw_probabilities,
    visible_probabilities,
)
from app.services.results_ingestion_service import ResultsIngestionService
from app.domain.entities import MatchResultStatus

from tests.test_live_results import (  # noqa: E402
    _make_official,
    _match_ids,
    _past,
    _seed_slate,
    _source,
)


@pytest.fixture
def db(tmp_path):
    from app.db import session as db_mod
    from app.db.migrations import run_migrations
    from app.db.session import configure_session

    configure_session(f"sqlite:///{tmp_path / 'pred_probs.db'}")
    run_migrations(db_mod.engine)
    with Session(db_mod.engine) as session:
        yield session


class _FakePred:
    """Minimal stand-in with the attributes the resolver reads."""

    def __init__(self, home, draw, away, audit=None):
        self.home_probability = home
        self.draw_probability = draw
        self.away_probability = away
        self.sanity_audit_json = audit


def test_visible_prefers_decision_vector_over_raw_columns():
    # Legacy shape: columns = raw 0.96/0.02/0.02, audit decision = capped.
    audit = json.dumps(
        {
            "raw_probabilities": {"L": 0.96, "E": 0.02, "V": 0.02},
            "decision_probabilities": {"L": 0.60, "E": 0.20, "V": 0.20},
            "display_probabilities": {"L": 0.60, "E": 0.20, "V": 0.20},
        }
    )
    pred = _FakePred(0.96, 0.02, 0.02, audit=audit)
    assert visible_probabilities(pred) == (0.60, 0.20, 0.20)
    assert raw_probabilities(pred) == (0.96, 0.02, 0.02)


def test_visible_falls_back_to_columns_without_audit():
    pred = _FakePred(0.44, 0.30, 0.26, audit=None)
    assert visible_probabilities(pred) == (0.44, 0.30, 0.26)
    assert raw_probabilities(pred) is None


def test_visible_falls_back_on_malformed_audit():
    pred = _FakePred(0.5, 0.3, 0.2, audit="{not json")
    assert visible_probabilities(pred) == (0.5, 0.3, 0.2)
    assert raw_probabilities(pred) is None


def _attach_legacy_audit(session: Session, slate) -> None:
    """Rewrite each prediction into the legacy shape: columns keep the raw
    extreme value, the audit carries a capped decision vector."""
    from app.models.tables import PredictionModel
    from sqlalchemy import select

    preds = session.scalars(
        select(PredictionModel).where(PredictionModel.slate_id == slate.id)
    ).all()
    for p in preds:
        raw = {"L": 0.96, "E": 0.02, "V": 0.02}
        decision = {"L": 0.60, "E": 0.20, "V": 0.20}
        p.home_probability = raw["L"]
        p.draw_probability = raw["E"]
        p.away_probability = raw["V"]
        p.sanity_audit_json = json.dumps(
            {
                "raw_probabilities": raw,
                "decision_probabilities": decision,
                "display_probabilities": decision,
            }
        )
    session.flush()


def test_score_uses_decision_vector_not_raw_columns(db):
    """Brier must reflect the capped decision vector, not the raw 0.96."""
    slate = _seed_slate(
        db, draw_code="PG-DEC", n=3, closes_at=_past(), outcomes=["1", "1", "1"]
    )
    _make_official(db, slate)
    _attach_legacy_audit(db, slate)
    src = _source(db, "ln", priority=40)
    # All three matches end 0-0 (draw) — worst case for a raw 0.96 home bet.
    for mid in _match_ids(slate):
        LiveResultService(db).record_observation(
            match_id=mid, source_id=src.id, status=MatchResultStatus.FULL_TIME,
            home_goals=0, away_goals=0, is_final=True,
        )
    score = JornadaScoringService(db).compute_for_slate(slate)
    # Per-match Brier on a draw: raw (0.96,0.02,0.02) -> 0.96^2+0.98^2+0.02^2
    # = 1.881; decision (0.60,0.20,0.20) -> 0.36+0.64+0.04 = 1.04. The fix
    # must produce the lower, decision-based number.
    assert score.brier_score_avg is not None
    assert abs(score.brier_score_avg - 1.04) < 1e-6
    # And the per-match detail must surface raw separately, never hide it.
    detail = json.loads(score.details_json)[0]
    assert detail["home_probability"] == 0.60
    assert detail["raw_probabilities"] == {"L": 0.96, "E": 0.02, "V": 0.02}


PG2338_TEXT = """
PROGOL CONCURSO 2338
1 CHEQUIA 0-3 MEXICO FINAL
2 SUIZA 2-1 CANADA FINAL
3 BOSNIA 3-1 CATAR FINAL
4 JAPON 1-1 SUECIA FINAL
5 TURQUIA 3-2 EUA FINAL
6 PARAGUAY 0-0 AUSTRALIA FINAL
7 NORUEGA 1-4 FRANCIA FINAL
8 CABO VERDE 0-0 ARABIA SAUDITA FINAL
9 URUGUAY 0-1 ESPANA FINAL
10 EGIPTO 1-1 IRAN FINAL
11 CROACIA 2-1 GHANA FINAL
12 COLOMBIA 0-0 PORTUGAL FINAL
13 REP CONGO 3-1 UZBEKISTAN FINAL
14 ARGELIA 3-3 AUSTRIA FINAL
"""

PG2338_SEQUENCE = ["2", "1", "1", "X", "1", "X", "2", "X", "2", "X", "1", "X", "1", "X"]


def test_pg2338_sequence_ingests_with_six_draws(db):
    slate = _seed_slate(db, draw_code="PG-2338", n=14, closes_at=_past())
    _make_official(db, slate)
    report = ResultsIngestionService(db).ingest_for_slate(
        slate,
        PG2338_TEXT,
        source_url="operator://screenshot/pg-2338",
        source_name="operator_screenshot_pg_2338",
        source_kind="operator_manual",
        source_priority=60,
    )
    db.commit()
    assert report["recorded"] == 14
    assert report["finals"] == 14
    assert report["skipped_pending"] == 0
    assert report["unmapped_positions"] == []
    assert report["source"] == "operator_screenshot_pg_2338"

    results = LiveResultsService(db).build_live_results(slate)
    by_pos = {m["position"]: m for m in results["matches"]}
    assert [by_pos[i]["result_code"] for i in range(1, 15)] == PG2338_SEQUENCE
    score = LiveResultsService(db).build_live_score(slate)
    assert score["empates_reales_hasta_ahora"] == 6
    assert score["is_complete"] is True


def test_operator_source_ranks_below_ln(db):
    """Operator screenshot is traceable but loses to a later LN acta."""
    from app.models.tables import SourceModel
    from sqlalchemy import select

    slate = _seed_slate(db, draw_code="PG-2338", n=14, closes_at=_past())
    _make_official(db, slate)
    ResultsIngestionService(db).ingest_for_slate(
        slate, PG2338_TEXT,
        source_name="operator_screenshot_pg_2338",
        source_kind="operator_manual",
        source_priority=60,
    )
    db.commit()
    op = db.scalar(
        select(SourceModel).where(SourceModel.name == "operator_screenshot_pg_2338")
    )
    assert op is not None
    assert op.kind == "operator_manual"
    # LN acta default priority (40) outranks the operator source (60).
    assert op.result_source_priority == 60
