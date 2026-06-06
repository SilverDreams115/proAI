"""The training artifact must record the XGBoost params + feature
schema version so an old prediction can be replayed even if the
runtime defaults later drift.
"""

from __future__ import annotations


def test_xgboost_artifact_records_hyperparams_and_schema_version(tmp_path) -> None:
    import xgboost as xgb

    from app.db.session import configure_session
    from app.db import session as db_session
    from app.db.migrations import run_migrations
    from app.repositories.entity_repository import EntityRepository
    from app.repositories.result_repository import ResultRepository
    from app.repositories.training_repository import TrainingRepository
    from app.services.model_training_service import ModelTrainingService

    configure_session(f"sqlite:///{tmp_path}/artifact.db")
    run_migrations(db_session.engine)
    session = db_session.SessionLocal()
    training = ModelTrainingService(
        TrainingRepository(session),
        EntityRepository(session),
        ResultRepository(session),
    )
    # Minimal synthetic dataset that exceeds XGBOOST_MIN_SAMPLE_SIZE.
    n_samples = max(40, training.XGBOOST_MIN_SAMPLE_SIZE)
    rows = [[0.0] * len(training.FEATURE_NAMES) for _ in range(n_samples)]
    # Vary labels so all three classes are present (avoids xgboost
    # complaining about a single-class dataset).
    labels = [(i % 3) for i in range(n_samples)]
    dataset = {
        "sample_size": n_samples,
        "classes_seen": [0, 1, 2],
        "rows": rows,
        "labels": labels,
        "played_at": [],
    }

    # Patch _class_priors to a stable scalar dict so we don't depend on
    # the real distribution.
    training._class_priors = lambda ds: {"1": 0.34, "X": 0.33, "2": 0.33}  # type: ignore[assignment]

    artifact = training._train_xgboost_artifact(dataset)
    assert artifact is not None, "Training should produce a booster on a sufficient sample."

    # Hyperparameters fully persisted.
    params = artifact["xgboost_params"]
    assert params["objective"] == "multi:softprob"
    assert params["num_class"] == 3
    assert params["seed"] == 42
    assert artifact["xgboost_num_boost_round"] == 160

    # Schema + decay constants come from the mixin defaults so an
    # operator can verify the booster was trained against the same
    # feature shape it's being scored on.
    assert artifact["feature_schema_version"] == training.FEATURE_SCHEMA_VERSION
    assert artifact["time_decay_half_life_days"] == training.TIME_DECAY_HALF_LIFE_DAYS

    # Booster JSON is still there for runtime use.
    assert "_booster_json_transient" in artifact
    booster_json = artifact["_booster_json_transient"]
    # Round-trip through xgboost to confirm the JSON is loadable.
    booster = xgb.Booster()
    booster.load_model(bytearray(booster_json, "utf-8"))
