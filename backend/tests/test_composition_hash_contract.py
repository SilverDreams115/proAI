"""Pure-unit contract tests for the slate ``composition_hash``.

These tests pin the *observable contract* of the two hashing helpers on
``SlateRepository`` without touching the database:

* ``_compute_composition_hash(payload)`` — hashes the RAW payload names
  (pre entity-resolution). This is what ``upsert_slate`` / promotion
  persists, so it is the hash every prediction and ticket snapshot is
  keyed on.
* ``_compute_hash_from_model(slate)`` — hashes the RESOLVED DB names
  (post canonicalization). Only ``backfill_composition_hashes`` uses it,
  and only for slates whose hash is currently NULL.

The PG-2338 golden values below are the real production hashes. They
document that the two helpers legitimately diverge once entity
resolution canonicalizes team names (e.g. ``CHEQUIA`` -> ``Czech
Republic``), and lock that behaviour against accidental change.
"""

from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

from app.repositories.slate_repository import SlateRepository
from app.schemas.common import CompetitionPayload, MatchReferencePayload, TeamPayload
from app.schemas.slate import ProgolSlateCreate

_COMPETITION = "International Friendlies"

# PG-2338 raw proposal fixtures (Spanish, as parsed from the Progol guide,
# before entity resolution). These names feed _compute_composition_hash and
# produce the persisted stored_hash.
PG2338_PAYLOAD_FIXTURES: list[tuple[int, str, str]] = [
    (1, "CHEQUIA", "MÉXICO"),
    (2, "SUIZA", "CANADÁ"),
    (3, "BOSNIA", "CATAR"),
    (4, "JAPÓN", "SUECIA"),
    (5, "TURQUÍA", "E.U.A."),
    (6, "PARAGUAY", "AUSTRALIA"),
    (7, "NORUEGA", "FRANCIA"),
    (8, "CABO VERDE", "ARABIA SAUDITA"),
    (9, "URUGUAY", "ESPAÑA"),
    (10, "EGIPTO", "IRÁN"),
    (11, "CROACIA", "GHANA"),
    (12, "COLOMBIA", "PORTUGAL"),
    (13, "REPÚBLICA DEL CONGO", "UZBEKISTÁN"),
    (14, "ARGELIA", "AUSTRIA"),
]

# PG-2338 fixtures as stored in the DB after entity resolution. The four
# unresolved placeholders (Suiza, Catar, Cabo Verde, República Del Congo)
# stay Spanish; everything else is canonical English.
PG2338_MODEL_FIXTURES: list[tuple[int, str, str]] = [
    (1, "Czech Republic", "México"),
    (2, "Suiza", "Canada"),
    (3, "Bosnia-Herzegovina", "Catar"),
    (4, "Japan", "Sweden"),
    (5, "Turkey", "USA"),
    (6, "Paraguay", "Australia"),
    (7, "Norway", "France"),
    (8, "Cabo Verde", "Saudi Arabia"),
    (9, "Uruguay", "Spain"),
    (10, "Egypt", "Iran"),
    (11, "Croatia", "Ghana"),
    (12, "Colombia", "Portugal"),
    (13, "República Del Congo", "Uzbekistan"),
    (14, "Algeria", "Austria"),
]

# Real production values (reproduced byte-for-byte by the helpers below).
PG2338_STORED_HASH = "308aafc934654c488841835c1a8548225ad4f6cb7d4d8b065b8fa1efe873cc6e"
PG2338_MODEL_HASH = "3a960f30e094b14ed3c264a8b9c90b44e6ddcc260fa7ebcce96c7046dce8f9e6"


def _kickoff(position: int) -> datetime:
    # PG-2338 kickoffs are 1-hour increments starting 07:00Z (pos 1).
    return datetime(2026, 6, 25, 6 + position, 0, tzinfo=timezone.utc)


def _payload(
    fixtures: list[tuple[int, str, str]],
    *,
    draw_code: str = "PG-2338",
    week_type: str = "weekend",
    label: str = "PG-2338",
    competition: str = _COMPETITION,
) -> ProgolSlateCreate:
    matches = [
        MatchReferencePayload(
            position=position,
            competition=CompetitionPayload(name=competition),
            home_team=TeamPayload(name=home),
            away_team=TeamPayload(name=away),
            kickoff_at=_kickoff(position),
        )
        for position, home, away in fixtures
    ]
    return ProgolSlateCreate(
        label=label, draw_code=draw_code, week_type=week_type, matches=matches
    )


def _model_slate(
    fixtures: list[tuple[int, str, str]],
    *,
    draw_code: str = "PG-2338",
    week_type: str = "weekend",
    competition: str = _COMPETITION,
) -> SimpleNamespace:
    # Duck-typed stand-in for ProgolSlateModel: _compute_hash_from_model only
    # reads attributes, so no ORM session / DB is required.
    matches = [
        SimpleNamespace(
            position=position,
            match=SimpleNamespace(
                kickoff_at=_kickoff(position),
                home_team=SimpleNamespace(name=home),
                away_team=SimpleNamespace(name=away),
                competition=SimpleNamespace(name=competition),
            ),
        )
        for position, home, away in fixtures
    ]
    return SimpleNamespace(draw_code=draw_code, week_type=week_type, matches=matches)


def _payload_hash(fixtures: list[tuple[int, str, str]], **kwargs: object) -> str:
    return SlateRepository._compute_composition_hash(_payload(fixtures, **kwargs))  # type: ignore[arg-type]


def _model_hash(fixtures: list[tuple[int, str, str]], **kwargs: object) -> str:
    return SlateRepository._compute_hash_from_model(_model_slate(fixtures, **kwargs))  # type: ignore[arg-type, return-value]


# 1. Deterministic for the same payload.
def test_payload_hash_is_deterministic() -> None:
    first = _payload_hash(PG2338_PAYLOAD_FIXTURES)
    second = _payload_hash(PG2338_PAYLOAD_FIXTURES)
    assert first == second


def test_payload_hash_ignores_non_fixture_fields() -> None:
    # label / registration_closes_at are not part of the composition hash.
    base = _payload_hash(PG2338_PAYLOAD_FIXTURES, label="PG-2338")
    relabelled = _payload_hash(PG2338_PAYLOAD_FIXTURES, label="Totally Different Label")
    assert base == relabelled


# 2. Independent of the order fixtures are supplied / internal JSON key order.
def test_payload_hash_independent_of_fixture_order() -> None:
    shuffled = list(reversed(PG2338_PAYLOAD_FIXTURES))
    assert _payload_hash(shuffled) == _payload_hash(PG2338_PAYLOAD_FIXTURES)


# 3. Changes when a fixture attribute (kickoff/competition) changes.
def test_payload_hash_changes_when_competition_changes() -> None:
    base = _payload_hash(PG2338_PAYLOAD_FIXTURES)
    other = _payload_hash(PG2338_PAYLOAD_FIXTURES, competition="World Cup Qualifiers")
    assert base != other


def test_payload_hash_changes_when_kickoff_changes() -> None:
    base = _payload(PG2338_PAYLOAD_FIXTURES)
    moved = _payload(PG2338_PAYLOAD_FIXTURES)
    moved.matches[0].kickoff_at = moved.matches[0].kickoff_at.replace(hour=23)
    assert (
        SlateRepository._compute_composition_hash(base)
        != SlateRepository._compute_composition_hash(moved)
    )


# 4. Changes when a raw payload team name changes.
def test_payload_hash_changes_when_raw_name_changes() -> None:
    mutated = [
        (pos, ("SWITZERLAND" if pos == 2 else home), away)
        for pos, home, away in PG2338_PAYLOAD_FIXTURES
    ]
    assert _payload_hash(mutated) != _payload_hash(PG2338_PAYLOAD_FIXTURES)


# 5. Payload-derived and model-derived hashes agree iff names match, and
#    diverge once canonicalization rewrites them.
def test_payload_and_model_hash_agree_when_names_identical() -> None:
    # Feed both helpers the identical (canonical) fixtures.
    assert _payload_hash(PG2338_MODEL_FIXTURES) == _model_hash(PG2338_MODEL_FIXTURES)


def test_payload_and_model_hash_diverge_under_canonicalization() -> None:
    # Same slate, but payload carries Spanish names and the model carries the
    # resolved canonical names: the two conventions legitimately differ.
    assert _payload_hash(PG2338_PAYLOAD_FIXTURES) != _model_hash(PG2338_MODEL_FIXTURES)


# 6. PG-2338 golden regression: reproduce the real production hashes.
def test_pg2338_payload_hash_matches_stored_production_hash() -> None:
    assert _payload_hash(PG2338_PAYLOAD_FIXTURES) == PG2338_STORED_HASH


def test_pg2338_model_hash_matches_recomputed_production_hash() -> None:
    assert _model_hash(PG2338_MODEL_FIXTURES) == PG2338_MODEL_HASH


def test_pg2338_stored_and_model_hashes_are_distinct() -> None:
    # The drift is benign: predictions/snapshots are keyed on the stored
    # payload hash; the model hash is only ever a recompute artifact.
    assert PG2338_STORED_HASH != PG2338_MODEL_HASH
