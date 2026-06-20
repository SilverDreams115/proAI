import hashlib
import json
import logging
from datetime import datetime, timezone

from sqlalchemy import and_
from sqlalchemy import delete
from sqlalchemy import select
from sqlalchemy.orm import Session
from sqlalchemy.orm import joinedload

from app.models.tables import MatchModel
from app.models.tables import ProgolSlateMatchModel
from app.models.tables import ProgolSlateModel
from app.models.tables import TicketRecommendationSnapshotModel
from app.repositories.entity_repository import EntityRepository
from app.schemas.slate import ProgolSlateCreate
from app.services.entity_resolution_service import EntityResolutionService

logger = logging.getLogger(__name__)


class SlateRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def list_slates(self) -> list[ProgolSlateModel]:
        statement = (
            select(ProgolSlateModel)
            .options(
                joinedload(ProgolSlateModel.matches)
                .joinedload(ProgolSlateMatchModel.match)
                .joinedload(MatchModel.home_team),
                joinedload(ProgolSlateModel.matches)
                .joinedload(ProgolSlateMatchModel.match)
                .joinedload(MatchModel.away_team),
                joinedload(ProgolSlateModel.matches)
                .joinedload(ProgolSlateMatchModel.match)
                .joinedload(MatchModel.competition),
            )
            .order_by(ProgolSlateModel.created_at.desc())
        )
        return list(self.session.scalars(statement).unique())

    def get_slate(self, slate_id: str) -> ProgolSlateModel | None:
        statement = (
            select(ProgolSlateModel)
            .where(ProgolSlateModel.id == slate_id)
            .options(
                joinedload(ProgolSlateModel.matches)
                .joinedload(ProgolSlateMatchModel.match)
                .joinedload(MatchModel.home_team),
                joinedload(ProgolSlateModel.matches)
                .joinedload(ProgolSlateMatchModel.match)
                .joinedload(MatchModel.away_team),
                joinedload(ProgolSlateModel.matches)
                .joinedload(ProgolSlateMatchModel.match)
                .joinedload(MatchModel.competition),
            )
        )
        return self.session.scalar(statement)

    def find_by_draw_code(self, draw_code: str) -> ProgolSlateModel | None:
        statement = select(ProgolSlateModel).where(ProgolSlateModel.draw_code == draw_code)
        return self.session.scalar(statement)

    def list_due_for_archive(self, now: datetime) -> list[ProgolSlateModel]:
        # Returns slates that have passed their registration cierre but are
        # still flagged as active. The cierre job uses this to flip them in
        # batch — idempotent because a second call returns an empty list
        # once is_archived is true.
        statement = select(ProgolSlateModel).where(
            and_(
                ProgolSlateModel.is_archived.is_(False),
                ProgolSlateModel.registration_closes_at.is_not(None),
                ProgolSlateModel.registration_closes_at <= now,
            )
        )
        return list(self.session.scalars(statement))

    def mark_archived(self, slate: ProgolSlateModel) -> None:
        slate.is_archived = True
        self.session.add(slate)
        self.session.flush()

    def create_slate(self, payload: ProgolSlateCreate) -> ProgolSlateModel:
        return self.upsert_slate(payload)

    @staticmethod
    def _compute_composition_hash(payload: ProgolSlateCreate) -> str:
        """SHA-256 of the ordered fixture list, from the RAW payload names.

        Deterministic from the payload alone — no DB IDs needed. Two
        calls with the same draw_code/week_type/fixtures always produce
        the same hash, enabling safe re-ingestion detection.

        CONTRACT: this is the *canonical* composition_hash. It hashes the
        team/competition names exactly as they arrive in the payload,
        BEFORE entity resolution. The value it produces is what gets
        persisted on the slate and copied onto every prediction and ticket
        snapshot. Because it runs pre-resolution, a payload of Spanish
        names (e.g. "CHEQUIA") yields a hash that will NOT match a recompute
        over the resolved canonical model names ("Czech Republic"). That
        divergence is expected — see ``_compute_hash_from_model``. Pinned by
        tests/test_composition_hash_contract.py.
        """
        fixtures = [
            {
                "position": item.position,
                "home_team": item.home_team.name.strip().lower(),
                "away_team": item.away_team.name.strip().lower(),
                "kickoff_at": item.kickoff_at.isoformat(),
                "competition": item.competition.name.strip().lower(),
            }
            for item in sorted(payload.matches, key=lambda m: m.position)
        ]
        content = json.dumps(
            {
                "draw_code": payload.draw_code,
                "week_type": payload.week_type,
                "fixtures": fixtures,
            },
            sort_keys=True,
            separators=(",", ":"),
        )
        return hashlib.sha256(content.encode()).hexdigest()

    def _invalidate_snapshots_for_composition(
        self, slate_id: str, old_hash: str
    ) -> int:
        """Flip is_valid=False on all valid snapshots for this slate.

        Called only when the composition hash changes for an existing
        draw_code — the old snapshots reference match_ids that are about
        to be replaced, so they must never be returned as current.
        """
        now = datetime.now(timezone.utc)
        statement = select(TicketRecommendationSnapshotModel).where(
            TicketRecommendationSnapshotModel.slate_id == slate_id,
            TicketRecommendationSnapshotModel.is_valid.is_(True),
        )
        snapshots = list(self.session.scalars(statement))
        for snapshot in snapshots:
            snapshot.is_valid = False
            snapshot.invalidated_at = now
            snapshot.invalidation_reason = f"composition_changed_from_{old_hash[:8]}"
            self.session.add(snapshot)
        if snapshots:
            self.session.flush()
        return len(snapshots)

    @staticmethod
    def _compute_hash_from_model(slate: "ProgolSlateModel") -> str | None:
        """Compute composition_hash from an already-loaded ProgolSlateModel.

        Used during backfill for slates that predate hash tracking. Requires
        that slate.matches and their nested match/home_team/away_team/competition
        are eagerly loaded. Returns None if the slate has no match links.

        WARNING: this hashes the RESOLVED (canonical) team/competition names,
        not the raw payload names. It is therefore NOT interchangeable with
        ``_compute_composition_hash``: for any slate whose names were
        canonicalized by entity resolution the two helpers return different
        digests. Never use this to "refresh" or overwrite a composition_hash
        that originally came from a raw payload (doing so would silently
        invalidate every prediction/snapshot keyed on the stored hash). It is
        safe ONLY for first-time backfill of slates whose hash is NULL (see
        ``backfill_composition_hashes``). A future cleanup could unify both
        helpers behind a single canonical implementation; until then they are
        intentionally kept separate and documented.
        """
        if not slate.matches:
            return None
        fixtures = []
        for sm in sorted(slate.matches, key=lambda m: m.position):
            kickoff = sm.match.kickoff_at
            if kickoff.tzinfo is None:
                kickoff = kickoff.replace(tzinfo=timezone.utc)
            fixtures.append({
                "position": sm.position,
                "home_team": sm.match.home_team.name.strip().lower(),
                "away_team": sm.match.away_team.name.strip().lower(),
                "kickoff_at": kickoff.isoformat(),
                "competition": sm.match.competition.name.strip().lower(),
            })
        content = json.dumps(
            {"draw_code": slate.draw_code, "week_type": slate.week_type, "fixtures": fixtures},
            sort_keys=True,
            separators=(",", ":"),
        )
        return hashlib.sha256(content.encode()).hexdigest()

    def backfill_composition_hashes(self) -> int:
        """Populate composition_hash for slates where it is NULL (idempotent).

        Safe to call at startup: slates that already have a hash are skipped,
        snapshots are never invalidated. Returns the count of slates updated.

        Note: this fills NULL hashes using ``_compute_hash_from_model`` (the
        resolved-name convention), which may differ from the raw-payload hash
        the slate would have received at upsert time. This is acceptable here
        precisely because it only ever touches slates that have NO hash yet —
        it can never overwrite a persisted payload-derived hash, so existing
        predictions/snapshots are unaffected.
        """
        statement = (
            select(ProgolSlateModel)
            .where(ProgolSlateModel.composition_hash.is_(None))
            .options(
                joinedload(ProgolSlateModel.matches)
                .joinedload(ProgolSlateMatchModel.match)
                .joinedload(MatchModel.home_team),
                joinedload(ProgolSlateModel.matches)
                .joinedload(ProgolSlateMatchModel.match)
                .joinedload(MatchModel.away_team),
                joinedload(ProgolSlateModel.matches)
                .joinedload(ProgolSlateMatchModel.match)
                .joinedload(MatchModel.competition),
            )
        )
        slates = list(self.session.scalars(statement).unique())
        updated = 0
        for slate in slates:
            new_hash = self._compute_hash_from_model(slate)
            if new_hash is None:
                continue
            slate.composition_hash = new_hash
            if slate.slate_version is None:
                slate.slate_version = 1
            self.session.add(slate)
            updated += 1
        if updated:
            self.session.flush()
        logger.info(
            "composition_hash_backfill",
            extra={"event": "composition_hash_backfill", "slates_updated": updated},
        )
        return updated

    def upsert_slate(self, payload: ProgolSlateCreate) -> ProgolSlateModel:
        new_hash = self._compute_composition_hash(payload)
        resolver = EntityResolutionService(EntityRepository(self.session))
        entity_repository = EntityRepository(self.session)
        slate = self.find_by_draw_code(payload.draw_code)

        if slate is None:
            slate = ProgolSlateModel(
                label=payload.label,
                draw_code=payload.draw_code,
                week_type=payload.week_type,
                registration_closes_at=payload.registration_closes_at,
                is_archived=payload.is_archived,
                composition_hash=new_hash,
                slate_version=1,
            )
            self.session.add(slate)
            self.session.flush()
        else:
            old_hash = slate.composition_hash

            slate.label = payload.label
            slate.week_type = payload.week_type
            slate.registration_closes_at = payload.registration_closes_at
            slate.is_archived = payload.is_archived

            if old_hash is not None and old_hash != new_hash:
                new_version = (slate.slate_version or 1) + 1
                slate.slate_version = new_version
                invalidated_count = self._invalidate_snapshots_for_composition(slate.id, old_hash)
                logger.warning(
                    "slate_composition_changed",
                    extra={
                        "event": "slate_composition_changed",
                        "slate_id": slate.id,
                        "draw_code": slate.draw_code,
                        "old_hash": old_hash,
                        "new_hash": new_hash,
                        "slate_version": new_version,
                        "invalidated_snapshots": invalidated_count,
                    },
                )
            elif old_hash is None:
                # Backfill: first hash for a pre-existing slate — safe to set
                # without bumping version since we have no prior hash to compare.
                slate.slate_version = slate.slate_version or 1

            slate.composition_hash = new_hash

            self.session.execute(
                delete(ProgolSlateMatchModel).where(ProgolSlateMatchModel.slate_id == slate.id)
            )

        for item in payload.matches:
            competition = resolver.resolve_competition(
                item.competition.name,
                item.competition.country,
                item.competition.season,
                is_placeholder=item.competition.is_placeholder,
            )
            home_team = resolver.resolve_team(
                item.home_team.name,
                item.home_team.country,
                is_placeholder=item.home_team.is_placeholder,
            )
            away_team = resolver.resolve_team(
                item.away_team.name,
                item.away_team.country,
                is_placeholder=item.away_team.is_placeholder,
            )
            match = entity_repository.find_match_by_identity(
                competition_id=competition.id,
                home_team_id=home_team.id,
                away_team_id=away_team.id,
                kickoff_at=item.kickoff_at,
            )
            if match is None:
                match = MatchModel(
                    competition=competition,
                    home_team=home_team,
                    away_team=away_team,
                    kickoff_at=item.kickoff_at,
                    venue=item.venue,
                )
                self.session.add(match)
                self.session.flush()
            else:
                match.venue = item.venue or match.venue
            slate_link = ProgolSlateMatchModel(position=item.position, match=match, slate=slate)
            self.session.add(slate_link)

        self.session.flush()
        return self.get_slate(slate.id)  # type: ignore[return-value]
