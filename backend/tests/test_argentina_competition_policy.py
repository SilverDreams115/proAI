from app.services.model_training_service import ModelTrainingService


def test_argentinian_primera_loaded_history_has_explicit_policy():
    service = ModelTrainingService.__new__(ModelTrainingService)

    policy = service.competition_operating_policy("Argentinian Primera Division")

    assert policy["competition_key"] == "arg"
    assert policy["competition_readiness"] == "ready"
    assert policy["live_pick_allowed"] is True
    assert "no audited walk-forward benchmark" in policy["policy_reason"]
