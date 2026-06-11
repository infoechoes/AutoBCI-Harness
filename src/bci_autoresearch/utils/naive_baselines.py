from __future__ import annotations

import numpy as np


def mean_pose_prediction(mean_target: np.ndarray, *, n_rows: int) -> np.ndarray:
    mean_target = np.asarray(mean_target, dtype=np.float32)
    if mean_target.ndim != 1:
        raise ValueError("mean_target must be a 1D vector.")
    if n_rows < 0:
        raise ValueError("n_rows must be >= 0.")
    if n_rows == 0:
        return np.empty((0, mean_target.shape[0]), dtype=np.float32)
    return np.repeat(mean_target[None, :], n_rows, axis=0).astype(np.float32)


def per_session_mean_prediction(y_true: np.ndarray) -> np.ndarray:
    y_true = np.asarray(y_true, dtype=np.float32)
    if y_true.ndim != 2:
        raise ValueError("y_true must be a 2D matrix.")
    if y_true.shape[0] == 0:
        return np.empty_like(y_true)
    mean_target = np.mean(y_true, axis=0, dtype=np.float64).astype(np.float32)
    return mean_pose_prediction(mean_target, n_rows=y_true.shape[0])


def last_frame_prediction(y_true: np.ndarray) -> np.ndarray:
    y_true = np.asarray(y_true, dtype=np.float32)
    if y_true.ndim != 2:
        raise ValueError("y_true must be a 2D matrix.")
    if y_true.shape[0] == 0:
        return np.empty_like(y_true)
    y_pred = np.empty_like(y_true)
    y_pred[0] = y_true[0]
    if y_true.shape[0] > 1:
        y_pred[1:] = y_true[:-1]
    return y_pred.astype(np.float32)
