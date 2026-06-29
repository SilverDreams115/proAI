"""Date Sanity Gate — pure validation of a slate's registration/kickoff dates.

A slate is only safe to present as an OPEN, playable boleta when its dates are
coherent. The PGM-802 incident (a guide whose CIERRE block was stale — it
belonged to concurso 800 — yielding a cierre in the past, before the slate was
even created) showed we must gate on dates before activation, never invent a
date, and surface WHY a slate is held back.

Pure: no DB, no I/O. Callers pass the already-loaded values.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from enum import Enum


class DateStatus(str, Enum):
    DATE_VALID = "date_valid"
    DATE_SUSPECT = "date_suspect"
    STALE_SOURCE = "stale_source"
    # PDF has valid fixtures for this concurso but NO valid cierre for it.
    NEEDS_OFFICIAL_PDF_DATE = "needs_official_pdf_date"
    # PDF's only cierre block belongs to a DIFFERENT concurso (wrong source).
    SOURCE_INVALID = "source_invalid"
    PARSE_ERROR = "parse_error"
    NEEDS_OPERATOR_CONFIRMATION = "needs_operator_confirmation"


# A newly-created/promoted slate whose cierre is in the past by more than this
# is stale (a fresh concurso never closes before it is discovered).
STALE_BEFORE_CREATED_TOLERANCE = timedelta(hours=12)
# How far the source may be observed AFTER its own cierre before we treat the
# document as stale rather than merely "closed recently".
STALE_OBSERVED_AFTER_CLOSE = timedelta(days=3)


def _aware(dt: datetime | None) -> datetime | None:
    if dt is None:
        return None
    from datetime import timezone

    return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt


def evaluate_slate_dates(
    *,
    registration_closes_at: datetime | None,
    kickoffs: list[datetime] | None = None,
    created_at: datetime | None = None,
    observed_at: datetime | None = None,
    prev_same_type_closes_at: datetime | None = None,
    extraction_confidence: str | None = None,
    fixtures_present: bool = False,
    rejected_close_block: bool = False,
) -> tuple[DateStatus, list[str]]:
    """Return (status, reasons). Never raises; missing inputs degrade safely.

    Order matters: a missing date with a wrong-concurso cierre block in the PDF
    is source_invalid; a missing date with valid fixtures is
    needs_official_pdf_date; a missing date with nothing is
    needs_operator_confirmation; an explicitly low-confidence extraction or an
    impossible-vs-creation date is stale_source; a non-monotonic date is
    date_suspect.
    """
    reasons: list[str] = []
    closes = _aware(registration_closes_at)
    created = _aware(created_at)
    observed = _aware(observed_at)
    prev = _aware(prev_same_type_closes_at)
    kicks = [k for k in (_aware(x) for x in (kickoffs or [])) if k is not None]

    if closes is None:
        if rejected_close_block:
            reasons.append(
                "el PDF oficial trae el bloque de cierre de OTRO concurso; no se aplica"
            )
            return DateStatus.SOURCE_INVALID, reasons
        if fixtures_present:
            reasons.append(
                "fixtures válidos del PDF pero sin bloque de cierre del concurso correcto"
            )
            return DateStatus.NEEDS_OFFICIAL_PDF_DATE, reasons
        reasons.append("no se extrajo una fecha de cierre confiable del guía")
        return DateStatus.NEEDS_OPERATOR_CONFIRMATION, reasons

    if (extraction_confidence or "").lower() == "low":
        reasons.append("extracción de fecha con baja confianza (bloque cierre stale/ambiguo)")
        return DateStatus.STALE_SOURCE, reasons

    # A brand-new slate cannot have a cierre well in the past relative to when
    # it was created/promoted — that is a stale source, not a closed contest.
    if created is not None and closes < created - STALE_BEFORE_CREATED_TOLERANCE:
        reasons.append(
            f"cierre {closes.date()} es anterior a la creación de la slate {created.date()}"
        )
        return DateStatus.STALE_SOURCE, reasons

    if observed is not None and closes < observed - STALE_OBSERVED_AFTER_CLOSE:
        reasons.append(
            f"fuente observada {observed.date()} muy posterior al cierre {closes.date()}"
        )
        return DateStatus.STALE_SOURCE, reasons

    # Monotonicity: a higher draw_code of the same week_type must not close
    # before the previous one (PGM-802 must not close before PGM-801).
    if prev is not None and closes <= prev:
        reasons.append(
            f"cierre {closes.date()} no es posterior al concurso previo del mismo tipo ({prev.date()})"
        )
        return DateStatus.DATE_SUSPECT, reasons

    # Note: a "kickoff before cierre" coherence check was intentionally NOT
    # made a hard block — historical seed/imported slates carry approximate
    # kickoff stamps that would false-flag completed concursos. The real
    # PGM-802 staleness is caught above (close-before-created / low-confidence
    # / non-monotonic), and ``kicks`` is still accepted for callers that want
    # to inspect it without changing the verdict.
    _ = kicks
    reasons.append("fechas coherentes")
    return DateStatus.DATE_VALID, reasons
