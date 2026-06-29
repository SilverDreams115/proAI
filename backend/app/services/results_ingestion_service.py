"""Map parsed Progol official results onto slate matches and feed them
to :class:`LiveResultService`.

Mapping is strict: the document's CONCURSO number must match the slate's
draw_code digits, and each result row is mapped to a match purely by
**position** (the Progol casillero index). Team names are compared only to
surface a mismatch in the report — they never drive the mapping, so a
fuzzy name match can't silently feed the wrong match. Pending rows (no
score, no sign) are skipped, never invented.

The connector fetches the LN document; this service is the parse →
map → record step, callable from the operator endpoint and the worker.
"""
from __future__ import annotations

import re
import unicodedata
from datetime import datetime, timezone
from typing import Any

from sqlalchemy.orm import Session

from app.connectors.progol_resultados import parse_progol_resultados_text
from app.models.tables import ProgolSlateModel, SourceModel
from app.repositories.source_repository import SourceRepository
from app.services.live_result_service import LiveResultService

# LN official results outrank scraped feeds (TSDB=50) when both report a
# final, so the canonical result resolves to the official marcador.
RESULTS_SOURCE_NAME = "LN Progol Resultados"
RESULTS_SOURCE_PRIORITY = 40
RESULTS_SOURCE_KIND = "progol_resultados"
RESULTS_SOURCE_BASE_URL = "https://www.loterianacional.gob.mx/Progol/Resultados"

# Operator-provided results (e.g. a marcador the operator typed in from a
# screenshot) are real and traceable, but deliberately rank BELOW the LN
# official acta: a later LN ingest at priority 40 outranks them, and a
# disagreement surfaces as a conflict (CanonicalResultRepository) rather
# than silently overwriting. Still well above "nothing" so they score now.
OPERATOR_SOURCE_PRIORITY = 60
OPERATOR_SOURCE_KIND = "operator_manual"


class ResultsIngestionService:
    def __init__(self, session: Session) -> None:
        self.session = session
        self.live = LiveResultService(session)

    def ensure_source(
        self,
        base_url: str | None = None,
        *,
        name: str = RESULTS_SOURCE_NAME,
        kind: str = RESULTS_SOURCE_KIND,
        priority: int = RESULTS_SOURCE_PRIORITY,
    ) -> SourceModel:
        repo = SourceRepository(self.session)
        existing = repo.get_by_name(name)
        if existing is not None:
            if base_url:
                existing.base_url = base_url
            existing.result_source_priority = priority
            existing.is_active = True
            self.session.add(existing)
            self.session.flush()
            return existing
        source = SourceModel(
            name=name,
            base_url=base_url or RESULTS_SOURCE_BASE_URL,
            kind=kind,
            parser_profile="generic",
            is_active=True,
            result_source_priority=priority,
        )
        self.session.add(source)
        self.session.flush()
        return source

    def ingest_for_slate(
        self,
        slate: ProgolSlateModel,
        text: str,
        *,
        source_url: str | None = None,
        observed_at: datetime | None = None,
        source_name: str = RESULTS_SOURCE_NAME,
        source_kind: str = RESULTS_SOURCE_KIND,
        source_priority: int = RESULTS_SOURCE_PRIORITY,
    ) -> dict[str, Any]:
        observed_at = observed_at or datetime.now(timezone.utc)
        draw_code, rows = parse_progol_resultados_text(text)

        slate_digits = _trailing_digits(slate.draw_code)
        if draw_code is not None and slate_digits is not None and draw_code != slate_digits:
            return {
                "slate_id": slate.id,
                "draw_code": slate.draw_code,
                "error": "draw_code_mismatch",
                "parsed_concurso": draw_code,
                "expected_concurso": slate_digits,
                "recorded": 0,
            }

        source = self.ensure_source(
            source_url, name=source_name, kind=source_kind, priority=source_priority
        )
        pos_map = {sm.position: sm for sm in slate.matches}

        recorded = 0
        finals = 0
        live = 0
        skipped_pending = 0
        unmapped_positions: list[int] = []
        team_mismatches: list[dict[str, Any]] = []

        for row in rows:
            sm = pos_map.get(row.position)
            if sm is None:
                unmapped_positions.append(row.position)
                continue
            has_signal = (
                row.result_code is not None
                or row.home_goals is not None
                or row.away_goals is not None
            )
            if not has_signal:
                skipped_pending += 1
                continue
            if row.home and _team_mismatch(row.home, row.away, sm):
                team_mismatches.append(
                    {
                        "position": row.position,
                        "parsed": f"{row.home} vs {row.away}",
                        "slate": f"{sm.match.home_team.name} vs {sm.match.away_team.name}",
                    }
                )
            self.live.record_observation(
                match_id=sm.match_id,
                source_id=source.id,
                status=row.status,
                home_goals=row.home_goals,
                away_goals=row.away_goals,
                minute=row.minute,
                is_final=row.is_final,
                result_code=row.result_code,
                observed_at=observed_at,
            )
            recorded += 1
            if row.is_final:
                finals += 1
            elif row.status.value in {"live", "halftime"}:
                live += 1

        return {
            "slate_id": slate.id,
            "draw_code": slate.draw_code,
            "parsed_concurso": draw_code,
            "source": source.name,
            "source_url": source_url or source.base_url,
            "observed_at": observed_at,
            "rows_parsed": len(rows),
            "recorded": recorded,
            "finals": finals,
            "live": live,
            "skipped_pending": skipped_pending,
            "unmapped_positions": unmapped_positions,
            "team_mismatches": team_mismatches,
        }


def _trailing_digits(draw_code: str) -> str | None:
    match = re.search(r"(\d+)$", draw_code or "")
    return match.group(1) if match else None


def _normalize(name: str) -> str:
    decomposed = unicodedata.normalize("NFKD", name)
    ascii_only = decomposed.encode("ascii", "ignore").decode("ascii")
    return re.sub(r"[^a-z0-9]", "", ascii_only.lower())


def _team_mismatch(parsed_home: str, parsed_away: str | None, slate_match: Any) -> bool:
    """True when neither parsed team shares a token-prefix with the slate
    match — a heuristic *report only*, never used to remap.
    """
    slate_home = _normalize(slate_match.match.home_team.name)
    slate_away = _normalize(slate_match.match.away_team.name)
    p_home = _normalize(parsed_home)
    p_away = _normalize(parsed_away or "")
    home_ok = _shares_prefix(p_home, slate_home)
    away_ok = _shares_prefix(p_away, slate_away)
    return not (home_ok or away_ok)


def _shares_prefix(a: str, b: str, *, n: int = 4) -> bool:
    if not a or not b:
        return False
    return a.startswith(b[:n]) or b.startswith(a[:n])
