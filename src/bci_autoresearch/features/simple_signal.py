from __future__ import annotations

import numpy as np


def normalize_reducers(reducers: list[str] | tuple[str, ...]) -> tuple[str, ...]:
    normalized = tuple(item.strip().lower() for item in reducers if item.strip())
    if not normalized:
        raise ValueError("At least one reducer is required.")
    invalid = [item for item in normalized if item not in {"mean", "abs_mean", "rms"}]
    if invalid:
        raise ValueError(f"Unsupported reducers: {', '.join(sorted(set(invalid)))}")
    return normalized


def feature_channel_names(
    channel_names: list[str],
    reducers: list[str] | tuple[str, ...],
) -> list[str]:
    normalized = normalize_reducers(reducers)
    names: list[str] = []
    for reducer in normalized:
        for channel_name in channel_names:
            names.append(f"{channel_name}:{reducer}")
    return names


def bin_reduce(
    ecog: np.ndarray,
    *,
    bin_samples: int,
    reducers: list[str] | tuple[str, ...],
) -> np.ndarray:
    if ecog.ndim != 2:
        raise ValueError(f"ecog must be 2D (channels, time), got {ecog.shape}")
    if bin_samples <= 0:
        raise ValueError("bin_samples must be >= 1.")

    normalized = normalize_reducers(reducers)
    n_channels, n_time = ecog.shape
    usable = (n_time // bin_samples) * bin_samples
    if usable <= 0:
        raise ValueError("Window is shorter than one feature bin.")

    trimmed = np.asarray(ecog[:, :usable], dtype=np.float32)
    binned = trimmed.reshape(n_channels, usable // bin_samples, bin_samples)

    outputs: list[np.ndarray] = []
    for reducer in normalized:
        if reducer == "mean":
            outputs.append(np.mean(binned, axis=2))
        elif reducer == "abs_mean":
            outputs.append(np.mean(np.abs(binned), axis=2))
        elif reducer == "rms":
            outputs.append(np.sqrt(np.mean(np.square(binned), axis=2)))
    return np.concatenate(outputs, axis=0).astype(np.float32)
