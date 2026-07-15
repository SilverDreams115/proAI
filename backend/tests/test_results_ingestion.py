"""Progol official-results parser + ingestion onto slates.

Covers:
  - parser for PGM (9) and PG (14) result documents (score / sign / live /
    pending lines),
  - mapping strictly by draw_code + casillero position,
  - draw_code mismatch is rejected (no cross-concurso contamination),
  - pending matches are skipped, never invented,
  - final results promote to canonical and let scoring complete (only when
    all matches are final),
  - real draws are detected end-to-end,
  - sign-only finals record an outcome without a scoreline.
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest
from sqlalchemy.orm import Session

from app.connectors.progol_resultados import parse_progol_resultados_text
from app.domain.entities import MatchResultStatus
from app.services.live_result_service import LiveResultService
from app.services.live_results_service import (
    LiveResultsService,
    finalize_complete_closed_slates,
)
from app.services.results_ingestion_service import ResultsIngestionService

# Reuse the live-results seeding helpers.
from tests.test_live_results import _seed_slate, _match_ids, _future, _past, _make_official  # noqa: E402


@pytest.fixture
def db(tmp_path):
    from app.db import session as db_mod
    from app.db.migrations import run_migrations
    from app.db.session import configure_session

    configure_session(f"sqlite:///{tmp_path / 'results_ing.db'}")
    run_migrations(db_mod.engine)
    with Session(db_mod.engine) as session:
        yield session


# --------------------------------------------------------------------------
# Parser
# --------------------------------------------------------------------------

PG_TEXT = """
PROGOL CONCURSO 2336
RESULTADOS OFICIALES
1  MEXICO 2-1 SUDAFRICA  FINAL
2  COREA 0-0 R CHECA FINAL
3  CANADA 3-0 BOSNIA FINAL
4  USA 1-1 PARAGUAY FINAL
5  BRASIL 0-2 MARRUECOS FINAL
6  AUSTRALIA 1-0 TURQUIA FINAL
7  PAISES BAJOS 2-2 JAPON FINAL
8  COSTA MARFIL 3-1 ECUADOR FINAL
9  SUECIA 1-0 TUNEZ FINAL
10 BELGICA 1-1 EGIPTO FINAL
11 IRAN 2-0 N ZELANDA FINAL
12 FRANCIA 1-1 SENEGAL FINAL
13 INGLATERRA 1-1 CROACIA EN VIVO 75
14 GHANA vs PANAMA PENDIENTE
"""

PGM_TEXT = """
PROGOL MEDIA SEMANA CONCURSO 799
RESULTADOS
1  MEXICO 2-1 SUDAFRICA FINAL
2  COREA 0-0 R CHECA FINAL
3  CANADA 3-0 BOSNIA FINAL
4  USA 1-1 PARAGUAY FINAL
5  BRASIL 0-2 MARRUECOS FINAL
6  AUSTRALIA 1-0 TURQUIA FINAL
7  PAISES BAJOS 2-2 JAPON FINAL
8  COSTA MARFIL 3-1 ECUADOR FINAL
9  SUECIA 1-0 TUNEZ FINAL
"""


def test_parser_pg_extracts_draw_code_and_rows():
    draw_code, rows = parse_progol_resultados_text(PG_TEXT)
    assert draw_code == "2336"
    assert len(rows) == 14
    assert rows[0].position == 1
    assert (rows[0].home_goals, rows[0].away_goals) == (2, 1)
    assert rows[0].result_code == "1" and rows[0].is_final is True
    # Draw at position 2.
    assert rows[1].result_code == "X"
    # Live at 13, pending at 14.
    assert rows[12].status == MatchResultStatus.LIVE and rows[12].is_final is False
    assert rows[12].minute == 75
    assert rows[13].status == MatchResultStatus.SCHEDULED
    assert rows[13].home_goals is None


def test_parser_pgm_nine_rows():
    draw_code, rows = parse_progol_resultados_text(PGM_TEXT)
    assert draw_code == "799"
    assert len(rows) == 9
    assert all(r.is_final for r in rows)


def test_parser_sign_only():
    draw_code, rows = parse_progol_resultados_text("CONCURSO 800\n1 L\n2 E\n3 V\n")
    assert draw_code == "800"
    assert [r.result_code for r in rows] == ["1", "X", "2"]
    assert all(r.is_final and r.home_goals is None for r in rows)


def test_parser_ln_html_historical_combo_sign_only():
    html = """
    <table>
      <tbody>
        <tr>
          <td class="text-center">2341</td>
          <td class="text-center">12/07/2026</td>
          <td class="text-center">E E L V V L E L V L V L L E</td>
        </tr>
      </tbody>
    </table>
    """

    draw_code, rows = parse_progol_resultados_text(html)

    assert draw_code == "2341"
    assert len(rows) == 14
    assert [row.result_code for row in rows[:4]] == ["X", "X", "1", "2"]
    assert all(row.is_final and row.home_goals is None for row in rows)


# --------------------------------------------------------------------------
# Mapping + ingestion
# --------------------------------------------------------------------------

def test_ingest_maps_by_position_into_live_results(db):
    slate = _seed_slate(db, draw_code="PG-2336", n=14, closes_at=_past(),
                        outcomes=["1"] * 14, draw_probs=[0.25] * 14)
    report = ResultsIngestionService(db).ingest_for_slate(slate, PG_TEXT)
    assert report["recorded"] == 13  # 12 final + 1 live; position 14 pending skipped
    assert report["finals"] == 12
    assert report["live"] == 1
    assert report["skipped_pending"] == 1
    db.commit()

    payload = LiveResultsService(db).build_live_results(slate)
    assert payload["completed_count"] == 12
    assert payload["live_count"] == 1
    assert payload["pending_count"] == 1
    assert payload["is_complete"] is False
    pos2 = next(m for m in payload["matches"] if m["position"] == 2)
    assert pos2["result_code"] == "X" and pos2["draw_was_real"] is True


def test_ingest_rejects_draw_code_mismatch(db):
    slate = _seed_slate(db, draw_code="PG-2337", n=14, closes_at=_future())
    report = ResultsIngestionService(db).ingest_for_slate(slate, PG_TEXT)  # doc is 2336
    assert report["error"] == "draw_code_mismatch"
    assert report["recorded"] == 0
    db.commit()
    payload = LiveResultsService(db).build_live_results(slate)
    assert payload["completed_count"] == 0  # nothing fed


def test_ingest_does_not_invent_pending(db):
    slate = _seed_slate(db, draw_code="PG-2336", n=14, closes_at=_past())
    ResultsIngestionService(db).ingest_for_slate(slate, PG_TEXT)
    db.commit()
    # Position 14 was PENDIENTE — no observation should exist for it.
    ids = _match_ids(slate)
    status = LiveResultService(db).status_for_matches([ids[13]])
    assert ids[13] not in status  # truly pending, not fabricated


def test_full_results_let_pgm_complete_and_persist(db):
    from app.repositories.jornada_score_repository import JornadaScoreRepository

    slate = _seed_slate(db, draw_code="PGM-799", week_type="midweek", n=9,
                        closes_at=_past(), outcomes=["1"] * 9, draw_probs=[0.25] * 9)
    _make_official(db, slate)  # official lineage → eligible for official scoring
    report = ResultsIngestionService(db).ingest_for_slate(slate, PGM_TEXT)
    assert report["recorded"] == 9 and report["finals"] == 9
    db.commit()

    score = LiveResultsService(db).build_live_score(slate)
    assert score["evaluated_matches"] == 9
    assert score["is_complete"] is True

    summary = finalize_complete_closed_slates(db, now=datetime.now(timezone.utc))
    assert "PGM-799" in summary["finalized"]
    saved = JornadaScoreRepository(db).get_latest_for_slate(slate.id)
    assert saved is not None and saved.is_complete is True
    assert saved.matches_with_results == 9


def test_weekend_incomplete_stays_incomplete(db):
    # PG-2336 has a live + pending match → never complete.
    slate = _seed_slate(db, draw_code="PG-2336", n=14, closes_at=_past())
    ResultsIngestionService(db).ingest_for_slate(slate, PG_TEXT)
    db.commit()
    score = LiveResultsService(db).build_live_score(slate)
    assert score["is_complete"] is False
    summary = finalize_complete_closed_slates(db, now=datetime.now(timezone.utc))
    assert "PG-2336" not in summary["finalized"]


def test_empates_reales_counted(db):
    slate = _seed_slate(db, draw_code="PG-2336", n=14, closes_at=_past(),
                        outcomes=["1"] * 14, draw_probs=[0.25] * 14)
    ResultsIngestionService(db).ingest_for_slate(slate, PG_TEXT)
    db.commit()
    score = LiveResultsService(db).build_live_score(slate)
    # Final draws at positions 2,4,7,10,12 (12 finals evaluated; pos13 live).
    assert score["empates_reales_hasta_ahora"] == 5


def test_sign_only_records_outcome_without_goals(db):
    slate = _seed_slate(db, draw_code="PGM-800", week_type="midweek", n=9, closes_at=_past())
    text = "CONCURSO 800\n" + "\n".join(f"{i} L" for i in range(1, 10))
    report = ResultsIngestionService(db).ingest_for_slate(slate, text)
    assert report["recorded"] == 9
    db.commit()
    payload = LiveResultsService(db).build_live_results(slate)
    m0 = payload["matches"][0]
    assert m0["result_code"] == "1"
    assert m0["home_goals"] is None  # sign-only: no scoreline
    assert m0["is_final"] is True
