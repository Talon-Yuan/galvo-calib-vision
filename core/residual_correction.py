# -*- coding: utf-8 -*-
"""External-benchmark residual correction layer (residual feedback correction).

Industry path (Aerotech 2D Galvo Mark-and-Measure refinement workflow,
CN112077453B, etc.): keep the base calibration untouched and overlay a
correction layer fitted from an independent benchmark (e.g. 2D profile
projector / CMM / vision system) residuals. This module implements:

    fit_residual_correction : fit residual polynomial from (soft, real) pairs
    ResidualCorrection     : order-1/2/3 polynomial model dx=f(x,y), dy=g(x,y)
    merge_into_global      : merge order-1 residual terms into global coeffs
    save/load              : standalone res_fb_*.npz (never touches base npz)

Coordinate domain: all values are physical mm in the software output space.
The residual is defined as residual = real(2D) - soft(software), and applying
the correction means soft' = soft + residual(soft). This keeps the layer
independent of pixel-space details and valid on top of piecewise OR global
polynomial base conversion.
"""
from dataclasses import dataclass
from typing import Optional, Tuple

import numpy as np


@dataclass
class ResidualCorrection:
    """Residual feedback correction model.

    Attributes:
        order: polynomial order of the residual surface (1: [1,x,y]).
        cx: coefficients for dx residual (real_x - soft_x), shape (ncoef,).
        cy: coefficients for dy residual (real_y - soft_y), shape (ncoef,).
        source: free text describing the benchmark / fitting date.
        stats: dict with fitting statistics (rms_before, rms_after, max_before, max_after...).
        grid_xs / grid_ys: 1D grid node coordinates (mm), optional (scheme C).
        grid_dx / grid_dy: 2D residual table on the grid, shape (ny, nx).
        use_grid: if True, apply() uses bilinear interpolation on the grid
                  when inside the grid bbox, otherwise falls back to polynomial.
    """
    order: int = 1
    cx: Optional[np.ndarray] = None
    cy: Optional[np.ndarray] = None
    source: str = ""
    stats: Optional[dict] = None
    grid_xs: Optional[np.ndarray] = None
    grid_ys: Optional[np.ndarray] = None
    grid_dx: Optional[np.ndarray] = None
    grid_dy: Optional[np.ndarray] = None
    use_grid: bool = True

    def design(self, xy: np.ndarray) -> np.ndarray:
        """Build polynomial design matrix for physical coords (x, y)."""
        x = np.asarray(xy, np.float64)[:, 0]
        y = np.asarray(xy, np.float64)[:, 1]
        cols = [np.ones_like(x), x, y]
        if self.order >= 2:
            cols += [x ** 2, x * y, y ** 2]
        if self.order >= 3:
            cols += [x ** 3, x ** 2 * y, x * y ** 2, y ** 3]
        return np.stack(cols, axis=1)

    def apply(self, x: float, y: float) -> Tuple[float, float]:
        """Return correction increment (dx, dy) at software coordinate (x, y)."""
        if self.use_grid and self.grid_dx is not None and self.grid_xs is not None:
            dx, dy = self._apply_grid(x, y)
            if dx is not None:
                return dx, dy
        if self.cx is None or self.cy is None:
            return 0.0, 0.0
        A = self.design(np.array([[x, y]]))
        return float((A @ self.cx)[0]), float((A @ self.cy)[0])

    def _apply_grid(self, x: float, y: float) -> Tuple[Optional[float], Optional[float]]:
        """Bilinear interpolation on the residual grid.

        Returns (None, None) when the point is outside the grid bounding box
        (caller falls back to the polynomial), or (dx, dy) otherwise.
        """
        xs = self.grid_xs
        ys = self.grid_ys
        if xs is None or ys is None or self.grid_dx is None or self.grid_dy is None:
            return None, None
        nx, ny = len(xs), len(ys)
        if nx < 2 or ny < 2:
            return None, None
        x0, x1 = float(xs[0]), float(xs[-1])
        y0, y1 = float(ys[0]), float(ys[-1])
        if not (x0 <= x <= x1 and y0 <= y <= y1):
            return None, None
        # locate interval with clamping to valid cell (point is inside bbox so ok)
        ix = int(np.searchsorted(xs, x, side="right")) - 1
        iy = int(np.searchsorted(ys, y, side="right")) - 1
        ix = max(0, min(ix, nx - 2))
        iy = max(0, min(iy, ny - 2))
        xa, xb = float(xs[ix]), float(xs[ix + 1])
        ya, yb = float(ys[iy]), float(ys[iy + 1])
        tx = (x - xa) / (xb - xa) if xb > xa else 0.0
        ty = (y - ya) / (yb - ya) if yb > ya else 0.0
        dx00, dx01 = float(self.grid_dx[iy, ix]), float(self.grid_dx[iy, ix + 1])
        dx10, dx11 = float(self.grid_dx[iy + 1, ix]), float(self.grid_dx[iy + 1, ix + 1])
        dy00, dy01 = float(self.grid_dy[iy, ix]), float(self.grid_dy[iy, ix + 1])
        dy10, dy11 = float(self.grid_dy[iy + 1, ix]), float(self.grid_dy[iy + 1, ix + 1])
        dx = (dx00 * (1 - tx) + dx01 * tx) * (1 - ty) + (dx10 * (1 - tx) + dx11 * tx) * ty
        dy = (dy00 * (1 - tx) + dy01 * tx) * (1 - ty) + (dy10 * (1 - tx) + dy11 * tx) * ty
        return float(dx), float(dy)

    def apply_array(self, xy: np.ndarray) -> np.ndarray:
        """Vectorized correction increment, shape (N, 2)."""
        out = np.zeros((len(xy), 2), np.float64)
        if self.use_grid and self.grid_dx is not None and self.grid_xs is not None:
            xs = self.grid_xs
            ys = self.grid_ys
            if xs is not None and len(xs) >= 2 and len(ys) >= 2:
                x0, x1 = float(xs[0]), float(xs[-1])
                y0, y1 = float(ys[0]), float(ys[-1])
                inb = (xy[:, 0] >= x0) & (xy[:, 0] <= x1) & (xy[:, 1] >= y0) & (xy[:, 1] <= y1)
                if np.any(inb):
                    xv = xy[inb, 0]
                    yv = xy[inb, 1]
                    ix = np.clip(np.searchsorted(xs, xv, side="right") - 1, 0, len(xs) - 2)
                    iy = np.clip(np.searchsorted(ys, yv, side="right") - 1, 0, len(ys) - 2)
                    tx = np.where(xs[ix + 1] > xs[ix], (xv - xs[ix]) / (xs[ix + 1] - xs[ix]), 0.0)
                    ty = np.where(ys[iy + 1] > ys[iy], (yv - ys[iy]) / (ys[iy + 1] - ys[iy]), 0.0)
                    d00 = self.grid_dx[iy, ix]; d01 = self.grid_dx[iy, ix + 1]
                    d10 = self.grid_dx[iy + 1, ix]; d11 = self.grid_dx[iy + 1, ix + 1]
                    out[inb, 0] = (d00 * (1 - tx) + d01 * tx) * (1 - ty) + (d10 * (1 - tx) + d11 * tx) * ty
                    e00 = self.grid_dy[iy, ix]; e01 = self.grid_dy[iy, ix + 1]
                    e10 = self.grid_dy[iy + 1, ix]; e11 = self.grid_dy[iy + 1, ix + 1]
                    out[inb, 1] = (e00 * (1 - tx) + e01 * tx) * (1 - ty) + (e10 * (1 - tx) + e11 * tx) * ty
        # polynomial for points without grid coverage
        if self.cx is not None and self.cy is not None:
            A = self.design(xy)
            poly = np.stack([A @ self.cx, A @ self.cy], axis=1)
            if self.use_grid and self.grid_dx is not None and self.grid_xs is not None:
                xs = self.grid_xs
                ys = self.grid_ys
                if xs is not None and len(xs) >= 2 and len(ys) >= 2:
                    x0, x1 = float(xs[0]), float(xs[-1])
                    y0, y1 = float(ys[0]), float(ys[-1])
                    inb = (xy[:, 0] >= x0) & (xy[:, 0] <= x1) & (xy[:, 1] >= y0) & (xy[:, 1] <= y1)
                    out[~inb] = poly[~inb]
                    return out
            out = poly
        return out

    def summary(self) -> str:
        s = self.stats or {}
        return (f"残差校正层 order={self.order} | 修正前RMS={s.get('rms_before_mm', float('nan')):.4f}mm"
                f" max={s.get('max_before_mm', float('nan')):.4f}mm | "
                f"修正后RMS={s.get('rms_after_mm', float('nan')):.4f}mm"
                f" max={s.get('max_after_mm', float('nan')):.4f}mm")


def fit_residual_correction(soft_mm: np.ndarray, real_mm: np.ndarray,
                            order: int = 1, source: str = "") -> ResidualCorrection:
    """Fit residual correction: residual = real - soft as polynomial of soft.

    Args:
        soft_mm: Nx2 software-output physical coordinates (base conversion result).
        real_mm: Nx2 benchmark (2D projector) physical coordinates, same point order.
        order: polynomial order (1 recommended, 2 optional for strong curvature).
    Returns:
        ResidualCorrection with cx/cy fitted via least squares.
    """
    soft = np.asarray(soft_mm, np.float64)
    real = np.asarray(real_mm, np.float64)
    if soft.shape != real.shape or len(soft) < 3:
        raise ValueError("soft_mm and real_mm must be Nx2 with same length (>=3)")
    res = real - soft
    A = None
    rc = ResidualCorrection(order=order, source=source)
    A = rc.design(soft)
    cx, *_ = np.linalg.lstsq(A, res[:, 0], rcond=None)
    cy, *_ = np.linalg.lstsq(A, res[:, 1], rcond=None)
    rc.cx = np.asarray(cx, np.float64)
    rc.cy = np.asarray(cy, np.float64)

    pred = rc.apply_array(soft)
    rms_before = float(np.sqrt(np.mean(res[:, 0] ** 2 + res[:, 1] ** 2)))
    max_before = float(np.max(np.sqrt(res[:, 0] ** 2 + res[:, 1] ** 2)))
    rem = res - pred
    rms_after = float(np.sqrt(np.mean(rem[:, 0] ** 2 + rem[:, 1] ** 2)))
    max_after = float(np.max(np.sqrt(rem[:, 0] ** 2 + rem[:, 1] ** 2)))
    rc.stats = {
        "n_points": int(len(soft)),
        "rms_before_mm": rms_before,
        "max_before_mm": max_before,
        "rms_after_mm": rms_after,
        "max_after_mm": max_after,
        "mean_dx_mm": float(np.mean(res[:, 0])),
        "mean_dy_mm": float(np.mean(res[:, 1])),
    }
    return rc


def fit_residual_grid(soft_mm: np.ndarray, real_mm: np.ndarray,
                      grid_nx: int = 9, grid_ny: int = 9,
                      order_poly: int = 1, source: str = "") -> ResidualCorrection:
    """网格双线性残差拟合（加密网格方案，支持 9×9 / 13×13）。

    残差定义与 fit_residual_correction 一致：residual = real - soft。
    在 soft 自变量空间建立 grid_nx × grid_ny 规则网格，节点残差取邻域点
    反距离加权（IDW）估计；应用时网格内双线性插值、网格外回退多项式。

    Args:
        soft_mm: Nx2 software-output physical coordinates.
        real_mm: Nx2 benchmark physical coordinates, same point order.
        grid_nx / grid_ny: 网格节点数（>=2）。
        order_poly: 网格外回退多项式阶数（1/2/3）。
    Returns:
        ResidualCorrection（use_grid=True，含 grid_xs/grid_ys/grid_dx/grid_dy）。
    """
    soft = np.asarray(soft_mm, np.float64)
    real = np.asarray(real_mm, np.float64)
    if soft.shape != real.shape or len(soft) < 3:
        raise ValueError("soft_mm and real_mm must be Nx2 with same length (>=3)")
    res = real - soft
    nx = max(2, int(grid_nx))
    ny = max(2, int(grid_ny))
    xs = np.linspace(soft[:, 0].min(), soft[:, 0].max(), nx)
    ys = np.linspace(soft[:, 1].min(), soft[:, 1].max(), ny)
    span = float(np.sqrt((xs[-1] - xs[0]) ** 2 + (ys[-1] - ys[0]) ** 2)) + 1e-9

    def _node_value(nx0: float, ny0: float) -> Tuple[float, float]:
        # 邻域点 IDW：距离衰减，近点主导，避免远点拖拽
        d2 = (soft[:, 0] - nx0) ** 2 + (soft[:, 1] - ny0) ** 2
        d2 = np.maximum(d2, 1e-12)
        w = 1.0 / (d2 + (span * 0.05) ** 2)
        w = w / w.sum()
        return float(np.sum(w * res[:, 0])), float(np.sum(w * res[:, 1]))

    gdx = np.zeros((ny, nx))
    gdy = np.zeros((ny, nx))
    for j in range(ny):
        for i in range(nx):
            gdx[j, i], gdy[j, i] = _node_value(float(xs[i]), float(ys[j]))

    rc = ResidualCorrection(order=order_poly, source=source, use_grid=True)
    rc.grid_xs = xs
    rc.grid_ys = ys
    rc.grid_dx = gdx
    rc.grid_dy = gdy
    # 多项式回退层（网格外/降级兜底）
    A = rc.design(soft)
    rc.cx, *_ = np.linalg.lstsq(A, res[:, 0], rcond=None)
    rc.cy, *_ = np.linalg.lstsq(A, res[:, 1], rcond=None)

    pred = rc.apply_array(soft)
    rms_before = float(np.sqrt(np.mean(res[:, 0] ** 2 + res[:, 1] ** 2)))
    max_before = float(np.max(np.sqrt(res[:, 0] ** 2 + res[:, 1] ** 2)))
    rem = res - pred
    rms_after = float(np.sqrt(np.mean(rem[:, 0] ** 2 + rem[:, 1] ** 2)))
    max_after = float(np.max(np.sqrt(rem[:, 0] ** 2 + rem[:, 1] ** 2)))
    rc.stats = {
        "n_points": int(len(soft)),
        "rms_before_mm": rms_before,
        "max_before_mm": max_before,
        "rms_after_mm": rms_after,
        "max_after_mm": max_after,
        "grid_nx": nx,
        "grid_ny": ny,
    }
    return rc


def merge_into_global(coeff_x: np.ndarray, coeff_y: np.ndarray, order: int,
                      rc: ResidualCorrection) -> Tuple[np.ndarray, np.ndarray, int]:
    """Merge an order-1 residual correction into global polynomial coeffs.

    Base: mm = A(px) @ coeff  (order >= 1, columns [1, u, v, ...]).
    Residual layer is evaluated on base OUTPUT mm: mm' = mm + C(mm).
    For order-1 residual C(x,y) = [c0 + c1*x + c2*y, d0 + d1*x + d2*y],
    composition is exact and remains a polynomial of the same order:
        mm' = (coeff_x + c0*1 + c1*coeff_x + c2*coeff_y)  (constant terms get
              the affine combination; linear terms pick up c1/c2 on u/v).
    For order >= 2 the composition is NOT exact and this function raises.
    """
    if rc.order > 1:
        raise ValueError("merge_into_global only supports order-1 residual layer")
    if len(coeff_x) < 3 or len(coeff_y) < 3:
        raise ValueError("base coeffs must have at least order-1 terms")
    c0, c1, c2 = float(rc.cx[0]), float(rc.cx[1]), float(rc.cx[2])
    d0, d1, d2 = float(rc.cy[0]), float(rc.cy[1]), float(rc.cy[2])
    # affine transform on the output space: A = [[1+c1, c2], [d1, 1+d2]], t = [c0, d0]
    # base map (u,v) -> (x,y); merged map = A @ base + t
    a11, a12, a21, a22 = 1.0 + c1, c2, d1, 1.0 + d2
    new_x = np.array(coeff_x, np.float64).copy()
    new_y = np.array(coeff_y, np.float64).copy()
    # x' = a11 * X(u,v) + a12 * Y(u,v) + c0
    new_x[0] = a11 * coeff_x[0] + a12 * coeff_y[0] + c0
    new_x[1] = a11 * coeff_x[1] + a12 * coeff_y[1]
    new_x[2] = a11 * coeff_x[2] + a12 * coeff_y[2]
    # y' = a21 * X(u,v) + a22 * Y(u,v) + d0
    new_y[0] = a21 * coeff_x[0] + a22 * coeff_y[0] + d0
    new_y[1] = a21 * coeff_x[1] + a22 * coeff_y[1]
    new_y[2] = a21 * coeff_x[2] + a22 * coeff_y[2]
    return new_x, new_y, order


def save_residual_correction(path: str, rc: ResidualCorrection) -> None:
    """Save residual correction as standalone res_fb_*.npz."""
    if rc.cx is None or rc.cy is None:
        raise ValueError("residual correction not fitted")
    stats = rc.stats or {}
    kw = dict(
        order=np.array([rc.order]),
        cx=rc.cx, cy=rc.cy,
        source=np.array([rc.source]),
        n_points=np.array([stats.get("n_points", 0)]),
        rms_before_mm=np.array([stats.get("rms_before_mm", float("nan"))]),
        max_before_mm=np.array([stats.get("max_before_mm", float("nan"))]),
        rms_after_mm=np.array([stats.get("rms_after_mm", float("nan"))]),
        max_after_mm=np.array([stats.get("max_after_mm", float("nan"))]),
        use_grid=np.array([1 if rc.use_grid else 0]),
    )
    if rc.grid_xs is not None and rc.grid_dx is not None:
        kw["grid_xs"] = rc.grid_xs
        kw["grid_ys"] = rc.grid_ys
        kw["grid_dx"] = rc.grid_dx
        kw["grid_dy"] = rc.grid_dy
    np.savez_compressed(path, **kw)


def load_residual_correction(path: str) -> ResidualCorrection:
    """Load res_fb_*.npz produced by save_residual_correction."""
    d = np.load(path, allow_pickle=False)
    rc = ResidualCorrection(order=int(d["order"][0]))
    rc.cx = d["cx"].astype(np.float64)
    rc.cy = d["cy"].astype(np.float64)
    rc.source = str(d["source"][0]) if "source" in d.files else ""
    stats = {}
    for k in ("n_points", "rms_before_mm", "max_before_mm", "rms_after_mm", "max_after_mm"):
        if k in d.files:
            stats[k] = float(d[k][0])
    if "n_points" in stats:
        stats["n_points"] = int(stats["n_points"])
    rc.stats = stats or None
    rc.use_grid = bool(int(d["use_grid"][0])) if "use_grid" in d.files else False
    if "grid_xs" in d.files and "grid_dx" in d.files:
        rc.grid_xs = d["grid_xs"].astype(np.float64)
        rc.grid_ys = d["grid_ys"].astype(np.float64)
        rc.grid_dx = d["grid_dx"].astype(np.float64)
        rc.grid_dy = d["grid_dy"].astype(np.float64)
    return rc
