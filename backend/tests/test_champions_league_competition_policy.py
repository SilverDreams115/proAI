from app.services.model_training_service import ModelTrainingService


def _policy(name: str) -> dict:
    service = ModelTrainingService.__new__(ModelTrainingService)
    return service.competition_operating_policy(name)


def test_champions_league_is_no_longer_unclassified():
    """`unclassified` is the one readiness that forces confidence_band to
    "blocked" outright, whatever the data says. The Champions League sat there
    because no benchmark existed for it; the walk-forward published on
    2026-07-31 evaluated 365 fixtures, so that reason no longer holds."""
    policy = _policy("UEFA Champions League")

    assert policy["competition_key"] == "uefa-champions-league"
    assert policy["competition_readiness"] != "unclassified"
    assert policy["competition_readiness"] == "covered"


def test_champions_league_stays_short_of_live_pick_approval():
    """Coverage is audited, but no graded Progol history backs a live pick.
    `covered` is the same bucket Ligue 1 and MLS sit in — both with worse
    published Brier than this competition — so claiming `ready` here would be
    inflating a band the evidence does not support."""
    policy = _policy("UEFA Champions League")

    assert policy["live_pick_allowed"] is False
    assert "brier" in policy["policy_reason"].lower()


def test_womens_champions_league_keeps_its_own_policy():
    """The women's competition is a separate key with no benchmark of its own;
    the men's override must not leak onto it."""
    womens = _policy("UEFA Champions League Femenina")

    assert womens["competition_key"] != "uefa-champions-league"
    assert womens["competition_readiness"] == "unclassified"


def test_an_unbenchmarked_competition_is_still_unclassified():
    """The guard the override relaxes must stay in place for everything else."""
    policy = _policy("Some Competition We Never Ingested")

    assert policy["competition_readiness"] == "unclassified"
    assert policy["live_pick_allowed"] is False
