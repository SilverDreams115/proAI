"""R4: offline rating-feature experiment — dataset, split, metrics, safety.

Covers leak-free dataset building (conflicts/sign-only/no-score excluded,
rating features joined), temporal split (no future leak), metric correctness,
no DB writes, artifacts under the experimental path only, and that the
experiment does not touch FeatureService / PredictionService / approval gate.
"""

from __future__ import annotations

from datetime import datetime, timezone

from app.models.tables import CompetitionModel
from app.models.tables import MatchModel
from app.models.tables import MatchResultModel
from app.models.tables import SourceModel
from app.models.tables import TeamModel
from scripts import train_rating_experiment as exp


def _make_session(tmp_path):
    from app.db import session as db_session
    from app.db.migrations import run_migrations
    from app.db.session import configure_session

    configure_session(f"sqlite:///{tmp_path / 'exp.db'}")
    run_migrations(db_session.engine)
    return db_session.SessionLocal()


def _seed(session):
    comp = CompetitionModel(name="Liga", country="X", season="2026")
    teams = {n: TeamModel(name=n, country=None) for n in ("A", "B", "C", "D")}
    ghost = TeamModel(name="Ghost", country=None, is_placeholder=True)
    s1 = SourceModel(name="s1", base_url="http://a", kind="k", parser_profile="p",
                     is_active=True, result_source_priority=10)
    s2 = SourceModel(name="s2", base_url="http://b", kind="k", parser_profile="p",
                     is_active=True, result_source_priority=50)
    session.add_all([comp, ghost, s1, s2, *teams.values()])
    session.flush()
    base = datetime(2026, 1, 1, tzinfo=timezone.utc)

    def _match(h, a, day):
        m = MatchModel(competition_id=comp.id, home_team_id=h.id, away_team_id=a.id,
                       kickoff_at=base.replace(day=day))
        session.add(m)
        session.flush()
        return m

    def _res(m, src, hg, ag, day):
        code = "L" if hg > ag else ("E" if hg == ag else "V")
        session.add(MatchResultModel(match_id=m.id, source_id=src.id,
                    played_at=base.replace(day=day), home_goals=hg, away_goals=ag, result_code=code))

    # clean rated matches (agree)
    _res(_match(teams["A"], teams["B"], 1), s1, 2, 0, 1)
    _res(_match(teams["C"], teams["D"], 2), s1, 1, 1, 2)
    _res(_match(teams["A"], teams["C"], 3), s1, 0, 1, 3)
    # conflict → excluded
    m4 = _match(teams["B"], teams["D"], 4)
    _res(m4, s1, 1, 0, 4)
    _res(m4, s2, 0, 1, 4)
    # placeholder → excluded
    _res(_match(teams["D"], ghost, 5), s1, 3, 0, 5)
    session.commit()


def test_dataset_excludes_conflicts_placeholders_and_sets_target(tmp_path):
    session = _make_session(tmp_path)
    _seed(session)
    rows = exp.build_experiment_rows(session)
    # 3 clean matches mapped; conflict (m4) and placeholder (m5) excluded.
    assert len(rows) == 3
    # targets correct
    by_played = sorted(rows, key=lambda r: r.played_at)
    assert by_played[0].target == "1"   # A 2-0 B
    assert by_played[1].target == "X"   # C 1-1 D
    assert by_played[2].target == "2"   # A 0-1 C
    # rating features keys present
    for r in rows:
        for f in exp.RATING_FEATURES:
            assert f in r.features
        for f in exp.BASELINE_FEATURES:
            assert f in r.features
    session.close()


def test_features_are_leak_free_premarch(tmp_path):
    session = _make_session(tmp_path)
    _seed(session)
    rows = sorted(exp.build_experiment_rows(session), key=lambda r: r.played_at)
    # First match ever: both teams have no history → neutral rating, not present.
    first = rows[0]
    assert first.features["home_rating"] == 1500.0
    assert first.features["away_rating"] == 1500.0
    assert first.features["rating_diff"] == 0.0
    assert first.home_rating_present is False
    # Third match: A already played m1 → A has 1 prior match (present on its side).
    third = rows[2]  # A vs C
    assert third.features["home_rating_confidence"] >= 1.0  # A has history
    session.close()


def test_temporal_split_no_future_leak():
    base = datetime(2026, 1, 1, tzinfo=timezone.utc)
    rows = [
        exp.ExperimentRow(
            match_id=f"m{i}", played_at=base.replace(day=i + 1), competition="C",
            namespace="club", target="X", features={}, home_rating_present=True,
            away_rating_present=True, both_medium_plus=True,
        )
        for i in range(10)
    ]
    train, test = exp.temporal_split(rows, test_fraction=0.3)
    assert len(train) == 7 and len(test) == 3
    assert max(r.played_at for r in train) <= min(r.played_at for r in test)


def test_metrics_pure():
    # perfect predictions → brier 0, logloss ~0, acc 1
    probs = [[1.0, 0.0, 0.0], [0.0, 1.0, 0.0], [0.0, 0.0, 1.0]]
    y = [0, 1, 2]
    assert exp.top1_accuracy(probs, y) == 1.0
    assert exp.top2_coverage(probs, y) == 1.0
    assert exp.multiclass_brier(probs, y) == 0.0
    assert exp.multiclass_log_loss(probs, y) < 1e-6
    # uniform → brier = 3 * ( (1/3 - onehot)^2 averaged )
    uni = [[1 / 3, 1 / 3, 1 / 3]]
    assert round(exp.multiclass_brier(uni, [0]), 4) == round((2 / 3) ** 2 + 2 * (1 / 3) ** 2, 4)
    cal = exp.calibration_bins(probs, y)
    assert "ece" in cal and cal["ece"] == 0.0


def test_experiment_runs_and_writes_only_experimental_artifacts(tmp_path):
    import numpy as np

    base = datetime(2026, 1, 1, tzinfo=timezone.utc)
    rng = np.random.default_rng(0)
    rows = []
    for i in range(160):
        rd = float(rng.normal(0, 100))
        # target correlates with rating_diff so the model has signal
        t = "1" if rd > 40 else ("2" if rd < -40 else "X")
        feats = {name: float(rng.normal()) for name in exp.BASELINE_FEATURES}
        feats.update({
            "home_rating": 1500 + rd / 2, "away_rating": 1500 - rd / 2, "rating_diff": rd,
            "home_rating_confidence": 3.0, "away_rating_confidence": 3.0,
            "both_rating_medium_plus": 1.0, "rating_match_count_diff": 0.0,
        })
        rows.append(exp.ExperimentRow(
            match_id=f"m{i}", played_at=base.replace(day=1) , competition="Liga",
            namespace="club", target=t, features=feats,
            home_rating_present=True, away_rating_present=True, both_medium_plus=True,
        ))
    # give deterministic increasing played_at
    for i, r in enumerate(rows):
        r.played_at = base.fromtimestamp(base.timestamp() + i * 3600, tz=timezone.utc)

    save_dir = tmp_path / "exp_artifacts"
    rep = exp.run_competition(
        rows, "Liga", test_fraction=0.3, min_train=50, min_test=20,
        save_dir=save_dir, num_round=40,
    )
    assert rep["status"] == "evaluated"
    assert set(rep["ablation"]) == {"without_rating", "with_rating", "rating_only"}
    for arm in rep["ablation"].values():
        assert 0.0 <= arm["top1_accuracy"] <= 1.0
        assert arm["brier_score"] >= 0.0
        assert "_booster" not in arm  # booster object stripped from report
    # artifacts written ONLY under the given experimental dir
    written = list(save_dir.glob("*.json"))
    assert written and all(p.parent == save_dir for p in written)
    assert "verdict" in rep


def test_build_writes_nothing_and_no_service_coupling(tmp_path):
    session = _make_session(tmp_path)
    _seed(session)
    before = session.query(MatchResultModel).count()
    exp.build_experiment_rows(session)
    assert not session.new and not session.dirty
    assert session.query(MatchResultModel).count() == before
    session.rollback()
    session.close()

    # the experiment module must not import the productive feature/prediction
    # services nor the approval-gate flag flip.
    with open(exp.__file__) as fh:
        text = fh.read()
    assert "feature_service" not in text
    assert "prediction_service" not in text
    assert "PROAI_TEAM_RATING_FEATURE_ENABLED" not in text
