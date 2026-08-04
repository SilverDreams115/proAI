"""Decide whether a slate position is a knockout (elimination) tie.

Progol grades every position on the 90-minute result, so a knockout that
ends level is an official "X" — extra time and penalties do not count.
The flag therefore never removes the draw. It tells the prediction
service to trim the draw probability toward the calibrated knockout
band, because two sides that must produce a winner play like it.

Detection is deliberately conservative: a wrong ``True`` shifts real
picks on a real boleta. We only auto-flag competitions where *every*
fixture is an elimination tie — domestic cups. Two large families
cannot be settled from the competition name at all:

* Continental cups (Champions League, Libertadores, Sudamericana) run a
  group stage under the same competition name as their knockout rounds.
* Domestic playoffs — most importantly the Liga MX liguilla — run under
  the plain league name, with nothing in the name to distinguish the
  final from a week-3 league fixture.

Both stay manual, and :func:`detect_knockout` returns ``None`` for them
rather than guessing. The durable fix is to persist the round/stage that
TheSportsDB already returns in ``strRound`` and read the phase off the
fixture; until that lands, ``None`` means "ask an operator".
"""

from __future__ import annotations

from app.services.normalization_service import NormalizationService

_normalizer = NormalizationService()

# Competitions where every fixture is an elimination tie. Slugs are the
# output of NormalizationService.normalize_competition_name.
ALWAYS_KNOCKOUT_SLUGS: frozenset[str] = frozenset(
    {
        "german-cup",  # DFB-Pokal, also ingested as "Copa de Alemania"
        "russian-cup",  # also ingested as "Copa de Rusia"
        "copa-chile",
        "canadian-championship",
        "copa-del-rey",
        "coupe-de-france",
        "coppa-italia",
        "fa-cup",
        "efl-cup",
        "copa-mx",
        "us-open-cup",
    }
)

# Competitions that mix a group stage with knockout rounds under one
# name. Never auto-flagged; listed so the ambiguity is explicit rather
# than an accident of the token heuristic below.
MIXED_STAGE_SLUGS: frozenset[str] = frozenset(
    {
        "uefa-champions",
        "uefa-europa",
        "uefa-conference",
        "copa-libertadores",
        "copa-sudamericana",
        "concacaf-champions-cup",
        "concacaf-w-champions-cup",
        # Leagues Cup opens with a league phase (2026: Aug 4-13) whose
        # fixtures are ordinary draws-allowed games, then switches to
        # knockout rounds under the same name. Without this entry the
        # "cup" token below flagged every league-phase fixture as an
        # elimination tie and trimmed the draw ~55% — all 9 positions of
        # PGM-807 came out that way.
        "leagues-cup",
        "club-world-cup",
        "world-cup",
        "copa-america",
        "gold-cup",
    }
)

# Fallback for cups we have not enumerated. Only fires when the slug is
# in neither set above. "championship" is deliberately absent: the EFL
# Championship is a second-tier league, not a cup.
_KNOCKOUT_NAME_TOKENS: frozenset[str] = frozenset(
    {"cup", "copa", "pokal", "coupe", "coppa", "beker", "taca", "cupen"}
)


def detect_knockout(competition_name: str | None) -> bool | None:
    """Return ``True`` for an always-knockout competition, ``False`` for
    a pure league, and ``None`` when the name cannot decide it.

    ``None`` is not "no" — it means the position needs an operator to
    look at it, and callers must not coerce it to ``False`` silently.
    """
    if not competition_name or not competition_name.strip():
        return None

    slug = _normalizer.normalize_competition_name(competition_name)
    if slug in MIXED_STAGE_SLUGS:
        return None
    if slug in ALWAYS_KNOCKOUT_SLUGS:
        return True

    tokens = set(slug.split("-"))
    if tokens & _KNOCKOUT_NAME_TOKENS:
        return True

    # A plain league name. It still hosts playoffs (liguilla), which the
    # name cannot express, so this is a weak False rather than a strong
    # one — see is_ambiguous_competition.
    return False


def is_ambiguous_competition(competition_name: str | None) -> bool:
    """True when the competition can host knockout ties that the name
    does not reveal, so an unflagged position there deserves review."""
    return detect_knockout(competition_name) is None


# TheSportsDB reports a bare round number for leagues ("1", "17") and a
# phase name for cups ("Final", "Semi-Final", "Round of 16"). Matched
# against the lowercased stage with punctuation flattened to spaces, so
# "Semi-Final", "Semi Final" and "semifinal" all land the same way.
_KNOCKOUT_STAGE_MARKERS: tuple[str, ...] = (
    "final",  # also covers semi-final, quarter-final, semifinal
    "round of",
    "last 16",
    "last 32",
    "playoff",
    "play off",
    "liguilla",
    "repechaje",  # Liga MX play-in; loser is eliminated
    "octavos",
    "cuartos",
    "semis",
    "eliminat",  # eliminatoria / eliminatorias
    "knockout",
)

# Checked before the markers above: a group stage is not a knockout even
# though it lives inside a cup, and "Final Group Stage" style labels
# would otherwise trip the "final" marker.
_GROUP_STAGE_MARKERS: tuple[str, ...] = (
    "group",
    "grupo",
    "regular season",
    "league phase",
    "fase de grupos",
)


def detect_knockout_from_stage(stage: str | None) -> bool | None:
    """Read the knockout question off a fixture's reported stage.

    Returns ``None`` when there is no stage or it says nothing useful,
    so callers fall back to :func:`detect_knockout`. This is the only
    signal that can separate a Champions League group match from a
    semi-final, or a Liga MX league week from a liguilla final.
    """
    if not stage or not stage.strip():
        return None

    text = " ".join(
        "".join(ch if ch.isalnum() else " " for ch in stage.lower()).split()
    )
    if not text:
        return None

    # A bare round number is a league week — the common case by volume.
    if text.isdigit():
        return False

    if any(marker in text for marker in _GROUP_STAGE_MARKERS):
        return False
    if any(marker in text for marker in _KNOCKOUT_STAGE_MARKERS):
        return True
    return None


def resolve_knockout(
    competition_name: str | None,
    stage: str | None = None,
) -> bool | None:
    """Best available answer: the fixture's own stage wins, and the
    competition name only fills in when no stage was reported."""
    from_stage = detect_knockout_from_stage(stage)
    if from_stage is not None:
        return from_stage
    return detect_knockout(competition_name)
