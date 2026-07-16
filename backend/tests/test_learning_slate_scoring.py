"""R7.0 — learning slate scoring (read-only post-jornada comparison)."""
from __future__ import annotations

from sqlalchemy import func, select

from app.models.tables import MatchResultModel, PredictionModel
from app.services.learning_slate_scoring_service import LearningSlateScoringService
from backend.tests._learning_seed import learn_db, seed_official_slate  # noqa: F401


def _counts(session):
    return (
        int(session.scalar(select(func.count()).select_from(MatchResultModel)) or 0),
        int(session.scalar(select(func.count()).select_from(PredictionModel)) or 0),
    )


def test_scoring_compares_prediction_vs_result(learn_db):  # noqa: F811
    """7 — scoring lines up each prediction against its canonical result."""
    slate = seed_official_slate(learn_db, draw="PG-SCORE", n=4)
    report = LearningSlateScoringService(learn_db).score_slate(slate)
    assert report["match_count"] == 4
    assert report["score"]["total"] == 4
    by_pos = {p["position"]: p for p in report["by_position"]}
    assert by_pos[1]["prediction"] == "L"
    assert by_pos[1]["actual"] == "L"
    assert by_pos[1]["hit"] is True
    assert by_pos[2]["actual"] == "V"
    assert by_pos[2]["hit"] is False


def test_scoring_never_rebuilds_money_mode_for_closed_slates(learn_db, monkeypatch):  # noqa: F811
    """Latency + honesty regression (tabs audit 2026-07-16): scoring an
    archived slate rebuilt the full Money Mode pipeline (~3s each, fanned over
    every slate on /learning/completed-slates/scores) and reported TODAY'S
    verdict as if it had governed the played jornada. Closed slates must skip
    the rebuild and report the flag as unknown (None)."""
    import app.services.money_mode_service as money_mode

    def _boom(*args, **kwargs):
        raise AssertionError("money mode must not be rebuilt for a closed slate")

    monkeypatch.setattr(money_mode, "build_money_mode", _boom)
    slate = seed_official_slate(learn_db, draw="PG-MMCLOSED", n=4, archived=True)
    report = LearningSlateScoringService(learn_db).score_slate(slate)
    assert report["money_mode_blocked"] is None
    by_pos = {p["position"]: p for p in report["by_position"]}
    assert by_pos[1]["was_money_mode_blocked"] is None


def test_scoring_counts_hits(learn_db):  # noqa: F811
    """8 — odd positions hit (home), even miss (away) -> 2/4 hits."""
    slate = seed_official_slate(learn_db, draw="PG-HITS", n=4)
    report = LearningSlateScoringService(learn_db).score_slate(slate)
    assert report["score"]["hits"] == 2
    assert report["score"]["hit_rate"] == 0.5
    assert report["comparable"] is True


def test_scoring_computes_brier_and_logloss(learn_db):  # noqa: F811
    """9 — Brier and log-loss are computed and within sane bounds."""
    slate = seed_official_slate(learn_db, draw="PG-BRIER", n=4)
    report = LearningSlateScoringService(learn_db).score_slate(slate)
    brier = report["score"]["brier"]
    logloss = report["score"]["logloss"]
    assert brier is not None and 0.0 <= brier <= 2.0
    assert logloss is not None and logloss > 0.0
    assert report["score"]["top2_covered"] >= report["score"]["top1_hits"]


def test_scoring_without_results_is_not_comparable(learn_db):  # noqa: F811
    slate = seed_official_slate(learn_db, draw="PG-NORES", n=4, with_results=False)
    report = LearningSlateScoringService(learn_db).score_slate(slate)
    assert report["comparable"] is False
    assert report["score"]["total"] == 0
    assert all(p["error_type"] == "data_quality_issue" for p in report["by_position"])


def test_scoring_is_read_only(learn_db):  # noqa: F811
    slate = seed_official_slate(learn_db, draw="PG-RO", n=4)
    before = _counts(learn_db)
    LearningSlateScoringService(learn_db).score_slate(slate)
    assert _counts(learn_db) == before
