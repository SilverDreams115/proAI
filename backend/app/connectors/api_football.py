"""API-Football (api-sports.io v3) read-only connector — AUDIT ONLY.

Unblocks real marcadores for international friendlies whose results the
existing connectors do not cover (PG-2336 / PG-2337 / PGM-799 / PGM-800).
The connector is deliberately *read-only*: it never touches the DB, never
runs in the worker, and never makes an external call unless it is both
``enabled`` and given an API key.

Two responsibilities are kept separate so the parser can be tested
without any network access:

* :func:`normalize_response` / :func:`normalize_fixture` — pure functions
  that turn an API-Football ``/fixtures`` payload into the internal
  :class:`ApiFootballFixture` model. Tests exercise these against local
  JSON fixtures.
* :class:`ApiFootballConnector` — wraps the pure parser with guarded HTTP
  access. ``search_*`` methods raise :class:`ApiFootballDisabledError`
  when the connector is disabled or unconfigured, so a misconfigured
  deploy fails loud instead of silently hitting the wire.

Internal normalized model (one fixture):

    {
        "source": "api_football",
        "fixture_id": "...",
        "date": "2026-06-12",
        "home": "...",
        "away": "...",
        "home_score": 2,
        "away_score": 1,
        "status": "finished",
        "competition": "...",
        "country": "...",
        "result_code": "1"
    }
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import Request

from app.connectors.http import safe_urlopen as urlopen

SOURCE_NAME = "api_football"
DEFAULT_BASE_URL = "https://v3.football.api-sports.io"

# api-sports.io fixture status short codes → coarse lifecycle bucket.
# Only "finished" statuses carry an authoritative final scoreline that an
# apply step would ever be allowed to trust.
_FINISHED_CODES = {"FT", "AET", "PEN"}
_LIVE_CODES = {"1H", "HT", "2H", "ET", "BT", "P", "LIVE", "INT", "SUSP"}
_SCHEDULED_CODES = {"TBD", "NS"}
_POSTPONED_CODES = {"PST"}
_CANCELLED_CODES = {"CANC", "ABD", "AWD", "WO"}

# Classified API-Football error kinds. A non-empty ``errors`` field (or an
# HTTP 4xx) means the provider DENIED the request — which must never be
# confused with a genuinely empty result set ("no fixtures that day").
API_ERROR_PLAN = "api_error_plan_restricted"
API_ERROR_AUTH = "api_error_auth"
API_ERROR_QUOTA = "api_error_quota"
API_ERROR_RATE_LIMIT = "api_error_rate_limit"
API_ERROR_UNKNOWN = "api_error_unknown"

_MAX_ERROR_MESSAGE_LEN = 300


class ApiFootballError(RuntimeError):
    """Base error for the API-Football connector."""


class ApiFootballDisabledError(ApiFootballError):
    """Raised when an external call is attempted while disabled/unconfigured."""


@dataclass(frozen=True, slots=True)
class ApiFootballFixture:
    """Internal normalized representation of one API-Football fixture."""

    source: str
    fixture_id: str
    date: str | None
    home: str | None
    away: str | None
    home_score: int | None
    away_score: int | None
    status: str
    competition: str | None
    country: str | None
    result_code: str | None
    league_id: str | None = None

    @property
    def is_finished(self) -> bool:
        return self.status == "finished"

    @property
    def has_score(self) -> bool:
        return self.home_score is not None and self.away_score is not None

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def _status_bucket(short_code: str | None) -> str:
    code = (short_code or "").strip().upper()
    if code in _FINISHED_CODES:
        return "finished"
    if code in _LIVE_CODES:
        return "live"
    if code in _SCHEDULED_CODES:
        return "scheduled"
    if code in _POSTPONED_CODES:
        return "postponed"
    if code in _CANCELLED_CODES:
        return "cancelled"
    return "unknown"


def _result_code(home_score: int | None, away_score: int | None) -> str | None:
    if home_score is None or away_score is None:
        return None
    if home_score > away_score:
        return "1"
    if home_score < away_score:
        return "2"
    return "X"


def _parse_int(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _date_only(raw: Any) -> str | None:
    if not raw:
        return None
    text = str(raw).strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        # api-sports.io always returns ISO-8601, but fall back to the
        # leading YYYY-MM-DD rather than dropping the fixture entirely.
        return text[:10] if len(text) >= 10 else None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc).date().isoformat()


def normalize_fixture(item: dict[str, Any]) -> ApiFootballFixture:
    """Normalize one element of an API-Football ``/fixtures`` response."""
    fixture = item.get("fixture") or {}
    league = item.get("league") or {}
    teams = item.get("teams") or {}
    goals = item.get("goals") or {}

    home_team = (teams.get("home") or {}).get("name")
    away_team = (teams.get("away") or {}).get("name")
    home_score = _parse_int(goals.get("home"))
    away_score = _parse_int(goals.get("away"))
    status = _status_bucket((fixture.get("status") or {}).get("short"))

    # Only a finished match carries an authoritative result_code. A live
    # or scheduled fixture never produces one, even if partial goals are
    # present, so a downstream apply can't mistake an in-progress score
    # for a final marcador.
    code = _result_code(home_score, away_score) if status == "finished" else None

    return ApiFootballFixture(
        source=SOURCE_NAME,
        fixture_id=str(fixture.get("id") or ""),
        date=_date_only(fixture.get("date")),
        home=home_team.strip() if isinstance(home_team, str) else home_team,
        away=away_team.strip() if isinstance(away_team, str) else away_team,
        home_score=home_score,
        away_score=away_score,
        status=status,
        competition=(league.get("name") or None),
        country=(league.get("country") or None),
        result_code=code,
        league_id=str(league.get("id")) if league.get("id") is not None else None,
    )


def normalize_response(payload: dict[str, Any] | list[Any]) -> list[ApiFootballFixture]:
    """Normalize a full API-Football ``/fixtures`` payload.

    Accepts the raw provider envelope (``{"response": [...]}``) or a bare
    list of fixture objects, so local JSON fixtures can be stored in
    either shape. Elements without team names are dropped.
    """
    if isinstance(payload, dict):
        items = payload.get("response") or []
    else:
        items = payload
    fixtures: list[ApiFootballFixture] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        normalized = normalize_fixture(item)
        if not normalized.home or not normalized.away:
            continue
        fixtures.append(normalized)
    return fixtures


def _sanitize_error_message(message: str) -> str:
    collapsed = " ".join(str(message).split())
    if len(collapsed) > _MAX_ERROR_MESSAGE_LEN:
        return collapsed[:_MAX_ERROR_MESSAGE_LEN] + "…"
    return collapsed


def classify_api_errors(errors: Any) -> tuple[str, str] | None:
    """Classify a non-empty API-Football ``errors`` field.

    Returns ``(kind, sanitized_message)`` or ``None`` when there is no
    error. API-Football returns ``errors`` as an empty list ``[]`` when
    all is well, or a dict keyed by the failing concern (``plan`` /
    ``token`` / ``requests`` / ``rateLimit``) when it denies the request.
    """
    if not errors:
        return None
    if isinstance(errors, dict):
        items = list(errors.items())
    elif isinstance(errors, list):
        items = [("error", str(item)) for item in errors]
    else:
        items = [("error", str(errors))]
    if not items:
        return None

    keys = " ".join(str(k).lower() for k, _ in items)
    message = "; ".join(f"{k}: {v}" for k, v in items)
    text = f"{keys} {message}".lower()

    if "plan" in keys or "do not have access" in text or "not have access" in text:
        kind = API_ERROR_PLAN
    elif "token" in keys or "invalid api key" in text or "api key" in text:
        kind = API_ERROR_AUTH
    elif "rate" in keys or "too many requests" in text or "rate limit" in text:
        kind = API_ERROR_RATE_LIMIT
    elif "request" in keys or "quota" in text or "limit for the day" in text or "daily" in text:
        kind = API_ERROR_QUOTA
    else:
        kind = API_ERROR_UNKNOWN
    return kind, _sanitize_error_message(message)


def _kind_from_http_status(status: int) -> str:
    if status in (401, 403):
        return API_ERROR_AUTH
    if status == 429:
        return API_ERROR_RATE_LIMIT
    return API_ERROR_UNKNOWN


@dataclass(frozen=True, slots=True)
class ApiFootballFetchResult:
    """Outcome of a fixtures fetch — fixtures OR a classified API error.

    ``api_error`` is the single field a caller must branch on: when true,
    ``fixtures`` is empty *because the provider denied the request*, not
    because the day had no matches. ``results`` mirrors the provider's
    own count (``None`` when the call never returned a body).
    """

    fixtures: list[ApiFootballFixture]
    results: int | None
    api_error: bool
    api_error_kind: str | None
    api_error_message: str | None


@dataclass(frozen=True, slots=True)
class ApiFootballTeamCandidate:
    """One normalized candidate from API-Football ``/teams``."""

    team_id: int
    name: str
    country: str | None
    national: bool | None

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class ApiFootballTeamFetchResult:
    """Outcome of a team search — candidates OR a classified API error."""

    candidates: list[ApiFootballTeamCandidate]
    results: int | None
    api_error: bool
    api_error_kind: str | None
    api_error_message: str | None


def _normalize_team_candidates(payload: dict[str, Any] | list[Any]) -> list[ApiFootballTeamCandidate]:
    if isinstance(payload, dict):
        items = payload.get("response") or []
    else:
        items = payload
    candidates: list[ApiFootballTeamCandidate] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        team = item.get("team") or {}
        team_id = _parse_int(team.get("id"))
        name = team.get("name")
        if team_id is None or not isinstance(name, str) or not name.strip():
            continue
        candidates.append(
            ApiFootballTeamCandidate(
                team_id=team_id,
                name=name.strip(),
                country=(team.get("country") or None),
                national=team.get("national") if isinstance(team.get("national"), bool) else None,
            )
        )
    return candidates


def normalize_team_payload(payload: dict[str, Any] | list[Any]) -> ApiFootballTeamFetchResult:
    """Normalize a ``/teams`` payload, preserving provider denials."""
    errors = payload.get("errors") if isinstance(payload, dict) else None
    classified = classify_api_errors(errors)
    if classified is not None:
        kind, message = classified
        results = payload.get("results") if isinstance(payload, dict) else None
        return ApiFootballTeamFetchResult(
            candidates=[],
            results=results,
            api_error=True,
            api_error_kind=kind,
            api_error_message=message,
        )
    candidates = _normalize_team_candidates(payload)
    results = payload.get("results") if isinstance(payload, dict) else len(candidates)
    return ApiFootballTeamFetchResult(
        candidates=candidates,
        results=results,
        api_error=False,
        api_error_kind=None,
        api_error_message=None,
    )


def normalize_payload(payload: dict[str, Any] | list[Any]) -> ApiFootballFetchResult:
    """Normalize a payload, surfacing any ``errors`` as a classified error."""
    errors = payload.get("errors") if isinstance(payload, dict) else None
    classified = classify_api_errors(errors)
    if classified is not None:
        kind, message = classified
        results = payload.get("results") if isinstance(payload, dict) else None
        return ApiFootballFetchResult(
            fixtures=[],
            results=results,
            api_error=True,
            api_error_kind=kind,
            api_error_message=message,
        )
    fixtures = normalize_response(payload)
    results = payload.get("results") if isinstance(payload, dict) else len(fixtures)
    return ApiFootballFetchResult(
        fixtures=fixtures,
        results=results,
        api_error=False,
        api_error_kind=None,
        api_error_message=None,
    )


def load_local_fixtures(path: str | Path) -> list[ApiFootballFixture]:
    """Load and normalize fixtures from a local JSON file (no network)."""
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    return normalize_response(raw)


def load_local_payload(path: str | Path) -> ApiFootballFetchResult:
    """Load a local JSON file as a fetch result (surfaces ``errors``)."""
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    return normalize_payload(raw)


class ApiFootballConnector:
    """Read-only API-Football client. Never writes the DB.

    Construct via :meth:`from_settings` for the configured singleton, or
    directly for tests. When ``enabled`` is false or ``api_key`` is empty
    the ``search_*`` methods raise :class:`ApiFootballDisabledError` —
    they never silently reach the network.
    """

    def __init__(
        self,
        *,
        enabled: bool = False,
        api_key: str | None = None,
        base_url: str | None = None,
        timeout: int = 20,
    ) -> None:
        self.enabled = enabled
        self.api_key = api_key
        self.base_url = (base_url or DEFAULT_BASE_URL).rstrip("/")
        self.timeout = timeout

    @classmethod
    def from_settings(cls, settings: Any) -> "ApiFootballConnector":
        return cls(
            enabled=getattr(settings, "apifootball_enabled", False),
            api_key=getattr(settings, "apifootball_api_key", None),
            base_url=getattr(settings, "apifootball_base_url", None),
        )

    @property
    def is_operational(self) -> bool:
        """True only when an external call is actually permitted."""
        return bool(self.enabled and self.api_key)

    # ---- search (network-guarded) -------------------------------------

    @staticmethod
    def _fixtures_params(
        *,
        date: str | None,
        team: str | int | None,
        league: str | int | None,
        season: str | int | None,
        fixture_id: str | int | None,
        from_date: str | None,
        to_date: str | None,
    ) -> dict[str, str]:
        params: dict[str, str] = {}
        if fixture_id is not None:
            params["id"] = str(fixture_id)
        if date:
            params["date"] = date
        if from_date:
            params["from"] = from_date
        if to_date:
            params["to"] = to_date
        if team is not None:
            params["team"] = str(team)
        if league is not None:
            params["league"] = str(league)
        if season is not None:
            params["season"] = str(season)
        if not params:
            raise ValueError("a fixtures query requires at least one filter.")
        return params

    def fetch_fixtures(
        self,
        *,
        date: str | None = None,
        team: str | int | None = None,
        league: str | int | None = None,
        season: str | int | None = None,
        fixture_id: str | int | None = None,
        from_date: str | None = None,
        to_date: str | None = None,
    ) -> ApiFootballFetchResult:
        """Query ``/fixtures`` and surface fixtures OR a classified error.

        Never confuses an API denial with an empty day: a non-empty
        ``errors`` field or an HTTP 4xx is returned as
        ``api_error=True`` with a ``kind`` and sanitized message. Raises
        :class:`ApiFootballDisabledError` when not operational.
        """
        params = self._fixtures_params(
            date=date,
            team=team,
            league=league,
            season=season,
            fixture_id=fixture_id,
            from_date=from_date,
            to_date=to_date,
        )
        try:
            payload = self._get("/fixtures", params)
        except HTTPError as exc:
            return ApiFootballFetchResult(
                fixtures=[],
                results=None,
                api_error=True,
                api_error_kind=_kind_from_http_status(exc.code),
                api_error_message=_sanitize_error_message(f"HTTP {exc.code}: {exc.reason}"),
            )
        return normalize_payload(payload)

    def search_fixtures(
        self,
        *,
        date: str | None = None,
        team: str | int | None = None,
        league: str | int | None = None,
        season: str | int | None = None,
        fixture_id: str | int | None = None,
        from_date: str | None = None,
        to_date: str | None = None,
    ) -> list[ApiFootballFixture]:
        """Convenience wrapper returning only the fixtures list.

        Prefer :meth:`fetch_fixtures` when you need to distinguish an API
        error from an empty result. Raises
        :class:`ApiFootballDisabledError` when not operational.
        """
        return self.fetch_fixtures(
            date=date,
            team=team,
            league=league,
            season=season,
            fixture_id=fixture_id,
            from_date=from_date,
            to_date=to_date,
        ).fixtures

    def fetch_team_candidates(self, name: str) -> ApiFootballTeamFetchResult:
        """Query ``/teams?search=`` and surface candidates OR an API error."""
        try:
            payload = self._get("/teams", {"search": name})
        except HTTPError as exc:
            return ApiFootballTeamFetchResult(
                candidates=[],
                results=None,
                api_error=True,
                api_error_kind=_kind_from_http_status(exc.code),
                api_error_message=_sanitize_error_message(f"HTTP {exc.code}: {exc.reason}"),
            )
        return normalize_team_payload(payload)

    def search_team_id(self, name: str) -> list[dict[str, Any]]:
        """Resolve a team name to API-Football team candidates (raw).

        Kept raw because team resolution is an operator-assisted step in
        the audit flow, not part of the scored matching.
        """
        payload = self._get("/teams", {"search": name})
        response = payload.get("response") if isinstance(payload, dict) else None
        return list(response or [])

    # ---- internal -----------------------------------------------------

    def _get(self, path: str, params: dict[str, str]) -> dict[str, Any]:
        if not self.is_operational:
            raise ApiFootballDisabledError(
                "API-Football connector is disabled or missing an API key; "
                "no external call was made."
            )
        url = f"{self.base_url}{path}?{urlencode(params)}"
        request = Request(
            url,
            headers={
                # api-sports.io direct host uses x-apisports-key; the
                # RapidAPI gateway uses x-rapidapi-key. Send both so a
                # single key works behind either base_url.
                "x-apisports-key": self.api_key or "",
                "x-rapidapi-key": self.api_key or "",
                "User-Agent": "proAI/0.1 (+https://local.proai)",
            },
        )
        with urlopen(request, timeout=self.timeout) as response:
            body = response.read().decode("utf-8", errors="replace")
        return json.loads(body)
