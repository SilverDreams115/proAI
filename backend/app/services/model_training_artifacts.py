import base64
import json
import math
import pickle
from collections import defaultdict
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any, ClassVar

from app.core.settings import settings
from app.models.tables import MatchModel
from app.services import model_training_math as mtm

if TYPE_CHECKING:
    from app.repositories.entity_repository import EntityRepository
    from app.repositories.result_repository import ResultRepository
    from app.repositories.training_repository import TrainingRepository
    from app.services.feature_service import FeatureService


class ModelTrainingArtifactsMixin:
    """Mixin combined with ModelTrainingService.

    These attributes are provided by the concrete subclass; declared here so
    static type checking resolves references inside the mixin without
    requiring runtime stubs.
    """

    MODEL_NAME: ClassVar[str]
    LOGISTIC_MIN_SAMPLE_SIZE: ClassVar[int]
    SMALL_SAMPLE_MAX_SIZE: ClassVar[int]
    XGBOOST_MIN_SAMPLE_SIZE: ClassVar[int]
    FEATURE_NAMES: ClassVar[list[str]]
    LABEL_TO_INDEX: ClassVar[dict[str, int]]
    INDEX_TO_LABEL: ClassVar[dict[int, str]]
    READY_HIT_RATE_THRESHOLD: ClassVar[float]
    READY_BRIER_THRESHOLD: ClassVar[float]
    READY_CONFIDENT_HIT_RATE_THRESHOLD: ClassVar[float]
    READY_MIN_CONFIDENT_PICKS: ClassVar[int]
    COMPETITION_ALIASES: ClassVar[dict[str, str]]
    COMPETITION_POLICY_OVERRIDES: ClassVar[dict[str, dict[str, Any]]]
    # Versioning of the feature engineering layer. Bumped any time
    # FEATURE_NAMES changes shape or a feature's semantics drift; the
    # artifact stores this number so a replayed prediction can verify
    # the booster was trained against the same feature schema it is
    # being scored on. Keep it aligned with FeatureService.FEATURE_SET_VERSION.
    FEATURE_SCHEMA_VERSION: ClassVar[str] = "v3"
    # Half-life of the exponential time-decay applied to training
    # sample weights. Stored in the artifact so old runs replay with
    # the original decay, even if the runtime constant later moves.
    TIME_DECAY_HALF_LIFE_DAYS: ClassVar[float] = 365.0
    # Minimum per-class probability after isotonic calibration. A
    # literal 0% on any outcome would make a rare-but-possible result
    # look impossible and tank 14/14 chances if it actually happens.
    # 2% per class still leaves 96% for the favoured outcome.
    CALIBRATION_PROBABILITY_FLOOR: ClassVar[float] = 0.02
    # Default blend weights for the heuristic engine. Tuned 2026-05-28
    # after PG-2335 review surfaced three positions where the model
    # picked the higher-Elo side against a clearly hotter opponent
    # (Albacete on a 4-game win streak, Senegal recent W vs USA recent
    # L, Mexico recent 4W vs Australia 58d idle). Raising profile from
    # 0.35 → 0.45 and lowering elo from 0.40 → 0.30 puts more weight on
    # current-form signals without abandoning the long-horizon rating.
    BLEND_WEIGHTS_DEFAULT: ClassVar[dict[str, float]] = {
        "elo": 0.30,
        "poisson": 0.25,
        "profile": 0.45,
    }

    training_repository: "TrainingRepository"
    entity_repository: "EntityRepository"
    result_repository: "ResultRepository"
    feature_service: "FeatureService"

    def _build_training_artifact(self, matches: list[MatchModel], model_name: str) -> dict[str, Any]:
        dataset = self._build_training_dataset(matches)
        # Always compute the heuristic baseline. Even when XGBoost
        # ends up the primary engine, the score-time router (Fase 2.6
        # gate) needs ratings/offense/defense/competition_profiles in
        # the artifact to fall back to heuristic for competitions
        # where the walk-forward verdict disqualified the booster.
        heuristic_artifact = self._build_heuristic_artifact(matches)

        similarity_artifact = self._train_similarity_artifact(dataset)
        if similarity_artifact is not None:
            similarity_artifact["model_name"] = model_name
            self._merge_heuristic_into_higher_tier(similarity_artifact, heuristic_artifact)
            return similarity_artifact

        xgboost_artifact = self._train_xgboost_artifact(dataset)
        if xgboost_artifact is not None:
            xgboost_artifact["model_name"] = model_name
            self._merge_heuristic_into_higher_tier(xgboost_artifact, heuristic_artifact)
            return xgboost_artifact

        heuristic_artifact["model_name"] = model_name
        return heuristic_artifact

    @staticmethod
    def _merge_heuristic_into_higher_tier(
        primary_artifact: dict[str, Any],
        heuristic_artifact: dict[str, Any],
    ) -> None:
        """Copy the heuristic-side fields onto an XGBoost / similarity
        artifact so ``_score_match_with_artifact`` can use the
        heuristic blend whenever the booster is bypassed at scoring
        time. We pick only the fields the heuristic scoring branch
        actually reads — not the entire heuristic artifact, to keep
        the persisted JSON lean.
        """
        for field in (
            "ratings",
            "offense",
            "defense",
            "competition_profiles",
            "team_profiles",
            "league_draw_rate",
            "blend_weights",
        ):
            if field in heuristic_artifact and field not in primary_artifact:
                primary_artifact[field] = heuristic_artifact[field]

    def _build_heuristic_artifact(
        self,
        matches: list[MatchModel],
        *,
        result_lookup: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        ratings: defaultdict[str, float] = defaultdict(lambda: 1500.0)
        offense: defaultdict[str, float] = defaultdict(lambda: 1.0)
        defense: defaultdict[str, float] = defaultdict(lambda: 1.0)
        competition_profiles: dict[str, dict[str, float]] = defaultdict(
            lambda: {
                "matches": 0.0,
                "home_wins": 0.0,
                "away_wins": 0.0,
                "draws": 0.0,
                "home_goals": 0.0,
                "away_goals": 0.0,
            }
        )
        team_profiles: dict[str, dict[str, float]] = defaultdict(
            lambda: {
                "matches": 0.0,
                "points": 0.0,
                "goal_balance": 0.0,
                "goals_for": 0.0,
                "goals_against": 0.0,
                "draws": 0.0,
                "home_matches": 0.0,
                "home_points": 0.0,
                "home_goal_balance": 0.0,
                "home_goals_for": 0.0,
                "home_goals_against": 0.0,
                "home_draws": 0.0,
                "away_matches": 0.0,
                "away_points": 0.0,
                "away_goal_balance": 0.0,
                "away_goals_for": 0.0,
                "away_goals_against": 0.0,
                "away_draws": 0.0,
            }
        )
        draw_count = 0.0

        sample_size = 0
        for match in matches:
            latest = (
                result_lookup.get(match.id)
                if result_lookup is not None
                else self._latest_result_for_match(match)
            )
            if latest is None:
                continue
            sample_size += 1
            home = match.home_team.name
            away = match.away_team.name
            competition_name = getattr(match.competition, "name", "Unknown League")
            competition_key = self._competition_key(competition_name)
            home_goals = float(latest.home_goals)
            away_goals = float(latest.away_goals)
            home_signal = 1.0 + latest.home_goals / 4
            away_signal = 1.0 + latest.away_goals / 4
            expected_home = 1 / (1 + 10 ** ((ratings[away] - (ratings[home] + 75.0)) / 400))
            realized_home = 1.0 if latest.result_code == "1" else 0.0 if latest.result_code == "2" else 0.5
            delta = 24 * (realized_home - expected_home)
            ratings[home] += delta
            ratings[away] -= delta
            offense[home] = (offense[home] + home_signal) / 2
            defense[away] = (defense[away] + home_signal) / 2
            offense[away] = (offense[away] + away_signal) / 2
            defense[home] = (defense[home] + away_signal) / 2
            home_points = 3.0 if home_goals > away_goals else 1.0 if home_goals == away_goals else 0.0
            away_points = 3.0 if away_goals > home_goals else 1.0 if home_goals == away_goals else 0.0
            if home_goals == away_goals:
                draw_count += 1.0

            competition_profile = competition_profiles[competition_key]
            competition_profile["matches"] += 1.0
            competition_profile["home_goals"] += home_goals
            competition_profile["away_goals"] += away_goals
            if home_goals > away_goals:
                competition_profile["home_wins"] += 1.0
            elif away_goals > home_goals:
                competition_profile["away_wins"] += 1.0
            else:
                competition_profile["draws"] += 1.0

            self._update_team_profile(
                team_profiles[home],
                is_home=True,
                points=home_points,
                goals_for=home_goals,
                goals_against=away_goals,
            )
            self._update_team_profile(
                team_profiles[away],
                is_home=False,
                points=away_points,
                goals_for=away_goals,
                goals_against=home_goals,
            )

        return {
            "model_type": "heuristic_blend",
            "trained_at": datetime.now(timezone.utc).isoformat(),
            "ratings": dict(ratings),
            "offense": dict(offense),
            "defense": dict(defense),
            "competition_profiles": dict(competition_profiles),
            "team_profiles": dict(team_profiles),
            "league_draw_rate": round(draw_count / sample_size, 4) if sample_size else 0.28,
            "blend_weights": dict(self.BLEND_WEIGHTS_DEFAULT),
            "source": "historical_match_results",
            "feature_names": self.FEATURE_NAMES,
            "training_sample_size": sample_size,
            "class_priors": self._class_priors_from_matches(matches, result_lookup=result_lookup),
        }

    def latest_artifact(self, model_name: str | None = None) -> dict[str, Any] | None:
        run = self.training_repository.latest_run(model_name or self.MODEL_NAME)
        if run is None:
            return None
        return json.loads(run.artifact_json)

    def score_match(self, match: MatchModel) -> dict[str, float]:
        artifact = self.latest_artifact()
        if artifact is None:
            artifact = self._build_heuristic_artifact(self.entity_repository.list_matches())
            if int(artifact["training_sample_size"]) <= 0:
                return {"home": 0.4, "draw": 0.3, "away": 0.3}
        return self._score_match_with_artifact(match, artifact)

    def competition_operating_policy(self, competition_name: str) -> dict[str, Any]:
        competition_key = self._competition_key(competition_name)
        policy = dict(self.COMPETITION_POLICY_OVERRIDES.get(competition_key, {}))
        if competition_key in settings.live_pick_ready_competitions:
            policy.update(
                {
                    "competition_readiness": "ready",
                    "live_pick_allowed": True,
                    "policy_reason": policy.get(
                        "policy_reason",
                        "Competition explicitly approved for live picks.",
                    ),
                }
            )
        elif competition_key in settings.live_pick_blocked_competitions:
            policy.update(
                {
                    "competition_readiness": "not_ready",
                    "live_pick_allowed": False,
                    "policy_reason": policy.get(
                        "policy_reason",
                        "Competition explicitly blocked for live picks.",
                    ),
                }
            )
        if not policy:
            # Progol placeholder fixtures (international friendlies, lower
            # tiers we don't ingest) end up under "Progol Concurso NNNN".
            # Treat them as context_only rather than unclassified so the
            # UI surfaces them as caution instead of fully blocked — the
            # model still scores them on slate-context heuristics even
            # without an audited backtest for the league.
            if competition_key.startswith("progol-concurso-"):
                policy = {
                    "competition_readiness": "context_only",
                    "live_pick_allowed": False,
                    "policy_reason": (
                        "Fixture sin historial dedicado en la base; el modelo "
                        "lo califica con contexto de quiniela. No usar como fijo."
                    ),
                    "blend_weights": {"elo": 0.25, "poisson": 0.20, "profile": 0.55},
                    "draw_bias": 0.0,
                }
            else:
                policy = {
                    "competition_readiness": "unclassified",
                    "live_pick_allowed": False,
                    "policy_reason": "Competition has no historical benchmark yet; live picks stay blocked.",
                }
        policy["competition_key"] = competition_key
        return policy

    # Empirical 90-minute draw rate observed in knockout fixtures
    # across leagues (UEFA/CONMEBOL/CONCACAF playoffs, 2018-2025).
    # Used as the calibration anchor: per-league shrinkage closes the
    # gap between that league's natural draw rate and this knockout
    # baseline. The constant is intentionally global because it
    # describes the structural change in incentives (no draw allowed
    # via penalties) more than any league-specific habit.
    KNOCKOUT_TARGET_DRAW_RATE = 0.22
    # Need this many played matches in a competition before we trust
    # the empirical draw rate enough to drive the calibration. Smaller
    # samples fall back to the conservative default band.
    KNOCKOUT_CALIBRATION_MIN_SAMPLES = 30

    def competition_draw_rate(self, competition_name: str) -> float | None:
        """Return the empirical draw rate for a competition, using the
        latest artifact's competition_profiles. ``None`` when we have
        too little data to trust the figure."""
        artifact = self.latest_artifact()
        if not artifact:
            return None
        profiles = artifact.get("competition_profiles", {})
        if not isinstance(profiles, dict):
            return None
        profile = profiles.get(self._competition_key(competition_name), {})
        matches = float(profile.get("matches", 0) or 0)
        if matches < self.KNOCKOUT_CALIBRATION_MIN_SAMPLES:
            return None
        draws = float(profile.get("draws", 0) or 0)
        if matches <= 0:
            return None
        return draws / matches

    def knockout_shrinkage_bounds(
        self,
        competition_name: str,
    ) -> tuple[float, float, dict[str, float]]:
        """Per-competition (min, max, diagnostics) shrinkage band.

        The baseline shrinkage is the fraction of the model's draw
        mass that — on average across that league — should be moved
        to L/V to close the gap between the league's empirical draw
        rate and the knockout target. The (min, max) band then
        widens around the baseline so a defensive match still
        shrinks at least a little and an open match can shrink more.

        Without enough data we fall back to a conservative default
        derived from the average league draw rate (~28%) vs the
        knockout target — that is the same shape that produced the
        previous hardcoded (0.50, 0.85) numbers, but tighter so the
        unlabeled-league fallback doesn't overcorrect.
        """
        draw_rate = self.competition_draw_rate(competition_name)
        if draw_rate is None or draw_rate < 0.05:
            return 0.15, 0.55, {
                "league_draw_rate": float(draw_rate or 0.0),
                "baseline": 0.21,
                "calibrated": 0.0,
            }
        baseline = max(0.0, 1.0 - self.KNOCKOUT_TARGET_DRAW_RATE / draw_rate)
        # Defensive matches keep half the baseline; open matches go
        # up to baseline * 2, capped at 0.90 so we never blow away
        # the entire draw mass.
        shrink_min = max(0.10, baseline * 0.6)
        shrink_max = min(0.90, max(baseline * 2.0, baseline + 0.20))
        diagnostics = {
            "league_draw_rate": draw_rate,
            "baseline": baseline,
            "calibrated": 1.0,
        }
        return shrink_min, shrink_max, diagnostics

    # Refresh the backtest verdict from disk at most this often. The
    # index file is only rewritten when `make publish-backtest` runs;
    # 60s is more than enough to pick up a fresh roll-out while
    # avoiding a disk-read per scored match.
    _XGBOOST_VERDICT_TTL_SECONDS: ClassVar[float] = 60.0
    # Paths searched for the published backtest verdict, in order.
    # Exposed as a class attribute so tests can point them at an
    # isolated tmpdir without touching the production /data volume.
    _XGBOOST_VERDICT_PATHS: ClassVar[tuple[str, ...]] = (
        "/data/backtest_history/index.json",
        "reports/backtest_history/index.json",
    )
    _xgboost_verdict_cache: ClassVar[dict[str, Any]] = {
        "loaded_at": 0.0,
        "approved": frozenset[str](),
        "available": False,
    }

    @classmethod
    def _xgboost_approved_competitions(cls) -> frozenset[str]:
        """Return the set of competition keys where the latest
        walk-forward backtest declared XGBoost the winner. Used by the
        score router to bypass the booster for competitions where
        heuristic actually performs better — the walk-forward
        (Fase 2.6) revealed that's most of them today.

        Returns an empty set when no published backtest exists, which
        keeps the legacy "use XGBoost when present" behaviour intact
        until a verdict is produced.
        """
        import json as _json
        import time
        from pathlib import Path

        cache = cls._xgboost_verdict_cache
        now = time.time()
        if cache["available"] and now - float(cache["loaded_at"]) < cls._XGBOOST_VERDICT_TTL_SECONDS:
            return cache["approved"]  # type: ignore[return-value]
        # Match the CLI default: /data/backtest_history is the
        # persistent location across container rebuilds. We also
        # check the legacy reports/ path so a freshly-deployed
        # backend that hasn't republished yet still picks up the old
        # verdict. Tests substitute _XGBOOST_VERDICT_PATHS to an
        # isolated tmpdir so a stale prod verdict doesn't leak into
        # a synthetic scenario.
        index_path: Path | None = None
        for candidate in cls._XGBOOST_VERDICT_PATHS:
            candidate_path = Path(candidate)
            if candidate_path.is_file():
                index_path = candidate_path
                break
        if index_path is None:
            cache.update(loaded_at=now, approved=frozenset(), available=False)
            return cache["approved"]  # type: ignore[return-value]
        try:
            data = _json.loads(index_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            cache.update(loaded_at=now, approved=frozenset(), available=False)
            return cache["approved"]  # type: ignore[return-value]
        approved: set[str] = set()
        for entry in data.get("competitions", []):
            if entry.get("xgboost_beats_heuristic") is True:
                key = entry.get("competition_key")
                if isinstance(key, str):
                    approved.add(key)
        cache.update(loaded_at=now, approved=frozenset(approved), available=True)
        return cache["approved"]  # type: ignore[return-value]

    @classmethod
    def reset_xgboost_verdict_cache(cls) -> None:
        cls._xgboost_verdict_cache.update(
            loaded_at=0.0, approved=frozenset(), available=False
        )

    def _score_match_with_artifact(self, match: MatchModel, artifact: dict[str, Any]) -> dict[str, float]:
        from time import perf_counter

        from app.core.metrics import metrics_store

        started = perf_counter()
        if artifact.get("model_type") == "xgboost_multiclass":
            # Walk-forward gate: only use the booster when the latest
            # backtest verdict says it actually beats heuristic for
            # this match's competition. Otherwise drop to the
            # heuristic branch below — same code path as if no
            # booster were present.
            # IMPORTANT: call the loader first so the cache's
            # `available` flag reflects this run's read, not a stale
            # initialization.
            approved = self._xgboost_approved_competitions()
            verdict_available = bool(self._xgboost_verdict_cache.get("available", False))
            competition_name = getattr(match.competition, "name", "")
            competition_key = self._competition_key(competition_name)
            if verdict_available and competition_key not in approved:
                metrics_store.record_prediction(
                    engine="xgboost_bypassed", duration_seconds=0.0
                )
            else:
                scored = self._score_with_xgboost(match, artifact)
                if scored is not None:
                    duration_seconds = perf_counter() - started
                    metrics_store.record_prediction(engine="xgboost", duration_seconds=duration_seconds)
                    final = self._finalize_scores(scored, artifact, match)
                    self._emit_scoring_log(
                        match=match, engine="xgboost", raw=scored, final=final,
                        duration_seconds=duration_seconds, artifact=artifact,
                    )
                    return final
        if artifact.get("model_type") == "similarity_knn":
            scored = self._score_with_small_sample_rule(match)
            if scored is not None:
                duration_seconds = perf_counter() - started
                metrics_store.record_prediction(
                    engine="similarity_knn", duration_seconds=duration_seconds
                )
                final = self._finalize_scores(scored, artifact, match)
                self._emit_scoring_log(
                    match=match, engine="similarity_knn", raw=scored, final=final,
                    duration_seconds=duration_seconds, artifact=artifact,
                )
                return final
        ratings = artifact.get("ratings", {})
        offense = artifact.get("offense", {})
        defense = artifact.get("defense", {})
        weights = artifact.get("blend_weights", dict(self.BLEND_WEIGHTS_DEFAULT))
        competition_profiles = artifact.get("competition_profiles", {})
        team_profiles = artifact.get("team_profiles", {})
        league_draw_rate = float(artifact.get("league_draw_rate", 0.28))

        home_name = match.home_team.name
        away_name = match.away_team.name
        competition_name = getattr(match.competition, "name", "Unknown League")
        competition_key = self._competition_key(competition_name)
        competition_policy = self.competition_operating_policy(competition_name)
        competition_profile = (
            competition_profiles.get(competition_key, {})
            if isinstance(competition_profiles, dict)
            else {}
        )
        weights = competition_policy.get("blend_weights") or weights

        home_rating = float(ratings.get(home_name, 1500.0))
        away_rating = float(ratings.get(away_name, 1500.0))
        competition_home_bonus = self._competition_home_bonus(competition_profile)
        elo_home = 1 / (1 + 10 ** ((away_rating - (home_rating + competition_home_bonus)) / 400))

        # Dixon-Coles bivariate-Poisson scoring component.
        # offense[t] and defense[t] are exponential-moving averages of goals
        # scored/conceded per match. The league baseline lambda comes from the
        # competition profile (mean goals per side). The expected scoring
        # rates feed a Poisson grid with the DC correction for low scores.
        league_home_lambda, league_away_lambda = self._competition_lambda_priors(competition_profile)
        home_lambda = max(
            league_home_lambda
            * max(float(offense.get(home_name, 1.0)), 0.05)
            * max(float(defense.get(away_name, 1.0)), 0.05),
            0.05,
        )
        away_lambda = max(
            league_away_lambda
            * max(float(offense.get(away_name, 1.0)), 0.05)
            * max(float(defense.get(home_name, 1.0)), 0.05),
            0.05,
        )
        league_draw_share = max(min(league_draw_rate, 0.45), 0.18)
        dc_rho = self._dixon_coles_rho_from_draw_rate(league_draw_share)
        poisson_home, poisson_draw, poisson_away = self._dixon_coles_outcome(
            home_lambda, away_lambda, dc_rho
        )
        profile_home = self._team_profile_strength(team_profiles.get(home_name), is_home=True)
        profile_away = self._team_profile_strength(team_profiles.get(away_name), is_home=False)
        profile_edge = max(min(profile_home - profile_away, 1.5), -1.5)
        profile_home_share = 1.0 / (1.0 + math.exp(-profile_edge * 1.35))
        profile_away_share = 1.0 - profile_home_share

        elo_away = 1.0 - elo_home
        home_prob = (
            float(weights["elo"]) * elo_home
            + float(weights["poisson"]) * poisson_home
            + float(weights.get("profile", 0.0)) * profile_home_share
        )
        away_prob = (
            float(weights["elo"]) * elo_away
            + float(weights["poisson"]) * poisson_away
            + float(weights.get("profile", 0.0)) * profile_away_share
        )
        # Draw probability comes from the Dixon-Coles grid (a real
        # probability, not a smoothing constant). The competition draw bias
        # only nudges it within a bounded range.
        draw_bias = float(competition_policy.get("draw_bias", 0.0))
        draw_prob = min(max(poisson_draw + draw_bias, 0.08), 0.42)

        total = home_prob + away_prob + draw_prob
        heuristic = {
            "home": round(home_prob / total, 3),
            "draw": round(draw_prob / total, 3),
            "away": round(away_prob / total, 3),
        }
        duration_seconds = perf_counter() - started
        metrics_store.record_prediction(
            engine="heuristic_blend", duration_seconds=duration_seconds
        )
        final = self._finalize_scores(heuristic, artifact, match)
        self._emit_scoring_log(
            match=match, engine="heuristic_blend", raw=heuristic, final=final,
            duration_seconds=duration_seconds, artifact=artifact,
        )
        return final

    def _emit_scoring_log(
        self,
        *,
        match: MatchModel,
        engine: str,
        raw: dict[str, float],
        final: dict[str, float],
        duration_seconds: float,
        artifact: dict[str, Any],
    ) -> None:
        """Emit one structured JSON log line per scoring event.

        Captures everything an operator needs to retrace a prediction:
        which engine ran, raw vs calibrated probabilities, latency, and
        whether per-league calibration was applied. Loud enough to debug
        with `docker logs`, structured enough for downstream ingestion.
        """
        import logging

        competition_name = getattr(match.competition, "name", "Unknown League")
        competition_key = self._competition_key(competition_name)
        calibration_curves = artifact.get("calibration_curves")
        calibration_applied = (
            isinstance(calibration_curves, dict)
            and competition_key in calibration_curves
        )
        logger = logging.getLogger("proai.scoring")
        logger.info(
            "match scored",
            extra={
                "event": "match_scored",
                "match_id": getattr(match, "id", None),
                "competition_key": competition_key,
                "competition_name": competition_name,
                "home_team": getattr(match.home_team, "name", None),
                "away_team": getattr(match.away_team, "name", None),
                "engine": engine,
                "model_name": artifact.get("model_name"),
                "raw_probabilities": {k: round(float(v), 4) for k, v in raw.items()},
                "final_probabilities": {k: round(float(v), 4) for k, v in final.items()},
                "calibration_applied": calibration_applied,
                "latency_ms": round(duration_seconds * 1000.0, 3),
            },
        )

    # --- Dixon-Coles helpers --------------------------------------------------

    DIXON_COLES_GRID_MAX = mtm.DIXON_COLES_GRID_MAX

    def _competition_lambda_priors(self, competition_profile: Any) -> tuple[float, float]:
        """Mean home/away goals per match from the competition profile.

        Falls back to a sensible global prior when the competition has too
        little history. The defaults (1.45 home, 1.15 away) approximate the
        long-run average across the leagues currently supported."""
        default = (1.45, 1.15)
        if not isinstance(competition_profile, dict):
            return default
        matches = float(competition_profile.get("matches", 0.0))
        if matches < 10:
            return default
        home_lambda = float(competition_profile.get("home_goals", 0.0)) / matches
        away_lambda = float(competition_profile.get("away_goals", 0.0)) / matches
        return (max(home_lambda, 0.4), max(away_lambda, 0.3))

    def _dixon_coles_rho_from_draw_rate(self, league_draw_rate: float) -> float:
        return mtm.dixon_coles_rho_from_draw_rate(league_draw_rate)

    def _dixon_coles_outcome(
        self, home_lambda: float, away_lambda: float, rho: float
    ) -> tuple[float, float, float]:
        return mtm.dixon_coles_outcome(
            home_lambda, away_lambda, rho, max_goals=self.DIXON_COLES_GRID_MAX
        )

    def _dixon_coles_tau(
        self, home_goals: int, away_goals: int, home_lambda: float, away_lambda: float, rho: float
    ) -> float:
        return mtm.dixon_coles_tau(home_goals, away_goals, home_lambda, away_lambda, rho)

    def _poisson_pmf(self, lam: float, k: int) -> float:
        return mtm.poisson_pmf(lam, k)

    def _competition_key(self, competition_name: str) -> str:
        normalized = "-".join(
            token
            for token in "".join(
                character.lower() if character.isalnum() else "-"
                for character in competition_name.strip()
            ).split("-")
            if token
        )
        return self.COMPETITION_ALIASES.get(normalized, normalized)

    def _empty_evaluation(
        self,
        model_name: str,
        min_training_matches: int,
        confidence_threshold: float,
        matches_considered: int,
    ) -> dict[str, Any]:
        return {
            "model_name": model_name,
            "evaluation_mode": "walk_forward",
            "matches_considered": matches_considered,
            "matches_evaluated": 0,
            "min_training_matches": min_training_matches,
            "confidence_threshold": confidence_threshold,
            "hit_rate": 0.0,
            "brier_score": 0.0,
            "log_loss": 0.0,
            "confident_pick_rate": 0.0,
            "confident_pick_hit_rate": 0.0,
            "ready_for_live_picks": False,
            "verdict": "insufficient_data",
            "thresholds": {
                "hit_rate": self.READY_HIT_RATE_THRESHOLD,
                "brier_score_max": self.READY_BRIER_THRESHOLD,
                "confident_hit_rate": self.READY_CONFIDENT_HIT_RATE_THRESHOLD,
                "min_confident_picks": self.READY_MIN_CONFIDENT_PICKS,
            },
        }

    def _match_sort_key(self, match: MatchModel) -> tuple[datetime, str]:
        results = self.result_repository.list_results_for_match(match.id)
        if results:
            played_at = results[0].played_at
        else:
            played_at = match.kickoff_at
        if played_at.tzinfo is None:
            played_at = played_at.replace(tzinfo=timezone.utc)
        return played_at, match.id

    def _brier_score(self, probabilities: list[float], actual_index: int) -> float:
        return mtm.brier_score(probabilities, actual_index)

    def _log_loss(self, probabilities: list[float], actual_index: int) -> float:
        return mtm.log_loss(probabilities, actual_index)

    def _build_training_dataset(self, matches: list[MatchModel]) -> dict[str, Any]:
        rows: list[list[float]] = []
        labels: list[int] = []
        played_at: list[datetime] = []
        classes_seen: set[int] = set()
        for match in matches:
            results = self.result_repository.list_results_for_match(match.id)
            if not results:
                continue
            target = self.LABEL_TO_INDEX.get(results[0].result_code)
            if target is None:
                continue
            feature_map = self.feature_service.build_model_features(match, cutoff=match.kickoff_at)
            rows.append([float(feature_map.get(name, 0.0)) for name in self.FEATURE_NAMES])
            labels.append(target)
            played_at.append(results[0].played_at)
            classes_seen.add(target)
        return {
            "rows": rows,
            "labels": labels,
            "played_at": played_at,
            "sample_size": len(rows),
            "classes_seen": sorted(classes_seen),
        }

    @staticmethod
    def _time_decay_weights(
        played_at: list[datetime],
        *,
        half_life_days: float = 365.0,
    ) -> list[float]:
        return mtm.time_decay_weights(played_at, half_life_days=half_life_days)

    def _train_xgboost_artifact(self, dataset: dict[str, Any]) -> dict[str, Any] | None:
        if dataset["sample_size"] < self.XGBOOST_MIN_SAMPLE_SIZE or len(dataset["classes_seen"]) < 2:
            return None
        # XGBoost is a hard runtime dependency: see backend/pyproject.toml.
        # We use the native Booster API (xgboost.train + DMatrix) on
        # purpose — the sklearn-compatible XGBClassifier wrapper imports
        # sklearn at construction time and proAI does not ship sklearn.
        import xgboost as xgb

        # F7.1 time-decay: weight each match by exp(-age / half-life)
        # so the booster cares more about recent results than seasons
        # past. The half-life lives on the mixin so artifacts can replay
        # with the exact constant used at training time.
        sample_weights = ModelTrainingArtifactsMixin._time_decay_weights(
            dataset.get("played_at", []),
            half_life_days=self.TIME_DECAY_HALF_LIFE_DAYS,
        )
        dtrain = xgb.DMatrix(
            dataset["rows"],
            label=dataset["labels"],
            weight=sample_weights if sample_weights else None,
        )
        params = {
            "objective": "multi:softprob",
            "num_class": 3,
            "max_depth": 4,
            "learning_rate": 0.05,
            "subsample": 0.9,
            "colsample_bytree": 0.9,
            "min_child_weight": 1.0,
            "reg_lambda": 1.0,
            "seed": 42,
            "eval_metric": "mlogloss",
            "verbosity": 0,
        }
        num_boost_round = 160
        booster = xgb.train(params, dtrain, num_boost_round=num_boost_round)
        # XGBoost has a native JSON serialization that does not need pickle.
        # Closes C1 (RCE via pickle.loads from a compromised DB row).
        # We carry the booster JSON as a transient field; the training
        # service persists it to disk via `artifact_storage` so the DB
        # row stores only the storage descriptor (path + sha256).
        booster_json = booster.save_raw(raw_format="json").decode("utf-8")
        class_counts = {
            self.INDEX_TO_LABEL[class_index]: dataset["labels"].count(class_index)
            for class_index in range(3)
        }
        return {
            "model_type": "xgboost_multiclass",
            "trained_at": datetime.now(timezone.utc).isoformat(),
            "source": "historical_match_results_plus_context",
            "feature_names": self.FEATURE_NAMES,
            "feature_schema_version": self.FEATURE_SCHEMA_VERSION,
            "training_sample_size": dataset["sample_size"],
            "class_counts": class_counts,
            "class_priors": self._class_priors(dataset),
            # Persist the booster hyperparameters + boosting rounds so
            # an old prediction can be replayed exactly even if the
            # source defaults later drift. Stored alongside the booster
            # JSON in the artifact; the runtime reads them back via
            # `xgboost_params` when reconstructing the booster.
            "xgboost_params": dict(params),
            "xgboost_num_boost_round": num_boost_round,
            "time_decay_half_life_days": self.TIME_DECAY_HALF_LIFE_DAYS,
            "_booster_json_transient": booster_json,
        }

    def _train_similarity_artifact(self, dataset: dict[str, Any]) -> dict[str, Any] | None:
        if dataset["sample_size"] < self.LOGISTIC_MIN_SAMPLE_SIZE or len(dataset["classes_seen"]) < 2:
            return None
        if dataset["sample_size"] > self.SMALL_SAMPLE_MAX_SIZE:
            return None
        return {
            "model_type": "similarity_knn",
            "trained_at": datetime.now(timezone.utc).isoformat(),
            "source": "historical_match_results_plus_context",
            "feature_names": self.FEATURE_NAMES,
            "training_sample_size": dataset["sample_size"],
            "class_counts": {
                self.INDEX_TO_LABEL[class_index]: dataset["labels"].count(class_index)
                for class_index in range(3)
            },
            "class_priors": self._class_priors(dataset),
            "rows": dataset["rows"],
            "labels": dataset["labels"],
        }

    def _load_booster_json(self, artifact: dict[str, Any]) -> str | None:
        """Return the booster JSON from either the on-disk descriptor (the
        Fase 2.1 default) or the legacy inline field. Returns None when
        neither is available."""
        descriptor = artifact.get("booster_storage")
        if isinstance(descriptor, dict):
            from app.services.artifact_storage import load_booster_json

            loaded = load_booster_json({str(k): str(v) for k, v in descriptor.items()})
            if loaded is not None:
                return loaded
        # Legacy inline storage (kept for one release so old DB rows still
        # score). New training runs always write the descriptor instead.
        inline = artifact.get("booster_json")
        return inline if isinstance(inline, str) else None

    def _score_with_xgboost(self, match: MatchModel, artifact: dict[str, Any]) -> dict[str, float] | None:
        booster_json = self._load_booster_json(artifact)
        feature_names = artifact.get("feature_names", self.FEATURE_NAMES)
        if not isinstance(booster_json, str) or not isinstance(feature_names, list):
            # Legacy artifacts may still carry pickle blobs. They are only
            # loadable when explicitly allowed; default-off in production.
            return self._score_with_xgboost_legacy_pickle(match, artifact)
        # XGBoost is a hard runtime dependency: see backend/pyproject.toml.
        import xgboost

        booster = xgboost.Booster()
        booster.load_model(bytearray(booster_json, encoding="utf-8"))
        feature_map = self.feature_service.build_model_features(match, cutoff=match.kickoff_at)
        row = [[float(feature_map.get(str(name), 0.0)) for name in feature_names]]
        probabilities = booster.predict(xgboost.DMatrix(row))[0]
        if len(probabilities) != 3:
            return None
        return {
            "home": round(float(probabilities[0]), 3),
            "draw": round(float(probabilities[1]), 3),
            "away": round(float(probabilities[2]), 3),
        }

    def _score_with_xgboost_legacy_pickle(
        self, match: MatchModel, artifact: dict[str, Any]
    ) -> dict[str, float] | None:
        """Loads a legacy pickle-format XGBoost artifact only when the
        operator explicitly enables it via PROAI_ALLOW_PICKLE_MODEL_ARTIFACTS.

        New training runs persist with `booster_json`; this path exists only
        to score against artifacts trained before the format change."""
        if not settings.allow_pickle_model_artifacts:
            return None
        model_blob = artifact.get("model_blob")
        feature_names = artifact.get("feature_names", self.FEATURE_NAMES)
        if not isinstance(model_blob, str) or not isinstance(feature_names, list):
            return None
        try:
            model = pickle.loads(base64.b64decode(model_blob.encode("ascii")))
        except (ValueError, pickle.PickleError):
            return None
        feature_map = self.feature_service.build_model_features(match, cutoff=match.kickoff_at)
        row = [[float(feature_map.get(str(name), 0.0)) for name in feature_names]]
        probabilities = model.predict_proba(row)[0]
        if len(probabilities) != 3:
            return None
        return {
            "home": round(float(probabilities[0]), 3),
            "draw": round(float(probabilities[1]), 3),
            "away": round(float(probabilities[2]), 3),
        }

    def _score_with_similarity(self, match: MatchModel, artifact: dict[str, Any]) -> dict[str, float] | None:
        rows = artifact.get("rows")
        labels = artifact.get("labels")
        feature_names = artifact.get("feature_names", self.FEATURE_NAMES)
        if not isinstance(rows, list) or not isinstance(labels, list) or not isinstance(feature_names, list):
            return None
        feature_map = self.feature_service.build_model_features(match, cutoff=match.kickoff_at)
        target = [float(feature_map.get(str(name), 0.0)) for name in feature_names]
        scored = {0: 0.0, 1: 0.0, 2: 0.0}
        for row, label in zip(rows, labels, strict=False):
            if not isinstance(row, list) or not isinstance(label, int):
                continue
            distance = math.sqrt(sum((float(a) - float(b)) ** 2 for a, b in zip(target, row, strict=False)))
            scored[label] += 1.0 / max(distance, 0.05)
        total = sum(scored.values())
        if total <= 0:
            return None
        return {
            "home": round(scored[0] / total, 3),
            "draw": round(scored[1] / total, 3),
            "away": round(scored[2] / total, 3),
        }

    def _score_with_small_sample_rule(self, match: MatchModel) -> dict[str, float]:
        feature_map = self.feature_service.build_model_features(match, cutoff=match.kickoff_at)
        form_gap = float(feature_map.get("form_gap", 0.0))
        goal_gap = float(feature_map.get("goal_balance_gap", 0.0))
        rest_gap = float(feature_map.get("rest_gap_days", 0.0))
        head_to_head_points_gap = float(feature_map.get("head_to_head_points_gap", 0.0))
        head_to_head_goal_gap = float(feature_map.get("head_to_head_goal_balance_gap", 0.0))
        context_gap = float(feature_map.get("away_context_signal", 0.0)) - float(
            feature_map.get("home_context_signal", 0.0)
        )
        raw_strength = (
            (0.75 * form_gap)
            + (0.55 * goal_gap)
            + (0.08 * rest_gap)
            + (0.35 * head_to_head_points_gap)
            + (0.22 * head_to_head_goal_gap)
            + (0.45 * context_gap)
        )
        strength = max(min(raw_strength, 4.0), -4.0)
        draw_probability = max(0.18, 0.34 - min(abs(strength) * 0.05, 0.16))
        home_share = 1.0 / (1.0 + math.exp(-strength))
        residual = 1.0 - draw_probability
        home_probability = residual * home_share
        away_probability = residual * (1.0 - home_share)
        return {
            "home": round(home_probability, 3),
            "draw": round(draw_probability, 3),
            "away": round(away_probability, 3),
        }

    def _class_priors(self, dataset: dict[str, Any]) -> dict[str, float]:
        sample_size = max(int(dataset["sample_size"]), 1)
        return {
            self.INDEX_TO_LABEL[class_index]: round(
                (dataset["labels"].count(class_index) + 1) / (sample_size + 3),
                4,
            )
            for class_index in range(3)
        }

    def _class_priors_from_matches(
        self,
        matches: list[MatchModel],
        *,
        result_lookup: dict[str, Any] | None = None,
    ) -> dict[str, float]:
        labels: list[int] = []
        for match in matches:
            latest = (
                result_lookup.get(match.id)
                if result_lookup is not None
                else self._latest_result_for_match(match)
            )
            if latest is None:
                continue
            target = self.LABEL_TO_INDEX.get(latest.result_code)
            if target is not None:
                labels.append(target)
        return self._class_priors({"labels": labels, "sample_size": len(labels)})

    def _latest_result_for_match(self, match: MatchModel):
        prefetched_results = getattr(match, "results", None)
        if prefetched_results:
            return sorted(prefetched_results, key=lambda item: item.played_at, reverse=True)[0]
        results = self.result_repository.list_results_for_match(match.id)
        return results[0] if results else None

    def _update_team_profile(
        self,
        profile: dict[str, float],
        *,
        is_home: bool,
        points: float,
        goals_for: float,
        goals_against: float,
    ) -> None:
        profile["matches"] += 1.0
        profile["points"] += points
        profile["goals_for"] += goals_for
        profile["goals_against"] += goals_against
        profile["goal_balance"] += goals_for - goals_against
        if goals_for == goals_against:
            profile["draws"] += 1.0
        prefix = "home" if is_home else "away"
        profile[f"{prefix}_matches"] += 1.0
        profile[f"{prefix}_points"] += points
        profile[f"{prefix}_goals_for"] += goals_for
        profile[f"{prefix}_goals_against"] += goals_against
        profile[f"{prefix}_goal_balance"] += goals_for - goals_against
        if goals_for == goals_against:
            profile[f"{prefix}_draws"] += 1.0

    def _team_profile_strength(self, profile: Any, *, is_home: bool) -> float:
        if not isinstance(profile, dict):
            return 0.0
        prefix = "home" if is_home else "away"
        split_matches = max(float(profile.get(f"{prefix}_matches", 0.0)), 0.0)
        total_matches = max(float(profile.get("matches", 0.0)), 0.0)
        split_weight = 0.7 if split_matches >= 5 else 0.45
        split_points = self._safe_rate(profile.get(f"{prefix}_points", 0.0), split_matches)
        split_goal_balance = self._safe_rate(profile.get(f"{prefix}_goal_balance", 0.0), split_matches)
        split_scoring = self._safe_rate(profile.get(f"{prefix}_goals_for", 0.0), split_matches)
        split_conceding = self._safe_rate(profile.get(f"{prefix}_goals_against", 0.0), split_matches)
        overall_points = self._safe_rate(profile.get("points", 0.0), total_matches)
        overall_goal_balance = self._safe_rate(profile.get("goal_balance", 0.0), total_matches)
        overall_scoring = self._safe_rate(profile.get("goals_for", 0.0), total_matches)
        overall_conceding = self._safe_rate(profile.get("goals_against", 0.0), total_matches)
        points_component = split_weight * split_points + (1.0 - split_weight) * overall_points
        goal_component = split_weight * split_goal_balance + (1.0 - split_weight) * overall_goal_balance
        attack_component = split_weight * split_scoring + (1.0 - split_weight) * overall_scoring
        defense_component = split_weight * split_conceding + (1.0 - split_weight) * overall_conceding
        return (
            0.55 * points_component
            + 0.3 * goal_component
            + 0.12 * attack_component
            - 0.1 * defense_component
        )

    def _draw_tendency(
        self,
        home_profile: Any,
        away_profile: Any,
        league_draw_rate: float,
        competition_profile: Any,
    ) -> float:
        home_draw_rate = self._profile_draw_rate(home_profile, is_home=True)
        away_draw_rate = self._profile_draw_rate(away_profile, is_home=False)
        overall_home_draw = self._profile_draw_rate(home_profile, is_home=None)
        overall_away_draw = self._profile_draw_rate(away_profile, is_home=None)
        competition_draw_rate = self._competition_draw_rate(competition_profile, league_draw_rate)
        return (
            0.25 * league_draw_rate
            + 0.3 * competition_draw_rate
            + 0.25 * home_draw_rate
            + 0.25 * away_draw_rate
            + 0.05 * overall_home_draw
            + 0.05 * overall_away_draw
        )

    def _profile_draw_rate(self, profile: Any, *, is_home: bool | None) -> float:
        if not isinstance(profile, dict):
            return 0.0
        if is_home is None:
            return self._safe_rate(profile.get("draws", 0.0), profile.get("matches", 0.0))
        prefix = "home" if is_home else "away"
        return self._safe_rate(profile.get(f"{prefix}_draws", 0.0), profile.get(f"{prefix}_matches", 0.0))

    def _safe_rate(self, numerator: Any, denominator: Any) -> float:
        return mtm.safe_rate(numerator, denominator)

    def _competition_draw_rate(self, competition_profile: Any, fallback: float) -> float:
        if not isinstance(competition_profile, dict):
            return fallback
        matches = float(competition_profile.get("matches", 0.0))
        if matches <= 0:
            return fallback
        return self._safe_rate(competition_profile.get("draws", 0.0), matches)

    def _competition_home_bonus(self, competition_profile: Any) -> float:
        if not isinstance(competition_profile, dict):
            return 75.0
        matches = float(competition_profile.get("matches", 0.0))
        if matches < 10:
            return 75.0
        home_win_rate = self._safe_rate(competition_profile.get("home_wins", 0.0), matches)
        away_win_rate = self._safe_rate(competition_profile.get("away_wins", 0.0), matches)
        goal_gap = self._safe_rate(
            float(competition_profile.get("home_goals", 0.0)) - float(competition_profile.get("away_goals", 0.0)),
            matches,
        )
        bonus = 55.0 + ((home_win_rate - away_win_rate) * 120.0) + (goal_gap * 18.0)
        return max(min(bonus, 110.0), 35.0)

    def _match_sort_key_from_result(self, result, match_id: str) -> tuple[datetime, str]:
        played_at = result.played_at
        if played_at.tzinfo is None:
            played_at = played_at.replace(tzinfo=timezone.utc)
        return played_at, match_id

    def _finalize_scores(
        self,
        scored: dict[str, float],
        artifact: dict[str, Any],
        match: MatchModel,
    ) -> dict[str, float]:
        """Apply per-league isotonic calibration when available, otherwise
        fall back to the class-priors blend.

        Calibration curves are stored in `artifact["calibration_curves"]` as
        `{competition_key: {"1"|"X"|"2": [[x, y], ...]}}`. When a curve
        covers the match's league for all three classes, the raw
        probabilities are mapped through PAV (Fase 1.2). The result is then
        renormalized to a proper distribution.
        """
        curves = artifact.get("calibration_curves")
        if isinstance(curves, dict):
            competition_name = getattr(match.competition, "name", "Unknown League")
            league_curves = curves.get(self._competition_key(competition_name))
            if isinstance(league_curves, dict):
                calibrated = self._apply_isotonic_curves(scored, league_curves)
                if calibrated is not None:
                    return calibrated
        return self._blend_with_priors(scored, artifact)

    def _apply_isotonic_curves(
        self,
        scored: dict[str, float],
        league_curves: dict[str, Any],
    ) -> dict[str, float] | None:
        """Map (home, draw, away) through their per-class isotonic curves.

        Returns None if any of the three classes lacks a curve — the caller
        falls back to the priors blend. The output is renormalized so it
        remains a valid probability vector after calibration."""
        from app.services.calibration import apply_isotonic

        breakpoints_by_class: dict[str, list[tuple[float, float]]] = {}
        for label in ("1", "X", "2"):
            raw_curve = league_curves.get(label)
            if not isinstance(raw_curve, list) or not raw_curve:
                return None
            breakpoints: list[tuple[float, float]] = []
            for point in raw_curve:
                if not isinstance(point, (list, tuple)) or len(point) != 2:
                    return None
                breakpoints.append((float(point[0]), float(point[1])))
            breakpoints_by_class[label] = breakpoints

        calibrated_home = apply_isotonic(breakpoints_by_class["1"], float(scored["home"]))
        calibrated_draw = apply_isotonic(breakpoints_by_class["X"], float(scored["draw"]))
        calibrated_away = apply_isotonic(breakpoints_by_class["2"], float(scored["away"]))
        # Floor: isotonic curves can collapse a class to exactly 0 when
        # the held-out slice had no samples in that bucket. The boleta
        # cares about 14/14 — a literal 0% on any outcome makes a
        # rare-but-possible result look impossible. Pin each class to
        # at least 2% so even unfavoured outcomes still carry a
        # non-negligible posterior.
        calibrated_home = max(calibrated_home, self.CALIBRATION_PROBABILITY_FLOOR)
        calibrated_draw = max(calibrated_draw, self.CALIBRATION_PROBABILITY_FLOOR)
        calibrated_away = max(calibrated_away, self.CALIBRATION_PROBABILITY_FLOOR)
        total = calibrated_home + calibrated_draw + calibrated_away
        if total <= 0:
            return None
        return {
            "home": round(calibrated_home / total, 3),
            "draw": round(calibrated_draw / total, 3),
            "away": round(calibrated_away / total, 3),
        }

    def _blend_with_priors(self, scored: dict[str, float], artifact: dict[str, Any]) -> dict[str, float]:
        priors = artifact.get("class_priors", {})
        if not isinstance(priors, dict):
            return scored
        blended = {
            "home": 0.82 * scored["home"] + 0.18 * float(priors.get("1", 0.33)),
            "draw": 0.82 * scored["draw"] + 0.18 * float(priors.get("X", 0.33)),
            "away": 0.82 * scored["away"] + 0.18 * float(priors.get("2", 0.33)),
        }
        total = blended["home"] + blended["draw"] + blended["away"]
        return {
            "home": round(blended["home"] / total, 3),
            "draw": round(blended["draw"] / total, 3),
            "away": round(blended["away"] / total, 3),
        }
