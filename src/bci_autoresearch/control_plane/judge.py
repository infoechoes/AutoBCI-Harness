from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .paths import AutoBciControlPlanePaths
from .runtime_store import append_judgment_update, write_json_atomic


def utcnow() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _as_dict(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, dict) else {}


def _as_list(value: Any) -> list[Any]:
    return list(value) if isinstance(value, list) else []


def _same_split(program: dict[str, Any], result: dict[str, Any]) -> bool:
    result_split = result.get("split_policy")
    if not isinstance(result_split, dict):
        return True
    expected = _as_dict(program.get("split_policy"))
    for key in ("unit", "frozen_train_sessions", "frozen_val_sessions", "frozen_test_sessions"):
        if result_split.get(key) != expected.get(key):
            return False
    return True


def _classify_result(result: dict[str, Any]) -> str:
    artifacts = " ".join(str(item) for item in _as_list(result.get("artifacts")))
    package_mode = str(result.get("package_mode") or "").lower()
    if "historical_safe_band" in package_mode or "historical_073" in artifacts:
        return "historical_filtered_candidate"
    if "competition" in package_mode or "competition_package" in artifacts or "wide" in package_mode:
        return "wide_official_result"
    return "unclassified_result"


def build_judge_report(
    *,
    program: dict[str, Any],
    run_id: str,
    result: dict[str, Any],
    judge_request: dict[str, Any],
) -> dict[str, Any]:
    warnings: list[str] = []
    failures: list[str] = []
    metrics = _as_dict(result.get("metrics"))
    primary = str(_as_dict(program.get("metrics")).get("primary") or "").strip()
    if primary and primary not in metrics:
        failures.append(f"missing primary metric: {primary}")
    if "director_scratchpad" in judge_request:
        failures.append("judge_request leaked director_scratchpad")
    if not _same_split(program, result):
        failures.append("result split_policy differs from frozen Program")
    if "confusion_matrix" not in metrics and "confusion_matrix" not in result:
        warnings.append("missing confusion_matrix for gait binary reproducibility review")

    result_classification = _classify_result(result)
    if result_classification == "historical_filtered_candidate":
        warnings.append("historical safe-band result must not be mixed with wide-official results")
    if result_classification == "wide_official_result":
        warnings.append("wide-official result is the relevant baseline for external reproduction")

    if any("split_policy" in item or "scratchpad" in item for item in failures):
        verdict = "fail_policy_violation"
    elif failures:
        verdict = "fail_metric_invalid"
    elif warnings:
        verdict = "pass_with_warnings"
    else:
        verdict = "pass"

    return {
        "report_id": f"judge-{run_id}",
        "recorded_at": utcnow(),
        "run_id": run_id,
        "program_id": str(program.get("program_id") or ""),
        "verdict": verdict,
        "policy_check": "failed" if verdict == "fail_policy_violation" else "passed",
        "metric_check": "failed" if verdict == "fail_metric_invalid" else "passed",
        "split_check": "failed" if any("split_policy" in item for item in failures) else "passed",
        "artifact_check": "warning" if warnings else "passed",
        "result_classification": result_classification,
        "failures": failures,
        "reproducibility_warnings": warnings,
        "recommended_next_action": "request_amendment_or_rerun" if failures else "continue_with_caution" if warnings else "eligible_for_review",
    }


def write_judge_report(paths: AutoBciControlPlanePaths, report: dict[str, Any]) -> Path:
    run_id = str(report.get("run_id") or "unknown-run").strip() or "unknown-run"
    target = paths.judge_reports_dir / f"{run_id}.json"
    write_json_atomic(target, report)
    append_judgment_update(
        paths.judgment_updates,
        {
            "recorded_at": str(report.get("recorded_at") or utcnow()),
            "run_id": run_id,
            "topic_id": str(report.get("program_id") or ""),
            "hypothesis_id": f"judge_{run_id}",
            "outcome": str(report.get("verdict") or ""),
            "reason": "; ".join(str(item) for item in report.get("failures") or report.get("reproducibility_warnings") or []),
            "queue_update": "judge_report_recorded",
            "next_recommended_action": str(report.get("recommended_next_action") or ""),
        },
    )
    return target
