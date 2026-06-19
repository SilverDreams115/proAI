"""Read-only API-Football team-id fixture diagnosis for one Progol slate.

This is an operator diagnostic, not an apply path. It reads slate matches
from the DB, queries API-Football only when ``--online`` is explicit, and
prints a JSON report. It never writes the DB, never applies scores, and
never trains.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from dataclasses import dataclass
from datetime import date as date_cls
from datetime import datetime
from datetime import timedelta
from pathlib import Path
from typing import Any

# Make `app` importable when run as a bare script from the repo root.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.connectors.api_football import (  # noqa: E402
    API_ERROR_PLAN,
    ApiFootballConnector,
    ApiFootballFetchResult,
    ApiFootballFixture,
    ApiFootballTeamCandidate,
    ApiFootballTeamFetchResult,
)
from app.services.normalization_service import NormalizationService  # noqa: E402
from app.services.sports_score_matching import SlateMatchInput  # noqa: E402
from app.services.sports_score_matching import score_candidate  # noqa: E402

AUDIT_WINDOW_DAYS = 1
DIAGNOSTIC_WINDOW_DAYS = 7
SEASON = "2026"
DIAGNOSTIC_SEARCH_ALIASES = {
    "jordania": "Jordan",
}

_normalizer = NormalizationService()


@dataclass(frozen=True, slots=True)
class DiagnosticPosition:
    slate_id: str
    draw_code: str | None
    position: int
    match_id: str
    home: str
    away: str
    kickoff_at: datetime | None
    competition: str | None

    @property
    def expected_date(self) -> date_cls | None:
        return self.kickoff_at.date() if self.kickoff_at else None


@dataclass(frozen=True, slots=True)
class ResolvedTeam:
    team_id: int | None
    status: str
    candidates_top5: list[dict[str, Any]]


def _canonical_team(value: str | None) -> str:
    if not value:
        return ""
    base = _normalizer.normalize_team_name(value)
    alias = DIAGNOSTIC_SEARCH_ALIASES.get(base)
    return _normalizer.normalize_team_name(alias) if alias else base


def _team_search_terms(value: str) -> list[str]:
    terms = [value]
    alias = DIAGNOSTIC_SEARCH_ALIASES.get(_normalizer.normalize_team_name(value))
    if alias and alias not in terms:
        terms.append(alias)
    return terms


def _team_compatible(expected: str | None, actual: str | None) -> bool:
    return bool(expected and actual and _canonical_team(expected) == _canonical_team(actual))


def _candidate_dict(candidate: ApiFootballTeamCandidate) -> dict[str, Any]:
    return {
        "team_id": candidate.team_id,
        "name": candidate.name,
        "country": candidate.country,
        "national": candidate.national,
    }


def resolve_team_id(
    expected_name: str,
    candidates: list[ApiFootballTeamCandidate],
) -> ResolvedTeam:
    """Resolve only when exactly one candidate has the expected canonical name."""
    top5 = [_candidate_dict(candidate) for candidate in candidates[:5]]
    expected = _canonical_team(expected_name)
    exact = [candidate for candidate in candidates if _canonical_team(candidate.name) == expected]
    if len(exact) == 1:
        return ResolvedTeam(team_id=exact[0].team_id, status="resolved", candidates_top5=top5)
    if len(exact) > 1:
        return ResolvedTeam(team_id=None, status="team_ambiguous", candidates_top5=top5)
    return ResolvedTeam(team_id=None, status="team_not_found", candidates_top5=top5)


def _fixture_key(fixture: ApiFootballFixture) -> str:
    return fixture.fixture_id or f"{fixture.date}:{fixture.home}:{fixture.away}"


def _dedupe_fixtures(fixtures: list[ApiFootballFixture]) -> list[ApiFootballFixture]:
    deduped: dict[str, ApiFootballFixture] = {}
    for fixture in fixtures:
        deduped[_fixture_key(fixture)] = fixture
    return list(deduped.values())


def _date_distance(expected: date_cls | None, fixture: ApiFootballFixture) -> int:
    if expected is None or not fixture.date:
        return 99_999
    try:
        actual = datetime.fromisoformat(fixture.date).date()
    except ValueError:
        return 99_999
    return abs((actual - expected).days)


def _fixture_match_kind(pos: DiagnosticPosition, fixture: ApiFootballFixture) -> str:
    direct = _team_compatible(pos.home, fixture.home) and _team_compatible(pos.away, fixture.away)
    swapped = _team_compatible(pos.home, fixture.away) and _team_compatible(pos.away, fixture.home)
    if direct:
        return "both_teams"
    if swapped:
        return "both_teams_swapped"
    one_side = any(
        (
            _team_compatible(pos.home, fixture.home),
            _team_compatible(pos.home, fixture.away),
            _team_compatible(pos.away, fixture.home),
            _team_compatible(pos.away, fixture.away),
        )
    )
    return "single_team_only" if one_side else "no_team_match"


def _best_fixture(
    pos: DiagnosticPosition,
    fixtures: list[ApiFootballFixture],
) -> tuple[ApiFootballFixture | None, str | None]:
    matches: list[tuple[int, int, ApiFootballFixture, str]] = []
    singles: list[tuple[int, ApiFootballFixture]] = []
    for fixture in fixtures:
        kind = _fixture_match_kind(pos, fixture)
        distance = _date_distance(pos.expected_date, fixture)
        if kind in {"both_teams", "both_teams_swapped"}:
            orientation_penalty = 0 if kind == "both_teams" else 1
            matches.append((distance, orientation_penalty, fixture, kind))
        elif kind == "single_team_only":
            singles.append((distance, fixture))
    if matches:
        distance, orientation_penalty, fixture, kind = sorted(matches, key=lambda item: item[:2])[0]
        return fixture, kind
    if singles:
        _distance, fixture = sorted(singles, key=lambda item: item[0])[0]
        return fixture, "single_team_only"
    return None, None


def _window(expected: date_cls | None, days: int) -> tuple[str | None, str | None]:
    if expected is None:
        return None, None
    return (expected - timedelta(days=days)).isoformat(), (expected + timedelta(days=days)).isoformat()


def _api_error_dict(label: str, result: ApiFootballFetchResult | ApiFootballTeamFetchResult) -> dict[str, Any]:
    return {
        "endpoint": label,
        "kind": result.api_error_kind,
        "message": result.api_error_message,
    }


def _collect_api_errors(
    items: list[tuple[str, ApiFootballFetchResult | ApiFootballTeamFetchResult | None]],
) -> list[dict[str, Any]]:
    return [
        _api_error_dict(label, result)
        for label, result in items
        if result is not None and result.api_error
    ]


def _reason_if_not_found(
    *,
    home: ResolvedTeam,
    away: ResolvedTeam,
    fixture_kind: str | None,
    api_errors: list[dict[str, Any]],
) -> str | None:
    if fixture_kind in {"both_teams", "both_teams_swapped"}:
        return None
    if any(error.get("kind") == API_ERROR_PLAN for error in api_errors):
        return "plan_blocked"
    if home.status == "team_ambiguous" or away.status == "team_ambiguous":
        return "team_ambiguous"
    if home.status == "team_not_found" or away.status == "team_not_found":
        return "team_not_found"
    if fixture_kind == "single_team_only":
        return "single_team_only"
    return "provider_missing"


def _conclusion(row: dict[str, Any]) -> str:
    if row["fixture_found"]:
        return (
            "fixture_found_outside_window"
            if row["fixture_found_outside_audit_window"]
            else "fixture_found_inside_window"
        )
    reason = row.get("reason_if_not_found")
    if reason == "plan_blocked":
        return "plan_blocked"
    if reason == "team_ambiguous":
        return "team_ambiguous"
    if reason == "single_team_only":
        return "single_team_only"
    return "provider_missing"


def diagnose_position(
    pos: DiagnosticPosition,
    *,
    home_search: ApiFootballTeamFetchResult,
    away_search: ApiFootballTeamFetchResult,
    home_fixtures_window: ApiFootballFetchResult | None,
    away_fixtures_window: ApiFootballFetchResult | None,
    home_fixtures_season: ApiFootballFetchResult | None = None,
    away_fixtures_season: ApiFootballFetchResult | None = None,
) -> dict[str, Any]:
    home = resolve_team_id(pos.home, home_search.candidates)
    away = resolve_team_id(pos.away, away_search.candidates)
    from_date, to_date = _window(pos.expected_date, DIAGNOSTIC_WINDOW_DAYS)

    fixture_sources = [
        result
        for result in (
            home_fixtures_window,
            away_fixtures_window,
            home_fixtures_season,
            away_fixtures_season,
        )
        if result is not None and not result.api_error
    ]
    fixtures = _dedupe_fixtures([fixture for result in fixture_sources for fixture in result.fixtures])
    fixture, fixture_kind = _best_fixture(pos, fixtures)
    found = fixture is not None and fixture_kind in {"both_teams", "both_teams_swapped"}

    api_errors = _collect_api_errors(
        [
            ("teams:home", home_search),
            ("teams:away", away_search),
            ("fixtures_window:home", home_fixtures_window),
            ("fixtures_window:away", away_fixtures_window),
            ("fixtures_season:home", home_fixtures_season),
            ("fixtures_season:away", away_fixtures_season),
        ]
    )
    reason = _reason_if_not_found(
        home=home,
        away=away,
        fixture_kind=fixture_kind,
        api_errors=api_errors,
    )

    confidence = None
    if fixture is not None:
        slate_input = SlateMatchInput(
            slate_id=pos.slate_id,
            draw_code=pos.draw_code,
            position=pos.position,
            home=pos.home,
            away=pos.away,
            date=pos.expected_date,
            competition=pos.competition,
        )
        confidence = score_candidate(slate_input, fixture).overall_confidence

    outside = False
    if found and fixture is not None:
        outside = _date_distance(pos.expected_date, fixture) > AUDIT_WINDOW_DAYS

    row = {
        "pos": pos.position,
        "home": pos.home,
        "away": pos.away,
        "db_kickoff_at": pos.kickoff_at.isoformat() if pos.kickoff_at else None,
        "db_expected_date": pos.expected_date.isoformat() if pos.expected_date else None,
        "home_team_candidates_from_api_top5": home.candidates_top5,
        "away_team_candidates_from_api_top5": away.candidates_top5,
        "resolved_home_team_id": home.team_id,
        "resolved_away_team_id": away.team_id,
        "fixture_search_window": {"from": from_date, "to": to_date},
        "fixture_found": found,
        "fixture_found_outside_audit_window": outside,
        "fixture_id": fixture.fixture_id if found and fixture else None,
        "api_date": fixture.date if found and fixture else None,
        "api_home": fixture.home if found and fixture else None,
        "api_away": fixture.away if found and fixture else None,
        "api_league_id": fixture.league_id if found and fixture else None,
        "api_league_name": fixture.competition if found and fixture else None,
        "api_status": fixture.status if found and fixture else None,
        "score": (
            f"{fixture.home_score}-{fixture.away_score}"
            if found and fixture and fixture.home_score is not None and fixture.away_score is not None
            else None
        ),
        "result_code": fixture.result_code if found and fixture else None,
        "confidence": confidence if found else None,
        "reason_if_not_found": reason,
        "api_errors": api_errors,
    }
    row["conclusion"] = _conclusion(row)
    return row


def _empty_fixture_result() -> ApiFootballFetchResult:
    return ApiFootballFetchResult([], 0, False, None, None)


def _sleep_between_requests(seconds: float) -> None:
    if seconds > 0:
        time.sleep(seconds)


def _fetch_team_search(
    connector: ApiFootballConnector,
    team_name: str,
    request_delay_seconds: float,
) -> ApiFootballTeamFetchResult:
    last = ApiFootballTeamFetchResult([], 0, False, None, None)
    for term in _team_search_terms(team_name):
        last = connector.fetch_team_candidates(term)
        _sleep_between_requests(request_delay_seconds)
        if last.api_error or last.candidates:
            return last
    return last


def _fetch_window_fixtures(
    connector: ApiFootballConnector,
    *,
    team_id: int,
    from_date: str,
    to_date: str,
    plan_cache: dict[str, ApiFootballFetchResult],
    request_delay_seconds: float,
) -> ApiFootballFetchResult:
    cache_key = f"team-season-window:{SEASON}"
    if cache_key in plan_cache:
        return plan_cache[cache_key]
    result = connector.fetch_fixtures(
        team=team_id,
        season=SEASON,
        from_date=from_date,
        to_date=to_date,
    )
    _sleep_between_requests(request_delay_seconds)
    if result.api_error and result.api_error_kind == API_ERROR_PLAN:
        plan_cache[cache_key] = result
    return result


def fetch_and_diagnose_position(
    pos: DiagnosticPosition,
    connector: ApiFootballConnector,
    *,
    plan_cache: dict[str, ApiFootballFetchResult] | None = None,
    request_delay_seconds: float = 0,
) -> dict[str, Any]:
    if plan_cache is None:
        plan_cache = {}
    home_search = _fetch_team_search(connector, pos.home, request_delay_seconds)
    away_search = _fetch_team_search(connector, pos.away, request_delay_seconds)
    home = resolve_team_id(pos.home, home_search.candidates)
    away = resolve_team_id(pos.away, away_search.candidates)
    from_date, to_date = _window(pos.expected_date, DIAGNOSTIC_WINDOW_DAYS)

    home_window = _empty_fixture_result()
    away_window = _empty_fixture_result()

    if home.team_id is not None and from_date and to_date:
        home_window = _fetch_window_fixtures(
            connector,
            team_id=home.team_id,
            from_date=from_date,
            to_date=to_date,
            plan_cache=plan_cache,
            request_delay_seconds=request_delay_seconds,
        )
    if away.team_id is not None and from_date and to_date:
        away_window = _fetch_window_fixtures(
            connector,
            team_id=away.team_id,
            from_date=from_date,
            to_date=to_date,
            plan_cache=plan_cache,
            request_delay_seconds=request_delay_seconds,
        )

    return diagnose_position(
        pos,
        home_search=home_search,
        away_search=away_search,
        home_fixtures_window=home_window,
        away_fixtures_window=away_window,
    )


def _parse_positions(raw: str) -> set[int]:
    return {int(part.strip()) for part in raw.split(",") if part.strip()}


def _positions_from_slate(slate: Any, positions: set[int]) -> list[DiagnosticPosition]:
    out: list[DiagnosticPosition] = []
    for link in sorted(slate.matches, key=lambda item: item.position):
        if link.position not in positions:
            continue
        match = link.match
        out.append(
            DiagnosticPosition(
                slate_id=slate.id,
                draw_code=slate.draw_code,
                position=link.position,
                match_id=match.id,
                home=match.home_team.name,
                away=match.away_team.name,
                kickoff_at=match.kickoff_at,
                competition=match.competition.name if match.competition else None,
            )
        )
    return out


def build_report(
    *,
    slate_id: str,
    draw_code: str | None,
    source: str,
    rows: list[dict[str, Any]],
) -> dict[str, Any]:
    return {
        "slate_id": slate_id,
        "draw_code": draw_code,
        "source": source,
        "dry_run": True,
        "db_writes": 0,
        "diagnostic_window_days": DIAGNOSTIC_WINDOW_DAYS,
        "audit_window_days": AUDIT_WINDOW_DAYS,
        "rows": rows,
        "conclusions": {str(row["pos"]): row["conclusion"] for row in rows},
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--slate-id", required=True)
    parser.add_argument("--positions", required=True, help="Comma-separated slate positions.")
    parser.add_argument("--source", default="api_football", choices=["api_football"])
    parser.add_argument("--online", action="store_true")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="No-op flag for clarity; this diagnostic is always read-only.",
    )
    parser.add_argument(
        "--request-delay-seconds",
        type=float,
        default=0,
        help="Sleep between API calls to avoid provider rate limits.",
    )
    args = parser.parse_args(argv)

    if not args.online:
        raise SystemExit("This diagnostic needs --online; no local fixture mode is implemented.")

    from app.core.settings import load_settings
    from app.db.session import SessionLocal
    from app.repositories.slate_repository import SlateRepository

    connector = ApiFootballConnector.from_settings(load_settings())
    if not connector.is_operational:
        raise SystemExit("--online requires PROAI_APIFOOTBALL_ENABLED=true and an API key.")

    session = SessionLocal()
    try:
        slate = SlateRepository(session).get_slate(args.slate_id)
        if slate is None:
            raise SystemExit(f"Slate {args.slate_id} not found.")
        positions = _parse_positions(args.positions)
        diagnostic_positions = _positions_from_slate(slate, positions)
        plan_cache: dict[str, ApiFootballFetchResult] = {}
        rows = [
            fetch_and_diagnose_position(
                pos,
                connector,
                plan_cache=plan_cache,
                request_delay_seconds=args.request_delay_seconds,
            )
            for pos in diagnostic_positions
        ]
        report = build_report(
            slate_id=slate.id,
            draw_code=slate.draw_code,
            source=args.source,
            rows=rows,
        )
    finally:
        session.rollback()
        session.close()

    print(json.dumps(report, ensure_ascii=False, indent=2))
    print("\n[dry-run] 0 escrituras en DB. Nada aplicado, nada entrenado.")
    return 0


__all__ = [
    "DiagnosticPosition",
    "build_report",
    "diagnose_position",
    "fetch_and_diagnose_position",
    "main",
    "resolve_team_id",
]


if __name__ == "__main__":
    raise SystemExit(main())
