from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from scipy import signal

from .simple_signal import bin_reduce, feature_channel_names, normalize_reducers


BANDPOWER_BANK: tuple[tuple[str, float, float], ...] = (
    ("delta", 0.5, 4.0),
    ("theta", 4.0, 8.0),
    ("alpha", 8.0, 13.0),
    ("beta", 13.0, 30.0),
    ("gamma", 30.0, 70.0),
    ("high_gamma", 70.0, 150.0),
)
SUPPORTED_SIGNAL_PREPROCESS = {"legacy_raw", "car_notch_bandpass"}
SUPPORTED_FEATURE_FAMILIES = {
    "simple_stats",
    "lmp",
    "hg_power",
    "bandpower_bank",
    "phase_state",
    "dmd_sdm",
}
POWER_EPS = 1e-8


@dataclass(frozen=True)
class FeatureSequence:
    values: np.ndarray
    feature_names: list[str]
    bin_samples: int
    usable_samples: int
    feature_families: tuple[str, ...]
    signal_preprocess: str


def parse_feature_families(raw_value: str) -> tuple[str, ...]:
    tokens = [
        token.strip().lower()
        for chunk in raw_value.split(",")
        for token in chunk.split("+")
        if token.strip()
    ]
    if not tokens:
        raise ValueError("--feature-family cannot be empty.")
    invalid = [token for token in tokens if token not in SUPPORTED_FEATURE_FAMILIES]
    if invalid:
        raise ValueError(f"Unsupported feature families: {', '.join(sorted(set(invalid)))}")
    return tuple(tokens)


def normalize_signal_preprocess(mode: str) -> str:
    normalized = mode.strip().lower()
    if normalized not in SUPPORTED_SIGNAL_PREPROCESS:
        raise ValueError(
            f"Unsupported signal preprocess: {mode!r}. "
            f"Expected one of {sorted(SUPPORTED_SIGNAL_PREPROCESS)}."
        )
    return normalized


def _trim_full_bins(ecog: np.ndarray, *, bin_samples: int) -> tuple[np.ndarray, int]:
    usable = (ecog.shape[1] // bin_samples) * bin_samples
    if usable <= 0:
        raise ValueError("Signal is shorter than one full feature bin.")
    return np.asarray(ecog[:, :usable], dtype=np.float32), int(usable)


def _common_average_reference(ecog: np.ndarray) -> np.ndarray:
    return np.asarray(ecog - np.mean(ecog, axis=0, keepdims=True), dtype=np.float32)


def _notch_filter(ecog: np.ndarray, *, fs_hz: float, line_hz: float = 50.0, q: float = 30.0) -> np.ndarray:
    b, a = signal.iirnotch(w0=line_hz, Q=q, fs=fs_hz)
    filtered = signal.lfilter(b, a, ecog, axis=1)
    return np.asarray(filtered, dtype=np.float32)


def _bandpass_filter(
    ecog: np.ndarray,
    *,
    fs_hz: float,
    low_hz: float,
    high_hz: float,
    order: int = 4,
) -> np.ndarray:
    nyquist = fs_hz / 2.0
    high_hz = min(high_hz, nyquist - 1.0)
    if not (0.0 < low_hz < high_hz < nyquist):
        raise ValueError(
            f"Invalid bandpass range for fs={fs_hz}: low={low_hz}, high={high_hz}, nyquist={nyquist}."
        )
    sos = signal.butter(
        order,
        [low_hz, high_hz],
        btype="bandpass",
        fs=fs_hz,
        output="sos",
    )
    filtered = signal.sosfilt(sos, ecog, axis=1)
    return np.asarray(filtered, dtype=np.float32)


def _apply_signal_preprocess(
    ecog: np.ndarray,
    *,
    fs_hz: float,
    mode: str,
) -> np.ndarray:
    normalized = normalize_signal_preprocess(mode)
    signal_in = np.asarray(ecog, dtype=np.float32)
    if normalized == "legacy_raw":
        return signal_in
    signal_out = _common_average_reference(signal_in)
    signal_out = _notch_filter(signal_out, fs_hz=fs_hz)
    signal_out = _bandpass_filter(signal_out, fs_hz=fs_hz, low_hz=0.5, high_hz=200.0)
    return signal_out.astype(np.float32)


def _binned_mean(signal_tc: np.ndarray, *, bin_samples: int) -> np.ndarray:
    trimmed, _ = _trim_full_bins(signal_tc, bin_samples=bin_samples)
    n_channels, usable = trimmed.shape
    binned = trimmed.reshape(n_channels, usable // bin_samples, bin_samples)
    return np.mean(binned, axis=2).astype(np.float32)


def _binned_log_power(signal_tc: np.ndarray, *, bin_samples: int) -> np.ndarray:
    trimmed, _ = _trim_full_bins(signal_tc, bin_samples=bin_samples)
    n_channels, usable = trimmed.shape
    binned = trimmed.reshape(n_channels, usable // bin_samples, bin_samples)
    power = np.mean(np.square(binned), axis=2)
    return np.log(power + POWER_EPS).astype(np.float32)


def _binned_phase_state_features(signal_tc: np.ndarray, *, bin_samples: int) -> np.ndarray:
    trimmed, usable = _trim_full_bins(signal_tc, bin_samples=bin_samples)
    n_channels = trimmed.shape[0]
    n_bins = usable // bin_samples
    analytic = signal.hilbert(trimmed, axis=1)
    analytic_norm = np.maximum(np.abs(analytic), POWER_EPS)
    unit_phase = analytic / analytic_norm
    binned_phase = unit_phase.reshape(n_channels, n_bins, bin_samples)
    phase_cos = np.mean(np.real(binned_phase), axis=2, dtype=np.float32)
    phase_sin = np.mean(np.imag(binned_phase), axis=2, dtype=np.float32)
    phase_norm = np.sqrt(np.square(phase_cos) + np.square(phase_sin) + POWER_EPS, dtype=np.float32)
    phase_cos = (phase_cos / phase_norm).astype(np.float32)
    phase_sin = (phase_sin / phase_norm).astype(np.float32)
    return np.concatenate([phase_cos, phase_sin], axis=0).astype(np.float32)


def _binned_sdm_mode_features(signal_tc: np.ndarray, *, bin_samples: int, n_modes: int = 2) -> np.ndarray:
    trimmed, usable = _trim_full_bins(signal_tc, bin_samples=bin_samples)
    n_channels = trimmed.shape[0]
    n_bins = usable // bin_samples
    binned = trimmed.reshape(n_channels, n_bins, bin_samples)
    modes = np.zeros((n_channels * n_modes, n_bins), dtype=np.float32)
    for bin_idx in range(n_bins):
        matrix = np.asarray(binned[:, bin_idx, :], dtype=np.float32)
        matrix = matrix - np.mean(matrix, axis=1, keepdims=True, dtype=np.float32)
        try:
            u, s, _ = np.linalg.svd(matrix, full_matrices=False)
        except np.linalg.LinAlgError:
            continue
        usable_modes = min(n_modes, u.shape[1], s.shape[0])
        for mode_idx in range(usable_modes):
            loading = np.asarray(u[:, mode_idx], dtype=np.float32)
            if float(np.sum(loading)) < 0.0:
                loading = -loading
            modes[mode_idx * n_channels:(mode_idx + 1) * n_channels, bin_idx] = (
                loading * np.float32(s[mode_idx])
            )
    return modes


def build_feature_sequence(
    *,
    ecog_uV: np.ndarray,
    channel_names: list[str],
    fs_hz: float,
    bin_samples: int,
    signal_preprocess: str,
    feature_families: tuple[str, ...],
    feature_reducers: tuple[str, ...],
) -> FeatureSequence:
    families = parse_feature_families(",".join(feature_families))
    reducers = normalize_reducers(feature_reducers)
    base_signal = _apply_signal_preprocess(
        np.asarray(ecog_uV, dtype=np.float32),
        fs_hz=fs_hz,
        mode=signal_preprocess,
    )
    _, usable_samples = _trim_full_bins(base_signal, bin_samples=bin_samples)
    pieces: list[np.ndarray] = []
    names: list[str] = []
    family_count = len(families)

    for family in families:
        if family == "simple_stats":
            piece = bin_reduce(base_signal, bin_samples=bin_samples, reducers=reducers)
            piece_names = feature_channel_names(channel_names, reducers)
        elif family == "lmp":
            lmp_signal = _bandpass_filter(base_signal, fs_hz=fs_hz, low_hz=0.5, high_hz=4.0)
            piece = _binned_mean(lmp_signal, bin_samples=bin_samples)
            piece_names = [f"{channel}:lmp" for channel in channel_names]
        elif family == "hg_power":
            hg_signal = _bandpass_filter(base_signal, fs_hz=fs_hz, low_hz=70.0, high_hz=150.0)
            piece = _binned_log_power(hg_signal, bin_samples=bin_samples)
            piece_names = [f"{channel}:hg_power" for channel in channel_names]
        elif family == "bandpower_bank":
            bank_outputs: list[np.ndarray] = []
            bank_names: list[str] = []
            for label, low_hz, high_hz in BANDPOWER_BANK:
                band_signal = _bandpass_filter(base_signal, fs_hz=fs_hz, low_hz=low_hz, high_hz=high_hz)
                bank_outputs.append(_binned_log_power(band_signal, bin_samples=bin_samples))
                bank_names.extend(f"{channel}:{label}" for channel in channel_names)
            piece = np.concatenate(bank_outputs, axis=0).astype(np.float32)
            piece_names = bank_names
        elif family == "phase_state":
            phase_signal = _bandpass_filter(base_signal, fs_hz=fs_hz, low_hz=0.5, high_hz=4.0)
            piece = _binned_phase_state_features(phase_signal, bin_samples=bin_samples)
            piece_names = (
                [f"{channel}:phase_cos" for channel in channel_names]
                + [f"{channel}:phase_sin" for channel in channel_names]
            )
        elif family == "dmd_sdm":
            piece = _binned_sdm_mode_features(base_signal, bin_samples=bin_samples, n_modes=2)
            piece_names = (
                [f"{channel}:sdm_mode1" for channel in channel_names]
                + [f"{channel}:sdm_mode2" for channel in channel_names]
            )
        else:
            raise ValueError(f"Unsupported feature family: {family}")

        if family_count > 1:
            piece_names = [f"{family}/{name}" for name in piece_names]
        pieces.append(piece.astype(np.float32))
        names.extend(piece_names)

    values = np.concatenate(pieces, axis=0).astype(np.float32)
    return FeatureSequence(
        values=values,
        feature_names=names,
        bin_samples=bin_samples,
        usable_samples=usable_samples,
        feature_families=families,
        signal_preprocess=normalize_signal_preprocess(signal_preprocess),
    )


def slice_feature_window(
    feature_sequence: FeatureSequence,
    *,
    x_start: int,
    x_end: int,
) -> np.ndarray:
    if x_start < 0 or x_end <= x_start:
        raise ValueError(f"Invalid feature window: start={x_start}, end={x_end}")
    if x_end > feature_sequence.usable_samples:
        raise ValueError(
            f"Feature window exceeds usable samples: end={x_end}, usable={feature_sequence.usable_samples}"
        )
    if x_start % feature_sequence.bin_samples != 0 or x_end % feature_sequence.bin_samples != 0:
        raise ValueError(
            f"Feature window is not aligned to feature bins: "
            f"start={x_start}, end={x_end}, bin={feature_sequence.bin_samples}"
        )
    start_bin = x_start // feature_sequence.bin_samples
    end_bin = x_end // feature_sequence.bin_samples
    return np.asarray(feature_sequence.values[:, start_bin:end_bin], dtype=np.float32)
