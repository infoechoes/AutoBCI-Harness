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


DEFAULT_DIRECTOR_PLAN_INPUT = Path("artifacts/monitor/rsvp_ship_image_autoresearch_latest.json")
DIRECTOR_AGENT_INSTRUCTIONS = Path("memory/docs/dev_pack_2026_04_20/08_LOCAL_AGENT_HANDOFF/DIRECTOR_AGENT.md")
DIRECTOR_PLAN_DIR = Path("artifacts/monitor/director_plans")
MAX_SEARCH_QUERIES = 5
MAX_EVIDENCE = 8
DEFAULT_MIN_TRACKS = 10
OPENAI_RESPONSES_URL = "https://api.openai.com/v1/responses"


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
        raise FileNotFoundError(f"Director Agent instructions missing: {path}") from exc
    if not content:
        raise FileNotFoundError(f"Director Agent instructions empty: {path}")
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
        "target_mode": state.get("target_mode") or "rsvp_ship_image_classification",
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
    dataset = str(state_summary.get("dataset_name") or "RSVP ship image dataset")
    return [
        "ship image classification HOG color histogram baseline small dataset",
        "ship vs non ship image classification feature extraction logistic regression",
        "small image dataset binary classification transfer learning embedding",
        "hard negative mining image classification ships",
        f"{dataset} image classification balanced accuracy calibration threshold",
    ][:MAX_SEARCH_QUERIES]


def _local_evidence(state_summary: dict[str, Any]) -> dict[str, Any]:
    score = state_summary.get("test_primary_metric")
    benchmark = state_summary.get("benchmark_primary_score")
    score_text = "-" if score is None else f"{float(score):.4f}" if isinstance(score, (int, float)) else str(score)
    benchmark_text = "-" if benchmark is None else f"{float(benchmark):.4f}" if isinstance(benchmark, (int, float)) else str(benchmark)
    return {
        "evidence_id": "local_state_image_baseline",
        "source_type": "local_artifact",
        "title": "当前纯图像基线结果",
        "url": "",
        "snippet": (
            f"{state_summary.get('selected_model_id')} 使用 {state_summary.get('selected_feature_view')}，"
            f"测试 {state_summary.get('primary_metric')}={score_text}，对照={benchmark_text}。"
        ),
        "summary": (
            "当前证据只支持纯图像 ship/not-ship 二分类；脑电路径仍被标记为 "
            f"{state_summary.get('eeg_status')}。Director 只能生成研究队列，不能据此启动执行。"
        ),
    }


def _fixture_web_evidence(queries: list[str]) -> list[dict[str, Any]]:
    templates = [
        (
            "fixture_hog_color_baselines",
            "传统图像特征基线",
            "HOG、颜色直方图和线性分类器通常是小样本二分类的强基线组合。",
        ),
        (
            "fixture_transfer_embedding_probe",
            "预训练 embedding 线性探针",
            "固定预训练视觉 embedding 后训练轻量分类头，适合作为复杂 CNN 前的强对照。",
        ),
        (
            "fixture_hard_negative_mining",
            "Hard negative 误差分析",
            "船/非船二分类容易受水面、码头、地平线和小目标干扰，误差桶应先被审计。",
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
            "Search for compact, practical directions for ship versus non-ship image classification. "
            "Return concise evidence for baselines, features, calibration, hard negatives, and transfer embeddings.\n"
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
            "track_id": "image_pixel_logistic_16x16_recheck",
            "title": "16x16 灰度像素线性基线复核",
            "hypothesis": "如果 32x32 像素基线已经接近 0.87，降到 16x16 后仍应保留多数信号；若分数不降，说明任务主要靠粗形状或背景差异。",
            "algorithm_family": "pixel_linear_model",
            "input_mode": "image_only",
            "expected_signal": "粗轮廓、海天线、水面和船体亮暗分布",
            "risk": "可能学习到采集背景而非船本身，需要配合错误桶审计。",
            "runnable_now": True,
            "runner_hint": "复用当前 image_logistic_baseline runner，仅改 resize=16x16 与正则化。",
            "evidence_ids": ["local_state_image_baseline"],
        },
        {
            "track_id": "image_pixel_logistic_regularization_sweep",
            "title": "32x32 像素线性模型正则扫描",
            "hypothesis": "当前强基线可能对正则强度敏感；扫描 L2/学习率/类别权重能判断 0.8696 是稳定结果还是偶然参数点。",
            "algorithm_family": "pixel_linear_model",
            "input_mode": "image_only",
            "expected_signal": "全局灰度模板和目标尺寸差异",
            "risk": "只改训练超参，方向增益可能有限，但能建立可信固定对照线。",
            "runnable_now": True,
            "runner_hint": "复用当前 numpy weighted logistic regression，记录多个 seed 和正则配置。",
            "evidence_ids": ["local_state_image_baseline"],
        },
        {
            "track_id": "image_color_histogram_logistic",
            "title": "RGB/HSV 颜色直方图基线",
            "hypothesis": "如果 ship/not-ship 标签与海面、天空、船体颜色分布相关，颜色直方图加逻辑回归应给出非平凡分数。",
            "algorithm_family": "color_histogram",
            "input_mode": "image_only",
            "expected_signal": "颜色比例、饱和度、亮度分布",
            "risk": "容易捕捉背景偏差；必须和分层数据划分、hard negative 一起看。",
            "runnable_now": False,
            "runner_hint": "新增轻量 feature extractor：RGB/HSV bin counts + weighted logistic regression。",
            "evidence_ids": ["local_state_image_baseline", "fixture_hog_color_baselines"],
        },
        {
            "track_id": "image_edge_hog_linear_probe",
            "title": "边缘/HOG 线性探针",
            "hypothesis": "船体轮廓和桅杆等结构应在梯度方向统计中更稳定，HOG 可能比原始像素更抗亮度变化。",
            "algorithm_family": "hog_edges",
            "input_mode": "image_only",
            "expected_signal": "局部边缘方向、船体外轮廓、水平线干扰",
            "risk": "小目标或低分辨率图像会让 HOG 稀疏，参数需要小网格搜索。",
            "runnable_now": False,
            "runner_hint": "实现 HOG 或 Sobel/梯度直方图特征，再接线性 SVM/逻辑回归。",
            "evidence_ids": ["local_state_image_baseline", "fixture_hog_color_baselines"],
        },
        {
            "track_id": "image_lbp_texture_baseline",
            "title": "LBP/纹理统计基线",
            "hypothesis": "水面、船体、码头和天空区域纹理不同，LBP 或简单纹理统计可能补上像素均值无法表达的局部模式。",
            "algorithm_family": "texture_features",
            "input_mode": "image_only",
            "expected_signal": "局部纹理、边缘密度、重复结构",
            "risk": "纹理特征也可能高度依赖背景，跨批次泛化不一定好。",
            "runnable_now": False,
            "runner_hint": "新增 LBP/GLCM 或 patch texture summary，接 weighted logistic regression。",
            "evidence_ids": ["local_state_image_baseline", "fixture_hog_color_baselines"],
        },
        {
            "track_id": "image_threshold_calibration_sweep",
            "title": "阈值与概率校准扫描",
            "hypothesis": "balanced accuracy 对阈值敏感；固定 0.5 可能不是最佳工作点，校准曲线能解释错分结构。",
            "algorithm_family": "calibration_thresholding",
            "input_mode": "image_only",
            "expected_signal": "分类概率排序质量、阳性/阴性召回平衡",
            "risk": "在验证集上调阈值后必须只在测试集做一次最终评估，不能反复看测试集。",
            "runnable_now": True,
            "runner_hint": "在当前 logistic 输出上做 validation-only threshold sweep 和 reliability bins。",
            "evidence_ids": ["local_state_image_baseline", "fixture_calibration_threshold"],
        },
        {
            "track_id": "image_split_seed_robustness",
            "title": "数据划分和随机种子鲁棒性",
            "hypothesis": "单次 test balanced accuracy=0.8696 需要跨 seed 或分层切分复核，避免把偶然容易样本当成稳定能力。",
            "algorithm_family": "evaluation_robustness",
            "input_mode": "image_only",
            "expected_signal": "分数方差、类别比例敏感性、重复样本影响",
            "risk": "如果原始数据有时序或受试者结构，随机切分可能泄漏相近样本；必须保持项目约束。",
            "runnable_now": True,
            "runner_hint": "不改标签和 raw data，只重放现有划分生成逻辑的多个 seed/strata 报告。",
            "evidence_ids": ["local_state_image_baseline"],
        },
        {
            "track_id": "image_hard_negative_error_buckets",
            "title": "Hard negative 和错分桶分析",
            "hypothesis": "下一轮算法方向应由错分图像决定：水面/码头/小目标/远景/模糊等桶会指向不同特征或数据清洗动作。",
            "algorithm_family": "error_analysis",
            "input_mode": "image_only",
            "expected_signal": "错误样本的视觉共性和标签异常",
            "risk": "如果只看少量截图，结论会主观；需要保存可复查的样本索引和缩略图。",
            "runnable_now": True,
            "runner_hint": "导出 top false positive/false negative 缩略图、score、label、split，不启动训练。",
            "evidence_ids": ["local_state_image_baseline", "fixture_hard_negative_mining"],
        },
        {
            "track_id": "image_label_duplicate_audit",
            "title": "标签、重复图和近重复审计",
            "hypothesis": "图像二分类的高分可能来自重复或近重复样本；哈希/embedding 近邻审计能判断数据划分是否被污染。",
            "algorithm_family": "data_audit",
            "input_mode": "image_only",
            "expected_signal": "重复样本、近重复帧、标签反转",
            "risk": "需要读取派生图像索引，但不能修改 data/raw/；审计只能写 artifacts。",
            "runnable_now": False,
            "runner_hint": "实现 perceptual hash 或 embedding nearest-neighbor audit，输出 split overlap 报告。",
            "evidence_ids": ["local_state_image_baseline"],
        },
        {
            "track_id": "image_tiny_cnn_regularized_probe",
            "title": "小型 CNN 正则化探针",
            "hypothesis": "如果强基线仍漏掉局部形状，小型 CNN 可学习更有空间结构的局部模式，但需要严格早停和增强控制。",
            "algorithm_family": "small_cnn",
            "input_mode": "image_only",
            "expected_signal": "局部纹理、船体部件组合、尺度变化",
            "risk": "样本少时极易过拟合；必须以线性/HOG/审计结果作为前置门槛。",
            "runnable_now": False,
            "runner_hint": "新增小 CNN runner，限制参数量，固定 validation early stopping，报告多 seed。",
            "evidence_ids": ["local_state_image_baseline"],
        },
        {
            "track_id": "image_pretrained_embedding_linear_probe",
            "title": "预训练视觉 embedding 线性探针",
            "hypothesis": "通用视觉 embedding 可能已编码船、海面和场景结构；固定特征加线性分类头可作为重模型前的强上限参考。",
            "algorithm_family": "pretrained_embedding",
            "input_mode": "image_only",
            "expected_signal": "高层语义、目标类别、场景上下文",
            "risk": "需要下载或加载外部模型；如果环境不稳定，先做离线候选，不进入当前 runner。",
            "runnable_now": False,
            "runner_hint": "后续接入 CLIP/ConvNeXt/ViT embedding extractor，固定 backbone，只训练线性头。",
            "evidence_ids": ["local_state_image_baseline", "fixture_transfer_embedding_probe"],
        },
        {
            "track_id": "image_background_mask_ablation",
            "title": "背景遮挡和中心裁剪消融",
            "hypothesis": "如果模型依赖背景，中心裁剪、边缘遮挡或简单 saliency ablation 会显著改变分数；这能验证是否真的看到了船。",
            "algorithm_family": "ablation_sanity_check",
            "input_mode": "image_only",
            "expected_signal": "船体区域和背景区域的相对贡献",
            "risk": "没有目标框时遮挡策略很粗，只能作为诊断，不应作为最终模型。",
            "runnable_now": False,
            "runner_hint": "对派生图像做中心裁剪、上下半幅遮挡、随机 patch mask，复跑固定 logistic/HOG。",
            "evidence_ids": ["local_state_image_baseline", "fixture_hard_negative_mining"],
        },
    ]


def _build_tracks(min_tracks: int, evidence_ids: set[str]) -> list[dict[str, Any]]:
    wanted = max(int(min_tracks or DEFAULT_MIN_TRACKS), DEFAULT_MIN_TRACKS)
    tracks: list[dict[str, Any]] = []
    for template in _track_templates():
        item = dict(template)
        item["evidence_ids"] = [evidence_id for evidence_id in template["evidence_ids"] if evidence_id in evidence_ids]
        if not item["evidence_ids"]:
            item["evidence_ids"] = ["local_state_image_baseline"]
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
    if not isinstance(state, dict) or not state:
        raise FileNotFoundError(f"Director input state missing or invalid: {source_path}")
    state_summary = _summarize_state(state)
    queries = _director_search_queries(state_summary)
    web_record, web_evidence = _collect_web_evidence(web=web, web_provider=web_provider, queries=queries)
    evidence = [_local_evidence(state_summary), *web_evidence]
    evidence = evidence[:MAX_EVIDENCE]
    evidence_ids = {str(item.get("evidence_id") or "") for item in evidence if isinstance(item, dict)}
    tracks = _build_tracks(min_tracks, evidence_ids)
    stamp = _utc_now()
    plan_id = f"director-plan-{_safe_slug(str(state_summary.get('run_id') or 'image'))}-{time.strftime('%Y%m%dT%H%M%SZ', time.gmtime())}"
    plan_dir = director_plan_dir(root)
    plan_path = plan_dir / f"{plan_id}.json"
    latest_path = latest_director_plan_path(root)
    payload: dict[str, Any] = {
        "plan_id": plan_id,
        "created_at": stamp,
        "mode": "director_only_debug",
        "source_state_path": str(source_path),
        "source_run_id": state_summary.get("run_id"),
        "program_id": state_summary.get("program_id"),
        "agent_instructions_path": str(director_agent_instructions_path(root)),
        "agent_instructions_sha_hint": hashlib.sha256(instructions.encode("utf-8")).hexdigest()[:12],
        "task": {
            "target_mode": "pure_image_ship_not_ship_binary",
            "input_mode": "image_only",
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
