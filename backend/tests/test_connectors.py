from app.connectors.football_data_uk import FootballDataUkSeasonConnector
from app.connectors.html import GenericHtmlConnector
from app.connectors.local_context_json import LocalContextJsonConnector
from app.connectors.progol_catalog_html import ProgolCatalogHtmlConnector
from io import BytesIO
import json
from urllib.request import addinfourl


def test_football_data_uk_connector_normalizes_slash_dates_to_iso() -> None:
    connector = FootballDataUkSeasonConnector(
        name="Historical Season Source",
        base_url="https://www.football-data.co.uk",
        season_path="mmz4281/2425/E0.csv",
    )

    played_at = connector._normalize_played_at("16/08/2024")

    assert played_at == "2024-08-16T00:00:00+00:00"


def test_football_data_uk_connector_infers_competition_code_from_season_path() -> None:
    connector = FootballDataUkSeasonConnector(
        name="Historical LaLiga Source",
        base_url="https://www.football-data.co.uk",
        season_path="mmz4281/2425/SP1.csv",
    )

    assert connector._competition_code == "SP1"


def test_football_data_uk_connector_supports_new_world_league_schema(monkeypatch) -> None:
    connector = FootballDataUkSeasonConnector(
        name="Historical Liga MX Source",
        base_url="https://www.football-data.co.uk",
        season_path="new/MEX.csv?season=2025/2026",
    )

    csv_payload = (
        "Country,League,Season,Date,Time,Home,Away,HG,AG,Res\n"
        "Mexico,Liga MX,2025/2026,16/05/2026,20:00,Pachuca,Pumas,1,0,H\n"
    ).encode("utf-8")

    def fake_urlopen(request, timeout=20):
        return addinfourl(BytesIO(csv_payload), headers={}, url=request.full_url)

    monkeypatch.setattr("app.connectors.football_data_uk.urlopen", fake_urlopen)

    documents = connector.fetch()

    assert connector._competition_code == "MEX"
    assert documents[0].payload["fixtures"][0]["competition"] == "Liga MX"
    assert documents[0].payload["fixtures"][0]["home_team"] == "Pachuca"
    assert connector._allowed_seasons == {"2025/2026"}


def test_progol_catalog_connector_extracts_current_media_draw_number() -> None:
    connector = ProgolCatalogHtmlConnector(
        name="Progol Media",
        base_url="https://example.com/progol-media-semana",
        contest_type="progol_media_semana",
    )

    draw_number = connector._extract_draw_number(
        "Programa / Resultados Progol 1/2 Semana 796",
        [("796", "/resultados_media_796.html"), ("795", "/resultados_media_795.html")],
    )

    assert draw_number == 796


def test_generic_html_connector_extracts_current_progol_media_fixture_section(monkeypatch) -> None:
    connector = GenericHtmlConnector(
        name="Resultados Ya Media Semana",
        base_url="https://www.resultados-ya.com/w/progol-media-semana.php",
    )
    html_payload = """
    <html><title>Resultados Progol Media Semana</title><body>
      <p>Próximos partidos del 13 al 14 de Mayo del 2026 || Quiniela No 795</p>
      <p>FECHA LOCAL . VISITANTE</p>
      <p>1 14 Mayo Pachuca 1 - 0 Pumas FINAL !!</p>
      <p>Horarios del centro de México</p>
      <p>Próximos partidos del 19 al 21 de Mayo del 2026 || Quiniela No 796</p>
      <p>FECHA LOCAL . VISITANTE</p>
      <p>1 Mayo Cruz Azul VS Pumas --</p>
      <p>2 20 Mayo Friburgo VS Aston Villa 1:00 pm</p>
      <p>3 20 Mayo Aguilas F VS Gotham FC F 5:30 pm</p>
      <p>Horarios del centro de México</p>
    </body></html>
    """.encode()

    def fake_urlopen(request, timeout=15):
        return addinfourl(BytesIO(html_payload), headers={}, url=request.full_url)

    monkeypatch.setattr("app.connectors.html.urlopen", fake_urlopen)

    documents = connector.fetch()
    payload = documents[0].payload

    assert payload["catalog_metadata"]["draw_number"] == 796
    assert payload["catalog_metadata"]["match_count"] == 3
    assert payload["fixture_candidates"][0]["home_team"] == "Cruz Azul"
    assert payload["fixture_candidates"][0]["away_team"] == "Pumas"
    assert payload["fixture_candidates"][1]["kickoff_at"] == "2026-05-20T19:00:00+00:00"


def test_generic_html_connector_splits_progol_analysis_into_match_context(monkeypatch) -> None:
    connector = GenericHtmlConnector(
        name="Reporte Indigo Progol 796",
        base_url="https://example.com/progol-796-context",
    )
    html_payload = """
    <html><title>Predicciones Progol 796 ½ semana</title><body>
      <h1>Guía Progol Media Semana 796</h1>
      <h3>Cruz Azul vs. Pumas</h3>
      <p>Final de Ida de la Liga MX.</p>
      <p>Último resultado: Pumas 2-2 Cruz Azul.</p>
      <p>Predicción: Empate.</p>
      <h3>Friburgo vs. Aston Villa</h3>
      <p>Final de la Europa League.</p>
      <p>Aston Villa llega con mejor regularidad.</p>
      <p>Predicción: Visitante.</p>
    </body></html>
    """.encode()

    def fake_urlopen(request, timeout=15):
        return addinfourl(BytesIO(html_payload), headers={}, url=request.full_url)

    monkeypatch.setattr("app.connectors.html.urlopen", fake_urlopen)

    documents = connector.fetch()

    assert len(documents) == 2
    assert documents[0].payload["title"] == "Cruz Azul vs. Pumas"
    assert documents[0].payload["teams"] == ["Cruz Azul", "Pumas"]
    assert documents[0].payload["article_prediction"] == "E"
    assert "Último resultado" in documents[0].payload["context_summary"]
    assert documents[1].payload["article_prediction"] == "V"


def test_generic_html_connector_extracts_resultados_ya_recent_results(monkeypatch) -> None:
    connector = GenericHtmlConnector(
        name="Resultados Ya Home",
        base_url="https://www.resultados-ya.com/",
    )
    html_payload = """
    <html><title>Resultados-ya.com</title><body>
      <h6>Partidos Recientes :</h6>
      <p>Fecha Local VS Visitante</p>
      <p>1 dom - 17 mayo Pumas UNAM 1-0 Pachuca Final</p>
      <p>2 sáb - 16 mayo Chivas Guadalajara 1-2 Cruz Azul Final</p>
      <h6>Proximos partidos :</h6>
    </body></html>
    """.encode()

    def fake_urlopen(request, timeout=15):
        return addinfourl(BytesIO(html_payload), headers={}, url=request.full_url)

    monkeypatch.setattr("app.connectors.html.urlopen", fake_urlopen)

    documents = connector.fetch()
    results = documents[0].payload["historical_results"]

    assert len(results) == 2
    assert results[0]["home_team"] == "Pumas UNAM"
    assert results[0]["away_team"] == "Pachuca"
    assert results[0]["home_goals"] == 1
    assert results[1]["away_team"] == "Cruz Azul"


def test_local_context_json_connector_reads_verified_context_pack(tmp_path) -> None:
    context_path = tmp_path / "current.json"
    context_path.write_text(
        json.dumps(
            {
                "items": [
                    {
                        "title": "Cruz Azul vs Pumas context",
                        "source_url": "https://www.resultados-ya.com/",
                        "competition": "Progol Media Semana",
                        "teams": ["Cruz Azul", "Pumas"],
                        "context_summary": "Verified recent-form context.",
                        "historical_results": [
                            {
                                "competition_name": "Resultados Ya Recent Form",
                                "home_team": "Pumas UNAM",
                                "away_team": "Pachuca",
                                "played_at": "2026-05-17T18:00:00Z",
                                "home_goals": 1,
                                "away_goals": 0,
                            }
                        ],
                        "availability_reports": [
                            {
                                "team_name": "Pumas",
                                "player_name": "Sample Player",
                                "status": "suspended",
                                "category": "suspension",
                                "detail": "Verified suspension.",
                                "confidence": 0.9,
                                "impact_score": 0.6,
                            }
                        ],
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    connector = LocalContextJsonConnector(
        name="Local Context",
        file_path=str(context_path),
        allowed_root=str(tmp_path),
    )

    documents = connector.fetch()

    assert len(documents) == 1
    assert documents[0].source_url == "https://www.resultados-ya.com/"
    assert documents[0].payload["teams"] == ["Cruz Azul", "Pumas"]
    assert documents[0].payload["context_summary"] == "Verified recent-form context."
    assert documents[0].payload["availability_reports"][0]["status"] == "suspended"
