import os

from app.connectors.availability_json import AvailabilityJsonConnector
from app.connectors.base import ConnectorMetadata
from app.connectors.csv_feed import CsvFeedConnector
from app.connectors.football_data_api import FootballDataApiConnector
from app.connectors.football_data_uk import FootballDataUkSeasonConnector
from app.connectors.html import GenericHtmlConnector
from app.connectors.http import UnsafeSourceUrlError
from app.connectors.http import validate_public_https_url
from app.connectors.json_feed import JsonFeedConnector
from app.connectors.local_context_json import LocalContextJsonConnector
from app.connectors.progol_catalog_html import ProgolCatalogHtmlConnector
from app.connectors.progol_guia_pdf import ProgolMsGuiaPdfConnector
from app.connectors.registry import connector_registry
from app.connectors.rss_feed import RssFeedConnector
from app.core.errors import ConflictError
from app.core.errors import ValidationError
from app.db.session import managed_transaction
from app.models.tables import SourceModel
from app.repositories.source_repository import SourceRepository
from app.schemas.provider_bootstrap import ProviderBootstrapRequest
from app.schemas.source import SourceCreate


class SourceService:
    ALLOWED_SOURCE_KINDS = {
        "html_page",
        "json_feed",
        "rss_feed",
        "csv_feed",
        "football_data_api",
        "football_data_uk_csv",
        "availability_json_feed",
        "progol_catalog_html",
        "progol_ms_guia_pdf",
        "local_context_json",
    }
    ALLOWED_PARSER_PROFILES = {"generic", "sports_feed_v1"}
    KIND_TO_ALLOWED_PARSERS = {
        "html_page": {"generic", "sports_feed_v1"},
        "json_feed": {"generic", "sports_feed_v1"},
        "rss_feed": {"generic"},
        "csv_feed": {"generic"},
        "football_data_api": {"sports_feed_v1"},
        "football_data_uk_csv": {"sports_feed_v1"},
        "availability_json_feed": {"generic"},
        "progol_catalog_html": {"generic"},
        "progol_ms_guia_pdf": {"generic"},
        "local_context_json": {"generic"},
    }
    SUPPORTED_PROVIDERS = [
        {
            "provider_id": "generic-html",
            "connector_kind": "html_page",
            "parser_profile": "generic",
            "description": "Generic HTML page scraping profile.",
        },
        {
            "provider_id": "generic-json-feed",
            "connector_kind": "json_feed",
            "parser_profile": "generic",
            "description": "Generic JSON feed ingestion profile.",
        },
        {
            "provider_id": "generic-rss-feed",
            "connector_kind": "rss_feed",
            "parser_profile": "generic",
            "description": "RSS ingestion profile for editorial sources.",
        },
        {
            "provider_id": "sports-feed-v1-json",
            "connector_kind": "json_feed",
            "parser_profile": "sports_feed_v1",
            "description": "Structured sports feed profile with fixtures and results.",
        },
        {
            "provider_id": "football-data-org-v4",
            "connector_kind": "football_data_api",
            "parser_profile": "sports_feed_v1",
            "description": "football-data.org v4 competition matches feed using X-Auth-Token.",
        },
        {
            "provider_id": "football-data-uk-season-csv",
            "connector_kind": "football_data_uk_csv",
            "parser_profile": "sports_feed_v1",
            "description": "football-data.co.uk season CSV import for historical match results.",
        },
        {
            "provider_id": "generic-csv-feed",
            "connector_kind": "csv_feed",
            "parser_profile": "generic",
            "description": "CSV ingestion profile for exported match datasets.",
        },
        {
            "provider_id": "injury-feed-json",
            "connector_kind": "availability_json_feed",
            "parser_profile": "generic",
            "description": "Structured injuries feed with availability reports per player.",
        },
        {
            "provider_id": "disciplinary-feed-json",
            "connector_kind": "availability_json_feed",
            "parser_profile": "generic",
            "description": "Structured suspensions and disciplinary absences feed.",
        },
        {
            "provider_id": "projected-lineups-json",
            "connector_kind": "availability_json_feed",
            "parser_profile": "generic",
            "description": "Structured projected lineups and rotation risk feed.",
        },
        {
            "provider_id": "tulotero-progol-catalog",
            "connector_kind": "progol_catalog_html",
            "parser_profile": "generic",
            "description": "TuLotero Progol product page with contest options and links to the weekly quiniela.",
        },
        {
            "provider_id": "tulotero-progol-media-semana-catalog",
            "connector_kind": "progol_catalog_html",
            "parser_profile": "generic",
            "description": "TuLotero Progol Media Semana product page with contest options and sale window.",
        },
        {
            "provider_id": "ln-progol-ms-guide",
            "connector_kind": "progol_ms_guia_pdf",
            "parser_profile": "generic",
            "description": (
                "LN Progol Media Semana guide PDF — official 9-fixture MS contest source. "
                "URL: https://www.loterianacional.gob.mx/ProgolMediaSemana/Quiniela"
            ),
        },
        {
            "provider_id": "official-progol-quiniela-page",
            "connector_kind": "progol_catalog_html",
            "parser_profile": "generic",
            "description": "Official Lotería Nacional quiniela page for Progol schedule discovery.",
        },
        {
            "provider_id": "local-context-json",
            "connector_kind": "local_context_json",
            "parser_profile": "generic",
            "description": "Local structured context pack for injuries, suspensions, lineups, and recent form.",
        },
    ]

    def __init__(self, repository: SourceRepository) -> None:
        self.repository = repository

    def list_sources(self) -> list[SourceModel]:
        return self.repository.list_sources()

    def create_source(self, payload: SourceCreate) -> SourceModel:
        self._validate_source_payload(payload)
        if self.repository.get_by_name(payload.name) is not None:
            raise ConflictError(f"Source '{payload.name}' already exists.")
        with managed_transaction(self.repository.session):
            source = self.repository.create_source(payload)
            self._register_connector(source, payload.kind)
        return source

    def list_registered_connectors(self) -> list[ConnectorMetadata]:
        return connector_registry.list_metadata()

    def list_supported_providers(self) -> list[dict[str, str]]:
        return self.SUPPORTED_PROVIDERS

    def create_source_from_provider(self, payload: ProviderBootstrapRequest) -> SourceModel:
        if payload.provider_id == "football-data-org-v4":
            with managed_transaction(self.repository.session):
                source = self._create_provider_source(
                    payload=payload,
                    base_url="https://api.football-data.org",
                    kind="football_data_api",
                    parser_profile="sports_feed_v1",
                )
                api_key = os.getenv("PROAI_FOOTBALL_DATA_API_KEY", "")
                connector_registry.register(
                    FootballDataApiConnector(
                        name=source.name,
                        base_url=source.base_url,
                        api_key=api_key,
                        competition_code=payload.competition_code or "PL",
                        date_from=payload.date_from,
                        date_to=payload.date_to,
                    )
                )
            return source

        if payload.provider_id == "football-data-uk-season-csv":
            with managed_transaction(self.repository.session):
                source = self._create_provider_source(
                    payload=payload,
                    base_url="https://www.football-data.co.uk",
                    kind="football_data_uk_csv",
                    parser_profile="sports_feed_v1",
                )
                connector_registry.register(
                    FootballDataUkSeasonConnector(
                        name=source.name,
                        base_url=source.base_url,
                        season_path=payload.season_path or "mmz4281/2425/E0.csv",
                    )
                )
            return source

        if payload.provider_id in {
            "injury-feed-json",
            "disciplinary-feed-json",
            "projected-lineups-json",
        }:
            if not payload.feed_url:
                raise ValidationError("feed_url is required for structured availability providers.")
            with managed_transaction(self.repository.session):
                source = self._create_provider_source(
                    payload=payload,
                    base_url=str(payload.feed_url),
                    kind="availability_json_feed",
                    parser_profile="generic",
                )
                feed_type = {
                    "injury-feed-json": "injury",
                    "disciplinary-feed-json": "suspension",
                    "projected-lineups-json": "rotation",
                }[payload.provider_id]
                connector_registry.register(
                    AvailabilityJsonConnector(
                        name=source.name,
                        base_url=source.base_url,
                        feed_type=feed_type,
                    )
                )
            return source

        if payload.provider_id in {
            "tulotero-progol-catalog",
            "tulotero-progol-media-semana-catalog",
            "official-progol-quiniela-page",
        }:
            default_url = {
                "tulotero-progol-catalog": "https://tulotero.mx/progol/",
                "tulotero-progol-media-semana-catalog": "https://tulotero.mx/progol-media-semana/",
                "official-progol-quiniela-page": "https://www.loterianacional.gob.mx/Progol/Quiniela",
            }[payload.provider_id]
            with managed_transaction(self.repository.session):
                source = self._create_provider_source(
                    payload=payload,
                    base_url=str(payload.feed_url or default_url),
                    kind="progol_catalog_html",
                    parser_profile="generic",
                )
                contest_type = {
                    "tulotero-progol-catalog": "progol",
                    "tulotero-progol-media-semana-catalog": "progol_media_semana",
                    "official-progol-quiniela-page": "official_quiniela",
                }[payload.provider_id]
                connector_registry.register(
                    ProgolCatalogHtmlConnector(
                        name=source.name,
                        base_url=source.base_url,
                        contest_type=contest_type,
                    )
                )
            return source

        if payload.provider_id == "ln-progol-ms-guide":
            ms_url = str(payload.feed_url or ProgolMsGuiaPdfConnector.DEFAULT_LANDING_URL)
            with managed_transaction(self.repository.session):
                source = self._create_provider_source(
                    payload=payload,
                    base_url=ms_url,
                    kind="progol_ms_guia_pdf",
                    parser_profile="generic",
                )
                connector_registry.register(
                    ProgolMsGuiaPdfConnector(name=source.name, base_url=ms_url)
                )
            return source

        if payload.provider_id == "local-context-json":
            local_path = (
                payload.local_path
                or os.getenv("PROAI_LOCAL_CONTEXT_PATH")
                or "/data/progol_context/current.json"
            )
            resolved_path = LocalContextJsonConnector.resolve_allowed_path(local_path)
            if self.repository.get_by_name(payload.source_name) is not None:
                raise ConflictError(f"Source '{payload.source_name}' already exists.")
            with managed_transaction(self.repository.session):
                source = SourceModel(
                    name=payload.source_name,
                    base_url=LocalContextJsonConnector.to_base_url(str(resolved_path)),
                    kind="local_context_json",
                    parser_profile="generic",
                    is_active=True,
                )
                self.repository.session.add(source)
                self.repository.session.flush()
                self.repository.session.refresh(source)
                connector_registry.register(
                    LocalContextJsonConnector(
                        name=source.name,
                        file_path=source.base_url,
                    )
                )
            return source

        raise ValidationError("Unsupported provider bootstrap request.")

    def _create_provider_source(
        self,
        *,
        payload: ProviderBootstrapRequest,
        base_url: str,
        kind: str,
        parser_profile: str,
    ) -> SourceModel:
        if self.repository.get_by_name(payload.source_name) is not None:
            raise ConflictError(f"Source '{payload.source_name}' already exists.")
        source_payload = SourceCreate.model_validate(
            {
                "name": payload.source_name,
                "base_url": base_url,
                "kind": kind,
                "parser_profile": parser_profile,
                "is_active": True,
            }
        )
        self._validate_source_payload(source_payload)
        return self.repository.create_source(source_payload)

    def _validate_source_payload(self, payload: SourceCreate) -> None:
        if payload.kind not in self.ALLOWED_SOURCE_KINDS:
            raise ValidationError(f"Unsupported source kind '{payload.kind}'.")
        if payload.parser_profile not in self.ALLOWED_PARSER_PROFILES:
            raise ValidationError(f"Unsupported parser profile '{payload.parser_profile}'.")
        allowed_parsers = self.KIND_TO_ALLOWED_PARSERS.get(payload.kind, set())
        if payload.parser_profile not in allowed_parsers:
            raise ValidationError(
                f"Parser profile '{payload.parser_profile}' is not supported for kind '{payload.kind}'."
            )
        self._validate_remote_url(str(payload.base_url))

    def _validate_remote_url(self, raw_url: str) -> None:
        try:
            validate_public_https_url(raw_url)
        except UnsafeSourceUrlError as exc:
            raise ValidationError(str(exc)) from exc

    def _register_connector(self, source: SourceModel, kind: str) -> None:
        if kind == "html_page" and connector_registry.get(source.name) is None:
            connector_registry.register(GenericHtmlConnector(name=source.name, base_url=source.base_url))
        if kind == "json_feed" and connector_registry.get(source.name) is None:
            connector_registry.register(JsonFeedConnector(name=source.name, base_url=source.base_url))
        if kind == "rss_feed" and connector_registry.get(source.name) is None:
            connector_registry.register(RssFeedConnector(name=source.name, base_url=source.base_url))
        if kind == "csv_feed" and connector_registry.get(source.name) is None:
            connector_registry.register(CsvFeedConnector(name=source.name, base_url=source.base_url))
        if kind == "availability_json_feed" and connector_registry.get(source.name) is None:
            connector_registry.register(AvailabilityJsonConnector(name=source.name, base_url=source.base_url))
        if kind == "progol_catalog_html" and connector_registry.get(source.name) is None:
            connector_registry.register(
                ProgolCatalogHtmlConnector(name=source.name, base_url=source.base_url)
            )
        if kind == "progol_ms_guia_pdf" and connector_registry.get(source.name) is None:
            connector_registry.register(
                ProgolMsGuiaPdfConnector(name=source.name, base_url=source.base_url)
            )
        if kind == "local_context_json" and connector_registry.get(source.name) is None:
            connector_registry.register(
                LocalContextJsonConnector(name=source.name, file_path=source.base_url)
            )
