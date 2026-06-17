from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path


DATASET_BUDGET_ENV = "AUTOBCI_MAX_DATASET_BYTES"
ARTIFACT_BUDGET_ENV = "AUTOBCI_MAX_ARTIFACT_BYTES"
DEFAULT_MAX_DATASET_BYTES = 2 * 1024**3
DEFAULT_MAX_ARTIFACT_BYTES = 512 * 1024**2


@dataclass(frozen=True)
class PathSize:
    bytes: int
    files: int
    stopped_early: bool = False


@dataclass(frozen=True)
class StorageBudgetCheck:
    purpose: str
    path: Path
    current_bytes: int
    max_bytes: int | None
    env_var: str
    files_scanned: int
    stopped_early: bool

    @property
    def ok(self) -> bool:
        return self.max_bytes is None or self.current_bytes <= self.max_bytes

    def as_dict(self) -> dict[str, object]:
        return {
            "purpose": self.purpose,
            "path": str(self.path),
            "current_bytes": self.current_bytes,
            "current_human": format_bytes(self.current_bytes),
            "max_bytes": self.max_bytes,
            "max_human": format_bytes(self.max_bytes) if self.max_bytes is not None else "disabled",
            "env_var": self.env_var,
            "files_scanned": self.files_scanned,
            "stopped_early": self.stopped_early,
            "ok": self.ok,
        }


class StorageBudgetError(RuntimeError):
    def __init__(self, check: StorageBudgetCheck):
        max_text = format_bytes(check.max_bytes) if check.max_bytes is not None else "disabled"
        super().__init__(
            f"{check.purpose} exceeds storage budget: {check.path} is at "
            f"{format_bytes(check.current_bytes)}; limit is {max_text}. "
            f"Move large data/artifacts outside the repo, clean old generated outputs, "
            f"or set {check.env_var}=<bytes|512M|2G|0> explicitly. "
            "Set it to 0 only when you intentionally disable this guard."
        )
        self.check = check


def format_bytes(value: int | None) -> str:
    if value is None:
        return "disabled"
    units = ("B", "KiB", "MiB", "GiB", "TiB")
    number = float(max(0, int(value)))
    for unit in units:
        if number < 1024 or unit == units[-1]:
            if unit == "B":
                return f"{int(number)}B"
            return f"{number:.1f}{unit}"
        number /= 1024.0
    return f"{int(value)}B"


def parse_byte_budget(raw: str | None, default: int) -> int | None:
    text = str(raw or "").strip()
    if not text:
        return int(default)
    normalized = text.replace("_", "").replace(" ", "").lower()
    if normalized in {"0", "off", "none", "disabled", "disable"}:
        return None
    match = re.fullmatch(r"([0-9]+(?:\.[0-9]+)?)([kmgt]?i?b?|bytes?)?", normalized)
    if not match:
        raise ValueError(f"Invalid byte budget: {raw!r}. Use examples like 512M, 2G, 1048576, or 0.")
    number = float(match.group(1))
    unit = match.group(2) or ""
    multipliers = {
        "": 1,
        "b": 1,
        "byte": 1,
        "bytes": 1,
        "k": 1024,
        "kb": 1024,
        "kib": 1024,
        "m": 1024**2,
        "mb": 1024**2,
        "mib": 1024**2,
        "g": 1024**3,
        "gb": 1024**3,
        "gib": 1024**3,
        "t": 1024**4,
        "tb": 1024**4,
        "tib": 1024**4,
    }
    return int(number * multipliers[unit])


def configured_budget(env_var: str, default: int) -> int | None:
    return parse_byte_budget(os.environ.get(env_var), default)


def measure_path_size(path: str | Path, *, stop_after_bytes: int | None = None) -> PathSize:
    root = Path(path).expanduser()
    if not root.exists():
        return PathSize(bytes=0, files=0, stopped_early=False)
    total = 0
    files = 0

    def add_size(entry_path: Path, *, follow_symlinks: bool = False) -> bool:
        nonlocal total, files
        try:
            total += int(entry_path.stat(follow_symlinks=follow_symlinks).st_size)
            files += 1
        except OSError:
            return False
        return stop_after_bytes is not None and total > stop_after_bytes

    if root.is_file() or root.is_symlink():
        add_size(root, follow_symlinks=False)
        return PathSize(bytes=total, files=files, stopped_early=False)

    stack = [root]
    stopped = False
    while stack:
        current = stack.pop()
        try:
            with os.scandir(current) as entries:
                for entry in entries:
                    entry_path = Path(entry.path)
                    try:
                        if entry.is_symlink():
                            stopped = add_size(entry_path, follow_symlinks=False)
                        elif entry.is_file(follow_symlinks=False):
                            stopped = add_size(entry_path, follow_symlinks=False)
                        elif entry.is_dir(follow_symlinks=False):
                            stack.append(entry_path)
                    except OSError:
                        continue
                    if stopped:
                        return PathSize(bytes=total, files=files, stopped_early=True)
        except OSError:
            continue
    return PathSize(bytes=total, files=files, stopped_early=False)


def check_storage_budget(
    path: str | Path,
    *,
    purpose: str,
    env_var: str,
    default_max_bytes: int,
) -> StorageBudgetCheck:
    max_bytes = configured_budget(env_var, default_max_bytes)
    size = measure_path_size(path, stop_after_bytes=max_bytes)
    return StorageBudgetCheck(
        purpose=purpose,
        path=Path(path).expanduser().resolve(),
        current_bytes=size.bytes,
        max_bytes=max_bytes,
        env_var=env_var,
        files_scanned=size.files,
        stopped_early=size.stopped_early,
    )


def assert_storage_budget(
    path: str | Path,
    *,
    purpose: str,
    env_var: str,
    default_max_bytes: int,
) -> StorageBudgetCheck:
    check = check_storage_budget(
        path,
        purpose=purpose,
        env_var=env_var,
        default_max_bytes=default_max_bytes,
    )
    if not check.ok:
        raise StorageBudgetError(check)
    return check
