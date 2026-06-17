from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

from sqlalchemy import Boolean
from sqlalchemy import DateTime
from sqlalchemy import Float
from sqlalchemy import ForeignKey
from sqlalchemy import Integer
from sqlalchemy import String
from sqlalchemy import Text
from sqlalchemy import UniqueConstraint
from sqlalchemy.orm import Mapped
from sqlalchemy.orm import mapped_column
from sqlalchemy.orm import relationship

from app.db.base import Base


def generate_id() -> str:
    return str(uuid4())


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class CompetitionModel(Base):
    __tablename__ = "competitions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=generate_id)
    name: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    country: Mapped[str | None] = mapped_column(String(120))
    season: Mapped[str | None] = mapped_column(String(80))
    is_placeholder: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    matches: Mapped[list["MatchModel"]] = relationship(back_populates="competition")
    aliases: Mapped[list["CompetitionAliasModel"]] = relationship(back_populates="competition")


class TeamModel(Base):
    __tablename__ = "teams"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=generate_id)
    name: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    country: Mapped[str | None] = mapped_column(String(120))
    is_placeholder: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    home_matches: Mapped[list["MatchModel"]] = relationship(
        foreign_keys="MatchModel.home_team_id",
        back_populates="home_team",
    )
    away_matches: Mapped[list["MatchModel"]] = relationship(
        foreign_keys="MatchModel.away_team_id",
        back_populates="away_team",
    )
    aliases: Mapped[list["TeamAliasModel"]] = relationship(back_populates="team")
    roster_links: Mapped[list["TeamPlayerModel"]] = relationship(back_populates="team")


class MatchModel(Base):
    __tablename__ = "matches"
    __table_args__ = (
        UniqueConstraint(
            "competition_id",
            "home_team_id",
            "away_team_id",
            "kickoff_at",
            name="uq_matches_fixture_identity",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=generate_id)
    competition_id: Mapped[str] = mapped_column(ForeignKey("competitions.id"), nullable=False)
    home_team_id: Mapped[str] = mapped_column(ForeignKey("teams.id"), nullable=False)
    away_team_id: Mapped[str] = mapped_column(ForeignKey("teams.id"), nullable=False)
    kickoff_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    venue: Mapped[str | None] = mapped_column(String(255))

    competition: Mapped["CompetitionModel"] = relationship(back_populates="matches")
    home_team: Mapped["TeamModel"] = relationship(
        foreign_keys=[home_team_id],
        back_populates="home_matches",
    )
    away_team: Mapped["TeamModel"] = relationship(
        foreign_keys=[away_team_id],
        back_populates="away_matches",
    )
    evidence_items: Mapped[list["EvidenceItemModel"]] = relationship(back_populates="match")
    predictions: Mapped[list["PredictionModel"]] = relationship(back_populates="match")
    slate_links: Mapped[list["ProgolSlateMatchModel"]] = relationship(back_populates="match")
    source_documents: Mapped[list["SourceDocumentModel"]] = relationship(back_populates="matched_match")
    results: Mapped[list["MatchResultModel"]] = relationship(back_populates="match")


class SourceModel(Base):
    __tablename__ = "sources"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=generate_id)
    name: Mapped[str] = mapped_column(String(255), nullable=False, unique=True)
    base_url: Mapped[str] = mapped_column(String(500), nullable=False)
    kind: Mapped[str] = mapped_column(String(80), nullable=False)
    parser_profile: Mapped[str] = mapped_column(String(80), nullable=False, default="generic")
    is_active: Mapped[bool] = mapped_column(default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)
    # Lower number = higher priority when selecting the canonical result.
    # Use this to rank official providers (e.g. football-data.org = 10)
    # above secondary scrapers (default = 50) so the scorer and dataset
    # always prefer the authoritative source when multiple sources agree.
    result_source_priority: Mapped[int] = mapped_column(Integer, nullable=False, default=50)

    evidence_items: Mapped[list["EvidenceItemModel"]] = relationship(back_populates="source")
    ingestion_runs: Mapped[list["IngestionRunModel"]] = relationship(back_populates="source")
    health_checks: Mapped[list["SourceHealthCheckModel"]] = relationship(back_populates="source")
    scheduled_jobs: Mapped[list["ScheduledIngestionJobModel"]] = relationship(back_populates="source")


class EvidenceItemModel(Base):
    __tablename__ = "evidence_items"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=generate_id)
    match_id: Mapped[str] = mapped_column(ForeignKey("matches.id"), nullable=False, index=True)
    source_id: Mapped[str] = mapped_column(ForeignKey("sources.id"), nullable=False, index=True)
    kind: Mapped[str] = mapped_column(String(80), nullable=False)
    captured_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)
    confidence: Mapped[float] = mapped_column(Float, nullable=False)
    summary: Mapped[str] = mapped_column(Text, nullable=False)
    payload_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")

    match: Mapped["MatchModel"] = relationship(back_populates="evidence_items")
    source: Mapped["SourceModel"] = relationship(back_populates="evidence_items")
    availability_items: Mapped[list["PlayerAvailabilityModel"]] = relationship(back_populates="evidence")


class PredictionModel(Base):
    __tablename__ = "predictions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=generate_id)
    match_id: Mapped[str] = mapped_column(ForeignKey("matches.id"), nullable=False, index=True)
    slate_id: Mapped[str | None] = mapped_column(ForeignKey("progol_slates.id"), nullable=True, index=True)
    composition_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    slate_version: Mapped[int | None] = mapped_column(Integer, nullable=True)
    generated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)
    home_probability: Mapped[float] = mapped_column(Float, nullable=False)
    draw_probability: Mapped[float] = mapped_column(Float, nullable=False)
    away_probability: Mapped[float] = mapped_column(Float, nullable=False)
    recommended_outcome: Mapped[str] = mapped_column(String(1), nullable=False)
    confidence_band: Mapped[str] = mapped_column(String(32), nullable=False)
    # v8 audit columns: persist the rationale that gated the band so
    # operators can answer "why was this blocked?" after log rotation.
    competition_readiness: Mapped[str | None] = mapped_column(String(32))
    blocked_reason: Mapped[str | None] = mapped_column(String(120))
    anchors_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    # v18 sanity-audit trace (JSON). Nullable: pre-sanity rows stay NULL
    # rather than inventing a decision that was never taken. Holds the full
    # raw/display/decision/optimizer vectors, flags, evidence/risk/status,
    # sanity_policy_version, model_artifact_id, fallback_used and
    # is_international_friendly so a row is self-describing after the fact.
    # IMPORTANT: home/draw/away_probability above remain the MODEL-adjusted
    # values (the backtesting source) — this column never overwrites them.
    sanity_audit_json: Mapped[str | None] = mapped_column(Text, nullable=True)

    match: Mapped["MatchModel"] = relationship(back_populates="predictions")
    slate: Mapped["ProgolSlateModel | None"] = relationship(back_populates="predictions")


class MatchFeatureSnapshotModel(Base):
    __tablename__ = "match_feature_snapshots"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=generate_id)
    match_id: Mapped[str] = mapped_column(ForeignKey("matches.id"), nullable=False, index=True)
    generated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)
    feature_set_version: Mapped[str] = mapped_column(String(32), nullable=False)
    payload_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")


class TeamStatSnapshotModel(Base):
    __tablename__ = "team_stat_snapshots"
    __table_args__ = (
        UniqueConstraint(
            "team_id",
            "source_id",
            "captured_at",
            "stat_type",
            name="uq_team_stat_snapshot_identity",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=generate_id)
    team_id: Mapped[str] = mapped_column(ForeignKey("teams.id"), nullable=False, index=True)
    source_id: Mapped[str] = mapped_column(ForeignKey("sources.id"), nullable=False, index=True)
    captured_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    stat_type: Mapped[str] = mapped_column(String(80), nullable=False)
    value: Mapped[float] = mapped_column(Float, nullable=False)
    sample_size: Mapped[int] = mapped_column(Integer, nullable=False, default=0)


class MatchStatSnapshotModel(Base):
    __tablename__ = "match_stat_snapshots"
    __table_args__ = (
        UniqueConstraint(
            "match_id",
            "source_id",
            "captured_at",
            "stat_type",
            name="uq_match_stat_snapshot_identity",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=generate_id)
    match_id: Mapped[str] = mapped_column(ForeignKey("matches.id"), nullable=False, index=True)
    source_id: Mapped[str] = mapped_column(ForeignKey("sources.id"), nullable=False, index=True)
    captured_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    stat_type: Mapped[str] = mapped_column(String(80), nullable=False)
    home_value: Mapped[float] = mapped_column(Float, nullable=False)
    away_value: Mapped[float] = mapped_column(Float, nullable=False)


class MatchResultModel(Base):
    __tablename__ = "match_results"
    __table_args__ = (
        UniqueConstraint(
            "match_id",
            "source_id",
            "played_at",
            name="uq_match_result_identity",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=generate_id)
    match_id: Mapped[str] = mapped_column(ForeignKey("matches.id"), nullable=False, index=True)
    source_id: Mapped[str] = mapped_column(ForeignKey("sources.id"), nullable=False, index=True)
    played_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    home_goals: Mapped[int] = mapped_column(Integer, nullable=False)
    away_goals: Mapped[int] = mapped_column(Integer, nullable=False)
    result_code: Mapped[str] = mapped_column(String(1), nullable=False)

    match: Mapped["MatchModel"] = relationship(back_populates="results")


class MatchLiveResultModel(Base):
    """Latest live / partial / final observation per (match_id, source).

    Kept SEPARATE from ``match_results`` so the canonical-final store and
    ``CanonicalResultRepository`` are never polluted by in-progress
    scores. Goals are nullable (a scheduled match has none yet);
    ``result_code`` is only set once both goal fields are known. When an
    observation reaches ``is_final`` the LiveResultService promotes it
    into ``match_results`` as the canonical final result.
    """

    __tablename__ = "match_live_results"
    __table_args__ = (
        UniqueConstraint("match_id", "source_id", name="uq_match_live_identity"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=generate_id)
    match_id: Mapped[str] = mapped_column(ForeignKey("matches.id"), nullable=False, index=True)
    source_id: Mapped[str] = mapped_column(ForeignKey("sources.id"), nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="scheduled")
    home_goals: Mapped[int | None] = mapped_column(Integer, nullable=True)
    away_goals: Mapped[int | None] = mapped_column(Integer, nullable=True)
    result_code: Mapped[str | None] = mapped_column(String(1), nullable=True)
    minute: Mapped[int | None] = mapped_column(Integer, nullable=True)
    is_final: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    observed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc)
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )


class PlayerModel(Base):
    __tablename__ = "players"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=generate_id)
    name: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    normalized_name: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    country: Mapped[str | None] = mapped_column(String(120))
    primary_position: Mapped[str | None] = mapped_column(String(80))

    team_links: Mapped[list["TeamPlayerModel"]] = relationship(back_populates="player")
    availability_items: Mapped[list["PlayerAvailabilityModel"]] = relationship(back_populates="player")


class TeamPlayerModel(Base):
    __tablename__ = "team_players"
    __table_args__ = (
        UniqueConstraint("team_id", "player_id", name="uq_team_player_identity"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=generate_id)
    team_id: Mapped[str] = mapped_column(ForeignKey("teams.id"), nullable=False, index=True)
    player_id: Mapped[str] = mapped_column(ForeignKey("players.id"), nullable=False, index=True)
    squad_role: Mapped[str | None] = mapped_column(String(80))
    is_active: Mapped[bool] = mapped_column(default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)

    team: Mapped["TeamModel"] = relationship(back_populates="roster_links")
    player: Mapped["PlayerModel"] = relationship(back_populates="team_links")


class PlayerAvailabilityModel(Base):
    __tablename__ = "player_availability"
    __table_args__ = (
        UniqueConstraint(
            "match_id",
            "team_id",
            "player_name",
            "status",
            "category",
            "source_id",
            "captured_at",
            name="uq_player_availability_identity",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=generate_id)
    match_id: Mapped[str] = mapped_column(ForeignKey("matches.id"), nullable=False, index=True)
    team_id: Mapped[str] = mapped_column(ForeignKey("teams.id"), nullable=False, index=True)
    player_id: Mapped[str | None] = mapped_column(ForeignKey("players.id"), index=True)
    source_id: Mapped[str] = mapped_column(ForeignKey("sources.id"), nullable=False, index=True)
    evidence_id: Mapped[str | None] = mapped_column(ForeignKey("evidence_items.id"), index=True)
    captured_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)
    status: Mapped[str] = mapped_column(String(40), nullable=False)
    category: Mapped[str] = mapped_column(String(40), nullable=False)
    player_name: Mapped[str] = mapped_column(String(255), nullable=False)
    detail: Mapped[str] = mapped_column(Text, nullable=False, default="")
    confidence: Mapped[float] = mapped_column(Float, nullable=False, default=0.5)
    impact_score: Mapped[float] = mapped_column(Float, nullable=False, default=0.5)
    payload_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")

    player: Mapped["PlayerModel | None"] = relationship(back_populates="availability_items")
    evidence: Mapped["EvidenceItemModel | None"] = relationship(back_populates="availability_items")


class ProgolSlateModel(Base):
    __tablename__ = "progol_slates"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=generate_id)
    label: Mapped[str] = mapped_column(String(255), nullable=False)
    draw_code: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    week_type: Mapped[str] = mapped_column(String(32), nullable=False)
    registration_closes_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    is_archived: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)
    # SHA-256 of the ordered fixture list. Changes whenever the composition
    # of matches for this draw_code is replaced by upsert_slate().
    composition_hash: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    # Incremented each time composition_hash changes for the same draw_code.
    slate_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)

    matches: Mapped[list["ProgolSlateMatchModel"]] = relationship(back_populates="slate")
    ticket_snapshots: Mapped[list["TicketRecommendationSnapshotModel"]] = relationship(back_populates="slate")
    predictions: Mapped[list["PredictionModel"]] = relationship(back_populates="slate")
    jornada_scores: Mapped[list["ProgolJornadaScoreModel"]] = relationship(back_populates="slate")


class ProgolSlateMatchModel(Base):
    __tablename__ = "progol_slate_matches"
    __table_args__ = (
        UniqueConstraint("slate_id", "position", name="uq_progol_slate_position"),
        UniqueConstraint("slate_id", "match_id", name="uq_progol_slate_match"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    slate_id: Mapped[str] = mapped_column(ForeignKey("progol_slates.id"), nullable=False, index=True)
    match_id: Mapped[str] = mapped_column(ForeignKey("matches.id"), nullable=False, index=True)
    position: Mapped[int] = mapped_column(Integer, nullable=False)
    # Knockout / elimination fixtures must produce a winner; the
    # boleta semantics in those positions don't accept "X". The flag
    # lives on the slate-match (not the match itself) because the
    # same fixture pair could appear in a league weekend in one
    # concurso and in a final the next one.
    is_knockout: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    slate: Mapped["ProgolSlateModel"] = relationship(back_populates="matches")
    match: Mapped["MatchModel"] = relationship(back_populates="slate_links")


class TeamAliasModel(Base):
    __tablename__ = "team_aliases"
    __table_args__ = (
        UniqueConstraint("normalized_alias", name="uq_team_alias_normalized"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=generate_id)
    team_id: Mapped[str] = mapped_column(ForeignKey("teams.id"), nullable=False, index=True)
    alias: Mapped[str] = mapped_column(String(255), nullable=False, unique=True, index=True)
    normalized_alias: Mapped[str] = mapped_column(String(255), nullable=False, index=True)

    team: Mapped["TeamModel"] = relationship(back_populates="aliases")


class CompetitionAliasModel(Base):
    __tablename__ = "competition_aliases"
    __table_args__ = (
        UniqueConstraint("normalized_alias", name="uq_competition_alias_normalized"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=generate_id)
    competition_id: Mapped[str] = mapped_column(ForeignKey("competitions.id"), nullable=False, index=True)
    alias: Mapped[str] = mapped_column(String(255), nullable=False, unique=True, index=True)
    normalized_alias: Mapped[str] = mapped_column(String(255), nullable=False, index=True)

    competition: Mapped["CompetitionModel"] = relationship(back_populates="aliases")


class IngestionRunModel(Base):
    __tablename__ = "ingestion_runs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=generate_id)
    source_id: Mapped[str] = mapped_column(ForeignKey("sources.id"), nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False, index=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    documents_found: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    error_message: Mapped[str | None] = mapped_column(Text)

    source: Mapped["SourceModel"] = relationship(back_populates="ingestion_runs")
    documents: Mapped[list["SourceDocumentModel"]] = relationship(back_populates="ingestion_run")


class SourceDocumentModel(Base):
    __tablename__ = "source_documents"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=generate_id)
    ingestion_run_id: Mapped[str] = mapped_column(ForeignKey("ingestion_runs.id"), nullable=False, index=True)
    source_id: Mapped[str] = mapped_column(ForeignKey("sources.id"), nullable=False, index=True)
    external_url: Mapped[str] = mapped_column(String(500), nullable=False)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    summary: Mapped[str] = mapped_column(Text, nullable=False)
    payload_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    normalized_key: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    captured_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    matched_match_id: Mapped[str | None] = mapped_column(ForeignKey("matches.id"), index=True)
    linked_evidence_id: Mapped[str | None] = mapped_column(ForeignKey("evidence_items.id"), index=True)

    ingestion_run: Mapped["IngestionRunModel"] = relationship(back_populates="documents")
    matched_match: Mapped["MatchModel | None"] = relationship(back_populates="source_documents")


class SourceHealthCheckModel(Base):
    __tablename__ = "source_health_checks"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=generate_id)
    source_id: Mapped[str] = mapped_column(ForeignKey("sources.id"), nullable=False, index=True)
    checked_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    latency_ms: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    detail: Mapped[str] = mapped_column(Text, nullable=False, default="")

    source: Mapped["SourceModel"] = relationship(back_populates="health_checks")


class ScheduledIngestionJobModel(Base):
    __tablename__ = "scheduled_ingestion_jobs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=generate_id)
    source_id: Mapped[str] = mapped_column(ForeignKey("sources.id"), nullable=False, index=True)
    job_name: Mapped[str] = mapped_column(String(255), nullable=False, unique=True)
    interval_minutes: Mapped[int] = mapped_column(Integer, nullable=False)
    is_active: Mapped[bool] = mapped_column(default=True, nullable=False, index=True)
    last_run_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    next_run_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)

    source: Mapped["SourceModel"] = relationship(back_populates="scheduled_jobs")


class ModelTrainingRunModel(Base):
    __tablename__ = "model_training_runs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=generate_id)
    model_name: Mapped[str] = mapped_column(String(120), nullable=False, index=True)
    trained_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False, index=True)
    training_sample_size: Mapped[int] = mapped_column(Integer, nullable=False)
    artifact_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")


class TicketRecommendationSnapshotModel(Base):
    __tablename__ = "ticket_recommendation_snapshots"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=generate_id)
    slate_id: Mapped[str] = mapped_column(ForeignKey("progol_slates.id"), nullable=False, index=True)
    generated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False, index=True)
    model_version: Mapped[str] = mapped_column(String(120), nullable=False)
    payload_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    # Copied from the slate's composition_hash at snapshot-generation time.
    # Allows detecting staleness: snapshot.composition_hash != slate.composition_hash.
    composition_hash: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    # Set to False when upsert_slate detects a composition change. Stale
    # snapshots are never returned by latest_for_slate().
    is_valid: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, index=True)
    invalidated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    invalidation_reason: Mapped[str | None] = mapped_column(String(120), nullable=True)

    slate: Mapped["ProgolSlateModel"] = relationship(back_populates="ticket_snapshots")


class ProgolSlateProposalModel(Base):
    """Staging row for an upcoming Progol contest scraped from the
    official LN PDF (Fase 2). One row per (draw_code, source_url) so
    consecutive re-fetches can confirm stability before promotion."""

    __tablename__ = "progol_slate_proposals"
    __table_args__ = (
        UniqueConstraint("draw_code", "source_url", name="uq_progol_proposal_source"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=generate_id)
    draw_code: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    week_type: Mapped[str] = mapped_column(String(32), nullable=False, default="weekend")
    source_name: Mapped[str] = mapped_column(String(120), nullable=False)
    source_url: Mapped[str] = mapped_column(String(500), nullable=False)
    registration_closes_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    payload_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    # Two-stage validation: first observation arrives with status="observed".
    # When a later observation matches the stored payload the row flips to
    # "validated" and becomes promotable. "promoted" means the operator (or
    # auto-promote when enabled) has copied it into progol_slates.
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="observed", index=True)
    observations: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)
    promoted_slate_id: Mapped[str | None] = mapped_column(ForeignKey("progol_slates.id"))


class ProgolJornadaScoreModel(Base):
    """Scoring record for one Progol jornada (slate version).

    One row per (slate_id, composition_hash) — computed each time the
    operator calls POST /scoring/slates/{slate_id}/compute. The row is
    updated in-place on repeated calls so the table accumulates one
    authoritative record per slate version, not one per call.
    """

    __tablename__ = "progol_jornada_scores"
    __table_args__ = (
        UniqueConstraint("slate_id", "composition_hash", name="uq_jornada_score_slate_version"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=generate_id)
    slate_id: Mapped[str] = mapped_column(ForeignKey("progol_slates.id"), nullable=False, index=True)
    draw_code: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    week_type: Mapped[str] = mapped_column(String(32), nullable=False)
    composition_hash: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    slate_version: Mapped[int | None] = mapped_column(Integer, nullable=True)
    total_matches: Mapped[int] = mapped_column(Integer, nullable=False)
    matches_with_results: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    simple_hits: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    simple_hit_rate: Mapped[float | None] = mapped_column(Float, nullable=True)
    ticket_hits: Mapped[int | None] = mapped_column(Integer, nullable=True)
    ticket_hit_rate: Mapped[float | None] = mapped_column(Float, nullable=True)
    brier_score_avg: Mapped[float | None] = mapped_column(Float, nullable=True)
    high_confidence_hits: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    high_confidence_total: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    medium_confidence_hits: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    medium_confidence_total: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    low_confidence_hits: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    low_confidence_total: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    blocked_hits: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    blocked_total: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    details_json: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    computed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False, index=True)
    is_complete: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    slate: Mapped["ProgolSlateModel"] = relationship(back_populates="jornada_scores")
