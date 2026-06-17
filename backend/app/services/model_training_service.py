import json
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.core.metrics import metrics_store
from app.db.session import managed_transaction
from app.models.tables import MatchModel
from app.repositories.feature_repository import FeatureRepository
from app.repositories.entity_repository import EntityRepository
from app.repositories.result_repository import ResultRepository
from app.repositories.training_repository import TrainingRepository
from app.services.feature_service import FeatureService
from app.services.model_training_artifacts import ModelTrainingArtifactsMixin
from app.services.model_training_metrics import (
    WalkForwardThresholds,
    drift_severity as _drift_severity,
    summarize_walk_forward as _summarize_walk_forward_pure,
)


class ModelTrainingService(ModelTrainingArtifactsMixin):
    MODEL_NAME = "elo_poisson_blend"
    LOGISTIC_MIN_SAMPLE_SIZE = 6
    SMALL_SAMPLE_MAX_SIZE = 20
    XGBOOST_MIN_SAMPLE_SIZE = 30
    FEATURE_NAMES = [
        "home_points_per_match",
        "away_points_per_match",
        "home_goal_balance_per_match",
        "away_goal_balance_per_match",
        "home_goals_for_per_match",
        "away_goals_for_per_match",
        "home_goals_against_per_match",
        "away_goals_against_per_match",
        "form_gap",
        "goal_balance_gap",
        "rest_gap_days",
        "head_to_head_matches",
        "head_to_head_points_gap",
        "head_to_head_goal_balance_gap",
        "evidence_count",
        "injury_signal_total",
        "suspension_signal_total",
        "rotation_signal_total",
        "home_context_signal",
        "away_context_signal",
        "home_injury_signals",
        "away_injury_signals",
        "home_suspension_signals",
        "away_suspension_signals",
        "home_rotation_signals",
        "away_rotation_signals",
        "same_country_matchup",
        "venue_known",
        "home_advantage",
    ]
    LABEL_TO_INDEX = {"1": 0, "X": 1, "2": 2}
    INDEX_TO_LABEL = {0: "1", 1: "X", 2: "2"}
    READY_HIT_RATE_THRESHOLD = 0.5
    READY_BRIER_THRESHOLD = 0.62
    READY_CONFIDENT_HIT_RATE_THRESHOLD = 0.55
    READY_MIN_CONFIDENT_PICKS = 8
    COMPETITION_ALIASES = {
        "e0": "e0",
        "english-premier-league": "e0",
        "premier-league": "e0",
        "premierleague": "e0",
        "sp1": "sp1",
        "la-liga": "sp1",
        "laliga": "sp1",
        "spanish-la-liga": "sp1",
        "f1": "f1",
        "ligue-1": "f1",
        "ligue1": "f1",
        "france-ligue-1": "f1",
        "i1": "i1",
        "serie-a": "i1",
        "seriea": "i1",
        "d1": "d1",
        "bundesliga": "d1",
        "mex": "mex",
        "liga-mx": "mex",
        "ligamx": "mex",
        "mexico-liga-mx": "mex",
        "bra": "bra",
        "serie-a-brazil": "bra",
        "serie-a-brasil": "bra",
        "brasileirao": "bra",
        "chi": "chi",
        "primera-division-chile": "chi",
        "russian-cup": "rus-cup",
        "copa-de-rusia": "rus-cup",
        "j1-league": "j1",
        "j-league": "j1",
        "usa": "usa",
        "mls": "usa",
        "major-league-soccer": "usa",
        "progol-media-semana": "progol-media-semana",
        "progol-1-2-semana": "progol-media-semana",
        "conference": "conference",
        "uefa-conference-league": "conference",
        "uefa-europa-conference-league": "conference",
        "europa-conference-league": "conference",
        "libertadores": "libertadores",
        "copa-libertadores": "libertadores",
        "conmebol-libertadores": "libertadores",
        "copa-conmebol-libertadores": "libertadores",
        "sudamericana": "sudamericana",
        "copa-sudamericana": "sudamericana",
        "conmebol-sudamericana": "sudamericana",
        "international-friendlies": "international-friendlies",
        "international-friendly": "international-friendlies",
        "amistosos-internacionales": "international-friendlies",
        "amistoso-internacional": "international-friendlies",
        "fifa-international-friendlies": "international-friendlies",
        "friendlies": "international-friendlies",
        "friendly": "international-friendlies",
        # UEFA Nations League: treated as international-friendlies for
        # policy purposes until a dedicated walk-forward benchmark is built.
        "uefa-nations-league": "international-friendlies",
        "nations-league": "international-friendlies",
        "uefa-nations-league-a": "international-friendlies",
        "uefa-nations-league-b": "international-friendlies",
        "uefa-nations-league-c": "international-friendlies",
        "mex-2": "mex-2",
        "liga-expansion-mx": "mex-2",
        # The competition name actually stored in the DB is "Liga de
        # Expansion MX". _competition_key naively lowers and dashes —
        # it doesn't strip stopwords — so the lookup key is
        # "liga-de-expansion-mx" with "de" intact. Map it explicitly.
        "liga-de-expansion-mx": "mex-2",
        "mexican-liga-de-expansion-mx": "mex-2",
        "mexican-liga-expansion-mx": "mex-2",
        "ascenso-mx": "mex-2",
        "sp2": "sp2",
        "spanish-la-liga-2": "sp2",
        "la-liga-2": "sp2",
        "laliga-2": "sp2",
        "laliga-hypermotion": "sp2",
        "hypermotion": "sp2",
        "swe": "swe",
        "swedish-allsvenskan": "swe",
        "allsvenskan": "swe",
    }
    COMPETITION_POLICY_OVERRIDES = {
        "progol-media-semana": {
            "competition_readiness": "context_only",
            "live_pick_allowed": False,
            "policy_reason": "Current slate context can be used, but no audited historical benchmark exists yet.",
            "blend_weights": {"elo": 0.3, "poisson": 0.2, "profile": 0.5},
            "draw_bias": 0.0,
        },
        "e0": {
            "competition_readiness": "ready",
            "live_pick_allowed": True,
            "policy_reason": "Historical benchmark passed for Premier League regime.",
            "blend_weights": {"elo": 0.36, "poisson": 0.20, "profile": 0.44},
            "draw_bias": -0.01,
        },
        "sp1": {
            "competition_readiness": "not_ready",
            "live_pick_allowed": False,
            "policy_reason": "Historical benchmark is below the live-pick threshold for LaLiga.",
            "blend_weights": {"elo": 0.34, "poisson": 0.21, "profile": 0.45},
            "draw_bias": 0.01,
        },
        "f1": {
            "competition_readiness": "covered",
            "live_pick_allowed": False,
            "policy_reason": "Historical coverage exists for Ligue 1; predictions are shown with caution.",
            "blend_weights": {"elo": 0.35, "poisson": 0.22, "profile": 0.43},
            "draw_bias": 0.01,
        },
        "mex": {
            "competition_readiness": "ready",
            "live_pick_allowed": True,
            "policy_reason": "Operator-forced ready policy: TheSportsDB Liga MX history is loaded; no audited walk-forward benchmark yet, treat picks with caution.",
            "blend_weights": {"elo": 0.34, "poisson": 0.23, "profile": 0.43},
            "draw_bias": 0.015,
        },
        "usa": {
            "competition_readiness": "covered",
            "live_pick_allowed": False,
            "policy_reason": "Historical coverage exists for MLS; predictions are shown with caution.",
            "blend_weights": {"elo": 0.33, "poisson": 0.24, "profile": 0.43},
            "draw_bias": -0.005,
        },
        "bra": {
            "competition_readiness": "ready",
            "live_pick_allowed": True,
            "policy_reason": "Operator-forced ready policy: TheSportsDB Brasileirao history is loaded; no audited walk-forward benchmark yet, treat picks with caution.",
            "blend_weights": {"elo": 0.32, "poisson": 0.23, "profile": 0.45},
            "draw_bias": 0.015,
        },
        "chi": {
            "competition_readiness": "ready",
            "live_pick_allowed": True,
            "policy_reason": "Operator-forced ready policy: TheSportsDB Chile Primera history is loaded; no audited walk-forward benchmark yet, treat picks with caution.",
            "blend_weights": {"elo": 0.32, "poisson": 0.23, "profile": 0.45},
            "draw_bias": 0.015,
        },
        "rus-cup": {
            "competition_readiness": "context_only",
            "live_pick_allowed": False,
            "policy_reason": "Russian Cup context is present, but cup ties need conservative handling without a benchmark.",
            "blend_weights": {"elo": 0.30, "poisson": 0.20, "profile": 0.50},
            "draw_bias": 0.0,
        },
        "j1": {
            "competition_readiness": "context_only",
            "live_pick_allowed": False,
            "policy_reason": "J1 League context is present, but no audited backtest is loaded for live picks.",
            "blend_weights": {"elo": 0.32, "poisson": 0.23, "profile": 0.45},
            "draw_bias": 0.015,
        },
        "i1": {
            "competition_readiness": "not_ready",
            "live_pick_allowed": False,
            "policy_reason": "Historical benchmark failed for Serie A; live picks are blocked.",
            "blend_weights": {"elo": 0.33, "poisson": 0.22, "profile": 0.45},
            "draw_bias": 0.02,
        },
        "d1": {
            "competition_readiness": "not_ready",
            "live_pick_allowed": False,
            "policy_reason": "Historical benchmark failed for Bundesliga; live picks are blocked.",
            "blend_weights": {"elo": 0.39, "poisson": 0.27, "profile": 0.34},
            "draw_bias": -0.01,
        },
        "conference": {
            "competition_readiness": "ready",
            "live_pick_allowed": True,
            "policy_reason": "Operator-forced ready policy: UEFA Conference League tracked via club domestic history; no dedicated walk-forward benchmark yet, treat picks with caution.",
            "blend_weights": {"elo": 0.34, "poisson": 0.22, "profile": 0.44},
            "draw_bias": 0.005,
        },
        "libertadores": {
            "competition_readiness": "ready",
            "live_pick_allowed": True,
            "policy_reason": "Operator-forced ready policy: football-data.org Libertadores history (2024-2026) loaded; no audited walk-forward benchmark yet, treat picks with caution.",
            "blend_weights": {"elo": 0.32, "poisson": 0.23, "profile": 0.45},
            "draw_bias": 0.015,
        },
        "sudamericana": {
            "competition_readiness": "ready",
            "live_pick_allowed": True,
            "policy_reason": "Operator-forced ready policy: Sudamericana lacks dedicated provider; predictions lean on club domestic history. No audited benchmark yet, treat picks with caution.",
            "blend_weights": {"elo": 0.30, "poisson": 0.22, "profile": 0.48},
            "draw_bias": 0.015,
        },
        "international-friendlies": {
            # Fase 2 (2nd pass) — Opción B: los amistosos internacionales
            # NUNCA se marcan "ready". La forma de selecciones nacionales
            # oscila demasiado (rosters cambian partido a partido) para
            # tratarlos como benchmark listo. Quedan en `context_only`: el
            # modelo los califica con contexto, pero no como fijo seguro.
            # La capa de seguridad (sanity_service) sigue dando headroom de
            # probabilidad solo cuando la evidencia del partido es ALTA.
            "competition_readiness": "context_only",
            "live_pick_allowed": False,
            "policy_reason": (
                "Amistoso internacional: TheSportsDB International Friendlies "
                "history (2024-2026) cargada, pero la forma de selecciones "
                "nacionales varía mucho. Se califica con contexto, no como "
                "fijo seguro; la capa de seguridad penaliza incertidumbre."
            ),
            # National-team friendlies are noisier than clubs: rosters change
            # match-by-match. Lean harder on per-team profile (recent form)
            # and less on ELO drift, with a slightly elevated draw bias since
            # cagey friendly draws are common.
            "blend_weights": {"elo": 0.28, "poisson": 0.22, "profile": 0.50},
            "draw_bias": 0.02,
        },
        "mex-2": {
            "competition_readiness": "ready",
            "live_pick_allowed": True,
            "policy_reason": (
                "Operator-forced ready policy: TheSportsDB Liga de Expansión MX "
                "history (2024-2026) loaded. Promotion-fight torneos create more "
                "draw outcomes than Liga MX; no audited walk-forward benchmark yet."
            ),
            "blend_weights": {"elo": 0.32, "poisson": 0.23, "profile": 0.45},
            "draw_bias": 0.02,
        },
        "sp2": {
            "competition_readiness": "ready",
            "live_pick_allowed": True,
            "policy_reason": (
                "Operator-forced ready policy: TheSportsDB LaLiga 2 / Hypermotion "
                "history (2024-2026) loaded. Lower-tier Spanish football skews to "
                "narrow scorelines; no audited walk-forward benchmark yet."
            ),
            "blend_weights": {"elo": 0.34, "poisson": 0.22, "profile": 0.44},
            "draw_bias": 0.025,
        },
        "swe": {
            "competition_readiness": "ready",
            "live_pick_allowed": True,
            "policy_reason": (
                "Operator-forced ready policy: TheSportsDB Allsvenskan history "
                "(2024-2026) loaded. Tight Nordic top-flight; no audited "
                "walk-forward benchmark yet."
            ),
            "blend_weights": {"elo": 0.33, "poisson": 0.23, "profile": 0.44},
            "draw_bias": 0.02,
        },
    }

    def __init__(
        self,
        training_repository: TrainingRepository,
        entity_repository: EntityRepository,
        result_repository: ResultRepository,
    ) -> None:
        self.training_repository = training_repository
        self.entity_repository = entity_repository
        self.result_repository = result_repository
        self.feature_service = FeatureService(FeatureRepository(training_repository.session), result_repository)

    def train(self, model_name: str | None = None) -> dict[str, Any]:
        selected_model_name = model_name or self.MODEL_NAME
        matches = self.entity_repository.list_matches()
        artifact = self._build_training_artifact(matches, selected_model_name)
        # Fase 1.2: fit per-league isotonic calibration curves from
        # out-of-fold walk-forward predictions and attach them to the
        # artifact so production scoring can apply them.
        if selected_model_name == self.MODEL_NAME:
            curves = self._fit_calibration_curves(matches)
            artifact["calibration_curves"] = curves
            for competition_key, by_class in curves.items():
                for class_label, breakpoints in by_class.items():
                    metrics_store.record_calibration_curve(
                        competition_key=competition_key,
                        class_label=class_label,
                        breakpoints=len(breakpoints),
                    )
            # Fase 2.4: persist a per-feature distribution baseline so future
            # incoming matches can be PSI-checked against the training data.
            artifact["drift_baseline"] = self._fit_drift_baseline(matches)
        with managed_transaction(self.training_repository.session):
            run = self.training_repository.save_run(
                selected_model_name,
                int(artifact["training_sample_size"]),
                artifact,
            )
            # Fase 2.1: heavy booster goes to disk; the DB row carries only
            # the storage descriptor (path + sha256), keeping training_runs
            # small and removing the deserializable blob from SQL.
            booster_json = artifact.pop("_booster_json_transient", None)
            if isinstance(booster_json, str):
                from app.services.artifact_storage import save_booster_json

                descriptor = save_booster_json(selected_model_name, run.id, booster_json)
                artifact["booster_storage"] = descriptor
                # Re-persist so the descriptor reaches the DB.
                self.training_repository.save_run(
                    selected_model_name,
                    int(artifact["training_sample_size"]),
                    artifact,
                )
        return artifact

    def _fit_drift_baseline(self, matches: list[MatchModel]) -> dict[str, Any]:
        """Compute a per-feature distribution baseline from training data.

        Builds the same feature vector used for ML training, then delegates
        bucket computation to the drift module. Stored under
        `artifact["drift_baseline"]` for later PSI checks.
        """
        from app.services.drift import train_drift_baseline

        feature_rows: list[dict[str, float]] = []
        for match in matches:
            results = self.result_repository.list_results_for_match(match.id)
            if not results:
                continue
            feature_map = self.feature_service.build_model_features(match, cutoff=match.kickoff_at)
            feature_rows.append({name: float(feature_map.get(name, 0.0)) for name in self.FEATURE_NAMES})
        return train_drift_baseline(feature_rows, list(self.FEATURE_NAMES))

    def publish_backtest_history(
        self,
        *,
        output_dir: Path,
        model_name: str | None = None,
        min_training_matches: int = 6,
    ) -> dict[str, Any]:
        """Write one auditable JSON per competition with the full
        walk-forward trail: prediction, actual outcome, hit, and Brier per
        match. Used by `make publish-backtest` and the `/backtest/history`
        endpoint so the moat is the public trail, not aggregates.
        """
        selected_model_name = model_name or self.MODEL_NAME
        all_matches = self.entity_repository.list_matches()
        result_lookup = {match.id: self._latest_result_for_match(match) for match in all_matches}
        matches_by_competition: dict[str, list[MatchModel]] = defaultdict(list)
        competition_names: dict[str, str] = {}
        for match in all_matches:
            if result_lookup[match.id] is None:
                continue
            competition_name = getattr(match.competition, "name", "Unknown League")
            key = self._competition_key(competition_name)
            competition_names.setdefault(key, competition_name)
            matches_by_competition[key].append(match)

        output_dir.mkdir(parents=True, exist_ok=True)
        index_entries: list[dict[str, Any]] = []
        for competition_key, league_matches in sorted(matches_by_competition.items()):
            entries, summary = self._backtest_entries_for_competition(
                selected_model_name=selected_model_name,
                ordered_matches=sorted(
                    league_matches,
                    key=lambda m: self._match_sort_key_from_result(result_lookup[m.id], m.id),
                ),
                result_lookup=result_lookup,
                min_training_matches=min_training_matches,
            )
            payload = {
                "model_name": selected_model_name,
                "competition_key": competition_key,
                "competition_name": competition_names[competition_key],
                "generated_at": datetime.now(timezone.utc).isoformat(),
                "summary": summary,
                "entries": entries,
            }
            file_path = output_dir / f"{competition_key}.json"
            with file_path.open("w", encoding="utf-8") as handle:
                json.dump(payload, handle, ensure_ascii=False, indent=2, default=str)
            index_entries.append(
                {
                    "competition_key": competition_key,
                    "competition_name": competition_names[competition_key],
                    "file": file_path.name,
                    **summary,
                }
            )

        index_payload = {
            "model_name": selected_model_name,
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "competitions": index_entries,
        }
        index_path = output_dir / "index.json"
        with index_path.open("w", encoding="utf-8") as handle:
            json.dump(index_payload, handle, ensure_ascii=False, indent=2, default=str)
        return index_payload

    # If XGBoost beats heuristic by at least this much in Brier (lower
    # is better) across the walk-forward, we declare it "ready" for
    # this competition. The 0.01 threshold filters out noise — a small
    # win on a single backtest run can flip across re-runs.
    XGBOOST_BACKTEST_BRIER_MARGIN = 0.01
    # Maximum number of XGBoost trainings per competition during the
    # walk-forward. Each training has to rebuild features for the
    # entire prior window, so an unbounded loop costs N^2 feature
    # builds and runs for hours on the bigger leagues. Sampling at ~50
    # points still gives a statistically meaningful comparison
    # against heuristic without making `make publish-backtest`
    # prohibitive. The heuristic loop stays unbounded (it's cheap).
    XGBOOST_BACKTEST_MAX_TRAININGS = 50

    def _backtest_entries_for_competition(
        self,
        *,
        selected_model_name: str,
        ordered_matches: list[MatchModel],
        result_lookup: dict[str, Any],
        min_training_matches: int,
    ) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        """Walk forward through a competition's match history, scoring
        each match with BOTH the heuristic and (when enough prior
        data is available) a freshly-trained XGBoost booster.

        The result is per-match: heuristic at the top level (kept for
        backward compatibility with existing /backtest/history
        consumers), plus an ``xgboost`` sub-object when the booster
        was trained. The summary aggregates both and surfaces
        ``xgboost_beats_heuristic`` so operators can see, per
        competition, whether the more expensive model is earning its
        keep before promoting it.
        """
        entries: list[dict[str, Any]] = []
        h_evaluated = 0
        h_hits = 0
        h_brier_total = 0.0
        h_log_loss_total = 0.0
        xgb_evaluated = 0
        xgb_hits = 0
        xgb_brier_total = 0.0
        xgb_log_loss_total = 0.0

        # Precompute which match indices are XGBoost sample points so
        # the per-match check inside the loop stays O(1). We pick
        # evenly-spaced points from the subset that's actually
        # evaluable (passes the prior-window thresholds).
        xgb_eligible_indices = [
            i for i, m in enumerate(ordered_matches)
            if result_lookup[m.id] is not None
            and i >= min_training_matches
            and i >= self.XGBOOST_MIN_SAMPLE_SIZE
        ]
        xgb_sample_indices: set[int] = set()
        if xgb_eligible_indices:
            total_eligible = len(xgb_eligible_indices)
            step = max(1, total_eligible // self.XGBOOST_BACKTEST_MAX_TRAININGS)
            for offset, idx in enumerate(xgb_eligible_indices):
                if offset % step == 0:
                    xgb_sample_indices.add(idx)
        # Cache feature maps so the unbounded heuristic loop AND the
        # sampled XGBoost trainings share a single computed view per
        # match — without this, a 900-match league would recompute
        # ~400k feature maps across the walk-forward.
        feature_map_cache: dict[str, dict[str, float]] = {}

        def _feature_map_for(match: MatchModel) -> dict[str, float]:
            if match.id not in feature_map_cache:
                feature_map_cache[match.id] = self.feature_service.build_model_features(
                    match, cutoff=match.kickoff_at
                )
            return feature_map_cache[match.id]

        for index, match in enumerate(ordered_matches):
            prior_matches = ordered_matches[:index]
            actual_result = result_lookup[match.id]
            if actual_result is None or len(prior_matches) < min_training_matches:
                continue

            actual_label = actual_result.result_code
            actual_index = self.LABEL_TO_INDEX[actual_label]

            # Heuristic always — keeps the cheap baseline trail.
            heuristic_artifact = self._build_heuristic_artifact(
                prior_matches, result_lookup=result_lookup
            )
            heuristic_artifact["model_name"] = selected_model_name
            h_scored = self._score_match_with_artifact(match, heuristic_artifact)
            h_metrics = self._evaluate_walk_forward_scored(h_scored, actual_label, actual_index)
            h_evaluated += 1
            if h_metrics["hit"]:
                h_hits += 1
            h_brier_total += h_metrics["brier"]
            h_log_loss_total += h_metrics["log_loss"]

            # XGBoost only at the precomputed sample indices. Each
            # training rebuilds the dataset from prior_matches; the
            # feature_map_cache above keeps the per-match compute
            # bounded so a 900-match league finishes in seconds
            # instead of hours.
            xgb_entry: dict[str, Any] | None = None
            if index in xgb_sample_indices:
                xgb_artifact = self._train_xgboost_artifact_for_backtest(
                    prior_matches,
                    feature_map_for=_feature_map_for,
                )
                if xgb_artifact is not None:
                    xgb_scored = self._score_match_with_artifact(match, xgb_artifact)
                    xgb_metrics = self._evaluate_walk_forward_scored(
                        xgb_scored, actual_label, actual_index
                    )
                    xgb_entry = {
                        "predicted_result": xgb_metrics["predicted_label"],
                        "probabilities": xgb_metrics["probabilities_dict"],
                        "hit": xgb_metrics["hit"],
                        "brier": round(xgb_metrics["brier"], 4),
                        "log_loss": round(xgb_metrics["log_loss"], 4),
                    }
                    xgb_evaluated += 1
                    if xgb_metrics["hit"]:
                        xgb_hits += 1
                    xgb_brier_total += xgb_metrics["brier"]
                    xgb_log_loss_total += xgb_metrics["log_loss"]

            entries.append(
                {
                    "match_id": match.id,
                    "played_at": actual_result.played_at.isoformat(),
                    "home_team": match.home_team.name,
                    "away_team": match.away_team.name,
                    "actual_result": actual_label,
                    "score": f"{actual_result.home_goals}-{actual_result.away_goals}",
                    # Top-level fields stay as heuristic — back-compat.
                    "predicted_result": h_metrics["predicted_label"],
                    "predicted_top_probability": round(
                        h_metrics["top_probability"], 4
                    ),
                    "probabilities": h_metrics["probabilities_dict"],
                    "hit": h_metrics["hit"],
                    "brier": round(h_metrics["brier"], 4),
                    "log_loss": round(h_metrics["log_loss"], 4),
                    "prior_matches": len(prior_matches),
                    "xgboost": xgb_entry,
                }
            )

        heuristic_summary = {
            "matches_evaluated": h_evaluated,
            "hit_rate": round(h_hits / h_evaluated, 4) if h_evaluated else 0.0,
            "brier_score": round(h_brier_total / h_evaluated, 4) if h_evaluated else 0.0,
            "log_loss": round(h_log_loss_total / h_evaluated, 4) if h_evaluated else 0.0,
        }
        xgboost_summary: dict[str, Any] | None = None
        brier_delta: float | None = None
        xgboost_beats_heuristic = False
        if xgb_evaluated:
            xgboost_summary = {
                "matches_evaluated": xgb_evaluated,
                "hit_rate": round(xgb_hits / xgb_evaluated, 4),
                "brier_score": round(xgb_brier_total / xgb_evaluated, 4),
                "log_loss": round(xgb_log_loss_total / xgb_evaluated, 4),
            }
            if h_evaluated:
                brier_delta = round(
                    heuristic_summary["brier_score"]
                    - xgboost_summary["brier_score"],
                    4,
                )
                xgboost_beats_heuristic = (
                    brier_delta >= self.XGBOOST_BACKTEST_BRIER_MARGIN
                )

        summary = {
            # Top-level numbers stay as heuristic for back-compat.
            **heuristic_summary,
            "heuristic": heuristic_summary,
            "xgboost": xgboost_summary,
            "brier_delta": brier_delta,
            "xgboost_beats_heuristic": xgboost_beats_heuristic,
            "xgboost_brier_margin": self.XGBOOST_BACKTEST_BRIER_MARGIN,
        }
        return entries, summary

    def _evaluate_walk_forward_scored(
        self,
        scored: dict[str, float],
        actual_label: str,
        actual_index: int,
    ) -> dict[str, Any]:
        """Reduce one (home, draw, away) score dict to the per-match
        backtest fields. Shared by the heuristic and XGBoost paths so
        the metric definitions can't drift between the two."""
        probabilities = [
            float(scored.get("home", 0.0)),
            float(scored.get("draw", 0.0)),
            float(scored.get("away", 0.0)),
        ]
        top_outcome, top_probability = max(scored.items(), key=lambda item: item[1])
        predicted_label = {"home": "1", "draw": "X", "away": "2"}[top_outcome]
        return {
            "probabilities": probabilities,
            "probabilities_dict": {
                "1": round(probabilities[0], 4),
                "X": round(probabilities[1], 4),
                "2": round(probabilities[2], 4),
            },
            "top_probability": float(top_probability),
            "predicted_label": predicted_label,
            "hit": predicted_label == actual_label,
            "brier": self._brier_score(probabilities, actual_index),
            "log_loss": self._log_loss(probabilities, actual_index),
        }

    def _train_xgboost_artifact_for_backtest(
        self,
        prior_matches: list[MatchModel],
        *,
        feature_map_for=None,
    ) -> dict[str, Any] | None:
        """Train an XGBoost booster on the walk-forward window and
        return an artifact ready for ``_score_match_with_artifact``.

        Differs from the production training path in two ways:
        * The booster JSON is parked under ``booster_json`` (inline)
          instead of the on-disk descriptor — the artifact is
          throwaway, no need to involve ``artifact_storage``.
        * No calibration curves are fit (would require a hold-out
          slice inside the prior window we don't have here). The
          scorer falls back to the class-priors blend in
          ``_finalize_scores``, which is the same fallback prod uses
          when curves are missing for a league.

        ``feature_map_for`` lets the caller pass a per-match cached
        view, avoiding the N^2 feature-build cost that would otherwise
        dominate a 900-match league.

        Returns ``None`` when the dataset is too small for XGBoost,
        matching the production gate behaviour.
        """
        dataset = self._build_training_dataset_from_cache(
            prior_matches, feature_map_for=feature_map_for
        )
        artifact = self._train_xgboost_artifact(dataset)
        if artifact is None:
            return None
        booster_json = artifact.pop("_booster_json_transient", None)
        if isinstance(booster_json, str):
            artifact["booster_json"] = booster_json
        artifact["model_name"] = self.MODEL_NAME
        return artifact

    def _build_training_dataset_from_cache(
        self,
        matches: list[MatchModel],
        *,
        feature_map_for=None,
    ) -> dict[str, Any]:
        """Same shape as ``_build_training_dataset`` but pulls feature
        maps through the caller-supplied cache. Falls back to the
        feature service when the cache misses, mirroring the
        non-backtest path."""
        rows: list[list[float]] = []
        labels: list[int] = []
        played_at: list[Any] = []
        classes_seen: set[int] = set()
        for match in matches:
            results = self.result_repository.list_results_for_match(match.id)
            if not results:
                continue
            target = self.LABEL_TO_INDEX.get(results[0].result_code)
            if target is None:
                continue
            if feature_map_for is not None:
                feature_map = feature_map_for(match)
            else:
                feature_map = self.feature_service.build_model_features(
                    match, cutoff=match.kickoff_at
                )
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

    def drift_report(
        self,
        *,
        model_name: str | None = None,
        sample_size: int = 200,
    ) -> dict[str, Any]:
        """Compare the most recent `sample_size` resulted matches against
        the training baseline and report a PSI per feature.

        Updates Prometheus gauges along the way so a scrape immediately
        reflects the latest drift snapshot.
        """
        from app.services.drift import compute_psi

        selected = model_name or self.MODEL_NAME
        artifact = self.latest_artifact(selected)
        if artifact is None or not isinstance(artifact.get("drift_baseline"), dict):
            return {
                "model_name": selected,
                "ready": False,
                "reason": "no_drift_baseline",
                "features": [],
            }
        baseline = artifact["drift_baseline"]
        matches = self.entity_repository.list_matches()
        result_lookup = {match.id: self._latest_result_for_match(match) for match in matches}
        resulted = [match for match in matches if result_lookup[match.id] is not None]
        resulted.sort(
            key=lambda match: self._match_sort_key_from_result(result_lookup[match.id], match.id),
            reverse=True,
        )
        sample_matches = resulted[: max(sample_size, 1)]
        if not sample_matches:
            return {
                "model_name": selected,
                "ready": False,
                "reason": "no_resulted_matches",
                "features": [],
            }

        sample_by_feature: dict[str, list[float]] = {name: [] for name in self.FEATURE_NAMES}
        for match in sample_matches:
            feature_map = self.feature_service.build_model_features(match, cutoff=match.kickoff_at)
            for name in self.FEATURE_NAMES:
                sample_by_feature[name].append(float(feature_map.get(name, 0.0)))

        report: list[dict[str, Any]] = []
        for feature_name, sample_values in sample_by_feature.items():
            feature_baseline = baseline.get(feature_name)
            if not isinstance(feature_baseline, dict):
                continue
            psi = compute_psi(sample_values, feature_baseline)
            report.append(
                {
                    "feature": feature_name,
                    "psi": round(psi, 4),
                    "severity": _drift_severity(psi),
                    "sample_size": len(sample_values),
                }
            )
        report.sort(key=lambda item: item["psi"], reverse=True)

        return {
            "model_name": selected,
            "ready": True,
            "sample_size": len(sample_matches),
            "features": report,
            "thresholds": {"moderate": 0.10, "significant": 0.25},
        }

    def _fit_calibration_curves(
        self, matches: list[MatchModel], *, min_training_matches: int = 6
    ) -> dict[str, dict[str, list[list[float]]]]:
        """Generate isotonic calibration curves keyed by competition_key.

        Strategy: walk-forward by league (Fase 1.3 invariant) to get OOF
        (raw_probability, actual_outcome) pairs for each class; fit PAV per
        (league, class). Curves are stored as lists of [x, y] so they
        round-trip cleanly through the artifact JSON.
        """
        from app.services.calibration import fit_pav  # local import: keeps the mixin file independent.

        result_lookup = {match.id: self._latest_result_for_match(match) for match in matches}
        oof_by_league_class: dict[str, dict[str, list[tuple[float, float]]]] = defaultdict(
            lambda: {"1": [], "X": [], "2": []}
        )

        matches_by_competition: dict[str, list[MatchModel]] = defaultdict(list)
        for match in matches:
            if result_lookup[match.id] is None:
                continue
            competition_name = getattr(match.competition, "name", "Unknown League")
            matches_by_competition[self._competition_key(competition_name)].append(match)

        for competition_key, league_matches in matches_by_competition.items():
            ordered = sorted(
                league_matches,
                key=lambda m: self._match_sort_key_from_result(result_lookup[m.id], m.id),
            )
            for index, match in enumerate(ordered):
                prior_matches = ordered[:index]
                if len(prior_matches) < min_training_matches:
                    continue
                artifact = self._build_heuristic_artifact(prior_matches, result_lookup=result_lookup)
                artifact["model_name"] = self.MODEL_NAME
                # Strip any curve coming from a recursive call to avoid
                # double-calibrating the OOF predictions.
                artifact.pop("calibration_curves", None)
                scored = self._score_match_with_artifact(match, artifact)
                actual_code = result_lookup[match.id].result_code
                oof_by_league_class[competition_key]["1"].append(
                    (float(scored["home"]), 1.0 if actual_code == "1" else 0.0)
                )
                oof_by_league_class[competition_key]["X"].append(
                    (float(scored["draw"]), 1.0 if actual_code == "X" else 0.0)
                )
                oof_by_league_class[competition_key]["2"].append(
                    (float(scored["away"]), 1.0 if actual_code == "2" else 0.0)
                )

        curves: dict[str, dict[str, list[list[float]]]] = {}
        for competition_key, by_class in oof_by_league_class.items():
            league_curves: dict[str, list[list[float]]] = {}
            for class_label, samples in by_class.items():
                if len(samples) < min_training_matches:
                    continue
                breakpoints = fit_pav(samples)
                if breakpoints:
                    league_curves[class_label] = [[x, y] for x, y in breakpoints]
            if league_curves:
                curves[competition_key] = league_curves
        return curves

    def evaluate_walk_forward(
        self,
        *,
        model_name: str | None = None,
        min_training_matches: int = 6,
        confidence_threshold: float = 0.5,
    ) -> dict[str, Any]:
        """Walk-forward evaluation aggregated across leagues.

        Each match is scored using an artifact trained only on prior matches
        from the **same competition** (Fase 1.3). This avoids cross-league
        contamination — distributions of Premier League, Liga MX, J1 League
        are not interchangeable. The aggregated metrics in the response are
        a sample-weighted average across leagues; the per-league breakdown
        comes from `evaluate_competitions_walk_forward`.
        """
        selected_model_name = model_name or self.MODEL_NAME
        all_matches = self.entity_repository.list_matches()
        result_lookup = {match.id: self._latest_result_for_match(match) for match in all_matches}
        matches_by_competition: dict[str, list[MatchModel]] = defaultdict(list)
        for match in all_matches:
            if result_lookup[match.id] is None:
                continue
            competition_name = getattr(match.competition, "name", "Unknown League")
            matches_by_competition[self._competition_key(competition_name)].append(match)

        if not matches_by_competition:
            return self._empty_evaluation(selected_model_name, min_training_matches, confidence_threshold, 0)

        evaluated = 0
        hits = 0
        brier_total = 0.0
        log_loss_total = 0.0
        confident_picks = 0
        confident_hits = 0
        matches_considered = 0

        for matches in matches_by_competition.values():
            ordered_matches = sorted(
                matches,
                key=lambda match: self._match_sort_key_from_result(result_lookup[match.id], match.id),
            )
            matches_considered += len(ordered_matches)
            metrics = self._collect_walk_forward_metrics(
                selected_model_name=selected_model_name,
                ordered_matches=ordered_matches,
                result_lookup=result_lookup,
                min_training_matches=min_training_matches,
                confidence_threshold=confidence_threshold,
            )
            evaluated += metrics["evaluated"]
            hits += metrics["hits"]
            brier_total += metrics["brier_total"]
            log_loss_total += metrics["log_loss_total"]
            confident_picks += metrics["confident_picks"]
            confident_hits += metrics["confident_hits"]

        if evaluated == 0:
            return self._empty_evaluation(
                selected_model_name,
                min_training_matches,
                confidence_threshold,
                matches_considered,
            )

        return self._summarize_walk_forward(
            selected_model_name=selected_model_name,
            matches_considered=matches_considered,
            evaluated=evaluated,
            hits=hits,
            brier_total=brier_total,
            log_loss_total=log_loss_total,
            confident_picks=confident_picks,
            confident_hits=confident_hits,
            min_training_matches=min_training_matches,
            confidence_threshold=confidence_threshold,
        )

    def evaluate_competitions_walk_forward(
        self,
        *,
        model_name: str | None = None,
        min_training_matches: int = 6,
        confidence_threshold: float = 0.5,
    ) -> dict[str, Any]:
        selected_model_name = model_name or self.MODEL_NAME
        all_matches = self.entity_repository.list_matches()
        result_lookup = {match.id: self._latest_result_for_match(match) for match in all_matches}
        matches_by_competition: dict[str, list[MatchModel]] = defaultdict(list)
        competition_names: dict[str, str] = {}
        for match in all_matches:
            if result_lookup[match.id] is None:
                continue
            competition_name = getattr(match.competition, "name", "Unknown League")
            competition_key = self._competition_key(competition_name)
            competition_names.setdefault(competition_key, competition_name)
            matches_by_competition[competition_key].append(match)

        evaluations: list[dict[str, Any]] = []
        for competition_key, matches in sorted(matches_by_competition.items()):
            ordered_matches = sorted(matches, key=lambda match: self._match_sort_key_from_result(result_lookup[match.id], match.id))
            evaluation = self._evaluate_ordered_matches(
                selected_model_name=selected_model_name,
                ordered_matches=ordered_matches,
                result_lookup=result_lookup,
                min_training_matches=min_training_matches,
                confidence_threshold=confidence_threshold,
            )
            evaluation["competition_key"] = competition_key
            evaluation["competition_name"] = competition_names[competition_key]
            evaluations.append(evaluation)
            # Surface latest per-league quality numbers to Prometheus.
            if int(evaluation.get("matches_evaluated") or 0) > 0:
                metrics_store.record_model_evaluation(
                    competition_key=competition_key,
                    brier_score=float(evaluation["brier_score"]),
                    log_loss=float(evaluation["log_loss"]),
                    hit_rate=float(evaluation["hit_rate"]),
                    matches_evaluated=int(evaluation["matches_evaluated"]),
                )

        return {
            "model_name": selected_model_name,
            "evaluation_mode": "walk_forward_by_competition",
            "min_training_matches": min_training_matches,
            "confidence_threshold": confidence_threshold,
            "competitions_considered": len(evaluations),
            "competitions_ready": sum(1 for item in evaluations if item["ready_for_live_picks"]),
            "competitions": evaluations,
        }

    def calibration_report(
        self,
        *,
        model_name: str | None = None,
        min_training_matches: int = 6,
        confidence_threshold: float = 0.5,
    ) -> dict[str, Any]:
        selected_model_name = model_name or self.MODEL_NAME
        all_matches = self.entity_repository.list_matches()
        result_lookup = {match.id: self._latest_result_for_match(match) for match in all_matches}
        resulted_matches = [match for match in all_matches if result_lookup[match.id] is not None]
        ordered_matches = sorted(
            resulted_matches,
            key=lambda item: self._match_sort_key_from_result(result_lookup[item.id], item.id),
        )
        bins: dict[str, dict[str, float]] = {}
        evaluated = 0
        accepted = 0

        for index, match in enumerate(ordered_matches):
            prior_matches = ordered_matches[:index]
            if len(prior_matches) < min_training_matches:
                continue
            artifact = (
                self._build_heuristic_artifact(prior_matches, result_lookup=result_lookup)
                if selected_model_name == self.MODEL_NAME
                else self._build_training_artifact(prior_matches, selected_model_name)
            )
            scored = self._score_match_with_artifact(match, artifact)
            actual_result = result_lookup[match.id]
            if actual_result is None:
                continue
            predicted_key, top_probability = max(scored.items(), key=lambda item: item[1])
            predicted_label = {"home": "1", "draw": "X", "away": "2"}[predicted_key]
            top_probability = float(top_probability)
            lower = min(int(top_probability * 10) / 10, 0.9)
            upper = lower + 0.1
            label = f"{lower:.1f}-{upper:.1f}"
            bucket = bins.setdefault(
                label,
                {"matches": 0.0, "hits": 0.0, "confidence_total": 0.0, "brier_total": 0.0},
            )
            probabilities = [float(scored["home"]), float(scored["draw"]), float(scored["away"])]
            actual_index = self.LABEL_TO_INDEX[actual_result.result_code]
            hit = predicted_label == actual_result.result_code
            bucket["matches"] += 1.0
            bucket["hits"] += 1.0 if hit else 0.0
            bucket["confidence_total"] += top_probability
            bucket["brier_total"] += self._brier_score(probabilities, actual_index)
            evaluated += 1
            if top_probability >= confidence_threshold:
                accepted += 1

        calibration_bins = []
        for label, bucket in sorted(bins.items()):
            matches = int(bucket["matches"])
            if matches <= 0:
                continue
            average_confidence = bucket["confidence_total"] / matches
            hit_rate = bucket["hits"] / matches
            calibration_bins.append(
                {
                    "confidence_bin": label,
                    "matches": matches,
                    "average_confidence": round(average_confidence, 4),
                    "hit_rate": round(hit_rate, 4),
                    "calibration_gap": round(average_confidence - hit_rate, 4),
                    "brier_score": round(bucket["brier_total"] / matches, 4),
                }
            )

        return {
            "model_name": selected_model_name,
            "evaluation_mode": "walk_forward_calibration",
            "matches_considered": len(ordered_matches),
            "matches_evaluated": evaluated,
            "min_training_matches": min_training_matches,
            "confidence_threshold": confidence_threshold,
            "accepted_picks": accepted,
            "accepted_pick_rate": round(accepted / evaluated, 4) if evaluated else 0.0,
            "bins": calibration_bins,
        }

    def _evaluate_ordered_matches(
        self,
        *,
        selected_model_name: str,
        ordered_matches: list[MatchModel],
        result_lookup: dict[str, Any],
        min_training_matches: int,
        confidence_threshold: float,
    ) -> dict[str, Any]:
        if not ordered_matches:
            return self._empty_evaluation(selected_model_name, min_training_matches, confidence_threshold, 0)

        metrics = self._collect_walk_forward_metrics(
            selected_model_name=selected_model_name,
            ordered_matches=ordered_matches,
            result_lookup=result_lookup,
            min_training_matches=min_training_matches,
            confidence_threshold=confidence_threshold,
        )
        if metrics["evaluated"] == 0:
            return self._empty_evaluation(
                selected_model_name,
                min_training_matches,
                confidence_threshold,
                len(ordered_matches),
            )

        return self._summarize_walk_forward(
            selected_model_name=selected_model_name,
            matches_considered=len(ordered_matches),
            evaluated=metrics["evaluated"],
            hits=metrics["hits"],
            brier_total=metrics["brier_total"],
            log_loss_total=metrics["log_loss_total"],
            confident_picks=metrics["confident_picks"],
            confident_hits=metrics["confident_hits"],
            min_training_matches=min_training_matches,
            confidence_threshold=confidence_threshold,
        )

    def _collect_walk_forward_metrics(
        self,
        *,
        selected_model_name: str,
        ordered_matches: list[MatchModel],
        result_lookup: dict[str, Any],
        min_training_matches: int,
        confidence_threshold: float,
    ) -> dict[str, Any]:
        """Run the walk-forward loop once over already-time-ordered matches.

        Returns the raw counters; aggregation is deferred to
        `_summarize_walk_forward` so multiple league partitions can be
        combined."""
        evaluated = 0
        hits = 0
        brier_total = 0.0
        log_loss_total = 0.0
        confident_picks = 0
        confident_hits = 0

        for index, match in enumerate(ordered_matches):
            prior_matches = ordered_matches[:index]
            if len(prior_matches) < min_training_matches:
                continue
            if selected_model_name == self.MODEL_NAME:
                artifact = self._build_heuristic_artifact(prior_matches, result_lookup=result_lookup)
                artifact["model_name"] = selected_model_name
            else:
                artifact = self._build_training_artifact(prior_matches, selected_model_name)
            scored = self._score_match_with_artifact(match, artifact)
            actual_result = result_lookup[match.id]
            if actual_result is None:
                continue
            actual_label = actual_result.result_code
            predicted_label = max(scored.items(), key=lambda item: item[1])[0]
            predicted_label = {"home": "1", "draw": "X", "away": "2"}[predicted_label]
            top_probability = max(scored.values())

            evaluated += 1
            if predicted_label == actual_label:
                hits += 1
            if top_probability >= confidence_threshold:
                confident_picks += 1
                if predicted_label == actual_label:
                    confident_hits += 1

            probabilities = [float(scored["home"]), float(scored["draw"]), float(scored["away"])]
            actual_index = self.LABEL_TO_INDEX[actual_label]
            brier_total += self._brier_score(probabilities, actual_index)
            log_loss_total += self._log_loss(probabilities, actual_index)

        return {
            "evaluated": evaluated,
            "hits": hits,
            "brier_total": brier_total,
            "log_loss_total": log_loss_total,
            "confident_picks": confident_picks,
            "confident_hits": confident_hits,
        }

    def _walk_forward_thresholds(self) -> WalkForwardThresholds:
        return {
            "hit_rate": self.READY_HIT_RATE_THRESHOLD,
            "brier_score_max": self.READY_BRIER_THRESHOLD,
            "confident_hit_rate": self.READY_CONFIDENT_HIT_RATE_THRESHOLD,
            "min_confident_picks": self.READY_MIN_CONFIDENT_PICKS,
        }

    def _summarize_walk_forward(
        self,
        *,
        selected_model_name: str,
        matches_considered: int,
        evaluated: int,
        hits: int,
        brier_total: float,
        log_loss_total: float,
        confident_picks: int,
        confident_hits: int,
        min_training_matches: int,
        confidence_threshold: float,
    ) -> dict[str, Any]:
        return _summarize_walk_forward_pure(
            selected_model_name=selected_model_name,
            matches_considered=matches_considered,
            evaluated=evaluated,
            hits=hits,
            brier_total=brier_total,
            log_loss_total=log_loss_total,
            confident_picks=confident_picks,
            confident_hits=confident_hits,
            min_training_matches=min_training_matches,
            confidence_threshold=confidence_threshold,
            thresholds=self._walk_forward_thresholds(),
        )
