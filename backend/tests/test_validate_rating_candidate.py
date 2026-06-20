"""R4.2: held-out candidate validation — split, calibration isolation, guard.

Confirms train/cal/test temporal split has no leak, the calibrator is fit on
the calibration fold (never on test), PAV isotonic is monotone, the guard
filters confident rows, and the script writes no DB and no productive coupling.
"""

from __future__ import annotations

from datetime import datetime, timezone

from scripts import train_rating_experiment as exp
from scripts import validate_rating_candidate as val


def _rows(n=320, seed=1):
    import numpy as np
    base = datetime(2026, 1, 1, tzinfo=timezone.utc)
    rng = np.random.default_rng(seed)
    rows = []
    for i in range(n):
        rd = float(rng.normal(0, 100))
        t = "1" if rd > 35 else ("2" if rd < -35 else "X")
        feats = {name: float(rng.normal()) for name in exp.BASELINE_FEATURES}
        feats.update({
            "home_rating": 1500 + rd / 2, "away_rating": 1500 - rd / 2, "rating_diff": rd,
            "home_rating_confidence": 3.0, "away_rating_confidence": 3.0,
            "both_rating_medium_plus": 1.0, "rating_match_count_diff": 0.0,
        })
        rows.append(exp.ExperimentRow(
            match_id=f"m{i}",
            played_at=base.fromtimestamp(base.timestamp() + i * 3600, tz=timezone.utc),
            competition="International Friendlies", namespace="national", target=t,
            features=feats, home_rating_present=True, away_rating_present=True,
            both_medium_plus=True,
        ))
    return rows


def test_three_way_split_no_leak():
    rows = _rows(100)
    tr, cal, te = val.three_way_split(rows, 0.6, 0.2)
    assert len(tr) == 60 and len(cal) == 20 and len(te) == 20
    assert max(r.played_at for r in tr) <= min(r.played_at for r in cal)
    assert max(r.played_at for r in cal) <= min(r.played_at for r in te)


def test_pav_isotonic_is_monotone_and_bounded():
    xs = [0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9]
    ys = [0, 0, 1, 0, 1, 1, 0, 1, 1]  # noisy but increasing trend
    knots, fit = val._pav(xs, ys)
    assert all(fit[i] <= fit[i + 1] + 1e-9 for i in range(len(fit) - 1))  # non-decreasing
    for x in (0.0, 0.15, 0.55, 1.0):
        v = val._isotonic_apply(knots, fit, x)
        assert 0.0 <= v <= 1.0


def test_isotonic_multiclass_renormalizes():
    probs = [[0.6, 0.3, 0.1], [0.2, 0.2, 0.6], [0.4, 0.4, 0.2]] * 20
    y = [0, 2, 1] * 20
    models = val.fit_isotonic_multiclass(probs, y)
    out = val.apply_isotonic_multiclass(models, probs)
    for row in out:
        assert abs(sum(row) - 1.0) < 1e-9


def test_calibrator_does_not_see_test_fold():
    # Temperature is fit on the calibration fold; permuting TEST targets must
    # not change the chosen temperature.
    rows = _rows(320, seed=2)
    r1 = val.validate(rows, competition="International Friendlies",
                      subset="both_medium_plus_only", num_round=30, save_dir=None)
    # flip every test-fold target; cal fold untouched
    _, _, test = val.three_way_split(rows)
    test_ids = {r.match_id for r in test}
    flip = {"1": "2", "2": "1", "X": "X"}
    rows2 = []
    for r in rows:
        if r.match_id in test_ids:
            rows2.append(exp.ExperimentRow(
                match_id=r.match_id, played_at=r.played_at, competition=r.competition,
                namespace=r.namespace, target=flip[r.target], features=r.features,
                home_rating_present=True, away_rating_present=True, both_medium_plus=True,
            ))
        else:
            rows2.append(r)
    r2 = val.validate(rows2, competition="International Friendlies",
                      subset="both_medium_plus_only", num_round=30, save_dir=None)
    t1 = r1["arms"]["with_rating_temperature_calibrated"]["temperature"]
    t2 = r2["arms"]["with_rating_temperature_calibrated"]["temperature"]
    assert t1 == t2  # calibrator never saw the test fold


def test_validate_reports_arms_and_test_metrics(tmp_path):
    rows = _rows(320, seed=3)
    rep = val.validate(rows, competition="International Friendlies",
                       subset="both_medium_plus_only", num_round=30, save_dir=tmp_path / "art")
    assert rep["status"] == "evaluated"
    assert rep["rows_train"] + rep["rows_calibration"] + rep["rows_test"] == rep["rows_total"]
    for arm in ("baseline_without_rating", "with_rating_uncalibrated",
                "with_rating_temperature_calibrated", "rating_only_uncalibrated"):
        assert "brier_score" in rep["arms"][arm]
    # isotonic skipped at this sample size (cal fold < 150)
    assert rep["arms"]["with_rating_isotonic_calibrated"]["status"] == "skipped"
    # oracle is present but explicitly not a criterion
    assert "oracle_diagnostic_not_a_criterion" in rep
    assert "recommendation" in rep["verdict"]
    # artifacts only under the experimental dir
    written = list((tmp_path / "art").glob("validate__*.json"))
    assert written and all(p.parent == tmp_path / "art" for p in written)


def test_guard_simulation_filters_confident_rows():
    rows = _rows(50)
    # add some non-confident rows that must NOT pass the guard
    base = datetime(2027, 1, 1, tzinfo=timezone.utc)
    for i in range(10):
        rows.append(exp.ExperimentRow(
            match_id=f"weak{i}", played_at=base, competition="International Friendlies",
            namespace="national", target="X", features={}, home_rating_present=True,
            away_rating_present=(i % 2 == 0), both_medium_plus=False,
        ))
    g = val.simulate_guard(rows, "International Friendlies")
    assert g["historical_matches"] == 60
    assert g["would_pass_guard"] == 50  # only the confident both_mp rows
    assert g["would_fallback"] == 10


def test_no_db_writes_and_no_service_coupling(tmp_path):
    from app.db import session as db_session
    from app.db.migrations import run_migrations
    from app.db.session import configure_session

    configure_session(f"sqlite:///{tmp_path / 'v.db'}")
    run_migrations(db_session.engine)
    session = db_session.SessionLocal()
    rows = val.build_experiment_rows(session)  # empty DB → no rows, but read-only
    assert rows == []
    assert not session.new and not session.dirty
    session.close()

    with open(val.__file__) as fh:
        text = fh.read()
    assert "feature_service" not in text
    assert "prediction_service" not in text
    assert "PROAI_TEAM_RATING_FEATURE_ENABLED" not in text
