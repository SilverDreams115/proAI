"""R4.2 — strict held-out validation of a rating candidate. OFFLINE / NON-PRODUCTIVE.

Validates whether one competition (default International Friendlies),
restricted to the deploy regime (`both_medium_plus_only`), is
`ready_for_controlled_gate_design` — using an HONEST calibration protocol:

    temporal split  train 60% | calibration 20% | test 20%  (ordered by played_at)
      * the XGBoost booster is fit ONLY on the train fold;
      * the calibrator (temperature / isotonic) is fit ONLY on the calibration
        fold (never on test);
      * every reported metric is computed ONLY on the test fold.

An oracle calibration (fit on test) is reported too, but ONLY as a secondary
diagnostic upper bound — never as an approval criterion.

Usage::

    python backend/scripts/validate_rating_candidate.py \
      --competition "International Friendlies" --subset both-medium-plus

Hard read-only on the DB (rollback). Experimental artifacts under
artifacts/experiments/team_rating_r4/ (gitignored), never registered. Touches
no productive table, no FeatureService/PredictionService, no approval gate;
feature flag stays OFF.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from app.db.session import SessionLocal
from scripts.train_rating_experiment import BASELINE_FEATURES
from scripts.train_rating_experiment import FEATURE_SETS
from scripts.train_rating_experiment import LABEL_TO_INDEX
from scripts.train_rating_experiment import RATING_FEATURES
from scripts.train_rating_experiment import SUBSETS
from scripts.train_rating_experiment import ExperimentRow
from scripts.train_rating_experiment import apply_temperature
from scripts.train_rating_experiment import build_experiment_rows
from scripts.train_rating_experiment import calibration_bins
from scripts.train_rating_experiment import fit_temperature
from scripts.train_rating_experiment import multiclass_brier
from scripts.train_rating_experiment import multiclass_log_loss
from scripts.train_rating_experiment import top1_accuracy
from scripts.train_rating_experiment import top2_coverage

_EXPERIMENT_DIR = Path("artifacts/experiments/team_rating_r4")
_ISOTONIC_MIN_CAL_ROWS = 150
_CLI_TO_SUBSET = {
    "all-trainable": "all_trainable",
    "both-medium-plus": "both_medium_plus_only",
    "rating-present": "rating_present_only",
}


# --- split ------------------------------------------------------------------


def three_way_split(
    rows: list[ExperimentRow], train_frac: float = 0.6, cal_frac: float = 0.2
) -> tuple[list[ExperimentRow], list[ExperimentRow], list[ExperimentRow]]:
    """Temporal train/calibration/test split. No future leak: each fold is a
    contiguous, increasing slice of played_at."""
    ordered = sorted(rows, key=lambda r: (r.played_at, r.match_id))
    n = len(ordered)
    i_tr = int(round(n * train_frac))
    i_cal = int(round(n * (train_frac + cal_frac)))
    return ordered[:i_tr], ordered[i_tr:i_cal], ordered[i_cal:]


# --- model ------------------------------------------------------------------


def _matrix(rows: list[ExperimentRow], feature_names: list[str]):
    import numpy as np
    X = np.asarray([[r.features[name] for name in feature_names] for r in rows], dtype=float)
    y = [LABEL_TO_INDEX[r.target] for r in rows]
    return X, y


def _train_booster(train: list[ExperimentRow], feature_names: list[str], *, num_round: int, seed: int = 42):
    import numpy as np
    import xgboost as xgb
    X, y = _matrix(train, feature_names)
    dtrain = xgb.DMatrix(X, label=np.asarray(y), feature_names=feature_names)
    params = {
        "objective": "multi:softprob", "num_class": 3, "max_depth": 4,
        "eta": 0.1, "subsample": 0.9, "colsample_bytree": 0.9, "seed": seed,
        "eval_metric": "mlogloss", "verbosity": 0,
    }
    return xgb.train(params, dtrain, num_boost_round=num_round)


def _predict(booster, rows: list[ExperimentRow], feature_names: list[str]) -> list[list[float]]:
    import xgboost as xgb
    X, _ = _matrix(rows, feature_names)
    return [[float(x) for x in row] for row in booster.predict(xgb.DMatrix(X, feature_names=feature_names))]


# --- isotonic (PAV, pure; no sklearn) ---------------------------------------


def _pav(xs: list[float], ys: list[float]) -> tuple[list[float], list[float]]:
    """Pool-Adjacent-Violators isotonic (non-decreasing) fit. Returns the
    sorted x knots and the fitted (monotone) y values for interpolation."""
    order = sorted(range(len(xs)), key=lambda i: xs[i])
    x_sorted = [xs[i] for i in order]
    y_sorted = [ys[i] for i in order]
    # blocks of (sum, count, value)
    blocks: list[list[float]] = []
    for y in y_sorted:
        blocks.append([y, 1.0, y])
        while len(blocks) > 1 and blocks[-2][2] >= blocks[-1][2]:
            s2, c2, _ = blocks.pop()
            s1, c1, _ = blocks.pop()
            s, c = s1 + s2, c1 + c2
            blocks.append([s, c, s / c])
    fitted: list[float] = []
    for s, c, v in blocks:
        fitted.extend([v] * int(c))
    return x_sorted, fitted


def _isotonic_apply(x_knots: list[float], y_fit: list[float], x: float) -> float:
    """Linear interpolation over the PAV step function (clamped at the ends)."""
    if not x_knots:
        return x
    if x <= x_knots[0]:
        return y_fit[0]
    if x >= x_knots[-1]:
        return y_fit[-1]
    lo, hi = 0, len(x_knots) - 1
    while lo + 1 < hi:
        mid = (lo + hi) // 2
        if x_knots[mid] <= x:
            lo = mid
        else:
            hi = mid
    x0, x1 = x_knots[lo], x_knots[hi]
    y0, y1 = y_fit[lo], y_fit[hi]
    if x1 == x0:
        return y0
    return y0 + (y1 - y0) * (x - x0) / (x1 - x0)


def fit_isotonic_multiclass(probs: list[list[float]], y: list[int]):
    """One-vs-rest isotonic per class, fit on the calibration fold."""
    models = []
    for k in range(3):
        xs = [p[k] for p in probs]
        ys = [1.0 if yi == k else 0.0 for yi in y]
        models.append(_pav(xs, ys))
    return models


def apply_isotonic_multiclass(models, probs: list[list[float]]) -> list[list[float]]:
    out: list[list[float]] = []
    for p in probs:
        cal = [max(_isotonic_apply(models[k][0], models[k][1], p[k]), 1e-6) for k in range(3)]
        s = sum(cal) or 1.0
        out.append([c / s for c in cal])
    return out


# --- per-arm evaluation on the test fold ------------------------------------


def _test_metrics(probs: list[list[float]], y: list[int]) -> dict[str, Any]:
    import numpy as np
    maxp = [max(p) for p in probs]
    arr = np.asarray(maxp) if maxp else np.asarray([0.0])
    return {
        "top1_accuracy": round(top1_accuracy(probs, y), 4),
        "top2_coverage": round(top2_coverage(probs, y), 4),
        "brier_score": round(multiclass_brier(probs, y), 4),
        "log_loss": round(multiclass_log_loss(probs, y), 4),
        "ece": calibration_bins(probs, y)["ece"],
        "calibration_bins": calibration_bins(probs, y)["bins"],
        "max_prob_distribution": {
            "mean": round(float(arr.mean()), 4),
            "p10": round(float(np.percentile(arr, 10)), 4),
            "p50": round(float(np.percentile(arr, 50)), 4),
            "p90": round(float(np.percentile(arr, 90)), 4),
        },
    }


def validate(
    rows: list[ExperimentRow],
    *,
    competition: str,
    subset: str,
    num_round: int = 200,
    save_dir: Path | None = _EXPERIMENT_DIR,
) -> dict[str, Any]:
    predicate = SUBSETS[subset]
    subset_rows = [r for r in rows if predicate(r)]
    train, cal, test = three_way_split(subset_rows)

    def _balance(rs: list[ExperimentRow]) -> dict[str, float]:
        n = len(rs) or 1
        c = {"1": 0, "X": 0, "2": 0}
        for r in rs:
            c[r.target] += 1
        return {k: round(v / n, 3) for k, v in c.items()}

    report: dict[str, Any] = {
        "competition": competition,
        "subset": subset,
        "rows_total": len(subset_rows),
        "rows_train": len(train),
        "rows_calibration": len(cal),
        "rows_test": len(test),
        "class_balance_test": _balance(test),
        "split_boundaries": {
            "train_end": str(train[-1].played_at) if train else None,
            "cal_start": str(cal[0].played_at) if cal else None,
            "cal_end": str(cal[-1].played_at) if cal else None,
            "test_start": str(test[0].played_at) if test else None,
        },
    }
    if len(train) < 50 or len(cal) < 30 or len(test) < 50:
        report["status"] = "insufficient_sample"
        report["arms"] = {}
        report["verdict"] = {"recommendation": "needs_more_data",
                             "reasons": ["insufficient train/cal/test sample"]}
        return report

    yte = [LABEL_TO_INDEX[r.target] for r in test]
    ycal = [LABEL_TO_INDEX[r.target] for r in cal]

    # Baseline (no rating)
    base_booster = _train_booster(train, BASELINE_FEATURES, num_round=num_round)
    base_probs = _predict(base_booster, test, BASELINE_FEATURES)

    # With rating
    wr_booster = _train_booster(train, FEATURE_SETS["with_rating"], num_round=num_round)
    wr_test = _predict(wr_booster, test, FEATURE_SETS["with_rating"])
    wr_cal = _predict(wr_booster, cal, FEATURE_SETS["with_rating"])

    # Rating only
    ro_booster = _train_booster(train, RATING_FEATURES, num_round=num_round)
    ro_test = _predict(ro_booster, test, RATING_FEATURES)
    ro_cal = _predict(ro_booster, cal, RATING_FEATURES)

    # Calibrators fit ONLY on the calibration fold.
    t_wr = fit_temperature(wr_cal, ycal)
    t_ro = fit_temperature(ro_cal, ycal)
    wr_temp = apply_temperature(wr_test, t_wr)
    ro_temp = apply_temperature(ro_test, t_ro)

    arms: dict[str, Any] = {
        "baseline_without_rating": _test_metrics(base_probs, yte),
        "with_rating_uncalibrated": _test_metrics(wr_test, yte),
        "with_rating_temperature_calibrated": {**_test_metrics(wr_temp, yte), "temperature": t_wr},
        "rating_only_uncalibrated": _test_metrics(ro_test, yte),
        "rating_only_temperature_calibrated": {**_test_metrics(ro_temp, yte), "temperature": t_ro},
    }

    # Isotonic (only if the calibration fold is big enough to be stable).
    if len(cal) >= _ISOTONIC_MIN_CAL_ROWS:
        iso = fit_isotonic_multiclass(wr_cal, ycal)
        wr_iso = apply_isotonic_multiclass(iso, wr_test)
        arms["with_rating_isotonic_calibrated"] = _test_metrics(wr_iso, yte)
    else:
        arms["with_rating_isotonic_calibrated"] = {
            "status": "skipped",
            "reason": f"calibration fold {len(cal)} < {_ISOTONIC_MIN_CAL_ROWS}",
        }

    # Secondary diagnostic ONLY: oracle temperature fit on test (upper bound).
    t_oracle = fit_temperature(wr_test, yte)
    report["oracle_diagnostic_not_a_criterion"] = {
        "temperature": t_oracle,
        **{k: _test_metrics(apply_temperature(wr_test, t_oracle), yte)[k] for k in ("log_loss", "ece")},
    }

    report["arms"] = arms
    report["status"] = "evaluated"
    report["verdict"] = _decide(report, arms)

    if save_dir is not None:
        save_dir.mkdir(parents=True, exist_ok=True)
        safe = competition.lower().replace(" ", "_").replace("/", "_")
        for name, bst in (("baseline", base_booster), ("with_rating", wr_booster), ("rating_only", ro_booster)):
            bst.save_model(str(save_dir / f"validate__{safe}__{subset}__{name}.json"))
    return report


def _decide(report: dict[str, Any], arms: dict[str, Any]) -> dict[str, Any]:
    base = arms["baseline_without_rating"]
    # Prefer the calibrated arm that wins on the cal-fit calibrator (temperature
    # is always available; isotonic when not skipped). Pick the better-Brier.
    cal_arms = [arms["with_rating_temperature_calibrated"]]
    iso = arms.get("with_rating_isotonic_calibrated", {})
    if "brier_score" in iso:
        cal_arms.append(iso)
    chosen = min(cal_arms, key=lambda a: a["brier_score"])
    uncal = arms["with_rating_uncalibrated"]

    reasons: list[str] = []
    brier_ok = chosen["brier_score"] < base["brier_score"]
    logloss_ok = chosen["log_loss"] <= base["log_loss"] + 1e-9
    ece_ok = chosen["ece"] <= uncal["ece"] + 1e-9 and chosen["ece"] <= base["ece"] + 1e-9
    top1_ok = chosen["top1_accuracy"] >= base["top1_accuracy"] - 0.02
    top2_ok = chosen["top2_coverage"] >= base["top2_coverage"] - 0.02
    sample_ok = report["rows_test"] >= 150

    if not brier_ok:
        reasons.append(f"brier not improved ({chosen['brier_score']} vs {base['brier_score']})")
    if not logloss_ok:
        reasons.append(f"log_loss worse ({chosen['log_loss']} vs {base['log_loss']})")
    if not ece_ok:
        reasons.append(f"ece not improved vs uncal/base ({chosen['ece']} / uncal {uncal['ece']} / base {base['ece']})")
    if not top1_ok:
        reasons.append(f"top1 drop >2pp ({chosen['top1_accuracy']} vs {base['top1_accuracy']})")
    if not top2_ok:
        reasons.append(f"top2 drop >2pp ({chosen['top2_coverage']} vs {base['top2_coverage']})")
    if not sample_ok:
        reasons.append(f"test rows {report['rows_test']} < 150")

    if brier_ok and logloss_ok and ece_ok and top1_ok and top2_ok and sample_ok:
        rec = "ready_for_controlled_gate_design"
    elif not sample_ok:
        rec = "needs_more_data"
    elif not ece_ok and brier_ok and logloss_ok:
        rec = "needs_calibration_pipeline"
    else:
        rec = "reject_for_now"
    return {
        "recommendation": rec,
        "chosen_calibrated_arm_brier": chosen["brier_score"],
        "baseline_brier": base["brier_score"],
        "reasons": reasons or ["all acceptance gates passed on the held-out test fold"],
    }


# --- future-guard simulation (read-only) ------------------------------------


def simulate_guard(rows: list[ExperimentRow], competition: str) -> dict[str, Any]:
    """How many historical matches of this competition would pass a future
    scoring guard (both_rating_medium_plus AND rating_present)."""
    comp_rows = [r for r in rows if r.competition == competition]
    n = len(comp_rows)
    passed = [r for r in comp_rows if r.both_medium_plus and r.home_rating_present and r.away_rating_present]
    return {
        "competition": competition,
        "historical_matches": n,
        "would_pass_guard": len(passed),
        "would_fallback": n - len(passed),
        "guard_pass_rate": round(len(passed) / n, 3) if n else 0.0,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Held-out validation of a rating candidate (R4.2).")
    parser.add_argument("--competition", default="International Friendlies")
    parser.add_argument("--subset", choices=list(_CLI_TO_SUBSET), default="both-medium-plus")
    parser.add_argument("--num-round", type=int, default=200)
    parser.add_argument("--no-save", action="store_true")
    args = parser.parse_args(argv)

    subset = _CLI_TO_SUBSET[args.subset]
    save_dir = None if args.no_save else _EXPERIMENT_DIR
    with SessionLocal() as session:
        try:
            rows = build_experiment_rows(session)
            comp_rows = [r for r in rows if r.competition == args.competition]
            report = validate(
                comp_rows, competition=args.competition, subset=subset,
                num_round=args.num_round, save_dir=save_dir,
            )
            report["guard_simulation"] = simulate_guard(rows, args.competition)
        finally:
            session.rollback()  # hard read-only guarantee
    print(json.dumps(report, indent=2, sort_keys=True, default=str))
    return 0


if __name__ == "__main__":
    import sys

    sys.exit(main())
