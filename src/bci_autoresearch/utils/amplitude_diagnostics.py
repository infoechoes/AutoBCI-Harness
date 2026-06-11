from __future__ import annotations

from typing import Any


def classify_gain_status(gain: float | int | None) -> str:
    if gain is None:
        return "unknown"
    gain_value = float(gain)
    if gain_value < 0.5:
        return "severe compression"
    if gain_value < 0.8:
        return "moderate compression"
    if gain_value > 1.2:
        return "amplitude expansion"
    return "near matched"


def _per_dim_lookup(rows: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return {str(row["name"]): row for row in rows}


def build_amplitude_comparison(
    *,
    accepted_best: dict[str, Any],
    candidate: dict[str, Any],
) -> dict[str, Any]:
    accepted_lookup = _per_dim_lookup(list(accepted_best.get("per_dim", [])))
    candidate_lookup = _per_dim_lookup(list(candidate.get("per_dim", [])))
    rows: list[dict[str, Any]] = []
    for name in sorted(candidate_lookup):
        candidate_row = candidate_lookup[name]
        accepted_row = accepted_lookup.get(name, {})
        gain = candidate_row.get("gain")
        bias = candidate_row.get("bias")
        mae = candidate_row.get("mae")
        rmse = candidate_row.get("rmse")
        pearson_r = candidate_row.get("pearson_r_zero_lag")
        accepted_gain = accepted_row.get("gain")
        accepted_bias = accepted_row.get("bias")
        accepted_mae = accepted_row.get("mae")
        accepted_rmse = accepted_row.get("rmse")
        accepted_r = accepted_row.get("pearson_r_zero_lag")
        rows.append(
            {
                "name": name,
                "gain": gain,
                "bias": bias,
                "mae": mae,
                "rmse": rmse,
                "pearson_r_zero_lag": pearson_r,
                "gain_status": classify_gain_status(gain),
                "delta_gain_vs_accepted": None if gain is None or accepted_gain is None else float(gain) - float(accepted_gain),
                "delta_bias_vs_accepted": None if bias is None or accepted_bias is None else float(bias) - float(accepted_bias),
                "delta_mae_vs_accepted": None if mae is None or accepted_mae is None else float(mae) - float(accepted_mae),
                "delta_rmse_vs_accepted": None if rmse is None or accepted_rmse is None else float(rmse) - float(accepted_rmse),
                "delta_r_vs_accepted": None if pearson_r is None or accepted_r is None else float(pearson_r) - float(accepted_r),
            }
        )

    rows.sort(
        key=lambda item: (
            float("inf") if item["gain"] is None else float(item["gain"]),
            -abs(float(item["bias"])) if item["bias"] is not None else float("-inf"),
            -float(item["mae"]) if item["mae"] is not None else float("-inf"),
        )
    )
    return {
        "accepted_best_run_id": accepted_best.get("run_id"),
        "candidate_run_id": candidate.get("run_id"),
        "rows": rows,
    }


def build_amplitude_report(
    *,
    accepted_best: dict[str, Any],
    candidates: list[dict[str, Any]],
) -> dict[str, Any]:
    return {
        "accepted_best_run_id": accepted_best.get("run_id"),
        "comparisons": [
            build_amplitude_comparison(accepted_best=accepted_best, candidate=candidate)
            for candidate in candidates
        ],
    }


def format_amplitude_report_markdown(payload: dict[str, Any]) -> str:
    lines = [
        "# Phase C amplitude diagnostic",
        "",
        f"- accepted best: `{payload['accepted_best_run_id']}`",
        "",
    ]
    for comparison in payload.get("comparisons", []):
        lines.extend(
            [
                f"## {comparison['candidate_run_id']}",
                "",
                "| joint | gain | bias | r | MAE | RMSE | status | Δgain | Δbias |",
                "| --- | ---: | ---: | ---: | ---: | ---: | --- | ---: | ---: |",
            ]
        )
        for row in comparison.get("rows", []):
            lines.append(
                f"| {row['name']} | "
                f"{'-' if row['gain'] is None else f'{float(row['gain']):.4f}'} | "
                f"{'-' if row['bias'] is None else f'{float(row['bias']):.4f}'} | "
                f"{'-' if row['pearson_r_zero_lag'] is None else f'{float(row['pearson_r_zero_lag']):.4f}'} | "
                f"{'-' if row['mae'] is None else f'{float(row['mae']):.4f}'} | "
                f"{'-' if row['rmse'] is None else f'{float(row['rmse']):.4f}'} | "
                f"{row['gain_status']} | "
                f"{'-' if row['delta_gain_vs_accepted'] is None else f'{float(row['delta_gain_vs_accepted']):.4f}'} | "
                f"{'-' if row['delta_bias_vs_accepted'] is None else f'{float(row['delta_bias_vs_accepted']):.4f}'} |"
            )
        lines.append("")
    return "\n".join(lines)
