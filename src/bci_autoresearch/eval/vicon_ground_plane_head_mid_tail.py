from __future__ import annotations

from statistics import median
from typing import Final


WINDOW_LABELS: Final[tuple[str, str, str]] = ("开头 10 秒", "中间 10 秒", "结尾 10 秒")


def build_head_mid_tail_windows(duration_s: float, window_s: float = 10.0) -> list[tuple[str, float, float]]:
    duration_s = max(0.0, float(duration_s))
    window_s = max(0.0, float(window_s))

    if duration_s <= 0.0 or window_s <= 0.0:
        return [(label, 0.0, 0.0) for label in WINDOW_LABELS]

    max_start = max(0.0, duration_s - window_s)
    starts = (
        0.0,
        max_start / 2.0,
        max_start,
    )

    windows: list[tuple[str, float, float]] = []
    for label, start_s in zip(WINDOW_LABELS, starts, strict=True):
        end_s = min(duration_s, start_s + window_s)
        windows.append((label, round(start_s, 3), round(end_s, 3)))
    return windows


def summarize_ground_visibility(
    median_abs_toe_slope_mm_per_s: float | None,
    complete_file_ratio: float,
) -> str:
    ratio = float(complete_file_ratio)
    if ratio < 0.25:
        return "marker 缺失较多，谨慎判断"

    if median_abs_toe_slope_mm_per_s is None:
        return "示例段不足，谨慎判断"

    slope = abs(float(median_abs_toe_slope_mm_per_s))
    if slope <= 0.04:
        return "较容易看出平面"
    if slope <= 0.08:
        return "部分区段可看出平面"
    return "平面不明显"


def summarize_sampled_swing_ratios(window_rows: list[dict[str, float]]) -> dict[str, float | None]:
    rh_values = [float(row["rh_swing_ratio"]) for row in window_rows if row.get("rh_swing_ratio") is not None]
    rf_values = [float(row["rf_swing_ratio"]) for row in window_rows if row.get("rf_swing_ratio") is not None]
    return {
        "rh_swing_ratio_median": round(float(median(rh_values)), 4) if rh_values else None,
        "rf_swing_ratio_median": round(float(median(rf_values)), 4) if rf_values else None,
    }
