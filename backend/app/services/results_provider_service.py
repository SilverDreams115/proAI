"""R6.3 — Free results provider (read-only dry-run).

A safe, read-only window onto a *free* results source — primarily
football-data.org (v4), with TheSportsDB and a manual public page as
cross-check/backup. It NEVER writes ``match_results``: it fetches what the
provider reports for a slate's fixtures, matches teams to the local slate via
the existing ``NormalizationService`` (so México/Mexico, E.U.A./USA,
Chequia/Czech Republic resolve), and reports coverage + per-match status/score.

Safety contract:

* Default **disabled** (``PROAI_RESULTS_PROVIDER_ENABLED=false``) and
  **dry-run-only** (``PROAI_RESULTS_PROVIDER_DRY_RUN_ONLY=true``). When disabled,
  no network call is made at all.
* No API key in code — the key is read from the environment. A missing key is a
  non-fatal ``unavailable_missing_key`` status, never an exception.
* Applying results is a separate, explicitly-confirmed CLI step; this module
  never writes a row.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Iterable
from urllib.parse import urlencode
from urllib.request import Request

from app.connectors.http import safe_urlopen
from app.core.settings import settings as global_settings
from app.models.tables import ProgolSlateModel
from app.services.normalization_service import NormalizationService

PROVIDER_FOOTBALL_DATA = "football_data_org"
PROVIDER_THESPORTSDB = "thesportsdb"
PROVIDER_MANUAL = "manual_public_page"

# Provider status codes.
STATUS_OK = "ok"
STATUS_DISABLED = "disabled"
STATUS_MISSING_KEY = "unavailable_missing_key"
STATUS_INSUFFICIENT = "insufficient_coverage"
STATUS_ERROR = "provider_error"

_FOOTBALL_DATA_STATUS = {
    "FINISHED": "finished",
    "AWARDED": "finished",
    "IN_PLAY": "in_play",
    "PAUSED": "in_play",
    "SCHEDULED": "scheduled",
    "TIMED": "scheduled",
    "POSTPONED": "scheduled",
    "SUSPENDED": "scheduled",
}

_normalizer = NormalizationService()


@dataclass(frozen=True)
class ProviderMatch:
    home: str
    away: str
    status: str  # finished|scheduled|in_play|unknown
    score: str | None  # e.g. "2-1"
    utc_date: str | None
    competition: str | None = None


def _norm(name: str | None) -> str:
    if not name:
        return ""
    return _normalizer.normalize_team_name(name)


def _match_one(
    home: str | None, away: str | None, provider_matches: Iterable[ProviderMatch]
) -> tuple[ProviderMatch | None, str]:
    """Return (provider_match, confidence) for one local fixture.

    high   — both teams normalise-match (either orientation),
    low    — exactly one side matches,
    none   — no overlap.
    """
    nh, na = _norm(home), _norm(away)
    best: ProviderMatch | None = None
    best_conf = "none"
    for pm in provider_matches:
        ph, pa = _norm(pm.home), _norm(pm.away)
        same = (nh == ph and na == pa) or (nh == pa and na == ph)
        if same and nh and na:
            return pm, "high"
        one = bool(nh) and (nh in (ph, pa)) or bool(na) and (na in (ph, pa))
        if one and best_conf == "none":
            best, best_conf = pm, "low"
    return best, best_conf


def match_slate(slate: ProgolSlateModel, provider_matches: list[ProviderMatch]) -> dict[str, Any]:
    """Pure matcher: map provider matches onto the slate fixtures (no I/O)."""
    rows: list[dict[str, Any]] = []
    matched = 0
    for link in sorted(slate.matches, key=lambda item: item.position):
        home = getattr(link.match.home_team, "name", None)
        away = getattr(link.match.away_team, "name", None)
        pm, confidence = _match_one(home, away, provider_matches)
        if pm is not None and confidence == "high":
            matched += 1
        rows.append(
            {
                "position": link.position,
                "local_match": f"{home} vs {away}",
                "provider_match": f"{pm.home} vs {pm.away}" if pm else None,
                "status": pm.status if pm else "unmatched",
                "score": pm.score if pm else None,
                "confidence": confidence,
            }
        )
    total = len(slate.matches)
    return {
        "matched": matched,
        "total": total,
        "rate": round(matched / total, 4) if total else 0.0,
        "rows": rows,
    }


def _slate_window(slate: ProgolSlateModel) -> tuple[str, str]:
    """Provider query window for a slate's real-world fixtures.

    Kickoffs can be SYNTHETIC (promotion assigns hourly placeholders near the
    cierre when LN's guide carries no times), so a tight ±1d window around them
    misses real matches — PGM-804's World Cup semifinals landed outside it and
    the dry-run reported zero coverage. Anchor the upper bound on both the
    latest kickoff and the registration cierre, plus a margin that covers a
    concurso's actual playing week."""
    def _utc(value: datetime) -> datetime:
        return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value

    kickoffs = [
        _utc(link.match.kickoff_at)
        for link in slate.matches
        if getattr(link.match, "kickoff_at", None) is not None
    ]
    if not kickoffs:
        now = datetime.now(timezone.utc)
        return now.strftime("%Y-%m-%d"), (now + timedelta(days=3)).strftime("%Y-%m-%d")
    anchors = list(kickoffs)
    closes_at = getattr(slate, "registration_closes_at", None)
    if closes_at is not None:
        anchors.append(_utc(closes_at))
    lo = min(kickoffs) - timedelta(days=1)
    hi = max(anchors) + timedelta(days=3)
    return lo.strftime("%Y-%m-%d"), hi.strftime("%Y-%m-%d")


def _parse_football_data(payload: dict[str, Any]) -> list[ProviderMatch]:
    out: list[ProviderMatch] = []
    for item in payload.get("matches", []) or []:
        home = (item.get("homeTeam") or {}).get("name")
        away = (item.get("awayTeam") or {}).get("name")
        raw_status = str(item.get("status") or "").upper()
        status = _FOOTBALL_DATA_STATUS.get(raw_status, "unknown")
        full = (item.get("score") or {}).get("fullTime") or {}
        h, a = full.get("home"), full.get("away")
        score = f"{h}-{a}" if h is not None and a is not None else None
        comp = (item.get("competition") or {}).get("name")
        if home and away:
            out.append(ProviderMatch(home, away, status, score, item.get("utcDate"), comp))
    return out


# The free tier allows ~10 requests/min and its scores are already delayed, so
# a short shared cache per date-window both respects the rate limit and keeps
# fan-out callers (inventory/validation over many slates, the Seguimiento
# overlay) from paying one HTTP round-trip each. 5 minutes matches the
# provider's own freshness.
_FETCH_CACHE_TTL_SECONDS = 300.0


def _fetch_football_data(date_from: str, date_to: str, *, settings=global_settings) -> list[ProviderMatch]:
    from app.services.diagnostic_ttl_cache import cached_diagnostic_report

    def _fetch() -> list[ProviderMatch]:
        base = settings.football_data_base_url.rstrip("/")
        if not base.endswith("/v4"):
            base = base + "/v4"
        query = urlencode({"dateFrom": date_from, "dateTo": date_to})
        url = f"{base}/matches?{query}"
        request = Request(
            url, headers={"X-Auth-Token": settings.football_data_api_key or "", "User-Agent": "proAI/0.1"}
        )
        with safe_urlopen(request, timeout=20) as response:
            payload = json.loads(response.read().decode("utf-8", errors="replace"))
        return _parse_football_data(payload)

    return cached_diagnostic_report(
        "football_data_fetch",
        (date_from, date_to),
        _fetch,
        ttl_seconds=_FETCH_CACHE_TTL_SECONDS,
    )


def provider_configured(provider: str, *, settings=global_settings) -> bool:
    if provider == PROVIDER_FOOTBALL_DATA:
        return bool(settings.football_data_api_key)
    # TheSportsDB free livescores and the manual page need no key but are
    # cross-check only; treat them as not-configured-as-primary here.
    return False


def build_slate_results_dry_run(
    slate: ProgolSlateModel,
    *,
    provider: str | None = None,
    settings=global_settings,
    fetch_fn: Callable[[str, str], list[ProviderMatch]] | None = None,
) -> dict[str, Any]:
    """Read-only provider dry-run for one slate. Never writes a row.

    ``fetch_fn`` is injectable so tests exercise the matcher/coverage logic with
    fixed provider data and zero network I/O.
    """
    provider = provider or settings.results_provider_primary
    total = len(slate.matches)
    base = {
        "mode": "results_provider_dry_run",
        "provider": provider,
        "enabled": bool(settings.results_provider_enabled),
        "dry_run_only": bool(settings.results_provider_dry_run_only),
        "slate": {"slate_id": slate.id, "draw_code": slate.draw_code, "match_count": total},
        "write_safety": {"writes_performed": False, "snapshots_created": False},
    }

    def _empty(status: str, note: str) -> dict[str, Any]:
        return {
            **base,
            "status": status,
            "note": note,
            "coverage": {"matched": 0, "total": total, "rate": 0.0},
            "matches": [
                {
                    "position": link.position,
                    "local_match": f"{getattr(link.match.home_team, 'name', None)} vs "
                    f"{getattr(link.match.away_team, 'name', None)}",
                    "provider_match": None,
                    "status": "unmatched",
                    "score": None,
                    "confidence": "none",
                }
                for link in sorted(slate.matches, key=lambda item: item.position)
            ],
        }

    if not settings.results_provider_enabled:
        return _empty(STATUS_DISABLED, "Results provider disabled (dry-run informativo).")
    if not provider_configured(provider, settings=settings):
        return _empty(STATUS_MISSING_KEY, "API key ausente para el proveedor de resultados.")

    # Fetch provider data (injected fetcher in tests; real HTTP otherwise).
    date_from, date_to = _slate_window(slate)
    try:
        if fetch_fn is not None:
            provider_matches = fetch_fn(date_from, date_to)
        elif provider == PROVIDER_FOOTBALL_DATA:
            provider_matches = _fetch_football_data(date_from, date_to, settings=settings)
        else:
            return _empty(STATUS_MISSING_KEY, "Proveedor no implementado como primario.")
    except Exception as exc:  # noqa: BLE001 — provider failure must never be fatal
        return {**_empty(STATUS_ERROR, f"Fallo del proveedor: {type(exc).__name__}")}

    coverage = match_slate(slate, provider_matches)
    status = STATUS_OK if coverage["matched"] > 0 else STATUS_INSUFFICIENT
    note = (
        "Cobertura disponible."
        if status == STATUS_OK
        else "El proveedor no cubre esta competencia/slate en el plan gratuito."
    )
    return {
        **base,
        "status": status,
        "note": note,
        "window": {"date_from": date_from, "date_to": date_to},
        "coverage": {"matched": coverage["matched"], "total": coverage["total"], "rate": coverage["rate"]},
        "matches": coverage["rows"],
    }


def probe_provider(
    provider: str,
    *,
    settings=global_settings,
    fetch_fn: Callable[[str, str], list[ProviderMatch]] | None = None,
) -> dict[str, Any]:
    """Provider accessibility/coverage probe (read-only, no slate needed)."""
    out: dict[str, Any] = {
        "provider": provider,
        "enabled": bool(settings.results_provider_enabled),
        "dry_run_only": bool(settings.results_provider_dry_run_only),
        "api_key_present": provider_configured(provider, settings=settings),
        "write_safety": {"writes_performed": False},
    }
    if provider != PROVIDER_FOOTBALL_DATA:
        out["status"] = "cross_check_only"
        out["note"] = "Proveedor de respaldo/contraste; no primario."
        return out
    if not provider_configured(provider, settings=settings):
        out["status"] = STATUS_MISSING_KEY
        out["note"] = "Configura PROAI_FOOTBALL_DATA_API_KEY para el probe."
        return out
    if not settings.results_provider_enabled and fetch_fn is None:
        out["status"] = STATUS_DISABLED
        out["note"] = "Habilita PROAI_RESULTS_PROVIDER_ENABLED para consultar en vivo."
        return out
    now = datetime.now(timezone.utc)
    date_from = (now - timedelta(days=2)).strftime("%Y-%m-%d")
    date_to = now.strftime("%Y-%m-%d")
    try:
        fetcher = fetch_fn or (lambda f, t: _fetch_football_data(f, t, settings=settings))
        matches = fetcher(date_from, date_to)
    except Exception as exc:  # noqa: BLE001
        out["status"] = STATUS_ERROR
        out["note"] = f"Fallo del proveedor: {type(exc).__name__}"
        return out
    out["status"] = STATUS_OK
    out["matches_found"] = len(matches)
    out["finished_found"] = sum(1 for m in matches if m.status == "finished")
    out["competitions"] = sorted({m.competition for m in matches if m.competition})
    out["window"] = {"date_from": date_from, "date_to": date_to}
    return out
