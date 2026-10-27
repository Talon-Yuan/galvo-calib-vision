# -*- coding: utf-8 -*-
"""分区/局部校正场（阶段三核心模块）。

背景：全局多项式校正场（fit_correction_field）对大幅面非线性畸变（枕形/桶形/
局部翘曲）建模能力有限——单一多项式在全幅残差平均后，局部系统偏差仍可达
0.05~0.12mm，逼近误差预算上限。本模块把像素平面划分为网格分区，每区用低阶
局部多项式拟合像素px→物理mm，分区边界用反距离加权(IDW)融合，兼顾局部精度
与全场连续性。

设计要点：
1. 分区拟合：按检测点包围盒切分为 grid_nx × grid_ny 网格，每分区用区内点
   做局部多项式最小二乘（order 可配置，默认1阶仿射即可吸收局部线性畸变；
   点稀疏或点数不足时自动降阶/合并）。
2. 边界融合：应用时取距离最近的两个分区（跨区时）按到分区中心距离做
   IDW 加权，消除分区边界跳变，保证全场 C0 连续。
3. 兼容性：提供 fit_piecewise / apply_piecewise / compute_piecewise_residuals
   三函数，签名风格与 calibrator.fit_correction_field 一致，可独立验证后
   再接入 calibrate() 与 GUI。
"""
from typing import Optional, Tuple

import numpy as np


def _poly_design_local(uv: np.ndarray, order: int) -> np.ndarray:
    """局部多项式设计矩阵（含常数项）。order=1: [1,u,v]；order=2: +[u²,uv,v²]。"""
    u, v = uv[:, 0], uv[:, 1]
    cols = [np.ones_like(u), u, v]
    if order >= 2:
        cols += [u ** 2, u * v, v ** 2]
    return np.stack(cols, axis=1)


def _fit_local(px: np.ndarray, mm: np.ndarray, order: int) -> Optional[Tuple[np.ndarray, np.ndarray]]:
    """对一组点拟合局部多项式，点数不足或奇异时返回 None。"""
    n = len(px)
    need = 3 if order == 1 else 6
    if n < need:
        order_use = 1 if n >= 3 else 0
        need_use = 3 if order_use == 1 else 1
        if n < need_use:
            return None
        order = order_use
    A = _poly_design_local(px, order)
    try:
        cx, *_ = np.linalg.lstsq(A, mm[:, 0], rcond=None)
        cy, *_ = np.linalg.lstsq(A, mm[:, 1], rcond=None)
    except np.linalg.LinAlgError:
        return None
    return cx, cy


class PiecewiseCorrection:
    """分区校正场。

    属性：
        grid_nx / grid_ny: 分区网格数
        order: 局部多项式阶数
        bounds: 像素包围盒 (x0, y0, x1, y1)
        cells: 每个分区为 dict(x0,y0,x1,y1,cx,cy,order,cx0,cy0,npts)
        centers: 各分区中心 (nx*ny, 2)
    """

    def __init__(self, grid_nx: int = 3, grid_ny: int = 3, order: int = 1):
        self.grid_nx = max(1, int(grid_nx))
        self.grid_ny = max(1, int(grid_ny))
        self.order = order
        self.bounds: Optional[np.ndarray] = None
        self.cells: list = []
        self.centers: Optional[np.ndarray] = None
        self.span: Optional[float] = None

    def fit(self, px: np.ndarray, mm: np.ndarray) -> "PiecewiseCorrection":
        """拟合分区校正场。px/mm 均为 Nx2。返回 self。"""
        px = np.asarray(px, np.float64)
        mm = np.asarray(mm, np.float64)
        x0, y0 = px[:, 0].min(), px[:, 1].min()
        x1, y1 = px[:, 0].max(), px[:, 1].max()
        self.bounds = np.array([x0, y0, x1, y1])
        w = max(x1 - x0, 1e-6)
        h = max(y1 - y0, 1e-6)
        # 分区边界（含小外扩，避免边缘点贴边界）
        self.cells = []
        self.centers = np.zeros((self.grid_nx * self.grid_ny, 2))
        k = 0
        for iy in range(self.grid_ny):
            for ix in range(self.grid_nx):
                cx0 = x0 + w * ix / self.grid_nx
                cx1 = x0 + w * (ix + 1) / self.grid_nx
                cy0 = y0 + h * iy / self.grid_ny
                cy1 = y0 + h * (iy + 1) / self.grid_ny
                mask = (
                    (px[:, 0] >= cx0 - 0.05 * w / self.grid_nx)
                    & (px[:, 0] <= cx1 + 0.05 * w / self.grid_nx)
                    & (px[:, 1] >= cy0 - 0.05 * h / self.grid_ny)
                    & (px[:, 1] <= cy1 + 0.05 * h / self.grid_ny)
                )
                idx = np.where(mask)[0]
                if len(idx) == 0:
                    k += 1
                    continue
                fit = _fit_local(px[idx], mm[idx], self.order)
                if fit is None:
                    k += 1
                    continue
                cx, cy = fit
                ncoef = cx.shape[0]
                cell_order = 1 if ncoef == 3 else (2 if ncoef == 6 else 0)
                self.cells.append({
                    "x0": cx0, "y0": cy0, "x1": cx1, "y1": cy1,
                    "cx": cx, "cy": cy,
                    "order": cell_order,
                    "center": np.array([(cx0 + cx1) / 2, (cy0 + cy1) / 2]),
                    "npts": int(len(idx)),
                })
                self.centers[k] = self.cells[-1]["center"]
                k += 1
        self.centers = self.centers[:k]
        # 特征尺度：用于 IDW 距离归一化
        self.span = float(np.sqrt(w * w + h * h))
        if len(self.cells) == 0:
            # 全空（理论上不会）：退化为全局仿射
            fit = _fit_local(px, mm, 1)
            if fit is not None:
                cx, cy = fit
                self.cells = [{
                    "x0": x0, "y0": y0, "x1": x1, "y1": y1,
                    "cx": cx, "cy": cy, "order": 1,
                    "center": np.array([(x0 + x1) / 2, (y0 + y1) / 2]),
                    "npts": len(px),
                }]
                self.centers = np.array([self.cells[0]["center"]])
        return self

    def apply(self, u: float, v: float) -> Tuple[float, float]:
        """应用校正场：像素(u,v) → 物理(mm)。分区加权融合保证连续性。"""
        if not self.cells:
            raise RuntimeError("PiecewiseCorrection 未拟合或分区为空")
        p = np.array([u, v])
        # 到各分区中心的距离（按特征尺度归一化）
        d2 = np.sum((self.centers - p) ** 2, axis=1)
        d2 = np.maximum(d2, 1e-12)
        # 最近分区单独处理：若几乎重合直接用该分区
        if d2.min() < 1e-9:
            i = int(np.argmin(d2))
            cell = self.cells[i]
            A = _poly_design_local(np.array([[u, v]]), cell["order"])
            out = A @ np.stack([cell["cx"], cell["cy"]], axis=1)
            return float(out[0, 0]), float(out[0, 1])
        w = 1.0 / d2
        w = w / w.sum()
        out = np.zeros(2)
        for i, cell in enumerate(self.cells):
            A = _poly_design_local(np.array([[u, v]]), cell["order"])
            out += w[i] * (A @ np.stack([cell["cx"], cell["cy"]], axis=1))[0]
        return float(out[0]), float(out[1])

    def predict(self, px: np.ndarray) -> np.ndarray:
        """批量预测：Nx2 像素 → Nx2 物理mm。"""
        px = np.asarray(px, np.float64)
        out = np.zeros((len(px), 2))
        # 向量化：按最近分区分组避免每点全分区加权（边界融合仅邻近分区）
        for i, cell in enumerate(self.cells):
            # 权重：距离归一化后用 exp 衰减，仅邻近分区参与
            pass
        # 简单稳妥：逐点调用 apply（分区数少、点数几千时足够快）
        for i in range(len(px)):
            out[i] = self.apply(px[i, 0], px[i, 1])
        return out

    def compute_residuals(self, px: np.ndarray, mm: np.ndarray) -> Tuple[float, float, np.ndarray]:
        """残差（mm）：分区校正场预测物理坐标 vs 理想物理坐标。返回 (rms_mm, max_mm, res_mm)。"""
        pred = self.predict(px)
        res_mm = pred - np.asarray(mm, np.float64)
        rms_mm = float(np.sqrt(np.mean(res_mm[:, 0] ** 2 + res_mm[:, 1] ** 2)))
        max_mm = float(np.max(np.sqrt(res_mm[:, 0] ** 2 + res_mm[:, 1] ** 2)))
        return rms_mm, max_mm, res_mm


def fit_piecewise(px: np.ndarray, mm: np.ndarray, grid_nx: int = 3, grid_ny: int = 3,
                  order: int = 1) -> PiecewiseCorrection:
    """兼容式入口：拟合分区校正场。"""
    return PiecewiseCorrection(grid_nx, grid_ny, order).fit(px, mm)


def apply_piecewise(u: float, v: float, model: PiecewiseCorrection) -> Tuple[float, float]:
    return model.apply(u, v)


def compute_piecewise_residuals(px: np.ndarray, mm: np.ndarray,
                                model: PiecewiseCorrection) -> Tuple[float, float, np.ndarray]:
    """残差（mm）：分区校正场预测物理坐标 vs 理想物理坐标。返回 (rms_mm, max_mm, res_mm)。"""
    pred = model.predict(px)
    res_mm = pred - np.asarray(mm, np.float64)
    rms_mm = float(np.sqrt(np.mean(res_mm[:, 0] ** 2 + res_mm[:, 1] ** 2)))
    max_mm = float(np.max(np.sqrt(res_mm[:, 0] ** 2 + res_mm[:, 1] ** 2)))
    return rms_mm, max_mm, res_mm
