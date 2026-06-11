from __future__ import annotations

import math
import re
from pathlib import Path
from statistics import median
from typing import Any


DATE_DIR_RE = re.compile(r"^\d{8}$")


def discover_motion_days(base_dir: Path) -> list[str]:
    return sorted(
        child.name
        for child in base_dir.iterdir()
        if child.is_dir() and DATE_DIR_RE.fullmatch(child.name)
    )


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return float(default)
    if not math.isfinite(number):
        return float(default)
    return number


def select_representative_file_rows(rows: list[dict[str, Any]], *, top_k: int = 3) -> list[dict[str, Any]]:
    ranked = sorted(
        rows,
        key=lambda row: (
            -int(row.get("complete_markers") or 0),
            int(row.get("incomplete_markers") or 0),
            -_safe_float(row.get("complete_prefix_s")),
            -_safe_float(row.get("duration_s")),
            str(row.get("file") or ""),
        ),
    )
    return ranked[: max(0, int(top_k))]


def choose_window_start_s(*, duration_s: float, complete_prefix_s: float | None, window_s: float = 10.0) -> float:
    duration_s = max(0.0, _safe_float(duration_s))
    window_s = max(0.0, _safe_float(window_s))
    prefix_s = _safe_float(complete_prefix_s, default=float("nan"))

    usable_end_s = duration_s
    if math.isfinite(prefix_s) and prefix_s >= window_s:
        usable_end_s = min(duration_s, prefix_s)

    max_start_s = max(0.0, usable_end_s - window_s)
    if max_start_s <= 0.0:
        return 0.0
    return round(max_start_s / 2.0, 3)


def build_day_statistics_rows(
    summary_rows: list[dict[str, Any]],
    segment_rows: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    slopes_by_day: dict[str, list[float]] = {}
    for row in segment_rows:
        day = str(row.get("date") or "")
        if not day:
            continue
        value = row.get("abs_toe_slope_mm_per_s")
        if value is None:
            continue
        slope = _safe_float(value, default=float("nan"))
        if math.isfinite(slope):
            slopes_by_day.setdefault(day, []).append(slope)

    table_rows: list[dict[str, Any]] = []
    for row in summary_rows:
        day = str(row.get("date") or "")
        files = int(row.get("files") or 0)
        complete_files = int(row.get("all_12_complete_files") or 0)
        partial_files = int(row.get("partial_files") or 0)
        earliest_dropout_s = row.get("earliest_dropout_s")
        earliest_dropout_value = _safe_float(earliest_dropout_s, default=float("nan"))
        table_rows.append(
            {
                "date": day,
                "files": files,
                "all_12_complete_files": complete_files,
                "partial_files": partial_files,
                "complete_file_ratio": round((complete_files / files), 4) if files else 0.0,
                "duration_hms_sum": str(row.get("duration_hms_sum") or ""),
                "earliest_dropout_s": round(earliest_dropout_value, 2) if math.isfinite(earliest_dropout_value) else None,
                "median_abs_toe_slope_mm_per_s": round(median(slopes_by_day.get(day, [])), 4)
                if slopes_by_day.get(day)
                else None,
            }
        )
    return table_rows
