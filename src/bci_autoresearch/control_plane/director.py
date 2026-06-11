"""Director agent for AutoResearch.

Reads the results of a completed campaign, uses an LLM to reason about
what to try next, and writes new program.current.md + tracks manifest
for the Executor (Codex) to pick up.

Usage:
    autobci-agent direct [--repo-root PATH]
"""
from __future__ import annotations

import json
import os
import shlex
import subprocess
import tempfile
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .paths import AutoBciControlPlanePaths, get_control_plane_paths
from .program_contract import (
    ProgramContract,
    ProgramContractError,
    build_round_program_text,
    extract_track_prefix,
    read_program_contract,
)
from .runtime_store import append_jsonl, read_json, write_json_atomic


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

GAIT_PHASE_SEARCH_FALLBACK_QUERIES = [
    "gait EEG timing window length support swing decoding attention",
    "premovement EEG gait phase decoding fixed lag attention pooling",
    "temporal attention masked pooling gait decoding strict causal",
]
PRECHECK_IGNORED_FLAGS = {
    "--output-json",
    "--output_json",
    "--preflight-only",
    "--window-seconds",
    "--global-lag-ms",
}
DIRECTOR_CALL_TIMEOUT_SECONDS = 600
DIRECTOR_MAX_ATTEMPTS = 2
DIRECTOR_BLOCKED_MESSAGE = "所有方向都接近随机，需要人工介入或新的研究假设。"
KNOWN_TASK_PREFIXES = (
    "walk_matched_joints_",
    "gait_phase_eeg_",
    "relative_origin_xyz_",
    "moonshot_upper_bound_",
    "incubation_",
    "phase_conditioned_",
    "kinematics_only_",
)

@dataclass
class TrackSummary:
    track_id: str
    runner_family: str
    best_metric: float | None
    decision: str
    iterations: int
    best_formal_metric: float | None = None
    best_smoke_metric: float | None = None


@dataclass
class CampaignRetrospective:
    campaign_id: str
    stop_reason: str
    total_iterations: int
    tracks: list[TrackSummary]
    all_near_chance: bool
    best_overall_metric: float | None
    best_track_id: str | None
    hypotheses_tried: list[str]
    search_evidence: list[dict[str, Any]]
    current_problem_statement: str
    constitution_summary: str
    previous_program_text: str
    primary_metric_name: str = ""


def _load_program_contract(paths: AutoBciControlPlanePaths) -> ProgramContract:
    try:
        return read_program_contract(paths.program_doc)
    except ProgramContractError as exc:
        raise RuntimeError(str(exc)) from exc


def _program_boundary_section(contract: ProgramContract) -> str:
    forbidden_prefixes = " / ".join(sorted({"非 " + prefix.rstrip("_") for prefix in contract.allowed_track_prefixes}))
    return "\n".join(
        [
            "## Current Program Boundary (IMMUTABLE for this cycle)",
            f"- Program ID: {contract.program_id}",
            f"- Title: {contract.title}",
            f"- Problem family: {contract.problem_family}",
            f"- Primary metric: {contract.primary_metric_name}",
            f"- Allowed track prefixes: {', '.join(contract.allowed_track_prefixes)}",
            f"- Allowed dataset names: {', '.join(contract.allowed_dataset_names)}",
            f"- Current reliable best: {contract.current_reliable_best}",
            "- Forbidden changes: 切到不属于上述前缀、数据集、主指标的新任务。",
            f"- Reminder: {forbidden_prefixes or '不得跨任务切换'}",
        ]
    )


def _append_program_boundary_violation(
    paths: AutoBciControlPlanePaths,
    *,
    contract: ProgramContract,
    campaign_id: str,
    attempted_track_id: str,
    attempted_dataset_name: str = "",
    attempted_primary_metric_name: str = "",
) -> None:
    attempted_prefix = extract_track_prefix(attempted_track_id)
    prefix_label = attempted_prefix.rstrip("_") or attempted_track_id or "未知任务"
    message = f"Director 试图切换到 {prefix_label} 任务，被当前 Program 边界规则拦截。请用 program start 开启新任务。"
    append_jsonl(
        paths.supervisor_events,
        {
            "recorded_at": _utcnow(),
            "event": "program_boundary_violation",
            "program_id": contract.program_id,
            "campaign_id": campaign_id,
            "attempted_track_id": attempted_track_id,
            "attempted_prefix": attempted_prefix,
            "expected_prefixes": list(contract.allowed_track_prefixes),
            "attempted_dataset_name": attempted_dataset_name,
            "expected_dataset_names": list(contract.allowed_dataset_names),
            "attempted_primary_metric_name": attempted_primary_metric_name,
            "expected_primary_metric_name": contract.primary_metric_name,
            "message": message,
        },
    )
    runtime = read_json(paths.runtime_state, {}) or {}
    runtime.update(
        {
            "runtime_status": "blocked",
            "supervisor_status": "idle_blocked",
            "director_status": "blocked",
            "program_id": contract.program_id,
            "program_status": "closed",
            "last_program_boundary_violation_message": message,
        }
    )
    write_json_atomic(paths.runtime_state, runtime)


def _is_explicit_cross_task_track(track_id: str, contract: ProgramContract) -> bool:
    tid = str(track_id or "").strip()
    if not tid:
        return False
    if any(tid.startswith(prefix) for prefix in contract.allowed_track_prefixes):
        return False
    return any(tid.startswith(prefix) for prefix in KNOWN_TASK_PREFIXES)


def _ensure_round_program_text(
    text: str,
    *,
    program_id: str,
    source_campaign_id: str,
    next_campaign_id: str,
) -> str:
    stripped = text.lstrip()
    if stripped.startswith("---\n") and "program_id:" in stripped[:300]:
        return text
    return build_round_program_text(
        text,
        program_id=program_id,
        source_campaign_id=source_campaign_id,
        next_campaign_id=next_campaign_id,
    )


@dataclass
class DirectorResult:
    next_campaign_id: str
    diagnosis: str
    reasoning: str
    next_program_text: str
    next_tracks: list[dict[str, Any]]
    research_tree_update: str
    source_campaign_id: str = ""
    search_queries: list[str] = field(default_factory=list)
    confidence: str = "medium"
    decision_source: str = "codex_sdk"
    program_id: str = ""


# ---------------------------------------------------------------------------
# Step 1: Analyze previous campaign results
# ---------------------------------------------------------------------------

def _infer_family(track_id: str) -> str:
    tid = track_id.lower()
    for token in ("tree_xgboost", "cnn_lstm", "state_space", "conformer", "tcn", "gru", "lstm", "ridge", "xgboost", "linear_logistic"):
        if token in tid:
            return token
    return "unknown"


def _metric_name_from_row(row: dict[str, Any], metric_payload: dict[str, Any]) -> str:
    for source in (metric_payload, row):
        metric_name = str(source.get("primary_metric_name") or "").strip()
        if metric_name:
            return metric_name
    return ""


def _is_near_chance(metric_name: str, value: float | None) -> bool:
    if value is None:
        return True
    normalized = metric_name.strip().lower()
    if "balanced_accuracy" in normalized or normalized.endswith("accuracy"):
        return value < 0.55
    if "pearson" in normalized or normalized.endswith("_r") or "_cc" in normalized:
        return abs(value) < 0.10
    return value < 0.05


def _is_balanced_accuracy_metric(metric_name: str) -> bool:
    normalized = metric_name.strip().lower()
    return "balanced_accuracy" in normalized or normalized.endswith("accuracy") or normalized == ""


def _is_stalled_retrospective(retro: CampaignRetrospective) -> bool:
    best_metric = retro.best_overall_metric
    if retro.all_near_chance:
        return True
    if best_metric is None:
        return True
    if _is_balanced_accuracy_metric(retro.primary_metric_name):
        if retro.total_iterations >= 4 and best_metric < 0.60:
            return True
    stop_reason = retro.stop_reason.strip().lower()
    return (
        stop_reason in {"no_improvement", "patience_exhausted", "stagnant"}
        and retro.total_iterations >= 4
        and best_metric < 0.62
    )


def analyze_campaign_results(paths: AutoBciControlPlanePaths) -> CampaignRetrospective:
    status = read_json(paths.autoresearch_status, {}) or {}
    campaign_id = status.get("campaign_id", "unknown")

    # Read ledger, filter to this campaign
    ledger_rows: list[dict[str, Any]] = []
    if paths.experiment_ledger.exists():
        for line in paths.experiment_ledger.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
                if row.get("campaign_id") == campaign_id:
                    ledger_rows.append(row)
            except json.JSONDecodeError:
                continue

    # Build per-track summaries
    track_best: dict[str, TrackSummary] = {}
    hypotheses: list[str] = []
    primary_metric_name = ""
    for row in ledger_rows:
        tid = row.get("track_id") or ""
        final_metrics = row.get("final_metrics") or {}
        smoke_metrics = row.get("smoke_metrics") or {}
        selected_metrics = final_metrics or smoke_metrics or {}
        metric = selected_metrics.get("val_primary_metric")
        formal_metric = final_metrics.get("val_primary_metric")
        smoke_metric = smoke_metrics.get("val_primary_metric")
        primary_metric_name = primary_metric_name or _metric_name_from_row(row, selected_metrics)
        decision = row.get("decision", "")
        hyp = row.get("hypothesis") or row.get("track_goal") or ""
        if hyp and hyp not in hypotheses:
            hypotheses.append(hyp)

        if tid not in track_best:
            track_best[tid] = TrackSummary(
                track_id=tid,
                runner_family=_infer_family(tid),
                best_metric=formal_metric if formal_metric is not None else smoke_metric,
                decision=decision,
                iterations=1,
                best_formal_metric=formal_metric,
                best_smoke_metric=smoke_metric,
            )
        else:
            ts = track_best[tid]
            ts.iterations += 1
            ts.decision = decision
            if formal_metric is not None and (ts.best_formal_metric is None or formal_metric > ts.best_formal_metric):
                ts.best_formal_metric = formal_metric
            if smoke_metric is not None and (ts.best_smoke_metric is None or smoke_metric > ts.best_smoke_metric):
                ts.best_smoke_metric = smoke_metric
            ts.best_metric = ts.best_formal_metric if ts.best_formal_metric is not None else ts.best_smoke_metric

    tracks = list(track_best.values())
    metrics = [t.best_metric for t in tracks if t.best_metric is not None]
    best_overall = max(metrics) if metrics else None
    best_tid = None
    for t in tracks:
        if t.best_metric == best_overall:
            best_tid = t.track_id
            break

    all_near_chance = all(
        _is_near_chance(primary_metric_name, m) for m in metrics
    ) if metrics else True

    # Read search evidence
    evidence: list[dict[str, Any]] = []
    if paths.research_evidence.exists():
        for line in paths.research_evidence.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line:
                try:
                    evidence.append(json.loads(line))
                except json.JSONDecodeError:
                    continue

    # Read previous program and constitution
    prev_program = ""
    if paths.program_current.exists():
        prev_program = paths.program_current.read_text(encoding="utf-8")

    constitution = ""
    const_path = paths.repo_root / "docs" / "CONSTITUTION.md"
    if const_path.exists():
        constitution = const_path.read_text(encoding="utf-8")

    problem_statement = status.get("current_command") or "未定义"

    return CampaignRetrospective(
        campaign_id=campaign_id,
        stop_reason=status.get("stop_reason", "unknown"),
        total_iterations=len(ledger_rows),
        tracks=tracks,
        all_near_chance=all_near_chance,
        best_overall_metric=best_overall,
        best_track_id=best_tid,
        hypotheses_tried=hypotheses,
        search_evidence=evidence[-10:],
        current_problem_statement=problem_statement,
        constitution_summary=constitution,
        previous_program_text=prev_program,
        primary_metric_name=primary_metric_name,
    )


# ---------------------------------------------------------------------------
# Step 2: Build LLM prompt
# ---------------------------------------------------------------------------

def build_director_prompt(retro: CampaignRetrospective, paths: AutoBciControlPlanePaths) -> str:
    contract = _load_program_contract(paths)
    # Track results table
    track_lines = []
    for t in sorted(retro.tracks, key=lambda x: -(x.best_metric or 0)):
        m = f"{t.best_metric:.1%}" if t.best_metric is not None else "N/A"
        track_lines.append(f"  {t.track_id}: {m} ({t.runner_family}, {t.iterations} iters, decision={t.decision})")
    track_table = "\n".join(track_lines) if track_lines else "  (no tracks)"

    # Research tree
    research_tree = ""
    if paths.research_tree.exists():
        research_tree = paths.research_tree.read_text(encoding="utf-8")[:2000]

    diagnosis_hint = ""
    if retro.all_near_chance:
        diagnosis_hint = """
IMPORTANT: The campaign remained near chance on its primary metric. This means
the current approach is fundamentally not working. Do NOT suggest more of the same.
Stay within immutable constraints: do not propose changing alignment, split rules,
or strict-causality guarantees.
Consider instead: wrong features? wrong preprocessing? wrong target representation?
insufficient data coverage? wrong model architecture entirely?
"""

    return f"""You are the research director for AutoBCI, a brain-computer interface research system.
Your job: analyze the results of the previous campaign and decide what the next campaign should focus on.

## Constitution (immutable constraints)
{retro.constitution_summary}

{_program_boundary_section(contract)}

## Previous Campaign: {retro.campaign_id}
- Stop reason: {retro.stop_reason}
- Total iterations: {retro.total_iterations}
- Primary metric: {retro.primary_metric_name or "N/A"}
- Best overall metric: {f"{retro.best_overall_metric:.1%}" if retro.best_overall_metric else "N/A"} ({retro.best_track_id or "N/A"})
- All near chance: {retro.all_near_chance}
{diagnosis_hint}

## Per-Track Results (sorted by metric, descending)
{track_table}

## Previous Program Instructions
{retro.previous_program_text[:1500]}

## Research Tree Context
{research_tree[:1500]}

## Available Runner Families
feature_lstm, feature_gru, feature_tcn, feature_cnn_lstm, feature_state_space_lite,
feature_conformer_lite, ridge, xgboost, linear_logistic, tree_xgboost

## Your Task
1. Diagnose: Why did the previous campaign not make progress?
2. Decide: What should the next campaign focus on? Be specific.
3. Generate: 2-4 concrete tracks for the next campaign.
4. Write: A new program.current.md that explains the next campaign's focus.

For each track, provide:
- track_id (snake_case, unique)
- runner_family (from the list above)
- internet_research_enabled (true if Executor should be allowed to search before coding/running)
- track_goal (one sentence)
- smoke_command (must reference existing scripts in scripts/ directory)
- formal_command (same script, full dataset)

## Output Format
Respond with a JSON object (in a ```json code block):
```json
{{
  "reasoning": "Your step-by-step reasoning about what happened and what to try next",
  "diagnosis": "One-paragraph diagnosis of why the previous campaign failed/stagnated",
  "next_program_text": "Full text for the new program.current.md",
  "next_tracks": [
    {{
      "track_id": "...",
      "topic_id": "gait_phase_eeg_classification",
      "runner_family": "...",
      "internet_research_enabled": true,
      "track_goal": "...",
      "promotion_target": "gait_phase_eeg_classification",
      "smoke_command": "...",
      "formal_command": "...",
      "allowed_change_scope": ["scripts", "src/bci_autoresearch/models", "src/bci_autoresearch/features", "src/bci_autoresearch/eval"]
    }}
  ],
  "research_tree_update": "Text to append to the research tree summarizing this decision",
  "search_queries": ["optional web search queries if needed"],
  "confidence": "high|medium|low"
}}
```
"""


# ---------------------------------------------------------------------------
# Step 3: Call LLM
# ---------------------------------------------------------------------------

def call_llm(
    prompt: str,
    paths: AutoBciControlPlanePaths | None = None,
    *,
    source_campaign_id: str = "",
) -> str:
    """Call Codex SDK to run Director reasoning. Returns raw response text.

    Uses a Codex thread (via the Node.js SDK) to get the Director's analysis.
    If a previous Director thread exists, resumes it for context continuity.
    """
    resolved = paths or get_control_plane_paths()
    director_state = read_json(resolved.director_reasoning, {}) or {}
    previous_thread_id = director_state.get("codex_thread_id")

    # Write prompt to temp file for the Node script to read
    prompt_path = resolved.monitor_dir / "director_prompt.txt"
    prompt_path.write_text(prompt, encoding="utf-8")

    script_path = resolved.repo_root / "tools" / "autoresearch" / "director_runner.mjs"
    if not script_path.exists():
        raise RuntimeError(f"Codex Director runner not found: {script_path}")

    env = os.environ.copy()
    env["NODE_PATH"] = str(resolved.repo_root / "tools" / "autoresearch" / "node_modules")
    env["DIRECTOR_PROMPT_PATH"] = str(prompt_path)
    env["DIRECTOR_OUTPUT_PATH"] = str(resolved.monitor_dir / "director_response.txt")
    env["DIRECTOR_REPO_ROOT"] = str(resolved.repo_root)
    env["DIRECTOR_THREAD_ID"] = str(previous_thread_id or "")
    response_path = resolved.monitor_dir / "director_response.txt"
    last_error: RuntimeError | None = None

    for attempt_index in range(1, DIRECTOR_MAX_ATTEMPTS + 1):
        _update_director_runtime_state(
            resolved,
            director_status="running",
            last_director_attempt_at=_utcnow(),
            director_retry_count=attempt_index - 1,
        )
        if response_path.exists():
            response_path.unlink()
        try:
            completed = subprocess.run(
                ["node", str(script_path)],
                cwd=str(resolved.repo_root / "tools" / "autoresearch"),
                env=env,
                capture_output=True,
                text=True,
                timeout=DIRECTOR_CALL_TIMEOUT_SECONDS,
            )
            if completed.returncode != 0:
                stderr = completed.stderr[:500] if completed.stderr else "no stderr"
                raise RuntimeError(f"Codex Director thread failed (rc={completed.returncode}): {stderr}")
            if not response_path.exists():
                raise RuntimeError("Codex Director produced no response file")

            response_text = response_path.read_text(encoding="utf-8")
            if not response_text.strip():
                raise RuntimeError("Codex Director response was empty")
        except subprocess.TimeoutExpired as exc:
            last_error = RuntimeError(f"Codex Director timed out after {DIRECTOR_CALL_TIMEOUT_SECONDS} seconds")
        except RuntimeError as exc:
            last_error = exc
        else:
            # Extract thread_id from stdout if present (for future resume)
            for line in completed.stdout.splitlines():
                if line.startswith("DIRECTOR_THREAD_ID="):
                    thread_id = line.split("=", 1)[1].strip()
                    if thread_id:
                        director_state["codex_thread_id"] = thread_id
                        write_json_atomic(resolved.director_reasoning, director_state)
            _update_director_runtime_state(
                resolved,
                director_status="running",
                last_director_attempt_at=_utcnow(),
                last_director_error="",
                director_retry_count=attempt_index - 1,
            )
            return response_text

        assert last_error is not None
        _update_director_runtime_state(
            resolved,
            director_status="running",
            last_director_attempt_at=_utcnow(),
            last_director_error=str(last_error)[:300],
            director_retry_count=attempt_index,
        )
        _append_director_attempt_error(
            resolved,
            source_campaign_id=source_campaign_id,
            error_message=str(last_error),
            attempt_index=attempt_index,
        )
        if attempt_index >= DIRECTOR_MAX_ATTEMPTS:
            raise last_error

    raise RuntimeError("Codex Director failed without a terminal error")


def _update_director_runtime_state(paths: AutoBciControlPlanePaths, **updates: Any) -> None:
    runtime = read_json(paths.runtime_state, {}) or {}
    runtime.update(updates)
    write_json_atomic(paths.runtime_state, runtime)


def _append_director_attempt_error(
    paths: AutoBciControlPlanePaths,
    *,
    source_campaign_id: str,
    error_message: str,
    attempt_index: int,
) -> None:
    append_jsonl(
        paths.supervisor_events,
        {
            "recorded_at": _utcnow(),
            "event": "director_attempt_error",
            "source_campaign_id": source_campaign_id,
            "target_campaign_id": "",
            "decision_source": "codex_sdk",
            "attempt_index": attempt_index,
            "error": error_message[:300],
        },
    )


def _mark_research_blocked(
    paths: AutoBciControlPlanePaths,
    *,
    source_campaign_id: str,
    reason: str = DIRECTOR_BLOCKED_MESSAGE,
) -> None:
    _update_director_runtime_state(
        paths,
        director_status="blocked",
        runtime_status="completed",
        supervisor_status="idle_blocked",
        last_director_attempt_at=_utcnow(),
        last_director_error=reason[:300],
    )
    append_jsonl(
        paths.supervisor_events,
        {
            "recorded_at": _utcnow(),
            "event": "research_blocked",
            "source_campaign_id": source_campaign_id,
            "target_campaign_id": "",
            "decision_source": "continue_best",
            "message": reason,
        },
    )


# ---------------------------------------------------------------------------
# Step 4: Parse LLM response
# ---------------------------------------------------------------------------

def parse_director_response(text: str) -> DirectorResult:
    """Extract structured JSON from LLM response text."""
    import re

    # Try to find JSON in code block
    match = re.search(r"```json\s*\n(.*?)\n```", text, re.DOTALL)
    if match:
        raw = match.group(1)
    else:
        # Try bare JSON
        match = re.search(r"\{.*\}", text, re.DOTALL)
        raw = match.group(0) if match else text

    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        # Fallback: return a minimal result with the raw text as diagnosis
        return DirectorResult(
            next_campaign_id=f"director-fallback-{int(time.time())}",
            diagnosis=f"LLM response could not be parsed. Raw:\n{text[:500]}",
            reasoning="parse_failed",
            next_program_text="",
            next_tracks=[],
            research_tree_update="Director parse failure.",
            confidence="low",
        )

    campaign_id = f"director-{int(time.time())}"

    return DirectorResult(
        next_campaign_id=campaign_id,
        diagnosis=data.get("diagnosis", ""),
        reasoning=data.get("reasoning", ""),
        next_program_text=data.get("next_program_text", ""),
        next_tracks=data.get("next_tracks", []),
        research_tree_update=data.get("research_tree_update", ""),
        search_queries=data.get("search_queries", []),
        confidence=data.get("confidence", "medium"),
        decision_source="codex_sdk",
    )


def _extract_script_path(command: str, repo_root: Path) -> Path | None:
    for part in shlex.split(command):
        if part.endswith(".py") and "/" in part:
            return repo_root / part
    return None


def _build_preflight_command(command: str, output_path: Path) -> list[str] | None:
    argv = shlex.split(command)
    if not argv:
        return None
    output_flag_names = ("--output-json", "--output_json")
    output_flag_index = None
    for flag_name in output_flag_names:
        if flag_name in argv:
            output_flag_index = argv.index(flag_name)
            break
    if output_flag_index is None:
        argv.extend(["--output-json", str(output_path)])
    else:
        if output_flag_index + 1 >= len(argv):
            return None
        argv[output_flag_index + 1] = str(output_path)
    if "--preflight-only" not in argv:
        argv.append("--preflight-only")
    return argv


def _extract_flag_value(command: str, flag_name: str) -> str | None:
    argv = shlex.split(command)
    for index, token in enumerate(argv):
        if token == flag_name:
            if index + 1 >= len(argv):
                return None
            return argv[index + 1]
        if token.startswith(f"{flag_name}="):
            return token.split("=", 1)[1]
    return None


def _has_valid_float_flag(command: str, flag_name: str) -> bool:
    value = _extract_flag_value(command, flag_name)
    if value is None:
        return True
    try:
        float(value)
    except ValueError:
        return False
    return True


def _preflight_signature(command: str) -> tuple[str, ...] | None:
    argv = shlex.split(command)
    if not argv:
        return None
    normalized: list[str] = []
    index = 0
    while index < len(argv):
        token = argv[index]
        if token in PRECHECK_IGNORED_FLAGS:
            index += 2 if token != "--preflight-only" else 1
            continue
        if any(token.startswith(f"{flag}=") for flag in PRECHECK_IGNORED_FLAGS if flag != "--preflight-only"):
            index += 1
            continue
        normalized.append(token)
        index += 1
    return tuple(normalized)


def _run_preflight(command: str, paths: AutoBciControlPlanePaths) -> bool:
    script_path = _extract_script_path(command, paths.repo_root)
    if script_path is None or not script_path.exists():
        return False
    with tempfile.TemporaryDirectory(prefix="autobci-director-preflight-") as temp_dir:
        output_path = Path(temp_dir) / "preflight.json"
        argv = _build_preflight_command(command, output_path)
        if argv is None:
            return False
        env = os.environ.copy()
        env.setdefault("PYTHONPATH", str(paths.repo_root / "src"))
        env["AUTOBCI_ROOT"] = str(paths.repo_root)
        completed = subprocess.run(
            argv,
            cwd=paths.repo_root,
            env=env,
            capture_output=True,
            text=True,
            timeout=90,
        )
        return completed.returncode == 0


def _tracks_respect_program_boundary(
    tracks: list[dict[str, Any]],
    paths: AutoBciControlPlanePaths,
    *,
    campaign_id: str,
) -> bool:
    contract = _load_program_contract(paths)
    all_valid = True
    for track in tracks:
        track_id = str(track.get("track_id") or "").strip()
        if not track_id:
            continue
        if not _is_explicit_cross_task_track(track_id, contract):
            continue
        _append_program_boundary_violation(
            paths,
            contract=contract,
            campaign_id=campaign_id,
            attempted_track_id=track_id,
        )
        all_valid = False
    return all_valid


# ---------------------------------------------------------------------------
# Step 5: Validate tracks
# ---------------------------------------------------------------------------

def validate_tracks(
    tracks: list[dict[str, Any]],
    paths: AutoBciControlPlanePaths,
) -> list[dict[str, Any]]:
    """Validate generated tracks. Returns only tracks that pass preflight."""
    contract = _load_program_contract(paths)
    valid = []
    preflight_cache: dict[tuple[str, ...], bool] = {}
    for track in tracks:
        smoke_cmd = track.get("smoke_command", "")
        formal_cmd = track.get("formal_command", "")
        if not smoke_cmd:
            continue
        if not formal_cmd:
            continue

        # Ensure required fields
        if not track.get("track_id"):
            continue
        track_id = str(track.get("track_id") or "").strip()
        if _is_explicit_cross_task_track(track_id, contract):
            _append_program_boundary_violation(
                paths,
                contract=contract,
                campaign_id=str((read_json(paths.autoresearch_status, {}) or {}).get("campaign_id") or ""),
                attempted_track_id=track_id,
            )
            continue
        if not _has_valid_float_flag(smoke_cmd, "--window-seconds"):
            continue
        if not _has_valid_float_flag(smoke_cmd, "--global-lag-ms"):
            continue
        if not _has_valid_float_flag(formal_cmd, "--window-seconds"):
            continue
        if not _has_valid_float_flag(formal_cmd, "--global-lag-ms"):
            continue

        smoke_signature = _preflight_signature(smoke_cmd)
        formal_signature = _preflight_signature(formal_cmd)
        smoke_ok = preflight_cache.get(smoke_signature) if smoke_signature is not None else None
        formal_ok = preflight_cache.get(formal_signature) if formal_signature is not None else None
        if smoke_ok is None:
            smoke_ok = _run_preflight(smoke_cmd, paths)
            if smoke_signature is not None:
                preflight_cache[smoke_signature] = smoke_ok
        if not smoke_ok:
            continue
        if formal_ok is None:
            formal_ok = _run_preflight(formal_cmd, paths)
            if formal_signature is not None:
                preflight_cache[formal_signature] = formal_ok
        if not formal_ok:
            continue

        valid.append(track)

    return valid


def _looks_like_gait_phase_attention_profile(paths: AutoBciControlPlanePaths) -> bool:
    manifest_path = paths.repo_root / "tools" / "autoresearch" / "tracks.gait_phase_eeg_attention.json"
    program_path = paths.repo_root / "tools" / "autoresearch" / "program.gait_phase.eeg.attention.current.md"
    return manifest_path.exists() and program_path.exists()


def _is_gait_phase_campaign(retro: CampaignRetrospective) -> bool:
    corpus = "\n".join(
        [
            retro.campaign_id,
            retro.current_problem_statement,
            retro.previous_program_text,
            *(track.track_id for track in retro.tracks),
        ]
    ).lower()
    return any(token in corpus for token in ("gait_phase_eeg", "gait-phase-eeg", "步态脑电"))


def _build_gait_phase_attention_fallback(
    retro: CampaignRetrospective,
    paths: AutoBciControlPlanePaths,
    *,
    error_message: str | None = None,
) -> DirectorResult | None:
    if not _is_gait_phase_campaign(retro):
        return None
    if not _looks_like_gait_phase_attention_profile(paths):
        return None
    if "feature_gru_attention" in retro.previous_program_text or "feature_tcn_attention" in retro.previous_program_text:
        return None
    if not _is_stalled_retrospective(retro):
        return None

    program_path = paths.repo_root / "tools" / "autoresearch" / "program.gait_phase.eeg.attention.current.md"
    manifest_path = paths.repo_root / "tools" / "autoresearch" / "tracks.gait_phase_eeg_attention.json"
    try:
        next_program_text = program_path.read_text(encoding="utf-8")
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None

    raw_tracks = manifest.get("tracks") if isinstance(manifest, dict) else []
    if not isinstance(raw_tracks, list) or not raw_tracks:
        return None

    next_tracks: list[dict[str, Any]] = []
    for item in raw_tracks:
        if not isinstance(item, dict):
            continue
        track = dict(item)
        track.setdefault("internet_research_enabled", True)
        next_tracks.append(track)

    if not next_tracks:
        return None

    best_metric_text = (
        f"{retro.best_overall_metric:.1%}"
        if retro.best_overall_metric is not None
        else "N/A"
    )
    fallback_reason = "Claude Director 当前不可用，切到仓库内置的 attention 备用方案。"
    if error_message:
        fallback_reason = f"{fallback_reason} 原因：{error_message[:180]}"
    return DirectorResult(
        next_campaign_id=f"gait-phase-eeg-attention-{int(time.time())}",
        diagnosis=(
            f"上一轮 plain 步态脑电 timing scan 在正式口径最好只有 {best_metric_text}，"
            "连续停在略高于随机但没有形成稳定突破；继续在 plain GRU/TCN 上细扫收益低，"
            "下一轮改切 masked safe band attention，并打开联网搜索去补 timing 与 attention 的外部证据。"
        ),
        reasoning=(
            "Director fallback triggered because the completed gait-phase campaign looked stalled. "
            f"{fallback_reason} "
            "The repo already contains a prepared attention profile with signed lag coverage, "
            "internet research enabled tracks, and scripts that preflight successfully, so the safest "
            "next move is to promote that prepared branch instead of fabricating a new manifest."
        ),
        next_program_text=next_program_text,
        next_tracks=next_tracks,
        research_tree_update=(
            f"上一轮 plain gait EEG timing scan 最好正式 balanced_accuracy 只有 {best_metric_text}，"
            "没有形成稳定突破。Director 触发 attention 备用分支：切到 masked safe band "
            "attention 的 GRU/TCN timing scan，并允许 Executor 先搜 gait timing / temporal attention 证据再执行。"
        ),
        search_queries=list(GAIT_PHASE_SEARCH_FALLBACK_QUERIES),
        confidence="medium",
        decision_source="fallback",
    )


def _build_continue_best_fallback(
    retro: CampaignRetrospective,
    paths: AutoBciControlPlanePaths,
    *,
    error_message: str | None = None,
) -> DirectorResult | None:
    manifest = read_json(paths.track_manifest, {}) or {}
    raw_tracks = manifest.get("tracks") if isinstance(manifest.get("tracks"), list) else []
    if not raw_tracks:
        return None
    manifest_tracks = {
        str(track.get("track_id") or "").strip(): dict(track)
        for track in raw_tracks
        if isinstance(track, dict) and str(track.get("track_id") or "").strip()
    }
    candidate_summaries = [
        track
        for track in sorted(retro.tracks, key=lambda item: -(item.best_metric or float("-inf")))
        if track.best_metric is not None and not _is_near_chance(retro.primary_metric_name, track.best_metric)
    ]
    selected_tracks: list[dict[str, Any]] = []
    selected_ids: list[str] = []
    for summary in candidate_summaries:
        track_payload = manifest_tracks.get(summary.track_id)
        if not track_payload:
            continue
        if not track_payload.get("smoke_command") or not track_payload.get("formal_command"):
            continue
        selected_tracks.append(track_payload)
        selected_ids.append(summary.track_id)
        if len(selected_tracks) >= 2:
            break
    if not selected_tracks:
        return None

    best_metric_text = (
        f"{candidate_summaries[0].best_metric:.1%}"
        if candidate_summaries and candidate_summaries[0].best_metric is not None
        else "N/A"
    )
    fallback_note = "Director 降级到 continue-best：先沿当前最可信的最好结果继续，不让研究链路空转。"
    if error_message:
        fallback_note = f"{fallback_note} 原因：{error_message[:180]}"
    previous_program_text = retro.previous_program_text.strip() or "# 当前执行合同\n\n继续沿上一轮最可信方向推进。"
    next_program_text = (
        previous_program_text.rstrip()
        + "\n\n## Director 降级续跑\n\n"
        + "- Codex Director 当前不可用，先沿上一轮当前最可信的最好结果继续。\n"
        + f"- 当前保守续跑对象：{', '.join(selected_ids)}。\n"
    )
    return DirectorResult(
        next_campaign_id=f"continue-best-{int(time.time())}",
        diagnosis=(
            f"Director 当前不可用，先保守沿上一轮当前最可信的最好结果继续。"
            f"上一轮最可信方向最好成绩约为 {best_metric_text}，先复用已验证可跑的 track，避免系统停在空闲状态。"
        ),
        reasoning=(
            "Director fallback continue-best triggered because Codex reasoning did not return a usable answer. "
            f"{fallback_note} "
            "This fallback only reuses previously materialized tracks whose metrics were not near chance, "
            "and keeps the search space narrow to reduce wasted budget."
        ),
        next_program_text=next_program_text,
        next_tracks=selected_tracks,
        research_tree_update=(
            f"Director 当前不可用，因此触发降级续跑：暂时沿上一轮当前最可信的最好结果继续，"
            f"保留 {', '.join(selected_ids)} 这几条已验证可执行的方向，等待下一次完整 Director 推理恢复。"
        ),
        source_campaign_id=retro.campaign_id,
        confidence="low",
        decision_source="continue_best",
    )


def _build_best_effort_fallback(
    retro: CampaignRetrospective,
    paths: AutoBciControlPlanePaths,
    *,
    error_message: str | None = None,
) -> DirectorResult | None:
    domain_fallback = _build_gait_phase_attention_fallback(
        retro,
        paths,
        error_message=error_message,
    )
    if domain_fallback is not None:
        return domain_fallback
    return _build_continue_best_fallback(
        retro,
        paths,
        error_message=error_message,
    )


# ---------------------------------------------------------------------------
# Step 6: Write next campaign
# ---------------------------------------------------------------------------

def _utcnow() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def write_next_campaign(
    result: DirectorResult,
    paths: AutoBciControlPlanePaths,
) -> None:
    """Write Director output to disk for Executor to pick up."""
    contract = _load_program_contract(paths)
    result.program_id = result.program_id or contract.program_id

    # Write program.current.md
    if result.next_program_text:
        paths.program_current.write_text(result.next_program_text, encoding="utf-8")

    # Write tracks manifest
    if result.next_tracks:
        manifest = {
            "tracks": result.next_tracks,
            "review_cadence": "hourly",
            "director_generated": True,
            "director_campaign_id": result.next_campaign_id,
            "director_source_campaign_id": result.source_campaign_id,
            "director_decision_source": result.decision_source,
            "program_id": result.program_id,
            "director_top_3_track_ids": [
                str(track.get("track_id")).strip()
                for track in result.next_tracks[:3]
                if str(track.get("track_id")).strip()
            ],
        }
        write_json_atomic(paths.track_manifest, manifest)

    # Write director reasoning
    reasoning_path = paths.director_reasoning
    existing_reasoning = read_json(reasoning_path, {}) or {}
    reasoning_payload = {
        "recorded_at": _utcnow(),
        "source_campaign_id": result.source_campaign_id,
        "program_id": result.program_id,
        "next_campaign_id": result.next_campaign_id,
        "diagnosis": result.diagnosis,
        "reasoning": result.reasoning,
        "confidence": result.confidence,
        "decision_source": result.decision_source,
        "next_tracks_count": len(result.next_tracks),
        "next_track_ids": [
            str(track.get("track_id")).strip()
            for track in result.next_tracks
            if str(track.get("track_id")).strip()
        ],
        "top_3_track_ids": [
            str(track.get("track_id")).strip()
            for track in result.next_tracks[:3]
            if str(track.get("track_id")).strip()
        ],
        "search_queries": result.search_queries,
    }
    codex_thread_id = existing_reasoning.get("codex_thread_id")
    if isinstance(codex_thread_id, str) and codex_thread_id.strip():
        reasoning_payload["codex_thread_id"] = codex_thread_id.strip()
    write_json_atomic(reasoning_path, reasoning_payload)

    # Append to research tree
    if result.research_tree_update and paths.research_tree.exists():
        current = paths.research_tree.read_text(encoding="utf-8")
        timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M")
        update = f"\n\n### Director Decision ({timestamp})\n\n{result.research_tree_update}\n"
        paths.research_tree.write_text(current + update, encoding="utf-8")

    # Update runtime state
    runtime_path = paths.runtime_state
    runtime = read_json(runtime_path, {}) or {}
    runtime["director_status"] = "completed"
    runtime["program_id"] = result.program_id
    runtime["program_status"] = contract.status
    runtime["last_director_at"] = _utcnow()
    runtime["last_director_attempt_at"] = runtime["last_director_at"]
    runtime["last_director_diagnosis"] = result.diagnosis[:300]
    runtime["last_director_confidence"] = result.confidence
    runtime["last_director_next_campaign_id"] = result.next_campaign_id
    runtime["last_director_error"] = ""
    runtime["director_retry_count"] = 0
    write_json_atomic(runtime_path, runtime)

    # Log to supervisor events
    append_jsonl(
        paths.supervisor_events,
        {
            "recorded_at": _utcnow(),
            "event": "director_cycle",
            "program_id": result.program_id,
            "source_campaign_id": result.source_campaign_id,
            "next_campaign_id": result.next_campaign_id,
            "decision_source": result.decision_source,
            "diagnosis": result.diagnosis,
            "tracks_generated": len(result.next_tracks),
            "top_3_track_ids": [
                str(track.get("track_id")).strip()
                for track in result.next_tracks[:3]
                if str(track.get("track_id")).strip()
            ],
            "confidence": result.confidence,
        },
    )


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def run_director_cycle(
    paths: AutoBciControlPlanePaths | None = None,
) -> DirectorResult | None:
    """Run one Director reasoning cycle.

    Reads the latest campaign results, calls the LLM for analysis,
    and writes new program + tracks for the next Executor run.

    Returns the DirectorResult, or None if the cycle failed.
    """
    resolved = paths or get_control_plane_paths()

    # 1. Analyze previous campaign
    retro = analyze_campaign_results(resolved)

    if retro.total_iterations == 0:
        # No campaign data to analyze
        return None

    # 2. Build prompt
    prompt = build_director_prompt(retro, resolved)

    result: DirectorResult | None = None
    _update_director_runtime_state(
        resolved,
        director_status="running",
        last_director_attempt_at=_utcnow(),
    )

    # 3. Call LLM
    try:
        response_text = call_llm(prompt, resolved, source_campaign_id=retro.campaign_id)
    except Exception as e:
        result = _build_best_effort_fallback(
            retro,
            resolved,
            error_message=str(e),
        )
        runtime = read_json(resolved.runtime_state, {}) or {}
        attempt_index = int(runtime.get("director_retry_count") or 0)
        append_jsonl(
            resolved.supervisor_events,
            {
                "recorded_at": _utcnow(),
                "event": "director_fallback" if result else "director_error",
                "source_campaign_id": retro.campaign_id,
                "target_campaign_id": result.next_campaign_id if result else "",
                "error": str(e)[:300],
                "fallback_used": bool(result),
                "decision_source": result.decision_source if result else "codex_sdk",
                "attempt_index": attempt_index,
                "diagnosis": result.diagnosis if result else "",
                "tracks_generated": len(result.next_tracks) if result else 0,
                "top_3_track_ids": [
                    str(track.get("track_id")).strip()
                    for track in (result.next_tracks[:3] if result else [])
                    if str(track.get("track_id")).strip()
                ],
                "next_campaign_id": result.next_campaign_id if result else "",
            },
        )
        if result is None:
            if retro.all_near_chance:
                _mark_research_blocked(
                    resolved,
                    source_campaign_id=retro.campaign_id,
                )
                return None
            _update_director_runtime_state(
                resolved,
                director_status="error",
                last_director_attempt_at=_utcnow(),
                last_director_error=str(e)[:300],
            )
            return None
    else:
        # 4. Parse response
        result = parse_director_response(response_text)
    contract = _load_program_contract(resolved)
    result.source_campaign_id = retro.campaign_id
    result.program_id = contract.program_id
    result.next_program_text = _ensure_round_program_text(
        result.next_program_text,
        program_id=result.program_id,
        source_campaign_id=result.source_campaign_id,
        next_campaign_id=result.next_campaign_id,
    )

    if not _tracks_respect_program_boundary(
        result.next_tracks,
        resolved,
        campaign_id=result.next_campaign_id,
    ):
        return None

    # 5. Validate tracks
    valid_tracks = validate_tracks(result.next_tracks, resolved)
    result.next_tracks = valid_tracks

    if not valid_tracks:
        fallback = _build_best_effort_fallback(
            retro,
            resolved,
            error_message="Director 生成的 tracks 未通过 preflight，切换到 best-effort fallback。",
        )
        if fallback is not None:
            fallback.source_campaign_id = retro.campaign_id
            fallback.program_id = contract.program_id
            fallback.next_program_text = _ensure_round_program_text(
                fallback.next_program_text,
                program_id=fallback.program_id,
                source_campaign_id=fallback.source_campaign_id,
                next_campaign_id=fallback.next_campaign_id,
            )
            fallback.next_tracks = validate_tracks(fallback.next_tracks, resolved)
            if fallback.next_tracks:
                write_next_campaign(fallback, resolved)
                append_jsonl(
                    resolved.supervisor_events,
                    {
                        "recorded_at": _utcnow(),
                        "event": "director_manifest_fallback",
                        "source_campaign_id": retro.campaign_id,
                        "next_campaign_id": fallback.next_campaign_id,
                        "decision_source": fallback.decision_source,
                        "attempt_index": int((read_json(resolved.runtime_state, {}) or {}).get("director_retry_count") or 0),
                        "tracks_generated": len(fallback.next_tracks),
                        "top_3_track_ids": [
                            str(track.get("track_id")).strip()
                            for track in fallback.next_tracks[:3]
                            if str(track.get("track_id")).strip()
                        ],
                    },
                )
                return fallback
        # LLM generated no valid tracks — log and return partial result
        append_jsonl(
            resolved.supervisor_events,
            {
                "recorded_at": _utcnow(),
                "event": "director_no_valid_tracks",
                "source_campaign_id": retro.campaign_id,
                "target_campaign_id": result.next_campaign_id,
                "decision_source": result.decision_source,
                "diagnosis": result.diagnosis[:200],
            },
        )
        if retro.all_near_chance:
            _mark_research_blocked(
                resolved,
                source_campaign_id=retro.campaign_id,
            )
            return None
        # Still write reasoning for dashboard visibility
        write_next_campaign(result, resolved)
        _update_director_runtime_state(
            resolved,
            director_status="error",
            last_director_attempt_at=_utcnow(),
            last_director_error="Director generated no valid tracks after fallback attempts.",
        )
        return result

    # 6. Write output
    write_next_campaign(result, resolved)

    return result
