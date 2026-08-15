"""Club matching for the ESPN fixture resolver.

The script rewrites a slate's competition and kickoff from an outside feed, so
the only thing standing between a fabricated fixture and the wrong real one is
this comparison. It has to accept the same club written two ways and refuse
two different clubs, with no maybe in between.
"""
from __future__ import annotations

import pytest

from scripts.resolve_slate_fixtures_from_espn import _same_club, _tokens


@pytest.mark.parametrize(
    "espn,local",
    [
        # Every rename PGM-809 actually needed.
        ("Club Olimpia", "Olimpia Asuncion"),
        ("Red Bull New York", "New York Red Bulls"),
        ("NEC Nijmegen", "Nijmegen"),
        ("Independiente Rivadavia", "CS Independiente Rivadavia"),
        ("Platense", "CA Platense"),
        ("Rosario Central", "CA Rosario Central"),
        ("Orlando City SC", "Orlando City"),
        ("Chicago Fire FC", "Chicago Fire"),
        ("Universidad Católica", "Universidad Catolica"),
        # The ascii fold eats the ø, leaving a prefix rather than a match.
        ("Bodo/Glimt", "FK Bodø/Glimt"),
    ],
)
def test_same_club_accepts_the_same_club_written_two_ways(espn, local):
    assert _same_club(espn, local)
    assert _same_club(local, espn), "the comparison must not depend on argument order"


@pytest.mark.parametrize(
    "left,right",
    [
        ("Independiente Rivadavia", "Independiente del Valle"),
        ("Atletico Madrid", "Atletico Mineiro"),
        ("Boca Juniors", "River Plate"),
        ("Universidad Catolica", "Universidad de Chile"),
        ("Nashville SC", "Chicago Fire"),
        ("", "Fluminense"),
    ],
)
def test_same_club_refuses_different_clubs(left, right):
    assert not _same_club(left, right)


def test_affixes_alone_never_make_a_club():
    """A name that is nothing but affixes has no tokens left, and an empty
    token set must never match anything."""
    assert _tokens("FC SC AC") == set()
    assert not _same_club("FC SC AC", "Corinthians")


def test_containment_is_the_known_limit():
    """`Santos` matching `Santos Laguna` is containment doing its job and
    getting it wrong — two real clubs, one name inside the other. The pairing
    is what saves it: both sides of a tie must match, and an ambiguous
    position is skipped rather than resolved. Pinned so the day someone
    tightens this, the trade-off is visible rather than discovered.
    """
    assert _same_club("Santos", "Santos Laguna")
