# -*- coding: utf-8 -*-
"""棋盘格校准：角点检测→理想物理坐标→校正场多项式拟合→残差硬性校验→精度预估。
F9 必需前置；误差预算：总体≤0.05mm（校正残差≤0.03mm + 检测≤0.02mm）。"""
from dataclasses import dataclass
from typing import List, Optional, Tuple

import cv2
import math
import numpy as np

from .config import ChessboardParams
from .piecewise_correction import PiecewiseCorrection, fit_piecewise, compute_piecewise_residuals


@dataclass
class CalibResult:
    ok: bool
    detected: np.ndarray          # Nx2 检测角点像素坐标
    ideal_px: np.ndarray          # Nx2 理想像素坐标
    ideal_mm: np.ndarray          # Nx2 理想物理坐标(mm)
    coeff_x: Optional[np.ndarray] = None
    coeff_y: Optional[np.ndarray] = None
    rms_mm: float = float("inf")
    max_mm: float = float("inf")
    message: str = ""
    piecewise: Optional[PiecewiseCorrection] = None  # 分区校正场（use_piecewise=True 时非 None）
    use_piecewise: bool = False


def build_ideal_points(p: ChessboardParams) -> np.ndarray:
    """按内角点行列、单格/间隔生成理想物理坐标(mm)。原点=左上角点。
    dilation(膨胀系数)整体修正步长：>1=图形实际尺寸大于标称(膨胀)，<1=收缩。"""
    rows, cols = p.rows, p.cols
    sq = p.square_mm
    gap = p.gap_mm
    # 实测尺寸优先（F10）：实测单格边长直接替换标称值
    if p.measured_square_mm and p.measured_square_mm > 0:
        sq = p.measured_square_mm
    step = (sq + gap) * max(0.5, float(p.dilation))
    us, vs = np.meshgrid(np.arange(cols), np.arange(rows))
    pts = np.stack([us * step, vs * step], axis=-1).reshape(-1, 2).astype(np.float64)
    # 实测总宽高：对整板做尺度校正（约束总跨度到实测值，抑制打印尺度偏差<0.01%）
    if p.measured_w_mm and p.measured_h_mm and p.measured_w_mm > 0 and p.measured_h_mm > 0:
        nominal_w = (cols - 1) * step
        nominal_h = (rows - 1) * step
        if nominal_w > 0 and nominal_h > 0:
            sx = p.measured_w_mm / nominal_w
            sy = p.measured_h_mm / nominal_h
            pts[:, 0] *= sx
            pts[:, 1] *= sy
    return pts


def detect_chessboard(gray: np.ndarray, p: ChessboardParams, progress_cb=None) -> Optional[np.ndarray]:
    """检测棋盘格角点，返回 Nx2 亚像素像素坐标（与 ideal 逐行对应）。
    gap>0 时 OpenCV 内置检测器无法识别（期望紧邻棋盘），走自研方块角点提取；
    gap=0 时优先 SB，失败回退自研。"""
    gray = np.ascontiguousarray(gray)  # 超大整图灰度化后可能非连续，OpenCV SB 检测要求连续内存
    if p.gap_mm > 0:
        return _detect_gapped_chessboard(gray, p, progress_cb)
    pattern = (p.cols, p.rows)
    flags = cv2.CALIB_CB_ADAPTIVE_THRESH + cv2.CALIB_CB_NORMALIZE_IMAGE
    # v3.3.2 性能修复：超大整图（>2亿像素）内置检测器不可用——SB 的 calcFeatureMap 在该尺度
    # 直接抛 cv2.error；经典 findChessboardCorners 在该尺度极慢（实测 5.57 亿像素约 120s），
    # 且其结果与"内部降采样检测+原图亚像素细化"逐点相同（实测 266 点差 0.0000px，
    # 拟合残差同为 RMS=0.029768/Max=0.098793mm）。故直接走原有的降采样回退路径。
    # 实测单次检测 144.5s → 5.8s（24.4x）。降采样失败时仍回退到下面的内置检测器尝试。
    if gray.size > 200_000_000:
        down = _detect_downscaled(gray, pattern, flags, p, progress_cb)
        if down is not None:
            return down
    if progress_cb:
        progress_cb(0.15)
    if p.corner_method == "sb":
        try:
            found, corners = cv2.findChessboardCornersSB(gray, pattern, flags)
        except cv2.error:
            # OpenCV5 SB 对超大图(>2亿像素)内部 calcFeatureMap 直接抛错，回退经典检测器
            found, corners = False, None
        if not found:
            found, corners = cv2.findChessboardCorners(gray, pattern, flags)
    else:
        found, corners = cv2.findChessboardCorners(gray, pattern, flags)
    if progress_cb:
        progress_cb(0.6)
    if not found or corners is None:
        # 超大整图：OpenCV 内置检测器不可用（SB 内部 FastX 限制 / 经典检测器失效），
        # 自动内部降采样检测 + 坐标还原 + 原图亚像素细化，保持整图模式对外语义
        if gray.size > 200_000_000:
            down = _detect_downscaled(gray, pattern, flags, p, progress_cb)
            if down is not None:
                return down
        return _detect_gapped_chessboard(gray, p, progress_cb)
    corners = corners.reshape(-1, 2)
    if p.subpixel:
        try:
            criteria = (cv2.TERM_CRITERIA_EPS + cv2.TERM_CRITERIA_MAX_ITER, 60, 0.001)
            corners = cv2.cornerSubPix(gray, corners.astype(np.float32), (11, 11), (-1, -1), criteria)
        except cv2.error:
            pass
    return corners.astype(np.float64)


def _detect_downscaled(gray: np.ndarray, pattern, flags, p: "ChessboardParams", progress_cb=None) -> Optional[np.ndarray]:
    """超大整图（>2亿像素）检测回退：内部降采样到检测器可用尺度，坐标还原到整图坐标系，
    再用原图亚像素细化，保持整图模式对外语义（调用方仍拿到整图尺度角点）。"""
    h, w = gray.shape
    ds = 2.0
    if w * h > 800_000_000:
        ds = 4.0  # >8亿像素再降一档
    small = cv2.resize(gray, (int(round(w / ds)), int(round(h / ds))), interpolation=cv2.INTER_AREA)
    if progress_cb:
        progress_cb(0.7)
    found, corners = False, None
    # v3.3.2：与主路径 :75-79 一致，SB 在个别尺度仍可能抛 cv2.error（OpenCV 内部 FastX 限制），
    # 必须捕获后回退经典检测器；否则异常会穿透到 UI 线程池导致"处理中…"卡死且无报错。
    try:
        found, corners = cv2.findChessboardCornersSB(small, pattern, flags)
    except cv2.error:
        found, corners = False, None
    if not found:
        found, corners = cv2.findChessboardCorners(small, pattern, flags)
    if progress_cb:
        progress_cb(0.85)
    if not found or corners is None:
        return None
    corners = corners.reshape(-1, 2) * ds
    if p.subpixel:
        try:
            criteria = (cv2.TERM_CRITERIA_EPS + cv2.TERM_CRITERIA_MAX_ITER, 60, 0.001)
            corners = cv2.cornerSubPix(gray, corners.astype(np.float32), (11, 11), (-1, -1), criteria)
        except cv2.error:
            pass
    return corners.astype(np.float64)


def _cluster_1d(values: np.ndarray, thr: float) -> List[float]:
    """一维排序后按间距聚类，返回各类均值（升序）。"""
    v = np.sort(np.asarray(values, dtype=np.float64))
    groups = [[v[0]]]
    for x in v[1:]:
        if x - groups[-1][-1] > thr:
            groups.append([x])
        else:
            groups[-1].append(x)
    return [float(np.mean(g)) for g in groups]


def _detect_gapped_chessboard(gray: np.ndarray, p: ChessboardParams,
                              progress_cb=None) -> Optional[np.ndarray]:
    """间隔棋盘自研检测：找黑方块连通域→方块角点→亚像素精化→按网格聚类→取内角点。
    返回 (rows*cols, 2) 行优先角点，失败返回 None。"""
    rows, cols = p.rows, p.cols
    if progress_cb:
        progress_cb(0.05)
    _, bw = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)
    bw = cv2.morphologyEx(bw, cv2.MORPH_OPEN, np.ones((3, 3), np.uint8))
    n, _, stats, _ = cv2.connectedComponentsWithStats(bw, connectivity=4)
    squares = []
    for i in range(1, n):
        x, y, w, h, area = stats[i]
        if w < 4 or h < 4 or area < 20:
            continue
        squares.append((x, y, w, h, area))
    if progress_cb:
        progress_cb(0.2)
    if len(squares) < max(4, (rows + 1) * (cols + 1) // 4):
        return None
    # 实测方块尺度（连通域宽高中位数），用于聚类阈值
    ws = np.array([s[2] for s in squares], np.float64)
    sq_px_est = float(np.median(ws))
    thr = max(6.0, sq_px_est * 0.75)
    # 收集方块轮廓角点
    raw_corners = []
    for (x, y, w, h, _) in squares:
        roi = bw[y:y + h, x:x + w]
        cnts, _ = cv2.findContours(roi, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        if not cnts:
            continue
        cnt = cnts[0]
        eps = 0.02 * cv2.arcLength(cnt, True)
        approx = cv2.approxPolyDP(cnt, eps, True)
        pts = approx.reshape(-1, 2).astype(np.float32) if len(approx) == 4 else \
            np.array([[0, 0], [w - 1, 0], [w - 1, h - 1], [0, h - 1]], np.float32)
        for px_, py_ in pts:
            raw_corners.append([x + px_, y + py_])
    if progress_cb:
        progress_cb(0.5)
    if len(raw_corners) < (rows + 1) * (cols + 1):
        return None
    corners = np.array(raw_corners, np.float64).reshape(-1, 2)
    # 亚像素精化
    try:
        criteria = (cv2.TERM_CRITERIA_EPS + cv2.TERM_CRITERIA_MAX_ITER, 60, 0.001)
        corners = cv2.cornerSubPix(gray, corners.astype(np.float32), (5, 5), (-1, -1), criteria).astype(np.float64)
    except cv2.error:
        pass
    if progress_cb:
        progress_cb(0.7)
    # 聚类行边界 y、列边界 x。方块左/上边缘落在网格边界，右/下边缘落在间隔中间，
    # 聚类结果呈 [边界, 间隔线, 边界, ...] 交替，取偶数索引即网格边界。
    ys = _cluster_1d(corners[:, 1], thr)
    xs = _cluster_1d(corners[:, 0], thr)
    if len(ys) >= 2 * (rows + 1) - 1:
        ys = ys[0::2]
    if len(xs) >= 2 * (cols + 1) - 1:
        xs = xs[0::2]
    if len(ys) < rows + 1 or len(xs) < cols + 1:
        return None
    ys = ys[:rows + 1]
    xs = xs[:cols + 1]
    # 内角点网格（行边界1..rows × 列边界1..cols），行优先
    pts = []
    for i in range(1, rows + 1):
        for j in range(1, cols + 1):
            pts.append([xs[j], ys[i]])
    if progress_cb:
        progress_cb(0.9)
    return np.array(pts, np.float64)


def _poly_design(uv: np.ndarray, order: int) -> np.ndarray:
    """物理坐标(u,v) → 多项式设计矩阵（含常数项），2阶含 u,v,u²,uv,v²。"""
    u, v = uv[:, 0], uv[:, 1]
    cols = [np.ones_like(u), u, v]
    if order >= 2:
        cols += [u ** 2, u * v, v ** 2]
    if order >= 3:
        cols += [u ** 3, u ** 2 * v, u * v ** 2, v ** 3]
    return np.stack(cols, axis=1)


def fit_correction_field(px: np.ndarray, mm: np.ndarray, order: int = 2):
    """拟合 像素px → 物理mm 的校正场（最小二乘多项式）。
    输入为检测到的像素坐标，输出为该点的物理坐标(mm)，供 GUI/Excel 直接使用。"""
    # C01 守卫：lstsq 对任何输入都返回"最小二乘解"，点数不足会静默坏拟合。
    # 合法输入下本守卫不改变任何数值结果（仅拦非法输入）。
    px = np.asarray(px, np.float64).reshape(-1, 2)
    mm = np.asarray(mm, np.float64).reshape(-1, 2)
    if px.shape != mm.shape:
        raise ValueError("校正场拟合输入形状不一致：%s vs %s" % (px.shape, mm.shape))
    _ncol = _poly_design(px[:1], order).shape[1]
    if len(px) < _ncol + 2:
        raise ValueError("校正场拟合点数不足：order=%d 需 >= %d 点，实际 %d" % (order, _ncol + 2, len(px)))
    A = _poly_design(px, order)
    coeff_x, *_ = np.linalg.lstsq(A, mm[:, 0], rcond=None)
    coeff_y, *_ = np.linalg.lstsq(A, mm[:, 1], rcond=None)
    return coeff_x, coeff_y


def apply_correction(u: float, v: float, coeff_x: np.ndarray, coeff_y: np.ndarray, order: int = 2):
    A = _poly_design(np.array([[u, v]]), order)
    out = A @ np.stack([coeff_x, coeff_y], axis=1)
    return float(out[0, 0]), float(out[0, 1])


def compute_residuals(px: np.ndarray, mm: np.ndarray, coeff_x, coeff_y, order: int) -> Tuple[float, float, np.ndarray]:
    """残差（mm）：校正场预测物理坐标 vs 理想物理坐标。返回 (rms_mm, max_mm, residuals_mm)。"""
    pred = _poly_design(px, order) @ np.stack([coeff_x, coeff_y], axis=1)
    res_mm = pred - mm
    rms_mm = float(np.sqrt(np.mean(res_mm[:, 0] ** 2 + res_mm[:, 1] ** 2)))
    max_mm = float(np.max(np.sqrt(res_mm[:, 0] ** 2 + res_mm[:, 1] ** 2)))
    return rms_mm, max_mm, res_mm


def fit_residual_affine(xy: np.ndarray, res: np.ndarray) -> dict:
    """对校正残差做最小二乘仿射分解（D3 旋转/剪切残差分析）。

    模型：dx = a0 + a1*X + b1*Y；dy = c0 + a2*X + b2*Y
    残差线性部分 M = [[a1,b1],[a2,b2]]，恢复完整变换 A = M + I 后做极分解：
      - 平移      (a0, c0) mm
      - 旋转角    theta = atan2(A21-A12, A11+A22)（°；基于完整变换避免减 I 后对称部分消失）
      - 主轴比例  sx = hypot(A11,A21)、sy = hypot(A12,A22)（旋转系下的 X/Y 完整比例因子）
      - 剪切项    shear = A12 + A21（Y 轴相对 X 轴的倾斜，0=无剪切）
    返回 dict 含上述分量与仿射解释后的剩余 RMS（mm），用于定位系统误差来源。
    """
    # C02 守卫：仿射分解至少需 3 个不重合点，否则静默给出无意义系数
    xy = np.asarray(xy, np.float64).reshape(-1, 2)
    res = np.asarray(res, np.float64).reshape(-1, 2)
    if xy.shape != res.shape or len(xy) < 3:
        raise ValueError("残差仿射分解点数不足或形状不一致：need >=3, got %d" % len(xy))
    X, Y = xy[:, 0], xy[:, 1]
    A = np.stack([np.ones_like(X), X, Y], axis=1)
    cx, *_ = np.linalg.lstsq(A, res[:, 0], rcond=None)
    cy, *_ = np.linalg.lstsq(A, res[:, 1], rcond=None)
    a0, a1, b1 = cx
    c0, a2, b2 = cy
    # 完整线性变换 A_lin = M + I（残差增量 + 恒等）
    m11 = a1 + 1.0
    m12 = b1
    m21 = a2
    m22 = b2 + 1.0
    theta = math.atan2(m21 - m12, m11 + m22)
    sx = math.hypot(m11, m21)
    sy = math.hypot(m12, m22)
    shear = m12 + m21
    pred = A @ np.stack([cx, cy], axis=1)
    resid = res - pred
    rms = float(np.sqrt(np.mean(resid[:, 0] ** 2 + resid[:, 1] ** 2)))
    return {
        "tx_mm": float(a0), "ty_mm": float(c0),
        "scale_x": sx, "scale_y": sy,
        "rot_deg": math.degrees(theta), "shear": shear,
        "residual_rms_mm": rms,
    }


def decompose_residual_affine(px: np.ndarray, mm: np.ndarray,
                              coeff_x, coeff_y, order: int = 2) -> dict:
    """对校正场残差整体做仿射分解（D3 报表入口）。

    等价于先 compute_residuals 再 fit_residual_affine；返回 dict 含
    平移/比例/旋转角/剪切/剩余 RMS。残差为 mm 单位，仿射分解自变量
    必须用 mm 名义坐标（用像素坐标会稀释线性项，导致旋转角被低估）。
    """
    _, _, res_mm = compute_residuals(px, mm, coeff_x, coeff_y, order)
    return fit_residual_affine(mm, res_mm)


def estimate_precision(rows: int, cols: int, square_mm: float, gap_mm: float,
                       order: int = 2, extrapolate: bool = True) -> dict:
    """F10 精度预估：残差经验模型 0.008×(步长/10)^1.6 mm（2阶）。"""
    step = square_mm + gap_mm
    base = 0.008 * (step / 10.0) ** 1.6
    if order >= 3:
        base *= 0.7
    if extrapolate:
        base *= 1.5
    base = round(base, 4)
    level = "绿" if base <= 0.03 else ("黄" if base <= 0.05 else "红")
    return {
        "rows": rows, "cols": cols,
        "points": rows * cols,
        "step_mm": round(step, 3),
        "span_w_mm": round((cols - 1) * step, 1),
        "span_h_mm": round((rows - 1) * step, 1),
        "est_residual_mm": base,
        "level": level,
        "budget_mm": 0.05,
    }


def _match_to_ideal(ideal_mm: np.ndarray, detected: np.ndarray) -> np.ndarray:
    """将检测角点按理想坐标顺序重排（归一化空间最近邻）。
    兼容 findChessboardCornersSB 返回顺序不确定的问题。"""
    def norm(pts):
        mn = pts.min(axis=0)
        mx = pts.max(axis=0)
        rng = np.where(mx - mn > 0, mx - mn, 1.0)
        return (pts - mn) / rng
    id_n = norm(ideal_mm)
    det_n = norm(detected)
    out = np.zeros_like(detected)
    for i, p in enumerate(id_n):
        d = np.sum((det_n - p) ** 2, axis=1)
        out[i] = detected[int(np.argmin(d))]
    return out


def calibrate(gray: np.ndarray, p: ChessboardParams,
              px_scale: float = 1.0, detected: Optional[np.ndarray] = None) -> CalibResult:
    """完整校准流程：检测→理想点→顺序匹配→拟合→残差校验。
    px_scale: 检测图到原始图像的坐标放大比例（降采样加载>1，整图=1）。
    检测角点先还原到原始图像坐标系再拟合，保证与十字检测/坐标输出坐标系一致。
    detected: 已检测好的角点（加载分辨率坐标）。调用方若已自行检测（如需显示识别过程），
    传入本参数可避免重复检测——超大图一次检测耗时可观（v3.3.2）。None 时内部检测。"""
    ideal_mm = build_ideal_points(p)
    if detected is None:
        detected = detect_chessboard(gray, p)
    if detected is None or len(detected) != len(ideal_mm):
        n_ideal = len(ideal_mm)
        n_det = 0 if detected is None else len(detected)
        return CalibResult(False, np.zeros((0, 2)), np.zeros((0, 2)), ideal_mm,
                           message=f"角点检测失败或数量不符：期望{n_ideal}，检出{n_det}。请检查棋盘格参数与扫描图像。")
    detected = _match_to_ideal(ideal_mm, detected)
    if px_scale != 1.0:
        detected = detected * px_scale
    # 校正场拟合：分区模式（默认，推荐 3x3 order=1）或全局多项式
    if p.use_piecewise:
        pw = fit_piecewise(detected, ideal_mm,
                           grid_nx=p.piecewise_nx, grid_ny=p.piecewise_ny,
                           order=p.piecewise_order)
        rms_mm, max_mm, _ = compute_piecewise_residuals(detected, ideal_mm, pw)
        ok = rms_mm <= p.residual_limit_mm
        mode = f"分区校正({p.piecewise_nx}×{p.piecewise_ny},order={p.piecewise_order})"
        msg = (f"[{mode}] 校正残差 RMS={rms_mm:.4f}mm / Max={max_mm:.4f}mm，"
               + (f"通过硬性门槛(≤{p.residual_limit_mm}mm)" if ok else f"未通过硬性门槛(>{p.residual_limit_mm}mm)"))
        # 兼容回退：仍计算全局多项式系数，供旧路径/导出/测量图片线程兜底；
        # GUI 换算优先走 piecewise（apply_piecewise），精度更高。
        c_x, c_y = fit_correction_field(detected, ideal_mm, p.order)
        return CalibResult(ok, detected, np.zeros((0, 2)), ideal_mm,
                           c_x, c_y, rms_mm, max_mm, msg,
                           piecewise=pw, use_piecewise=True)
    coeff_x, coeff_y = fit_correction_field(detected, ideal_mm, p.order)
    rms_mm, max_mm, _ = compute_residuals(detected, ideal_mm, coeff_x, coeff_y, p.order)
    ok = rms_mm <= p.residual_limit_mm
    msg = (f"[全局多项式(order={p.order})] 校正残差 RMS={rms_mm:.4f}mm / Max={max_mm:.4f}mm，"
           + (f"通过硬性门槛(≤{p.residual_limit_mm}mm)" if ok else f"未通过硬性门槛(>{p.residual_limit_mm}mm)"))
    return CalibResult(ok, detected, np.zeros((0, 2)), ideal_mm,
                       coeff_x, coeff_y, rms_mm, max_mm, msg)


def _pw_to_arrays(pw: PiecewiseCorrection):
    """分区校正场 → 定长数值数组（cx/cy 按最大系数数 pad，避免 object/pickle）。"""
    cells = pw.cells
    n = len(cells)
    max_nc = max((c["cx"].shape[0] for c in cells), default=3)
    cx_pad = np.zeros((n, max_nc), np.float64)
    cy_pad = np.zeros((n, max_nc), np.float64)
    ncoef = np.zeros(n, np.int32)
    x0 = np.zeros(n); y0 = np.zeros(n); x1 = np.zeros(n); y1 = np.zeros(n)
    corder = np.zeros(n, np.int32); cnpts = np.zeros(n, np.int32)
    for i, c in enumerate(cells):
        k = c["cx"].shape[0]
        cx_pad[i, :k] = c["cx"]
        cy_pad[i, :k] = c["cy"]
        ncoef[i] = k
        x0[i], y0[i], x1[i], y1[i] = c["x0"], c["y0"], c["x1"], c["y1"]
        corder[i] = c["order"]
        cnpts[i] = c["npts"]
    centers = np.array([c["center"] for c in cells], np.float64)
    return (cx_pad, cy_pad, ncoef, x0, y0, x1, y1, corder, cnpts, centers)


def save_calibration(path: str, res: CalibResult, p: ChessboardParams, mm_per_px: float):
    extra = {}
    if res.piecewise is not None:
        (cx_pad, cy_pad, ncoef, x0, y0, x1, y1,
         corder, cnpts, centers) = _pw_to_arrays(res.piecewise)
        extra = {
            "pw_grid_nx": np.array([p.piecewise_nx]), "pw_grid_ny": np.array([p.piecewise_ny]),
            "pw_order": np.array([p.piecewise_order]),
            "pw_bounds": res.piecewise.bounds,
            "pw_span": np.array([res.piecewise.span]),
            "pw_cx": cx_pad, "pw_cy": cy_pad, "pw_ncoef": ncoef,
            "pw_x0": x0, "pw_y0": y0, "pw_x1": x1, "pw_y1": y1,
            "pw_corder": corder, "pw_cnpts": cnpts, "pw_centers": centers,
        }
    np.savez_compressed(path, coeff_x=res.coeff_x, coeff_y=res.coeff_y,
                        order=np.array([p.order]), rms_mm=np.array([res.rms_mm]),
                        mm_per_px=np.array([mm_per_px]),
                        rows=np.array([p.rows]), cols=np.array([p.cols]),
                        use_piecewise=np.array([bool(res.use_piecewise)]), **extra)


def load_calibration(path: str):
    d = np.load(path, allow_pickle=False)
    pw = None
    use_pw = bool(d["use_piecewise"][0]) if "use_piecewise" in d.files else False
    if use_pw and "pw_cx" in d.files:
        n = len(d["pw_ncoef"])
        cells = []
        for i in range(n):
            k = int(d["pw_ncoef"][i])
            cells.append({
                "x0": float(d["pw_x0"][i]), "y0": float(d["pw_y0"][i]),
                "x1": float(d["pw_x1"][i]), "y1": float(d["pw_y1"][i]),
                "cx": d["pw_cx"][i, :k].astype(np.float64),
                "cy": d["pw_cy"][i, :k].astype(np.float64),
                "order": int(d["pw_corder"][i]),
                "center": d["pw_centers"][i].astype(np.float64),
                "npts": int(d["pw_cnpts"][i]),
            })
        pw = PiecewiseCorrection(grid_nx=int(d["pw_grid_nx"][0]),
                                 grid_ny=int(d["pw_grid_ny"][0]),
                                 order=int(d["pw_order"][0]))
        pw.bounds = np.asarray(d["pw_bounds"], np.float64)
        pw.span = float(d["pw_span"][0])
        pw.cells = cells
        pw.centers = np.asarray(d["pw_centers"], np.float64)
    return {
        "coeff_x": d["coeff_x"], "coeff_y": d["coeff_y"],
        "order": int(d["order"][0]), "rms_mm": float(d["rms_mm"][0]),
        "mm_per_px": float(d["mm_per_px"][0]),
        "rows": int(d["rows"][0]), "cols": int(d["cols"][0]),
        "piecewise": pw, "use_piecewise": use_pw,
    }


@dataclass
class AutoChessDetectResult:
    """棋盘格/十字阵列自动识别结果。"""
    ok: bool
    rows: int = 0                       # 行数（棋盘格=内角点行数；十字阵列=中心点行数）
    cols: int = 0                       # 列数
    square_px: float = 0.0              # 实测单格像素边长 / 中心点间距(px)
    gap_px: float = 0.0                 # 实测格间间隔(px)，紧邻棋盘=0
    corners: Optional[np.ndarray] = None  # Nx2 角点/中心点像素坐标（行优先）
    mm_per_px: Optional[float] = None   # 实测像素物理尺度(mm/px)，需 square_mm
    dpi_est: Optional[float] = None     # 估算等效 DPI，需 square_mm
    dilation_rec: float = 1.0           # 推荐膨胀系数（实测单格/标称单格）
    pattern_detected: str = "chessboard"  # chessboard / cross
    extent_px: Optional[Tuple[float, float, float, float]] = None  # 识别范围 (x0,y0,x1,y1)
    message: str = ""


def _rotate_pts_to_principal(pts: np.ndarray):
    """PCA 主轴校正：返回 (主轴坐标系投影 Nx2, 旋转角 rad, 中心点)。"""
    center = pts.mean(axis=0)
    p = pts - center
    if len(pts) < 3:
        return p, 0.0, center
    cov = np.cov(p, rowvar=False)
    evals, evecs = np.linalg.eigh(cov)
    main = evecs[:, int(np.argmax(evals))]
    ang = float(np.arctan2(main[1], main[0]))
    c, s = np.cos(-ang), np.sin(-ang)
    proj = p @ np.array([[c, -s], [s, c]], np.float64).T
    return proj, ang, center


def _project_back(proj: np.ndarray, ang: float, center: np.ndarray) -> np.ndarray:
    """主轴坐标 → 原图坐标。"""
    c, s = np.cos(ang), np.sin(ang)
    R = np.array([[c, -s], [s, c]], np.float64)
    return proj @ R.T + center


def _grid_uniform(v: np.ndarray) -> bool:
    """簇间距均匀性校验（标准差/均值<=25% 视为规整网格）。"""
    if len(v) < 3:
        return True
    dv = np.diff(np.asarray(v, np.float64))
    return float(np.std(dv) / max(1e-6, np.mean(dv))) <= 0.25


def _dedup_points(pts: np.ndarray, r: float) -> np.ndarray:
    """2D 密度去重：把距离 <= r 的响应点合并为一个质心。
    用于 goodFeaturesToTrack 在同一内角/十字中心附近返回多个点的情况。"""
    if r <= 0 or len(pts) <= 4:
        return pts
    pts = np.asarray(pts, np.float64)
    used = np.zeros(len(pts), bool)
    kept = []
    for i in range(len(pts)):
        if used[i]:
            continue
        d = np.linalg.norm(pts - pts[i], axis=1)
        grp = pts[d <= r]
        kept.append(grp.mean(axis=0))
        used |= d <= r
    return np.array(kept, np.float64)


def _cluster_grid_from_points(pts: np.ndarray, direction: str,
                              min_rows: int, max_rows: int,
                              min_cols: int, max_cols: int,
                              dedup_r: float = 0.0,
                              use_pca: bool = True):
    """对点集（内角点/十字中心）做密度去重+主轴校正+1D聚类推导行列。
    use_pca=False 时跳过内部 PCA 主轴校正（用于已用 Hough 角数学校正过的坐标，
    避免二次旋转引入误差）。返回 (rows, cols, step_px, grid Nx2 行优先) 或 None。"""
    if pts is None or len(pts) < 4:
        return None
    if dedup_r > 0:
        pts = _dedup_points(pts, dedup_r)
        if pts is None or len(pts) < 4:
            return None

    def _try_grid(proj, ang, center):
        # 用两主轴方向排序后的相邻差分中位数估计网格步长（比"相对首点距离"稳健，
        # 不会因首点位于边角而把整板聚成一簇）
        px = np.sort(proj[:, 0])
        py = np.sort(proj[:, 1])
        dx = np.diff(px)
        dy = np.diff(py)
        # 过滤簇内微差分（同一行/列内角点坐标几乎相同产生的小差分），
        # 只保留真正的网格间距量级（>= 跨度1%）后再取中位数
        min_d = max(1e-6, min(px[-1] - px[0], py[-1] - py[0]) * 0.01)
        dx = dx[dx > min_d]
        dy = dy[dy > min_d]
        cand = np.concatenate([dx, dy])
        if len(cand) == 0:
            return None
        step0 = float(np.median(cand))
        # 轻微旋转下，同一行/列角点投影会引入 ~step*sin(ang) 的簇内差分，
        # 可能把中位数拉低。用“显著大于中位数”的差分集再估一次真实网格步长。
        big = cand[cand > step0 * 1.5]
        if len(big) >= 3:
            step1 = float(np.median(big))
            if step1 > step0:
                step0 = step1
        if step0 <= 1.0:
            return None
        thr = max(2.0, step0 * 0.5)
        xs = np.array(_cluster_1d(proj[:, 0], thr), np.float64)
        ys = np.array(_cluster_1d(proj[:, 1], thr), np.float64)
        cols, rows = len(xs), len(ys)
        # 一致性校验：聚类出的网格行列数应与实际点数相符（允许缺角/重复，3 倍为上限）。
        # 近方形点阵（如 5×5）PCA 主轴不稳定时可能旋转出伪周期，导致行列数虚高
        # （实测约旦图 25 点被旋转后聚出 21×21），此时应回退原始坐标聚类兜底。
        n_pts = len(proj)
        if rows * cols > n_pts * 3 or rows * cols * 3 < n_pts:
            return None
        if cols < min_cols or cols > max_cols or rows < min_rows or rows > max_rows:
            return None
        if not (_grid_uniform(xs) and _grid_uniform(ys)):
            return None
        if direction == "transpose":
            rows, cols = cols, rows
            xs, ys = ys, xs
        gx, gy = np.meshgrid(xs, ys)
        grid = np.stack([gx.ravel(), gy.ravel()], axis=1)
        grid = _project_back(grid, ang, center)
        step_px = float(np.mean(np.diff(xs))) if len(xs) > 1 else step0
        return rows, cols, step_px, grid

    if use_pca:
        # 候选1：主轴校正（应对轻微旋转棋盘）
        proj, ang, center = _rotate_pts_to_principal(pts)
        res = _try_grid(proj, ang, center)
        if res is not None:
            return res
    # 候选2：原始坐标直接聚类（近方形点阵 PCA 主轴不稳定时兜底）
    res = _try_grid(pts.astype(np.float64), 0.0, np.zeros(2))
    return res


def _downscale(gray: np.ndarray, max_side: int = 2200):
    """超大图降采样（OpenCV 在 2 万像素级大图上会抛异常/极慢），
    返回 (小图, scale) 其中 scale = 小图尺寸/原图尺寸。"""
    h, w = gray.shape
    if max(h, w) <= max_side:
        return gray, 1.0
    scale = max_side / float(max(h, w))
    nw = max(16, int(round(w * scale)))
    nh = max(16, int(round(h * scale)))
    small = cv2.resize(gray, (nw, nh), interpolation=cv2.INTER_AREA)
    return small, float(nw) / w


def _shrink_vertex_grid(rows: int, cols: int, grid: np.ndarray,
                        proj: np.ndarray, thr: float):
    """角点法 Shi-Tomasi 检测到的是方块顶点网格（比内角点多一圈）。
    当首/尾行（列）角点数显著少于内圈中位（外圈顶点检测不全/贴边界）时，
    缩一圈输出内角点网格；否则维持原结果。返回 (rows, cols, grid)。"""
    if rows < 3 or cols < 3 or grid is None:
        return rows, cols, grid
    xs = np.array(_cluster_1d(proj[:, 0], thr))
    ys = np.array(_cluster_1d(proj[:, 1], thr))
    if len(xs) != cols or len(ys) != rows:
        return rows, cols, grid
    cnt_rows = np.array([int(np.sum(np.abs(proj[:, 1] - yv) < thr)) for yv in ys], np.float64)
    cnt_cols = np.array([int(np.sum(np.abs(proj[:, 0] - xv) < thr)) for xv in xs], np.float64)
    med_r = float(np.median(cnt_rows[1:-1])) if rows > 2 else 0.0
    med_c = float(np.median(cnt_cols[1:-1])) if cols > 2 else 0.0
    sparse_r = med_r > 0 and (cnt_rows[0] < 0.85 * med_r or cnt_rows[-1] < 0.85 * med_r)
    sparse_c = med_c > 0 and (cnt_cols[0] < 0.85 * med_c or cnt_cols[-1] < 0.85 * med_c)
    if not (sparse_r or sparse_c):
        return rows, cols, grid
    g = grid.reshape(rows, cols, 2)
    inner = g[1:-1, 1:-1].reshape(-1, 2)
    return rows - 2, cols - 2, inner


def _auto_detect_corners(gray: np.ndarray, direction: str,
                         min_rows: int, max_rows: int,
                         min_cols: int, max_cols: int):
    """角点法主流程：Shi-Tomasi 检测内角点 → 主轴校正 → 聚类行列 → 规整网格。
    相比旧的方块边缘聚类法，轻微旋转/噪声/margin 下不会把边缘误聚成额外网格线，
    行列数与识别范围（仅覆盖实际内角点）更准确。大图自动降采样并乘回原图坐标。"""
    gray, sc = _downscale(gray)
    h, w = gray.shape
    m = max(2, int(min(h, w) * 0.01))
    mask = np.zeros((h, w), np.uint8)
    mask[m:h - m, m:w - m] = 255
    pts = cv2.goodFeaturesToTrack(gray, maxCorners=3000, qualityLevel=0.01,
                                  minDistance=max(3, int(min(h, w) * 0.005)),
                                  mask=mask)
    if pts is None or len(pts) < 4:
        return None
    pts = pts.reshape(-1, 2).astype(np.float64)
    # 候选0：Hough 直线方向估计旋转角，数学旋转角点坐标后聚类
    # （避免图像级 warpAffine 回正引入插值伪影；对明显旋转棋盘最准）
    ang0 = _estimate_rotation_deg(gray)
    if ang0 is not None and abs(ang0) >= 1.0:
        th0 = np.deg2rad(-ang0)
        c0, s0 = np.cos(th0), np.sin(th0)
        R0 = np.array([[c0, -s0], [s0, c0]], np.float64)
        center0 = np.array([w / 2.0, h / 2.0])
        proj0 = (pts - center0) @ R0.T + center0
        r0 = _cluster_grid_from_points(proj0, direction, min_rows, max_rows, min_cols, max_cols,
                                       dedup_r=max(3, int(min(h, w) * 0.005)) * 1.6,
                                       use_pca=False)
        if r0 is not None:
            rows, cols, step_px, grid = r0
            grid_back = (grid - center0) @ R0 + center0
            rows, cols, grid_back = _shrink_vertex_grid(
                rows, cols, grid_back, proj0, thr=max(2.0, step_px * 0.5))
            return rows, cols, step_px / sc, grid_back / sc
    r = _cluster_grid_from_points(pts, direction, min_rows, max_rows, min_cols, max_cols,
                                  dedup_r=max(3, int(min(h, w) * 0.005)) * 1.6)
    if r is None:
        return None
    rows, cols, step_px, grid = r
    return rows, cols, step_px / sc, grid / sc


def _auto_detect_edges(gray: np.ndarray, direction: str,
                       min_rows: int, max_rows: int,
                       min_cols: int, max_cols: int,
                       margin_frac: float = 0.0):
    """边缘法兜底（旧逻辑修正版）：方块边缘聚类 + 伪线过滤 + 奇线补全。
    修复点：① 过滤面积/尺寸离群方块；② 补线仅在确有间隔证据时触发；
    ③ 均匀性校验不通过即放弃，避免“网格边界聚类异常（行94/列5）”。
    margin_frac>0 时剔除贴近图像边界的伪块（供旋转回正的白边插值碎片场景）。"""
    gray, sc = _downscale(gray)
    h, w = gray.shape
    _, bw = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)
    dark_ratio = float(np.count_nonzero(bw)) / bw.size
    if dark_ratio > 0.55:
        bw = cv2.bitwise_not(bw)
    bw = cv2.morphologyEx(bw, cv2.MORPH_OPEN, np.ones((3, 3), np.uint8))
    n, _, stats, _ = cv2.connectedComponentsWithStats(bw, connectivity=4)
    squares = []
    for i in range(1, n):
        x, y, ww, hh, area = stats[i]
        if ww < 4 or hh < 4 or area < 20:
            continue
        squares.append((x, y, ww, hh, area))
    if len(squares) < 4:
        return None
    ws = np.array([s[2] for s in squares], np.float64)
    hs = np.array([s[3] for s in squares], np.float64)
    sq_px_est = float(np.median(np.concatenate([ws, hs])))
    med_area = float(np.median([s[4] for s in squares]))
    squares = [s for s in squares
               if 0.25 * med_area <= s[4] <= 4.0 * med_area
               and 0.4 * sq_px_est <= s[2] <= 2.5 * sq_px_est
               and 0.4 * sq_px_est <= s[3] <= 2.5 * sq_px_est]
    if len(squares) < 4:
        return None
    # margin_frac>0 时剔除贴近图像边界的伪块（旋转回正的白边插值碎片），避免把 margin 当成网格线
    if margin_frac > 0:
        mrg = max(4, int(min(h, w) * margin_frac))
        squares = [s for s in squares
                   if s[0] >= mrg and s[1] >= mrg
                   and s[0] + s[2] <= w - mrg and s[1] + s[3] <= h - mrg]
        if len(squares) < 4:
            return None
    thr = max(6.0, sq_px_est * 0.75)
    xs_all = np.concatenate([np.array([s[0], s[0] + s[2]], np.float64) for s in squares])
    ys_all = np.concatenate([np.array([s[1], s[1] + s[3]], np.float64) for s in squares])
    xs = np.array(_cluster_1d(xs_all, thr), np.float64)
    ys = np.array(_cluster_1d(ys_all, thr), np.float64)

    def _drop_pseudo(lines):
        """间隔棋盘：小间距后跟大间距 → 丢伪线（间隔中间线）。"""
        lines = sorted(lines)
        changed = True
        while changed:
            changed = False
            out = []
            i, n = 0, len(lines)
            while i < n:
                if i + 1 < n and lines[i + 1] - lines[i] < sq_px_est * 1.2:
                    nxt = lines[i + 2] - lines[i + 1] if i + 2 < n else None
                    if nxt is not None and nxt > sq_px_est * 1.5:
                        out.append(lines[i])
                        i += 2
                        changed = True
                        continue
                out.append(lines[i])
                i += 1
            lines = out
        if len(lines) >= 3 and lines[-1] - lines[-2] < sq_px_est * 1.2                 and lines[-2] - lines[-3] > sq_px_est * 1.5:
            lines = lines[:-1]
        return lines

    def _is_gapped(lines):
        if len(lines) < 3:
            return False
        dv = np.diff(sorted(lines))
        return bool(float(np.max(dv)) > 1.5 * sq_px_est)

    def _append_tail(lines, raw_max):
        """奇线补全：仅当确有间隔证据且末线后仍有黑格右缘残留时，补一条尾线。"""
        lines = list(lines)
        if len(lines) >= 3 and raw_max - lines[-1] > 0.3 * sq_px_est                 and float(np.mean(np.diff(lines))) > sq_px_est * 1.2:
            step = float(np.mean(np.diff(lines)))
            lines.append(lines[-1] + step)
        return np.asarray(lines, np.float64)

    xs = _append_tail(_drop_pseudo(xs), float(np.max(xs_all)))
    ys = _append_tail(_drop_pseudo(ys), float(np.max(ys_all)))
    nr, nc = len(ys), len(xs)
    if nr < 4 or nc < 4 or nr > 82 or nc > 82:
        return None
    rows, cols = nr - 2, nc - 2
    if rows < min_rows or rows > max_rows or cols < min_cols or cols > max_cols:
        return None
    dx, dy = np.diff(xs), np.diff(ys)
    if (len(dx) >= 2 and not _grid_uniform(xs)) or (len(dy) >= 2 and not _grid_uniform(ys)):
        return None
    if direction == "transpose":
        rows, cols = cols, rows
        xs, ys = ys, xs
    pts = []
    for i in range(1, len(ys) - 1):
        for j in range(1, len(xs) - 1):
            pts.append([xs[j], ys[i]])
    corners = np.array(pts, np.float64)
    try:
        criteria = (cv2.TERM_CRITERIA_EPS + cv2.TERM_CRITERIA_MAX_ITER, 60, 0.001)
        corners = cv2.cornerSubPix(gray, corners.astype(np.float32), (5, 5), (-1, -1), criteria).astype(np.float64)
    except cv2.error:
        pass
    step_px = float(np.median(np.concatenate([ws, hs])))  # 单格宽=方块宽中位数（紧邻/间隔棋盘均适用）
    # 间隔 = 块中心间距（周期） - 单格宽；紧邻棋盘周期=单格宽 → gap=0
    ccx = np.sort(np.array([s[0] + s[2] / 2.0 for s in squares], np.float64))
    ccy = np.sort(np.array([s[1] + s[3] / 2.0 for s in squares], np.float64))
    dc = np.concatenate([np.diff(ccx), np.diff(ccy)])
    dc = dc[dc > step_px * 0.5]
    gap_px = max(0.0, float(np.median(dc)) - step_px) if len(dc) else 0.0
    return rows, cols, step_px / sc, gap_px / sc, corners / sc


def _find_dark_rois(gray: np.ndarray, block: int = 128, dark_thr: float = 70.0,
                    min_frac: float = 0.02, pad_frac: float = 0.08,
                    max_rois: int = 4):
    """用分块均值定位"成片暗背景"区域，返回 [(x0, y0, x1, y1)]（按面积降序）。

    混合背景图（黑纸标定板 + 白纸余白）上，整图全局 OTSU 的"暗像素占比>0.5"
    极性判据会被白纸区稀释（实测 37.3% < 50%），导致黑底亮十字被误判为白底暗十字、
    亮十字被当背景丢弃。先在暗区 ROI 内独立做极性判断可天然规避该问题。

    无成片暗区（整幅白底图）时返回空列表，调用方回退原整图路径，不改变旧行为。
    """
    h, w = gray.shape[:2]
    if h < block * 2 or w < block * 2:
        return []
    nb_y, nb_x = h // block, w // block
    blocks = gray[:nb_y * block, :nb_x * block].reshape(
        nb_y, block, nb_x, block).mean(axis=(1, 3))
    dark = (blocks < dark_thr).astype(np.uint8)
    if dark.mean() < min_frac:
        return []
    mask = cv2.resize(dark * 255, (w, h), interpolation=cv2.INTER_NEAREST)
    k = max(3, block * 3)
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, np.ones((k, k), np.uint8))
    n, _, stats, _ = cv2.connectedComponentsWithStats(mask, connectivity=8)
    min_area = min_frac * float(h * w)
    rois = []
    for i in range(1, n):
        x, y, ww, hh, area = stats[i]
        if area < min_area:
            continue
        pad = int(pad_frac * max(ww, hh))
        rois.append((max(0, x - pad), max(0, y - pad),
                     min(w, x + ww + pad), min(h, y + hh + pad), int(area)))
    rois.sort(key=lambda r: -r[4])
    return [(a, b, c, d) for (a, b, c, d, _a) in rois[:max_rois]]


def _filter_cross_comps(bw: np.ndarray, min_side: int = 3, min_area: int = 30,
                        max_frac: float = 0.25):
    """从二值图提取"疑似十字/圆点"连通域，并按面积中位数 0.2~5 倍过滤，返回 [(x,y,w,h,area)]。

    注意：本函数刻意不做形态学 OPEN——实测在 2200 长边降采样下十字线宽仅 2px 量级，
    3×3 OPEN 会把 25 个十字整条抹掉（只剩 ROI 外框一个连通域），导致筛查失败。
    降采样尺度越小线宽越细，OPEN 的破坏性越强，故统一以面积过滤代替。
    """
    h, w = bw.shape[:2]
    n, _, stats, _ = cv2.connectedComponentsWithStats(bw, connectivity=8)
    comps, areas = [], []
    for i in range(1, n):
        x, y, ww, hh, area = stats[i]
        if ww < min_side or hh < min_side or area < min_area:
            continue
        if area > max_frac * h * w:
            continue
        comps.append((x, y, ww, hh, area))
        areas.append(area)
    if len(comps) < 4:
        return []
    med_area = float(np.median(areas))
    return [c for c in comps if 0.2 * med_area <= c[4] <= 5.0 * med_area]


def _cross_centers_in(region: np.ndarray, force_invert: Optional[bool] = None,
                      min_side: int = 3, min_area: int = 30):
    """在单块区域内部提取十字中心候选点（返回 Nx2 局部坐标，或 None）。

    region 内部做 OTSU + 极性自适应：暗像素占比 > 0.5 判为"黑底亮十字"，反色后
    使十字线条统一成为前景。force_invert 可强制指定极性（兜底双极性尝试用）。
    区域越小、背景越单一，极性判据越可靠——这是摆脱整图混合背景干扰的关键。
    """
    h, w = region.shape[:2]
    if h < min_side * 4 or w < min_side * 4:
        return None
    _, bw = cv2.threshold(region, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)
    dark_ratio = float(np.count_nonzero(bw)) / bw.size
    invert = (dark_ratio > 0.5) if force_invert is None else bool(force_invert)
    if invert:
        bw = cv2.bitwise_not(bw)  # 黑底亮十字：反色后亮线成为前景
    bw = cv2.morphologyEx(bw, cv2.MORPH_OPEN, np.ones((3, 3), np.uint8))
    comps = _filter_cross_comps(bw, min_side=min_side, min_area=min_area)
    if not comps:
        return None
    centers = []
    for (x, y, ww, hh, area) in comps:
        roi = region[y:y + hh, x:x + ww].astype(np.float32)
        # 质心权重极性必须与连通域前景同步：黑线前景（未反色）用 255-roi 加重暗线；
        # 白线前景（已反色 invert=True）直接用 roi 加重亮线，否则反色图中心会退化为
        # bbox 中心（255-roi 对白线权重≈0，触发 rs/cs.sum()<=0 兜底分支）
        wgt = roi if invert else np.clip(255.0 - roi, 0, 255)
        rs = wgt.sum(axis=1)
        cs = wgt.sum(axis=0)
        if rs.sum() <= 0 or cs.sum() <= 0:
            cx, cy = x + ww / 2.0, y + hh / 2.0
        else:
            cy = y + float(np.average(np.arange(hh), weights=rs))
            cx = x + float(np.average(np.arange(ww), weights=cs))
        centers.append([cx, cy])
    return np.array(centers, np.float64)


def _looks_like_cross_array(gray: np.ndarray) -> bool:
    """快速筛查图像是否像十字/圆点阵列：多数连通域非实心方块（面积/外接矩形比明显<1）。

    混合背景图（黑纸标定板 + 白纸余白）先定位成片暗区，在暗区内判断；整图极性判据
    会被白纸区稀释（实测暗像素占比 37.3% 被判成白底暗十字），导致十字阵列被漏判。
    无成片暗区时回退整图，行为与原实现一致。
    """
    g2, _ = _downscale(gray)
    regions = _find_dark_rois(g2)
    if not regions:
        regions = [(0, 0, g2.shape[1], g2.shape[0])]
    for (x0, y0, x1, y1) in regions:
        sub = g2[y0:y1, x0:x1]
        if sub.size == 0:
            continue
        _, bw = cv2.threshold(sub, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)
        if float(np.count_nonzero(bw)) / bw.size > 0.5:
            bw = cv2.bitwise_not(bw)
        # 不做 OPEN：2200 尺度下十字线宽仅 2px，3×3 OPEN 会把十字整条抹掉（实测只剩外框）
        comps = _filter_cross_comps(bw)
        if len(comps) < 4:
            continue
        ratios = [float(c[4]) / float(c[2] * c[3]) for c in comps]
        if float(np.median(ratios)) < 0.85:
            return True
    return False


def _estimate_rotation_deg(gray: np.ndarray) -> Optional[float]:
    """用 Hough 直线方向投票估计旋转角，归一到 [-45°, 45°)。

    棋盘格线在旋转后保持相互垂直，Canny+HoughLines 检测出的长直线方向
    主峰即旋转角（棋盘格线远多于图像边框，白边边框直线干扰有限）。
    """
    g2, _ = _downscale(gray)
    edges = cv2.Canny(g2, 50, 150)
    hh, ww = edges.shape
    thr = max(30, int(min(hh, ww) * 0.2))
    lines = cv2.HoughLines(edges, 1, np.pi / 180.0, thr)
    if lines is None or len(lines) < 3:
        return None
    thetas = np.degrees(lines[:, 0, 1])
    rhos = np.abs(lines[:, 0, 0])
    angs = np.mod(thetas, 180.0)
    a = np.where(angs > 90.0, angs - 180.0, angs)
    a = np.where(a > 45.0, a - 90.0, a)
    a = np.where(a < -45.0, a + 90.0, a)
    hist, bin_edges = np.histogram(a, bins=90, range=(-45.0, 45.0), weights=rhos)
    ang = float(bin_edges[np.argmax(hist)] + 0.5)
    return ang


def _rotate_back(gray: np.ndarray, ang_deg: float):
    """绕图中心旋转回正（-ang_deg），白边填充。

    超大图先降采样再旋转（否则 4800dpi 全幅 warpAffine INTER_CUBIC 单次数十秒，
    是自动识别兜底链“卡在找角点”的耗时放大器）。返回 (小图, scale)，
    scale = 小图边长/原图边长；调用方需把检测到的角点坐标除以 scale 还原到原图。
    """
    g, sc = _downscale(gray)
    h, w = g.shape
    m = cv2.getRotationMatrix2D((w / 2.0, h / 2.0), -ang_deg, 1.0)
    return cv2.warpAffine(g, m, (w, h), flags=cv2.INTER_CUBIC, borderValue=255), sc


def _unrotate_points(pts: np.ndarray, ang_deg: float, shape) -> np.ndarray:
    """把回正坐标系下的点按 +ang_deg 转回原图坐标系。"""
    h, w = shape[:2]
    th = np.deg2rad(ang_deg)
    c, s = np.cos(th), np.sin(th)
    R = np.array([[c, -s], [s, c]], np.float64)
    center = np.array([w / 2.0, h / 2.0], np.float64)
    return (pts - center) @ R.T + center


def _auto_detect_cross(gray: np.ndarray, direction: str,
                       min_rows: int, max_rows: int,
                       min_cols: int, max_cols: int):
    """十字中心点自动识别：暗背景 ROI 优先 → 整图双极性兜底 → 聚类行列。

    返回 (rows, cols, step_px, grid)，坐标已还原到入参 gray 的坐标系。

    混合背景图（黑纸标定板 + 白纸余白）下，整图 OTSU 的极性判据会被白纸稀释
    （实测暗像素占比 37.3% → 误判为白底暗十字），黑底亮十字会被当背景丢弃。
    因此先在成片暗区 ROI 内独立二值化与极性判断；ROI 路径全部失败时，
    再回退整图并按两种极性各试一次。单块 ROI 内背景单一，极性判据天然可靠。
    """
    # 十字阵列在 2200 长边降采样下碎片面积过小（<30px）会被连通域过滤光，导致整链失败。
    # 十字法需要保留足够像素：长边 4400 实测可稳定取到全部 25 个十字连通域。
    g, sc = _downscale(gray, max_side=4400)
    gh, gw = g.shape[:2]
    dedup_r = max(3, int(min(gh, gw) * 0.005)) * 1.6

    def _try(points, off_x: int, off_y: int):
        """点集 → 规整网格。返回 (rows, cols, step_px, grid)，坐标为 g 的坐标系。"""
        if points is None or len(points) < 4:
            return None
        n_pts = len(points)
        best = None
        for use_pca in (True, False):
            r = _cluster_grid_from_points(points, direction, min_rows, max_rows,
                                          min_cols, max_cols, dedup_r=dedup_r,
                                          use_pca=use_pca)
            if r is None:
                continue
            rows, cols, step_px, grid = r
            # 评分：行列乘积与实际点数越吻合越好（防止近方形点阵被 PCA 旋转成 7×7
            # 之类"点数更多但不符"的伪网格），其次取点数多者
            deficit = abs(rows * cols - n_pts)
            score = (deficit, -len(grid))
            if best is None or score < best[0]:
                grid = grid + np.array([off_x, off_y], np.float64)
                best = (score, rows, cols, step_px, grid)
        return None if best is None else best[1:]

    cand = []
    # ---- 路径1（主）：成片暗背景 ROI 内独立处理 ----
    for (x0, y0, x1, y1) in _find_dark_rois(g):
        res = _try(_cross_centers_in(g[y0:y1, x0:x1]), x0, y0)
        if res is not None:
            cand.append(res)
    # ---- 路径2（兜底）：整图双极性（暗区不成片、或标定板被切成多块时）----
    if not cand:
        for fi in (None, True, False):
            res = _try(_cross_centers_in(g, force_invert=fi), 0, 0)
            if res is not None:
                cand.append(res)
    if not cand:
        return None

    # 多 ROI 命中时取"行列乘积与点数最吻合、点数最多"的一组
    def _rank(res):
        rows, cols, _step, grid = res
        return (abs(rows * cols - len(grid)), -len(grid))

    rows, cols, step_px, grid = min(cand, key=_rank)
    # step_px 只做降采样还原（ROI 平移不影响间距）；点坐标需先加偏移再还原
    return rows, cols, step_px / sc, grid / sc


def _build_chess_result(ok: bool, rows: int, cols: int, step_px: float, gap_px: float,
                        pts: np.ndarray, pattern_detected: str,
                        square_mm: Optional[float], dpi: Optional[float],
                        message: str, pixel_scale: float = 1.0) -> AutoChessDetectResult:
    """组装自动识别结果。

    pixel_scale: 传入 gray 相对原始图像的分辨率比例（>1 表示 gray 是降采样图）。
    内部检测/聚类在降采样图或加载分辨率上进行，step_px 仅为该分辨率下的值；
    计算 mm_per_px / dpi_est / dilation_rec 等物理量时必须还原到原始分辨率
    （step_px * pixel_scale），否则推荐膨胀系数会按降采样倍数整体偏小，
    用户一键应用后理想点步长被错误压缩，导致校准场整体缩小（坐标系统性偏小）。
    """
    scale = float(pixel_scale or 1.0)
    step_orig = float(step_px) * scale
    res = AutoChessDetectResult(ok, rows=rows, cols=cols, square_px=round(step_orig, 2),
                                gap_px=round(float(gap_px) * scale, 2), corners=pts,
                                pattern_detected=pattern_detected,
                                extent_px=(float(pts[:, 0].min()), float(pts[:, 1].min()),
                                           float(pts[:, 0].max()), float(pts[:, 1].max())),
                                message=message)
    if square_mm and square_mm > 0 and step_orig > 0:
        mm_per_px = square_mm / step_orig
        res.mm_per_px = mm_per_px
        res.dpi_est = 25.4 / mm_per_px
        if dpi and dpi > 0:
            nominal_sq_px = square_mm * dpi / 25.4
            res.dilation_rec = round(step_orig / nominal_sq_px, 4)
        res.message += f"，等效DPI≈{res.dpi_est:.0f}"
        if abs(res.dilation_rec - 1.0) > 1e-4:
            res.message += f"，推荐膨胀系数≈{res.dilation_rec:.4f}"
    return res


def _diagnose_failure(gray: np.ndarray, max_rows: int, max_cols: int) -> str:
    """自动识别失败时给出更具体的诊断与操作指引。"""
    gray, _ = _downscale(gray)
    h, w = gray.shape
    pts = cv2.goodFeaturesToTrack(gray, maxCorners=3000, qualityLevel=0.01,
                                  minDistance=max(3, int(min(h, w) * 0.005)))
    n_pts = 0 if pts is None else len(pts)
    if n_pts >= 4:
        proj, ang, center = _rotate_pts_to_principal(pts.reshape(-1, 2).astype(np.float64))
        # 步长估计必须与 _try_grid 一致：用相邻差分中位数，而非"相对首点距离"。
        # 旧逻辑在首点位于边角时中位数≈板跨度一半，阈值过大把整板聚成 1 簇，
        # 误报"候选行列 1×1"（真实约旦图 25 点曾被聚成 1×1）。
        px = np.sort(proj[:, 0])
        py = np.sort(proj[:, 1])
        dx = np.diff(px)
        dy = np.diff(py)
        min_d = max(1e-6, min(px[-1] - px[0], py[-1] - py[0]) * 0.01)
        dx = dx[dx > min_d]
        dy = dy[dy > min_d]
        cand = np.concatenate([dx, dy])
        step0 = float(np.median(cand)) if len(cand) else 0.0
        thr = max(2.0, step0 * 0.5)
        nxc = len(_cluster_1d(proj[:, 0], thr))
        nyc = len(_cluster_1d(proj[:, 1], thr))
        if nxc > max_cols or nyc > max_rows:
            return (f"检测到 {nyc}×{nxc} 个候选行列（超出允许 {max_rows}×{max_cols}），"
                    f"疑似图像倾斜、边缘干扰或图案非规整。建议："
                    f"① 在自动识别区选择【棋盘格走向】并重试；"
                    f"② 换用【十字中心点】识别模式；"
                    f"③ 手动指定行列数后重试。")
        return (f"识别失败：检测到 {n_pts} 个候选角点但无法构成规整网格（候选行列 {nyc}×{nxc}）。"
                f"请确认图像为完整棋盘格或十字阵列，或手动指定行列数后重试。")
    return (f"识别失败：图像中未找到足够角点/方块（仅 {n_pts} 个）。"
            f"请确认已导入正确的标定板图像（棋盘格或十字阵列），并适当提高扫描质量。")


def auto_detect_chessboard(gray: np.ndarray, square_mm: Optional[float] = None,
                           dpi: Optional[float] = None, progress_cb=None,
                           direction: str = "auto", pattern: str = "auto",
                           min_rows: int = 2, max_rows: int = 60,
                           min_cols: int = 2, max_cols: int = 80,
                           pixel_scale: float = 1.0) -> AutoChessDetectResult:
    """自动识别标定板行列数与关键参数（无需预知行/列）。

    pixel_scale: 传入 gray 相对原始图像的分辨率比例（>1 表示 gray 为降采样/加载缩放图），
    透传给 _build_chess_result 用于修正步长、等效 DPI 与推荐膨胀系数。

    棋盘格主流程（角点法）：Shi-Tomasi 检测内角点 → PCA 主轴校正轻微旋转 →
    主轴坐标系 1D 聚类行/列 → 均匀性校验 → 由聚类中心生成规整网格角点。
    相比旧“方块边缘聚类”法，旋转/噪声/margin 下不会把边缘误聚成额外网格线，
    行列数与识别范围（仅覆盖实际内角点）更准确；边缘法保留为兜底。

    十字中心点模式：二值化 → 十字连通域筛选 → 投影质心 → 主轴校正 → 聚类行列，
    输出规整中心点网格，可直接用于十字阵列标定。

    pattern: "chessboard" 仅棋盘格 | "cross" 仅十字中心 | "auto" 先棋盘格后十字
    direction: "auto"/"normal" 保持检测方向 | "transpose" 行列交换（确认走向）
    """
    if progress_cb:
        progress_cb(0.05)

    # 十字法涉及 4400 长边连通域+多 ROI 遍历，代价高；auto 模式下"边缘法命中但实为十字"
    # 与末尾"十字中心点模式"会各调一次，故做单次缓存，避免同图重复计算。
    _cross_cache = {}

    def _cross_once():
        if "r" not in _cross_cache:
            _cross_cache["r"] = _auto_detect_cross(gray, direction, min_rows, max_rows,
                                                   min_cols, max_cols)
        return _cross_cache["r"]

    def _bounded(r, c, tag):
        ok = (min_rows <= r <= max_rows) and (min_cols <= c <= max_cols)
        return ok, f"{tag}行列越界：行{r}/列{c}（允许 {min_rows}~{max_rows} × {min_cols}~{max_cols}）"

    # ---------- 棋盘格：边缘法优先（轴对齐最稳），十字筛查/角点法/旋转回正依次兜底 ----------
    if pattern in ("chessboard", "auto"):
        if progress_cb:
            progress_cb(0.3)
        ee = _auto_detect_edges(gray, direction, min_rows, max_rows, min_cols, max_cols)
        if ee is not None:
            rows, cols, step_px, gap_px, corners = ee
            # auto 模式：若边缘法命中的其实是十字/圆点阵列（连通域非实心），优先十字中心点法
            if pattern == "auto" and _looks_like_cross_array(gray):
                cc = _cross_once()
                if cc is not None:
                    r2, c2, s2, centers = cc
                    ok2, _ = _bounded(r2, c2, "十字中心点法")
                    if ok2:
                        return _build_chess_result(True, r2, c2, s2, 0.0, centers,
                                                   "cross", square_mm, dpi,
                                                   f"识别成功：中心点 {r2}×{c2}（十字中心点法），间距≈{s2:.1f}px", pixel_scale=pixel_scale)
            ok, msg = _bounded(rows, cols, "棋盘格边缘法")
            if ok:
                if progress_cb:
                    progress_cb(0.9)
                return _build_chess_result(True, rows, cols, step_px, gap_px, corners,
                                           "chessboard", square_mm, dpi,
                                           f"识别成功：内角点 {rows}×{cols}（边缘法），单格≈{step_px:.1f}px", pixel_scale=pixel_scale)
        # 旋转回正优先：边缘法失败后，若图像存在明显旋转，先回正走边缘法（最稳）
        ang = _estimate_rotation_deg(gray)
        if ang is not None and abs(ang) >= 1.0:
            gray_rot, rot_sc = _rotate_back(gray, ang)
            ee2 = _auto_detect_edges(gray_rot, direction, min_rows, max_rows, min_cols, max_cols,
                                     margin_frac=0.05)
            if ee2 is not None:
                rows, cols, step_px, gap_px, corners = ee2
                ok, msg = _bounded(rows, cols, "棋盘格旋转回正")
                if ok:
                    corners2 = _unrotate_points(corners / rot_sc, ang, gray.shape)
                    return _build_chess_result(True, rows, cols, step_px / rot_sc, gap_px / rot_sc, corners2,
                                               "chessboard", square_mm, dpi,
                                               f"识别成功：内角点 {rows}×{cols}（旋转回正+边缘法），单格≈{step_px / rot_sc:.1f}px", pixel_scale=pixel_scale)
        if progress_cb:
            progress_cb(0.6)
        rr = _auto_detect_corners(gray, direction, min_rows, max_rows, min_cols, max_cols)
        if rr is not None:
            rows, cols, step_px, corners = rr
            ok, msg = _bounded(rows, cols, "棋盘格角点法")
            if ok:
                return _build_chess_result(True, rows, cols, step_px, 0.0, corners,
                                           "chessboard", square_mm, dpi,
                                           f"识别成功：内角点 {rows}×{cols}（角点法），单格≈{step_px:.1f}px", pixel_scale=pixel_scale)
        # 旋转回正兜底：角点法因轻微旋转漏检角点（行列偏少）时，估计旋转角回正后走边缘法
        if ang is not None and abs(ang) >= 1.0:
            gray_rot2, rot_sc2 = _rotate_back(gray, ang)
            ee3 = _auto_detect_edges(gray_rot2, direction, min_rows, max_rows, min_cols, max_cols,
                                     margin_frac=0.05)
            if ee3 is not None:
                rows, cols, step_px, gap_px, corners = ee3
                ok, msg = _bounded(rows, cols, "棋盘格旋转回正")
                if ok:
                    corners2 = _unrotate_points(corners / rot_sc2, ang, gray.shape)
                    return _build_chess_result(True, rows, cols, step_px / rot_sc2, gap_px / rot_sc2, corners2,
                                               "chessboard", square_mm, dpi,
                                               f"识别成功：内角点 {rows}×{cols}（旋转回正+边缘法），单格≈{step_px / rot_sc2:.1f}px", pixel_scale=pixel_scale)

    # ---------- 十字/圆点中心点模式 ----------
    if pattern in ("cross", "auto"):
        if progress_cb:
            progress_cb(0.7)
        cc = _cross_once()
        if cc is not None:
            rows, cols, step_px, centers = cc
            ok, msg = _bounded(rows, cols, "十字中心点法")
            if ok:
                return _build_chess_result(True, rows, cols, step_px, 0.0, centers,
                                           "cross", square_mm, dpi,
                                           f"识别成功：中心点 {rows}×{cols}（十字中心点法），间距≈{step_px:.1f}px", pixel_scale=pixel_scale)

    # ---------- 失败诊断 ----------
    return AutoChessDetectResult(False, message=_diagnose_failure(gray, max_rows, max_cols))
