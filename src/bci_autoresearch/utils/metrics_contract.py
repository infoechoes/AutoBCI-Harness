from __future__ import annotations

from typing import Any


def _to_float_or_none(value: Any) -> float | None:
    if value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if number != number or number in (float("inf"), float("-inf")):
        return None
    return number


def _extract_metric(payload: dict[str, Any], *paths: str) -> float | None:
    for path in paths:
        current: Any = payload
        valid = True
        for part in path.split("."):
            if not isinstance(current, dict) or part not in current:
                valid = False
                break
            current = current[part]
        if not valid:
            continue
        number = _to_float_or_none(current)
        if number is not None:
            return number
    return None


def normalize_metrics_contract(metrics: dict[str, Any]) -> dict[str, Any]:
    normalized = dict(metrics)

    val_primary = _extract_metric(
        normalized,
        "val_primary_metric",
        "val_metrics.mean_pearson_r_zero_lag_macro",
        "mean_pearson_r_zero_lag_macro",
        "val_r_zero",
        "val_r",
    )
    test_primary = _extract_metric(
        normalized,
        "test_primary_metric",
        "test_metrics.mean_pearson_r_zero_lag_macro",
        "test_r_zero",
        "test_r",
    )
    val_rmse = _extract_metric(
        normalized,
        "val_rmse",
        "val_rmse_deg",
        "val_metrics.mean_rmse_deg_macro",
        "val_metrics.mean_rmse_macro",
        "mean_rmse_deg",
        "mean_rmse",
    )
    test_rmse = _extract_metric(
        normalized,
        "test_rmse",
        "test_rmse_deg",
        "test_metrics.mean_rmse_deg_macro",
        "test_metrics.mean_rmse_macro",
    )

    normalized["val_primary_metric"] = val_primary
    normalized["test_primary_metric"] = test_primary
    normalized["val_rmse"] = val_rmse
    normalized["test_rmse"] = test_rmse

    missing_metric_fields: list[str] = []
    if val_rmse is None:
        missing_metric_fields.append("val_rmse")

    has_test_metrics = isinstance(normalized.get("test_metrics"), dict) or test_primary is not None
    if has_test_metrics and test_rmse is None:
        missing_metric_fields.append("test_rmse")

    normalized["missing_metric_fields"] = missing_metric_fields
    normalized["rmse_complete"] = len(missing_metric_fields) == 0
    return normalized
