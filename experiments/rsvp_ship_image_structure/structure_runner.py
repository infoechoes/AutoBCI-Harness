from __future__ import annotations

from typing import Any


def build_candidates(context: dict[str, Any]) -> list[dict[str, Any]]:
    """Editable RSVP image structure hook.

    This is the single file the structure-sandbox researcher is allowed to edit.
    Fixed dataset loading, split construction, metrics, and artifact writing live
    in scripts/run_rsvp_ship_image_autoresearch.py.
    """

    evaluate_logistic = context["evaluate_logistic_feature_candidate"]
    split_rows = context["split_rows"]
    logistic_epochs = int(context["logistic_epochs"])
    return [
        evaluate_logistic(
            split_rows,
            model_family="image_structure_fusion_logistic",
            feature_family="fusion_lbp_hog_color_projection_edge",
            feature_label="fusion_lbp_hog_color_projection_edge",
            resize=None,
            logistic_epochs=logistic_epochs,
            lr=0.18,
            l2=0.02,
        )
    ]
