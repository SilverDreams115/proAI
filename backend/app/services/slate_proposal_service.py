"""Stages upcoming Progol contests scraped from the LN guide PDF.

The service implements the dual-time validation pattern (Fase 2):

  * First observation of a (draw_code, source_url) lands as
    `status='observed'` with `observations=1`.
  * A second observation with the same fixture signature flips the row
    to `status='validated'`. The frontend "Próximo concurso" card only
    surfaces validated proposals.
  * If a second observation produces a different signature (LN edited
    the slate between scrapes), the row resets to `observations=1` and
    waits again — so transient parsing flips never bubble into
    auto-promote.

Promotion to the real `progol_slates` table is intentionally NOT
automatic in Fase 2. An operator clicks promote in the UI; Fase 3 adds
the option to auto-promote once trust is established.
"""
from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.session import managed_transaction
from app.models.tables import ProgolSlateModel
from app.models.tables import ProgolSlateProposalModel
from app.repositories.slate_repository import SlateRepository
from app.schemas.common import CompetitionPayload
from app.schemas.common import MatchReferencePayload
from app.schemas.common import TeamPayload
from app.schemas.slate import ProgolSlateCreate
from app.services.progol_fixture_resolver import ProgolFixtureResolver
from app.services.slate_service import SlateService

logger = logging.getLogger("proai.slate_proposal")

# Prefix used when building the draw_code for a promoted slate, keyed by
# week_type. Must stay consistent with SlateDiscoveryService._default_draw_code.
_WEEK_TYPE_PREFIX: dict[str, str] = {
    "weekend": "PG",
    "midweek": "PGM",
    "revancha": "PGR",
}


@dataclass
class PromotionResult:
    """Returned by promote_proposal to distinguish fresh creation from
    idempotent re-promotion of an already-active slate."""
    slate: ProgolSlateModel
    already_active: bool


class SlateProposalService:
    """Reads the canonical LN Progol guide PDF and persists proposals.

    The connector instance is injected so tests can substitute a stub
    that returns a captured PDF payload without hitting the network.
    """

    REQUIRED_OBSERVATIONS = 2

    def __init__(self, session: Session, connector_factory=None) -> None:
        self.session = session
        self._connector_factory = connector_factory

    def observe(self) -> ProgolSlateProposalModel | None:
        """Fetch the live LN weekend guide and record an observation.

        Returns the stored proposal row, or None when the PDF didn't parse
        cleanly (no draw_code or < 14 fixtures). None is the worker's
        signal that this cycle was a no-op — don't alert."""
        from app.connectors.progol_guia_pdf import ProgolGuiaPdfConnector

        factory = self._connector_factory or (
            lambda: ProgolGuiaPdfConnector(name="progol-guia-ln-weekend")
        )
        connector = factory()
        try:
            documents = connector.fetch()
        except Exception as exc:  # pragma: no cover - network failure path
            logger.warning("progol guide fetch failed", extra={"event": "guia_fetch_failed", "error": str(exc)})
            return None
        if not documents:
            return None
        payload = documents[0].payload
        draw_code = payload.get("draw_code")
        fixtures = payload.get("fixtures") or []
        if not draw_code or len(fixtures) < 14:
            logger.info(
                "progol guide observation rejected: incomplete parse",
                extra={
                    "event": "guia_observation_rejected",
                    "draw_code": draw_code,
                    "fixture_count": len(fixtures),
                },
            )
            return None
        return self._record_observation(
            draw_code=draw_code,
            source_name=connector.name,
            source_url=documents[0].source_url,
            week_type=payload.get("week_type", "weekend"),
            closes_at_iso=payload.get("registration_closes_at"),
            payload=payload,
        )

    def observe_ms(self) -> ProgolSlateProposalModel | None:
        """Fetch the live LN Progol Media Semana guide and record an observation.

        Mirrors observe() but targets the MS PDF (9 fixtures, midweek).
        Returns None when the PDF yields fewer than 9 fixtures or no
        draw_code — worker treats None as a silent no-op."""
        from app.connectors.progol_guia_pdf import ProgolMsGuiaPdfConnector

        factory = self._connector_factory or (
            lambda: ProgolMsGuiaPdfConnector(name="progol-guia-ln-ms")
        )
        connector = factory()
        try:
            documents = connector.fetch()
        except Exception as exc:  # pragma: no cover
            logger.warning(
                "progol MS guide fetch failed",
                extra={"event": "guia_ms_fetch_failed", "error": str(exc)},
            )
            return None
        if not documents:
            return None
        payload = documents[0].payload
        draw_code = payload.get("draw_code")
        fixtures = payload.get("fixtures") or []
        if not draw_code or len(fixtures) < 9:
            logger.info(
                "progol MS guide observation rejected: incomplete parse",
                extra={
                    "event": "guia_ms_observation_rejected",
                    "draw_code": draw_code,
                    "fixture_count": len(fixtures),
                },
            )
            return None
        return self._record_observation(
            draw_code=draw_code,
            source_name=connector.name,
            source_url=documents[0].source_url,
            week_type="midweek",
            closes_at_iso=payload.get("registration_closes_at"),
            payload=payload,
        )

    def list_proposals(self, status: str | None = None) -> list[ProgolSlateProposalModel]:
        stmt = select(ProgolSlateProposalModel).order_by(
            ProgolSlateProposalModel.last_seen_at.desc()
        )
        if status:
            stmt = stmt.where(ProgolSlateProposalModel.status == status)
        return list(self.session.scalars(stmt))

    def get_proposal(self, proposal_id: str) -> ProgolSlateProposalModel | None:
        return self.session.get(ProgolSlateProposalModel, proposal_id)

    # ------------------------------------------------------------------
    # Promotion
    # ------------------------------------------------------------------

    # Fallback kickoffs spread across one hour per partido starting
    # cierre + 12h. Used only when ProgolFixtureResolver can't find a
    # real match for the pair — typically friendlies or competitions we
    # don't ingest.
    FALLBACK_BASE_OFFSET = timedelta(hours=12)

    def promote_proposal(
        self,
        proposal: ProgolSlateProposalModel,
        *,
        actor: str = "operator",
    ) -> PromotionResult:
        """Create a real progol_slates row from a validated proposal.

        Pair by pair: try to find a real upcoming match in the DB. When
        found, use its competition/kickoff/venue. When not, fall back to
        a synthetic placeholder so the slate still has 14 partidos and
        the model can score what it can. The `actor` parameter is
        recorded in logs so we can distinguish operator clicks from
        worker auto-promotions during incident review.

        Returns a PromotionResult with already_active=True when a slate
        for this draw_code already exists with the same composition_hash
        — the proposal is still marked "promoted" (linked to the
        existing slate) so repeated calls are idempotent.

        Raises ValueError when the proposal isn't in a promotable state
        — callers translate that into a 409 at the HTTP boundary.
        """
        if proposal.status == "promoted":
            raise ValueError("Proposal already promoted.")
        if proposal.status != "validated":
            raise ValueError(
                f"Proposal status is '{proposal.status}'; only validated proposals can be promoted."
            )

        try:
            payload = json.loads(proposal.payload_json or "{}")
        except json.JSONDecodeError as exc:
            raise ValueError(f"Stored payload is malformed: {exc}") from exc
        fixtures_raw = payload.get("fixtures", [])
        if not fixtures_raw:
            raise ValueError("Proposal payload has no fixtures.")

        cierre = proposal.registration_closes_at or datetime.now(timezone.utc)
        if cierre.tzinfo is None:
            cierre = cierre.replace(tzinfo=timezone.utc)

        resolver = ProgolFixtureResolver(self.session)
        pairs = [
            (int(f.get("position", 0)), str(f.get("home", "")), str(f.get("away", "")))
            for f in fixtures_raw
        ]
        resolved = resolver.resolve_many(pairs, cierre)

        fallback_base = cierre + self.FALLBACK_BASE_OFFSET
        placeholder_competition = f"Progol Concurso {proposal.draw_code}"
        matches: list[MatchReferencePayload] = []
        matched_count = 0
        inferred_count = 0
        for position, home, away in pairs:
            match_model = resolved.get(position)
            if match_model is not None:
                matched_count += 1
                matches.append(
                    MatchReferencePayload(
                        position=position,
                        competition=CompetitionPayload(
                            name=match_model.competition.name,
                            country=match_model.competition.country,
                            season=match_model.competition.season,
                        ),
                        home_team=TeamPayload(
                            name=match_model.home_team.name,
                            country=match_model.home_team.country,
                        ),
                        away_team=TeamPayload(
                            name=match_model.away_team.name,
                            country=match_model.away_team.country,
                        ),
                        kickoff_at=match_model.kickoff_at,
                        venue=match_model.venue,
                    )
                )
            else:
                # No upcoming match found. Try to infer the competition
                # from each team's history so the readiness policy can
                # still classify the fixture instead of pinning it to
                # "unclassified" / blocked. When neither team is known,
                # fall through to the synthetic placeholder competition.
                inferred = resolver.infer_competition_for_pair(home, away)
                if inferred is not None:
                    inferred_count += 1
                    competition_payload = CompetitionPayload(
                        name=inferred.name,
                        country=inferred.country,
                        season=inferred.season,
                    )
                else:
                    competition_payload = CompetitionPayload(
                        name=placeholder_competition,
                        is_placeholder=True,
                    )
                # Resolver couldn't find an existing team row, so any
                # team we now create from the raw PDF name is a
                # placeholder by definition. Marking it so prevents the
                # row from outranking a real team that lands later via
                # ingestion (see Tampico/Tampico-Madero incident).
                home_team_payload = TeamPayload(
                    name=home.title(),
                    is_placeholder=True,
                )
                away_team_payload = TeamPayload(
                    name=away.title(),
                    is_placeholder=True,
                )
                matches.append(
                    MatchReferencePayload(
                        position=position,
                        competition=competition_payload,
                        home_team=home_team_payload,
                        away_team=away_team_payload,
                        kickoff_at=fallback_base + timedelta(hours=max(0, position - 1)),
                        venue=None,
                    )
                )

        prefix = _WEEK_TYPE_PREFIX.get(proposal.week_type, "PG")
        formatted_draw_code = f"{prefix}-{proposal.draw_code}"
        create_payload = ProgolSlateCreate(
            label=f"Progol {proposal.draw_code}",
            draw_code=formatted_draw_code,
            week_type=proposal.week_type,
            registration_closes_at=proposal.registration_closes_at,
            is_archived=False,
            matches=matches,
        )

        # Guard: if a non-archived slate already exists for this draw_code,
        # compare the RAW fixture signature of the current proposal against the
        # signature of whichever proposal previously promoted that slate.
        # Using the raw PDF signature (draw_code + ordered home/away names) avoids
        # false hash mismatches caused by timezone-stripping in SQLite tests or by
        # differences in fixture resolver output across calls.
        slate_repo = SlateRepository(self.session)
        existing_slate = slate_repo.find_by_draw_code(formatted_draw_code)
        if existing_slate is not None and not existing_slate.is_archived:
            current_sig = self._signature(payload)
            existing_promoter = self.session.scalar(
                select(ProgolSlateProposalModel)
                .where(
                    ProgolSlateProposalModel.promoted_slate_id == existing_slate.id,
                    ProgolSlateProposalModel.status == "promoted",
                )
                .limit(1)
            )
            if existing_promoter is not None:
                prior_sig = self._signature(json.loads(existing_promoter.payload_json))
                if prior_sig == current_sig:
                    proposal.status = "promoted"
                    proposal.promoted_slate_id = existing_slate.id
                    self.session.flush()
                    logger.info(
                        "progol proposal promote skipped: slate already active with same raw fixtures",
                        extra={
                            "event": "progol_proposal_already_active",
                            "draw_code": proposal.draw_code,
                            "actor": actor,
                            "slate_id": existing_slate.id,
                        },
                    )
                    return PromotionResult(
                        slate=slate_repo.get_slate(existing_slate.id),  # type: ignore[arg-type]
                        already_active=True,
                    )

        slate_service = SlateService(slate_repo)
        slate = slate_service.create_slate(create_payload)

        proposal.status = "promoted"
        proposal.promoted_slate_id = slate.id
        self.session.flush()

        logger.info(
            "progol proposal promoted",
            extra={
                "event": "progol_proposal_promoted",
                "draw_code": proposal.draw_code,
                "actor": actor,
                "matched_fixtures": matched_count,
                "inferred_fixtures": inferred_count,
                "total_fixtures": len(matches),
                "slate_id": slate.id,
            },
        )
        return PromotionResult(slate=slate, already_active=False)

    def _record_observation(
        self,
        *,
        draw_code: str,
        source_name: str,
        source_url: str,
        week_type: str,
        closes_at_iso: str | None,
        payload: dict[str, Any],
    ) -> ProgolSlateProposalModel:
        signature = self._signature(payload)
        existing = self.session.scalar(
            select(ProgolSlateProposalModel).where(
                ProgolSlateProposalModel.draw_code == draw_code,
                ProgolSlateProposalModel.source_url == source_url,
            )
        )
        now = datetime.now(timezone.utc)
        closes_at = None
        if closes_at_iso:
            try:
                closes_at = datetime.fromisoformat(closes_at_iso.replace("Z", "+00:00"))
            except ValueError:
                closes_at = None
        with managed_transaction(self.session):
            if existing is None:
                row = ProgolSlateProposalModel(
                    draw_code=str(draw_code),
                    week_type=week_type,
                    source_name=source_name,
                    source_url=source_url,
                    registration_closes_at=closes_at,
                    payload_json=json.dumps(payload, ensure_ascii=False),
                    status="observed",
                    observations=1,
                    first_seen_at=now,
                    last_seen_at=now,
                )
                self.session.add(row)
                self.session.flush()
                logger.info(
                    "progol proposal observed (first sighting)",
                    extra={
                        "event": "progol_proposal_observed",
                        "draw_code": draw_code,
                        "status": "observed",
                    },
                )
                return row
            existing_signature = self._signature(json.loads(existing.payload_json))
            if existing_signature == signature:
                existing.observations += 1
                if (
                    existing.status == "observed"
                    and existing.observations >= self.REQUIRED_OBSERVATIONS
                ):
                    existing.status = "validated"
                    logger.info(
                        "progol proposal validated",
                        extra={
                            "event": "progol_proposal_validated",
                            "draw_code": draw_code,
                            "observations": existing.observations,
                        },
                    )
                existing.last_seen_at = now
                # Refresh cierre in case LN updated the venta window
                # without changing the fixtures (rare but observed).
                if closes_at is not None:
                    existing.registration_closes_at = closes_at
            else:
                # The fixture set drifted between observations — we trust
                # the latest payload but reset the counter so we wait
                # for a second confirmation before validating again.
                existing.payload_json = json.dumps(payload, ensure_ascii=False)
                existing.registration_closes_at = closes_at
                existing.observations = 1
                existing.status = "observed"
                existing.last_seen_at = now
                logger.warning(
                    "progol proposal payload drifted between observations",
                    extra={
                        "event": "progol_proposal_drift",
                        "draw_code": draw_code,
                    },
                )
            self.session.flush()
            return existing

    @staticmethod
    def _signature(payload: dict[str, Any]) -> str:
        # Signature ignores `raw_text_excerpt` and metadata that LN may
        # cosmetically change between revisions; what matters is the
        # draw_code and the ordered home/away tuples.
        relevant = {
            "draw_code": payload.get("draw_code"),
            "fixtures": sorted(
                [(f.get("position"), f.get("home"), f.get("away")) for f in payload.get("fixtures", [])]
            ),
        }
        encoded = json.dumps(relevant, sort_keys=True, ensure_ascii=False)
        return hashlib.sha256(encoded.encode("utf-8")).hexdigest()
