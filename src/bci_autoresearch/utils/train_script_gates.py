from __future__ import annotations

import json
from pathlib import Path
from typing import Mapping


VALID_ARTIFACT_PROBES = frozenset(
    {"none", "session_center", "target_shuffle", "target_shift"}
)


def normalize_artifact_probe(raw_value: str) -> str:
    artifact_probe = str(raw_value).strip().lower()
    if artifact_probe not in VALID_ARTIFACT_PROBES:
        raise ValueError(
            "--artifact-probe must be one of none, session_center, target_shuffle, target_shift."
        )
    return artifact_probe


def validate_bin_size_ms(*, fs_hz: float, bin_ms: float, flag_name: str) -> int:
    bin_samples = int(round(float(fs_hz) * float(bin_ms) / 1000.0))
    if bin_samples <= 0:
        raise ValueError(f"{flag_name} is too small.")
    return bin_samples


def write_preflight_payload(
    path: Path,
    *,
    script_name: str,
    dataset_config: Path | str,
    target_names: list[str],
    extra_fields: Mapping[str, object] | None = None,
) -> None:
    payload: dict[str, object] = {
        "status": "preflight_ok",
        "script": script_name,
        "dataset_config": str(Path(dataset_config).resolve()),
        "target_names": list(target_names),
    }
    if extra_fields:
        payload.update(dict(extra_fields))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
