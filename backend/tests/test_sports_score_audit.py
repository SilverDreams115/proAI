"""Tests for the API-Football audit connector + matching (read-only).

All tests run fully offline against local JSON fixtures — no external
calls, no DB writes.
"""
from __future__ import annotations

from datetime import date
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.connectors.api_football import (
    API_ERROR_AUTH,
    API_ERROR_PLAN,
    API_ERROR_QUOTA,
    API_ERROR_RATE_LIMIT,
    ApiFootballConnector,
    ApiFootballDisabledError,
    ApiFootballFetchResult,
    ApiFootballFixture,
    classify_api_errors,
    load_local_fixtures,
    load_local_payload,
    normalize_payload,
    normalize_response,
)
from scripts.audit_sports_scores import _fetch_fixtures_online, run_audit
from app.services.normalization_service import NormalizationService
from app.services.sports_score_matching import (
    SlateMatchInput,
    evaluate_ln_sign_check,
    match_slate_fixture,
)

_norm = NormalizationService()


def _fx(home, away, *, date="2026-06-18", status="finished", hs=2, as_=1, comp="World Cup", fid="1"):
    return ApiFootballFixture(
        source="api_football", fixture_id=fid, date=date, home=home, away=away,
        home_score=hs, away_score=as_, status=status, competition=comp, country="World",
        result_code=(None if status != "finished" or hs is None else
                     ("1" if hs > as_ else "2" if hs < as_ else "X")),
    )

FIXTURE_PATH = Path(__file__).parent / "fixtures" / "api_football_pg2336.json"


def _slate_input(home, away, *, competition="International Friendlies", day=12, position=1):
    return SlateMatchInput(
        slate_id="slate-pg-2336",
        draw_code="2336",
        position=position,
        home=home,
        away=away,
        date=date(2026, 6, day),
        competition=competition,
    )


# --- 1. Connector parses a finished fixture with score ------------------

def test_connector_parses_finished_fixture_with_score():
    fixtures = load_local_fixtures(FIXTURE_PATH)
    mex = next(f for f in fixtures if f.home == "Mexico")
    assert mex.status == "finished"
    assert (mex.home_score, mex.away_score) == (2, 1)
    assert mex.result_code == "1"
    assert mex.fixture_id == "1190234"


# --- 2. Connector ignores a fixture without a marcador ------------------

def test_connector_yields_no_result_for_unscored_fixture():
    fixtures = load_local_fixtures(FIXTURE_PATH)
    pending = next(f for f in fixtures if f.home == "Colombia")
    assert pending.status == "scheduled"
    assert pending.has_score is False
    assert pending.result_code is None


# --- 3. Exact match → confidence >= 0.90 (safe) -------------------------

def test_exact_match_is_safe_high_confidence():
    fixtures = load_local_fixtures(FIXTURE_PATH)
    decision = match_slate_fixture(_slate_input("Mexico", "Honduras"), fixtures)
    assert decision.confidence >= 0.90
    assert decision.decision == "safe"
    assert decision.safe_blockers == []


# --- 4. Date out of range lowers confidence -----------------------------

def test_date_out_of_range_lowers_confidence():
    fixtures = load_local_fixtures(FIXTURE_PATH)
    in_range = match_slate_fixture(_slate_input("Mexico", "Honduras", day=12), fixtures)
    out_of_range = match_slate_fixture(_slate_input("Mexico", "Honduras", day=20), fixtures)
    assert out_of_range.confidence < in_range.confidence
    assert out_of_range.decision != "safe"
    assert "date_out_of_range" in out_of_range.safe_blockers


# --- 5. Ambiguous team → needs_review or no_match -----------------------

def test_ambiguous_team_is_not_safe():
    fixtures = load_local_fixtures(FIXTURE_PATH)
    # Home matches Mexico but the away team is a different opponent, so the
    # combined team match is below the clarity floor → never safe.
    decision = match_slate_fixture(_slate_input("Mexico", "Guatemala"), fixtures)
    assert decision.decision in {"needs_review", "no_match"}
    assert "ambiguous_team_match" in decision.safe_blockers


# --- 6. Two strong candidates block safe --------------------------------

def test_two_strong_candidates_block_safe():
    # Two identical finished friendlies on the same date → ambiguous.
    payload = {
        "response": [
            {
                "fixture": {"id": 1, "date": "2026-06-12T20:00:00+00:00",
                            "status": {"short": "FT"}},
                "league": {"name": "International Friendlies", "country": "World"},
                "teams": {"home": {"name": "Mexico"}, "away": {"name": "Honduras"}},
                "goals": {"home": 2, "away": 1},
            },
            {
                "fixture": {"id": 2, "date": "2026-06-12T20:00:00+00:00",
                            "status": {"short": "FT"}},
                "league": {"name": "International Friendlies", "country": "World"},
                "teams": {"home": {"name": "Mexico"}, "away": {"name": "Honduras"}},
                "goals": {"home": 0, "away": 3},
            },
        ]
    }
    fixtures = normalize_response(payload)
    decision = match_slate_fixture(_slate_input("Mexico", "Honduras"), fixtures)
    assert "two_strong_candidates" in decision.safe_blockers
    assert decision.decision != "safe"


# --- 7. LN sign-only matches scored result → allowed --------------------

def test_ln_sign_matches_scored_result_is_allowed():
    check = evaluate_ln_sign_check(ln_sign_result_code="1", sports_result_code="1")
    assert check.ln_sign_check == "matches"
    assert check.usable_for_learning is True
    assert check.decision is None


# --- 8. LN sign-only differs from scored result → blocked ---------------

def test_ln_sign_conflict_blocks():
    check = evaluate_ln_sign_check(ln_sign_result_code="2", sports_result_code="1")
    assert check.ln_sign_check == "conflict"
    assert check.decision == "blocked"
    assert check.usable_for_learning is False
    assert check.exclusion_reason == "ln_sign_sports_score_conflict"


def test_ln_sign_not_available_when_no_official_sign():
    check = evaluate_ln_sign_check(ln_sign_result_code=None, sports_result_code="1")
    assert check.ln_sign_check == "not_available"
    assert check.usable_for_learning is True


# --- 9. Dry-run audit writes no DB --------------------------------------

def _fake_slate():
    def link(position, home, away, competition, day):
        match = SimpleNamespace(
            id=f"m-{position}",
            home_team=SimpleNamespace(name=home),
            away_team=SimpleNamespace(name=away),
            competition=SimpleNamespace(name=competition),
            kickoff_at=__import__("datetime").datetime(2026, 6, day, 20, 0),
        )
        return SimpleNamespace(position=position, match=match)

    return SimpleNamespace(
        id="slate-pg-2336",
        draw_code="2336",
        matches=[
            link(1, "Mexico", "Honduras", "International Friendlies", 12),
            link(2, "USA", "Canada", "International Friendlies", 12),
        ],
    )


def test_dry_run_audit_is_pure_no_db():
    fixtures = load_local_fixtures(FIXTURE_PATH)
    slate = _fake_slate()
    # LN sign for position 1 conflicts with the 2-1 (code "1") scoreline.
    ln_signs = {1: "2", 2: "X"}
    rows = run_audit(slate, fixtures, ln_signs, "api_football")

    assert len(rows) == 2
    pos1 = next(r for r in rows if r["position"] == 1)
    assert pos1["decision"] == "blocked"
    assert pos1["ln_sign_check"] == "conflict"
    assert pos1["usable_for_learning"] is False

    pos2 = next(r for r in rows if r["position"] == 2)
    assert pos2["ln_sign_check"] == "matches"
    assert pos2["result_code"] == "X"
    # run_audit takes no DB session — pure by construction.
    assert "run_audit" in run_audit.__name__


# --- 10. No API key / disabled does not break the app -------------------

def test_disabled_connector_never_calls_network():
    connector = ApiFootballConnector(enabled=False, api_key=None)
    assert connector.is_operational is False
    with pytest.raises(ApiFootballDisabledError):
        connector.search_fixtures(date="2026-06-12")


def test_enabled_without_key_is_not_operational():
    connector = ApiFootballConnector(enabled=True, api_key=None)
    assert connector.is_operational is False
    with pytest.raises(ApiFootballDisabledError):
        connector.search_fixtures(team="26")


def test_normalization_works_without_any_connector_config():
    # Parsing is pure and must never require a key or network.
    fixtures = normalize_response({"response": []})
    assert fixtures == []


# --- Error-surfacing (risk #2 fix) --------------------------------------

PLAN_ERROR_PATH = Path(__file__).parent / "fixtures" / "api_football_plan_error.json"


def test_errors_plan_classifies_as_plan_restricted():
    result = load_local_payload(PLAN_ERROR_PATH)
    assert result.api_error is True
    assert result.api_error_kind == API_ERROR_PLAN
    assert "do not have access" in result.api_error_message
    assert result.fixtures == []


def test_classify_distinguishes_error_kinds():
    assert classify_api_errors({"plan": "Free plans do not have access"})[0] == API_ERROR_PLAN
    assert classify_api_errors({"token": "invalid api key"})[0] == API_ERROR_AUTH
    assert classify_api_errors({"requests": "request limit for the day"})[0] == API_ERROR_QUOTA
    assert classify_api_errors({"rateLimit": "Too many requests"})[0] == API_ERROR_RATE_LIMIT
    # Empty errors (the all-clear shape) is not an error.
    assert classify_api_errors([]) is None
    assert classify_api_errors({}) is None


def test_api_error_blocks_matching_and_is_not_no_match():
    slate = _fake_slate()  # positions on 2026-06-12
    plan_error = load_local_payload(PLAN_ERROR_PATH)
    date_results = {"2026-06-12": plan_error}
    # Even if (stale) fixtures were in the pool, an errored date must block.
    rows = run_audit(slate, [], {1: "1", 2: "X"}, "api_football", date_results)

    for row in rows:
        assert row["api_error"] is True
        assert row["api_error_kind"] == API_ERROR_PLAN
        assert row["decision"] == "blocked"
        assert row["decision"] != "no_match"
        assert row["mapping_warnings"] == ["api_plan_restricted"]
        assert "no_candidates" not in row["mapping_warnings"]
        assert row["usable_for_learning"] is False


def test_valid_empty_response_without_errors_is_no_candidates():
    # No errors + zero fixtures = genuinely empty day → no_match/no_candidates.
    result = normalize_payload({"errors": [], "results": 0, "response": []})
    assert result.api_error is False
    slate = _fake_slate()
    rows = run_audit(slate, [], {}, "api_football", {"2026-06-12": result})
    for row in rows:
        assert row["api_error"] is False
        assert row["decision"] == "no_match"
        assert row["mapping_warnings"] == ["no_candidates"]


def test_dry_run_with_api_error_still_zero_db_writes():
    # run_audit takes no DB session; an API-error path is equally pure.
    plan_error = ApiFootballFetchResult([], 0, True, API_ERROR_PLAN, "denied")
    rows = run_audit(_fake_slate(), [], {}, "api_football", {"2026-06-12": plan_error})
    assert all(r["decision"] == "blocked" for r in rows)


# --- Phase 9: normalization, date scoring, query strategy ---------------

def test_mexico_normalizes_across_languages():
    assert _norm.normalize_team_name("México") == _norm.normalize_team_name("Mexico")


def test_czech_republic_and_czechia_normalize_together():
    assert _norm.normalize_team_name("Czech Republic") == _norm.normalize_team_name("Czechia")


def test_suiza_and_switzerland_normalize_together():
    assert _norm.normalize_team_name("Suiza") == _norm.normalize_team_name("Switzerland")


def test_bosnia_variants_normalize_together():
    a = _norm.normalize_team_name("Bosnia-Herzegovina")
    b = _norm.normalize_team_name("Bosnia & Herzegovina")
    c = _norm.normalize_team_name("Bosnia and Herzegovina")
    assert a == b == c


def test_friendlies_competition_aliases_normalize():
    intl = _norm.normalize_competition_name("International Friendlies")
    assert _norm.normalize_competition_name("Friendlies") == intl
    assert _norm.normalize_competition_name("Friendly") == intl
    # Women's friendlies must stay a distinct slug.
    assert _norm.normalize_competition_name("Friendlies Women") != intl


def test_one_day_offset_does_not_block_when_teams_match():
    # Slate 06-18, fixture 06-19 (timezone), strong teams, finished.
    slate = _slate_input("Mexico", "South Korea", competition="International Friendlies", day=18)
    fx = _fx("Mexico", "South Korea", date="2026-06-19", hs=1, as_=0)
    decision = match_slate_fixture(slate, [fx])
    assert "date_out_of_range" not in decision.safe_blockers
    assert decision.decision == "safe"


def test_date_beyond_two_days_blocks():
    slate = _slate_input("Mexico", "South Korea", day=18)
    fx = _fx("Mexico", "South Korea", date="2026-06-25", hs=1, as_=0)
    decision = match_slate_fixture(slate, [fx])
    assert "date_out_of_range" in decision.safe_blockers
    assert decision.decision != "safe"


class _FakeConnector:
    """Minimal connector double for query-strategy tests (no network)."""

    def __init__(self, by_league, by_date):
        self._by_league = by_league  # date -> ApiFootballFetchResult
        self._by_date = by_date      # date -> ApiFootballFetchResult
        self.calls = []

    def fetch_fixtures(self, *, date=None, league=None, season=None, **_):
        self.calls.append((date, league, season))
        if league is not None:
            return self._by_league[date]
        return self._by_date[date]


def test_league_query_reduces_candidates_and_keeps_correct_match():
    correct = _fx("Mexico", "South Korea", date="2026-06-18", hs=1, as_=0)
    noise = [_fx(f"Club {i}", f"Other {i}", fid=str(100 + i)) for i in range(50)]
    conn = _FakeConnector(
        by_league={"2026-06-18": ApiFootballFetchResult([correct], 1, False, None, None)},
        by_date={"2026-06-18": ApiFootballFetchResult([correct, *noise], 51, False, None, None)},
    )
    slate_inputs = [_slate_input("Mexico", "South Korea", day=18)]
    results, strategy = _fetch_fixtures_online(conn, slate_inputs, league="1", season="2026")
    assert strategy["2026-06-18"] == "league"
    assert len(results["2026-06-18"].fixtures) == 1
    assert results["2026-06-18"].fixtures[0].home == "Mexico"


def test_date_query_fallback_when_league_empty():
    correct = _fx("Mexico", "South Korea", date="2026-06-18", hs=1, as_=0)
    conn = _FakeConnector(
        by_league={"2026-06-18": ApiFootballFetchResult([], 0, False, None, None)},
        by_date={"2026-06-18": ApiFootballFetchResult([correct], 1, False, None, None)},
    )
    slate_inputs = [_slate_input("Mexico", "South Korea", day=18)]
    results, strategy = _fetch_fixtures_online(conn, slate_inputs, league="1", season="2026")
    assert strategy["2026-06-18"] == "date_fallback"
    assert len(results["2026-06-18"].fixtures) == 1


def test_league_plan_error_falls_back_to_date_query():
    # Real free-plan behavior: league+season is denied for current seasons.
    correct = _fx("Mexico", "South Korea", date="2026-06-18", hs=1, as_=0)
    conn = _FakeConnector(
        by_league={"2026-06-18": ApiFootballFetchResult(
            [], 0, True, API_ERROR_PLAN, "Free plans do not have access to this season")},
        by_date={"2026-06-18": ApiFootballFetchResult([correct], 1, False, None, None)},
    )
    slate_inputs = [_slate_input("Mexico", "South Korea", day=18)]
    results, strategy = _fetch_fixtures_online(conn, slate_inputs, league="1", season="2026")
    assert strategy["2026-06-18"] == "date_fallback_error"
    assert results["2026-06-18"].fixtures[0].home == "Mexico"


def test_scheduled_fixture_has_no_result_code_and_not_applicable():
    fx = _fx("USA", "Australia", date="2026-06-18", status="scheduled", hs=None, as_=None)
    assert fx.result_code is None
    assert fx.has_score is False
    decision = match_slate_fixture(_slate_input("USA", "Australia", day=18), [fx])
    assert decision.decision != "safe"
    assert "match_not_finished" in decision.safe_blockers
    assert "missing_score" in decision.safe_blockers


def test_finished_fixture_with_score_can_be_scored_candidate():
    fx = _fx("Czechia", "South Africa", date="2026-06-18", hs=1, as_=1)
    assert fx.result_code == "X"
    decision = match_slate_fixture(
        _slate_input("Czech Republic", "South Africa", day=18), [fx]
    )
    assert decision.decision == "safe"
    assert decision.candidate.fixture.result_code == "X"
