from datetime import datetime, timezone
import logging
import time
from time import perf_counter
from typing import ClassVar

from app.connectors.availability_json import AvailabilityJsonConnector
from app.connectors.base import SourceConnector
from app.connectors.base import SourceDocument
from app.connectors.football_data_api import FootballDataApiConnector
from app.connectors.football_data_uk import FootballDataUkSeasonConnector
from app.connectors.html import GenericHtmlConnector
from app.connectors.json_feed import JsonFeedConnector
from app.connectors.local_context_json import LocalContextJsonConnector
from app.connectors.progol_catalog_html import ProgolCatalogHtmlConnector
from app.connectors.registry import connector_registry
from app.connectors.thesportsdb import TheSportsDbSeasonConnector
from app.core.errors import NotFoundError
from app.core.errors import ValidationError
from app.core.metrics import metrics_store
from app.db.session import managed_transaction
from app.models.tables import IngestionRunModel
from app.models.tables import MatchModel
from app.models.tables import MatchResultModel
from app.models.tables import MatchStatSnapshotModel
from app.models.tables import SourceDocumentModel
from app.models.tables import TeamStatSnapshotModel
from app.parsers.registry import parser_registry
from app.repositories.availability_repository import AvailabilityRepository
from app.repositories.entity_repository import EntityRepository
from app.repositories.evidence_repository import EvidenceRepository
from app.repositories.ingestion_repository import IngestionRepository
from app.repositories.result_repository import ResultRepository
from app.repositories.slate_repository import SlateRepository
from app.repositories.stats_repository import StatsRepository
from app.schemas.slate_discovery import SlateDiscoveryRequest
from app.services.entity_resolution_service import EntityResolutionService
from app.services.evidence_service import EvidenceService
from app.services.narrative_interpretation_service import NarrativeInterpretationService
from app.services.normalization_service import NormalizationService
from app.services.result_service import ResultService
from app.services.slate_discovery_service import SlateDiscoveryService
from app.services.stats_service import StatsService

logger = logging.getLogger(__name__)


class IngestionService:
    MAX_FETCH_ATTEMPTS = 3
    RETRYABLE_FETCH_EXCEPTIONS = (OSError, TimeoutError)
    # Default tolerance window used by _find_nearby_match_for_result when
    # the competition's actual cadence is unknown. For competitions where
    # we do have a result history the tolerance scales from the median
    # gap between matches (clamped to [RESULT_MATCH_TOLERANCE_FLOOR_DAYS,
    # RESULT_MATCH_TOLERANCE_CEILING_DAYS]) — friendly tours need a wider
    # window than knockouts where reschedules are rare.
    RESULT_MATCH_DATE_TOLERANCE_DAYS = 14
    RESULT_MATCH_TOLERANCE_FLOOR_DAYS = 5
    RESULT_MATCH_TOLERANCE_CEILING_DAYS = 30
    # Cache so per-result lookups don't re-query the same competition's
    # median gap. Class-level so it survives across the per-document loop
    # inside a single ingest run and warms between runs in the same
    # worker process.
    _competition_tolerance_cache: ClassVar[dict[str, float]] = {}

    def __init__(
        self,
        repository: IngestionRepository,
        normalization_service: NormalizationService | None = None,
    ) -> None:
        self.repository = repository
        self.normalization_service = normalization_service or NormalizationService()

    def list_runs(self) -> list[IngestionRunModel]:
        return self.repository.list_runs()

    def run_for_source(self, source_id: str) -> IngestionRunModel:
        source = self.repository.get_source(source_id)
        if source is None:
            raise NotFoundError("Source not found.")

        started = perf_counter()
        with managed_transaction(self.repository.session):
            run = self.repository.create_run(source_id)
        try:
            connector = self.get_connector_for_source(source)
            parser = parser_registry.get(source.parser_profile)
            documents = [
                self._normalize_document(document, parser.parse(document.payload))
                for document in self._fetch_documents(connector)
            ]
            with managed_transaction(self.repository.session):
                completed_run = self.repository.mark_run_success(run, documents)
                entity_repository = EntityRepository(self.repository.session)
                self._auto_discover_slates(completed_run.source_id)
                matches = entity_repository.list_matches()
                linked_items = EvidenceService(
                    EvidenceRepository(self.repository.session),
                    self.normalization_service,
                ).auto_link_unmatched_documents(matches)
                self._persist_player_availability(completed_run.source_id, linked_items)
                self._persist_structured_stats(completed_run.source_id, documents, matches)
                self._persist_historical_results(completed_run.source_id, documents)
                metrics_store.record_ingestion_run(
                    source_name=source.name,
                    status=completed_run.status,
                    duration_ms=(perf_counter() - started) * 1000,
                )
                return completed_run
        except Exception as exc:
            # The run row gets marked failed below, but without a log
            # entry the traceback is gone — operators only see "status:
            # failed" and have to repro the crash to debug. Emit the
            # exception so the error class + stack reaches the JSON
            # log stream alongside source_id.
            logger.exception(
                "ingestion run failed",
                extra={
                    "event": "ingestion_run_failed",
                    "source_id": source_id,
                    "source_name": source.name,
                    "error_type": type(exc).__name__,
                },
            )
            self.repository.session.rollback()
            with managed_transaction(self.repository.session):
                failed_run = self.repository.mark_run_failure(run, str(exc))
            metrics_store.record_ingestion_run(
                source_name=source.name,
                status=failed_run.status,
                duration_ms=(perf_counter() - started) * 1000,
            )
            return failed_run

    def run_for_source_documents_only(self, source_id: str) -> IngestionRunModel:
        """Fetch, parse and persist source documents without global side effects.

        Current Progol context refresh already knows the exact slate it is
        updating. Running the full ingestion pipeline for that local JSON source
        needlessly scans every match for discovery/evidence/stats/results and
        can make operator refreshes feel hung. This path keeps the ingestion run
        audit trail and stored source documents, then lets the caller perform
        the scoped slate work.
        """
        source = self.repository.get_source(source_id)
        if source is None:
            raise NotFoundError("Source not found.")

        started = perf_counter()
        with managed_transaction(self.repository.session):
            run = self.repository.create_run(source_id)
        try:
            documents = self._fetch_parsed_documents(source)
            with managed_transaction(self.repository.session):
                completed_run = self.repository.mark_run_success(run, documents)
                metrics_store.record_ingestion_run(
                    source_name=source.name,
                    status=completed_run.status,
                    duration_ms=(perf_counter() - started) * 1000,
                )
                return completed_run
        except Exception as exc:
            logger.exception(
                "documents-only ingestion run failed",
                extra={
                    "event": "ingestion_documents_only_failed",
                    "source_id": source_id,
                    "source_name": source.name,
                    "error_type": type(exc).__name__,
                },
            )
            self.repository.session.rollback()
            with managed_transaction(self.repository.session):
                failed_run = self.repository.mark_run_failure(run, str(exc))
            metrics_store.record_ingestion_run(
                source_name=source.name,
                status=failed_run.status,
                duration_ms=(perf_counter() - started) * 1000,
            )
            return failed_run

    def _fetch_parsed_documents(self, source) -> list[SourceDocument]:
        connector = self.get_connector_for_source(source)
        parser = parser_registry.get(source.parser_profile)
        return [
            self._normalize_document(document, parser.parse(document.payload))
            for document in self._fetch_documents(connector)
        ]

    def get_connector_for_source(self, source) -> SourceConnector:
        existing = connector_registry.get(source.name)
        if existing is not None:
            return existing
        built = self._build_default_connector(source)
        connector_registry.register(built)
        return built

    def _fetch_documents(self, connector: SourceConnector) -> list[SourceDocument]:
        # This method is sync on purpose: ingestion connectors do blocking
        # I/O (urllib, sqlalchemy). FastAPI runs sync endpoints in the
        # threadpool, so `time.sleep` here occupies a thread but does not
        # block the event loop. A real async rewrite is Fase 5 work; the
        # backoff below is intentionally short (max 0.6s total).
        last_error: Exception | None = None
        for attempt in range(1, self.MAX_FETCH_ATTEMPTS + 1):
            try:
                fetch = getattr(connector, "fetch")
                documents = fetch()
                return list(documents)
            except self.RETRYABLE_FETCH_EXCEPTIONS as exc:
                last_error = exc
                if attempt >= self.MAX_FETCH_ATTEMPTS:
                    break
                time.sleep(0.1 * attempt)
        if last_error is not None:
            raise last_error
        return []

    def _build_default_connector(self, source) -> SourceConnector:
        if source.kind == "json_feed":
            return JsonFeedConnector(name=source.name, base_url=source.base_url)
        if source.kind == "football_data_api":
            from os import getenv
            from urllib.parse import parse_qs, urlsplit

            parsed = urlsplit(source.base_url)
            params = parse_qs(parsed.query)
            # The factory derives the competition + optional date filters
            # from query params so one `football_data_api` kind can cover
            # PL, CLI (Copa Libertadores), CSA, etc. without per-league
            # subclasses. base_url shape:
            #   https://api.football-data.org?competition=CLI&date_from=...
            competition_code = (params.get("competition") or ["PL"])[0]
            date_from = (params.get("date_from") or [None])[0]
            date_to = (params.get("date_to") or [None])[0]
            season = (params.get("season") or [None])[0]
            # Drop the query so the connector builds the v4 URL itself.
            host = f"{parsed.scheme}://{parsed.netloc}" if parsed.scheme else source.base_url
            return FootballDataApiConnector(
                name=source.name,
                base_url=host,
                api_key=getenv("PROAI_FOOTBALL_DATA_API_KEY", ""),
                competition_code=competition_code,
                date_from=date_from,
                date_to=date_to,
                season=season,
            )
        if source.kind == "football_data_uk_csv":
            # Per-source season path: derive from the base_url's path so
            # each league/season has its own URL. Falls back to the EPL
            # 24-25 CSV when the source was created with only the host
            # for compatibility with the original Premier League sources.
            from urllib.parse import urlsplit

            parsed = urlsplit(source.base_url)
            root = f"{parsed.scheme}://{parsed.netloc}"
            path = parsed.path.lstrip("/")
            if not path.endswith(".csv"):
                path = "mmz4281/2425/E0.csv"
            return FootballDataUkSeasonConnector(
                name=source.name,
                base_url=root,
                season_path=path,
            )
        if source.kind == "thesportsdb_season":
            return TheSportsDbSeasonConnector(
                name=source.name,
                base_url=source.base_url,
            )
        if source.kind == "availability_json_feed":
            return AvailabilityJsonConnector(name=source.name, base_url=source.base_url)
        if source.kind == "progol_catalog_html":
            return ProgolCatalogHtmlConnector(name=source.name, base_url=source.base_url)
        if source.kind == "local_context_json":
            return LocalContextJsonConnector(name=source.name, file_path=source.base_url)
        return GenericHtmlConnector(name=source.name, base_url=source.base_url)

    def _normalize_document(self, document: SourceDocument, parsed_payload: dict[str, object]) -> SourceDocument:
        title = str(parsed_payload.get("title", document.source_name))
        normalized_key = self.normalization_service.normalize_competition_name(title)
        payload = {
            **parsed_payload,
            "normalized_key": normalized_key,
            "processed_at": datetime.now(timezone.utc).isoformat(),
        }
        return SourceDocument(
            source_name=document.source_name,
            source_url=document.source_url,
            captured_at=document.captured_at,
            payload=payload,
        )

    def _auto_discover_slates(self, source_id: str) -> None:
        slate_repo = SlateRepository(self.repository.session)
        discovery_service = SlateDiscoveryService(
            self.repository,
            slate_repo,
        )
        for week_type in ("weekend", "midweek", "revancha"):
            # Phase 1: dry-run discovery to get the inferred draw_code + week_type
            # without writing anything to the DB.
            try:
                response = discovery_service.discover(
                    SlateDiscoveryRequest(
                        week_type=week_type,
                        fixture_source_ids=[source_id],
                        create_persisted_slate=False,
                    )
                )
            except ValidationError:
                continue

            # Guard: never silently change the week_type of an already-promoted
            # slate. Doing so would produce a different composition_hash, bump
            # slate_version, and invalidate valid snapshots — all spuriously.
            existing = slate_repo.find_by_draw_code(response.draw_code)
            if existing is not None and existing.week_type != response.week_type:
                logger.warning(
                    "auto_discover_week_type_conflict",
                    extra={
                        "event": "auto_discover_week_type_conflict",
                        "draw_code": response.draw_code,
                        "existing_week_type": existing.week_type,
                        "discovered_week_type": response.week_type,
                        "source_id": source_id,
                        "action": "skipped",
                    },
                )
                continue

            # Phase 2: persist — same request, now with create_persisted_slate=True.
            try:
                discovery_service.discover(
                    SlateDiscoveryRequest(
                        week_type=week_type,
                        fixture_source_ids=[source_id],
                        create_persisted_slate=True,
                    )
                )
            except ValidationError:
                continue

    def _persist_structured_stats(
        self,
        source_id: str,
        documents: list[SourceDocument],
        matches,
    ) -> None:
        entity_repository = EntityRepository(self.repository.session)
        stats_service = StatsService(StatsRepository(self.repository.session))
        for document in documents:
            payload = document.payload
            team_stats = payload.get("team_stats", [])
            match_stats = payload.get("match_stats", [])
            # F6.2: bulk CSV sources (football-data) never carry stats.
            # Skipping documents that bring nothing avoids an O(D x M)
            # scan over the whole match table per document — that scan
            # was the real bottleneck of the historical backfill.
            if not team_stats and not match_stats:
                continue
            for item in team_stats:
                if not isinstance(item, dict):
                    continue
                team_name = str(item.get("team_name", ""))
                if not team_name:
                    continue
                team = entity_repository.resolve_team(team_name, "") if hasattr(entity_repository, "resolve_team") else None
                if team is None:
                    from app.services.entity_resolution_service import EntityResolutionService

                    team = EntityResolutionService(entity_repository, self.normalization_service).resolve_team(team_name, None)
                stats_service.persist_team_stat(
                    TeamStatSnapshotModel(
                        team_id=team.id,
                        source_id=source_id,
                        captured_at=document.captured_at,
                        stat_type=str(item.get("stat_type", "unknown")),
                        value=float(item.get("value", 0.0)),
                        sample_size=int(item.get("sample_size", 0)),
                    )
                )

            # match_stats is already fetched above; reuse it.
            normalized_haystack = self.normalization_service.normalize_competition_name(
                " ".join(
                    [
                        str(payload.get("title", "")),
                        str(payload.get("summary", "")),
                        " ".join(str(entry) for entry in payload.get("headings", [])),
                    ]
                )
            )
            best_match = None
            best_score = 0.0
            evidence_service = EvidenceService(EvidenceRepository(self.repository.session), self.normalization_service)
            for match in matches:
                score = evidence_service._score_document_match(match, normalized_haystack)
                if score > best_score:
                    best_score = score
                    best_match = match
            if best_match is None or best_score < EvidenceService.MATCH_THRESHOLD:
                continue
            for item in match_stats:
                if not isinstance(item, dict):
                    continue
                stats_service.persist_match_stat(
                    MatchStatSnapshotModel(
                        match_id=best_match.id,
                        source_id=source_id,
                        captured_at=document.captured_at,
                        stat_type=str(item.get("stat_type", "unknown")),
                        home_value=float(item.get("home_value", 0.0)),
                        away_value=float(item.get("away_value", 0.0)),
                    )
                )

    def _persist_historical_results(self, source_id: str, documents: list[SourceDocument]) -> None:
        entity_repository = EntityRepository(self.repository.session)
        resolver = EntityResolutionService(entity_repository, self.normalization_service)
        result_service = ResultService(ResultRepository(self.repository.session))
        for document in documents:
            historical_results = document.payload.get("historical_results", [])
            for item in historical_results:
                if not isinstance(item, dict):
                    continue
                competition_name = str(item.get("competition_name") or item.get("competition") or "")
                home_team_name = str(item.get("home_team", ""))
                away_team_name = str(item.get("away_team", ""))
                played_at_raw = str(item.get("played_at", ""))
                if not competition_name or not home_team_name or not away_team_name or not played_at_raw:
                    continue
                played_at = datetime.fromisoformat(played_at_raw.replace("Z", "+00:00"))
                competition = resolver.resolve_competition(competition_name, None, None)
                home_team = resolver.resolve_team(home_team_name, None)
                away_team = resolver.resolve_team(away_team_name, None)
                match = entity_repository.find_match_by_identity(
                    competition_id=competition.id,
                    home_team_id=home_team.id,
                    away_team_id=away_team.id,
                    kickoff_at=played_at,
                )
                if match is None:
                    match = self._find_nearby_match_for_result(
                        entity_repository=entity_repository,
                        competition_id=competition.id,
                        home_team_id=home_team.id,
                        away_team_id=away_team.id,
                        played_at=played_at,
                    )
                if match is None:
                    match = MatchModel(
                        competition=competition,
                        home_team=home_team,
                        away_team=away_team,
                        kickoff_at=played_at,
                        venue=None,
                    )
                    self.repository.session.add(match)
                    self.repository.session.flush()
                    self.repository.session.refresh(match)
                home_goals = int(item.get("home_goals", 0))
                away_goals = int(item.get("away_goals", 0))
                result_code = "1" if home_goals > away_goals else "2" if away_goals > home_goals else "X"
                result_service.persist_result(
                    MatchResultModel(
                        match_id=match.id,
                        source_id=source_id,
                        played_at=played_at,
                        home_goals=home_goals,
                        away_goals=away_goals,
                        result_code=result_code,
                    )
                )

    def _find_nearby_match_for_result(
        self,
        *,
        entity_repository: EntityRepository,
        competition_id: str,
        home_team_id: str,
        away_team_id: str,
        played_at: datetime,
    ) -> MatchModel | None:
        # F6.2: pull only the matches that share the exact (competition,
        # teams) identity and a kickoff inside the tolerance window. The
        # previous implementation listed ALL matches in memory per
        # historical result -> O(N^2) over the whole DB and made the
        # backfill grind to a halt at ~1500 results.
        from datetime import timedelta as _td

        from sqlalchemy import select as _select

        if played_at.tzinfo is None:
            played_at = played_at.replace(tzinfo=timezone.utc)
        tolerance_days = self._tolerance_days_for_competition(competition_id, entity_repository)
        tolerance = _td(days=tolerance_days)
        statement = (
            _select(MatchModel)
            .where(
                MatchModel.competition_id == competition_id,
                MatchModel.home_team_id == home_team_id,
                MatchModel.away_team_id == away_team_id,
                MatchModel.kickoff_at >= played_at - tolerance,
                MatchModel.kickoff_at <= played_at + tolerance,
            )
            .order_by(MatchModel.kickoff_at.asc())
        )
        best_match: MatchModel | None = None
        best_delta_seconds: float | None = None
        for candidate in entity_repository.session.scalars(statement).all():
            kickoff_at = candidate.kickoff_at
            if kickoff_at.tzinfo is None:
                kickoff_at = kickoff_at.replace(tzinfo=timezone.utc)
            delta_seconds = abs((kickoff_at - played_at).total_seconds())
            if best_delta_seconds is None or delta_seconds < best_delta_seconds:
                best_match = candidate
                best_delta_seconds = delta_seconds
        return best_match

    def _tolerance_days_for_competition(
        self,
        competition_id: str,
        entity_repository: EntityRepository,
    ) -> float:
        """Per-league tolerance for matching an incoming result row to a
        pre-existing fixture. Falls back to RESULT_MATCH_DATE_TOLERANCE_DAYS
        when the competition has no history yet (cold start) or the median
        gap is unavailable.

        We compute window = 1.5 * median_gap so an Apertura/Clausura
        torneo with weekly cadence gets ~10 days, a friendly tour with
        ~3-month gaps gets the ceiling cap, and a knockout fixture with
        near-zero historical cadence falls back to the conservative floor.
        Clamping prevents both runaway widening (which would merge an
        incoming result into the wrong fixture cluster) and absurd
        narrowing (which would create duplicate matches on every
        reschedule).
        """
        if not competition_id:
            return float(self.RESULT_MATCH_DATE_TOLERANCE_DAYS)
        cached = self._competition_tolerance_cache.get(competition_id)
        if cached is not None:
            return cached
        try:
            from app.repositories.result_repository import ResultRepository as _ResultRepo

            median = _ResultRepo(entity_repository.session).median_gap_days_for_competition(competition_id)
        except Exception:  # pragma: no cover - repository should always be importable
            median = None
        if median is None or median <= 0:
            tolerance = float(self.RESULT_MATCH_DATE_TOLERANCE_DAYS)
        else:
            tolerance = max(
                float(self.RESULT_MATCH_TOLERANCE_FLOOR_DAYS),
                min(float(self.RESULT_MATCH_TOLERANCE_CEILING_DAYS), median * 1.5),
            )
        self._competition_tolerance_cache[competition_id] = tolerance
        return tolerance

    def _persist_player_availability(
        self,
        source_id: str,
        linked_items: list[tuple[SourceDocumentModel, str]],
    ) -> None:
        evidence_repository = EvidenceRepository(self.repository.session)
        entity_repository = EntityRepository(self.repository.session)
        interpreter = NarrativeInterpretationService(
            AvailabilityRepository(self.repository.session),
            entity_repository,
            self.normalization_service,
        )
        for document, evidence_id in linked_items:
            match_id = getattr(document, "matched_match_id", None)
            if not match_id:
                continue
            match = evidence_repository.get_match_with_relations(match_id)
            if match is None:
                continue
            payload = {}
            if getattr(document, "payload_json", None):
                import json

                payload = json.loads(document.payload_json)
            interpreter.interpret_document_for_match(
                match=match,
                source_id=source_id,
                evidence_id=evidence_id,
                captured_at=document.captured_at,
                payload=payload,
            )
