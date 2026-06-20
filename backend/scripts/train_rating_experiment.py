"""Offline EXPERIMENTAL rating-feature backtest (R4). NOT PRODUCTIVE.

Question: does the internal team rating (``rating_diff`` & friends) improve a
3-class (1/X/2) model over a recent-form baseline, per competition?

This script is fully OFFLINE and side-effect free against productive tables:
  * it READS canonical results (rollback at the end, no writes);
  * it builds a LEAK-FREE dataset: for every match, baseline form features and
    rating features are computed from matches BEFORE that match (walk-forward),
    so the test fold never sees the future;
  * the rating walk-forward uses the SAME elo_v1 config and the SAME input
    mapping as the active run (``compute_team_ratings.build_input_matches``),
    so the final replayed ratings reconcile with the persisted snapshots;
  * it trains EXPERIMENTAL XGBoost models (the only sanctioned ML lib) and
    writes artifacts ONLY under ``artifacts/experiments/team_rating_r4/`` —
    never registered as an active model, never written to the DB.

Usage::

    python backend/scripts/train_rating_experiment.py --all
    python backend/scripts/train_rating_experiment.py --competition "Copa Libertadores"

NOTHING here activates production, changes FeatureService/PredictionService,
touches the approval gate, or flips the feature flag.
"""

from __future__ import annotations

import argparse
import json
import math
from collections import defaultdict
from collections import deque
from dataclasses import dataclass
from dataclasses import field
from pathlib import Path
from typing import Any

from app.db.session import SessionLocal
from app.domain.team_rating import ConfidenceBucket
from app.domain.team_rating import TeamRatingConfig
from app.domain.team_rating import confidence_bucket
from app.domain.team_rating import default_config
from scripts.compute_team_ratings import build_input_matches

# Feature sets compared in the ablation.
BASELINE_FEATURES = [
    "home_form_points_pm", "away_form_points_pm",
    "home_form_gb_pm", "away_form_gb_pm",
    "form_points_gap", "form_gb_gap",
    "h2h_matches", "h2h_points_gap", "h2h_gb_gap",
    "rest_gap_days",
]
RATING_FEATURES = [
    "home_rating", "away_rating", "rating_diff",
    "home_rating_confidence", "away_rating_confidence",
    "both_rating_medium_plus", "rating_match_count_diff",
]
FEATURE_SETS = {
    "without_rating": BASELINE_FEATURES,
    "with_rating": BASELINE_FEATURES + RATING_FEATURES,
    "rating_only": RATING_FEATURES,
}

LABEL_TO_INDEX = {"1": 0, "X": 1, "2": 2}
_FORM_WINDOW = 8
_CONF_ORDINAL = {
    ConfidenceBucket.NO_RATING: 0, ConfidenceBucket.WEAK: 1,
    ConfidenceBucket.MEDIUM: 2, ConfidenceBucket.STRONG: 3,
}
_EXPERIMENT_DIR = Path("artifacts/experiments/team_rating_r4")


@dataclass
class ExperimentRow:
    match_id: str
    played_at: Any
    competition: str
    namespace: str
    target: str               # '1' / 'X' / '2'
    features: dict[str, float]
    home_rating_present: bool
    away_rating_present: bool
    both_medium_plus: bool


# --- leak-free dataset build (read-only) ------------------------------------


@dataclass
class _TeamState:
    elo: float
    matches: int = 0
    recent: deque = field(default_factory=lambda: deque(maxlen=_FORM_WINDOW))  # (points, gb)
    last_played: Any = None


def _expected(rh: float, ra: float, home_adv: float) -> float:
    return 1.0 / (1.0 + 10 ** ((ra - rh - home_adv) / 400.0))


def build_experiment_rows(session, config: TeamRatingConfig | None = None) -> list[ExperimentRow]:
    """Walk matches in (played_at, match_id) order; emit one leak-free row per
    eligible match (pre-match features), then update state. Read-only."""
    cfg = config or default_config()
    matches, _teams, _prefilter, _considered = build_input_matches(session)
    # Same ordering the calculator uses; conflicts are skipped (excluded).
    matches = [m for m in matches if not m.is_conflict]
    matches.sort(key=lambda m: (m.played_at, m.match_id))

    states: dict[tuple[str, str], _TeamState] = {}
    h2h: dict[tuple[str, str, str], list[tuple[int, int]]] = defaultdict(list)  # ns, a, b -> (gb_for_a, pts_a)
    rows: list[ExperimentRow] = []

    def _state(team_id: str, ns: str) -> _TeamState:
        key = (team_id, ns)
        if key not in states:
            states[key] = _TeamState(elo=cfg.initial_rating)
        return states[key]

    for m in matches:
        if m.home_score is None or m.away_score is None:
            continue
        ns = m.namespace
        hs, as_ = int(m.home_score), int(m.away_score)
        target = "1" if hs > as_ else ("X" if hs == as_ else "2")

        hstate = _state(m.home_team_id, ns)
        astate = _state(m.away_team_id, ns)

        # --- pre-match features (no future info) ---
        def _form(s: _TeamState) -> tuple[float, float]:
            if not s.recent:
                return 0.0, 0.0
            pts = sum(p for p, _ in s.recent) / len(s.recent)
            gb = sum(g for _, g in s.recent) / len(s.recent)
            return pts, gb

        h_pts, h_gb = _form(hstate)
        a_pts, a_gb = _form(astate)
        pair = tuple(sorted((m.home_team_id, m.away_team_id)))
        hk = (ns, pair[0], pair[1])
        prior = h2h[hk]
        # h2h gaps from home team's perspective.
        h2h_n = len(prior)
        if h2h_n:
            # stored as (gb, pts) for pair[0]; flip if home is pair[1]
            gb0 = sum(g for g, _ in prior) / h2h_n
            pts0 = sum(p for _, p in prior) / h2h_n
            if m.home_team_id == pair[0]:
                h2h_pts_gap, h2h_gb_gap = (2 * pts0 - 3.0), gb0
            else:
                h2h_pts_gap, h2h_gb_gap = (3.0 - 2 * pts0), -gb0
        else:
            h2h_pts_gap = h2h_gb_gap = 0.0

        rest_gap = 0.0
        if hstate.last_played is not None and astate.last_played is not None:
            rest_gap = (astate.last_played - hstate.last_played).total_seconds() / 86400.0

        h_conf = _CONF_ORDINAL[confidence_bucket(hstate.matches)]
        a_conf = _CONF_ORDINAL[confidence_bucket(astate.matches)]
        both_mp = hstate.matches >= 4 and astate.matches >= 4
        present_h, present_a = hstate.matches > 0, astate.matches > 0
        rating_diff = (hstate.elo - astate.elo) if (present_h and present_a) else 0.0

        features = {
            "home_form_points_pm": h_pts, "away_form_points_pm": a_pts,
            "home_form_gb_pm": h_gb, "away_form_gb_pm": a_gb,
            "form_points_gap": h_pts - a_pts, "form_gb_gap": h_gb - a_gb,
            "h2h_matches": float(h2h_n), "h2h_points_gap": h2h_pts_gap,
            "h2h_gb_gap": h2h_gb_gap, "rest_gap_days": rest_gap,
            "home_rating": hstate.elo, "away_rating": astate.elo,
            "rating_diff": rating_diff,
            "home_rating_confidence": float(h_conf),
            "away_rating_confidence": float(a_conf),
            "both_rating_medium_plus": 1.0 if both_mp else 0.0,
            "rating_match_count_diff": float(hstate.matches - astate.matches),
        }
        rows.append(ExperimentRow(
            match_id=m.match_id, played_at=m.played_at, competition=m.competition,
            namespace=ns, target=target, features=features,
            home_rating_present=present_h, away_rating_present=present_a,
            both_medium_plus=both_mp,
        ))

        # --- update state AFTER recording (leak-free) ---
        rh, ra = hstate.elo, astate.elo
        exp_home = _expected(rh, ra, cfg.home_advantage)
        score_home = 1.0 if hs > as_ else (0.5 if hs == as_ else 0.0)
        delta = cfg.k_base * (score_home - exp_home)
        hstate.elo = rh + delta
        astate.elo = ra - delta
        hpts = 3 if hs > as_ else (1 if hs == as_ else 0)
        apts = 3 if as_ > hs else (1 if hs == as_ else 0)
        hstate.recent.append((hpts, hs - as_))
        astate.recent.append((apts, as_ - hs))
        hstate.matches += 1
        astate.matches += 1
        hstate.last_played = m.played_at
        astate.last_played = m.played_at
        # store h2h from pair[0] perspective
        if m.home_team_id == pair[0]:
            h2h[hk].append((hs - as_, float(hpts)))
        else:
            h2h[hk].append((as_ - hs, float(apts)))

    return rows


# --- temporal split ---------------------------------------------------------


def temporal_split(
    rows: list[ExperimentRow], test_fraction: float = 0.3
) -> tuple[list[ExperimentRow], list[ExperimentRow]]:
    """Split by played_at: oldest (1-frac) train, newest frac test. No future
    leaks into train (max train played_at <= min test played_at)."""
    ordered = sorted(rows, key=lambda r: (r.played_at, r.match_id))
    n = len(ordered)
    cut = int(round(n * (1.0 - test_fraction)))
    return ordered[:cut], ordered[cut:]


# --- metrics (pure) ---------------------------------------------------------


def _matrix(rows: list[ExperimentRow], feature_names: list[str]) -> tuple[list[list[float]], list[int]]:
    X = [[r.features[name] for name in feature_names] for r in rows]
    y = [LABEL_TO_INDEX[r.target] for r in rows]
    return X, y


def multiclass_brier(probs: list[list[float]], y: list[int]) -> float:
    total = 0.0
    for p, actual in zip(probs, y):
        total += sum((pi - (1.0 if i == actual else 0.0)) ** 2 for i, pi in enumerate(p))
    return total / len(y) if y else 0.0


def multiclass_log_loss(probs: list[list[float]], y: list[int]) -> float:
    eps = 1e-15
    total = 0.0
    for p, actual in zip(probs, y):
        total += -math.log(max(min(p[actual], 1 - eps), eps))
    return total / len(y) if y else 0.0


def top1_accuracy(probs: list[list[float]], y: list[int]) -> float:
    correct = sum(1 for p, a in zip(probs, y) if max(range(3), key=lambda i: p[i]) == a)
    return correct / len(y) if y else 0.0


def top2_coverage(probs: list[list[float]], y: list[int]) -> float:
    cov = 0
    for p, a in zip(probs, y):
        top2 = sorted(range(3), key=lambda i: p[i], reverse=True)[:2]
        if a in top2:
            cov += 1
    return cov / len(y) if y else 0.0


def calibration_bins(probs: list[list[float]], y: list[int], bins: int = 10) -> dict[str, Any]:
    """Reliability of the predicted (max-prob) class. Returns ECE + bins."""
    buckets: list[dict[str, float]] = [{"conf_sum": 0.0, "hits": 0.0, "n": 0.0} for _ in range(bins)]
    for p, a in zip(probs, y):
        pred = max(range(3), key=lambda i: p[i])
        conf = p[pred]
        idx = min(bins - 1, int(conf * bins))
        buckets[idx]["conf_sum"] += conf
        buckets[idx]["hits"] += 1.0 if pred == a else 0.0
        buckets[idx]["n"] += 1.0
    ece = 0.0
    n_total = len(y) or 1
    out_bins = []
    for b in buckets:
        if b["n"] == 0:
            continue
        avg_conf = b["conf_sum"] / b["n"]
        acc = b["hits"] / b["n"]
        ece += (b["n"] / n_total) * abs(avg_conf - acc)
        out_bins.append({
            "n": int(b["n"]), "avg_confidence": round(avg_conf, 4), "accuracy": round(acc, 4),
        })
    return {"ece": round(ece, 4), "bins": out_bins}


# --- training (XGBoost; the only sanctioned ML lib) -------------------------


def train_eval_xgb(
    train: list[ExperimentRow],
    test: list[ExperimentRow],
    feature_names: list[str],
    *,
    num_round: int = 200,
    max_depth: int = 4,
    eta: float = 0.1,
    seed: int = 42,
) -> dict[str, Any]:
    import numpy as np
    import xgboost as xgb

    Xtr, ytr = _matrix(train, feature_names)
    Xte, yte = _matrix(test, feature_names)
    dtrain = xgb.DMatrix(np.asarray(Xtr, dtype=float), label=np.asarray(ytr), feature_names=feature_names)
    dtest = xgb.DMatrix(np.asarray(Xte, dtype=float), label=np.asarray(yte), feature_names=feature_names)
    params = {
        "objective": "multi:softprob", "num_class": 3, "max_depth": max_depth,
        "eta": eta, "subsample": 0.9, "colsample_bytree": 0.9, "seed": seed,
        "eval_metric": "mlogloss", "verbosity": 0,
    }
    booster = xgb.train(params, dtrain, num_boost_round=num_round)
    raw = booster.predict(dtest)
    probs = [[float(x) for x in row] for row in raw]
    return {
        "n_train": len(train), "n_test": len(test),
        "top1_accuracy": round(top1_accuracy(probs, yte), 4),
        "top2_coverage": round(top2_coverage(probs, yte), 4),
        "brier_score": round(multiclass_brier(probs, yte), 4),
        "log_loss": round(multiclass_log_loss(probs, yte), 4),
        "calibration": calibration_bins(probs, yte),
        "_booster": booster,
    }


# --- orchestration ----------------------------------------------------------


def _class_balance(rows: list[ExperimentRow]) -> dict[str, float]:
    n = len(rows) or 1
    counts = {"1": 0, "X": 0, "2": 0}
    for r in rows:
        counts[r.target] += 1
    return {k: round(v / n, 3) for k, v in counts.items()}


def dataset_summary(rows: list[ExperimentRow], competition: str) -> dict[str, Any]:
    with_rating = [r for r in rows if r.home_rating_present and r.away_rating_present]
    both_mp = [r for r in rows if r.both_medium_plus]
    return {
        "competition": competition,
        "rows_total": len(rows),
        "rows_trainable": len(rows),
        "rows_with_rating": len(with_rating),
        "rows_both_medium_plus": len(both_mp),
        "both_medium_plus_rate": round(len(both_mp) / len(rows), 3) if rows else 0.0,
        "class_balance": _class_balance(rows),
    }


def run_competition(
    rows: list[ExperimentRow],
    competition: str,
    *,
    test_fraction: float,
    min_train: int,
    min_test: int,
    save_dir: Path | None,
    num_round: int = 200,
) -> dict[str, Any]:
    summary = dataset_summary(rows, competition)
    train, test = temporal_split(rows, test_fraction)
    summary["split"] = {"n_train": len(train), "n_test": len(test),
                        "train_end": str(train[-1].played_at) if train else None,
                        "test_start": str(test[0].played_at) if test else None}
    if len(train) < min_train or len(test) < min_test:
        summary["status"] = "insufficient_sample"
        summary["ablation"] = {}
        return summary

    ablation: dict[str, Any] = {}
    for set_name, fnames in FEATURE_SETS.items():
        res = train_eval_xgb(train, test, fnames, num_round=num_round)
        booster = res.pop("_booster")
        if save_dir is not None:
            save_dir.mkdir(parents=True, exist_ok=True)
            safe = competition.lower().replace(" ", "_").replace("/", "_")
            booster.save_model(str(save_dir / f"{safe}__{set_name}.json"))
        ablation[set_name] = res
    summary["ablation"] = ablation
    summary["status"] = "evaluated"
    summary["verdict"] = _verdict(summary, ablation)
    return summary


# Acceptance: rating must improve Brier AND not worsen log loss AND keep
# calibration AND have >=80% both_medium_plus coverage AND enough sample.
def _verdict(summary: dict[str, Any], ablation: dict[str, Any]) -> dict[str, Any]:
    base = ablation.get("without_rating", {})
    rated = ablation.get("with_rating", {})
    if not base or not rated:
        return {"recommendation": "needs_more_data", "reasons": ["missing ablation arm"]}
    brier_better = rated["brier_score"] < base["brier_score"]
    logloss_ok = rated["log_loss"] <= base["log_loss"] + 1e-9
    calib_ok = rated["calibration"]["ece"] <= base["calibration"]["ece"] + 0.02
    coverage_ok = summary["both_medium_plus_rate"] >= 0.80
    reasons = []
    if not brier_better:
        reasons.append(f"brier not improved ({rated['brier_score']} vs {base['brier_score']})")
    if not logloss_ok:
        reasons.append(f"log_loss worse ({rated['log_loss']} vs {base['log_loss']})")
    if not calib_ok:
        reasons.append(f"calibration degraded (ece {rated['calibration']['ece']} vs {base['calibration']['ece']})")
    if not coverage_ok:
        reasons.append(f"coverage {summary['both_medium_plus_rate']} < 0.80")
    if brier_better and logloss_ok and calib_ok and coverage_ok:
        rec = "approve_candidate"
    elif not coverage_ok or summary["rows_total"] < 200:
        rec = "needs_more_data"
    else:
        rec = "reject_for_now"
    return {
        "recommendation": rec,
        "brier_delta": round(rated["brier_score"] - base["brier_score"], 4),
        "log_loss_delta": round(rated["log_loss"] - base["log_loss"], 4),
        "reasons": reasons or ["all acceptance gates passed"],
    }


def run_experiment(
    session,
    *,
    competitions: list[str] | None,
    test_fraction: float = 0.3,
    min_train: int = 50,
    min_test: int = 20,
    save_dir: Path | None = _EXPERIMENT_DIR,
    num_round: int = 200,
) -> dict[str, Any]:
    rows = build_experiment_rows(session)
    by_comp: dict[str, list[ExperimentRow]] = defaultdict(list)
    for r in rows:
        by_comp[r.competition].append(r)

    targets = competitions if competitions else sorted(by_comp, key=lambda c: -len(by_comp[c]))
    reports = []
    for comp in targets:
        comp_rows = by_comp.get(comp, [])
        if not comp_rows:
            reports.append({"competition": comp, "status": "not_found", "rows_total": 0})
            continue
        reports.append(run_competition(
            comp_rows, comp, test_fraction=test_fraction, min_train=min_train,
            min_test=min_test, save_dir=save_dir, num_round=num_round,
        ))
    return {
        "experiment": "team_rating_r4",
        "total_rows": len(rows),
        "competitions_evaluated": len(reports),
        "feature_sets": FEATURE_SETS,
        "reports": reports,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Offline experimental rating-feature backtest.")
    parser.add_argument("--competition", action="append", help="repeatable; omit with --all")
    parser.add_argument("--all", action="store_true", help="evaluate all competitions")
    parser.add_argument("--test-fraction", type=float, default=0.3)
    parser.add_argument("--num-round", type=int, default=200)
    parser.add_argument("--no-save", action="store_true", help="do not write artifacts")
    args = parser.parse_args(argv)
    if not args.all and not args.competition:
        parser.error("pass --all or at least one --competition")

    save_dir = None if args.no_save else _EXPERIMENT_DIR
    with SessionLocal() as session:
        try:
            report = run_experiment(
                session,
                competitions=None if args.all else args.competition,
                test_fraction=args.test_fraction,
                save_dir=save_dir,
                num_round=args.num_round,
            )
        finally:
            session.rollback()  # hard read-only guarantee
    if save_dir is not None:
        save_dir.mkdir(parents=True, exist_ok=True)
        (save_dir / "metrics.json").write_text(json.dumps(report, indent=2, default=str))
    print(json.dumps(report, indent=2, sort_keys=True, default=str))
    return 0


if __name__ == "__main__":
    import sys

    sys.exit(main())
