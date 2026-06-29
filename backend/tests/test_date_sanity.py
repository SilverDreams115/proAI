"""MS guide date parser staleness + Date Sanity Gate.

Pins the PGM-802 fix:
  * the MS cierre is accepted ONLY when its block's Concurso matches the
    guide's draw_code — a stale block (fixtures=802, cierre=800) yields no date;
  * the candidate dump lists every cierre block with its concurso + confidence;
  * the printed year is honoured (no blind current-year assumption);
  * the date sanity gate blocks slates whose cierre is before creation /
    non-monotonic vs the previous same-type concurso / low-confidence.
"""
from __future__ import annotations

from datetime import datetime, timezone

from app.connectors.progol_guia_pdf import ms_date_candidates, parse_ms_guia_text
from app.services.date_sanity import DateStatus, evaluate_slate_dates

# Guide whose FIXTURES are concurso 802 but whose CIERRE block is the stale 800
# block (the exact PGM-802 shape observed in the live PDF).
_STALE_802 = (
    "GUÍA DE LA QUINIELA\nCONCURSO\n802\nLOCAL\nVISITANTE\n"
    "MÉXICO VS\nCASILLERO 1\nECUADOR\n"
    "BÉLGICA VS\nCASILLERO 2\nSENEGAL\n"
    "CIERRE DE VENTA\nConcurso 800\nMartes 16 de junio hasta las \n13:00 horas\n"
    "Juegos del martes 16 al viernes 19 de junio de 2025\n"
)

# Same guide but the cierre block matches the fixtures' concurso (802) with a
# coherent future date.
_VALID_802 = (
    "GUÍA DE LA QUINIELA\nCONCURSO\n802\nLOCAL\nVISITANTE\n"
    "MÉXICO VS\nCASILLERO 1\nECUADOR\n"
    "BÉLGICA VS\nCASILLERO 2\nSENEGAL\n"
    "CIERRE DE VENTA\nConcurso 802\nMartes 30 de junio hasta las \n13:00 horas\n"
    "Juegos del martes 30 de junio al 2 de julio de 2026\n"
)


def test_stale_cierre_block_is_rejected():
    draw_code, fixtures, closes_at = parse_ms_guia_text(_STALE_802)
    assert draw_code == "802"
    assert len(fixtures) >= 2
    # Cierre belongs to concurso 800 -> refused, no date.
    assert closes_at is None


def test_matching_cierre_block_uses_printed_year():
    draw_code, _fixtures, closes_at = parse_ms_guia_text(_VALID_802)
    assert draw_code == "802"
    assert closes_at == datetime(2026, 6, 30, 19, 0, tzinfo=timezone.utc)


def test_date_candidates_flag_concurso_mismatch():
    cands = ms_date_candidates(_STALE_802, "802")
    assert len(cands) == 1
    assert cands[0]["block_concurso"] == "800"
    assert cands[0]["matches_draw_code"] is False
    assert cands[0]["confidence"] == "low"
    assert cands[0]["year"] == "2025"


# --- Date Sanity Gate ------------------------------------------------------

def _dt(y, m, d, h=19):
    return datetime(y, m, d, h, 0, tzinfo=timezone.utc)


def test_gate_needs_confirmation_when_no_date():
    status, reasons = evaluate_slate_dates(registration_closes_at=None)
    assert status == DateStatus.NEEDS_OPERATOR_CONFIRMATION


def test_gate_stale_when_close_before_created():
    # PGM-802: cierre 2026-06-16, slate created 2026-06-27.
    status, _ = evaluate_slate_dates(
        registration_closes_at=_dt(2026, 6, 16),
        created_at=_dt(2026, 6, 27),
    )
    assert status == DateStatus.STALE_SOURCE


def test_gate_stale_on_low_extraction_confidence():
    status, _ = evaluate_slate_dates(
        registration_closes_at=_dt(2026, 6, 30),
        created_at=_dt(2026, 6, 27),
        extraction_confidence="low",
    )
    assert status == DateStatus.STALE_SOURCE


def test_gate_suspect_when_not_monotonic_vs_previous():
    # PGM-802 must not close before PGM-801 (2026-06-24).
    status, _ = evaluate_slate_dates(
        registration_closes_at=_dt(2026, 6, 20),
        created_at=_dt(2026, 6, 18),
        prev_same_type_closes_at=_dt(2026, 6, 24),
    )
    assert status == DateStatus.DATE_SUSPECT


def test_gate_valid_for_coherent_dates():
    status, reasons = evaluate_slate_dates(
        registration_closes_at=_dt(2026, 7, 1),
        created_at=_dt(2026, 6, 27),
        prev_same_type_closes_at=_dt(2026, 6, 24),
        kickoffs=[_dt(2026, 7, 1, 20)],
        extraction_confidence="high",
    )
    assert status == DateStatus.DATE_VALID
