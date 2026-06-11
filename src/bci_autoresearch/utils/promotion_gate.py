from __future__ import annotations

import math
from statistics import mean, median, pstdev
from typing import Any

from .amplitude_diagnostics import classify_gain_status


SEVERE_COMPRESSION = "severe compression"
SENTINEL_JOINTS = ("Kne", "Wri", "Mcp")
GAIN_IMPROVEMENT_EPSILON = 0.05
VAL_TIE_EPSILON = 0.005

MODEL_COMPLEXITY_RANK = {
    "ridge": 0,
    "random_forest": 1,
    "xgboost": 2,
    "feature_lstm": 3,
}


def _is_finite_number(value: Any) -> bool:
    try:
        return math.isfinite(float(value))
    except (TypeError, ValueError):
        return False


def _per_dim_lookup(rows: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return {str(row["name"]): row for row in rows}


def count_severe_compression(rows: list[dict[str, Any]]) -> int:
    return sum(1 for row in rows if classify_gain_status(row.get("gain")) == SEVERE_COMPRESSION)


def mean_abs_bias(rows: list[dict[str, Any]]) -> float | None:
    values = [abs(float(row["bias"])) for row in rows if _is_finite_number(row.get("bias"))]
    if not values:
        return None
    return float(mean(values))


def mean_gain_distance(
    rows: list[dict[str, Any]],
    *,
    joint_names: tuple[str, ...] | None = None,
) -> float | None:
    lookup = _per_dim_lookup(rows)
    names = joint_names or tuple(sorted(lookup))
    values = []
    for name in names:
        gain = lookup.get(name, {}).get("gain")
        if _is_finite_number(gain):
            values.append(abs(1.0 - float(gain)))
    if not values:
        return None
    return float(mean(values))


def model_complexity_rank(model_family: str | None) -> int:
    return MODEL_COMPLEXITY_RANK.get(str(model_family or ""), 99)


def summarize_scalar(values: list[float]) -> dict[str, float | int]:
    if not values:
        return {"count": 0, "mean": float("nan"), "median": float("nan"), "std": float("nan")}
    return {
        "count": len(values),
        "mean": float(mean(values)),
        "median": float(median(values)),
        "std": float(pstdev(values)) if len(values) > 1 else 0.0,
    }


def _aggregate_per_joint(seed_runs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_name: dict[str, list[dict[str, Any]]] = {}
    for run in seed_runs:
        for row in run.get("per_dim", []):
            by_name.setdefault(str(row["name"]), []).append(row)

    summary_rows: list[dict[str, Any]] = []
    for name, rows in sorted(by_name.items()):
        metrics: dict[str, Any] = {"name": name}
        for key in ("pearson_r_zero_lag", "mae", "rmse", "gain", "bias"):
            values = [float(row[key]) for row in rows if _is_finite_number(row.get(key))]
            if values:
                metrics[key] = float(median(values))
                metrics[f"{key}_mean"] = float(mean(values))
                metrics[f"{key}_std"] = float(pstdev(values)) if len(values) > 1 else 0.0
            else:
                metrics[key] = None
                metrics[f"{key}_mean"] = None
                metrics[f"{key}_std"] = None
        metrics["gain_status"] = classify_gain_status(metrics.get("gain"))
        metrics["severe_count"] = sum(
            1 for row in rows if classify_gain_status(row.get("gain")) == SEVERE_COMPRESSION
        )
        summary_rows.append(metrics)
    return summary_rows


def _build_seed_rows(
    *,
    seed_runs: list[dict[str, Any]],
    require_stopped_epoch: bool,
) -> list[dict[str, Any]]:
    per_seed_rows: list[dict[str, Any]] = []
    for row in seed_runs:
        per_dim = list(row.get("per_dim", []))
        anomalies: list[str] = []
        if not _is_finite_number(row.get("val_r")) or not _is_finite_number(row.get("test_r")):
            anomalies.append("non_finite_metrics")
        if require_stopped_epoch and int(row.get("stopped_epoch") or 0) <= 1:
            anomalies.append("stopped_epoch_le_1")
        per_seed_rows.append(
            {
                "run_id": str(row["run_id"]),
                "seed": int(row["seed"]),
                "val_r": float(row["val_r"]) if _is_finite_number(row.get("val_r")) else None,
                "test_r": float(row["test_r"]) if _is_finite_number(row.get("test_r")) else None,
                "test_mae": float(row["test_mae"]) if _is_finite_number(row.get("test_mae")) else None,
                "test_rmse": float(row["test_rmse"]) if _is_finite_number(row.get("test_rmse")) else None,
                "stopped_epoch": int(row.get("stopped_epoch") or 0),
                "best_epoch": int(row.get("best_epoch") or 0),
                "anomalies": anomalies,
                "per_dim": per_dim,
                "severe_compression_count": count_severe_compression(per_dim),
            }
        )
    return per_seed_rows


def _sentinel_gain_rows(
    *,
    accepted_per_dim: list[dict[str, Any]],
    candidate_per_dim: list[dict[str, Any]],
    sentinel_joint_names: tuple[str, ...],
    min_improvement: float,
) -> list[dict[str, Any]]:
    accepted_lookup = _per_dim_lookup(accepted_per_dim)
    candidate_lookup = _per_dim_lookup(candidate_per_dim)
    rows: list[dict[str, Any]] = []
    for name in sentinel_joint_names:
        accepted_row = accepted_lookup.get(name, {})
        candidate_row = candidate_lookup.get(name, {})
        accepted_gain = accepted_row.get("gain")
        candidate_gain = candidate_row.get("gain")
        accepted_distance = None if not _is_finite_number(accepted_gain) else abs(1.0 - float(accepted_gain))
        candidate_distance = None if not _is_finite_number(candidate_gain) else abs(1.0 - float(candidate_gain))
        improved = (
            accepted_distance is not None
            and candidate_distance is not None
            and candidate_distance <= accepted_distance - min_improvement
        )
        rows.append(
            {
                "name": name,
                "accepted_gain": accepted_gain,
                "candidate_gain": candidate_gain,
                "accepted_gain_distance": accepted_distance,
                "candidate_gain_distance": candidate_distance,
                "improved": bool(improved),
            }
        )
    return rows


def build_feature_lstm_seed_sweep_summary(
    *,
    accepted_best: dict[str, Any],
    seed_runs: list[dict[str, Any]],
    min_formal_val: float = 0.3680,
    sentinel_joint_names: tuple[str, ...] = SENTINEL_JOINTS,
    min_gain_improvement: float = GAIN_IMPROVEMENT_EPSILON,
) -> dict[str, Any]:
    if not seed_runs:
        raise ValueError("seed_runs must not be empty.")

    per_seed_rows = _build_seed_rows(seed_runs=seed_runs, require_stopped_epoch=True)

    valid_seed_rows = [row for row in per_seed_rows if row["val_r"] is not None and row["test_r"] is not None]
    val_values = [float(row["val_r"]) for row in valid_seed_rows]
    test_values = [float(row["test_r"]) for row in valid_seed_rows]
    mae_values = [float(row["test_mae"]) for row in valid_seed_rows if row["test_mae"] is not None]
    rmse_values = [float(row["test_rmse"]) for row in valid_seed_rows if row["test_rmse"] is not None]
    severe_counts = [int(row["severe_compression_count"]) for row in per_seed_rows]
    accepted_per_dim = list(accepted_best.get("per_dim", []))
    accepted_severe_count = count_severe_compression(accepted_per_dim)
    aggregated_per_joint = _aggregate_per_joint(per_seed_rows)
    sentinel_rows = _sentinel_gain_rows(
        accepted_per_dim=accepted_per_dim,
        candidate_per_dim=aggregated_per_joint,
        sentinel_joint_names=sentinel_joint_names,
        min_improvement=min_gain_improvement,
    )
    best_seed_row = max(valid_seed_rows, key=lambda row: float(row["val_r"]))

    checks = {
        "all_seeds_completed": len(valid_seed_rows) == len(per_seed_rows),
        "all_val_above_ridge": all(float(row["val_r"]) > float(accepted_best["val_r"]) for row in valid_seed_rows),
        "median_val_threshold": bool(val_values) and float(median(val_values)) >= float(min_formal_val),
        "median_test_not_worse": bool(test_values) and float(median(test_values)) >= float(accepted_best["test_r"]),
        "no_anomalies": all(not row["anomalies"] for row in per_seed_rows),
        "sentinel_gain_improved": any(row["improved"] for row in sentinel_rows),
        "severe_compression_guard": bool(severe_counts) and int(median(severe_counts)) <= int(accepted_severe_count + 1),
    }
    failed_reasons = [name for name, passed in checks.items() if not passed]

    return {
        "accepted_best_run_id": str(accepted_best["run_id"]),
        "accepted_best_val_r": float(accepted_best["val_r"]),
        "accepted_best_test_r": float(accepted_best["test_r"]),
        "best_seed_run_id": str(best_seed_row["run_id"]),
        "seed_runs": per_seed_rows,
        "aggregates": {
            "val_r": summarize_scalar(val_values),
            "test_r": summarize_scalar(test_values),
            "test_mae": summarize_scalar(mae_values),
            "test_rmse": summarize_scalar(rmse_values),
            "severe_compression_count": summarize_scalar([float(value) for value in severe_counts]),
        },
        "accepted_best_severe_compression_count": int(accepted_severe_count),
        "per_joint_median": aggregated_per_joint,
        "sentinel_joints": sentinel_rows,
        "gate": {
            "passed": all(checks.values()),
            "checks": checks,
            "failed_reasons": failed_reasons,
            "thresholds": {
                "min_formal_val": float(min_formal_val),
                "min_gain_improvement": float(min_gain_improvement),
                "max_extra_severe_joints": 1,
            },
        },
    }


def build_xgboost_seed_sweep_summary(
    *,
    accepted_best: dict[str, Any],
    seed_runs: list[dict[str, Any]],
    tie_epsilon: float = VAL_TIE_EPSILON,
    sentinel_joint_names: tuple[str, ...] = SENTINEL_JOINTS,
) -> dict[str, Any]:
    if not seed_runs:
        raise ValueError("seed_runs must not be empty.")

    per_seed_rows = _build_seed_rows(seed_runs=seed_runs, require_stopped_epoch=False)
    valid_seed_rows = [row for row in per_seed_rows if row["val_r"] is not None and row["test_r"] is not None]
    val_values = [float(row["val_r"]) for row in valid_seed_rows]
    test_values = [float(row["test_r"]) for row in valid_seed_rows]
    mae_values = [float(row["test_mae"]) for row in valid_seed_rows if row["test_mae"] is not None]
    rmse_values = [float(row["test_rmse"]) for row in valid_seed_rows if row["test_rmse"] is not None]
    severe_counts = [int(row["severe_compression_count"]) for row in per_seed_rows]

    accepted_per_dim = list(accepted_best.get("per_dim", []))
    accepted_val_r = float(accepted_best["val_r"])
    candidate_val_r = float(median(val_values)) if val_values else float("nan")
    accepted_abs_bias = mean_abs_bias(accepted_per_dim)
    candidate_per_joint = _aggregate_per_joint(per_seed_rows)
    candidate_abs_bias = mean_abs_bias(candidate_per_joint)
    accepted_sentinel_gain_distance = mean_gain_distance(accepted_per_dim, joint_names=sentinel_joint_names)
    candidate_sentinel_gain_distance = mean_gain_distance(candidate_per_joint, joint_names=sentinel_joint_names)
    accepted_model_rank = model_complexity_rank(str(accepted_best.get("model_family")))
    candidate_model_rank = model_complexity_rank("xgboost")
    val_gap = candidate_val_r - accepted_val_r
    within_tie = math.isfinite(val_gap) and abs(val_gap) <= float(tie_epsilon)

    wins_on_primary = math.isfinite(val_gap) and val_gap > float(tie_epsilon)
    wins_on_bias = (
        within_tie
        and accepted_abs_bias is not None
        and candidate_abs_bias is not None
        and candidate_abs_bias < accepted_abs_bias
    )
    wins_on_gain = (
        within_tie
        and not wins_on_bias
        and accepted_sentinel_gain_distance is not None
        and candidate_sentinel_gain_distance is not None
        and candidate_sentinel_gain_distance < accepted_sentinel_gain_distance
    )
    wins_on_complexity = (
        within_tie
        and not wins_on_bias
        and not wins_on_gain
        and candidate_model_rank < accepted_model_rank
    )

    sentinel_rows = _sentinel_gain_rows(
        accepted_per_dim=accepted_per_dim,
        candidate_per_dim=candidate_per_joint,
        sentinel_joint_names=sentinel_joint_names,
        min_improvement=0.0,
    )
    best_seed_row = max(valid_seed_rows, key=lambda row: float(row["val_r"]))

    checks = {
        "all_seeds_completed": len(valid_seed_rows) == len(per_seed_rows),
        "no_anomalies": all(not row["anomalies"] for row in per_seed_rows),
        "wins_on_primary": wins_on_primary,
        "wins_on_bias_tiebreak": wins_on_bias,
        "wins_on_gain_tiebreak": wins_on_gain,
        "wins_on_complexity_tiebreak": wins_on_complexity,
    }
    gate_passed = checks["all_seeds_completed"] and checks["no_anomalies"] and (
        wins_on_primary or wins_on_bias or wins_on_gain or wins_on_complexity
    )
    failed_reasons = [name for name, passed in checks.items() if not passed and name not in {"wins_on_primary", "wins_on_bias_tiebreak", "wins_on_gain_tiebreak", "wins_on_complexity_tiebreak"}]
    if not (wins_on_primary or wins_on_bias or wins_on_gain or wins_on_complexity):
        failed_reasons.append("does_not_beat_accepted_stable_best")

    return {
        "accepted_best_run_id": str(accepted_best["run_id"]),
        "accepted_best_val_r": accepted_val_r,
        "accepted_best_test_r": float(accepted_best["test_r"]),
        "best_seed_run_id": str(best_seed_row["run_id"]),
        "seed_runs": per_seed_rows,
        "aggregates": {
            "val_r": summarize_scalar(val_values),
            "test_r": summarize_scalar(test_values),
            "test_mae": summarize_scalar(mae_values),
            "test_rmse": summarize_scalar(rmse_values),
            "severe_compression_count": summarize_scalar([float(value) for value in severe_counts]),
        },
        "accepted_best_mean_abs_bias": accepted_abs_bias,
        "candidate_mean_abs_bias": candidate_abs_bias,
        "accepted_best_sentinel_gain_distance": accepted_sentinel_gain_distance,
        "candidate_sentinel_gain_distance": candidate_sentinel_gain_distance,
        "per_joint_median": candidate_per_joint,
        "sentinel_joints": sentinel_rows,
        "gate": {
            "passed": gate_passed,
            "checks": checks,
            "failed_reasons": failed_reasons,
            "comparison": {
                "val_gap": val_gap,
                "tie_epsilon": float(tie_epsilon),
                "accepted_model_rank": accepted_model_rank,
                "candidate_model_rank": candidate_model_rank,
            },
        },
    }


def format_xgboost_seed_sweep_markdown(payload: dict[str, Any]) -> str:
    gate = payload["gate"]
    lines = [
        "# Phase C XGBoost seed sweep",
        "",
        f"- accepted stable best: `{payload['accepted_best_run_id']}`",
        f"- best seed run: `{payload['best_seed_run_id']}`",
        f"- promotion gate: {'PASS' if gate['passed'] else 'HOLD'}",
        "",
        "## Seeds",
        "",
        "| run | seed | val r | test r | test MAE | test RMSE | severe joints | anomalies |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: | --- |",
    ]
    for row in payload.get("seed_runs", []):
        anomalies = ", ".join(row["anomalies"]) if row["anomalies"] else "-"
        lines.append(
            f"| {row['run_id']} | {row['seed']} | {row['val_r']:.4f} | {row['test_r']:.4f} | "
            f"{'-' if row['test_mae'] is None else f'{row['test_mae']:.4f}'} | "
            f"{'-' if row['test_rmse'] is None else f'{row['test_rmse']:.4f}'} | "
            f"{row['severe_compression_count']} | {anomalies} |"
        )
    lines.extend(
        [
            "",
            "## Gate comparison",
            "",
            f"- median val r: {payload['aggregates']['val_r']['median']:.4f}",
            f"- accepted stable best val r: {payload['accepted_best_val_r']:.4f}",
            f"- median test r: {payload['aggregates']['test_r']['median']:.4f}",
            f"- accepted stable best test r: {payload['accepted_best_test_r']:.4f}",
            f"- candidate mean abs bias: {'-' if payload['candidate_mean_abs_bias'] is None else f'{float(payload['candidate_mean_abs_bias']):.4f}'}",
            f"- accepted mean abs bias: {'-' if payload['accepted_best_mean_abs_bias'] is None else f'{float(payload['accepted_best_mean_abs_bias']):.4f}'}",
            "",
            "## Sentinel joints",
            "",
            "| joint | accepted gain | candidate gain | |1-gain| accepted | |1-gain| candidate |",
            "| --- | ---: | ---: | ---: | ---: |",
        ]
    )
    for row in payload.get("sentinel_joints", []):
        lines.append(
            f"| {row['name']} | "
            f"{'-' if row['accepted_gain'] is None else f'{float(row['accepted_gain']):.4f}'} | "
            f"{'-' if row['candidate_gain'] is None else f'{float(row['candidate_gain']):.4f}'} | "
            f"{'-' if row['accepted_gain_distance'] is None else f'{float(row['accepted_gain_distance']):.4f}'} | "
            f"{'-' if row['candidate_gain_distance'] is None else f'{float(row['candidate_gain_distance']):.4f}'} |"
        )
    lines.extend(
        [
            "",
            "## Gate checks",
            "",
        ]
    )
    for name, passed in gate.get("checks", {}).items():
        lines.append(f"- `{name}`: {'pass' if passed else 'fail'}")
    if gate.get("failed_reasons"):
        lines.extend(["", "## Hold reasons", ""])
        for reason in gate["failed_reasons"]:
            lines.append(f"- `{reason}`")
    lines.append("")
    return "\n".join(lines)


def format_feature_lstm_seed_sweep_markdown(payload: dict[str, Any]) -> str:
    gate = payload["gate"]
    lines = [
        "# Phase C feature-LSTM seed sweep",
        "",
        f"- accepted best: `{payload['accepted_best_run_id']}`",
        f"- best seed run: `{payload['best_seed_run_id']}`",
        f"- promotion gate: {'PASS' if gate['passed'] else 'HOLD'}",
        "",
        "## Seeds",
        "",
        "| run | seed | val r | test r | test MAE | test RMSE | severe joints | anomalies |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: | --- |",
    ]
    for row in payload.get("seed_runs", []):
        anomalies = ", ".join(row["anomalies"]) if row["anomalies"] else "-"
        lines.append(
            f"| {row['run_id']} | {row['seed']} | {row['val_r']:.4f} | {row['test_r']:.4f} | "
            f"{'-' if row['test_mae'] is None else f'{row['test_mae']:.4f}'} | "
            f"{'-' if row['test_rmse'] is None else f'{row['test_rmse']:.4f}'} | "
            f"{row['severe_compression_count']} | {anomalies} |"
        )
    lines.extend(
        [
            "",
            "## Aggregate",
            "",
            f"- median val r: {payload['aggregates']['val_r']['median']:.4f}",
            f"- median test r: {payload['aggregates']['test_r']['median']:.4f}",
            f"- median severe compression joints: {payload['aggregates']['severe_compression_count']['median']:.1f}",
            "",
            "## Sentinel joints",
            "",
            "| joint | accepted gain | candidate gain | |1-gain| accepted | |1-gain| candidate | improved |",
            "| --- | ---: | ---: | ---: | ---: | --- |",
        ]
    )
    for row in payload.get("sentinel_joints", []):
        lines.append(
            f"| {row['name']} | "
            f"{'-' if row['accepted_gain'] is None else f'{float(row['accepted_gain']):.4f}'} | "
            f"{'-' if row['candidate_gain'] is None else f'{float(row['candidate_gain']):.4f}'} | "
            f"{'-' if row['accepted_gain_distance'] is None else f'{float(row['accepted_gain_distance']):.4f}'} | "
            f"{'-' if row['candidate_gain_distance'] is None else f'{float(row['candidate_gain_distance']):.4f}'} | "
            f"{'yes' if row['improved'] else 'no'} |"
        )
    lines.extend(
        [
            "",
            "## Gate checks",
            "",
        ]
    )
    for name, passed in gate.get("checks", {}).items():
        lines.append(f"- `{name}`: {'pass' if passed else 'fail'}")
    if gate.get("failed_reasons"):
        lines.extend(
            [
                "",
                "## Hold reasons",
                "",
            ]
        )
        for reason in gate["failed_reasons"]:
            lines.append(f"- `{reason}`")
    lines.append("")
    return "\n".join(lines)
