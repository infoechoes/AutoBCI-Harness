from __future__ import annotations

from typing import Any

import numpy as np


def _safe_corr(x: np.ndarray, y: np.ndarray) -> float | None:
    if x.size < 2 or y.size < 2:
        return None
    if np.allclose(x, x[0]) or np.allclose(y, y[0]):
        return None
    return float(np.corrcoef(x, y)[0, 1])


def build_segment_candidates(
    *,
    session_id: str,
    time_s: np.ndarray,
    y_true: np.ndarray,
    y_pred: np.ndarray,
    target_names: list[str],
    segment_seconds: float,
) -> list[dict[str, Any]]:
    time_s = np.asarray(time_s, dtype=np.float32)
    y_true = np.asarray(y_true, dtype=np.float32)
    y_pred = np.asarray(y_pred, dtype=np.float32)
    if time_s.ndim != 1:
        raise ValueError("time_s must be 1D.")
    if y_true.shape != y_pred.shape:
        raise ValueError("y_true and y_pred must have identical shapes.")
    if y_true.shape[0] != time_s.shape[0]:
        raise ValueError("time_s length must match y rows.")

    segments: list[dict[str, Any]] = []
    for start_idx in range(time_s.shape[0]):
        end_idx = start_idx
        while end_idx + 1 < time_s.shape[0] and float(time_s[end_idx] - time_s[start_idx]) < segment_seconds:
            end_idx += 1
        if float(time_s[end_idx] - time_s[start_idx]) < segment_seconds or end_idx - start_idx + 1 < 3:
            continue
        true_window = y_true[start_idx : end_idx + 1]
        pred_window = y_pred[start_idx : end_idx + 1]
        per_joint_rows: list[dict[str, Any]] = []
        local_r_values: list[float] = []
        amplitudes: list[float] = []
        for dim_idx, name in enumerate(target_names):
            local_r = _safe_corr(true_window[:, dim_idx], pred_window[:, dim_idx])
            amplitude = float(np.max(true_window[:, dim_idx]) - np.min(true_window[:, dim_idx]))
            amplitudes.append(amplitude)
            if local_r is not None:
                local_r_values.append(local_r)
            per_joint_rows.append(
                {
                    "name": name,
                    "local_r": local_r,
                    "true_amplitude": amplitude,
                }
            )
        segments.append(
            {
                "session_id": session_id,
                "start_time_s": float(time_s[start_idx]),
                "end_time_s": float(time_s[end_idx]),
                "start_index": int(start_idx),
                "end_index": int(end_idx),
                "n_points": int(end_idx - start_idx + 1),
                "mean_local_r": float(np.mean(local_r_values)) if local_r_values else float("nan"),
                "mean_true_amplitude": float(np.mean(amplitudes)) if amplitudes else 0.0,
                "per_joint": per_joint_rows,
            }
        )
    return segments


def select_hard_segment(
    *,
    sessions: list[dict[str, Any]],
    segment_seconds: float = 12.0,
) -> dict[str, Any]:
    candidates: list[dict[str, Any]] = []
    for session in sessions:
        candidates.extend(
            build_segment_candidates(
                session_id=str(session["session_id"]),
                time_s=np.asarray(session["time_s"], dtype=np.float32),
                y_true=np.asarray(session["y_true"], dtype=np.float32),
                y_pred=np.asarray(session["y_pred"], dtype=np.float32),
                target_names=list(session["target_names"]),
                segment_seconds=segment_seconds,
            )
        )
    if not candidates:
        raise ValueError("No segment candidates were built.")

    amplitude_threshold = float(np.median([row["mean_true_amplitude"] for row in candidates]))
    eligible = [row for row in candidates if row["mean_true_amplitude"] >= amplitude_threshold]
    chosen = min(eligible, key=lambda row: (float(row["mean_local_r"]), -float(row["mean_true_amplitude"])))
    return {
        **chosen,
        "amplitude_threshold": amplitude_threshold,
    }
