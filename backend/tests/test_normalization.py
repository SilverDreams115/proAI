from app.services.normalization_service import NormalizationService


def test_normalization_applies_project_aliases() -> None:
    service = NormalizationService()

    assert service.normalize_team_name("Inter P.A.") == "internacional-porto-alegre"
    assert service.normalize_team_name("R. Sociedad") == "real-sociedad"
    assert service.normalize_team_name("C. Azul") == "cruz-azul"
    assert service.normalize_team_name("S. Laguna") == "santos-laguna"
    assert service.normalize_team_name("Vasco Da Ga") == "vasco-da-gama"
    assert service.normalize_team_name("G Argentina") == "argentina"
    assert service.normalize_team_name("Águilas") == "america"
    assert service.normalize_team_name("Chicago") == "chicago-fire"
    assert service.normalize_team_name("Vancouver") == "vancouver-whitecaps"
    assert service.normalize_team_name("Vitoria BA") == "vitoria"
    assert service.normalize_team_name("St. Louis") == "st-louis-city"
    assert service.normalize_team_name("Kansas City") == "sporting-kansas-city"
    assert service.normalize_team_name("Aucas") == "sd-aucas"
    assert service.normalize_team_name("Sport Recife") == "sport-do-recife"
    assert service.normalize_team_name("Operario") == "operario-ferroviario"
    assert service.normalize_team_name("Sarpsborg") == "sarpsborg-08"
    assert service.normalize_team_name("Kristiansund BK") == "kristiansund"
    assert service.normalize_team_name("Kalmar") == "kalmar-ff"
    assert service.normalize_team_name("Malmö") == "malmo-ff"
    assert service.normalize_competition_name("LaLiga") == "la-liga"
    assert service.normalize_competition_name("Serie A Brasil") == "serie-a-brazil"
    assert service.normalize_competition_name("Brazilian Serie B") == "serie-b-brazil"
    assert service.normalize_competition_name("Club Friendlies") == "club-friendlies"
    assert service.normalize_competition_name("Eliteserien") == "norwegian-eliteserien"
    assert service.normalize_competition_name("Liga AUF Uruguaya") == "uruguayan-primera-division"
    assert service.normalize_competition_name("LigaPro Serie A") == "ecuador-serie-a"
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
