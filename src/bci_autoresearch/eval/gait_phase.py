from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import numpy as np


@dataclass(frozen=True)
class ToePhaseLabels:
    signal_name: str
    status: str
    swing_intervals: list[tuple[int, int]]
    exception_counts: dict[str, int]


HYSTERESIS_REFERENCE_METHOD_FAMILY = "hysteresis_threshold"
HYSTERESIS_REFERENCE_METHOD_CONFIG = {
    "high_q": 0.70,
    "low_q": 0.35,
    "smooth_window_ms": 75.0,
    "min_swing_ms": 120.0,
    "min_support_ms": 120.0,
    "merge_gap_ms": 60.0,
    "split_long_swing_cycle_ratio": 1.45,
    "split_peak_min_spacing_ratio": 0.35,
    "split_valley_floor_ratio": 0.18,
}

REFERENCE_QUALITY_LIMITS = {
    "swing_ratio": (0.15, 0.60),
    "median_swing_ms": (100.0, 1500.0),
    "median_cadence_hz": (0.4, 4.0),
    "fore_hind_count_relative_diff": (0.0, 0.35),
    "short_swing_ratio": (0.0, 0.10),
}


def _moving_average(signal: np.ndarray, window: int) -> np.ndarray:
    if window <= 1:
        return signal.astype(np.float32, copy=False)
    kernel = np.ones(window, dtype=np.float32) / float(window)
    return np.convolve(signal.astype(np.float32, copy=False), kernel, mode="same").astype(np.float32)


def _find_local_extrema(signal: np.ndarray, *, kind: str) -> np.ndarray:
    if signal.size < 3:
        return np.empty(0, dtype=np.int64)
    left = signal[:-2]
    center = signal[1:-1]
    right = signal[2:]
    if kind == "max":
        mask = (center >= left) & (center > right)
    elif kind == "min":
        mask = (center <= left) & (center < right)
    else:
        raise ValueError(f"Unsupported extrema kind: {kind}")
    return np.nonzero(mask)[0].astype(np.int64) + 1


def _filter_min_separation(indices: np.ndarray, signal: np.ndarray, *, min_separation_samples: int, prefer: str) -> np.ndarray:
    if indices.size <= 1 or min_separation_samples <= 1:
        return indices.astype(np.int64, copy=False)
    kept: list[int] = []
    for idx in indices.tolist():
        if not kept:
            kept.append(idx)
            continue
        previous = kept[-1]
        if idx - previous >= min_separation_samples:
            kept.append(idx)
            continue
        previous_value = float(signal[previous])
        current_value = float(signal[idx])
        should_replace = current_value > previous_value if prefer == "higher" else current_value < previous_value
        if should_replace:
            kept[-1] = idx
    return np.asarray(kept, dtype=np.int64)


def _build_interval_around_peak(
    signal: np.ndarray,
    *,
    left_min: int,
    peak_idx: int,
    right_min: int,
) -> tuple[int, int] | None:
    peak_value = float(signal[peak_idx])
    baseline = float(max(signal[left_min], signal[right_min]))
    amplitude = peak_value - baseline
    if amplitude <= 1e-6:
        return None
    threshold = baseline + amplitude * 0.35
    window = signal[left_min : right_min + 1]
    above = np.nonzero(window >= threshold)[0]
    if above.size == 0:
        return None
    start_idx = left_min + int(above[0])
    end_idx = left_min + int(above[-1]) + 1
    if end_idx - start_idx < 2:
        return None
    return start_idx, end_idx


def build_extrema_reference_labels(
    *,
    time_s: np.ndarray,
    toe_z: np.ndarray,
    signal_name: str,
    smooth_window: int = 5,
    min_separation_samples: int = 5,
) -> dict[str, Any]:
    time_s = np.asarray(time_s, dtype=np.float64)
    toe_z = np.asarray(toe_z, dtype=np.float32)
    if time_s.ndim != 1 or toe_z.ndim != 1 or time_s.shape[0] != toe_z.shape[0]:
        raise ValueError("time_s and toe_z must be 1D arrays with the same length.")

    smoothed = _moving_average(toe_z, smooth_window)
    maxima = _filter_min_separation(
        _find_local_extrema(smoothed, kind="max"),
        smoothed,
        min_separation_samples=min_separation_samples,
        prefer="higher",
    )
    minima = _filter_min_separation(
        _find_local_extrema(smoothed, kind="min"),
        smoothed,
        min_separation_samples=min_separation_samples,
        prefer="lower",
    )

    exception_counts = {
        "missing_extrema": 0,
        "degenerate_interval": 0,
        "unpaired_peak": 0,
    }

    if maxima.size == 0 or minima.size < 2:
        exception_counts["missing_extrema"] += 1
        return {
            "signal_name": signal_name,
            "status": "needs_review",
            "swing_intervals": [],
            "exception_counts": exception_counts,
        }

    swing_intervals: list[dict[str, int]] = []
    for peak_idx in maxima.tolist():
        left_candidates = minima[minima < peak_idx]
        right_candidates = minima[minima > peak_idx]
        if left_candidates.size == 0 or right_candidates.size == 0:
            exception_counts["unpaired_peak"] += 1
            continue
        interval = _build_interval_around_peak(
            smoothed,
            left_min=int(left_candidates[-1]),
            peak_idx=int(peak_idx),
            right_min=int(right_candidates[0]),
        )
        if interval is None:
            exception_counts["degenerate_interval"] += 1
            continue
        start_idx, end_idx = interval
        if swing_intervals and start_idx <= swing_intervals[-1]["end_idx"]:
            swing_intervals[-1]["end_idx"] = max(swing_intervals[-1]["end_idx"], end_idx)
        else:
            swing_intervals.append({"start_idx": start_idx, "end_idx": end_idx})

    status = "ok" if swing_intervals else "needs_review"
    if not swing_intervals:
        exception_counts["missing_extrema"] += 1
    return {
        "signal_name": signal_name,
        "status": status,
        "swing_intervals": swing_intervals,
        "exception_counts": exception_counts,
    }


def _infer_sample_rate_hz(time_s: np.ndarray) -> float:
    diffs = np.diff(np.asarray(time_s, dtype=np.float64))
    positive = diffs[np.isfinite(diffs) & (diffs > 0)]
    if positive.size == 0:
        raise ValueError("Unable to infer sample rate from time axis.")
    return float(1.0 / np.median(positive))


def _ms_to_samples(duration_ms: float, sample_rate_hz: float, *, minimum: int = 0) -> int:
    samples = int(round(float(duration_ms) * float(sample_rate_hz) / 1000.0))
    return max(int(minimum), samples)


def _interval_dicts_from_mask(mask: np.ndarray) -> list[dict[str, int]]:
    intervals: list[dict[str, int]] = []
    start_idx: int | None = None
    for idx, active in enumerate(mask.tolist()):
        if active and start_idx is None:
            start_idx = idx
        elif not active and start_idx is not None:
            intervals.append({"start_idx": int(start_idx), "end_idx": int(idx)})
            start_idx = None
    if start_idx is not None:
        intervals.append({"start_idx": int(start_idx), "end_idx": int(mask.shape[0])})
    return intervals


def _fill_short_false_runs(mask: np.ndarray, *, max_gap_samples: int) -> tuple[np.ndarray, int]:
    if max_gap_samples <= 0 or mask.size == 0:
        return mask.astype(bool, copy=True), 0
    result = mask.astype(bool, copy=True)
    updates = 0
    idx = 0
    while idx < result.shape[0]:
        if result[idx]:
            idx += 1
            continue
        start = idx
        while idx < result.shape[0] and not result[idx]:
            idx += 1
        end = idx
        gap_len = end - start
        bounded = start > 0 and end < result.shape[0] and bool(result[start - 1]) and bool(result[end])
        if bounded and gap_len <= int(max_gap_samples):
            result[start:end] = True
            updates += 1
    return result, updates


def _drop_short_true_runs(mask: np.ndarray, *, min_len_samples: int) -> tuple[np.ndarray, int]:
    if min_len_samples <= 1 or mask.size == 0:
        return mask.astype(bool, copy=True), 0
    result = mask.astype(bool, copy=True)
    removed = 0
    idx = 0
    while idx < result.shape[0]:
        if not result[idx]:
            idx += 1
            continue
        start = idx
        while idx < result.shape[0] and result[idx]:
            idx += 1
        end = idx
        if end - start < int(min_len_samples):
            result[start:end] = False
            removed += 1
    return result, removed


def _estimate_representative_cycle_samples(
    swing_intervals: list[dict[str, int]],
    maxima_idx: np.ndarray,
    smoothed: np.ndarray,
    *,
    high_threshold: float,
    min_swing_samples: int,
) -> int | None:
    candidates: list[int] = []
    start_indices = np.asarray([int(item["start_idx"]) for item in swing_intervals], dtype=np.int64)
    if start_indices.size >= 2:
        candidates.extend(int(value) for value in np.diff(start_indices).tolist() if int(value) > 0)

    dominant_maxima = maxima_idx[np.asarray(smoothed[maxima_idx] >= high_threshold, dtype=bool)]
    if dominant_maxima.size >= 2:
        filtered_maxima = _filter_min_separation(
            dominant_maxima,
            smoothed,
            min_separation_samples=max(1, min_swing_samples // 2),
            prefer="higher",
        )
        candidates.extend(int(value) for value in np.diff(filtered_maxima).tolist() if int(value) > 0)

    if not candidates:
        return None
    return int(round(float(np.median(np.asarray(candidates, dtype=np.float64)))))


def _estimate_support_floor_value(smoothed: np.ndarray, *, low_threshold: float) -> float:
    support_samples = np.asarray(smoothed[np.isfinite(smoothed) & (smoothed <= low_threshold)], dtype=np.float32)
    if support_samples.size >= 8:
        return float(np.median(support_samples))
    return float(low_threshold)


def _split_overlong_intervals(
    swing_intervals: list[dict[str, int]],
    *,
    smoothed: np.ndarray,
    maxima_idx: np.ndarray,
    minima_idx: np.ndarray,
    high_threshold: float,
    low_threshold: float,
    support_floor_value: float,
    representative_cycle_samples: int | None,
    min_swing_samples: int,
    split_long_swing_cycle_ratio: float,
    split_peak_min_spacing_ratio: float,
    split_valley_floor_ratio: float,
) -> tuple[list[dict[str, int]], int]:
    if representative_cycle_samples is None or representative_cycle_samples <= 1:
        return list(swing_intervals), 0

    long_interval_threshold = max(
        int(round(float(representative_cycle_samples) * float(split_long_swing_cycle_ratio))),
        int(min_swing_samples * 2),
    )
    peak_spacing_threshold = max(
        int(round(float(representative_cycle_samples) * float(split_peak_min_spacing_ratio))),
        int(max(1, min_swing_samples // 2)),
    )

    dominant_maxima = maxima_idx[np.asarray(smoothed[maxima_idx] >= high_threshold, dtype=bool)]
    if dominant_maxima.size == 0:
        return list(swing_intervals), 0
    dominant_maxima = _filter_min_separation(
        dominant_maxima,
        smoothed,
        min_separation_samples=peak_spacing_threshold,
        prefer="higher",
    )

    result: list[dict[str, int]] = []
    split_count = 0
    for interval in swing_intervals:
        start_idx = int(interval["start_idx"])
        end_idx = int(interval["end_idx"])
        interval_len = end_idx - start_idx
        if interval_len < long_interval_threshold:
            result.append({"start_idx": start_idx, "end_idx": end_idx})
            continue

        peaks = [int(idx) for idx in dominant_maxima.tolist() if start_idx < int(idx) < end_idx]
        if len(peaks) < 2:
            result.append({"start_idx": start_idx, "end_idx": end_idx})
            continue

        split_points: list[int] = []
        previous_boundary = start_idx
        for left_peak_idx, right_peak_idx in zip(peaks[:-1], peaks[1:]):
            if right_peak_idx - left_peak_idx < peak_spacing_threshold:
                continue
            valley_candidates = [int(idx) for idx in minima_idx.tolist() if left_peak_idx < int(idx) < right_peak_idx]
            if not valley_candidates:
                continue
            valley_idx = min(valley_candidates, key=lambda idx: float(smoothed[idx]))
            valley_value = float(smoothed[valley_idx])
            smaller_peak_value = min(float(smoothed[left_peak_idx]), float(smoothed[right_peak_idx]))
            if smaller_peak_value <= support_floor_value + 1e-6:
                continue
            valley_floor_ratio = (valley_value - support_floor_value) / (smaller_peak_value - support_floor_value + 1e-6)
            if valley_floor_ratio > float(split_valley_floor_ratio):
                continue
            if valley_idx - previous_boundary < min_swing_samples:
                continue
            if end_idx - valley_idx < min_swing_samples:
                continue
            split_points.append(int(valley_idx))
            previous_boundary = int(valley_idx)

        if not split_points:
            result.append({"start_idx": start_idx, "end_idx": end_idx})
            continue

        boundaries = [start_idx, *split_points, end_idx]
        emitted = 0
        for left_idx, right_idx in zip(boundaries[:-1], boundaries[1:]):
            if int(right_idx) - int(left_idx) < min_swing_samples:
                continue
            result.append({"start_idx": int(left_idx), "end_idx": int(right_idx)})
            emitted += 1

        if emitted >= 2:
            split_count += emitted - 1
        else:
            result.append({"start_idx": start_idx, "end_idx": end_idx})

    result.sort(key=lambda item: int(item["start_idx"]))
    return result, split_count


def compute_hysteresis_reference_trace(
    *,
    time_s: np.ndarray,
    toe_z: np.ndarray,
    signal_name: str,
    high_q: float = float(HYSTERESIS_REFERENCE_METHOD_CONFIG["high_q"]),
    low_q: float = float(HYSTERESIS_REFERENCE_METHOD_CONFIG["low_q"]),
    smooth_window_ms: float = float(HYSTERESIS_REFERENCE_METHOD_CONFIG["smooth_window_ms"]),
    min_swing_ms: float = float(HYSTERESIS_REFERENCE_METHOD_CONFIG["min_swing_ms"]),
    min_support_ms: float = float(HYSTERESIS_REFERENCE_METHOD_CONFIG["min_support_ms"]),
    merge_gap_ms: float = float(HYSTERESIS_REFERENCE_METHOD_CONFIG["merge_gap_ms"]),
    split_long_swing_cycle_ratio: float = float(HYSTERESIS_REFERENCE_METHOD_CONFIG["split_long_swing_cycle_ratio"]),
    split_peak_min_spacing_ratio: float = float(HYSTERESIS_REFERENCE_METHOD_CONFIG["split_peak_min_spacing_ratio"]),
    split_valley_floor_ratio: float = float(HYSTERESIS_REFERENCE_METHOD_CONFIG["split_valley_floor_ratio"]),
) -> dict[str, Any]:
    time_s = np.asarray(time_s, dtype=np.float64)
    toe_z = np.asarray(toe_z, dtype=np.float32)
    if time_s.ndim != 1 or toe_z.ndim != 1 or time_s.shape[0] != toe_z.shape[0]:
        raise ValueError("time_s and toe_z must be 1D arrays with the same length.")

    sample_rate_hz = _infer_sample_rate_hz(time_s)
    smooth_window_samples = _ms_to_samples(smooth_window_ms, sample_rate_hz, minimum=1)
    if smooth_window_samples % 2 == 0:
        smooth_window_samples += 1

    smoothed = _moving_average(toe_z, smooth_window_samples)
    high_threshold = float(np.quantile(smoothed, float(high_q)))
    low_threshold = float(np.quantile(smoothed, float(low_q)))

    exception_counts: dict[str, int] = {
        "degenerate_threshold": 0,
        "merged_short_gap": 0,
        "removed_short_swing": 0,
        "merged_short_support": 0,
        "empty_after_filter": 0,
        "split_overlong_interval": 0,
    }
    if not np.isfinite(high_threshold) or not np.isfinite(low_threshold) or high_threshold <= low_threshold:
        exception_counts["degenerate_threshold"] += 1

    active = False
    swing_mask = np.zeros(smoothed.shape[0], dtype=bool)
    for idx, value in enumerate(smoothed.tolist()):
        if not active and float(value) >= high_threshold:
            active = True
        elif active and float(value) <= low_threshold:
            active = False
        swing_mask[idx] = active

    merged_gap_mask, merged_gap_count = _fill_short_false_runs(
        swing_mask,
        max_gap_samples=_ms_to_samples(merge_gap_ms, sample_rate_hz, minimum=0),
    )
    exception_counts["merged_short_gap"] = int(merged_gap_count)

    filtered_mask, removed_short_swing = _drop_short_true_runs(
        merged_gap_mask,
        min_len_samples=_ms_to_samples(min_swing_ms, sample_rate_hz, minimum=1),
    )
    exception_counts["removed_short_swing"] = int(removed_short_swing)

    support_merged_mask, merged_short_support = _fill_short_false_runs(
        filtered_mask,
        max_gap_samples=max(0, _ms_to_samples(min_support_ms, sample_rate_hz, minimum=1) - 1),
    )
    exception_counts["merged_short_support"] = int(merged_short_support)

    final_mask, removed_after_support_merge = _drop_short_true_runs(
        support_merged_mask,
        min_len_samples=_ms_to_samples(min_swing_ms, sample_rate_hz, minimum=1),
    )
    exception_counts["removed_short_swing"] += int(removed_after_support_merge)

    raw_swing_intervals = _interval_dicts_from_mask(final_mask)
    representative_cycle_samples = _estimate_representative_cycle_samples(
        raw_swing_intervals,
        _find_local_extrema(smoothed, kind="max"),
        smoothed,
        high_threshold=high_threshold,
        min_swing_samples=_ms_to_samples(min_swing_ms, sample_rate_hz, minimum=1),
    )
    support_floor_value = _estimate_support_floor_value(smoothed, low_threshold=low_threshold)
    swing_intervals, split_count = _split_overlong_intervals(
        raw_swing_intervals,
        smoothed=smoothed,
        maxima_idx=_find_local_extrema(smoothed, kind="max"),
        minima_idx=_find_local_extrema(smoothed, kind="min"),
        high_threshold=high_threshold,
        low_threshold=low_threshold,
        support_floor_value=support_floor_value,
        representative_cycle_samples=representative_cycle_samples,
        min_swing_samples=_ms_to_samples(min_swing_ms, sample_rate_hz, minimum=1),
        split_long_swing_cycle_ratio=split_long_swing_cycle_ratio,
        split_peak_min_spacing_ratio=split_peak_min_spacing_ratio,
        split_valley_floor_ratio=split_valley_floor_ratio,
    )
    exception_counts["split_overlong_interval"] = int(split_count)
    if not swing_intervals:
        exception_counts["empty_after_filter"] = 1

    local_maxima_idx = _find_local_extrema(smoothed, kind="max")
    local_minima_idx = _find_local_extrema(smoothed, kind="min")

    return {
        "signal_name": signal_name,
        "status": "ok" if swing_intervals else "needs_review",
        "swing_intervals": swing_intervals,
        "exception_counts": exception_counts,
        "sample_rate_hz": sample_rate_hz,
        "raw_signal": toe_z,
        "smoothed_signal": smoothed,
        "high_threshold": high_threshold,
        "low_threshold": low_threshold,
        "local_maxima_idx": local_maxima_idx,
        "local_minima_idx": local_minima_idx,
        "support_floor_value": support_floor_value,
        "representative_cycle_samples": representative_cycle_samples,
        "method_family": HYSTERESIS_REFERENCE_METHOD_FAMILY,
        "method_config": {
            "high_q": float(high_q),
            "low_q": float(low_q),
            "smooth_window_ms": float(smooth_window_ms),
            "min_swing_ms": float(min_swing_ms),
            "min_support_ms": float(min_support_ms),
            "merge_gap_ms": float(merge_gap_ms),
            "split_long_swing_cycle_ratio": float(split_long_swing_cycle_ratio),
            "split_peak_min_spacing_ratio": float(split_peak_min_spacing_ratio),
            "split_valley_floor_ratio": float(split_valley_floor_ratio),
        },
    }


def build_hysteresis_reference_labels(
    *,
    time_s: np.ndarray,
    toe_z: np.ndarray,
    signal_name: str,
    high_q: float = float(HYSTERESIS_REFERENCE_METHOD_CONFIG["high_q"]),
    low_q: float = float(HYSTERESIS_REFERENCE_METHOD_CONFIG["low_q"]),
    smooth_window_ms: float = float(HYSTERESIS_REFERENCE_METHOD_CONFIG["smooth_window_ms"]),
    min_swing_ms: float = float(HYSTERESIS_REFERENCE_METHOD_CONFIG["min_swing_ms"]),
    min_support_ms: float = float(HYSTERESIS_REFERENCE_METHOD_CONFIG["min_support_ms"]),
    merge_gap_ms: float = float(HYSTERESIS_REFERENCE_METHOD_CONFIG["merge_gap_ms"]),
    split_long_swing_cycle_ratio: float = float(HYSTERESIS_REFERENCE_METHOD_CONFIG["split_long_swing_cycle_ratio"]),
    split_peak_min_spacing_ratio: float = float(HYSTERESIS_REFERENCE_METHOD_CONFIG["split_peak_min_spacing_ratio"]),
    split_valley_floor_ratio: float = float(HYSTERESIS_REFERENCE_METHOD_CONFIG["split_valley_floor_ratio"]),
) -> dict[str, Any]:
    trace = compute_hysteresis_reference_trace(
        time_s=time_s,
        toe_z=toe_z,
        signal_name=signal_name,
        high_q=high_q,
        low_q=low_q,
        smooth_window_ms=smooth_window_ms,
        min_swing_ms=min_swing_ms,
        min_support_ms=min_support_ms,
        merge_gap_ms=merge_gap_ms,
        split_long_swing_cycle_ratio=split_long_swing_cycle_ratio,
        split_peak_min_spacing_ratio=split_peak_min_spacing_ratio,
        split_valley_floor_ratio=split_valley_floor_ratio,
    )
    return {
        "signal_name": signal_name,
        "status": trace["status"],
        "swing_intervals": list(trace["swing_intervals"]),
        "exception_counts": dict(trace["exception_counts"]),
        "method_family": HYSTERESIS_REFERENCE_METHOD_FAMILY,
        "method_config": dict(trace["method_config"]),
    }


def _normalize_intervals(raw: list[dict[str, Any]] | list[tuple[int, int]] | None) -> list[tuple[int, int]]:
    intervals: list[tuple[int, int]] = []
    for item in raw or []:
        if isinstance(item, dict):
            start_idx = int(item["start_idx"])
            end_idx = int(item["end_idx"])
        else:
            start_idx, end_idx = int(item[0]), int(item[1])
        if end_idx <= start_idx:
            continue
        intervals.append((start_idx, end_idx))
    intervals.sort()
    return intervals


def summarize_reference_label_quality(rows: list[dict[str, Any]]) -> dict[str, Any]:
    signal_metrics: dict[str, dict[str, Any]] = {}
    per_session_count_diffs: list[float] = []
    quality_violations: list[dict[str, Any]] = []

    for signal_name in ("RHTOE_z", "RFTOE_z"):
        total_samples = 0
        total_swing_samples = 0
        swing_durations_ms: list[float] = []
        per_session_cadence_hz: list[float] = []
        interval_count = 0
        short_swing_count = 0

        for row in rows:
            n_samples = int(row.get("n_samples") or 0)
            sample_rate_hz = float(row.get("sample_rate_hz") or 0.0)
            intervals = _normalize_intervals(((row.get("toe_labels") or {}).get(signal_name) or {}).get("swing_intervals"))
            total_samples += n_samples
            total_swing_samples += sum(end_idx - start_idx for start_idx, end_idx in intervals)
            interval_count += len(intervals)

            if sample_rate_hz > 0:
                swing_durations_ms.extend(
                    (float(end_idx - start_idx) * 1000.0 / sample_rate_hz)
                    for start_idx, end_idx in intervals
                )
                short_swing_count += sum(
                    1
                    for start_idx, end_idx in intervals
                    if ((float(end_idx - start_idx) * 1000.0 / sample_rate_hz) < 80.0)
                )
                if len(intervals) >= 2:
                    starts = np.asarray([start_idx for start_idx, _ in intervals], dtype=np.float64)
                    step_period_s = np.diff(starts) / sample_rate_hz
                    valid = step_period_s[np.isfinite(step_period_s) & (step_period_s > 0)]
                    if valid.size > 0:
                        per_session_cadence_hz.append(float(1.0 / np.median(valid)))

        swing_ratio = float(total_swing_samples / total_samples) if total_samples > 0 else 0.0
        median_swing_ms = float(np.median(np.asarray(swing_durations_ms, dtype=np.float64))) if swing_durations_ms else 0.0
        median_cadence_hz = float(np.median(np.asarray(per_session_cadence_hz, dtype=np.float64))) if per_session_cadence_hz else 0.0
        short_swing_ratio = float(short_swing_count / interval_count) if interval_count > 0 else 0.0

        signal_metrics[signal_name] = {
            "swing_ratio": swing_ratio,
            "median_swing_ms": median_swing_ms,
            "median_cadence_hz": median_cadence_hz,
            "short_swing_ratio": short_swing_ratio,
            "swing_interval_count": int(interval_count),
            "total_samples": int(total_samples),
        }

        lower, upper = REFERENCE_QUALITY_LIMITS["swing_ratio"]
        if not (lower <= swing_ratio <= upper):
            quality_violations.append(
                {
                    "code": "swing_ratio_out_of_range",
                    "signal_name": signal_name,
                    "value": swing_ratio,
                    "expected_range": [lower, upper],
                    "message": f"{signal_name} 的摆动占比超出允许范围。",
                }
            )
        lower, upper = REFERENCE_QUALITY_LIMITS["median_swing_ms"]
        if not (lower <= median_swing_ms <= upper):
            quality_violations.append(
                {
                    "code": "median_swing_ms_out_of_range",
                    "signal_name": signal_name,
                    "value": median_swing_ms,
                    "expected_range": [lower, upper],
                    "message": f"{signal_name} 的中位摆动时长不合理。",
                }
            )
        lower, upper = REFERENCE_QUALITY_LIMITS["median_cadence_hz"]
        if not (lower <= median_cadence_hz <= upper):
            quality_violations.append(
                {
                    "code": "median_cadence_hz_out_of_range",
                    "signal_name": signal_name,
                    "value": median_cadence_hz,
                    "expected_range": [lower, upper],
                    "message": f"{signal_name} 的中位步频超出允许范围。",
                }
            )
        lower, upper = REFERENCE_QUALITY_LIMITS["short_swing_ratio"]
        if not (lower <= short_swing_ratio <= upper):
            quality_violations.append(
                {
                    "code": "short_swing_ratio_out_of_range",
                    "signal_name": signal_name,
                    "value": short_swing_ratio,
                    "expected_range": [lower, upper],
                    "message": f"{signal_name} 的短摆动比例过高。",
                }
            )

    for row in rows:
        rh_intervals = _normalize_intervals(((row.get("toe_labels") or {}).get("RHTOE_z") or {}).get("swing_intervals"))
        rf_intervals = _normalize_intervals(((row.get("toe_labels") or {}).get("RFTOE_z") or {}).get("swing_intervals"))
        denom = max(len(rh_intervals), len(rf_intervals), 1)
        per_session_count_diffs.append(abs(len(rh_intervals) - len(rf_intervals)) / float(denom))

    fore_hind_count_relative_diff = float(max(per_session_count_diffs)) if per_session_count_diffs else 0.0
    _, max_allowed = REFERENCE_QUALITY_LIMITS["fore_hind_count_relative_diff"]
    if fore_hind_count_relative_diff > max_allowed:
        quality_violations.append(
            {
                "code": "fore_hind_count_relative_diff_out_of_range",
                "signal_name": "RHTOE_z/RFTOE_z",
                "value": fore_hind_count_relative_diff,
                "expected_range": [0.0, max_allowed],
                "message": "前后肢步数差异过大，说明切分一致性不足。",
            }
        )

    return {
        "quality_status": "passed" if not quality_violations else "failed",
        "quality_violations": quality_violations,
        "signal_metrics": signal_metrics,
        "fore_hind_count_relative_diff": fore_hind_count_relative_diff,
        "session_count": len(rows),
    }


def _intervals_to_mask(intervals: list[tuple[int, int]], *, n_samples: int) -> np.ndarray:
    mask = np.zeros(int(n_samples), dtype=bool)
    for start_idx, end_idx in intervals:
        start = max(0, int(start_idx))
        end = min(int(n_samples), int(end_idx))
        if end > start:
            mask[start:end] = True
    return mask


def _shift_intervals(intervals: list[tuple[int, int]], *, n_samples: int, global_lag_samples: int) -> list[tuple[int, int]]:
    shifted: list[tuple[int, int]] = []
    for start_idx, end_idx in intervals:
        start = start_idx - global_lag_samples
        end = end_idx - global_lag_samples
        if end <= 0 or start >= n_samples:
            continue
        shifted.append((max(0, start), min(n_samples, end)))
    return shifted


def _event_error_ms(
    reference_intervals: list[tuple[int, int]],
    predicted_intervals: list[tuple[int, int]],
    *,
    sample_rate_hz: float,
) -> float | None:
    if not reference_intervals or not predicted_intervals:
        return None
    errors_ms: list[float] = []
    for ref, pred in zip(reference_intervals, predicted_intervals):
        errors_ms.append(abs(ref[0] - pred[0]) * 1000.0 / sample_rate_hz)
        errors_ms.append(abs(ref[1] - pred[1]) * 1000.0 / sample_rate_hz)
    if not errors_ms:
        return None
    return float(np.mean(np.asarray(errors_ms, dtype=np.float64)))


def score_trial_prediction(
    reference_record: dict[str, Any],
    prediction_record: dict[str, Any],
    *,
    global_lag_samples: int = 0,
    usability_iou_threshold: float = 0.5,
) -> dict[str, Any]:
    n_samples = int(reference_record["n_samples"])
    sample_rate_hz = float(reference_record["sample_rate_hz"])
    reference_toes = reference_record["toe_labels"]
    prediction_toes = prediction_record["toe_labels"]

    toe_scores: dict[str, Any] = {}
    usable_flags: list[bool] = []
    iou_values: list[float] = []
    event_errors: list[float] = []
    exception_counts: dict[str, int] = {}

    for signal_name, reference_toe in reference_toes.items():
        predicted_toe = prediction_toes.get(signal_name, {"status": "missing", "swing_intervals": [], "exception_counts": {"missing_prediction": 1}})
        reference_intervals = _normalize_intervals(reference_toe.get("swing_intervals"))
        predicted_intervals = _shift_intervals(
            _normalize_intervals(predicted_toe.get("swing_intervals")),
            n_samples=n_samples,
            global_lag_samples=int(global_lag_samples),
        )
        reference_mask = _intervals_to_mask(reference_intervals, n_samples=n_samples)
        predicted_mask = _intervals_to_mask(predicted_intervals, n_samples=n_samples)
        union = int(np.logical_or(reference_mask, predicted_mask).sum())
        intersection = int(np.logical_and(reference_mask, predicted_mask).sum())
        phase_iou = float(intersection / union) if union > 0 else 0.0
        usable = (
            str(reference_toe.get("status", "ok")) == "ok"
            and str(predicted_toe.get("status", "ok")) == "ok"
            and bool(reference_intervals)
            and bool(predicted_intervals)
            and phase_iou >= usability_iou_threshold
        )
        event_error = _event_error_ms(
            reference_intervals,
            predicted_intervals,
            sample_rate_hz=sample_rate_hz,
        )
        toe_scores[signal_name] = {
            "usable": usable,
            "phase_iou": phase_iou,
            "event_error_ms": event_error,
            "reference_interval_count": len(reference_intervals),
            "predicted_interval_count": len(predicted_intervals),
        }
        usable_flags.append(usable)
        iou_values.append(phase_iou)
        if event_error is not None:
            event_errors.append(event_error)
        for source in (reference_toe.get("exception_counts"), predicted_toe.get("exception_counts")):
            if not isinstance(source, dict):
                continue
            for key, value in source.items():
                exception_counts[str(key)] = exception_counts.get(str(key), 0) + int(value or 0)

    return {
        "session_id": reference_record["session_id"],
        "trial_usable": bool(usable_flags) and all(usable_flags),
        "phase_iou_mean": float(np.mean(np.asarray(iou_values, dtype=np.float64))) if iou_values else 0.0,
        "event_error_ms_mean": float(np.mean(np.asarray(event_errors, dtype=np.float64))) if event_errors else None,
        "toe_scores": toe_scores,
        "exception_counts": exception_counts,
    }


def aggregate_phase_scores(
    session_scores: list[dict[str, Any]],
    *,
    dataset_name: str,
    split_name: str,
    global_lag_samples: int,
    sample_rate_hz: float,
) -> dict[str, Any]:
    total_trials = len(session_scores)
    usable_trials = sum(1 for score in session_scores if bool(score.get("trial_usable")))
    trial_usability_rate = float(usable_trials / total_trials) if total_trials > 0 else 0.0
    phase_iou_values = [float(score["phase_iou_mean"]) for score in session_scores if score.get("phase_iou_mean") is not None]
    event_error_values = [float(score["event_error_ms_mean"]) for score in session_scores if score.get("event_error_ms_mean") is not None]
    aggregated_exceptions: dict[str, int] = {}
    for score in session_scores:
        for key, value in dict(score.get("exception_counts") or {}).items():
            aggregated_exceptions[str(key)] = aggregated_exceptions.get(str(key), 0) + int(value or 0)

    return {
        "dataset_name": dataset_name,
        "target_mode": "gait_phase",
        "target_space": "support_swing_phase",
        "primary_metric": "trial_usability_rate",
        f"{split_name}_primary_metric": trial_usability_rate,
        "benchmark_primary_score": trial_usability_rate,
        "val_r": trial_usability_rate,
        "trial_usability_rate": trial_usability_rate,
        "event_error_ms": float(np.mean(np.asarray(event_error_values, dtype=np.float64))) if event_error_values else None,
        "phase_iou": float(np.mean(np.asarray(phase_iou_values, dtype=np.float64))) if phase_iou_values else 0.0,
        "lag_distribution": {
            "global_lag_samples": int(global_lag_samples),
            "global_lag_ms": float(global_lag_samples) * 1000.0 / float(sample_rate_hz),
        },
        "exception_counts": aggregated_exceptions,
        "trial_count": total_trials,
        "usable_trial_count": usable_trials,
    }


def classify_trial_label_status(record: dict[str, Any]) -> str:
    toe_labels = dict(record.get("toe_labels") or {})
    if not toe_labels:
        return "failed"

    toe_states: list[str] = []
    for toe in toe_labels.values():
        status = str((toe or {}).get("status") or "needs_review")
        intervals = _normalize_intervals((toe or {}).get("swing_intervals"))
        if status == "ok" and intervals:
            toe_states.append("ok")
        else:
            toe_states.append("failed" if not intervals else "needs_review")

    if toe_states and all(state == "ok" for state in toe_states):
        return "ok"
    if toe_states and all(state == "failed" for state in toe_states):
        return "failed"
    return "needs_review"


def summarize_label_records(rows: list[dict[str, Any]]) -> dict[str, Any]:
    split_names = ("train", "val", "test")
    overall_counts = {"ok": 0, "needs_review": 0, "failed": 0}
    overall_exceptions: dict[str, int] = {}
    split_rows: dict[str, list[dict[str, Any]]] = {split: [] for split in split_names}

    for row in rows:
        split_name = str(row.get("split") or "unknown")
        if split_name in split_rows:
            split_rows[split_name].append(row)
        status = classify_trial_label_status(row)
        overall_counts[status] = overall_counts.get(status, 0) + 1
        for toe in dict(row.get("toe_labels") or {}).values():
            for key, value in dict((toe or {}).get("exception_counts") or {}).items():
                overall_exceptions[str(key)] = overall_exceptions.get(str(key), 0) + int(value or 0)

    def build_coverage(counts: dict[str, int], total: int) -> dict[str, dict[str, float | int]]:
        return {
            key: {
                "count": int(counts.get(key, 0)),
                "rate": float(counts.get(key, 0) / total) if total > 0 else 0.0,
            }
            for key in ("ok", "needs_review", "failed")
        }

    def summarize_split(split_name: str, split_rows_local: list[dict[str, Any]]) -> dict[str, Any]:
        counts = {"ok": 0, "needs_review": 0, "failed": 0}
        exceptions: dict[str, int] = {}
        for row in split_rows_local:
            status = classify_trial_label_status(row)
            counts[status] = counts.get(status, 0) + 1
            for toe in dict(row.get("toe_labels") or {}).values():
                for key, value in dict((toe or {}).get("exception_counts") or {}).items():
                    exceptions[str(key)] = exceptions.get(str(key), 0) + int(value or 0)
        total = len(split_rows_local)
        ok_count = counts.get("ok", 0)
        return {
            "split": split_name,
            "trial_count": total,
            "usable_trial_count": ok_count,
            "reference_trial_usability_rate": float(ok_count / total) if total > 0 else 0.0,
            "coverage_breakdown": build_coverage(counts, total),
            "exception_counts": exceptions,
        }

    split_metrics = {
        split_name: summarize_split(split_name, split_rows[split_name])
        for split_name in split_names
    }
    total_trials = len(rows)
    usable_trials = overall_counts.get("ok", 0)
    val_primary_metric = float(split_metrics["val"]["reference_trial_usability_rate"])
    test_primary_metric = float(split_metrics["test"]["reference_trial_usability_rate"])
    return {
        "primary_metric": "reference_trial_usability_rate",
        "reference_trial_usability_rate": float(usable_trials / total_trials) if total_trials > 0 else 0.0,
        "benchmark_primary_score": val_primary_metric,
        "val_primary_metric": val_primary_metric,
        "test_primary_metric": test_primary_metric,
        "trial_count": total_trials,
        "usable_trial_count": usable_trials,
        "coverage_breakdown": build_coverage(overall_counts, total_trials),
        "exception_counts": overall_exceptions,
        "split_metrics": split_metrics,
    }
