from __future__ import annotations

import argparse
import csv
import hashlib
import importlib.util
import json
import os
import time
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DATASET_ROOT = Path(
    os.environ.get("AUTOBCI_DATASET_ROOT", str(ROOT / "data" / "rsvp_ship_image"))
)
DEFAULT_OUTPUT_DIR = ROOT / "artifacts" / "rsvp_ship_image_autoresearch"
DEFAULT_MONITOR_LATEST = ROOT / "artifacts" / "monitor" / "rsvp_ship_image_autoresearch_latest.json"
LABEL_TO_INDEX = {"nontarget": 0, "target": 1}
INDEX_TO_LABEL = {0: "nontarget", 1: "target"}
EEG_EVENT_SUFFIXES = {".vhdr", ".eeg", ".edf", ".bdf", ".set", ".fif", ".mat", ".csv", ".tsv", ".json", ".txt"}


@dataclass(frozen=True)
class ImageRecord:
    sha1: str
    label: str
    label_index: int
    canonical_path: Path
    duplicate_paths: tuple[Path, ...]


def _utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _json_default(value: Any) -> Any:
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, np.integer):
        return int(value)
    if isinstance(value, np.floating):
        return float(value)
    raise TypeError(f"Object of type {type(value).__name__} is not JSON serializable")


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=_json_default), encoding="utf-8")


def _sha1(path: Path) -> str:
    digest = hashlib.sha1()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _image_files(folder: Path) -> list[Path]:
    return sorted(path for path in folder.glob("*") if path.is_file() and path.suffix.lower() in {".jpg", ".jpeg", ".png"})


def _file_suffix_counts(root: Path) -> dict[str, int]:
    counts: dict[str, int] = defaultdict(int)
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        suffix = path.suffix.lower().lstrip(".") or "[no_ext]"
        counts[suffix] += 1
    return dict(sorted(counts.items()))


def _find_eeg_event_files(root: Path) -> list[str]:
    return sorted(str(path) for path in root.rglob("*") if path.is_file() and path.suffix.lower() in EEG_EVENT_SUFFIXES)


def build_dataset_audit(dataset_root: Path) -> tuple[dict[str, Any], list[ImageRecord]]:
    dataset_root = dataset_root.expanduser().resolve()
    if not dataset_root.exists():
        raise FileNotFoundError(f"Dataset root does not exist: {dataset_root}")

    folders: dict[str, dict[str, Any]] = {}
    hash_to_paths: dict[str, list[Path]] = defaultdict(list)
    hash_to_labels: dict[str, set[str]] = defaultdict(set)
    labeled_file_count = 0

    for label, folder_name in (("target", "target"), ("nontarget", "nontarget")):
        folder = dataset_root / folder_name
        files = _image_files(folder)
        folders[folder_name] = {"path": str(folder), "files": len(files)}
        for path in files:
            digest = _sha1(path)
            hash_to_paths[digest].append(path)
            hash_to_labels[digest].add(label)
            labeled_file_count += 1

    label_conflicts = [
        {"sha1": digest, "labels": sorted(labels), "paths": [str(path) for path in hash_to_paths[digest]]}
        for digest, labels in sorted(hash_to_labels.items())
        if len(labels) > 1
    ]
    if label_conflicts:
        records: list[ImageRecord] = []
    else:
        records = []
        for digest, labels in sorted(hash_to_labels.items()):
            label = next(iter(labels))
            paths = tuple(sorted(hash_to_paths[digest], key=lambda item: str(item)))
            records.append(
                ImageRecord(
                    sha1=digest,
                    label=label,
                    label_index=LABEL_TO_INDEX[label],
                    canonical_path=paths[0],
                    duplicate_paths=paths,
                )
            )

    allimages_files = _image_files(dataset_root / "allimages")
    allimages_hashes = {_sha1(path) for path in allimages_files}
    labeled_hashes = set(hash_to_labels)
    eeg_event_files = _find_eeg_event_files(dataset_root)

    audit = {
        "dataset_root": str(dataset_root),
        "created_at": _utc_now(),
        "folders": {
            **folders,
            "allimages": {"path": str(dataset_root / "allimages"), "files": len(allimages_files)},
        },
        "file_suffix_counts": _file_suffix_counts(dataset_root),
        "labeled_files": labeled_file_count,
        "labeled_unique_hashes": len(hash_to_labels),
        "duplicate_labeled_extra_files": sum(max(0, len(paths) - 1) for paths in hash_to_paths.values()),
        "label_conflicts": label_conflicts,
        "unique_label_counts": {
            "target": sum(1 for item in records if item.label == "target"),
            "nontarget": sum(1 for item in records if item.label == "nontarget"),
        },
        "allimages_files": len(allimages_files),
        "allimages_unique_hashes": len(allimages_hashes),
        "allimages_unlabeled_hashes": len(allimages_hashes - labeled_hashes),
        "labeled_hashes_missing_from_allimages": len(labeled_hashes - allimages_hashes),
        "eeg_event_candidate_files": eeg_event_files,
        "modality_availability": {
            "image": bool(records),
            "eeg": False,
            "event_table": False,
            "matched_trials": False,
        },
        "no_cross_modal_claim": True,
        "eeg_status": "blocked_missing_eeg_or_events",
    }
    return audit, records


def _split_one_class(records: list[ImageRecord], *, train_fraction: float, val_fraction: float) -> dict[str, list[ImageRecord]]:
    n = len(records)
    if n < 3:
        raise ValueError(f"Need at least 3 unique records per class for train/val/test split; got {n}.")
    n_train = max(1, int(round(n * train_fraction)))
    n_val = max(1, int(round(n * val_fraction)))
    if n_train + n_val >= n:
        n_train = max(1, n - 2)
        n_val = 1
    return {
        "train": records[:n_train],
        "val": records[n_train : n_train + n_val],
        "test": records[n_train + n_val :],
    }


def build_split_manifest(
    records: list[ImageRecord],
    *,
    train_fraction: float = 0.7,
    val_fraction: float = 0.15,
    split_salt: str = "",
) -> tuple[list[dict[str, Any]], dict[str, dict[str, int]]]:
    def split_key(item: ImageRecord) -> str:
        salt = str(split_salt or "").strip()
        if not salt:
            return item.sha1
        return hashlib.sha1(f"{salt}:{item.sha1}".encode("utf-8")).hexdigest()

    by_label: dict[str, list[ImageRecord]] = {
        label: sorted([item for item in records if item.label == label], key=split_key)
        for label in LABEL_TO_INDEX
    }
    split_records: list[dict[str, Any]] = []
    summary: dict[str, dict[str, int]] = {
        "train": {"target": 0, "nontarget": 0, "total": 0},
        "val": {"target": 0, "nontarget": 0, "total": 0},
        "test": {"target": 0, "nontarget": 0, "total": 0},
    }
    for label, class_records in by_label.items():
        split_map = _split_one_class(class_records, train_fraction=train_fraction, val_fraction=val_fraction)
        for split, items in split_map.items():
            for item in items:
                row = {
                    "split": split,
                    "sha1": item.sha1,
                    "label": item.label,
                    "label_index": item.label_index,
                    "canonical_path": str(item.canonical_path),
                    "duplicate_count": len(item.duplicate_paths),
                    "duplicate_paths": "|".join(str(path) for path in item.duplicate_paths),
                }
                split_records.append(row)
                summary[split][label] += 1
                summary[split]["total"] += 1
    return sorted(split_records, key=lambda row: (row["split"], row["label"], row["sha1"])), summary


def write_split_manifest(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fields = ["split", "sha1", "label", "label_index", "canonical_path", "duplicate_count", "duplicate_paths"]
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def _resampling_filter() -> Any:
    try:
        return Image.Resampling.BILINEAR
    except AttributeError:  # pragma: no cover - older Pillow fallback.
        return Image.BILINEAR


def _load_grayscale_feature(path: Path, *, resize: int) -> np.ndarray:
    with Image.open(path) as image:
        gray = image.convert("L").resize((resize, resize), _resampling_filter())
    return np.asarray(gray, dtype=np.float32).reshape(-1) / 255.0


def _rgb_to_hsv_pixels(rgb: np.ndarray) -> np.ndarray:
    maxc = np.max(rgb, axis=2)
    minc = np.min(rgb, axis=2)
    delta = maxc - minc
    hue = np.zeros_like(maxc, dtype=np.float32)
    mask = delta > 1e-6
    r, g, b = rgb[:, :, 0], rgb[:, :, 1], rgb[:, :, 2]
    r_mask = mask & (maxc == r)
    g_mask = mask & (maxc == g)
    b_mask = mask & (maxc == b)
    hue[r_mask] = ((g[r_mask] - b[r_mask]) / delta[r_mask]) % 6.0
    hue[g_mask] = ((b[g_mask] - r[g_mask]) / delta[g_mask]) + 2.0
    hue[b_mask] = ((r[b_mask] - g[b_mask]) / delta[b_mask]) + 4.0
    hue = hue / 6.0
    sat = np.zeros_like(maxc, dtype=np.float32)
    nonzero = maxc > 1e-6
    sat[nonzero] = delta[nonzero] / maxc[nonzero]
    return np.stack([hue, sat, maxc], axis=2).astype(np.float32)


def _histogram(values: np.ndarray, *, bins: int, value_range: tuple[float, float] = (0.0, 1.0)) -> np.ndarray:
    hist, _ = np.histogram(values.reshape(-1), bins=bins, range=value_range)
    hist = hist.astype(np.float32)
    denom = float(hist.sum())
    return hist / denom if denom > 0 else hist


def _load_rgb_hsv_hist_feature(path: Path, *, resize: int = 64, bins: int = 8) -> np.ndarray:
    with Image.open(path) as image:
        rgb_image = image.convert("RGB").resize((resize, resize), _resampling_filter())
    rgb = np.asarray(rgb_image, dtype=np.float32) / 255.0
    hsv = _rgb_to_hsv_pixels(rgb)
    parts: list[np.ndarray] = []
    for channel in range(3):
        parts.append(_histogram(rgb[:, :, channel], bins=bins))
    for channel in range(3):
        parts.append(_histogram(hsv[:, :, channel], bins=bins))
    stats = np.concatenate([rgb.mean(axis=(0, 1)), rgb.std(axis=(0, 1)), hsv.mean(axis=(0, 1)), hsv.std(axis=(0, 1))])
    return np.concatenate([*parts, stats.astype(np.float32)]).astype(np.float32)


def _load_gradient_hog_feature(path: Path, *, resize: int = 64, cells: int = 4, bins: int = 9) -> np.ndarray:
    with Image.open(path) as image:
        gray = image.convert("L").resize((resize, resize), _resampling_filter())
    arr = np.asarray(gray, dtype=np.float32) / 255.0
    gy, gx = np.gradient(arr)
    magnitude = np.sqrt(gx * gx + gy * gy)
    angle = np.mod(np.arctan2(gy, gx), np.pi)
    cell_h = resize // cells
    cell_w = resize // cells
    features: list[np.ndarray] = []
    for row in range(cells):
        for col in range(cells):
            y0, y1 = row * cell_h, (row + 1) * cell_h
            x0, x1 = col * cell_w, (col + 1) * cell_w
            hist, _ = np.histogram(
                angle[y0:y1, x0:x1].reshape(-1),
                bins=bins,
                range=(0.0, np.pi),
                weights=magnitude[y0:y1, x0:x1].reshape(-1),
            )
            features.append(hist.astype(np.float32))
    vector = np.concatenate(features).astype(np.float32)
    norm = float(np.linalg.norm(vector))
    return vector / norm if norm > 1e-8 else vector


def _load_lbp_texture_feature(path: Path, *, resize: int = 64, bins: int = 16) -> np.ndarray:
    with Image.open(path) as image:
        gray = image.convert("L").resize((resize, resize), _resampling_filter())
    arr = np.asarray(gray, dtype=np.float32) / 255.0
    center = arr[1:-1, 1:-1]
    code = np.zeros_like(center, dtype=np.uint8)
    neighbors = [
        arr[:-2, :-2],
        arr[:-2, 1:-1],
        arr[:-2, 2:],
        arr[1:-1, 2:],
        arr[2:, 2:],
        arr[2:, 1:-1],
        arr[2:, :-2],
        arr[1:-1, :-2],
    ]
    for bit, neighbor in enumerate(neighbors):
        code |= ((neighbor >= center).astype(np.uint8) << bit)
    grouped = (code.astype(np.int32) * bins) // 256
    hist = _histogram(grouped.astype(np.float32), bins=bins, value_range=(0.0, float(bins)))
    stats = np.asarray([arr.mean(), arr.std(), np.mean(np.abs(np.gradient(arr)[0])), np.mean(np.abs(np.gradient(arr)[1]))], dtype=np.float32)
    return np.concatenate([hist, stats]).astype(np.float32)


def _load_projection_profile_feature(path: Path, *, resize: int = 64, grid: int = 4) -> np.ndarray:
    with Image.open(path) as image:
        gray = image.convert("L").resize((resize, resize), _resampling_filter())
    arr = np.asarray(gray, dtype=np.float32) / 255.0
    row_mean = arr.mean(axis=1)
    col_mean = arr.mean(axis=0)
    row_std = arr.std(axis=1)
    col_std = arr.std(axis=0)
    cell_h = resize // grid
    cell_w = resize // grid
    cells: list[float] = []
    for row in range(grid):
        for col in range(grid):
            patch = arr[row * cell_h : (row + 1) * cell_h, col * cell_w : (col + 1) * cell_w]
            cells.extend([float(patch.mean()), float(patch.std())])
    return np.concatenate([row_mean, col_mean, row_std, col_std, np.asarray(cells, dtype=np.float32)]).astype(np.float32)


def _load_edge_density_feature(path: Path, *, resize: int = 64, grid: int = 4) -> np.ndarray:
    with Image.open(path) as image:
        gray = image.convert("L").resize((resize, resize), _resampling_filter())
    arr = np.asarray(gray, dtype=np.float32) / 255.0
    gy, gx = np.gradient(arr)
    magnitude = np.sqrt(gx * gx + gy * gy)
    threshold = float(np.quantile(magnitude, 0.75))
    cell_h = resize // grid
    cell_w = resize // grid
    features: list[float] = [
        float(magnitude.mean()),
        float(magnitude.std()),
        float(np.mean(magnitude > threshold)),
        float(np.percentile(magnitude, 90)),
    ]
    for row in range(grid):
        for col in range(grid):
            patch = magnitude[row * cell_h : (row + 1) * cell_h, col * cell_w : (col + 1) * cell_w]
            features.extend(
                [
                    float(patch.mean()),
                    float(patch.std()),
                    float(patch.max(initial=0.0)),
                    float(np.mean(patch > threshold)),
                ]
            )
    return np.asarray(features, dtype=np.float32)


def _load_fusion_lbp_hog_color_projection_edge_feature(path: Path) -> np.ndarray:
    parts = [
        _load_lbp_texture_feature(path),
        _load_gradient_hog_feature(path),
        _load_rgb_hsv_hist_feature(path),
        _load_projection_profile_feature(path),
        _load_edge_density_feature(path),
    ]
    return np.concatenate(parts).astype(np.float32)


def _load_feature(path: Path, *, feature_family: str, resize: int | None = None) -> np.ndarray:
    if feature_family == "grayscale_pixels":
        if resize is None:
            raise ValueError("grayscale_pixels requires resize")
        return _load_grayscale_feature(path, resize=resize)
    if feature_family == "rgb_hsv_histogram":
        return _load_rgb_hsv_hist_feature(path)
    if feature_family == "gradient_hog":
        return _load_gradient_hog_feature(path)
    if feature_family == "lbp_texture":
        return _load_lbp_texture_feature(path)
    if feature_family == "projection_profile":
        return _load_projection_profile_feature(path)
    if feature_family == "edge_density":
        return _load_edge_density_feature(path)
    if feature_family == "fusion_lbp_hog_color_projection_edge":
        return _load_fusion_lbp_hog_color_projection_edge_feature(path)
    raise ValueError(f"Unknown feature_family: {feature_family}")


def _features_for_split(
    rows: list[dict[str, Any]],
    split: str,
    *,
    feature_family: str = "grayscale_pixels",
    resize: int | None = None,
) -> tuple[np.ndarray, np.ndarray]:
    selected = [row for row in rows if row["split"] == split]
    x = np.stack(
        [_load_feature(Path(str(row["canonical_path"])), feature_family=feature_family, resize=resize) for row in selected],
        axis=0,
    )
    y = np.asarray([int(row["label_index"]) for row in selected], dtype=np.int64)
    return x, y


def _confusion_matrix(y_true: np.ndarray, y_pred: np.ndarray) -> list[list[int]]:
    matrix = [[0, 0], [0, 0]]
    for truth, pred in zip(y_true.astype(int), y_pred.astype(int), strict=True):
        matrix[int(truth)][int(pred)] += 1
    return matrix


def score_predictions(y_true: np.ndarray, y_pred: np.ndarray) -> dict[str, Any]:
    matrix = _confusion_matrix(y_true, y_pred)
    recalls: dict[str, float] = {}
    f1s: list[float] = []
    for idx, label in INDEX_TO_LABEL.items():
        tp = matrix[idx][idx]
        fn = sum(matrix[idx]) - tp
        fp = sum(matrix[row][idx] for row in range(2)) - tp
        recall = tp / (tp + fn) if (tp + fn) else 0.0
        precision = tp / (tp + fp) if (tp + fp) else 0.0
        f1 = 2 * precision * recall / (precision + recall) if (precision + recall) else 0.0
        recalls[label] = float(recall)
        f1s.append(float(f1))
    total = int(y_true.shape[0])
    correct = int(np.sum(y_true == y_pred))
    return {
        "accuracy": float(correct / total) if total else 0.0,
        "balanced_accuracy": float(sum(recalls.values()) / len(recalls)) if recalls else 0.0,
        "macro_f1": float(sum(f1s) / len(f1s)) if f1s else 0.0,
        "per_class_recall": recalls,
        "confusion_matrix": matrix,
        "n": total,
    }


def majority_predictions(train_y: np.ndarray, target_y: np.ndarray) -> np.ndarray:
    counts = np.bincount(train_y.astype(int), minlength=2)
    majority = int(np.argmax(counts))
    return np.full_like(target_y, majority)


def _standardize(train_x: np.ndarray, *others: np.ndarray) -> tuple[np.ndarray, ...]:
    mean = train_x.mean(axis=0)
    std = train_x.std(axis=0)
    std = np.where(std < 1e-6, 1.0, std)
    return tuple(((values - mean) / std).astype(np.float32) for values in (train_x, *others))


def _sigmoid(values: np.ndarray) -> np.ndarray:
    return 1.0 / (1.0 + np.exp(-np.clip(values, -40.0, 40.0)))


def fit_logistic_numpy(
    train_x: np.ndarray,
    train_y: np.ndarray,
    *,
    epochs: int,
    lr: float,
    l2: float,
) -> tuple[np.ndarray, float]:
    x = train_x.astype(np.float32)
    y = train_y.astype(np.float32)
    weights = np.zeros(x.shape[1], dtype=np.float32)
    bias = 0.0
    class_counts = np.bincount(train_y.astype(int), minlength=2).astype(np.float32)
    class_weights = np.asarray(
        [len(train_y) / (2.0 * max(1.0, class_counts[0])), len(train_y) / (2.0 * max(1.0, class_counts[1]))],
        dtype=np.float32,
    )
    sample_weights = class_weights[train_y.astype(int)]
    denom = float(np.sum(sample_weights))
    for _ in range(int(epochs)):
        logits = x @ weights + bias
        pred = _sigmoid(logits)
        error = (pred - y) * sample_weights
        grad_w = (x.T @ error) / denom + float(l2) * weights
        grad_b = float(np.sum(error) / denom)
        weights -= float(lr) * grad_w.astype(np.float32)
        bias -= float(lr) * grad_b
    return weights, float(bias)


def predict_logistic(x: np.ndarray, weights: np.ndarray, bias: float) -> np.ndarray:
    return (_sigmoid(x @ weights + bias) >= 0.5).astype(np.int64)


def predict_logistic_proba(x: np.ndarray, weights: np.ndarray, bias: float) -> np.ndarray:
    return _sigmoid(x @ weights + bias).astype(np.float32)


def predict_with_threshold(probability: np.ndarray, threshold: float) -> np.ndarray:
    return (probability >= float(threshold)).astype(np.int64)


def predict_nearest_centroid(train_x: np.ndarray, train_y: np.ndarray, target_x: np.ndarray) -> np.ndarray:
    centroids = []
    for label in (0, 1):
        rows = train_x[train_y == label]
        if rows.size == 0:
            centroids.append(np.zeros(train_x.shape[1], dtype=np.float32))
        else:
            centroids.append(rows.mean(axis=0).astype(np.float32))
    centroid_matrix = np.stack(centroids, axis=0)
    distances = np.sum((target_x[:, None, :] - centroid_matrix[None, :, :]) ** 2, axis=2)
    return np.argmin(distances, axis=1).astype(np.int64)


def predict_knn(train_x: np.ndarray, train_y: np.ndarray, target_x: np.ndarray, *, k: int) -> np.ndarray:
    k = max(1, min(int(k), int(train_x.shape[0])))
    train_norm = np.sum(train_x * train_x, axis=1)
    target_norm = np.sum(target_x * target_x, axis=1)
    distances = target_norm[:, None] + train_norm[None, :] - 2.0 * (target_x @ train_x.T)
    neighbor_indices = np.argpartition(distances, kth=k - 1, axis=1)[:, :k]
    predictions: list[int] = []
    for row_idx, neighbors in enumerate(neighbor_indices):
        labels = train_y[neighbors].astype(int)
        counts = np.bincount(labels, minlength=2)
        if counts[0] != counts[1]:
            predictions.append(int(np.argmax(counts)))
            continue
        mean_distances = [
            float(np.mean(distances[row_idx, neighbors[labels == label]])) if np.any(labels == label) else float("inf")
            for label in (0, 1)
        ]
        predictions.append(int(np.argmin(mean_distances)))
    return np.asarray(predictions, dtype=np.int64)


def fit_ridge_classifier(train_x: np.ndarray, train_y: np.ndarray, *, alpha: float) -> np.ndarray:
    x = np.concatenate([train_x.astype(np.float32), np.ones((train_x.shape[0], 1), dtype=np.float32)], axis=1)
    y = np.where(train_y.astype(int) == 1, 1.0, -1.0).astype(np.float32)
    reg = np.eye(x.shape[1], dtype=np.float32) * float(alpha)
    reg[-1, -1] = 0.0
    lhs = x.T @ x + reg
    rhs = x.T @ y
    try:
        return np.linalg.solve(lhs, rhs).astype(np.float32)
    except np.linalg.LinAlgError:
        return (np.linalg.pinv(lhs) @ rhs).astype(np.float32)


def predict_ridge_classifier(x: np.ndarray, weights: np.ndarray) -> np.ndarray:
    with_bias = np.concatenate([x.astype(np.float32), np.ones((x.shape[0], 1), dtype=np.float32)], axis=1)
    return ((with_bias @ weights) >= 0.0).astype(np.int64)


def predict_gaussian_nb(train_x: np.ndarray, train_y: np.ndarray, target_x: np.ndarray) -> np.ndarray:
    scores: list[np.ndarray] = []
    for label in (0, 1):
        rows = train_x[train_y == label]
        if rows.size == 0:
            mean = np.zeros(train_x.shape[1], dtype=np.float32)
            var = np.ones(train_x.shape[1], dtype=np.float32)
            prior = 1e-6
        else:
            mean = rows.mean(axis=0).astype(np.float32)
            var = np.maximum(rows.var(axis=0).astype(np.float32), 1e-3)
            prior = float(rows.shape[0] / max(1, train_x.shape[0]))
        log_prob = -0.5 * np.sum(np.log(2.0 * np.pi * var) + ((target_x - mean) ** 2) / var, axis=1)
        scores.append(log_prob + np.log(max(prior, 1e-6)))
    return np.argmax(np.stack(scores, axis=1), axis=1).astype(np.int64)


def choose_validation_threshold(y_true: np.ndarray, probability: np.ndarray) -> tuple[float, dict[str, Any]]:
    candidates = sorted({0.5, *[float(value) for value in np.linspace(0.05, 0.95, 19)], *[float(value) for value in probability]})
    best_threshold = 0.5
    best_metrics = score_predictions(y_true, predict_with_threshold(probability, best_threshold))
    for threshold in candidates:
        metrics = score_predictions(y_true, predict_with_threshold(probability, threshold))
        current_key = (float(metrics["balanced_accuracy"]), float(metrics["macro_f1"]), -abs(float(threshold) - 0.5))
        best_key = (float(best_metrics["balanced_accuracy"]), float(best_metrics["macro_f1"]), -abs(float(best_threshold) - 0.5))
        if current_key > best_key:
            best_threshold = float(threshold)
            best_metrics = metrics
    return best_threshold, best_metrics


def _evaluate_logistic_feature_candidate(
    split_rows: list[dict[str, Any]],
    *,
    model_family: str,
    feature_family: str,
    feature_label: str,
    resize: int | None,
    logistic_epochs: int,
    lr: float,
    l2: float,
    calibrated: bool = False,
) -> dict[str, Any]:
    train_x, train_y = _features_for_split(split_rows, "train", feature_family=feature_family, resize=resize)
    val_x, val_y = _features_for_split(split_rows, "val", feature_family=feature_family, resize=resize)
    test_x, test_y = _features_for_split(split_rows, "test", feature_family=feature_family, resize=resize)
    train_x, val_x, test_x = _standardize(train_x, val_x, test_x)
    weights, bias = fit_logistic_numpy(train_x, train_y, epochs=logistic_epochs, lr=lr, l2=l2)
    val_probability = predict_logistic_proba(val_x, weights, bias)
    test_probability = predict_logistic_proba(test_x, weights, bias)
    threshold = 0.5
    if calibrated:
        threshold, val_metrics = choose_validation_threshold(val_y, val_probability)
    else:
        val_metrics = score_predictions(val_y, predict_with_threshold(val_probability, threshold))
    test_pred = predict_with_threshold(test_probability, threshold)
    config: dict[str, Any] = {
        "feature_family": feature_label,
        "resize": resize,
        "epochs": int(logistic_epochs),
        "lr": float(lr),
        "l2": float(l2),
        "standardization": "train_split_only",
        "class_weight": "balanced",
        "decision_threshold": float(threshold),
        "threshold_policy": "validation_balanced_accuracy" if calibrated else "fixed_0.5",
    }
    if feature_family == "fusion_lbp_hog_color_projection_edge":
        config["structure_change"] = "feature_fusion"
        config["fusion_parts"] = ["lbp_texture", "gradient_hog", "rgb_hsv_histogram", "projection_profile", "edge_density"]
    return {
        "model_family": model_family,
        "model_backend": "numpy_weighted_logistic_regression",
        "config": config,
        "val_metrics": val_metrics,
        "test_metrics": score_predictions(test_y, test_pred),
    }


def _evaluate_non_logistic_candidate(
    split_rows: list[dict[str, Any]],
    *,
    model_family: str,
    model_backend: str,
    feature_family: str,
    feature_label: str,
    resize: int | None,
    classifier: str,
    k: int | None = None,
    alpha: float = 1.0,
) -> dict[str, Any]:
    train_x, train_y = _features_for_split(split_rows, "train", feature_family=feature_family, resize=resize)
    val_x, val_y = _features_for_split(split_rows, "val", feature_family=feature_family, resize=resize)
    test_x, test_y = _features_for_split(split_rows, "test", feature_family=feature_family, resize=resize)
    train_x, val_x, test_x = _standardize(train_x, val_x, test_x)

    if classifier == "nearest_centroid":
        val_pred = predict_nearest_centroid(train_x, train_y, val_x)
        test_pred = predict_nearest_centroid(train_x, train_y, test_x)
    elif classifier == "knn":
        if k is None:
            raise ValueError("knn classifier requires k")
        val_pred = predict_knn(train_x, train_y, val_x, k=k)
        test_pred = predict_knn(train_x, train_y, test_x, k=k)
    elif classifier == "ridge":
        weights = fit_ridge_classifier(train_x, train_y, alpha=alpha)
        val_pred = predict_ridge_classifier(val_x, weights)
        test_pred = predict_ridge_classifier(test_x, weights)
    elif classifier == "gaussian_nb":
        val_pred = predict_gaussian_nb(train_x, train_y, val_x)
        test_pred = predict_gaussian_nb(train_x, train_y, test_x)
    else:
        raise ValueError(f"Unknown classifier: {classifier}")

    config: dict[str, Any] = {
        "feature_family": feature_label,
        "resize": resize,
        "classifier": classifier,
        "standardization": "train_split_only",
    }
    if k is not None:
        config["k"] = int(k)
    if classifier == "ridge":
        config["alpha"] = float(alpha)
    return {
        "model_family": model_family,
        "model_backend": model_backend,
        "config": config,
        "val_metrics": score_predictions(val_y, val_pred),
        "test_metrics": score_predictions(test_y, test_pred),
    }


def _load_structure_runner_candidates(split_rows: list[dict[str, Any]], *, logistic_epochs: int) -> list[dict[str, Any]]:
    runner_path = ROOT / "experiments" / "rsvp_ship_image_structure" / "structure_runner.py"
    if not runner_path.exists():
        return []
    spec = importlib.util.spec_from_file_location("autobci_rsvp_ship_image_structure_runner", runner_path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Could not load structure runner: {runner_path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    build_candidates = getattr(module, "build_candidates", None)
    if not callable(build_candidates):
        return []
    context = {
        "split_rows": split_rows,
        "logistic_epochs": int(logistic_epochs),
        "evaluate_logistic_feature_candidate": _evaluate_logistic_feature_candidate,
        "evaluate_non_logistic_candidate": _evaluate_non_logistic_candidate,
    }
    raw_candidates = build_candidates(context)
    if raw_candidates is None:
        return []
    if not isinstance(raw_candidates, list):
        raise RuntimeError("structure_runner.build_candidates must return a list")
    candidates: list[dict[str, Any]] = []
    for index, candidate in enumerate(raw_candidates, start=1):
        if not isinstance(candidate, dict):
            raise RuntimeError(f"structure runner candidate {index} is not a dict")
        missing = [key for key in ("model_family", "model_backend", "config", "val_metrics", "test_metrics") if key not in candidate]
        if missing:
            raise RuntimeError(f"structure runner candidate {index} missing keys: {', '.join(missing)}")
        item = dict(candidate)
        config = dict(item.get("config") or {})
        config.setdefault("structure_source", "editable_structure_runner")
        item["config"] = config
        item.setdefault("source", "editable_structure_runner")
        candidates.append(item)
    return candidates


def evaluate_image_models(
    split_rows: list[dict[str, Any]],
    *,
    logistic_epochs: int,
) -> dict[str, Any]:
    candidates: list[dict[str, Any]] = []
    base_train_x, train_y = _features_for_split(split_rows, "train", feature_family="grayscale_pixels", resize=16)
    _base_val_x, val_y = _features_for_split(split_rows, "val", feature_family="grayscale_pixels", resize=16)
    _base_test_x, test_y = _features_for_split(split_rows, "test", feature_family="grayscale_pixels", resize=16)

    val_majority = majority_predictions(train_y, val_y)
    test_majority = majority_predictions(train_y, test_y)
    candidates.append(
        {
            "model_family": "majority_baseline",
            "model_backend": "constant_train_majority",
            "config": {"feature_family": "none", "resize": None},
            "val_metrics": score_predictions(val_y, val_majority),
            "test_metrics": score_predictions(test_y, test_majority),
        }
    )
    del base_train_x

    candidate_specs = [
        ("image_tiny_pixel_logistic", "grayscale_pixels", "grayscale_pixels_8x8", 8, 0.25, 0.01),
        ("image_logistic_baseline", "grayscale_pixels", "grayscale_pixels_16x16", 16, 0.2, 0.01),
        ("image_mid_pixel_logistic", "grayscale_pixels", "grayscale_pixels_24x24", 24, 0.16, 0.01),
        ("image_logistic_baseline", "grayscale_pixels", "grayscale_pixels_32x32", 32, 0.12, 0.01),
        ("image_color_histogram_logistic", "rgb_hsv_histogram", "rgb_hsv_histogram_8bins", None, 0.25, 0.001),
        ("image_edge_hog_linear_probe", "gradient_hog", "gradient_hog_4x4_9bins", None, 0.25, 0.01),
        ("image_lbp_texture_baseline", "lbp_texture", "lbp_texture_16bins", None, 0.2, 0.005),
        ("image_projection_profile_logistic", "projection_profile", "projection_profile_rows_cols_grid", None, 0.2, 0.005),
        ("image_edge_density_logistic", "edge_density", "edge_density_4x4", None, 0.25, 0.005),
    ]
    for model_family, feature_family, feature_label, resize, lr, l2 in candidate_specs:
        candidates.append(
            _evaluate_logistic_feature_candidate(
                split_rows,
                model_family=model_family,
                feature_family=feature_family,
                feature_label=feature_label,
                resize=resize,
                logistic_epochs=logistic_epochs,
                lr=lr,
                l2=l2,
            )
        )
        if model_family == "image_logistic_baseline":
            candidates.append(
                _evaluate_logistic_feature_candidate(
                    split_rows,
                    model_family="image_threshold_calibration_sweep",
                    feature_family=feature_family,
                    feature_label=f"{feature_label}_validation_threshold",
                    resize=resize,
                    logistic_epochs=logistic_epochs,
                    lr=lr,
                    l2=l2,
                    calibrated=True,
                )
            )

    non_logistic_specs = [
        {
            "model_family": "image_ridge_pixel_classifier",
            "model_backend": "numpy_ridge_least_squares",
            "feature_family": "grayscale_pixels",
            "feature_label": "grayscale_pixels_16x16",
            "resize": 16,
            "classifier": "ridge",
            "alpha": 5.0,
        },
        {
            "model_family": "image_gaussian_nb_color_histogram",
            "model_backend": "numpy_gaussian_naive_bayes",
            "feature_family": "rgb_hsv_histogram",
            "feature_label": "rgb_hsv_histogram_8bins",
            "resize": None,
            "classifier": "gaussian_nb",
        },
        {
            "model_family": "image_nearest_centroid_hog",
            "model_backend": "numpy_nearest_centroid",
            "feature_family": "gradient_hog",
            "feature_label": "gradient_hog_4x4_9bins",
            "resize": None,
            "classifier": "nearest_centroid",
        },
        {
            "model_family": "image_knn3_pixel_classifier",
            "model_backend": "numpy_knn",
            "feature_family": "grayscale_pixels",
            "feature_label": "grayscale_pixels_16x16",
            "resize": 16,
            "classifier": "knn",
            "k": 3,
        },
        {
            "model_family": "image_knn5_texture_classifier",
            "model_backend": "numpy_knn",
            "feature_family": "lbp_texture",
            "feature_label": "lbp_texture_16bins",
            "resize": None,
            "classifier": "knn",
            "k": 5,
        },
    ]
    for spec in non_logistic_specs:
        candidates.append(_evaluate_non_logistic_candidate(split_rows, **spec))

    candidates.extend(_load_structure_runner_candidates(split_rows, logistic_epochs=logistic_epochs))

    best = max(
        candidates,
        key=lambda item: (float(item["val_metrics"]["balanced_accuracy"]), float(item["val_metrics"]["macro_f1"])),
    )
    return {
        "candidates": candidates,
        "selected_model": {
            "model_family": best["model_family"],
            "model_backend": best["model_backend"],
            "config": best["config"],
            "selection_metric": "val_balanced_accuracy",
            "selection_value": best["val_metrics"]["balanced_accuracy"],
        },
        "val_metrics": best["val_metrics"],
        "test_metrics": best["test_metrics"],
    }


def _write_comparison_report(path: Path, result: dict[str, Any]) -> None:
    selected = result["selected_model"]
    test = result["test_metrics"]
    lines = [
        "# RSVP Ship Image-Only AutoResearch Report",
        "",
        f"- run_id: {result['run_id']}",
        f"- status: {result['status']}",
        f"- selected image model: {selected['model_family']} / {selected['model_backend']}",
        f"- test balanced accuracy: {test['balanced_accuracy']:.4f}",
        f"- test macro F1: {test['macro_f1']:.4f}",
        f"- EEG status: {result['eeg_status']}",
        f"- no cross-modal claim: {str(result['no_cross_modal_claim']).lower()}",
        "",
        "## 数据划分",
    ]
    for split, counts in result["split_summary"].items():
        lines.append(f"- {split}: total={counts['total']}, target={counts['target']}, nontarget={counts['nontarget']}")
    lines.extend(["", "## 候选模型"])
    for candidate in result.get("candidates", []):
        test = candidate["test_metrics"]
        val = candidate["val_metrics"]
        config = candidate.get("config") or {}
        lines.append(
            "- "
            f"{candidate['model_family']} / {config.get('feature_family')}: "
            f"val_balanced_accuracy={val['balanced_accuracy']:.4f}, "
            f"test_balanced_accuracy={test['balanced_accuracy']:.4f}, "
            f"test_macro_f1={test['macro_f1']:.4f}, "
            f"threshold={float(config.get('decision_threshold', 0.5)):.4f}"
        )
    lines.extend(
        [
            "",
            "## 边界",
            "- 当前只运行图像分支。",
            "- 没有 EEG 文件、事件表和 matched trial 映射，因此不比较 EEG 与图像高低。",
            "- split 单位是唯一图片内容 hash，重复图片不会跨 split。",
        ]
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def run_image_autoresearch(
    *,
    dataset_root: Path,
    output_dir: Path,
    run_id: str | None = None,
    seed: int = 7,
    logistic_epochs: int = 1200,
    split_salt: str = "",
    monitor_latest_path: Path | None = None,
) -> dict[str, Any]:
    started = time.time()
    run_id = run_id or f"rsvp-ship-image-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}"
    run_dir = output_dir.expanduser().resolve() / run_id
    audit, records = build_dataset_audit(dataset_root)
    if audit["label_conflicts"]:
        raise RuntimeError("Label conflicts found; inspect dataset_audit.json before training.")
    split_rows, split_summary = build_split_manifest(records, split_salt=split_salt)
    model_result = evaluate_image_models(split_rows, logistic_epochs=int(logistic_epochs))

    artifacts = {
        "dataset_audit": str(run_dir / "dataset_audit.json"),
        "split_manifest": str(run_dir / "split_manifest.csv"),
        "image_result": str(run_dir / "image_result.json"),
        "comparison_report": str(run_dir / "comparison_report.md"),
        "run_config": str(run_dir / "run_config.json"),
    }
    if monitor_latest_path is not None:
        artifacts["monitor_latest"] = str(monitor_latest_path.expanduser().resolve())
    result = {
        "run_id": run_id,
        "created_at": _utc_now(),
        "program_id": "rsvp_ship_image_only_v0",
        "dataset_name": "Downloads/RSVP跨模态数据",
        "dataset_root": str(dataset_root.expanduser().resolve()),
        "status": "completed_image_only",
        "target_mode": "rsvp_ship_image_classification",
        "primary_metric": "test_balanced_accuracy",
        "benchmark_primary_score": float(model_result["val_metrics"]["balanced_accuracy"]),
        "test_primary_metric": float(model_result["test_metrics"]["balanced_accuracy"]),
        "no_cross_modal_claim": True,
        "eeg_status": "blocked_missing_eeg_or_events",
        "split_policy": "deduplicated_content_hash_stratified_70_15_15",
        "split_salt": str(split_salt or ""),
        "split_summary": split_summary,
        "audit_summary": {
            "labeled_files": audit["labeled_files"],
            "labeled_unique_hashes": audit["labeled_unique_hashes"],
            "duplicate_labeled_extra_files": audit["duplicate_labeled_extra_files"],
            "allimages_unlabeled_hashes": audit["allimages_unlabeled_hashes"],
            "modality_availability": audit["modality_availability"],
        },
        "selected_model": model_result["selected_model"],
        "val_metrics": model_result["val_metrics"],
        "test_metrics": model_result["test_metrics"],
        "candidates": model_result["candidates"],
        "elapsed_seconds": float(time.time() - started),
        "artifacts": artifacts,
    }
    run_config = {
        "seed": int(seed),
        "seed_note": "The split is deterministic by content hash plus optional split_salt; no random generator is used.",
        "logistic_epochs": int(logistic_epochs),
        "split_salt": str(split_salt or ""),
        "dataset_root": str(dataset_root.expanduser().resolve()),
        "output_dir": str(output_dir.expanduser().resolve()),
        "run_id": run_id,
        "started_at": result["created_at"],
        "raw_data_policy": "read_only",
        "forbidden_claims": ["eeg_vs_image_comparison_without_matched_trials"],
    }

    write_json(Path(artifacts["dataset_audit"]), audit)
    write_split_manifest(Path(artifacts["split_manifest"]), split_rows)
    write_json(Path(artifacts["run_config"]), run_config)
    _write_comparison_report(Path(artifacts["comparison_report"]), result)
    write_json(Path(artifacts["image_result"]), result)
    write_json(output_dir.expanduser().resolve() / "latest.json", result)
    if monitor_latest_path is not None:
        write_json(monitor_latest_path.expanduser().resolve(), result)
    return result


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run image-only AutoResearch for the RSVP ship dataset.")
    parser.add_argument("--dataset-root", type=Path, default=DEFAULT_DATASET_ROOT)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--run-id", default=None)
    parser.add_argument("--seed", type=int, default=7)
    parser.add_argument("--logistic-epochs", type=int, default=1200)
    parser.add_argument("--split-salt", default="")
    parser.add_argument("--monitor-latest", type=Path, default=DEFAULT_MONITOR_LATEST)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    result = run_image_autoresearch(
        dataset_root=args.dataset_root,
        output_dir=args.output_dir,
        run_id=args.run_id,
        seed=args.seed,
        logistic_epochs=args.logistic_epochs,
        split_salt=args.split_salt,
        monitor_latest_path=args.monitor_latest,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2, default=_json_default))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
