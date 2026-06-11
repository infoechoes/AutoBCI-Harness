from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml


REQUIRED_PROGRAM_FIELDS = (
    "program_id",
    "title",
    "status",
    "problem_family",
    "primary_metric_name",
    "allowed_track_prefixes",
    "allowed_dataset_names",
    "current_reliable_best",
)
ALLOWED_PROGRAM_STATUSES = {"draft", "active", "closed"}


class ProgramContractError(RuntimeError):
    pass


@dataclass(frozen=True)
class ProgramContract:
    program_id: str
    title: str
    status: str
    problem_family: str
    primary_metric_name: str
    allowed_track_prefixes: tuple[str, ...]
    allowed_dataset_names: tuple[str, ...]
    current_reliable_best: str
    body: str
    extras: dict[str, Any]

    @property
    def forbidden_summary(self) -> str:
        return "不得切到不属于当前前缀或数据集的新任务。"


def _normalize_text(value: Any) -> str:
    return str(value or "").strip()


def _split_front_matter(text: str) -> tuple[dict[str, Any], str]:
    if not text.startswith("---\n"):
        raise ProgramContractError("Program 文档缺少 YAML front matter。")
    marker = "\n---\n"
    end_index = text.find(marker, 4)
    if end_index < 0:
        raise ProgramContractError("Program 文档 front matter 未正确结束。")
    raw_yaml = text[4:end_index]
    body = text[end_index + len(marker):]
    parsed = yaml.safe_load(raw_yaml) or {}
    if not isinstance(parsed, dict):
        raise ProgramContractError("Program front matter 必须是键值映射。")
    return parsed, body


def parse_program_contract(text: str) -> ProgramContract:
    raw, body = _split_front_matter(text)
    missing = [field for field in REQUIRED_PROGRAM_FIELDS if field not in raw]
    if missing:
        raise ProgramContractError(f"Program 缺少必填字段：{', '.join(missing)}")
    status = _normalize_text(raw.get("status"))
    if status not in ALLOWED_PROGRAM_STATUSES:
        raise ProgramContractError(f"Program status 非法：{status or '-'}")
    prefixes = tuple(_normalize_text(item) for item in (raw.get("allowed_track_prefixes") or []) if _normalize_text(item))
    datasets = tuple(_normalize_text(item) for item in (raw.get("allowed_dataset_names") or []) if _normalize_text(item))
    if not prefixes:
        raise ProgramContractError("Program 至少需要 1 个 allowed_track_prefixes。")
    if not datasets:
        raise ProgramContractError("Program 至少需要 1 个 allowed_dataset_names。")
    extras = {key: value for key, value in raw.items() if key not in REQUIRED_PROGRAM_FIELDS}
    return ProgramContract(
        program_id=_normalize_text(raw["program_id"]),
        title=_normalize_text(raw["title"]),
        status=status,
        problem_family=_normalize_text(raw["problem_family"]),
        primary_metric_name=_normalize_text(raw["primary_metric_name"]),
        allowed_track_prefixes=prefixes,
        allowed_dataset_names=datasets,
        current_reliable_best=_normalize_text(raw["current_reliable_best"]),
        body=body,
        extras=extras,
    )


def read_program_contract(path: Path) -> ProgramContract:
    if not path.exists():
        raise ProgramContractError(f"Program 文档不存在：{path}")
    return parse_program_contract(path.read_text(encoding="utf-8"))


def render_program_contract(contract: ProgramContract) -> str:
    payload: dict[str, Any] = {
        "program_id": contract.program_id,
        "title": contract.title,
        "status": contract.status,
        "problem_family": contract.problem_family,
        "primary_metric_name": contract.primary_metric_name,
        "allowed_track_prefixes": list(contract.allowed_track_prefixes),
        "allowed_dataset_names": list(contract.allowed_dataset_names),
        "current_reliable_best": contract.current_reliable_best,
        **contract.extras,
    }
    yaml_text = yaml.safe_dump(payload, allow_unicode=True, sort_keys=False).strip()
    return f"---\n{yaml_text}\n---\n\n{contract.body.lstrip()}"


def with_program_status(
    contract: ProgramContract,
    *,
    status: str,
    extra_updates: dict[str, Any] | None = None,
) -> ProgramContract:
    if status not in ALLOWED_PROGRAM_STATUSES:
        raise ProgramContractError(f"Program status 非法：{status}")
    extras = dict(contract.extras)
    if extra_updates:
        extras.update(extra_updates)
    return ProgramContract(
        program_id=contract.program_id,
        title=contract.title,
        status=status,
        problem_family=contract.problem_family,
        primary_metric_name=contract.primary_metric_name,
        allowed_track_prefixes=contract.allowed_track_prefixes,
        allowed_dataset_names=contract.allowed_dataset_names,
        current_reliable_best=contract.current_reliable_best,
        body=contract.body,
        extras=extras,
    )


def archive_program_copy(contract_path: Path, archive_dir: Path, *, program_id: str) -> Path:
    archive_dir.mkdir(parents=True, exist_ok=True)
    destination = archive_dir / f"{program_id}.md"
    destination.write_text(contract_path.read_text(encoding="utf-8"), encoding="utf-8")
    return destination


def build_closeout_text(
    contract: ProgramContract,
    *,
    reason: str,
    close_reason: str,
    reference_campaign_id: str = "",
) -> str:
    lines = [
        f"# {contract.title} 关闭总结",
        "",
        f"- Program ID：`{contract.program_id}`",
        f"- 关闭原因标签：`{close_reason}`",
        f"- 人类可读原因：{reason}",
        f"- 当前最可信的最好结果：{contract.current_reliable_best or '未填写'}",
    ]
    if reference_campaign_id:
        lines.append(f"- 最后有效参考 campaign：`{reference_campaign_id}`")
    lines.extend(
        [
            "",
            "## 当前任务边界",
            f"- 问题族：`{contract.problem_family}`",
            f"- 主指标：`{contract.primary_metric_name}`",
            f"- 允许前缀：{', '.join(f'`{item}`' for item in contract.allowed_track_prefixes)}",
            f"- 允许数据集：{', '.join(f'`{item}`' for item in contract.allowed_dataset_names)}",
        ]
    )
    return "\n".join(lines) + "\n"


def build_round_program_text(
    existing_text: str,
    *,
    program_id: str,
    source_campaign_id: str,
    next_campaign_id: str,
) -> str:
    lines = [
        "---",
        f"program_id: {program_id}",
        f"source_campaign_id: {source_campaign_id}",
        f"next_campaign_id: {next_campaign_id}",
        "---",
        "",
        existing_text.lstrip(),
    ]
    return "\n".join(lines).rstrip() + "\n"


def extract_track_prefix(track_id: str) -> str:
    tid = _normalize_text(track_id)
    if not tid:
        return ""
    parts = tid.split("_")
    if len(parts) >= 3:
        return "_".join(parts[:3]) + "_"
    if len(parts) >= 2:
        return "_".join(parts[:2]) + "_"
    return f"{tid}_"
