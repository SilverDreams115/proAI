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


def test_womens_teams_do_not_collide_with_the_mens_club() -> None:
    """"Femenil"/"Femenino" must survive normalization. While they were
    stopwords, "Cruz Azul Femenil" and "Cruz Azul" both became `cruz-azul`,
    so a Liga MX Femenil fixture resolved to the men's team and its document
    linked to the men's match with the orientation flipped."""
    from app.services.normalization_service import NormalizationService

    service = NormalizationService()

    for womens, mens in (
        ("Cruz Azul Femenil", "Cruz Azul"),
        ("Pumas UNAM Femenil", "Pumas"),
        ("CF América Femenil", "America"),
        ("Barcelona Femenino", "Barcelona"),
    ):
        womens_slug = service.normalize_team_name(womens)
        mens_slug = service.normalize_team_name(mens)
        assert womens_slug != mens_slug, f"{womens} collided with {mens}"
        assert womens_slug.endswith(("femenil", "femenino")), womens_slug


def test_other_team_stopwords_still_apply() -> None:
    """Keeping the gender marker must not turn off the rest of the
    stopword list — those still collapse legitimate spelling variants."""
    from app.services.normalization_service import NormalizationService

    service = NormalizationService()

    assert service.normalize_team_name("FC Barcelona") == service.normalize_team_name("Barcelona")
    assert service.normalize_team_name("Club Tijuana") == service.normalize_team_name("Tijuana")
