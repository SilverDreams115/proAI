"""Replace a slate's fabricated fixtures with the real ones ESPN publishes (GUARDED).

A Progol position no feed reported is promoted against a placeholder: a
kickoff invented from the cierre and a competition guessed from team history.
PGM-809 had nine of them, all dated the same invented day an hour apart, while
the real matches run 18-21 August across four competitions.

Ingestion cannot fix that on its own. `_persist_historical_results` only ever
creates a match row for a fixture that already has goals, so an upcoming
fixture never becomes a match no matter how many feeds carry it; upcoming
matches enter through the slate refresh, from the local context. This script is
the missing bridge: it reads the ESPN scoreboard sources already registered,
finds each position's real fixture, and re-points the placeholder row at the
truth — competition, kickoff, and the placeholder mark itself.

Matching is deliberately narrow. Both sides of the tie must match, in the same
home/away order, after dropping corporate affixes (CA, CS, FK, SC, FC, CD,
Club, AC) and plural endings: "Red Bull New York" is "New York Red Bulls" and
"Club Olimpia" is "Olimpia Asuncion", but nothing matches on one side alone.
Anything ambiguous is reported and left as it was.

It also clamps the registration close when the fixtures say it is impossible.
809's cierre was 19 Aug 19:30Z, derived from the invented kickoffs, while its
first match starts 18 Aug 19:00Z — a window in which the system would have let
an operator play a slate whose first match had already kicked off.

Usage::

    python -m scripts.resolve_slate_fixtures_from_espn --draw-code PGM-809 --dry-run
    python -m scripts.resolve_slate_fixtures_from_espn --draw-code PGM-809 \\
        --apply --confirm RESOLVE-SLATE-FIXTURES
"""
from __future__ import annotations

import argparse
import re
import unicodedata
from datetime import datetime, timezone

from sqlalchemy import select

from app.connectors.espn_scoreboard import EspnScoreboardConnector
from app.db import session as db_session
from app.db.session import managed_transaction
from app.models.tables import (
    MatchModel,
    ProgolSlateMatchModel,
    ProgolSlateModel,
    SourceModel,
)
from app.repositories.entity_repository import EntityRepository
from app.services.entity_resolution_service import EntityResolutionService
from app.services.normalization_service import NormalizationService

CONFIRM_TOKEN = "RESOLVE-SLATE-FIXTURES"

# Dropped before comparing: they are how one feed writes a club and another
# does not, never what distinguishes two clubs from each other.
_AFFIXES = {"ca", "cs", "cf", "cd", "fc", "fk", "sc", "ac", "afc", "club", "sv", "as"}


def _tokens(name: str) -> set[str]:
    ascii_name = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode("ascii")
    raw = re.sub(r"[^a-z0-9]+", " ", ascii_name.lower()).split()
    out: set[str] = set()
    for token in raw:
        if token in _AFFIXES or len(token) < 2:
            continue
        # "Red Bulls" and "Red Bull" are the same club.
        out.add(token[:-1] if token.endswith("s") and len(token) > 3 else token)
    return out


def _same_club(a: str, b: str) -> bool:
    """One club written two ways, or two clubs. Never a maybe."""
    ta, tb = _tokens(a), _tokens(b)
    if not ta or not tb:
        return False
    if ta == tb or ta <= tb or tb <= ta:
        return True
    # "Bodo/Glimt" vs "FK Bodø/Glimt": the ø is dropped by the ascii fold, so
    # one token is a prefix of the other. Every other token still has to line up.
    small, large = (ta, tb) if len(ta) <= len(tb) else (tb, ta)
    for token in small:
        if not any(
            other == token or (len(token) >= 3 and (other.startswith(token) or token.startswith(other)))
            for other in large
        ):
            return False
    return True


def _espn_fixtures(session) -> list[dict]:
    """Every fixture the registered ESPN scoreboard sources currently serve."""
    sources = session.scalars(
        select(SourceModel).where(
            SourceModel.kind == "espn_scoreboard", SourceModel.is_active.is_(True)
        )
    ).all()
    fixtures: list[dict] = []
    for source in sources:
        connector = EspnScoreboardConnector(name=source.name, base_url=source.base_url)
        for document in connector.fetch():
            for fixture in document.payload.get("fixtures", []):
                if isinstance(fixture, dict):
                    fixtures.append(fixture)
    return fixtures


def _parse_kickoff(raw: str | None) -> datetime | None:
    if not raw:
        return None
    try:
        parsed = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _plan(session, draw_code: str) -> tuple[ProgolSlateModel | None, list[dict], list[dict]]:
    slate = session.scalar(
        select(ProgolSlateModel).where(ProgolSlateModel.draw_code == draw_code)
    )
    if slate is None:
        return None, [], []

    fixtures = _espn_fixtures(session)
    links = session.scalars(
        select(ProgolSlateMatchModel)
        .where(ProgolSlateMatchModel.slate_id == slate.id)
        .order_by(ProgolSlateMatchModel.position)
    ).all()

    rows: list[dict] = []
    for link in links:
        match = session.get(MatchModel, link.match_id)
        if match is None:
            continue
        home, away = match.home_team.name, match.away_team.name
        hits = [
            fixture
            for fixture in fixtures
            if _same_club(str(fixture.get("home_team") or ""), home)
            and _same_club(str(fixture.get("away_team") or ""), away)
        ]
        row = {
            "position": link.position,
            "label": f"{home} vs {away}",
            "match": match,
            "current_competition": match.competition.name,
            "current_kickoff": match.kickoff_at,
            "is_placeholder": bool(match.is_placeholder),
        }
        if len(hits) == 1:
            fixture = hits[0]
            kickoff = _parse_kickoff(fixture.get("kickoff_at"))
            row.update(
                {
                    "action": "RESOLVE" if kickoff else "SKIP",
                    "why": "" if kickoff else "ESPN fixture has no usable kickoff",
                    "new_competition": fixture.get("competition"),
                    "new_kickoff": kickoff,
                    "venue": fixture.get("venue"),
                }
            )
        elif not hits:
            row.update({"action": "SKIP", "why": "no ESPN fixture for this pair in the window"})
        else:
            row.update({"action": "SKIP", "why": f"{len(hits)} ESPN fixtures match — ambiguous"})
        rows.append(row)

    resolved_kickoffs = [r["new_kickoff"] for r in rows if r.get("action") == "RESOLVE"]
    cierre_rows: list[dict] = []
    closes_at = slate.registration_closes_at
    if resolved_kickoffs and closes_at is not None:
        if closes_at.tzinfo is None:
            closes_at = closes_at.replace(tzinfo=timezone.utc)
        first_kickoff = min(resolved_kickoffs)
        if closes_at > first_kickoff:
            cierre_rows.append(
                {
                    "current": closes_at,
                    "proposed": first_kickoff,
                    "why": "registration cannot close after the first match kicks off",
                }
            )
    return slate, rows, cierre_rows


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Re-point a slate's placeholder fixtures onto the real ESPN ones (guarded)."
    )
    parser.add_argument("--draw-code", required=True)
    parser.add_argument("--dry-run", action="store_true", help="(default)")
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--confirm", default="")
    args = parser.parse_args(argv)

    with db_session.SessionLocal() as session:
        slate, rows, cierre_rows = _plan(session, args.draw_code)
        if slate is None:
            print(f"BLOCKED: no slate with draw_code={args.draw_code}.")
            return 2

        resolvable = [r for r in rows if r["action"] == "RESOLVE"]
        print(f"== ESPN fixture resolution for {args.draw_code} (slate {slate.id}) ==")
        for row in rows:
            print(f"  {row['action']:8} {row['position']:>2} {row['label'][:40]:40}")
            if row["action"] == "RESOLVE":
                print(
                    f"           {row['current_competition']} -> {row['new_competition']}"
                )
                print(
                    f"           {row['current_kickoff']} -> {row['new_kickoff']}"
                    f"  (placeholder={row['is_placeholder']} -> False)"
                )
            else:
                print(f"           {row['why']}")
        for entry in cierre_rows:
            print(f"  CIERRE      {entry['current']} -> {entry['proposed']}")
            print(f"           {entry['why']}")
        print(f"  resolve: {len(resolvable)}  skip: {len(rows) - len(resolvable)}")

        if not args.apply:
            print(f"DRY-RUN: no write performed. To apply: --apply --confirm {CONFIRM_TOKEN}")
            return 0
        if args.confirm != CONFIRM_TOKEN:
            print(f"BLOCKED: --apply requires --confirm {CONFIRM_TOKEN}.")
            return 2
        if not resolvable and not cierre_rows:
            print("Nothing to resolve.")
            return 0

        entity_repository = EntityRepository(session)
        resolver = EntityResolutionService(entity_repository, NormalizationService())
        with managed_transaction(session):
            # Resolve each distinct competition once and flush before the next
            # lookup. Two positions in the same tournament — 809 has two UCL
            # qualifiers — otherwise queue two identical alias rows in the same
            # flush and the batch insert dies on the alias uniqueness index.
            competitions: dict[str, str] = {}
            for name in dict.fromkeys(str(row["new_competition"]) for row in resolvable):
                competitions[name] = resolver.resolve_competition(name, None, None).id
                session.flush()
            for row in resolvable:
                competition_id = competitions[str(row["new_competition"])]
                match = row["match"]
                match.competition_id = competition_id
                match.kickoff_at = row["new_kickoff"]
                match.venue = row.get("venue") or match.venue
                # A feed reported this fixture: it is no longer a construction.
                match.is_placeholder = False
            for entry in cierre_rows:
                slate.registration_closes_at = entry["proposed"]
            session.flush()
        print(f"APPLIED: {len(resolvable)} fixture(s) resolved, {len(cierre_rows)} cierre corrected.")
        print(
            "Predictions still carry the old metadata — refresh the slate "
            f"(POST /api/predictions/slates/{slate.id}/refresh)."
        )
        return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
