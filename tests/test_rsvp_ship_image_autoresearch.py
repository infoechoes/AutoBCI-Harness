from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest
from PIL import Image


def _write_image(path: Path, value: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    array = np.full((24, 24, 3), value, dtype=np.uint8)
    Image.fromarray(array).save(path)


def _make_dataset(root: Path) -> Path:
    dataset = root / "RSVP跨模态数据"
    for idx, value in enumerate([210, 215, 220, 225, 230, 235], start=1):
        _write_image(dataset / "target" / f"target_{idx}.jpg", value)
    for idx, value in enumerate([20, 25, 30, 35, 40, 45], start=1):
        _write_image(dataset / "nontarget" / f"image_{idx:04d}.jpg", value)
    # Duplicate copies should be audited but must not become independent samples.
    _write_image(dataset / "target" / "target_duplicate.jpg", 210)
    _write_image(dataset / "allimages" / "target_1.jpg", 210)
    _write_image(dataset / "allimages" / "image_0001.jpg", 20)
    _write_image(dataset / "allimages" / "extra_unlabeled.jpg", 128)
    return dataset


def test_build_dataset_audit_deduplicates_labeled_images_and_detects_missing_eeg(tmp_path: Path) -> None:
    from scripts.run_rsvp_ship_image_autoresearch import build_dataset_audit

    dataset = _make_dataset(tmp_path)

    audit, records = build_dataset_audit(dataset)

    assert audit["folders"]["target"]["files"] == 7
    assert audit["folders"]["nontarget"]["files"] == 6
    assert audit["labeled_unique_hashes"] == 12
    assert audit["duplicate_labeled_extra_files"] == 1
    assert audit["label_conflicts"] == []
    assert audit["modality_availability"]["image"] is True
    assert audit["modality_availability"]["eeg"] is False
    assert audit["modality_availability"]["event_table"] is False
    assert len(records) == 12


def test_split_manifest_keeps_duplicate_hashes_out_of_multiple_splits(tmp_path: Path) -> None:
    from scripts.run_rsvp_ship_image_autoresearch import build_dataset_audit, build_split_manifest

    dataset = _make_dataset(tmp_path)
    _audit, records = build_dataset_audit(dataset)

    split_records, summary = build_split_manifest(records)

    by_hash: dict[str, set[str]] = {}
    for item in split_records:
        by_hash.setdefault(item["sha1"], set()).add(item["split"])
    assert all(len(splits) == 1 for splits in by_hash.values())
    assert summary["train"]["target"] >= 1
    assert summary["val"]["target"] >= 1
    assert summary["test"]["target"] >= 1
    assert summary["train"]["nontarget"] >= 1
    assert summary["val"]["nontarget"] >= 1
    assert summary["test"]["nontarget"] >= 1


def test_split_manifest_supports_deterministic_split_salts(tmp_path: Path) -> None:
    from scripts.run_rsvp_ship_image_autoresearch import build_dataset_audit, build_split_manifest

    dataset = _make_dataset(tmp_path)
    _audit, records = build_dataset_audit(dataset)

    rows_a, _summary_a = build_split_manifest(records, split_salt="robust-a")
    rows_b, _summary_b = build_split_manifest(records, split_salt="robust-b")

    assignments_a = {(row["sha1"], row["label"]): row["split"] for row in rows_a}
    assignments_b = {(row["sha1"], row["label"]): row["split"] for row in rows_b}
    assert assignments_a != assignments_b
    assert set(assignments_a) == set(assignments_b)


def test_run_image_autoresearch_writes_artifacts_and_blocks_eeg_claim(tmp_path: Path) -> None:
    from scripts.run_rsvp_ship_image_autoresearch import run_image_autoresearch

    dataset = _make_dataset(tmp_path)
    output_dir = tmp_path / "artifacts"

    result = run_image_autoresearch(
        dataset_root=dataset,
        output_dir=output_dir,
        run_id="test-run",
        seed=7,
        logistic_epochs=120,
    )

    assert result["status"] == "completed_image_only"
    assert result["program_id"] == "rsvp_ship_image_only_v0"
    assert result["no_cross_modal_claim"] is True
    assert result["eeg_status"] == "blocked_missing_eeg_or_events"
    assert len(result["candidates"]) >= 10
    structure_candidates = [item for item in result["candidates"] if item["model_family"] == "image_structure_fusion_logistic"]
    assert structure_candidates
    assert structure_candidates[0]["config"]["feature_family"] == "fusion_lbp_hog_color_projection_edge"
    assert structure_candidates[0]["config"]["structure_change"] == "feature_fusion"
    assert result["selected_model"]["model_family"] in {item["model_family"] for item in result["candidates"]}
    assert result["test_metrics"]["balanced_accuracy"] >= 0.5
    assert "confusion_matrix" in result["test_metrics"]
    for key in ["dataset_audit", "split_manifest", "image_result", "comparison_report", "run_config"]:
        assert Path(result["artifacts"][key]).exists()
    saved = json.loads(Path(result["artifacts"]["image_result"]).read_text(encoding="utf-8"))
    assert saved["no_cross_modal_claim"] is True
    assert saved["storage_budget"]["dataset"]["ok"] is True
    assert saved["storage_budget"]["artifacts_after_run"]["ok"] is True


def test_run_image_autoresearch_rejects_dataset_over_budget(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from scripts.run_rsvp_ship_image_autoresearch import run_image_autoresearch

    dataset = _make_dataset(tmp_path)
    monkeypatch.setenv("AUTOBCI_MAX_DATASET_BYTES", "1B")

    with pytest.raises(RuntimeError) as exc_info:
        run_image_autoresearch(
            dataset_root=dataset,
            output_dir=tmp_path / "artifacts",
            run_id="budget-run",
            logistic_epochs=5,
        )

    assert "AUTOBCI_MAX_DATASET_BYTES" in str(exc_info.value)
    assert not (tmp_path / "artifacts" / "budget-run").exists()


def test_run_image_autoresearch_rejects_existing_artifacts_over_budget(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from scripts.run_rsvp_ship_image_autoresearch import run_image_autoresearch

    dataset = _make_dataset(tmp_path)
    output_dir = tmp_path / "artifacts"
    output_dir.mkdir()
    (output_dir / "old-result.json").write_text("{}", encoding="utf-8")
    monkeypatch.setenv("AUTOBCI_MAX_DATASET_BYTES", "0")
    monkeypatch.setenv("AUTOBCI_MAX_ARTIFACT_BYTES", "1B")

    with pytest.raises(RuntimeError) as exc_info:
        run_image_autoresearch(
            dataset_root=dataset,
            output_dir=output_dir,
            run_id="budget-run",
            logistic_epochs=5,
        )

    assert "AUTOBCI_MAX_ARTIFACT_BYTES" in str(exc_info.value)
    assert not (output_dir / "budget-run").exists()
