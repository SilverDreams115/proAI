import json
from collections import Counter
from datetime import datetime, timezone
from typing import Any

from app.core.errors import NotFoundError
from app.db.session import managed_transaction
from sqlalchemy import select

from app.models.tables import EvidenceItemModel
from app.models.tables import MatchFeatureSnapshotModel
from app.models.tables import MatchModel
from app.models.tables import PlayerAvailabilityModel
from app.repositories.feature_repository import FeatureRepository
from app.repositories.result_repository import ResultRepository


class FeatureService:
    FEATURE_SET_VERSION = "v3"
    # Club competitions play weekly so 45 days = ~6 fixtures, plenty.
    # National-team friendlies happen every 2-3 months, so the same
    # window would drop legitimate recent results. Use a longer window
    # for competitions where matches are infrequent.
    RECENT_FORM_MAX_AGE_DAYS = 45
    RECENT_FORM_MAX_AGE_DAYS_INFREQUENT = 240
    INFREQUENT_COMPETITION_KEYWORDS = (
        "friendl",  # International Friendlies / Friendly
        "amistos",  # amistosos
        "qualif",   # World Cup Qualifiers
        "world cup",
        "copa america",
        "euro",
        "nations league",
        "international",
        # Apertura/Clausura torneos with a ~6-8 week midseason gap. The
        # 45-day window cuts off mid-tournament fixtures by a few days
        # right after the gap. 240 days covers a full apertura+clausura
        # cycle without reaching back to last year's roster.
        "expansion",  # Liga de Expansion MX (lower-tier MX, bi-annual)
    )
    # Fase 2.3: features rebuilt at most once per TTL window per match.
    # Predictions/quality/ticket endpoints all touch the same match many
    # times per slate request; cache hits avoid 4x redundant queries.
    SNAPSHOT_TTL_SECONDS = 300
    NARRATIVE_KEYWORDS = {
        "injury": ("injury", "injuries", "injured", "lesion", "lesionado", "baja", "out"),
        "suspension": ("suspension", "suspensions", "suspended", "suspendido", "amonestado", "red card"),
        "rotation": ("rotation", "rotacion", "rest", "rested", "alternate lineup", "lineup"),
    }
    NEGATED_SIGNAL_PHRASES = {
        "injury": (
            "no verified injury",
            "no verified injuries",
            "no confirmed injury",
            "no confirmed injuries",
            "no se incluyen lesiones",
            "no hay lesiones",
            "sin lesiones",
        ),
        "suspension": (
            "no verified suspension",
            "no verified suspensions",
            "no confirmed suspension",
            "no confirmed suspensions",
            "no se incluyen suspendidos",
            "no se incluyen lesiones, suspendidos",
            "no hay suspendidos",
            "sin suspendidos",
        ),
        "rotation": (
            "no verified lineup",
            "no verified lineups",
            "no confirmed lineup",
            "no confirmed lineups",
            "no se incluyen alineaciones",
            "no se incluyen lesiones, suspendidos ni alineaciones",
            "no hay alineaciones",
            "sin alineaciones",
        ),
    }

    def __init__(self, repository: FeatureRepository, result_repository: ResultRepository | None = None) -> None:
        self.repository = repository
        self.result_repository = result_repository

    def build_match_features(
        self, match_id: str, *, use_cache: bool = True, persist: bool = False
    ) -> tuple[MatchModel, dict[str, Any], datetime]:
        """Return the feature payload for a match.

        Read-only by default: when ``persist`` is ``False`` (the default,
        used by every ``GET`` endpoint) a cache miss recomputes the payload
        *in memory* and returns it without ever writing a
        ``match_feature_snapshots`` row. Lazy-writing from read paths was the
        source of the snapshot drift (1110 -> 1124) observed while the API
        served PG-2338 GET requests with an expired cache.

        Persisting a fresh snapshot must be an explicit, non-GET action:
        callers that genuinely want to refresh the cache pass
        ``persist=True``.
        """
        match = self.repository.get_match(match_id)
        if match is None:
            raise NotFoundError("Match not found.")

        if use_cache:
            cached = self._read_fresh_snapshot(match_id)
            if cached is not None:
                payload, generated_at = cached
                return match, payload, generated_at

        now = datetime.now(timezone.utc)
        kickoff_at = match.kickoff_at
        if kickoff_at.tzinfo is None:
            kickoff_at = kickoff_at.replace(tzinfo=timezone.utc)
        hours_to_kickoff = max((kickoff_at - now).total_seconds() / 3600, 0.0)
        home_results = (
            self.result_repository.list_recent_team_results(match.home_team_id, kickoff_at, limit=5)
            if self.result_repository
            else []
        )
        away_results = (
            self.result_repository.list_recent_team_results(match.away_team_id, kickoff_at, limit=5)
            if self.result_repository
            else []
        )
        head_to_head_results = self._list_head_to_head_results(match.id)
        max_age = self._max_recent_age_days(match)
        home_results = self._filter_recent_results(home_results, kickoff_at, max_age_days=max_age)
        away_results = self._filter_recent_results(away_results, kickoff_at, max_age_days=max_age)
        evidence_items = self.repository.list_match_evidence(match_id)
        availability_items = self.repository.list_match_availability(match_id)
        narrative = self._extract_narrative_signals(match, evidence_items, availability_items)
        evidence_summaries = self._evidence_summaries(evidence_items)
        home_form = self._summarize_team_results(home_results, match.home_team_id, now)
        away_form = self._summarize_team_results(away_results, match.away_team_id, now)
        head_to_head_form = self._summarize_head_to_head_results(match, head_to_head_results)
        payload = {
            "hours_to_kickoff": round(hours_to_kickoff, 2),
            "venue_known": match.venue is not None,
            "linked_documents": self.repository.count_linked_documents(match_id),
            "evidence_items": self.repository.count_evidence_items(match_id),
            "evidence_summaries": evidence_summaries,
            "is_same_country_matchup": bool(match.home_team.country and match.home_team.country == match.away_team.country),
            "recent_results_count": len(home_results) + len(away_results),
            "home_recent_points": home_form["points"],
            "away_recent_points": away_form["points"],
            "home_recent_goal_balance": home_form["goal_balance"],
            "away_recent_goal_balance": away_form["goal_balance"],
            "home_recent_goals_for": home_form["goals_for"],
            "away_recent_goals_for": away_form["goals_for"],
            "home_recent_goals_against": home_form["goals_against"],
            "away_recent_goals_against": away_form["goals_against"],
            "home_recent_matches": home_form["matches"],
            "away_recent_matches": away_form["matches"],
            "home_days_rest": home_form["days_rest"],
            "away_days_rest": away_form["days_rest"],
            **head_to_head_form,
            **narrative,
        }
        if not persist:
            # Read-only path: never touch the session. Return the in-memory
            # payload stamped with the recompute time. No snapshot row is
            # created, so GET endpoints cannot grow match_feature_snapshots.
            return match, payload, now

        with managed_transaction(self.repository.session):
            snapshot = self.repository.save_snapshot(match_id, self.FEATURE_SET_VERSION, payload)
        generated_at = snapshot.generated_at
        return match, json.loads(snapshot.payload_json), generated_at

    def _read_fresh_snapshot(self, match_id: str) -> tuple[dict[str, Any], datetime] | None:
        """Return the most recent snapshot for this match if it is still
        within the TTL window. Returns None when no snapshot exists or it
        is too old."""
        statement = (
            select(MatchFeatureSnapshotModel)
            .where(
                MatchFeatureSnapshotModel.match_id == match_id,
                MatchFeatureSnapshotModel.feature_set_version == self.FEATURE_SET_VERSION,
            )
            .order_by(MatchFeatureSnapshotModel.generated_at.desc())
            .limit(1)
        )
        snapshot = self.repository.session.scalar(statement)
        if snapshot is None:
            return None
        generated_at = snapshot.generated_at
        if generated_at.tzinfo is None:
            generated_at = generated_at.replace(tzinfo=timezone.utc)
        if (datetime.now(timezone.utc) - generated_at).total_seconds() > self.SNAPSHOT_TTL_SECONDS:
            return None
        try:
            payload = json.loads(snapshot.payload_json)
        except json.JSONDecodeError:
            return None
        if not isinstance(payload, dict):
            return None
        return payload, generated_at

    def build_model_features(self, match: MatchModel, cutoff: datetime | None = None) -> dict[str, float]:
        if cutoff is None:
            cutoff = match.kickoff_at
        if cutoff.tzinfo is None:
            cutoff = cutoff.replace(tzinfo=timezone.utc)
        home_results = (
            self.result_repository.list_recent_team_results(match.home_team_id, cutoff, limit=8)
            if self.result_repository
            else []
        )
        away_results = (
            self.result_repository.list_recent_team_results(match.away_team_id, cutoff, limit=8)
            if self.result_repository
            else []
        )
        head_to_head_results = self._list_head_to_head_results(match.id)
        max_age = self._max_recent_age_days(match)
        home_results = self._filter_recent_results(home_results, cutoff, max_age_days=max_age)
        away_results = self._filter_recent_results(away_results, cutoff, max_age_days=max_age)
        home_form = self._summarize_team_results(home_results, match.home_team_id, cutoff)
        away_form = self._summarize_team_results(away_results, match.away_team_id, cutoff)
        head_to_head_form = self._summarize_head_to_head_results(match, head_to_head_results)
        availability_items = self.repository.list_match_availability(match.id)
        evidence_items = self.repository.list_match_evidence(match.id)
        narrative = self._extract_narrative_signals(
            match,
            evidence_items,
            availability_items,
        )
        return {
            "home_points_per_match": self._per_match(home_form["points"], home_form["matches"]),
            "away_points_per_match": self._per_match(away_form["points"], away_form["matches"]),
            "home_goal_balance_per_match": self._per_match(home_form["goal_balance"], home_form["matches"]),
            "away_goal_balance_per_match": self._per_match(away_form["goal_balance"], away_form["matches"]),
            "home_goals_for_per_match": self._per_match(home_form["goals_for"], home_form["matches"]),
            "away_goals_for_per_match": self._per_match(away_form["goals_for"], away_form["matches"]),
            "home_goals_against_per_match": self._per_match(home_form["goals_against"], home_form["matches"]),
            "away_goals_against_per_match": self._per_match(away_form["goals_against"], away_form["matches"]),
            "home_recent_matches": home_form["matches"],
            "away_recent_matches": away_form["matches"],
            "form_gap": self._per_match(home_form["points"], home_form["matches"])
            - self._per_match(away_form["points"], away_form["matches"]),
            "goal_balance_gap": self._per_match(home_form["goal_balance"], home_form["matches"])
            - self._per_match(away_form["goal_balance"], away_form["matches"]),
            "rest_gap_days": home_form["days_rest"] - away_form["days_rest"],
            "head_to_head_matches": head_to_head_form["head_to_head_results_count"],
            "head_to_head_points_gap": self._per_match(
                head_to_head_form["head_to_head_home_points"] - head_to_head_form["head_to_head_away_points"],
                head_to_head_form["head_to_head_results_count"],
            ),
            "head_to_head_goal_balance_gap": self._per_match(
                head_to_head_form["head_to_head_goal_balance"],
                head_to_head_form["head_to_head_results_count"],
            ),
            "evidence_count": float(len(evidence_items)),
            "injury_signal_total": float(narrative["injury_signal_total"]),
            "suspension_signal_total": float(narrative["suspension_signal_total"]),
            "rotation_signal_total": float(narrative["rotation_signal_total"]),
            "home_context_signal": float(
                narrative["home_availability_impact"]
            ),
            "away_context_signal": float(
                narrative["away_availability_impact"]
            ),
            "home_injury_signals": float(narrative["home_injury_signals"]),
            "away_injury_signals": float(narrative["away_injury_signals"]),
            "home_suspension_signals": float(narrative["home_suspension_signals"]),
            "away_suspension_signals": float(narrative["away_suspension_signals"]),
            "home_rotation_signals": float(narrative["home_rotation_signals"]),
            "away_rotation_signals": float(narrative["away_rotation_signals"]),
            "same_country_matchup": 1.0
            if bool(match.home_team.country and match.home_team.country == match.away_team.country)
            else 0.0,
            "venue_known": 1.0 if match.venue else 0.0,
            "home_advantage": 1.0,
        }

    def _list_head_to_head_results(self, match_id: str) -> list:
        if self.result_repository is None:
            return []
        list_method = getattr(self.result_repository, "list_head_to_head_results_for_match", None)
        if list_method is None:
            return []
        return list_method(match_id, limit=5)

    def _summarize_team_results(self, results: list, team_id: str, reference_time: datetime) -> dict[str, float]:
        points = 0.0
        goals_for = 0.0
        goals_against = 0.0
        goal_balance = 0.0
        days_rest = 7.0
        if results:
            latest = results[0]
            played_at = latest.played_at
            if played_at.tzinfo is None:
                played_at = played_at.replace(tzinfo=timezone.utc)
            days_rest = max((reference_time - played_at).total_seconds() / 86400, 0.0)
        for result in results:
            match = result.match
            is_home = getattr(match, "home_team_id", None) == team_id
            team_goals = float(result.home_goals if is_home else result.away_goals)
            opponent_goals = float(result.away_goals if is_home else result.home_goals)
            goals_for += team_goals
            goals_against += opponent_goals
            goal_balance += team_goals - opponent_goals
            if team_goals > opponent_goals:
                points += 3
            elif team_goals == opponent_goals:
                points += 1
        return {
            "matches": float(len(results)),
            "points": points,
            "goals_for": goals_for,
            "goals_against": goals_against,
            "goal_balance": goal_balance,
            "days_rest": round(days_rest, 2),
        }

    def _summarize_head_to_head_results(self, match: MatchModel, results: list) -> dict[str, Any]:
        home_points = 0.0
        away_points = 0.0
        goal_balance = 0.0
        home_wins = 0.0
        away_wins = 0.0
        draws = 0.0
        summaries: list[dict[str, Any]] = []
        for result in results:
            result_match = result.match
            current_home_was_home = result_match.home_team_id == match.home_team_id
            current_home_goals = float(result.home_goals if current_home_was_home else result.away_goals)
            current_away_goals = float(result.away_goals if current_home_was_home else result.home_goals)
            goal_balance += current_home_goals - current_away_goals
            if current_home_goals > current_away_goals:
                home_points += 3
                home_wins += 1
            elif current_home_goals < current_away_goals:
                away_points += 3
                away_wins += 1
            else:
                home_points += 1
                away_points += 1
                draws += 1
            summaries.append(
                {
                    "home_team_name": result_match.home_team.name,
                    "away_team_name": result_match.away_team.name,
                    "home_goals": result.home_goals,
                    "away_goals": result.away_goals,
                    "played_at": result.played_at.isoformat(),
                }
            )
        return {
            "head_to_head_results_count": float(len(results)),
            "head_to_head_home_points": home_points,
            "head_to_head_away_points": away_points,
            "head_to_head_goal_balance": goal_balance,
            "head_to_head_home_wins": home_wins,
            "head_to_head_away_wins": away_wins,
            "head_to_head_draws": draws,
            "head_to_head_summaries": summaries[:3],
        }

    def _filter_recent_results(
        self,
        results: list,
        reference_time: datetime,
        *,
        max_age_days: float | None = None,
    ) -> list:
        if reference_time.tzinfo is None:
            reference_time = reference_time.replace(tzinfo=timezone.utc)
        max_age = self.RECENT_FORM_MAX_AGE_DAYS if max_age_days is None else max_age_days
        filtered = []
        for result in results:
            played_at = result.played_at
            if played_at.tzinfo is None:
                played_at = played_at.replace(tzinfo=timezone.utc)
            age_days = (reference_time - played_at).total_seconds() / 86400
            if 0 <= age_days <= max_age:
                filtered.append(result)
        return filtered

    # Multiplier applied to the per-competition median gap to derive
    # the recent-form window. 3.0 keeps roughly the last three played
    # matches per team while still cutting off seasons-old fixtures.
    RECENT_FORM_GAP_MULTIPLIER = 3.0
    # Hard caps so an unusually thin competition can't blow the window
    # all the way to a year, and a busy weekly league still gets at
    # least ~one month of recent form.
    RECENT_FORM_MIN_WINDOW_DAYS = 30
    RECENT_FORM_MAX_WINDOW_DAYS = 365
    # In-process cache keyed by competition_id. The median gap doesn't
    # change between fixture ingests; recomputing it per match would
    # quadruple feature build time for a 14-fixture slate.
    _competition_gap_cache: dict[str, float | None] = {}

    def _max_recent_age_days(self, match: MatchModel) -> float:
        """Size the recent-form window from the competition's actual
        match cadence rather than a hand-curated keyword list.

        Falls back to the keyword list, then to the default constants,
        when no result_repository is wired (mocked tests) or the
        competition has too few matches to estimate a reliable gap.
        """
        competition = getattr(match, "competition", None)
        competition_id = getattr(competition, "id", None) if competition else None
        if competition_id and self.result_repository is not None:
            if competition_id not in self._competition_gap_cache:
                self._competition_gap_cache[competition_id] = (
                    self.result_repository.median_gap_days_for_competition(competition_id)
                )
            median_gap = self._competition_gap_cache[competition_id]
            if median_gap is not None and median_gap > 0:
                window = self.RECENT_FORM_GAP_MULTIPLIER * median_gap
                return float(
                    max(
                        self.RECENT_FORM_MIN_WINDOW_DAYS,
                        min(self.RECENT_FORM_MAX_WINDOW_DAYS, window),
                    )
                )
        # Fallback: hand-curated keyword list for competitions we
        # haven't ingested yet (so no median gap available).
        competition_name = (
            getattr(competition, "name", "") if competition else ""
        ).lower()
        for keyword in self.INFREQUENT_COMPETITION_KEYWORDS:
            if keyword in competition_name:
                return float(self.RECENT_FORM_MAX_AGE_DAYS_INFREQUENT)
        return float(self.RECENT_FORM_MAX_AGE_DAYS)

    def invalidate_competition_gap_cache(self) -> None:
        self._competition_gap_cache.clear()

    def _extract_narrative_signals(
        self,
        match: MatchModel,
        evidence_items: list[EvidenceItemModel],
        availability_items: list[PlayerAvailabilityModel],
    ) -> dict[str, float]:
        normalized_home_tokens = set(match.home_team.name.lower().split())
        normalized_away_tokens = set(match.away_team.name.lower().split())
        counts: Counter[str] = Counter()
        home_availability_impact = 0.0
        away_availability_impact = 0.0
        for availability in availability_items:
            counts[f"{availability.category}_signal_total"] += 1
            weighted_impact = float(availability.impact_score) * self._player_importance(availability)
            if availability.team_id == match.home_team_id:
                counts[f"home_{availability.category}_signals"] += 1
                home_availability_impact += weighted_impact
            elif availability.team_id == match.away_team_id:
                counts[f"away_{availability.category}_signals"] += 1
                away_availability_impact += weighted_impact
        for evidence in evidence_items:
            text = self._evidence_text(evidence)
            lowered = text.lower()
            mentions_home = any(token in lowered for token in normalized_home_tokens if len(token) > 2)
            mentions_away = any(token in lowered for token in normalized_away_tokens if len(token) > 2)
            for signal_name, keywords in self.NARRATIVE_KEYWORDS.items():
                if any(keyword in lowered for keyword in keywords):
                    if self._has_negated_signal(lowered, signal_name):
                        continue
                    counts[f"{signal_name}_signal_total"] += 1
                    if mentions_home:
                        counts[f"home_{signal_name}_signals"] += 1
                    if mentions_away:
                        counts[f"away_{signal_name}_signals"] += 1
        return {
            "injury_signal_total": float(counts["injury_signal_total"]),
            "suspension_signal_total": float(counts["suspension_signal_total"]),
            "rotation_signal_total": float(counts["rotation_signal_total"]),
            "home_injury_signals": float(counts["home_injury_signals"]),
            "away_injury_signals": float(counts["away_injury_signals"]),
            "home_suspension_signals": float(counts["home_suspension_signals"]),
            "away_suspension_signals": float(counts["away_suspension_signals"]),
            "home_rotation_signals": float(counts["home_rotation_signals"]),
            "away_rotation_signals": float(counts["away_rotation_signals"]),
            "home_availability_impact": round(home_availability_impact, 3),
            "away_availability_impact": round(away_availability_impact, 3),
        }

    def _player_importance(self, availability: PlayerAvailabilityModel) -> float:
        """Weight an availability event by how important the missing player is.

        Combines two signals when available:

        - **Squad role** from the team_players join (starter > reserve > unknown).
          When the role is missing, the player is assumed average (1.0) so
          unknown players never get a zero multiplier.
        - **Primary position**: goalkeepers and strikers tilt the weight up
          because their absence has a larger marginal impact on outcome.

        Falls back to 1.0 when no player metadata exists, keeping the
        previous flat behavior for anonymous availability rows.
        """
        importance = 1.0
        player = getattr(availability, "player", None)

        roles: list[str] = []
        if player is not None:
            for link in getattr(player, "team_links", []) or []:
                if getattr(link, "team_id", None) == availability.team_id:
                    role = getattr(link, "squad_role", None)
                    if role:
                        roles.append(str(role).lower())

        for role in roles:
            if any(token in role for token in ("starter", "titular", "key", "captain", "capitan")):
                importance *= 1.30
                break
            if any(token in role for token in ("reserve", "suplente", "bench", "sub")):
                importance *= 0.65
                break

        position = ""
        if player is not None and getattr(player, "primary_position", None):
            position = str(player.primary_position).lower()
        if any(token in position for token in ("keeper", "portero", "arquero", "gk")):
            importance *= 1.15
        elif any(token in position for token in ("striker", "forward", "delantero", "fw")):
            importance *= 1.10

        # Keep the multiplier in a safe band; we never want one role label
        # to dominate the signal beyond ~2x the flat impact.
        return max(min(importance, 2.0), 0.35)

    def _has_negated_signal(self, text: str, signal_name: str) -> bool:
        return any(phrase in text for phrase in self.NEGATED_SIGNAL_PHRASES.get(signal_name, ()))

    def _evidence_text(self, item: EvidenceItemModel) -> str:
        payload: dict[str, Any] = {}
        try:
            payload = json.loads(item.payload_json)
        except json.JSONDecodeError:
            payload = {}
        fragments = [
            item.summary,
            str(payload.get("title", "")),
            str(payload.get("summary", "")),
            " ".join(str(entry) for entry in payload.get("headings", [])),
            json.dumps(payload, sort_keys=True),
        ]
        return " ".join(fragment for fragment in fragments if fragment)

    def _evidence_summaries(self, evidence_items: list[EvidenceItemModel]) -> list[dict[str, object]]:
        summaries: list[dict[str, object]] = []
        for item in evidence_items[:3]:
            payload: dict[str, Any] = {}
            try:
                payload = json.loads(item.payload_json)
            except json.JSONDecodeError:
                payload = {}
            context = str(payload.get("context_summary") or item.summary).strip()
            if len(context) > 320:
                context = f"{context[:317].rstrip()}..."
            summaries.append(
                {
                    "summary": context,
                    "confidence": item.confidence,
                    "source_title": payload.get("source_title"),
                    "source_url": payload.get("source_url"),
                }
            )
        return summaries

    def _per_match(self, total: float, matches: float) -> float:
        if matches <= 0:
            return 0.0
        return round(total / matches, 4)
