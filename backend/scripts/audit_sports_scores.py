"""Dry-run audit: match a Progol slate against scored sports results.

READ-ONLY. Never writes the DB, never applies a result, never trains.
Built to unblock real marcadores for international friendlies the current
connectors don't cover (PG-2336 / PG-2337 / PGM-799 / PGM-800).

Pipeline per slate position::

    slate match  ->  candidate sports fixtures  ->  scored match decision
                 ->  LN sign-only cross-check    ->  per-match audit row

Fixtures come from a local JSON file (default, offline) or, only with the
explicit ``--online`` flag AND an enabled+keyed API-Football connector,
from a live query. The local-file path is the safe default so the audit
runs with zero external calls.

Usage::

    python backend/scripts/audit_sports_scores.py \\
        --slate-id <id> --source api_football --dry-run \\
        --fixtures-file fixtures.json [--json-out sports_score_audit.json]
"""
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict
from pathlib import Path
from typing import Any

# Make `app` importable when run as a bare script from the repo root.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.connectors.api_football import (  # noqa: E402
    API_ERROR_AUTH,
    API_ERROR_PLAN,
    API_ERROR_QUOTA,
    API_ERROR_RATE_LIMIT,
    API_ERROR_UNKNOWN,
    ApiFootballConnector,
    ApiFootballFetchResult,
    ApiFootballFixture,
    load_local_payload,
    normalize_response,
)
from app.services.sports_score_matching import (  # noqa: E402
    SlateMatchInput,
    evaluate_ln_sign_check,
    match_slate_fixture,
)

SUPPORTED_SOURCES = {"api_football"}

# An API error is NOT a "no_match" — surface it as a distinct, blocking
# warning so a plan/auth/quota denial can never be read as "no fixtures".
_API_KIND_WARNING = {
    API_ERROR_PLAN: "api_plan_restricted",
    API_ERROR_AUTH: "api_auth_error",
    API_ERROR_QUOTA: "api_quota_exceeded",
    API_ERROR_RATE_LIMIT: "api_rate_limited",
    API_ERROR_UNKNOWN: "api_error",
}


def _fetch_fixtures_online(
    connector: ApiFootballConnector,
    slate_inputs: list[SlateMatchInput],
    league: str | None = None,
    season: str | None = None,
) -> tuple[dict[str, ApiFootballFetchResult], dict[str, str]]:
    """Query API-Football once per distinct slate date (online mode).

    Strategy (Part 4): when a ``league`` id is supplied, query
    ``/fixtures?date=&league=&season=`` to cut the ~100+ global fixtures
    down to the relevant competition. If the league-filtered query
    returns an empty (non-error) set for a date, fall back to the broad
    ``date`` query so a correct match is never lost to an over-narrow
    filter. Returns ``(per_date_results, per_date_strategy)``.
    """
    results: dict[str, ApiFootballFetchResult] = {}
    strategy: dict[str, str] = {}
    for item in slate_inputs:
        if item.date is None:
            continue
        key = item.date.isoformat()
        if key in results:
            continue
        if league is not None:
            filtered = connector.fetch_fixtures(date=key, league=league, season=season)
            if filtered.fixtures:
                results[key] = filtered
                strategy[key] = "league"
            else:
                # The league filter yielded nothing usable — an empty set
                # OR an API denial (the free plan blocks league+season for
                # current seasons). Fall back to the broad date query so a
                # correct match is never lost to an unavailable filter
                # (Part 4 rule 5).
                results[key] = connector.fetch_fixtures(date=key)
                strategy[key] = "date_fallback_error" if filtered.api_error else "date_fallback"
        else:
            results[key] = connector.fetch_fixtures(date=key)
            strategy[key] = "date"
    return results, strategy


def _fixtures_pool(date_results: dict[str, ApiFootballFetchResult]) -> list[ApiFootballFixture]:
    pool: list[ApiFootballFixture] = []
    for result in date_results.values():
        pool.extend(result.fixtures)
    return pool


def _count_on_date(fixtures: list[ApiFootballFixture], date_iso: str | None) -> int:
    if date_iso is None:
        return 0
    return sum(1 for fx in fixtures if fx.date == date_iso)


def _build_slate_inputs(slate: Any) -> list[SlateMatchInput]:
    inputs: list[SlateMatchInput] = []
    for link in sorted(slate.matches, key=lambda m: m.position):
        match = link.match
        kickoff = match.kickoff_at
        inputs.append(
            SlateMatchInput(
                slate_id=slate.id,
                draw_code=slate.draw_code,
                position=link.position,
                home=match.home_team.name,
                away=match.away_team.name,
                date=kickoff.date() if kickoff is not None else None,
                competition=match.competition.name if match.competition else None,
            )
        )
    return inputs


def _base_row(slate_input: SlateMatchInput, source: str) -> dict[str, Any]:
    return {
        "draw_code": slate_input.draw_code,
        "position": slate_input.position,
        "home": slate_input.home,
        "away": slate_input.away,
        "date": slate_input.date.isoformat() if slate_input.date else None,
        "competition": slate_input.competition,
        "sports_source": source,
    }


def _row_for(
    slate_input: SlateMatchInput,
    decision_obj: Any,
    ln_sign: str | None,
    ln_check: Any,
    source: str,
    candidate_count: int,
) -> dict[str, Any]:
    cand = decision_obj.candidate
    fx = cand.fixture if cand else None
    sports_result_code = fx.result_code if fx else None

    # An LN/sports conflict is authoritative: it overrides the match
    # decision and blocks the row for learning.
    decision = decision_obj.decision
    if ln_check.decision == "blocked":
        decision = "blocked"

    row = _base_row(slate_input, source)
    row.update(
        {
            "api_error": False,
            "api_error_kind": None,
            "api_error_message": None,
            "candidate_count": candidate_count,
            "fixture_id": fx.fixture_id if fx else None,
            "candidate_home": fx.home if fx else None,
            "candidate_away": fx.away if fx else None,
            "home_score": fx.home_score if fx else None,
            "away_score": fx.away_score if fx else None,
            "status": fx.status if fx else None,
            "result_code": sports_result_code,
            "confidence": decision_obj.confidence,
            "decision": decision,
            "ln_sign": ln_sign,
            "ln_sign_check": ln_check.ln_sign_check,
            "usable_for_learning": ln_check.usable_for_learning,
            "exclusion_reason": ln_check.exclusion_reason,
            "team_match_score": cand.team_match_score if cand else None,
            "date_score": cand.date_score if cand else None,
            "competition_score": cand.competition_score if cand else None,
            "home_away_orientation_score": cand.home_away_orientation_score if cand else None,
            "safe_blockers": decision_obj.safe_blockers,
            "mapping_warnings": decision_obj.mapping_warnings,
        }
    )
    return row


def _error_row(
    slate_input: SlateMatchInput,
    probe: ApiFootballFetchResult,
    ln_sign: str | None,
    source: str,
) -> dict[str, Any]:
    """Row for a position whose date the API DENIED — blocked, not no_match.

    Matching is never run for this position: we don't know whether the
    day had fixtures, only that access was refused. ``candidate_count``
    mirrors the provider count (often 0/None on a denial) but the
    ``api_error`` flag is what a caller must branch on.
    """
    warning = _API_KIND_WARNING.get(probe.api_error_kind or API_ERROR_UNKNOWN, "api_error")
    row = _base_row(slate_input, source)
    row.update(
        {
            "api_error": True,
            "api_error_kind": probe.api_error_kind,
            "api_error_message": probe.api_error_message,
            "candidate_count": probe.results,
            "fixture_id": None,
            "candidate_home": None,
            "candidate_away": None,
            "home_score": None,
            "away_score": None,
            "status": None,
            "result_code": None,
            "confidence": None,
            "decision": "blocked",
            "ln_sign": ln_sign,
            "ln_sign_check": "not_available",
            "usable_for_learning": False,
            "exclusion_reason": warning,
            "team_match_score": None,
            "date_score": None,
            "competition_score": None,
            "home_away_orientation_score": None,
            "safe_blockers": [],
            "mapping_warnings": [warning],
        }
    )
    return row


def run_audit(
    slate: Any,
    fixtures: list[ApiFootballFixture],
    ln_signs: dict[int, str | None],
    source: str,
    date_results: dict[str, ApiFootballFetchResult] | None = None,
) -> list[dict[str, Any]]:
    """Pure audit: produce one row per slate position. No DB writes.

    When ``date_results`` is provided and a position's date carries an
    API error, that position is reported as ``blocked`` with the API
    error surfaced — matching is skipped so a denial is never mistaken
    for ``no_match``.
    """
    rows: list[dict[str, Any]] = []
    for slate_input in _build_slate_inputs(slate):
        date_iso = slate_input.date.isoformat() if slate_input.date else None
        probe = date_results.get(date_iso) if (date_results and date_iso) else None
        ln_sign = ln_signs.get(slate_input.position)

        if probe is not None and probe.api_error:
            rows.append(_error_row(slate_input, probe, ln_sign, source))
            continue

        decision_obj = match_slate_fixture(slate_input, fixtures)
        cand = decision_obj.candidate
        sports_result_code = cand.fixture.result_code if cand else None
        ln_check = evaluate_ln_sign_check(ln_sign, sports_result_code)
        candidate_count = _count_on_date(fixtures, date_iso)
        rows.append(
            _row_for(slate_input, decision_obj, ln_sign, ln_check, source, candidate_count)
        )
    return rows


def _date_summaries(
    slate_inputs: list[SlateMatchInput],
    fixtures: list[ApiFootballFixture],
    date_results: dict[str, ApiFootballFetchResult] | None,
) -> list[dict[str, Any]]:
    """Per-date candidate_count + API error, in slate-date order."""
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in slate_inputs:
        if item.date is None:
            continue
        date_iso = item.date.isoformat()
        if date_iso in seen:
            continue
        seen.add(date_iso)
        probe = date_results.get(date_iso) if date_results else None
        if probe is not None and probe.api_error:
            out.append(
                {
                    "date": date_iso,
                    "candidate_count": probe.results,
                    "api_error": True,
                    "api_error_kind": probe.api_error_kind,
                    "api_error_message": probe.api_error_message,
                }
            )
        else:
            out.append(
                {
                    "date": date_iso,
                    "candidate_count": _count_on_date(fixtures, date_iso),
                    "api_error": False,
                    "api_error_kind": None,
                    "api_error_message": None,
                }
            )
    return out


def _ln_signs_for_slate(session: Any, slate: Any) -> dict[int, str | None]:
    """Read LN sign-only result_code per position from match_live_results.

    Read-only: uses LiveResultService.status_for_matches, which surfaces a
    sign-only final (no goals) stored in ``match_live_results``.
    """
    from app.services.live_result_service import LiveResultService

    match_ids = [link.match.id for link in slate.matches]
    normalized = LiveResultService(session).status_for_matches(match_ids)
    out: dict[int, str | None] = {}
    for link in slate.matches:
        result = normalized.get(link.match.id)
        out[link.position] = result.result_code if result else None
    return out


def _summary(rows: list[dict[str, Any]]) -> dict[str, int]:
    summary = {"safe": 0, "needs_review": 0, "no_match": 0, "blocked": 0, "api_error": 0}
    for row in rows:
        summary[row["decision"]] = summary.get(row["decision"], 0) + 1
        if row.get("api_error"):
            summary["api_error"] += 1
    return summary


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--slate-id", required=True, help="Target slate id.")
    parser.add_argument(
        "--source", default="api_football", choices=sorted(SUPPORTED_SOURCES)
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="No-op flag for clarity; the audit is always read-only.",
    )
    parser.add_argument(
        "--fixtures-file",
        help="Local JSON file of sports fixtures (API-Football response or list).",
    )
    parser.add_argument(
        "--online",
        action="store_true",
        help="Allow a live API-Football query (requires enabled connector + key).",
    )
    parser.add_argument(
        "--league",
        help="API-Football league id to narrow the online query (e.g. 1). "
        "Falls back to the date-only query per date if it returns empty.",
    )
    parser.add_argument(
        "--season",
        help="API-Football season for the --league filter (e.g. 2026).",
    )
    parser.add_argument("--json-out", help="Optional path to write the audit JSON.")
    args = parser.parse_args(argv)

    from app.db.session import SessionLocal
    from app.repositories.slate_repository import SlateRepository

    session = SessionLocal()
    try:
        slate = SlateRepository(session).get_slate(args.slate_id)
        if slate is None:
            raise SystemExit(f"Slate {args.slate_id} not found.")

        slate_inputs = _build_slate_inputs(slate)
        date_results: dict[str, ApiFootballFetchResult] | None = None
        strategy: dict[str, str] = {}
        if args.fixtures_file:
            payload_result = load_local_payload(args.fixtures_file)
            if payload_result.api_error:
                # A local fixture that is itself an API-error envelope:
                # surface the denial for every slate date.
                fixtures = []
                date_results = {
                    item.date.isoformat(): payload_result
                    for item in slate_inputs
                    if item.date is not None
                }
            else:
                fixtures = payload_result.fixtures
            strategy = {"mode": "file"}
        elif args.online:
            from app.core.settings import load_settings

            connector = ApiFootballConnector.from_settings(load_settings())
            if not connector.is_operational:
                raise SystemExit(
                    "--online requires PROAI_APIFOOTBALL_ENABLED=true and an API key."
                )
            date_results, strategy = _fetch_fixtures_online(
                connector, slate_inputs, args.league, args.season
            )
            fixtures = _fixtures_pool(date_results)
        else:
            raise SystemExit(
                "Provide --fixtures-file <path> (offline) or --online (live query)."
            )

        ln_signs = _ln_signs_for_slate(session, slate)
        rows = run_audit(slate, fixtures, ln_signs, args.source, date_results)
    finally:
        session.close()

    report = {
        "slate_id": slate.id,
        "draw_code": slate.draw_code,
        "source": args.source,
        "dry_run": True,
        "db_writes": 0,
        "candidate_count": len(fixtures),
        "query": {
            "league": args.league,
            "season": args.season,
            "strategy_by_date": strategy,
        },
        "dates": _date_summaries(slate_inputs, fixtures, date_results),
        "summary": _summary(rows),
        "rows": rows,
    }

    print(json.dumps(report, ensure_ascii=False, indent=2))
    if args.json_out:
        try:
            Path(args.json_out).write_text(
                json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            print(f"\n[dry-run] Audit JSON escrito en {args.json_out}")
        except OSError as exc:
            # Non-fatal: stdout above is the source of truth. A read-only
            # or uid-mismatched mount must not fail the read-only audit.
            print(f"\n[dry-run] No se pudo escribir {args.json_out} ({exc}); usa stdout.")
    print("\n[dry-run] 0 escrituras en DB. Nada aplicado, nada entrenado.")
    return 0


# `normalize_response`/`asdict` re-exported for ad-hoc use in a REPL.
__all__ = ["main", "run_audit", "normalize_response", "asdict"]


if __name__ == "__main__":
    raise SystemExit(main())
