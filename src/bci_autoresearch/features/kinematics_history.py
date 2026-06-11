from __future__ import annotations

import numpy as np


def build_binned_history_features(
    *,
    target_matrix: np.ndarray,
    x_start: int,
    x_end: int,
    bin_samples: int,
) -> np.ndarray:
    if bin_samples <= 0:
        raise ValueError("bin_samples must be positive.")
    if x_start < 0 or x_end <= x_start:
        raise ValueError(f"Invalid history window: start={x_start}, end={x_end}.")
    if x_start % bin_samples != 0:
        raise ValueError(
            f"History window start is not aligned to history bins: start={x_start}, bin={bin_samples}."
        )
    if (x_end - x_start) % bin_samples != 0:
        raise ValueError(
            f"History window is not aligned to history bins: start={x_start}, end={x_end}, bin={bin_samples}."
        )
    if x_end > int(target_matrix.shape[0]):
        raise ValueError(
            f"History window exceeds target length: end={x_end}, length={target_matrix.shape[0]}."
        )

    window = np.asarray(target_matrix[x_start:x_end], dtype=np.float32)
    if window.ndim != 2:
        raise ValueError("target_matrix must be 2D [time, dims].")
    if window.shape[0] == 0:
        raise ValueError("History window is empty.")

    n_bins = window.shape[0] // bin_samples
    binned = window.reshape(n_bins, bin_samples, window.shape[1]).mean(axis=1)
    return np.asarray(binned.T.reshape(-1), dtype=np.float32)
