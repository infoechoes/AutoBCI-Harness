from __future__ import annotations

import argparse
import json
import os
import shutil
from pathlib import Path
from typing import Any

from scripts.serve_dashboard import DASHBOARD_DIR, build_status

DEFAULT_PUBLIC_MIRROR_DIR = Path(
    os.environ.get("AUTOBCI_PUBLIC_MIRROR_DIR", "artifacts/public_dashboard_mirror")
)


def export_dashboard_snapshot(
    *,
    output_dir: Path,
    dashboard_dir: Path = DASHBOARD_DIR,
    status_payload: dict[str, Any] | None = None,
) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    shutil.copy2(dashboard_dir / "index.html", output_dir / "index.html")

    assets_src = dashboard_dir / "assets"
    assets_dst = output_dir / "assets"
    if assets_src.exists():
        shutil.copytree(assets_src, assets_dst, dirs_exist_ok=True)

    payload = status_payload if status_payload is not None else build_status()
    snapshot_path = output_dir / "status.snapshot.json"
    snapshot_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return snapshot_path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Export the local dashboard as a static snapshot mirror.")
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=DEFAULT_PUBLIC_MIRROR_DIR,
        help="Directory that should receive index.html, assets/, and status.snapshot.json",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    snapshot_path = export_dashboard_snapshot(output_dir=args.output_dir)
    print(snapshot_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
