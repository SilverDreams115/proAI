"""Synthetic fallback fixtures — how they are built, recognised and rebased.

When ``ProgolFixtureResolver`` cannot find a real ingested match for a pair the
LN guide lists, the promotion path still has to produce 9 or 14 positions, so it
fabricates one: a kickoff placed at ``cierre + 12h`` and stepped one hour per
position. That keeps the slate complete and lets the model score what it can.

What the fabrication must never do is pass for real data. A fabricated kickoff
carries no information — nobody published it — and until the ``matches``
``is_placeholder`` column existed it was stored indistinguishably from a kickoff
a feed actually reported. Three things went wrong because of that:

* the UI printed the invented hour on the pick card as if it were the fixture's
  real kickoff;
* ``find_upcoming_match_for_pair`` happily returned a previous slate's fabricated
  row as the "real match found" for a new one, so the invention propagated
  forward (16 match rows ended up shared between two slates that way);
* correcting a slate's cierre left the kickoffs derived from the *old* cierre
  untouched, stranding them on the wrong side of their own registration close —
  PGM-806 ended up with all 9 kickoffs 1.5 days AFTER it had already closed.

This module is pure (no DB, no I/O) so the formula lives in exactly one place and
the detection can be locked with unit tests.

Detection note
--------------
``ladder_base`` recognises fabricated kickoffs by their shape rather than by
recomputing ``cierre + 12h``: once an operator corrects the cierre the stored
kickoffs no longer relate to it, and those are precisely the rows that most need
recognising. A run of >= 3 positions spaced at exactly one hour, sharing an
identical sub-hour component down to the microsecond, is the fabricator's
signature — real fixture feeds do not emit that.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Iterable

# Fabricated kickoffs start half a day after the venta cierre and step one
# position per hour. Both constants are part of the stored-data contract:
# changing them changes what `ladder_base` recognises in historical rows.
FALLBACK_BASE_OFFSET = timedelta(hours=12)
FALLBACK_STEP = timedelta(hours=1)

# Below this many positions an exact hourly ladder is plausible by chance
# (two fixtures an hour apart is an ordinary broadcast slot), so we abstain.
MIN_LADDER_POSITIONS = 3


def _aware(value: datetime) -> datetime:
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value


def fallback_kickoff(cierre: datetime, position: int) -> datetime:
    """The kickoff the promotion path fabricates for `position`."""
    return _aware(cierre) + FALLBACK_BASE_OFFSET + FALLBACK_STEP * max(0, int(position) - 1)


def ladder_base(entries: Iterable[tuple[int, datetime]]) -> datetime | None:
    """Return the ladder's position-1 kickoff, or None when it is not one.

    `entries` are ``(position, kickoff_at)`` pairs for a single slate. The
    result is the value ``fallback_kickoff`` would have produced for position 1,
    which is ``cierre + 12h`` for the cierre in force when the slate was
    promoted — not necessarily the cierre it carries today.
    """
    pairs = [(int(pos), _aware(kick)) for pos, kick in entries if kick is not None]
    if len(pairs) < MIN_LADDER_POSITIONS:
        return None
    bases = {kick - FALLBACK_STEP * (pos - 1) for pos, kick in pairs}
    if len(bases) != 1:
        return None
    # Distinct positions only: the same kickoff repeated across one position
    # would collapse to a single base without forming a ladder at all.
    if len({pos for pos, _ in pairs}) != len(pairs):
        return None
    return bases.pop()


def is_synthetic_ladder(entries: Iterable[tuple[int, datetime]]) -> bool:
    """True when this slate's kickoffs were fabricated by the fallback."""
    return ladder_base(entries) is not None


def rebase_to_cierre(
    cierre: datetime,
    entries: Iterable[tuple[int, datetime]],
) -> dict[int, datetime]:
    """Re-derive fabricated kickoffs from a corrected `cierre`.

    Returns ``{position: new_kickoff}`` for the positions whose stored kickoff
    changes, and an empty dict when the kickoffs are not a fabricated ladder or
    already sit where `cierre` puts them. Real kickoffs are never touched: a
    slate that resolved to ingested fixtures does not form a ladder, so it
    returns empty here regardless of what happens to its cierre.
    """
    pairs = [(int(pos), _aware(kick)) for pos, kick in entries if kick is not None]
    if ladder_base(pairs) is None:
        return {}
    moved: dict[int, datetime] = {}
    for position, current in pairs:
        target = fallback_kickoff(cierre, position)
        if target != current:
            moved[position] = target
    return moved
