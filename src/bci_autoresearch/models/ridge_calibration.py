from __future__ import annotations

import numpy as np


def fit_prediction_calibration(
    *,
    y_pred: np.ndarray,
    y_true: np.ndarray,
    min_variance: float = 1e-8,
) -> tuple[np.ndarray, np.ndarray]:
    pred = np.asarray(y_pred, dtype=np.float64)
    true = np.asarray(y_true, dtype=np.float64)
    if pred.shape != true.shape:
        raise ValueError("y_pred and y_true must have the same shape for calibration.")
    if pred.ndim != 2:
        raise ValueError("Prediction calibration expects 2D arrays shaped (n_samples, n_outputs).")
    if pred.shape[0] == 0:
        raise ValueError("Prediction calibration requires at least one row.")

    pred_mean = pred.mean(axis=0)
    true_mean = true.mean(axis=0)
    pred_centered = pred - pred_mean
    true_centered = true - true_mean
    pred_var = np.square(pred_centered).mean(axis=0)
    covariance = (pred_centered * true_centered).mean(axis=0)

    scale = np.divide(
        covariance,
        pred_var,
        out=np.zeros_like(covariance),
        where=pred_var >= min_variance,
    )
    offset = true_mean - scale * pred_mean
    return scale.astype(np.float32), offset.astype(np.float32)


def apply_prediction_calibration(
    *,
    y_pred: np.ndarray,
    scale: np.ndarray,
    offset: np.ndarray,
) -> np.ndarray:
    pred = np.asarray(y_pred, dtype=np.float32)
    scale_arr = np.asarray(scale, dtype=np.float32)
    offset_arr = np.asarray(offset, dtype=np.float32)
    if pred.ndim != 2:
        raise ValueError("Prediction calibration expects 2D prediction arrays.")
    if scale_arr.shape != (pred.shape[1],) or offset_arr.shape != (pred.shape[1],):
        raise ValueError("Calibration parameters must have shape (n_outputs,).")
    return pred * scale_arr[None, :] + offset_arr[None, :]
