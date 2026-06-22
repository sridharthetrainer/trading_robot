"""
trendline_engine.py — fit a line through a set of pivot points.

Methods: least-squares (numpy polyfit), RANSAC (robust to outliers, sklearn if
available), and a plain OLS r². Returns slope/intercept/r²/touch_count so callers
can judge line quality. x is the BAR INDEX, y is price.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Sequence, Tuple

import numpy as np

logger = logging.getLogger("pattern_engine")

try:
    from sklearn.linear_model import RANSACRegressor, LinearRegression
    _HAS_SK = True
except Exception:
    _HAS_SK = False


@dataclass
class TrendLine:
    slope: float
    intercept: float
    r_squared: float
    touch_count: int
    method: str

    def y_at(self, x: float) -> float:
        return self.slope * x + self.intercept


def _r2(x: np.ndarray, y: np.ndarray, slope: float, intercept: float) -> float:
    pred = slope * x + intercept
    ss_res = float(np.sum((y - pred) ** 2))
    ss_tot = float(np.sum((y - y.mean()) ** 2))
    return 1.0 - ss_res / ss_tot if ss_tot > 0 else 0.0


def fit_trendline(points: Sequence[Tuple[int, float]], method: str = "least_squares",
                  touch_tolerance_pct: float = 0.0015) -> TrendLine:
    if len(points) < 2:
        return TrendLine(0.0, 0.0, 0.0, len(points), method)
    x = np.array([p[0] for p in points], dtype=float)
    y = np.array([p[1] for p in points], dtype=float)

    if method == "ransac" and _HAS_SK and len(points) >= 3:
        try:
            model = RANSACRegressor(LinearRegression(), random_state=0)
            model.fit(x.reshape(-1, 1), y)
            slope = float(model.estimator_.coef_[0])
            intercept = float(model.estimator_.intercept_)
        except Exception:
            slope, intercept = np.polyfit(x, y, 1)
    else:
        slope, intercept = np.polyfit(x, y, 1)

    slope = float(slope); intercept = float(intercept)
    # touch count: points whose price is within tolerance of the line
    tol = touch_tolerance_pct
    touches = int(np.sum(np.abs((slope * x + intercept) - y) <= np.abs(y) * tol))
    return TrendLine(slope, intercept, _r2(x, y, slope, intercept), touches, method)
