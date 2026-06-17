from __future__ import annotations

import json
import os
import re
import hashlib
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .runtime_store import read_json, write_json_atomic


DEFAULT_DIRECTOR_PLAN_INPUT = Path("artifacts/monitor/autoresearch_status.json")
DIRECTOR_AGENT_INSTRUCTIONS = Path("memory/docs/dev_pack_2026_04_20/08_LOCAL_AGENT_HANDOFF/DIRECTOR_AGENT.md")
DIRECTOR_PLAN_DIR = Path("artifacts/monitor/director_plans")
MAX_SEARCH_QUERIES = 5
MAX_EVIDENCE = 8
DEFAULT_MIN_TRACKS = 10
OPENAI_RESPONSES_URL = "https://api.openai.com/v1/responses"
BUILTIN_DIRECTOR_AGENT_INSTRUCTIONS = """\
You are the AutoBCI Director. Generate bounded research directions for a public
BCI harness. Do not assume a bundled dataset, runner, or evaluator. Every
direction must preserve strict causality, read-only raw data, fixed split
contracts, fixed primary metrics, and artifact-backed review.
"""


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _safe_slug(value: str) -> str:
    slug = re.sub(r"[^a-zA-Z0-9_.-]+", "-", value.strip()).strip("-")
    return slug[:80] or "unknown"


def _resolve_repo_path(repo_root: Path, path: str | Path | None, *, default: Path) -> Path:
    candidate = Path(path) if path is not None else default
    if candidate.is_absolute():
        return candidate
    return repo_root / candidate


def director_agent_instructions_path(repo_root: str | Path) -> Path:
    return Path(repo_root).expanduser().resolve() / DIRECTOR_AGENT_INSTRUCTIONS


def load_director_agent_instructions(repo_root: str | Path) -> str:
    path = director_agent_instructions_path(repo_root)
    try:
        content = path.read_text(encoding="utf-8").strip()
    except FileNotFoundError as exc:
        return BUILTIN_DIRECTOR_AGENT_INSTRUCTIONS.strip()
    if not content:
        return BUILTIN_DIRECTOR_AGENT_INSTRUCTIONS.strip()
    return content


def director_plan_dir(repo_root: str | Path) -> Path:
    return Path(repo_root).expanduser().resolve() / DIRECTOR_PLAN_DIR


def latest_director_plan_path(repo_root: str | Path) -> Path:
    return director_plan_dir(repo_root) / "latest.json"


def load_latest_director_plan(repo_root: str | Path) -> dict[str, Any]:
    return read_json(latest_director_plan_path(repo_root), {})


def _summarize_state(state: dict[str, Any]) -> dict[str, Any]:
    selected_model = state.get("selected_model") if isinstance(state.get("selected_model"), dict) else {}
    selected_config = selected_model.get("config") if isinstance(selected_model.get("config"), dict) else {}
    selected_model_id = (
        selected_model.get("model_id")
        or selected_model.get("model_family")
        or selected_config.get("model_id")
        or "-"
    )
    selected_algorithm = (
        selected_model.get("algorithm")
        or selected_model.get("model_backend")
        or selected_config.get("algorithm")
        or "-"
    )
    selected_feature_view = (
        selected_model.get("feature_view")
        or selected_config.get("feature_family")
        or selected_config.get("feature_view")
        or "-"
    )
    return {
        "run_id": state.get("run_id") or "-",
        "program_id": state.get("program_id") or "-",
        "dataset_name": state.get("dataset_name") or "-",
        "status": state.get("status") or "-",
        "target_mode": state.get("target_mode") or "generic_bci_decoding",
        "primary_metric": state.get("primary_metric") or "test_balanced_accuracy",
        "benchmark_primary_score": state.get("benchmark_primary_score"),
        "test_primary_metric": state.get("test_primary_metric"),
        "selected_model_id": selected_model_id,
        "selected_algorithm": selected_algorithm,
        "selected_feature_view": selected_feature_view,
        "eeg_status": state.get("eeg_status") or "-",
        "no_cross_modal_claim": bool(state.get("no_cross_modal_claim", False)),
    }


def _director_search_queries(state_summary: dict[str, Any]) -> list[str]:
    dataset = str(state_summary.get("dataset_name") or "BCI decoding dataset")
    return [
        "strict causal neural decoding evaluation leakage prevention",
        "BCI decoder temporal generalization cross session evaluation",
        "ECoG kinematics decoding baseline ridge XGBoost recurrent neural network",
        "brain computer interface offline decoder calibration drift robustness",
        f"{dataset} BCI decoding fixed evaluation metrics artifacts ledger",
    ][:MAX_SEARCH_QUERIES]


def _local_evidence(state_summary: dict[str, Any]) -> dict[str, Any]:
    score = state_summary.get("test_primary_metric")
    benchmark = state_summary.get("benchmark_primary_score")
    score_text = "-" if score is None else f"{float(score):.4f}" if isinstance(score, (int, float)) else str(score)
    benchmark_text = "-" if benchmark is None else f"{float(benchmark):.4f}" if isinstance(benchmark, (int, float)) else str(benchmark)
    return {
        "evidence_id": "local_state_current_baseline",
        "source_type": "local_artifact",
        "title": "当前本地研究状态",
        "url": "",
        "snippet": (
            f"{state_summary.get('selected_model_id')} 使用 {state_summary.get('selected_feature_view')}，"
            f"测试 {state_summary.get('primary_metric')}={score_text}，对照={benchmark_text}。"
        ),
        "summary": (
            "当前证据只支持本地已冻结 Program 和已记录 artifact。"
            f"数据状态={state_summary.get('eeg_status')}。Director 只能生成研究队列，不能据此启动执行。"
        ),
    }


def _fixture_web_evidence(queries: list[str]) -> list[dict[str, Any]]:
    templates = [
        (
            "fixture_causal_baselines",
            "严格因果轻量基线",
            "固定数据划分、只用当前和过去样本的轻量基线，是判断后续复杂模型是否真有增益的起点。",
        ),
        (
            "fixture_drift_robustness",
            "跨试次漂移与鲁棒性",
            "BCI 解码常见失败来自 session drift、个体差异和时间段偏移，需要先用固定评价器定位误差结构。",
        ),
        (
            "fixture_error_audit",
            "错误分解和反证审计",
            "把错误按 session、目标维度、时间段和标签边界拆开，能避免把 lucky run 当成算法突破。",
        ),
        (
            "fixture_calibration_threshold",
            "阈值和校准检查",
            "当主要指标是 balanced accuracy 时，阈值扫描和概率校准能暴露默认 0.5 阈值的偏差。",
        ),
    ]
    rows: list[dict[str, Any]] = []
    for index, (evidence_id, title, summary) in enumerate(templates):
        rows.append(
            {
                "evidence_id": evidence_id,
                "source_type": "fixture_web",
                "title": title,
                "url": f"fixture://director-web/{index + 1}",
                "snippet": queries[index % len(queries)] if queries else title,
                "summary": summary,
            }
        )
    return rows


def _extract_openai_output_text(payload: dict[str, Any]) -> str:
    output_text = str(payload.get("output_text") or "").strip()
    if output_text:
        return output_text
    chunks: list[str] = []
    output = payload.get("output")
    if isinstance(output, list):
        for item in output:
            if not isinstance(item, dict):
                continue
            content = item.get("content")
            if not isinstance(content, list):
                continue
            for content_item in content:
                if isinstance(content_item, dict):
                    text = str(content_item.get("text") or "").strip()
                    if text:
                        chunks.append(text)
    return "\n".join(chunks).strip()


def _extract_openai_sources(payload: dict[str, Any], fallback_summary: str) -> list[dict[str, Any]]:
    sources: list[dict[str, Any]] = []
    output = payload.get("output")
    if isinstance(output, list):
        for item in output:
            if not isinstance(item, dict) or item.get("type") != "web_search_call":
                continue
            action = item.get("action")
            if not isinstance(action, dict):
                continue
            for source in action.get("sources") or []:
                if not isinstance(source, dict):
                    continue
                title = str(source.get("title") or source.get("url") or "OpenAI web source").strip()
                url = str(source.get("url") or "").strip()
                if not url:
                    continue
                sources.append(
                    {
                        "title": title,
                        "url": url,
                        "snippet": str(source.get("snippet") or "").strip(),
                        "summary": fallback_summary[:500],
                    }
                )
    return sources


def _openai_web_search(queries: list[str]) -> tuple[list[dict[str, Any]], str | None]:
    api_key = os.environ.get("OPENAI_API_KEY", "").strip()
    if not api_key:
        return [], "OPENAI_API_KEY is not configured"
    model = os.environ.get("AUTOBI_OPENAI_WEB_SEARCH_MODEL", "gpt-5").strip() or "gpt-5"
    request_payload = {
        "model": model,
        "reasoning": {"effort": "low"},
        "tools": [{"type": "web_search"}],
        "tool_choice": "auto",
        "include": ["web_search_call.action.sources"],
        "input": (
            "Search for compact, practical directions for strict-causal BCI decoding research. "
            "Return concise evidence for baselines, evaluation leakage checks, calibration, drift, and robust validation.\n"
            + "\n".join(f"- {query}" for query in queries)
        ),
    }
    data = json.dumps(request_payload).encode("utf-8")
    request = urllib.request.Request(
        OPENAI_RESPONSES_URL,
        data=data,
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {api_key}"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
        return [], str(exc)
    summary = _extract_openai_output_text(payload)
    source_rows = _extract_openai_sources(payload, summary)
    evidence: list[dict[str, Any]] = []
    for index, row in enumerate(source_rows[: MAX_EVIDENCE - 1], start=1):
        evidence.append(
            {
                "evidence_id": f"openai_web_{index}",
                "source_type": "openai_web_search",
                "title": row["title"],
                "url": row["url"],
                "snippet": row.get("snippet") or "",
                "summary": row.get("summary") or summary,
            }
        )
    if not evidence and summary:
        evidence.append(
            {
                "evidence_id": "openai_web_summary",
                "source_type": "openai_web_search",
                "title": "OpenAI web search summary",
                "url": "",
                "snippet": "",
                "summary": summary[:1000],
            }
        )
    return evidence, None


def _searxng_search(queries: list[str]) -> tuple[list[dict[str, Any]], str | None]:
    base_url = os.environ.get("AUTOBI_SEARXNG_URL", "").strip().rstrip("/")
    if not base_url:
        return [], "AUTOBI_SEARXNG_URL is not configured"
    evidence: list[dict[str, Any]] = []
    for query in queries:
        if len(evidence) >= MAX_EVIDENCE - 1:
            break
        params = urllib.parse.urlencode({"q": query, "format": "json"})
        request = urllib.request.Request(f"{base_url}/search?{params}", headers={"Accept": "application/json"})
        try:
            with urllib.request.urlopen(request, timeout=15) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
            return evidence, str(exc)
        results = payload.get("results") if isinstance(payload, dict) else []
        if not isinstance(results, list):
            continue
        for item in results:
            if len(evidence) >= MAX_EVIDENCE - 1:
                break
            if not isinstance(item, dict):
                continue
            url = str(item.get("url") or "").strip()
            if not url:
                continue
            evidence.append(
                {
                    "evidence_id": f"searxng_{len(evidence) + 1}",
                    "source_type": "searxng",
                    "title": str(item.get("title") or url),
                    "url": url,
                    "snippet": str(item.get("content") or item.get("snippet") or ""),
                    "summary": str(item.get("content") or item.get("snippet") or "")[:500],
                }
            )
    return evidence, None


def _choose_web_provider(web: str, explicit_provider: str | None) -> tuple[str, str]:
    mode = str(web or "auto").strip().lower()
    provider = str(explicit_provider or "").strip().lower()
    if provider:
        if provider == "disabled":
            return "disabled", "disabled" if mode == "off" else "unavailable"
        return provider, "pending"
    if mode == "off":
        return "disabled", "disabled"
    if os.environ.get("OPENAI_API_KEY", "").strip():
        return "openai_web_search", "pending"
    if os.environ.get("AUTOBI_SEARXNG_URL", "").strip():
        return "searxng", "pending"
    return "disabled", "unavailable"


def _collect_web_evidence(
    *,
    web: str,
    web_provider: str | None,
    queries: list[str],
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    provider, initial_status = _choose_web_provider(web, web_provider)
    web_record: dict[str, Any] = {
        "requested_mode": str(web or "auto").strip().lower(),
        "provider": provider,
        "web_status": initial_status,
        "queries": queries[:MAX_SEARCH_QUERIES],
        "max_queries": MAX_SEARCH_QUERIES,
        "max_evidence": MAX_EVIDENCE,
        "budget_state": {
            "queries_requested": len(queries),
            "queries_used": min(len(queries), MAX_SEARCH_QUERIES),
            "evidence_cap": MAX_EVIDENCE,
        },
    }
    if provider == "disabled":
        return web_record, []
    if provider == "fixture":
        evidence = _fixture_web_evidence(queries)
        web_record["web_status"] = "available"
        web_record["evidence_returned"] = len(evidence)
        return web_record, evidence
    if provider == "openai_web_search":
        evidence, error = _openai_web_search(queries)
    elif provider == "searxng":
        evidence, error = _searxng_search(queries)
    else:
        evidence, error = [], f"unknown web provider: {provider}"
    if error:
        web_record["web_status"] = "unavailable"
        web_record["error"] = error
    else:
        web_record["web_status"] = "available"
    web_record["evidence_returned"] = len(evidence)
    return web_record, evidence


def _track_templates() -> list[dict[str, Any]]:
    return [
        {
            "track_id": "causal_baseline_regularization_sweep",
            "title": "严格因果基线正则扫描",
            "hypothesis": "固定数据划分和评价器后，扫描轻量模型正则强度能区分真实增益和偶然参数点。",
            "algorithm_family": "regularized_baseline",
            "input_mode": "bci_signal",
            "expected_signal": "稳定但有限的神经信号趋势",
            "risk": "只改训练超参，方向增益可能有限，但能建立可信固定对照线。",
            "runnable_now": True,
            "runner_hint": "复用用户配置的固定评估器，记录多个 seed 和正则配置。",
            "evidence_ids": ["local_state_current_baseline", "fixture_causal_baselines"],
        },
        {
            "track_id": "session_drift_error_audit",
            "title": "跨试次漂移误差审计",
            "hypothesis": "如果跨试次误差集中在少数 session 或时间段，先定位漂移比盲目换大模型更可靠。",
            "algorithm_family": "diagnostic_audit",
            "input_mode": "bci_signal",
            "expected_signal": "session、时间段、目标维度之间的系统性误差差异",
            "risk": "只做诊断不直接提高分数，但能降低后续搜索空间噪声。",
            "runnable_now": False,
            "runner_hint": "新增误差分解报告：per-session、per-dimension、time-bin metrics。",
            "evidence_ids": ["local_state_current_baseline", "fixture_drift_robustness"],
        },
        {
            "track_id": "causality_leakage_replay",
            "title": "因果边界和泄漏复放检查",
            "hypothesis": "如果预处理、归一化或目标构造不小心用了未来样本，分数会虚高；复放检查能先排除假进步。",
            "algorithm_family": "leakage_guard",
            "input_mode": "bci_signal",
            "expected_signal": "严格过去窗口与全局处理之间的分数差异",
            "risk": "这类检查通常降低分数，但能保护后续实验的可信度。",
            "runnable_now": False,
            "runner_hint": "对每个 preprocessing step 输出 causality assertion 和 before/after metrics。",
            "evidence_ids": ["local_state_current_baseline", "fixture_causal_baselines"],
        },
        {
            "track_id": "temporal_window_lag_sweep",
            "title": "时间窗口和 lag 小网格",
            "hypothesis": "不同目标维度可能对应不同神经反应延迟；小网格能找出稳定窗口，而不是盲目加大模型。",
            "algorithm_family": "temporal_features",
            "input_mode": "bci_signal",
            "expected_signal": "窗口长度、lag 与主指标之间的稳定关系",
            "risk": "搜索空间过大会诱发多重比较；必须固定验证集并记录完整网格。",
            "runnable_now": False,
            "runner_hint": "只允许预声明窗口和 lag 列表，输出完整 grid artifact。",
            "evidence_ids": ["local_state_current_baseline", "fixture_causal_baselines"],
        },
        {
            "track_id": "feature_family_ablation",
            "title": "特征家族消融",
            "hypothesis": "低频位移、电位、高频功率或运动学历史可能贡献不同；消融能区分真实神经信号与 shortcut。",
            "algorithm_family": "feature_ablation",
            "input_mode": "bci_signal",
            "expected_signal": "不同 feature family 的增益、冗余和偏差",
            "risk": "如果消融同时改了归一化或样本集合，结论会失真。",
            "runnable_now": False,
            "runner_hint": "固定 split 和 model，只切换 feature family，并输出 per-dimension metrics。",
            "evidence_ids": ["local_state_current_baseline", "fixture_error_audit"],
        },
        {
            "track_id": "split_seed_robustness",
            "title": "数据划分和随机种子鲁棒性",
            "hypothesis": "单次高分需要跨 seed 或固定分层复核，避免把偶然容易样本当成稳定能力。",
            "algorithm_family": "evaluation_robustness",
            "input_mode": "bci_signal",
            "expected_signal": "分数方差、类别比例敏感性、重复样本影响",
            "risk": "如果原始数据有时序或受试者结构，随机切分可能泄漏相近样本；必须保持项目约束。",
            "runnable_now": True,
            "runner_hint": "不改标签和 raw data，只重放现有划分生成逻辑的多个 seed/strata 报告。",
            "evidence_ids": ["local_state_current_baseline", "fixture_drift_robustness"],
        },
        {
            "track_id": "error_bucket_audit",
            "title": "错分桶和反证审计",
            "hypothesis": "下一轮算法方向应由错误桶决定：session、标签边界、目标维度和异常片段会指向不同修复动作。",
            "algorithm_family": "error_analysis",
            "input_mode": "bci_signal",
            "expected_signal": "错误样本的结构共性和标签异常",
            "risk": "如果只看少量样本，结论会主观；需要保存可复查的样本索引和统计摘要。",
            "runnable_now": True,
            "runner_hint": "导出 top error cases、score、target、split 和 per-bucket metrics，不启动训练。",
            "evidence_ids": ["local_state_current_baseline", "fixture_error_audit"],
        },
        {
            "track_id": "label_boundary_audit",
            "title": "标签边界和样本重复审计",
            "hypothesis": "标签边界、重复片段或相邻窗口泄漏会让固定数据划分看起来过好；审计能先确认数据契约没被污染。",
            "algorithm_family": "data_audit",
            "input_mode": "bci_signal",
            "expected_signal": "重复样本、相邻窗口泄漏、标签反转",
            "risk": "需要读取派生样本索引，但不能修改 data/raw/；审计只能写 artifacts。",
            "runnable_now": False,
            "runner_hint": "实现 split overlap、time-neighbor 和 label consistency audit，输出 artifact 报告。",
            "evidence_ids": ["local_state_current_baseline", "fixture_error_audit"],
        },
        {
            "track_id": "model_family_scout",
            "title": "模型家族 scout 对照",
            "hypothesis": "GRU、TCN、树模型和线性基线可能分别适合不同信号形态；小规模 scout 能确定是否值得扩大训练。",
            "algorithm_family": "model_family_scout",
            "input_mode": "bci_signal",
            "expected_signal": "模型家族之间的稳定排名",
            "risk": "scout 只能决定下一步方向，不能包装成最终提升。",
            "runnable_now": False,
            "runner_hint": "每个模型只跑预声明小预算，输出相同 split 下的完整 metrics。",
            "evidence_ids": ["local_state_current_baseline", "fixture_causal_baselines"],
        },
        {
            "track_id": "calibration_threshold_sweep",
            "title": "阈值与概率校准扫描",
            "hypothesis": "分类主指标对阈值敏感；固定默认阈值可能不是最佳工作点，校准曲线能解释错分结构。",
            "algorithm_family": "calibration_thresholding",
            "input_mode": "bci_signal",
            "expected_signal": "预测概率排序质量、类别召回平衡",
            "risk": "在验证集上调阈值后必须只在测试集做一次最终评估，不能反复看测试集。",
            "runnable_now": False,
            "runner_hint": "在固定模型输出上做 validation-only threshold sweep 和 reliability bins。",
            "evidence_ids": ["local_state_current_baseline", "fixture_calibration_threshold"],
        },
        {
            "track_id": "artifact_storage_budget_check",
            "title": "artifact 与 checkpoint 存储预算检查",
            "hypothesis": "自动研究容易重复写中间产物；先压住 artifact 体积，才能长期运行而不拖垮用户机器。",
            "algorithm_family": "storage_guard",
            "input_mode": "local_artifacts",
            "expected_signal": "重复文件、可压缩记录、checkpoint 体积异常",
            "risk": "当前只做 audit，不自动删除或压缩用户文件。",
            "runnable_now": False,
            "runner_hint": "运行 storage audit，输出候选清单和预算建议，不移动文件。",
            "evidence_ids": ["local_state_current_baseline", "fixture_error_audit"],
        },
    ]


def _build_tracks(min_tracks: int, evidence_ids: set[str]) -> list[dict[str, Any]]:
    wanted = max(int(min_tracks or DEFAULT_MIN_TRACKS), DEFAULT_MIN_TRACKS)
    tracks: list[dict[str, Any]] = []
    for template in _track_templates():
        item = dict(template)
        item["evidence_ids"] = [evidence_id for evidence_id in template["evidence_ids"] if evidence_id in evidence_ids]
        if not item["evidence_ids"]:
            item["evidence_ids"] = ["local_state_current_baseline"]
        tracks.append(item)
        if len(tracks) >= wanted:
            break
    return tracks


def run_director_plan(
    repo_root: str | Path,
    *,
    input_path: str | Path | None = None,
    min_tracks: int = DEFAULT_MIN_TRACKS,
    web: str = "auto",
    web_provider: str | None = None,
) -> dict[str, Any]:
    root = Path(repo_root).expanduser().resolve()
    instructions = load_director_agent_instructions(root)
    source_path = _resolve_repo_path(root, input_path, default=DEFAULT_DIRECTOR_PLAN_INPUT)
    state = read_json(source_path, {})
    state_source_status = "loaded"
    if not isinstance(state, dict) or not state:
        state_source_status = "bootstrap_missing_state"
        state = {
            "run_id": "bootstrap",
            "program_id": "generic_bci_research",
            "dataset_name": "user_configured_bci_dataset",
            "status": "bootstrap",
            "target_mode": "generic_bci_decoding",
            "primary_metric": "user_defined_primary_metric",
            "eeg_status": "unconfigured",
            "no_cross_modal_claim": True,
        }
    state_summary = _summarize_state(state)
    queries = _director_search_queries(state_summary)
    web_record, web_evidence = _collect_web_evidence(web=web, web_provider=web_provider, queries=queries)
    evidence = [_local_evidence(state_summary), *web_evidence]
    evidence = evidence[:MAX_EVIDENCE]
    evidence_ids = {str(item.get("evidence_id") or "") for item in evidence if isinstance(item, dict)}
    tracks = _build_tracks(min_tracks, evidence_ids)
    stamp = _utc_now()
    plan_id = f"director-plan-{_safe_slug(str(state_summary.get('run_id') or 'generic-bci'))}-{time.strftime('%Y%m%dT%H%M%SZ', time.gmtime())}"
    plan_dir = director_plan_dir(root)
    plan_path = plan_dir / f"{plan_id}.json"
    latest_path = latest_director_plan_path(root)
    payload: dict[str, Any] = {
        "plan_id": plan_id,
        "created_at": stamp,
        "mode": "director_only_debug",
        "source_state_path": str(source_path),
        "source_state_status": state_source_status,
        "source_run_id": state_summary.get("run_id"),
        "program_id": state_summary.get("program_id"),
        "agent_instructions_path": str(director_agent_instructions_path(root)),
        "agent_instructions_sha_hint": hashlib.sha256(instructions.encode("utf-8")).hexdigest()[:12],
        "task": {
            "target_mode": state_summary.get("target_mode") or "generic_bci_decoding",
            "input_mode": "bci_signal",
            "primary_metric": state_summary.get("primary_metric"),
            "current_score": state_summary.get("test_primary_metric"),
            "current_baseline_score": state_summary.get("benchmark_primary_score"),
        },
        "web_research": web_record,
        "evidence_pack": {
            "web_status": web_record.get("web_status"),
            "local_state_summary": state_summary,
            "evidence": evidence,
        },
        "tracks": tracks,
        "recommended_queue": [track["track_id"] for track in tracks],
        "safety": {
            "executor_started": False,
            "formal_manifest_written": False,
            "raw_data_touched": False,
            "campaign_started": False,
        },
        "artifact_paths": {
            "plan": str(plan_path),
            "latest": str(latest_path),
        },
    }
    write_json_atomic(plan_path, payload)
    write_json_atomic(latest_path, payload)
    return payload
