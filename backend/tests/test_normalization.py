from app.services.normalization_service import NormalizationService


def test_normalization_applies_project_aliases() -> None:
    service = NormalizationService()

    assert service.normalize_team_name("Inter P.A.") == "internacional-porto-alegre"
    assert service.normalize_team_name("R. Sociedad") == "real-sociedad"
    assert service.normalize_team_name("C. Azul") == "cruz-azul"
    assert service.normalize_competition_name("LaLiga") == "la-liga"
    assert service.normalize_competition_name("Serie A Brasil") == "serie-a-brazil"
    assert service.normalize_competition_name("Copa de Rusia") == "russian-cup"
