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


def test_serie_a_short_forms_land_on_the_canonical_rows() -> None:
    """football-data.co.uk writes Serie A clubs short; the rows already in
    the database came from the UCL feed under their long names. Both forms
    have to reach the same slug or the CSV ingest splits every club's form
    across two rows."""
    service = NormalizationService()

    assert service.normalize_team_name("Inter") == service.normalize_team_name(
        "FC Internazionale Milano"
    )
    assert service.normalize_team_name("Atalanta") == service.normalize_team_name("Atalanta BC")
    # These two need no pin — `fc` and `ac` are team stopwords — but the
    # ingest depends on it, so assert the behaviour rather than trust it.
    assert service.normalize_team_name("Juventus") == service.normalize_team_name("Juventus FC")
    assert service.normalize_team_name("Milan") == service.normalize_team_name("AC Milan")


def test_inter_pin_does_not_swallow_the_other_inters() -> None:
    """Bare "Inter" is Internazionale, but the Brazilian and MLS clubs that
    start with the same token must keep their own slugs."""
    service = NormalizationService()

    internazionale = service.normalize_team_name("Inter")
    for other in ("Internacional", "Inter P.A.", "Internacional Porto Alegre", "Inter Miami"):
        assert service.normalize_team_name(other) != internazionale, other


def test_argentine_short_forms_land_on_the_canonical_rows() -> None:
    """football-data.co.uk's ARG.csv abbreviates Argentine clubs. Each short
    form has to reach the row the TheSportsDB ingests already built, or the
    CSV mints a parallel row and splits the club's form in two."""
    service = NormalizationService()

    for short, canonical in (
        ("Dep. Riestra", "Deportivo Riestra"),
        ("Estudiantes L.P.", "Estudiantes de La Plata"),
        ("Gimnasia L.P.", "Gimnasia y Esgrima de La Plata"),
        ("Gimnasia Mendoza", "Gimnasia y Esgrima de Mendoza"),
        ("Ind. Rivadavia", "CS Independiente Rivadavia"),
        ("San Martin S.J.", "San Martín de San Juan"),
        ("Sarmiento Junin", "Sarmiento"),
        ("Union de Santa Fe", "Union"),
    ):
        assert service.normalize_team_name(short) == service.normalize_team_name(
            canonical
        ), f"{short} does not reach {canonical}"


def test_argentine_pins_keep_distinct_clubs_apart() -> None:
    """The two Gimnasias are different clubs, and so are the Unións. A pin
    that collapsed either pair would merge unrelated histories."""
    service = NormalizationService()

    assert service.normalize_team_name("Gimnasia L.P.") != service.normalize_team_name(
        "Gimnasia Mendoza"
    )
    union_santa_fe = service.normalize_team_name("Union de Santa Fe")
    for other in ("Unión La Calera", "Unión Española"):
        assert service.normalize_team_name(other) != union_santa_fe, other
    # Estudiantes de Río Cuarto is not Estudiantes de La Plata.
    assert service.normalize_team_name("Estudiantes Rio Cuarto") != service.normalize_team_name(
        "Estudiantes L.P."
    )


def test_argentine_league_labels_share_one_competition_slug() -> None:
    """ARG.csv calls the Argentine top flight "Liga Profesional"; the TSDB
    ingests built it as "Argentinian Primera Division". A second competition
    row would halve the median-gap window, the competition profile and the
    walk-forward verdict key."""
    service = NormalizationService()

    canonical = service.normalize_competition_name("Argentinian Primera Division")
    assert service.normalize_competition_name("Liga Profesional") == canonical
    # The CSV emits a trailing-space variant on some rows.
    assert service.normalize_competition_name("Liga Profesional ") == canonical
    assert service.normalize_competition_name("Argentina Primera Division") == canonical


def test_the_argentine_league_cup_stays_its_own_competition() -> None:
    """Copa de la Liga Profesional is a different tournament and must not be
    folded into the league by the pin above."""
    service = NormalizationService()

    assert service.normalize_competition_name(
        "Copa De La Liga Profesional"
    ) != service.normalize_competition_name("Argentinian Primera Division")


def test_manchester_city_spellings_share_one_slug() -> None:
    """The E0 feed writes "Man City", the Champions League feed writes
    "Manchester City FC". Without a pin the long form lands on
    "manchester-city" and never meets the row holding the club's league
    history — the split v35 folds."""
    service = NormalizationService()

    canonical = service.normalize_team_name("Man City")
    assert service.normalize_team_name("Manchester City FC") == canonical
    assert service.normalize_team_name("Manchester City") == canonical


def test_manchester_city_pin_does_not_swallow_the_womens_side() -> None:
    """Gender markers are deliberately not stopwords; a women's Manchester
    City must keep its own slug even though the pin targets the men's row."""
    service = NormalizationService()

    mens = service.normalize_team_name("Man City")
    for womens in ("Manchester City Femenil", "Manchester City Femenino"):
        assert service.normalize_team_name(womens) != mens, womens
    # And a different Manchester club is untouched.
    assert service.normalize_team_name("Man United") != mens
