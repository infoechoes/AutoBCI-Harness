from __future__ import annotations

import hashlib
from collections import defaultdict
from pathlib import Path

from bci_autoresearch.storage_guard import format_bytes


DEFAULT_SCAN_ROOTS = ("artifacts", "output", "tmp", ".autobci")
TEXT_COMPRESS_SUFFIXES = {
    ".csv",
    ".html",
    ".json",
    ".jsonl",
    ".log",
    ".md",
    ".srt",
    ".tsv",
    ".txt",
    ".vtt",
    ".yaml",
    ".yml",
}


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _iter_files(root: Path) -> list[Path]:
    if not root.exists():
        return []
    if root.is_file():
        return [root]
    files: list[Path] = []
    for path in root.rglob("*"):
        try:
            if path.is_file() and not path.is_symlink():
                files.append(path)
        except OSError:
            continue
    return files


def _safe_stat_size(path: Path) -> int | None:
    try:
        return int(path.stat(follow_symlinks=False).st_size)
    except OSError:
        return None


def _relative_to_repo(path: Path, repo_root: Path) -> str:
    try:
        return str(path.relative_to(repo_root))
    except ValueError:
        return str(path)


def _build_duplicate_groups(
    files: list[Path],
    *,
    repo_root: Path,
    min_duplicate_bytes: int,
) -> list[dict[str, object]]:
    by_size: dict[int, list[Path]] = defaultdict(list)
    for path in files:
        size = _safe_stat_size(path)
        if size is None or size < min_duplicate_bytes:
            continue
        by_size[size].append(path)

    groups: list[dict[str, object]] = []
    for size, same_size_paths in by_size.items():
        if len(same_size_paths) < 2:
            continue
        by_hash: dict[str, list[Path]] = defaultdict(list)
        for path in same_size_paths:
            try:
                by_hash[_sha256(path)].append(path)
            except OSError:
                continue
        for digest, duplicate_paths in by_hash.items():
            if len(duplicate_paths) < 2:
                continue
            waste = size * (len(duplicate_paths) - 1)
            groups.append(
                {
                    "sha256": digest,
                    "size_bytes": size,
                    "size_human": format_bytes(size),
                    "copies": len(duplicate_paths),
                    "duplicate_waste_bytes": waste,
                    "duplicate_waste_human": format_bytes(waste),
                    "paths": sorted(_relative_to_repo(path, repo_root) for path in duplicate_paths),
                }
            )
    groups.sort(key=lambda item: int(item["duplicate_waste_bytes"]), reverse=True)
    return groups


def _build_compressible_files(
    files: list[Path],
    *,
    repo_root: Path,
    min_compressible_bytes: int,
) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for path in files:
        suffix = path.suffix.lower()
        if suffix not in TEXT_COMPRESS_SUFFIXES:
            continue
        size = _safe_stat_size(path)
        if size is None or size < min_compressible_bytes:
            continue
        rows.append(
            {
                "path": _relative_to_repo(path, repo_root),
                "size_bytes": size,
                "size_human": format_bytes(size),
                "suffix": suffix,
                "suggested_action": "compress_text_artifact",
            }
        )
    rows.sort(key=lambda item: int(item["size_bytes"]), reverse=True)
    return rows


def build_storage_optimization_report(
    repo_root: str | Path,
    *,
    scan_roots: tuple[str, ...] = DEFAULT_SCAN_ROOTS,
    min_duplicate_bytes: int = 5 * 1024 * 1024,
    min_compressible_bytes: int = 1024 * 1024,
) -> dict[str, object]:
    root = Path(repo_root).expanduser().resolve()
    existing_roots = [root / item for item in scan_roots if (root / item).exists()]
    files: list[Path] = []
    root_rows: list[dict[str, object]] = []
    for scan_root in existing_roots:
        root_files = _iter_files(scan_root)
        root_bytes = sum(size for path in root_files if (size := _safe_stat_size(path)) is not None)
        root_rows.append(
            {
                "path": _relative_to_repo(scan_root, root),
                "files": len(root_files),
                "bytes": root_bytes,
                "human": format_bytes(root_bytes),
            }
        )
        files.extend(root_files)

    duplicate_groups = _build_duplicate_groups(files, repo_root=root, min_duplicate_bytes=int(min_duplicate_bytes))
    compressible_files = _build_compressible_files(files, repo_root=root, min_compressible_bytes=int(min_compressible_bytes))
    scanned_bytes = sum(size for path in files if (size := _safe_stat_size(path)) is not None)
    duplicate_waste = sum(int(item["duplicate_waste_bytes"]) for item in duplicate_groups)
    compressible_bytes = sum(int(item["size_bytes"]) for item in compressible_files)
    recommendations: list[str] = []
    if duplicate_groups:
        recommendations.append("Replace repeated large files with manifest references or a content-addressed blob store.")
    if compressible_files:
        recommendations.append("Compress text-heavy audit/export files with gzip or zstd after they become cold.")
    if not recommendations:
        recommendations.append("No immediate storage optimization candidates found in default local artifact roots.")
    return {
        "ok": True,
        "mode": "audit_only",
        "repo_root": str(root),
        "roots": root_rows,
        "summary": {
            "scanned_roots": len(existing_roots),
            "scanned_files": len(files),
            "scanned_bytes": scanned_bytes,
            "scanned_human": format_bytes(scanned_bytes),
            "duplicate_groups": len(duplicate_groups),
            "duplicate_waste_bytes": duplicate_waste,
            "duplicate_waste_human": format_bytes(duplicate_waste),
            "compressible_files": len(compressible_files),
            "compressible_candidate_bytes": compressible_bytes,
            "compressible_candidate_human": format_bytes(compressible_bytes),
        },
        "duplicate_groups": duplicate_groups,
        "compressible_files": compressible_files,
        "recommendations": recommendations,
    }
