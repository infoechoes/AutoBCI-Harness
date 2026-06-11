from __future__ import annotations

from typing import Any

import numpy as np


def _float_or_none(value: Any) -> float | None:
    if value is None:
        return None
    value = float(value)
    if not np.isfinite(value):
        return None
    return value


def _nanmean_or_none(values: list[float]) -> float | None:
    arr = np.asarray(values, dtype=np.float64)
    finite = np.isfinite(arr)
    if not np.any(finite):
        return None
    return float(arr[finite].mean())


def pearson_r_per_dim(y_true: np.ndarray, y_pred: np.ndarray) -> np.ndarray:
    y_true = y_true - y_true.mean(axis=0, keepdims=True)
    y_pred = y_pred - y_pred.mean(axis=0, keepdims=True)
    numerator = np.sum(y_true * y_pred, axis=0)
    denominator = np.sqrt(np.sum(y_true ** 2, axis=0) * np.sum(y_pred ** 2, axis=0))
    denominator = np.where(denominator < 1e-8, np.nan, denominator)
    return numerator / denominator


def rmse_per_dim(y_true: np.ndarray, y_pred: np.ndarray) -> np.ndarray:
    return np.sqrt(np.mean((y_true - y_pred) ** 2, axis=0))


def mae_per_dim(y_true: np.ndarray, y_pred: np.ndarray) -> np.ndarray:
    return np.mean(np.abs(y_true - y_pred), axis=0)


def bias_per_dim(y_true: np.ndarray, y_pred: np.ndarray) -> np.ndarray:
    return np.mean(y_pred - y_true, axis=0)


def gain_per_dim(y_true: np.ndarray, y_pred: np.ndarray) -> np.ndarray:
    true_std = y_true.std(axis=0)
    pred_std = y_pred.std(axis=0)
    safe_true_std = np.where(true_std < 1e-8, np.nan, true_std)
    return pred_std / safe_true_std


def _shifted_views(
    y_true: np.ndarray,
    y_pred: np.ndarray,
    shift_steps: int,
) -> tuple[np.ndarray, np.ndarray]:
    if shift_steps == 0:
        return y_true, y_pred
    if shift_steps > 0:
        return y_true[:-shift_steps], y_pred[shift_steps:]
    return y_true[-shift_steps:], y_pred[:shift_steps]


def best_lag_stats_per_dim(
    y_true: np.ndarray,
    y_pred: np.ndarray,
    *,
    lag_step_ms: float,
    max_lag_ms: float,
) -> tuple[np.ndarray, np.ndarray]:
    if lag_step_ms <= 0:
        raise ValueError("lag_step_ms must be > 0.")

    max_lag_steps = int(round(max_lag_ms / lag_step_ms))
    if max_lag_steps <= 0:
        best_r = pearson_r_per_dim(y_true, y_pred)
        return best_r, np.zeros_like(best_r)

    best_r = np.full(y_true.shape[1], np.nan, dtype=np.float64)
    best_lag_ms = np.zeros(y_true.shape[1], dtype=np.float64)

    for shift_steps in range(-max_lag_steps, max_lag_steps + 1):
        y_true_shifted, y_pred_shifted = _shifted_views(y_true, y_pred, shift_steps)
        if y_true_shifted.shape[0] < 2:
            continue
        r = pearson_r_per_dim(y_true_shifted, y_pred_shifted)
        update = np.isnan(best_r) | (r > best_r)
        best_r[update] = r[update]
        best_lag_ms[update] = shift_steps * lag_step_ms

    return best_r, best_lag_ms


def _scalar_summary(
    zero_lag_r: np.ndarray,
    mae: np.ndarray,
    rmse: np.ndarray,
    nrmse: np.ndarray,
    bias: np.ndarray,
    gain: np.ndarray,
    best_lag_r: np.ndarray,
    lag_star_ms: np.ndarray,
) -> dict[str, float | None]:
    return {
        "mean_pearson_r_zero_lag": _nanmean_or_none(zero_lag_r.tolist()),
        "mean_mae": float(np.mean(mae)),
        "mean_rmse": float(np.mean(rmse)),
        "mean_nrmse": float(np.mean(nrmse)),
        "mean_bias": _nanmean_or_none(bias.tolist()),
        "mean_abs_bias": _nanmean_or_none(np.abs(bias).tolist()),
        "mean_gain": _nanmean_or_none(gain.tolist()),
        "mean_best_lag_r": _nanmean_or_none(best_lag_r.tolist()),
        "mean_abs_lag_star_ms": _nanmean_or_none(np.abs(lag_star_ms).tolist()),
    }


def _split_dim_name(name: str) -> tuple[str, str | None]:
    if "_" not in name:
        return name, None
    marker, axis = name.rsplit("_", 1)
    axis = axis.lower()
    if not axis:
        return marker, None
    return marker, axis


def summarize_per_dim_rows(per_dim_rows: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    axis_groups: dict[str, list[dict[str, Any]]] = {}
    marker_groups: dict[str, list[dict[str, Any]]] = {}

    for row in per_dim_rows:
        marker, axis = _split_dim_name(str(row["name"]))
        marker_groups.setdefault(marker, []).append(row)
        if axis is not None:
            axis_groups.setdefault(axis, []).append(row)

    def _summarize_group(
        *,
        label_key: str,
        label: str,
        rows: list[dict[str, Any]],
    ) -> dict[str, Any]:
        pearson = [_float_or_none(row.get("pearson_r_zero_lag")) for row in rows]
        mae = [_float_or_none(row.get("mae")) for row in rows]
        rmse = [_float_or_none(row.get("rmse")) for row in rows]
        nrmse = [_float_or_none(row.get("nrmse")) for row in rows]
        bias = [_float_or_none(row.get("bias")) for row in rows]
        gain = [_float_or_none(row.get("gain")) for row in rows]
        best_lag_r = [_float_or_none(row.get("best_lag_r")) for row in rows]
        lag_star_ms = [_float_or_none(row.get("lag_star_ms")) for row in rows]
        return {
            label_key: label,
            "dim_count": len(rows),
            "dim_names": [str(row["name"]) for row in rows],
            "pearson_r_zero_lag": _nanmean_or_none(
                [np.nan if value is None else value for value in pearson]
            ),
            "mae": float(np.mean([0.0 if value is None else value for value in mae])),
            "rmse": float(np.mean([0.0 if value is None else value for value in rmse])),
            "nrmse": float(np.mean([0.0 if value is None else value for value in nrmse])),
            "bias": _nanmean_or_none([np.nan if value is None else value for value in bias]),
            "abs_bias": _nanmean_or_none(
                [np.nan if value is None else abs(value) for value in bias]
            ),
            "gain": _nanmean_or_none([np.nan if value is None else value for value in gain]),
            "best_lag_r": _nanmean_or_none(
                [np.nan if value is None else value for value in best_lag_r]
            ),
            "lag_star_ms": _nanmean_or_none(
                [np.nan if value is None else value for value in lag_star_ms]
            ),
            "abs_lag_star_ms": _nanmean_or_none(
                [np.nan if value is None else abs(value) for value in lag_star_ms]
            ),
        }

    axis_order = ["x", "y", "z"]
    sorted_axis_keys = [key for key in axis_order if key in axis_groups] + [
        key for key in axis_groups.keys() if key not in axis_order
    ]

    return {
        "axis_macro": [
            _summarize_group(label_key="axis", label=axis, rows=axis_groups[axis])
            for axis in sorted_axis_keys
        ],
        "marker_macro": [
            _summarize_group(label_key="marker", label=marker, rows=rows)
            for marker, rows in marker_groups.items()
        ],
    }


def build_marker_axis_grid(per_dim_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    marker_map: dict[str, dict[str, dict[str, Any]]] = {}
    for row in per_dim_rows:
        marker, axis = _split_dim_name(str(row["name"]))
        if axis is None:
            continue
        marker_map.setdefault(marker, {})[axis] = {
            "name": str(row["name"]),
            "pearson_r_zero_lag": _float_or_none(row.get("pearson_r_zero_lag")),
            "mae": _float_or_none(row.get("mae")),
            "rmse": _float_or_none(row.get("rmse")),
            "nrmse": _float_or_none(row.get("nrmse")),
            "best_lag_r": _float_or_none(row.get("best_lag_r")),
            "abs_lag_star_ms": (
                None
                if _float_or_none(row.get("lag_star_ms")) is None
                else abs(float(row["lag_star_ms"]))
            ),
        }

    grid_rows: list[dict[str, Any]] = []
    for marker in sorted(marker_map.keys()):
        grid_rows.append(
            {
                "marker": marker,
                "axes": {
                    axis: marker_map[marker].get(axis)
                    for axis in ("x", "y", "z")
                },
            }
        )
    return grid_rows


def compute_session_metrics(
    *,
    session_id: str,
    y_true: np.ndarray,
    y_pred: np.ndarray,
    kin_names: list[str],
    target_std: np.ndarray,
    lag_step_ms: float,
    max_lag_ms: float,
) -> dict[str, Any]:
    zero_lag_r = pearson_r_per_dim(y_true, y_pred)
    mae = mae_per_dim(y_true, y_pred)
    rmse = rmse_per_dim(y_true, y_pred)
    safe_target_std = np.where(target_std < 1e-6, 1.0, target_std)
    nrmse = rmse / safe_target_std
    bias = bias_per_dim(y_true, y_pred)
    gain = gain_per_dim(y_true, y_pred)
    best_lag_r, lag_star_ms = best_lag_stats_per_dim(
        y_true,
        y_pred,
        lag_step_ms=lag_step_ms,
        max_lag_ms=max_lag_ms,
    )

    per_dim_rows = [
        {
            "name": name,
            "pearson_r_zero_lag": None if np.isnan(r0) else float(r0),
            "mae": float(mae_dim),
            "rmse": float(rmse_dim),
            "nrmse": float(nrmse_dim),
            "bias": None if np.isnan(bias_dim) else float(bias_dim),
            "gain": None if np.isnan(gain_dim) else float(gain_dim),
            "best_lag_r": None if np.isnan(best_r_dim) else float(best_r_dim),
            "lag_star_ms": None if np.isnan(lag_dim) else float(lag_dim),
        }
        for name, r0, mae_dim, rmse_dim, nrmse_dim, bias_dim, gain_dim, best_r_dim, lag_dim in zip(
            kin_names,
            zero_lag_r,
            mae,
            rmse,
            nrmse,
            bias,
            gain,
            best_lag_r,
            lag_star_ms,
        )
    ]

    grouped = summarize_per_dim_rows(per_dim_rows)
    metrics: dict[str, Any] = {
        "session_id": session_id,
        "n_samples": int(y_true.shape[0]),
        **_scalar_summary(zero_lag_r, mae, rmse, nrmse, bias, gain, best_lag_r, lag_star_ms),
        "per_dim": per_dim_rows,
        "axis_summary": grouped["axis_macro"],
        "marker_summary": grouped["marker_macro"],
        "marker_axis_grid": build_marker_axis_grid(per_dim_rows),
    }
    return metrics


def aggregate_split_metrics(
    *,
    session_metrics: list[dict[str, Any]],
    kin_names: list[str],
    pooled_y_true: np.ndarray,
    pooled_y_pred: np.ndarray,
    target_std: np.ndarray,
    lag_step_ms: float,
    max_lag_ms: float,
) -> dict[str, Any]:
    if not session_metrics:
        raise ValueError("aggregate_split_metrics requires at least one session.")

    def _mean_from_sessions(key: str) -> float | None:
        values = [session[key] for session in session_metrics if session[key] is not None]
        if not values:
            return None
        return float(np.mean(values))

    per_dim_macro: list[dict[str, Any]] = []
    for dim_idx, name in enumerate(kin_names):
        values = [session["per_dim"][dim_idx] for session in session_metrics]
        per_dim_macro.append(
            {
                "name": name,
                "pearson_r_zero_lag": _nanmean_or_none(
                    [np.nan if v["pearson_r_zero_lag"] is None else v["pearson_r_zero_lag"] for v in values]
                ),
                "mae": float(np.mean([v["mae"] for v in values])),
                "rmse": float(np.mean([v["rmse"] for v in values])),
                "nrmse": float(np.mean([v["nrmse"] for v in values])),
                "bias": _nanmean_or_none([np.nan if v["bias"] is None else v["bias"] for v in values]),
                "gain": _nanmean_or_none([np.nan if v["gain"] is None else v["gain"] for v in values]),
                "best_lag_r": _nanmean_or_none(
                    [np.nan if v["best_lag_r"] is None else v["best_lag_r"] for v in values]
                ),
                "lag_star_ms": _nanmean_or_none(
                    [np.nan if v["lag_star_ms"] is None else v["lag_star_ms"] for v in values]
                ),
            }
        )

    pooled = compute_session_metrics(
        session_id="pooled",
        y_true=pooled_y_true,
        y_pred=pooled_y_pred,
        kin_names=kin_names,
        target_std=target_std,
        lag_step_ms=lag_step_ms,
        max_lag_ms=max_lag_ms,
    )
    grouped = summarize_per_dim_rows(per_dim_macro)

    return {
        "n_sessions": len(session_metrics),
        "session_ids": [session["session_id"] for session in session_metrics],
        "mean_pearson_r_zero_lag_macro": _mean_from_sessions("mean_pearson_r_zero_lag"),
        "mean_mae_macro": _mean_from_sessions("mean_mae"),
        "mean_rmse_macro": _mean_from_sessions("mean_rmse"),
        "mean_nrmse_macro": _mean_from_sessions("mean_nrmse"),
        "mean_bias_macro": _mean_from_sessions("mean_bias"),
        "mean_abs_bias_macro": _mean_from_sessions("mean_abs_bias"),
        "mean_gain_macro": _mean_from_sessions("mean_gain"),
        "mean_best_lag_r_macro": _mean_from_sessions("mean_best_lag_r"),
        "mean_abs_lag_star_ms_macro": _mean_from_sessions("mean_abs_lag_star_ms"),
        "per_dim_macro": per_dim_macro,
        "axis_macro": grouped["axis_macro"],
        "marker_macro": grouped["marker_macro"],
        "marker_axis_grid": build_marker_axis_grid(per_dim_macro),
        "pooled": pooled,
        "per_session": session_metrics,
    }
