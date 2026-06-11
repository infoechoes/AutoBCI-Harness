from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
from typing import Any

import numpy as np


PHASE_LABEL_TO_INT = {
    "support": 0,
    "swing": 1,
}

INT_TO_PHASE_LABEL = {
    0: "support",
    1: "swing",
}


@dataclass(frozen=True)
class PhaseAnchorRecord:
    session_id: str
    split: str
    signal_name: str
    phase_label: str
    anchor_idx: int
    interval_start_idx: int
    interval_end_idx: int
    sample_rate_hz: float
    exception_label: str = ""

    @property
    def anchor_time_s(self) -> float:
        return float(self.anchor_idx) / float(self.sample_rate_hz)

    @property
    def class_index(self) -> int:
        return PHASE_LABEL_TO_INT[self.phase_label]


def load_reference_label_records(path: str | Path) -> dict[str, dict[str, Any]]:
    records: dict[str, dict[str, Any]] = {}
    with Path(path).open("r", encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            payload = json.loads(line)
            session_id = str(payload.get("session_id") or "").strip()
            if not session_id:
                raise ValueError("Reference label row is missing session_id.")
            records[session_id] = payload
    return records


def _normalize_intervals(raw: list[dict[str, Any]] | None) -> list[tuple[int, int]]:
    intervals: list[tuple[int, int]] = []
    for item in raw or []:
        start_idx = int(item["start_idx"])
        end_idx = int(item["end_idx"])
        if end_idx <= start_idx:
            continue
        intervals.append((start_idx, end_idx))
    intervals.sort()
    return intervals


def _midpoint(start_idx: int, end_idx: int) -> int:
    return int((int(start_idx) + int(end_idx)) // 2)


def _merge_close_intervals(
    intervals: list[tuple[int, int]],
    *,
    max_gap_samples: int,
) -> list[tuple[int, int]]:
    if not intervals:
        return []
    merged: list[list[int]] = [[int(intervals[0][0]), int(intervals[0][1])]]
    for start_idx, end_idx in intervals[1:]:
        gap = int(start_idx) - int(merged[-1][1])
        if gap < int(max_gap_samples):
            merged[-1][1] = max(int(merged[-1][1]), int(end_idx))
            continue
        merged.append([int(start_idx), int(end_idx)])
    return [(int(start_idx), int(end_idx)) for start_idx, end_idx in merged]


def collect_phase_anchor_records(
    reference_record: dict[str, Any],
    *,
    signal_names: tuple[str, ...] = ("RHTOE_z", "RFTOE_z"),
    min_support_samples: int = 24,
    min_phase_samples: int = 4,
) -> list[PhaseAnchorRecord]:
    session_id = str(reference_record.get("session_id") or "").strip()
    split = str(reference_record.get("split") or "").strip()
    sample_rate_hz = float(reference_record.get("sample_rate_hz") or 0.0)
    n_samples = int(reference_record.get("n_samples") or 0)
    toe_labels = reference_record.get("toe_labels") or {}
    anchors: list[PhaseAnchorRecord] = []

    for signal_name in signal_names:
        toe_payload = toe_labels.get(signal_name)
        if not isinstance(toe_payload, dict) or str(toe_payload.get("status") or "").strip() != "ok":
            continue
        swing_intervals = _merge_close_intervals(
            _normalize_intervals(toe_payload.get("swing_intervals")),
            max_gap_samples=max(1, int(min_support_samples)),
        )
        if not swing_intervals:
            continue

        for start_idx, end_idx in swing_intervals:
            if end_idx - start_idx < int(min_phase_samples):
                continue
            anchors.append(
                PhaseAnchorRecord(
                    session_id=session_id,
                    split=split,
                    signal_name=signal_name,
                    phase_label="swing",
                    anchor_idx=_midpoint(start_idx, end_idx),
                    interval_start_idx=start_idx,
                    interval_end_idx=end_idx,
                    sample_rate_hz=sample_rate_hz,
                )
            )

        if n_samples > 0:
            leading_start = 0
            leading_end = int(swing_intervals[0][0])
            if leading_end - leading_start >= int(min_support_samples):
                anchors.append(
                    PhaseAnchorRecord(
                        session_id=session_id,
                        split=split,
                        signal_name=signal_name,
                        phase_label="support",
                        anchor_idx=_midpoint(leading_start, leading_end),
                        interval_start_idx=leading_start,
                        interval_end_idx=leading_end,
                        sample_rate_hz=sample_rate_hz,
                    )
                )

        for previous_interval, next_interval in zip(swing_intervals[:-1], swing_intervals[1:]):
            support_start = int(previous_interval[1])
            support_end = int(next_interval[0])
            if support_end <= support_start:
                continue
            gap = support_end - support_start
            if gap < int(min_support_samples):
                anchors.append(
                    PhaseAnchorRecord(
                        session_id=session_id,
                        split=split,
                        signal_name=signal_name,
                        phase_label="swing",
                        anchor_idx=_midpoint(previous_interval[0], next_interval[1]),
                        interval_start_idx=support_start,
                        interval_end_idx=support_end,
                        sample_rate_hz=sample_rate_hz,
                        exception_label="ambiguous_double_peak",
                    )
                )
                continue
            anchors.append(
                PhaseAnchorRecord(
                    session_id=session_id,
                    split=split,
                    signal_name=signal_name,
                    phase_label="support",
                    anchor_idx=_midpoint(support_start, support_end),
                    interval_start_idx=support_start,
                    interval_end_idx=support_end,
                    sample_rate_hz=sample_rate_hz,
                )
            )

        if n_samples > 0:
            trailing_start = int(swing_intervals[-1][1])
            trailing_end = int(n_samples)
            if trailing_end - trailing_start >= int(min_support_samples):
                anchors.append(
                    PhaseAnchorRecord(
                        session_id=session_id,
                        split=split,
                        signal_name=signal_name,
                        phase_label="support",
                        anchor_idx=_midpoint(trailing_start, trailing_end),
                        interval_start_idx=trailing_start,
                        interval_end_idx=trailing_end,
                        sample_rate_hz=sample_rate_hz,
                    )
                )
    anchors.sort(key=lambda item: (item.session_id, item.signal_name, item.anchor_idx, item.phase_label))
    return anchors


def score_classification_predictions(y_true: np.ndarray, y_pred: np.ndarray) -> dict[str, Any]:
    truth = np.asarray(y_true, dtype=np.int64)
    pred = np.asarray(y_pred, dtype=np.int64)
    if truth.shape != pred.shape:
        raise ValueError("y_true and y_pred must have the same shape.")
    labels = (0, 1)
    confusion = np.zeros((2, 2), dtype=np.int64)
    per_class_recall_values: list[float] = []
    per_class_f1_values: list[float] = []
    for truth_label, pred_label in zip(truth.tolist(), pred.tolist()):
        if truth_label in labels and pred_label in labels:
            confusion[int(truth_label), int(pred_label)] += 1
    for label in labels:
        true_positive = float(confusion[label, label])
        false_negative = float(confusion[label, :].sum() - true_positive)
        false_positive = float(confusion[:, label].sum() - true_positive)
        recall = true_positive / (true_positive + false_negative) if (true_positive + false_negative) > 0 else 0.0
        precision = true_positive / (true_positive + false_positive) if (true_positive + false_positive) > 0 else 0.0
        f1 = (2.0 * precision * recall / (precision + recall)) if (precision + recall) > 0 else 0.0
        per_class_recall_values.append(float(recall))
        per_class_f1_values.append(float(f1))
    balanced_accuracy = float(np.mean(np.asarray(per_class_recall_values, dtype=np.float64)))
    macro_f1 = float(np.mean(np.asarray(per_class_f1_values, dtype=np.float64)))
    return {
        "balanced_accuracy": balanced_accuracy,
        "macro_f1": macro_f1,
        "per_class_recall": {
            "support": per_class_recall_values[0],
            "swing": per_class_recall_values[1],
        },
        "confusion_matrix": confusion.astype(int).tolist(),
        "n_samples": int(truth.shape[0]),
    }
