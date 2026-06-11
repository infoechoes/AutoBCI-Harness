from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import Any

import numpy as np
from xgboost import XGBRegressor


@dataclass
class MultiOutputXGBRegressor:
    n_estimators: int = 600
    max_depth: int = 6
    learning_rate: float = 0.05
    subsample: float = 0.8
    colsample_bytree: float = 0.8
    reg_lambda: float = 1.0
    objective: str = "reg:squarederror"
    tree_method: str = "hist"
    n_jobs: int = 1
    output_parallelism: int = 1
    random_state: int = 0

    def __post_init__(self) -> None:
        self.estimators_: list[XGBRegressor] = []

    def _base_params(self) -> dict[str, Any]:
        return {
            "n_estimators": self.n_estimators,
            "max_depth": self.max_depth,
            "learning_rate": self.learning_rate,
            "subsample": self.subsample,
            "colsample_bytree": self.colsample_bytree,
            "reg_lambda": self.reg_lambda,
            "objective": self.objective,
            "tree_method": self.tree_method,
            "n_jobs": self.n_jobs,
            "random_state": self.random_state,
        }

    def _fit_single_output(self, x: np.ndarray, y: np.ndarray, dim_idx: int) -> tuple[int, XGBRegressor]:
        estimator = XGBRegressor(**self._base_params())
        estimator.fit(x, np.ascontiguousarray(y[:, dim_idx], dtype=np.float32))
        return dim_idx, estimator

    def _predict_single_output(self, estimator: XGBRegressor, x: np.ndarray, dim_idx: int) -> tuple[int, np.ndarray]:
        return dim_idx, estimator.predict(x).astype(np.float32)

    def fit(self, x: np.ndarray, y: np.ndarray) -> "MultiOutputXGBRegressor":
        x = np.asarray(x, dtype=np.float32)
        y = np.asarray(y, dtype=np.float32)
        if y.ndim != 2:
            raise ValueError("MultiOutputXGBRegressor expects y with shape (n_samples, n_outputs).")
        self.estimators_ = []
        parallelism = max(1, min(int(self.output_parallelism), int(y.shape[1])))
        if parallelism == 1:
            for dim_idx in range(y.shape[1]):
                _dim_idx, estimator = self._fit_single_output(x, y, dim_idx)
                self.estimators_.append(estimator)
            return self

        fitted: list[XGBRegressor | None] = [None] * y.shape[1]
        with ThreadPoolExecutor(max_workers=parallelism) as executor:
            futures = [executor.submit(self._fit_single_output, x, y, dim_idx) for dim_idx in range(y.shape[1])]
            for future in futures:
                dim_idx, estimator = future.result()
                fitted[dim_idx] = estimator
        self.estimators_ = [estimator for estimator in fitted if estimator is not None]
        return self

    def predict(self, x: np.ndarray) -> np.ndarray:
        if not self.estimators_:
            raise RuntimeError("Call fit() before predict().")
        x = np.asarray(x, dtype=np.float32)
        parallelism = max(1, min(int(self.output_parallelism), len(self.estimators_)))
        if parallelism == 1:
            preds = [estimator.predict(x).astype(np.float32) for estimator in self.estimators_]
            return np.stack(preds, axis=1).astype(np.float32)

        preds_by_idx: list[np.ndarray | None] = [None] * len(self.estimators_)
        with ThreadPoolExecutor(max_workers=parallelism) as executor:
            futures = [
                executor.submit(self._predict_single_output, estimator, x, dim_idx)
                for dim_idx, estimator in enumerate(self.estimators_)
            ]
            for future in futures:
                dim_idx, pred = future.result()
                preds_by_idx[dim_idx] = pred
        preds = [pred for pred in preds_by_idx if pred is not None]
        return np.stack(preds, axis=1).astype(np.float32)
