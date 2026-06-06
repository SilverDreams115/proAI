from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Literal

from pydantic import BaseModel, Field


class Outcome(str, Enum):
    HOME = "1"
    DRAW = "X"
    AWAY = "2"


class EvidenceKind(str, Enum):
    STATISTIC = "statistic"
    NEWS = "news"
    INJURY = "injury"
    SCHEDULE = "schedule"
    MARKET = "market"


class AvailabilityStatus(str, Enum):
    OUT = "out"
    DOUBTFUL = "doubtful"
    SUSPENDED = "suspended"
    AVAILABLE = "available"
    ROTATION_RISK = "rotation_risk"


class Team(BaseModel):
    id: str
    name: str
    country: str | None = None


class Competition(BaseModel):
    id: str
    name: str
    country: str | None = None
    season: str | None = None


class Match(BaseModel):
    id: str
    competition_id: str
    home_team_id: str
    away_team_id: str
    kickoff_at: datetime
    venue: str | None = None


class EvidenceItem(BaseModel):
    id: str
    match_id: str
    source_name: str
    source_url: str
    kind: EvidenceKind
    captured_at: datetime
    confidence: float = Field(ge=0.0, le=1.0)
    summary: str
    payload: dict[str, object] = Field(default_factory=dict)


class Prediction(BaseModel):
    id: str
    match_id: str
    generated_at: datetime
    home_probability: float = Field(ge=0.0, le=1.0)
    draw_probability: float = Field(ge=0.0, le=1.0)
    away_probability: float = Field(ge=0.0, le=1.0)
    recommended_outcome: Outcome
    confidence_band: Literal["low", "medium", "high"]
