from app.services.normalization_service import NormalizationService


def test_normalization_applies_project_aliases() -> None:
    service = NormalizationService()

    assert service.normalize_team_name("Inter P.A.") == "internacional-porto-alegre"
    assert service.normalize_team_name("R. Sociedad") == "real-sociedad"
    assert service.normalize_team_name("C. Azul") == "cruz-azul"
    assert service.normalize_competition_name("LaLiga") == "la-liga"
    assert service.normalize_competition_name("Serie A Brasil") == "serie-a-brazil"
    assert service.normalize_competition_name("Copa de Rusia") == "russian-cup"


def test_wcq_competition_aliases_map_to_international_friendlies() -> None:
    service = NormalizationService()

    wcq_variants = [
        "World Cup Qualifying UEFA",
        "World Cup Qualifying CONMEBOL",
        "World Cup Qualifying CAF",
        "World Cup Qualifying AFC",
        "World Cup Qualifying CONCACAF",
        "World Cup Qualifying OFC",
        "World Cup Qualifying",
        "Eliminatorias Mundialistas",
        "Clasificatorio Mundial",
        "Eliminatorias Sudamericanas",
        "Eliminatorias CONMEBOL",
        "WCQ",
    ]
    for variant in wcq_variants:
        assert (
            service.normalize_competition_name(variant) == "international-friendlies"
        ), f"Expected 'international-friendlies' for {variant!r}"
