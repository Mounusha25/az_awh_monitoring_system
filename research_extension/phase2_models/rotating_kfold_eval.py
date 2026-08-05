"""
AWH Phase 2 — Rotating Time-Split K-Fold Evaluation

Settles the open question left in PENDING_TASKS.md Round 4: when the
benchmark was scoped down to stations 3+6 only, attribution F1 dropped from
0.437 (8-station scope, CI [0.354, 0.514]) to 0.297 (2-station scope, CI
[0.198, 0.388]) with barely-overlapping CIs. Two competing explanations were
raised and left unresolved:

  1. Genuine sensor calibration drift in the newly-backfilled, later test
     period (station 6 grew from 20,278 to 44,317 rows via the B9 backfill,
     extending the usable time range).
  2. Small-sample fragility — the 2-station test split just happened to draw
     an unlucky mix of fault instances (test-set spike count was 24 windows
     vs 84 in val), and per-fault-type recall can swing 10-20 points on
     sample size alone.

These predict different signatures under a walk-forward (expanding-window)
k-fold: if it's drift, degradation should track the split boundary —
whichever fold's test block sits latest in time should be worst, and
consistently so across all four fault types (a real physical drift affects
every parameter's calibration together). If it's small-sample fragility,
degradation should be noisy and fault-type-specific, tied to which
particular fault instances happen to land in a given fold's test block
rather than to how late that block falls chronologically.

Only the current-best model (the decoupled IsolationForestEnsemble) is
evaluated here — this is a diagnostic for the benchmark/methodology, not a
model bake-off (train_phase2_models.py already covers that).

Usage:
  python rotating_kfold_eval.py
  python rotating_kfold_eval.py --k 5 --stations 3,6
"""

from __future__ import annotations

import argparse
import os

import mlflow
import numpy as np
import pandas as pd

from build_benchmark_dataset import DATA_DIR, INCLUDED_STATIONS, TRACKING_URI, WINDOW, build_windows
from evaluate import bootstrap_ci, evaluate_model, per_fault_type_breakdown, tune_threshold
from isolation_forest_model import IsolationForestEnsemble

PERCENTILE_THRESHOLD_CANDIDATES = np.concatenate([np.arange(0.5, 0.95, 0.05), np.arange(0.95, 1.0, 0.01)])


def rotating_splits(windows: pd.DataFrame, k: int, warmup_frac: float = 0.5) -> list[dict]:
    """Walk-forward folds: train expands, val/test are contiguous blocks that
    slide later in time on each fold. `warmup_frac` sets how much of the
    timeline is reserved as the minimum training set before the first fold's
    val/test blocks begin — folds only rotate through the remaining
    (1 - warmup_frac) of the timeline, split into 2*k equal blocks (val, test
    alternating) so each fold's test block is a distinct, later time range.
    """
    windows = windows.sort_values("window_start").reset_index(drop=True)
    embargo = WINDOW

    edges = [warmup_frac + i * (1 - warmup_frac) / (2 * k) for i in range(2 * k + 1)]
    quantiles = windows["window_start"].quantile(edges).tolist()

    folds = []
    for i in range(k):
        train_end = quantiles[2 * i]
        val_start = train_end + embargo
        val_end = quantiles[2 * i + 1]
        test_start = val_end + embargo
        test_end = quantiles[2 * i + 2]

        train = windows[windows["window_end"] <= train_end]
        val = windows[(windows["window_start"] >= val_start) & (windows["window_end"] <= val_end)]
        test = windows[(windows["window_start"] >= test_start) & (windows["window_end"] <= test_end)]

        folds.append({
            "fold": i,
            "train": train, "val": val, "test": test,
            "train_end": train_end, "test_start": test_start, "test_end": test_end,
        })
    return folds


def run_fold(fold: dict) -> dict:
    train_df, val_df, test_df = fold["train"], fold["val"], fold["test"]
    n_test_faults = test_df.loc[test_df["is_anomaly"], "fault_id"].nunique()
    print(f"\n[Fold {fold['fold']}] train={len(train_df):,} val={len(val_df):,} test={len(test_df):,} "
          f"({n_test_faults} test fault instances) "
          f"test_range=[{fold['test_start'].date()}, {fold['test_end'].date()}]")

    if len(train_df) == 0 or len(val_df) == 0 or len(test_df) == 0 or n_test_faults == 0:
        print(f"[Fold {fold['fold']}] SKIPPED — empty split or no anomalous test instances")
        return {"fold": fold["fold"], "skipped": True}

    model = IsolationForestEnsemble().fit(train_df)
    best_thresh, val_f1 = tune_threshold(model, val_df, PERCENTILE_THRESHOLD_CANDIDATES)
    metrics = evaluate_model(model, test_df)
    breakdown = per_fault_type_breakdown(test_df, metrics["predictions"])
    ci = bootstrap_ci(model, test_df, n_bootstrap=200, seed=fold["fold"])

    print(f"[Fold {fold['fold']}] threshold={best_thresh:.2f} (val F1={val_f1:.3f}) "
          f"test_detection_f1={metrics['detection_f1']:.3f} "
          f"test_attribution_f1={metrics['attribution_f1']:.3f}")
    print(f"[Fold {fold['fold']}] detection F1 CI [{ci['detection_f1']['ci_low']:.3f}, "
          f"{ci['detection_f1']['ci_high']:.3f}], attribution F1 CI "
          f"[{ci['attribution_f1']['ci_low']:.3f}, {ci['attribution_f1']['ci_high']:.3f}]")
    print(breakdown.to_string(index=False))

    return {
        "fold": fold["fold"],
        "skipped": False,
        "test_start": fold["test_start"], "test_end": fold["test_end"],
        "n_test_faults": n_test_faults,
        "threshold": best_thresh,
        "detection_f1": metrics["detection_f1"],
        "attribution_f1": metrics["attribution_f1"],
        "detection_f1_ci_low": ci["detection_f1"]["ci_low"],
        "detection_f1_ci_high": ci["detection_f1"]["ci_high"],
        "attribution_f1_ci_low": ci["attribution_f1"]["ci_low"],
        "attribution_f1_ci_high": ci["attribution_f1"]["ci_high"],
        "breakdown": breakdown,
    }


def summarize(results: list[dict]) -> None:
    valid = [r for r in results if not r["skipped"]]
    if len(valid) < 2:
        print("\n[Summary] Fewer than 2 valid folds — can't assess a trend.")
        return

    print("\n[Summary] Attribution F1 by fold (chronological order = later test window):")
    for r in valid:
        print(f"  fold {r['fold']} ({r['test_start'].date()}–{r['test_end'].date()}, "
              f"{r['n_test_faults']} faults): attribution_f1={r['attribution_f1']:.3f} "
              f"CI=[{r['attribution_f1_ci_low']:.3f}, {r['attribution_f1_ci_high']:.3f}]")

    fold_order = [r["fold"] for r in valid]
    attribution_f1s = [r["attribution_f1"] for r in valid]
    monotonic_decline = all(a >= b for a, b in zip(attribution_f1s, attribution_f1s[1:]))
    corr = float(np.corrcoef(fold_order, attribution_f1s)[0, 1]) if len(valid) >= 2 else float("nan")

    print(f"\n[Summary] Spearman-ish trend check: fold-index vs attribution_f1 correlation = {corr:.3f}")
    print(f"[Summary] Strictly monotonic decline across folds: {monotonic_decline}")

    by_type: dict[str, list[float]] = {}
    for r in valid:
        for _, row in r["breakdown"].iterrows():
            by_type.setdefault(row["anomaly_type"], []).append(row["detection_recall"])
    print("\n[Summary] Per-fault-type detection recall across folds (uniform decline = drift signature; "
          "one type collapsing while others hold = small-sample fragility signature):")
    for fault_type, recalls in sorted(by_type.items()):
        print(f"  {fault_type:10s}: {[f'{r:.2f}' for r in recalls]}")

    print("\n[Summary] Interpretation guide (not automated — read the numbers above):")
    print("  - If correlation is strongly negative AND all four fault types decline together"
          " -> supports genuine drift.")
    print("  - If recall swings are large, inconsistent in direction across folds, and concentrated"
          " in whichever fault type has the fewest instances in a given fold's test block"
          " -> supports small-sample fragility.")


def main():
    parser = argparse.ArgumentParser(description="Rotating time-split k-fold diagnostic for Phase 2 attribution")
    parser.add_argument("--k", type=int, default=5)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--target-anomaly-frac", type=float, default=0.175)
    parser.add_argument("--stations", type=str, default=None,
                         help="Comma-separated station IDs (default: INCLUDED_STATIONS = 3,6)")
    parser.add_argument("--warmup-frac", type=float, default=0.5,
                         help="Fraction of the timeline reserved as minimum training data before folds begin")
    args = parser.parse_args()

    stations = [int(s) for s in args.stations.split(",")] if args.stations else INCLUDED_STATIONS
    windows, all_faults, _ = build_windows(args.seed, args.target_anomaly_frac, stations)
    print(f"\n[RotatingKFold] {len(windows):,} total windows, {len(all_faults):,} total fault instances, "
          f"k={args.k}, warmup_frac={args.warmup_frac}")

    folds = rotating_splits(windows, args.k, args.warmup_frac)
    results = [run_fold(f) for f in folds]
    summarize(results)

    os.makedirs(DATA_DIR, exist_ok=True)
    out_rows = [{k: v for k, v in r.items() if k != "breakdown"} for r in results if not r["skipped"]]
    out_path = os.path.join(DATA_DIR, "rotating_kfold_results.csv")
    pd.DataFrame(out_rows).to_csv(out_path, index=False)
    print(f"\n[RotatingKFold] Wrote per-fold results to {out_path}")

    breakdown_rows = []
    for r in results:
        if r["skipped"]:
            continue
        b = r["breakdown"].copy()
        b["fold"] = r["fold"]
        breakdown_rows.append(b)
    if breakdown_rows:
        breakdown_path = os.path.join(DATA_DIR, "rotating_kfold_breakdown.csv")
        pd.concat(breakdown_rows, ignore_index=True).to_csv(breakdown_path, index=False)
        print(f"[RotatingKFold] Wrote per-fault-type breakdown to {breakdown_path}")

    mlflow.set_tracking_uri(TRACKING_URI)
    mlflow.set_experiment("AWH-AnomalyDetection")
    with mlflow.start_run(run_name="phase2-rotating-kfold"):
        mlflow.log_params({"k": args.k, "stations": ",".join(map(str, stations)),
                            "warmup_frac": args.warmup_frac, "seed": args.seed})
        for r in results:
            if r["skipped"]:
                continue
            mlflow.log_metrics({
                f"fold{r['fold']}_detection_f1": r["detection_f1"],
                f"fold{r['fold']}_attribution_f1": r["attribution_f1"],
            })
        mlflow.set_tags({"phase": "2", "stage": "diagnostic", "rq": "RQ1"})
        mlflow.log_artifact(out_path)
        if breakdown_rows:
            mlflow.log_artifact(breakdown_path)


if __name__ == "__main__":
    main()
