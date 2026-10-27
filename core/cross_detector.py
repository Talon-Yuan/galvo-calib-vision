# -*- coding: utf-8 -*-
"""十字中心检测：主算法=投影质心，复核=cornerSubPix，兜底=模板匹配。
对大图采用ROI网格遍历，每个十字独立检测。"""
import math
from typing import List, Optional, Tuple

import cv2
import numpy as np

from .config import CrossDetectParams


class CrossResult:
    __slots__ = ("x", "y", "ok", "method", "area", "refined", "conf", "fail_reason")

    def __init__(self, x: float, y: float, ok: bool, method: str = "", area: float = 0.0,
                 refined: bool = False, conf: float = 1.0, fail_reason: str = ""):
        self.x, self.y, self.ok, self.method, self.area = x, y, ok, method, area
        self.refined = refined  # 手动点是否经吸附精化（识别过程记录用）
        self.conf = conf        # 检测置信度 [0,1]（方案D）：1.0=高置信，低置信时 ok=False
        self.fail_reason = fail_reason  # 失败原因标识（如 "LOW_CONF"），""=无


def _resolve_polarity(gray: np.ndarray, p: CrossDetectParams) -> str:
    """确定极性：auto 时用 Otsu 阈值统计暗像素占比，暗像素为多数则黑底白线，否则白底黑线。
    判断本身不改变图像，只决定阈值方向与投影权重方向，不引入精度损失。"""
    if p.polarity != "auto":
        return p.polarity
    t, _ = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    dark_ratio = float((gray < t).mean()) if gray.size else 0.0
    return "light_on_dark" if dark_ratio > 0.5 else "dark_on_light"


def _threshold(gray: np.ndarray, p: CrossDetectParams) -> np.ndarray:
    # dark_on_light：暗色十字为前景 → THRESH_BINARY_INV
    # light_on_dark：亮色十字为前景 → THRESH_BINARY
    inv = _resolve_polarity(gray, p) == "dark_on_light"
    flag = cv2.THRESH_BINARY_INV if inv else cv2.THRESH_BINARY
    if p.threshold_mode == "adaptive":
        return cv2.adaptiveThreshold(gray, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
                                     flag, p.threshold_blocksize | 1, p.threshold_c)
    if p.threshold_mode == "fixed":
        _, bw = cv2.threshold(gray, p.fixed_threshold, 255, flag)
        return bw
    # otsu（默认）
    _, bw = cv2.threshold(gray, 0, 255, flag + cv2.THRESH_OTSU)
    return bw


def _find_target_component(bw: np.ndarray, p: CrossDetectParams, min_area: int = 0, max_area: int = 0):
    """闭运算后找十字连通域（面积/宽高比/填充率筛选），返回掩膜bbox或None。
    min_area/max_area 显式传入时优先（避免并发下修改共享参数对象）。
    填充率过滤：十字为细臂交叉（fill≈0.02~0.15），可滤除实心块、坐标轴符号等干扰。"""
    k = p.morph_kernel
    if k > 1:
        bw = cv2.morphologyEx(bw, cv2.MORPH_CLOSE, np.ones((k, k), np.uint8))
    n, labels, stats, _ = cv2.connectedComponentsWithStats(bw, connectivity=8)
    best = None
    best_area = 0
    h, w = bw.shape
    ma = max_area if max_area else p.max_area
    for i in range(1, n):
        x, y, bw_, bh, area = stats[i]
        if area < min_area:
            continue
        if ma and area > ma:
            continue
        if bw_ <= 1 or bh <= 1:
            continue
        ar = bw_ / bh if bh > 0 else 0
        lo, hi = p.aspect_range
        if not (lo <= ar <= hi):
            continue
        # 填充率过滤：前景面积 / 外接矩形面积（十字细臂交叉远小于1）
        fill = area / (bw_ * bh) if bw_ and bh else 1.0
        flo, fhi = p.fill_range
        if not (flo <= fill <= fhi):
            continue
        # 占ROI面积比例过滤（十字一般不超过ROI的60%）
        if area > 0.6 * h * w:
            continue
        if area > best_area:
            best_area = area
            best = (x, y, bw_, bh)
    return best


def _projection_center(gray: np.ndarray, bw: np.ndarray, x: int, y: int, bw_: int, bh: int,
                       polarity: str = "dark_on_light") -> Tuple[float, float]:
    """对目标bbox做行列灰度投影，取加权质心。权重方向随极性同步取反：
    暗线(白底黑线)用 255-灰度，亮线(黑底白线)直接用灰度。"""
    roi = gray[y:y + bh, x:x + bw_].astype(np.float32)
    mask = bw[y:y + bh, x:x + bw_].astype(np.float32)
    if polarity == "light_on_dark":
        wgt = np.clip(roi, 0, 255) * (mask > 0)
    else:
        wgt = np.clip(255.0 - roi, 0, 255) * (mask > 0)
    row_sum = wgt.sum(axis=1)
    col_sum = wgt.sum(axis=0)
    ry = float(np.average(np.arange(bh), weights=row_sum)) if row_sum.sum() > 0 else bh / 2
    cx = float(np.average(np.arange(bw_), weights=col_sum)) if col_sum.sum() > 0 else bw_ / 2
    return x + cx, y + ry


def _edge_center(bw: np.ndarray, x: int, y: int, bw_: int, bh: int) -> Tuple[float, float]:
    """寻边中线交点法：把十字前景按相对质心的方向分为上/下/左/右四臂，
    分别取水平臂（左+右）纵坐标均值得到横中线 y、垂直臂（上+下）横坐标均值得到竖中线 x，
    两中线交点即十字中心。
    对臂宽不均匀、亮度不对称、局部反光均比投影质心更稳（中线只依赖各臂的均值中心）。"""
    sub = bw[y:y + bh, x:x + bw_].astype(np.uint8)
    ys, xs = np.nonzero(sub > 0)
    if xs.size == 0:
        return x + bw_ / 2.0, y + bh / 2.0
    cx0 = x + float(xs.mean())
    cy0 = y + float(ys.mean())
    dx = xs.astype(np.float64) + x - cx0
    dy = ys.astype(np.float64) + y - cy0
    horiz = np.abs(dx) >= np.abs(dy)   # 水平臂（左/右）
    vert = ~horiz                       # 垂直臂（上/下）
    if horiz.sum() == 0 or vert.sum() == 0:
        # 单臂退化：退回前景质心
        return cx0, cy0
    y_left_mask = horiz & (dx < 0)
    y_right_mask = horiz & (dx >= 0)
    x_top_mask = vert & (dy < 0)
    x_bottom_mask = vert & (dy >= 0)
    # 任一侧为空（十字贴ROI边缘/不完整）时 np.mean 产生 NaN，回退前景质心
    if y_left_mask.sum() == 0 or y_right_mask.sum() == 0 or x_top_mask.sum() == 0 or x_bottom_mask.sum() == 0:
        return cx0, cy0
    y_left = float(ys[y_left_mask].mean()) + y
    y_right = float(ys[y_right_mask].mean()) + y
    x_top = float(xs[x_top_mask].mean()) + x
    x_bottom = float(xs[x_bottom_mask].mean()) + x
    mid_x = (x_top + x_bottom) / 2.0
    mid_y = (y_left + y_right) / 2.0
    return mid_x, mid_y


# ==================== 改进算法（2026-09-23 方案实施） ====================

def _cross_geometry(bw_sub: np.ndarray, cx: Optional[float] = None,
                    cy: Optional[float] = None) -> Optional[Tuple[float, float]]:
    """估算十字线宽与最短臂长（组件bbox内坐标）。
    bw_sub: 组件掩膜（bbox内）；cx/cy: 组件内质心坐标（缺省用像素均值）。
    返回 (line_w, min_arm) 或 None（像素太少无法估计）。
    """
    h, w = bw_sub.shape
    ys, xs = np.nonzero(bw_sub > 0)
    if xs.size < 20:
        return None
    if cx is None:
        cx = float(xs.mean())
    if cy is None:
        cy = float(ys.mean())
    dx = xs.astype(np.float64) - cx
    dy = ys.astype(np.float64) - cy
    horiz = np.abs(dx) >= np.abs(dy)
    vert = ~horiz
    if horiz.sum() >= 5 and vert.sum() >= 5:
        # 线宽 = 水平臂的纵向半宽与垂直臂的横向半宽的较小值（×2 为全宽）
        hw_h = float(np.percentile(np.abs(dy[horiz]), 90)) * 2.0
        hw_v = float(np.percentile(np.abs(dx[vert]), 90)) * 2.0
        line_w = max(1.0, min(hw_h, hw_v))
    else:
        # 退化：用 bbox 短边 8% 作线宽估计
        line_w = max(1.0, min(h, w) * 0.08)
    min_arm = min(max(cx - xs.min(), 1.0), max(xs.max() - cx, 1.0),
                  max(cy - ys.min(), 1.0), max(ys.max() - cy, 1.0))
    return line_w, min_arm


def _robust_line_fit(xs, ys) -> Optional[Tuple[float, float]]:
    """最小二乘直线拟合 y = a*x + b，带一次离群剔除（|残差|>2.5σ 剔除后重拟合）。
    返回 (a, b) 或 None（点数不足）。拟合天然抑制噪点/毛刺，抗离群。"""
    xs = np.asarray(xs, np.float64)
    ys = np.asarray(ys, np.float64)
    if xs.size < 3:
        return None
    X = np.vstack([xs, np.ones_like(xs)]).T
    try:
        sol, *_ = np.linalg.lstsq(X, ys, rcond=None)
    except Exception:
        return None
    a, b = float(sol[0]), float(sol[1])
    res = ys - (a * xs + b)
    sd = float(np.std(res))
    if sd > 1e-6:
        keep = np.abs(res) <= 2.5 * sd
        if keep.sum() >= 3:
            try:
                a2, b2 = np.linalg.lstsq(X[keep], ys[keep], rcond=None)[0]
                a, b = float(a2), float(b2)
            except Exception:
                pass
    return a, b


def _sym_window_centroid(gray: np.ndarray, bw: np.ndarray, x: int, y: int, bw_: int, bh: int,
                         cx: float, cy: float, w_half: float,
                         polarity: str) -> Optional[Tuple[float, float]]:
    """在【以(cx,cy)为中心的对称方形窗口】内计算加权质心（整图坐标）。
    窗口对称 ⇒ 长臂/亮臂不再产生偏向权重，质心只反映交叉区真实中心。"""
    hw = int(round(w_half))
    x0 = max(x, int(round(cx)) - hw)
    y0 = max(y, int(round(cy)) - hw)
    x1 = min(x + bw_, int(round(cx)) + hw + 1)
    y1 = min(y + bh, int(round(cy)) + hw + 1)
    if x1 - x0 < 3 or y1 - y0 < 3:
        return None
    roi = gray[y0:y1, x0:x1].astype(np.float32)
    mask = bw[y0:y1, x0:x1].astype(np.float32)
    if polarity == "light_on_dark":
        wgt = np.clip(roi, 0, 255) * (mask > 0)
    else:
        wgt = np.clip(255.0 - roi, 0, 255) * (mask > 0)
    row_sum = wgt.sum(axis=1)
    col_sum = wgt.sum(axis=0)
    if row_sum.sum() <= 0 or col_sum.sum() <= 0:
        return None
    ry = float(np.average(np.arange(y1 - y0), weights=row_sum))
    rx = float(np.average(np.arange(x1 - x0), weights=col_sum))
    return x0 + rx, y0 + ry


def _projection_center_sym(gray: np.ndarray, bw: np.ndarray, x: int, y: int, bw_: int, bh: int,
                           polarity: str, p: CrossDetectParams) -> Tuple[float, float, bool]:
    """投影质心改进版：对称窗口质心 + 迭代收敛（+ 可选高斯拟合由调用方增强）。
    1) 粗定位：整bbox投影质心（沿用旧算法）；2) 以粗中心为心取对称窗口；
    3) 窗口内重新加权质心；4) 迭代至中心位移<0.1px。

    返回 (cx, cy, degraded)：degraded=True 表示对称窗口曾退化（返回 None），
    本次坐标退回到"整bbox粗质心"或"上一轮迭代值"，精度低于正常路径，
    供调用方在 method 上留痕（便于事后追溯，不影响坐标与判定）。"""
    cx0, cy0 = _projection_center(gray, bw, x, y, bw_, bh, polarity)
    bw_sub = bw[y:y + bh, x:x + bw_]
    geo = _cross_geometry(bw_sub, cx0 - x, cy0 - y)
    if geo is None:
        return cx0, cy0, False
    line_w, min_arm = geo
    # 窗口半宽 = clamp(线宽×系数, 8, 最短臂/2)：保证窗口不超出臂端，避免再次被长臂拉偏
    w_half = float(np.clip(line_w * p.sym_win_scale, 8.0, max(8.0, min_arm * 0.5)))
    cx, cy = cx0, cy0
    degraded = False
    for _ in range(max(1, p.sym_iterations)):
        # v3.3.1 修复：原实现写成 `nx, ny = _sym_window_centroid(...)`，随后才判 `nx is None`。
        # _sym_window_centroid 在对称窗口退化（窗口<3px，或窗口内无有效权重，如过曝/无对比）
        # 时会返回 None，此时解包先抛 TypeError，且异常会穿透 detect_points_fast 使**整批检测中断**
        # （已在 003 号 30mm 图复现）。改为先接返回值、判空后再解包；退化时保留当前质心并结束迭代，
        # 与作者原意（break）一致，不引入假兜底：返回的仍是可追溯的粗定位/上一轮质心。
        res = _sym_window_centroid(gray, bw, x, y, bw_, bh, cx, cy, w_half, polarity)
        if res is None:
            degraded = True      # 留痕：本次坐标来自粗质心/上一轮，精度低于正常路径
            break
        nx, ny = res
        dx, dy = nx - cx, ny - cy
        cx, cy = nx, ny
        if abs(dx) < 0.1 and abs(dy) < 0.1:
            break
    return cx, cy, degraded


def _gauss_peak_pos(profile) -> Optional[float]:
    """对一维灰度剖面做高斯峰位（亚像素）。暗线自动取反相，亮线直接用。
    采用 log 变换后抛物线拟合（高斯取对数=抛物线），返回峰位或 None。"""
    prof = np.asarray(profile, np.float64)
    if prof.size < 5:
        return None
    n = prof.size
    c = prof[n // 2]
    bg = (float(prof[0]) + float(prof[-1])) / 2.0
    if c < bg:  # 暗线（白底黑线）：取反为峰
        prof = 255.0 - prof
        c, bg = 255.0 - c, 255.0 - bg
    if c - bg < 3.0:
        return None
    y = np.log(np.clip(prof - bg + 1e-3, 1e-3, None))
    x = np.arange(n, dtype=np.float64)
    ymin, ymax = y.min(), y.max()
    if ymax - ymin < 1e-6:
        return None
    keep = y >= ymin + 0.3 * (ymax - ymin)
    if keep.sum() < 5:
        return None
    xs_ = x[keep]
    ys_ = y[keep]
    A = np.vstack([xs_ ** 2, xs_, np.ones_like(xs_)]).T
    try:
        sol, *_ = np.linalg.lstsq(A, ys_, rcond=None)
    except Exception:
        return None
    a, b, _c = float(sol[0]), float(sol[1]), float(sol[2])
    if a >= 0:  # 抛物线须开口向下（峰）
        return None
    peak = -b / (2.0 * a)
    if not (0.0 <= peak < n):
        return None
    return peak


def _gaussian_center(gray: np.ndarray, bw: np.ndarray, x: int, y: int, bw_: int, bh: int,
                     cx: float, cy: float, p: CrossDetectParams) -> Optional[Tuple[float, float]]:
    """可选高斯拟合增强：在对称窗口内，对水平/垂直两方向多条扫描线做高斯峰位，
    最小二乘拟合横/竖中线，交点作为最终中心。对亮度不均、线宽突变更稳。
    仅取避开交叉区（|偏移|>线宽×0.8）的臂中段扫描线，保证峰位反映臂中心。"""
    bw_sub = bw[y:y + bh, x:x + bw_]
    geo = _cross_geometry(bw_sub, cx - x, cy - y)
    if geo is None:
        return None
    line_w, min_arm = geo
    w_half = float(np.clip(line_w * p.sym_win_scale, 8.0, max(8.0, min_arm * 0.5)))
    x0 = max(x, int(round(cx)) - int(round(w_half)))
    y0 = max(y, int(round(cy)) - int(round(w_half)))
    x1 = min(x + bw_, int(round(cx)) + int(round(w_half)) + 1)
    y1 = min(y + bh, int(round(cy)) + int(round(w_half)) + 1)
    if x1 - x0 < 5 or y1 - y0 < 5:
        return None
    roi = gray[y0:y1, x0:x1].astype(np.float32)
    mask = (bw[y0:y1, x0:x1] > 0)
    excl = max(2.0, min(line_w * 0.8, w_half * 0.55))
    gap = max(1.0, min(line_w * p.gauss_scan_gap, w_half * 0.35))
    # 水平臂中心线：每列（避开交叉区）垂直剖面高斯峰位 → (x, y(x))
    ys_pts: List[Tuple[float, float]] = []
    xx = x0
    while xx < x1:
        col = int(round(xx - x0))
        if abs(xx - cx) > excl and mask[:, col].any():
            prof = roi[:, col]
            cyi = int(round(cy - y0))
            lo = max(0, cyi - int(line_w * 2))
            hi = min(len(prof), cyi + int(line_w * 2) + 1)
            seg = prof[lo:hi]
            if seg.size >= 5:
                peak = _gauss_peak_pos(seg)
                if peak is not None:
                    ys_pts.append((float(xx), float(lo + peak)))
        xx += gap
    # 垂直臂中心线：每行（避开交叉区）水平剖面高斯峰位 → (x(y), y)
    xs_pts: List[Tuple[float, float]] = []
    yy = y0
    while yy < y1:
        row = int(round(yy - y0))
        if abs(yy - cy) > excl and mask[row, :].any():
            prof = roi[row, :]
            cxi = int(round(cx - x0))
            lo = max(0, cxi - int(line_w * 2))
            hi = min(len(prof), cxi + int(line_w * 2) + 1)
            seg = prof[lo:hi]
            if seg.size >= 5:
                peak = _gauss_peak_pos(seg)
                if peak is not None:
                    xs_pts.append((float(lo + peak), float(yy)))
        yy += gap
    if len(ys_pts) < 3 or len(xs_pts) < 3:
        return None
    # 横中线：y = a*cx + b（在中心 x 处取值）
    fit_h = _robust_line_fit(np.array([p_[0] for p_ in ys_pts]), np.array([p_[1] for p_ in ys_pts]))
    if fit_h is None:
        return None
    mid_y = fit_h[0] * cx + fit_h[1]
    # 竖中线：x = a*cy + b（在中心 y 处取值）
    fit_v = _robust_line_fit(np.array([p_[1] for p_ in xs_pts]), np.array([p_[0] for p_ in xs_pts]))
    if fit_v is None:
        return None
    mid_x = fit_v[0] * cy + fit_v[1]
    return mid_x, mid_y


def _pixel_angle(dx, dy) -> Optional[float]:
    """像素集主轴角度（度，0=水平，90=垂直）。基于协方差主特征向量。
    返回值折叠到 [0,90]：近水平→0，近垂直→90，负角/钝角一并折叠。"""
    dx = np.asarray(dx, np.float64)
    dy = np.asarray(dy, np.float64)
    if dx.size < 3:
        return None
    cov = np.cov(np.vstack([dx, dy]))
    w, v = np.linalg.eigh(cov)
    main = v[:, int(np.argmax(w))]
    return float(np.degrees(np.arctan2(abs(main[1]), abs(main[0]))))


def _pixel_angle_signed(dx, dy) -> Optional[float]:
    """像素集主轴有符号角度（度，atan2，范围 (-180,180]），不折叠。
    用于正交判定：两直线夹角 = |d1-d2| 取模 180 到锐角，避免折叠误判。"""
    dx = np.asarray(dx, np.float64)
    dy = np.asarray(dy, np.float64)
    if dx.size < 3:
        return None
    cov = np.cov(np.vstack([dx, dy]))
    w, v = np.linalg.eigh(cov)
    main = v[:, int(np.argmax(w))]
    return float(np.degrees(np.arctan2(main[1], main[0])))


def _rotate_to_ref(dx, dy, ref_angle_deg) -> Tuple[np.ndarray, np.ndarray]:
    """把相对质心的像素坐标旋转到网格参考系（-ref_angle），使方向约束从"图像轴"
    变为"实测网格方向"。None 时原样返回（兼容旧调用，基准=图像轴）。"""
    if not ref_angle_deg:
        return dx, dy
    th = math.radians(float(ref_angle_deg))
    c, s = math.cos(th), math.sin(th)
    return c * dx + s * dy, -s * dx + c * dy


def _cross_shape_validate(sub: np.ndarray, cx: float, cy: float,
                          p: CrossDetectParams, ref_angle_deg: Optional[float] = None) -> bool:
    """十字形校验：臂数=4、两两正交（90°±tol）、臂长比≤ratio_max、方向约束。
    用于剔除 X/Y 坐标轴文字、字母符号、实心块、长条等非十字目标。
    sub: 组件bbox掩膜（组件内坐标）；cx/cy: 组件内质心坐标。
    ref_angle_deg: 网格参考方向（纸面X轴图像角°），None=图像轴（旧行为）。"""
    h, w = sub.shape
    ys, xs = np.nonzero(sub > 0)
    if xs.size < 20:
        return False
    dx = xs.astype(np.float64) - cx
    dy = ys.astype(np.float64) - cy
    dx, dy = _rotate_to_ref(dx, dy, ref_angle_deg)
    tol = math.tan(math.radians(p.arm_angle_tol_deg))
    horiz = (np.abs(dx) > 0) & (np.abs(dy) <= tol * np.abs(dx))
    vert = (np.abs(dy) > 0) & (np.abs(dx) <= tol * np.abs(dy))
    left = horiz & (dx < 0)
    right = horiz & (dx >= 0)
    top = vert & (dy < 0)
    bottom = vert & (dy >= 0)
    # 臂数=4：每臂像素数足够（不小于组件像素数 0.1%，且至少 8 像素）
    min_arm_px = max(8, int(sub.size * 0.001))
    for arm in (left, right, top, bottom):
        if arm.sum() < min_arm_px:
            return False
    # 臂长比：每臂像素径向距离的 90 分位
    def _arm_len(mask):
        d = np.hypot(dx[mask], dy[mask])
        return float(np.percentile(d, 90)) if d.size else 0.0
    lens = [_arm_len(a) for a in (left, right, top, bottom)]
    mn = min(lens)
    if mn <= 0 or max(lens) / mn > p.arm_ratio_max:
        return False
    # 方向约束 + 正交：旋转系锥形分类子集判定（先绕 ref 旋转，再按 0°/90° 期望分类）。
    # 正对期望方向的十字：旋转系下臂像素大量落入锥形（h_ang≈0/v_ang≈90）；
    # 偏斜目标（旋转角>tol）：臂像素被裁剪只剩交叉区 → 捕获率低被拒。
    h_ang = _pixel_angle(dx[horiz], dy[horiz])
    v_ang = _pixel_angle(dx[vert], dy[vert])
    if h_ang is None or v_ang is None:
        return False
    if h_ang > p.arm_angle_tol_deg or v_ang < 90.0 - p.arm_angle_tol_deg:
        return False
    # 捕获率：锥形分类保留像素占比须足够（十字正对期望方向时 ≈90%+）
    capture = float(horiz.sum() + vert.sum()) / max(xs.size, 1)
    if capture < p.dir_capture_min:
        return False
    # 正交判定用有符号角（避免折叠：旋转十字的垂直臂真实角≈90°±θ，折叠后变小会误判）
    h_s = _pixel_angle_signed(dx[horiz], dy[horiz])
    v_s = _pixel_angle_signed(dx[vert], dy[vert])
    if h_s is None or v_s is None:
        return False
    diff = abs(v_s - h_s) % 180.0
    if diff > 90.0:
        diff = 180.0 - diff
    if abs(diff - 90.0) > p.arm_ortho_tol_deg:
        return False
    return True


def _edge_center_improved(bw: np.ndarray, x: int, y: int, bw_: int, bh: int,
                          p: CrossDetectParams,
                          ref_angle_deg: Optional[float] = None) -> Optional[Tuple[float, float]]:
    """寻边中线交点法（改进版，2026-09-23 方案）：
    A. 十字形校验（臂数=4、正交、臂长比、方向约束）→ 过滤 X/Y 文字等非十字目标；
    B. 四臂稳健直线拟合替代像素均值（抗噪点/毛刺）；
    C. 方向约束剔除与主轴偏差>tol 的像素；
    D. 后验校验：中心与质心偏差 > 线宽×2 判定可疑返回失败（交给手动/扩窗）。
    返回 (mid_x, mid_y) 或 None（非十字目标/拟合失败/可疑）。
    ref_angle_deg: 网格参考方向（纸面X轴图像角°），None=图像轴（旧行为）。"""
    sub = bw[y:y + bh, x:x + bw_].astype(np.uint8)
    ys, xs = np.nonzero(sub > 0)
    if xs.size == 0:
        return None
    cx0 = x + float(xs.mean())
    cy0 = y + float(ys.mean())
    # A. 十字形校验
    if p.cross_check:
        if not _cross_shape_validate(sub, cx0 - x, cy0 - y, p, ref_angle_deg):
            return None
    dx = xs.astype(np.float64) + x - cx0
    dy = ys.astype(np.float64) + y - cy0
    dx, dy = _rotate_to_ref(dx, dy, ref_angle_deg)
    geo = _cross_geometry(sub, cx0 - x, cy0 - y)
    line_w = geo[0] if geo is not None else max(1.0, min(bw_, bh) * 0.08)
    lw_half = line_w * 0.5
    # C. 方向约束（线宽自适应）：水平臂允许 |dy| ≤ 线宽半宽 + tan(tol)*|dx|，
    #    垂直臂同理。既剔除与主轴明显偏斜的噪点，又不会把粗臂像素过度裁剪
    #    （避免小臂长/粗线宽场景下仅剩中心窄带、X/Y 文字占比被放大）。
    tol = math.tan(math.radians(p.arm_angle_tol_deg))
    horiz = (np.abs(dx) > 0) & (np.abs(dy) <= lw_half + tol * np.abs(dx))
    vert = (np.abs(dy) > 0) & (np.abs(dx) <= lw_half + tol * np.abs(dy))
    if horiz.sum() == 0 or vert.sum() == 0:
        return None
    left = horiz & (dx < 0)
    right = horiz & (dx >= 0)
    top = vert & (dy < 0)
    bottom = vert & (dy >= 0)
    if left.sum() < 5 or right.sum() < 5 or top.sum() < 5 or bottom.sum() < 5:
        return None
    xsg = (xs + x).astype(np.float64)
    ysg = (ys + y).astype(np.float64)
    # B. 四臂稳健直线拟合（替代像素均值）
    fit_l = _robust_line_fit(xsg[left], ysg[left])    # 左臂 y = aL*x+bL
    fit_r = _robust_line_fit(xsg[right], ysg[right])  # 右臂 y = aR*x+bR
    fit_t = _robust_line_fit(ysg[top], xsg[top])      # 上臂 x = aT*y+bT
    fit_b = _robust_line_fit(ysg[bottom], xsg[bottom])  # 下臂 x = aB*y+bB
    slope_ok = False
    if fit_l is not None and fit_r is not None and fit_t is not None and fit_b is not None:
        a_l, b_l = fit_l
        a_r, b_r = fit_r
        a_t, b_t = fit_t
        a_b, b_b = fit_b
        # 拟合斜率须在十字允许倾斜范围内（|a|≤tan(tol)）；超限说明拟合被
        # X/Y 文字/邻域结构污染（斜率呈伪相关），回退像素均值法。
        slope_ok = (abs(a_l) <= tol and abs(a_r) <= tol
                    and abs(a_t) <= tol and abs(a_b) <= tol)
        if slope_ok:
            # 在质心处取值：横中线 y=(yL(cx0)+yR(cx0))/2，竖中线 x=(xT(cy0)+xB(cy0))/2
            mid_x = (a_t * cy0 + b_t + a_b * cy0 + b_b) / 2.0
            mid_y = (a_l * cx0 + b_l + a_r * cx0 + b_r) / 2.0
            # 拟合与均值一致性校验：两者中心差超阈值说明拟合被局部结构污染，
            # 回退均值法（实测在中心十字/文字干扰场景更稳）。
            horiz_o = np.abs(dx) >= np.abs(dy)
            vert_o = ~horiz_o
            left_o = horiz_o & (dx < 0)
            right_o = horiz_o & (dx >= 0)
            top_o = vert_o & (dy < 0)
            bottom_o = vert_o & (dy >= 0)
            if left_o.sum() > 0 and right_o.sum() > 0 and top_o.sum() > 0 and bottom_o.sum() > 0:
                m_y = (float(ysg[left_o].mean()) + float(ysg[right_o].mean())) / 2.0
                m_x = (float(xsg[top_o].mean()) + float(xsg[bottom_o].mean())) / 2.0
                if math.hypot(mid_x - m_x, mid_y - m_y) > p.fit_vs_mean_max_delta_px:
                    mid_x, mid_y = m_x, m_y
                    slope_ok = False
    if not slope_ok:
        # 回退：四臂像素均值（对称抵消，抗文字/噪点污染，外推稳定）。
        # 分类用旧版对角线划分（|dx|>=|dy|），已实测在粗块/中心十字场景最稳
        # （方向约束的线宽放宽会把中心块更多像素混入臂分类，破坏均值对称性）。
        horiz_o = np.abs(dx) >= np.abs(dy)
        vert_o = ~horiz_o
        left_o = horiz_o & (dx < 0)
        right_o = horiz_o & (dx >= 0)
        top_o = vert_o & (dy < 0)
        bottom_o = vert_o & (dy >= 0)
        if left_o.sum() == 0 or right_o.sum() == 0 or top_o.sum() == 0 or bottom_o.sum() == 0:
            return None
        mid_y = (float(ysg[left_o].mean()) + float(ysg[right_o].mean())) / 2.0
        mid_x = (float(xsg[top_o].mean()) + float(xsg[bottom_o].mean())) / 2.0
    # D. 后验校验：中心与质心偏差 > 线宽×2 → 可疑（可能选中非十字目标/严重噪点）
    if abs(mid_x - cx0) > line_w * 2.0 or abs(mid_y - cy0) > line_w * 2.0:
        return None
    return mid_x, mid_y


def refine_manual_point(gray: np.ndarray, px: float, py: float, p: CrossDetectParams,
                        win: int = 41, max_iter: int = 5) -> Optional[Tuple[float, float]]:
    """手动点击吸附精化：以点击点为种子，在局部窗口内做迭代对称窗口质心（含局部掩膜）。
    返回精化后的整图坐标；窗口内无有效目标时返回 None（调用方保留点击原坐标）。
    win: 局部窗口边长（调用方按十字间距自适应传入）。"""
    h, w = gray.shape
    cx, cy = float(px), float(py)
    half = max(8, win // 2)
    for _ in range(max_iter):
        x0 = max(0, int(round(cx)) - half)
        y0 = max(0, int(round(cy)) - half)
        x1 = min(w, int(round(cx)) + half + 1)
        y1 = min(h, int(round(cy)) + half + 1)
        if x1 - x0 < 5 or y1 - y0 < 5:
            break
        roi = gray[y0:y1, x0:x1].astype(np.float32)
        roi_u8 = np.clip(roi, 0, 255).astype(np.uint8)
        polarity = _resolve_polarity(roi_u8, p)
        if polarity == "light_on_dark":
            wgt = np.clip(roi, 0, 255)
        else:
            wgt = np.clip(255.0 - roi, 0, 255)
        # 局部掩膜：窗口内 Otsu 二值化，前景占比过低/过高视为无有效目标
        bw_local = _threshold(roi.astype(np.uint8), p)
        ratio = float((bw_local > 0).mean())
        if 0.02 <= ratio <= 0.85:
            wgt *= (bw_local > 0).astype(np.float32)
        row_sum = wgt.sum(axis=1)
        col_sum = wgt.sum(axis=0)
        if row_sum.sum() <= 0 or col_sum.sum() <= 0:
            break
        ny = y0 + float(np.average(np.arange(y1 - y0), weights=row_sum))
        nx = x0 + float(np.average(np.arange(x1 - x0), weights=col_sum))
        ddx, ddy = nx - cx, ny - cy
        cx, cy = nx, ny
        if abs(ddx) < 0.1 and abs(ddy) < 0.1:
            break
    return cx, cy


def _confidence_score(gray: np.ndarray, bw: np.ndarray, x: int, y: int, bw_: int, bh: int,
                      cx: float, cy: float, polarity: str,
                      p: CrossDetectParams) -> float:
    """十字检测置信度评分 [0,1]（方案D：弱十字/噪声交点不再静默放行成 OK）。

    评分维度（加权求和）：
    1. 臂对称性：四臂 90 分位长度比 min/max（≈1 健康，越接近臂长比上限越差）
    2. 正交度：水平臂方向≈0°、垂直臂方向≈90° 的偏差（归一化到方向容差）
    3. 对比度：前景/背景灰度差（<16 灰度级 → 低分，弱十字/残影典型）
    4. 中心一致性：最终中心与掩膜质心偏差 / 线宽（中心漂移大 → 低分）

    说明：gray/bw 为 ROI 内图像与掩膜；x/y/bw_/bh 为组件 bbox（ROI 内）；
    cx/cy 为最终检测中心（ROI 内坐标）。
    """
    sub = bw[y:y + bh, x:x + bw_].astype(np.uint8)
    ys, xs = np.nonzero(sub > 0)
    if xs.size < 20:
        return 0.0
    cxc, cyc = cx - x, cy - y  # 组件内中心
    dx = xs.astype(np.float64) - cxc
    dy = ys.astype(np.float64) - cyc
    horiz = np.abs(dx) >= np.abs(dy)
    vert = ~horiz

    # 1) 臂对称性
    def _arm90(mask):
        d = np.hypot(dx[mask], dy[mask])
        return float(np.percentile(d, 90)) if d.size else 0.0
    lens = [_arm90(horiz & (dx < 0)), _arm90(horiz & (dx >= 0)),
            _arm90(vert & (dy < 0)), _arm90(vert & (dy >= 0))]
    mn, mx = min(lens), max(lens)
    sym = float(np.clip(1.0 - (mx / mn - 1.0) / 2.0, 0.0, 1.0)) if mn > 0 else 0.0

    # 2) 正交度（折叠角：水平臂≈0°、垂直臂≈90°）
    def _angle(mask):
        if mask.sum() < 5:
            return None
        return float(np.degrees(np.arctan2(np.mean(dy[mask]), np.mean(dx[mask]))))
    h_ang = _angle(horiz)
    v_ang = _angle(vert)
    tol = max(1.0, p.arm_angle_tol_deg)
    if h_ang is None or v_ang is None:
        ortho = 0.0
    else:
        dev = max(abs(h_ang - 0.0), abs(v_ang - 90.0))
        ortho = float(np.clip(1.0 - dev / tol, 0.0, 1.0))

    # 3) 对比度（前景/背景灰度差，归一化到 40 灰度级）
    roi = gray[y:y + bh, x:x + bw_].astype(np.float32)
    fg = roi[sub > 0]
    bg_mask = (sub == 0)
    bg = roi[bg_mask] if bg_mask.sum() > 0 else np.zeros(1, np.float32)
    if polarity == "dark_on_light":
        diff = float(np.mean(bg) - np.mean(fg))   # 白底黑十字
    else:
        diff = float(np.mean(fg) - np.mean(bg))   # 黑底白十字
    contrast = float(np.clip(diff / 40.0, 0.0, 1.0))

    # 4) 中心一致性（|最终中心 - 掩膜质心| 相对线宽）
    geo = _cross_geometry(sub, cxc, cyc)
    line_w = geo[0] if geo is not None else max(1.0, min(bw_, bh) * 0.08)
    cxm = float(xs.mean()) + x
    cym = float(ys.mean()) + y
    cent_dev = math.hypot(cx - cxm, cy - cym) / max(1.0, line_w * 2.0)
    center_cons = float(np.clip(1.0 - cent_dev, 0.0, 1.0))

    conf = (p.conf_w_sym * sym + p.conf_w_ortho * ortho +
            p.conf_w_contrast * contrast + p.conf_w_center * center_cons)
    return float(conf)


def detect_cross_in_roi(roi_gray: np.ndarray, p: CrossDetectParams,
                        ref_angle_deg: Optional[float] = None) -> Optional[CrossResult]:
    """在单个ROI中检测十字中心，返回亚像素坐标（相对整图偏移需由调用方叠加）。
    ref_angle_deg: 网格参考方向（纸面X轴图像角°），None=图像轴（旧行为）。"""
    if roi_gray is None or roi_gray.size == 0:
        return None
    # 十字面积约为 2*臂长*线宽，真实扫描细十字约占 ROI 0.3%；min_area_ratio 默认 0.1% 兜底兼容
    min_area = max(60, int(roi_gray.size * p.min_area_ratio)) if p.min_area == 0 else p.min_area
    # 自动上限：不超过 ROI 10%。真实十字在 ROI 中占比约 0.2%~2%，
    # 背景/纸面阴影等大块连通域可占 30%+，若按 60% 会误抢十字（4800dpi 角点实测复现），收紧后仅滤大背景。
    # 2026-10-14 真实测试图：粗十字占 6%~7% 被 5% 上限当背景滤掉致全图回退大窗口，上限放宽至 10% 后 coarse 命中。
    max_area = p.max_area if p.max_area else int(roi_gray.size * 0.10)
    bw = _threshold(roi_gray, p)
    comp = _find_target_component(bw, p, min_area=min_area, max_area=max_area)
    if comp is None:
        return None
    x, y, bw_, bh = comp
    polarity = _resolve_polarity(roi_gray, p)
    if p.method == "edge_center":
        # 寻边中线交点法（改进版）：十字形校验 + 四臂直线拟合 + 方向约束 + cornerSubPix 精化
        refined = _edge_center_improved(bw, x, y, bw_, bh, p, ref_angle_deg)
        if refined is None:
            # 非十字目标/拟合失败/后验可疑：失败（交给扩窗重试或手动补点），不再回退前景质心
            return None
        cx, cy = refined
        method = "edge_center"
        # 精化补充：交点附近 cornerSubPix（沿用 projection 分支参数与超限回退逻辑）
        if p.subpixel_refine and 0 < cx < roi_gray.shape[1] - 1 and 0 < cy < roi_gray.shape[0] - 1:
            try:
                win = p.refine_win
                gray32 = roi_gray.astype(np.float32)
                x0 = max(0, int(round(cx)) - win)
                y0 = max(0, int(round(cy)) - win)
                x1 = min(roi_gray.shape[1], int(round(cx)) + win + 1)
                y1 = min(roi_gray.shape[0], int(round(cy)) + win + 1)
                sub = gray32[y0:y1, x0:x1]
                corner = np.array([[[cx - x0, cy - y0]]], dtype=np.float32)
                cv2.cornerSubPix(sub, corner, (5, 5), (-1, -1),
                                 (cv2.TERM_CRITERIA_EPS + cv2.TERM_CRITERIA_MAX_ITER, 30, 0.01))
                rx, ry = corner[0, 0, 0] + x0, corner[0, 0, 1] + y0
                if abs(rx - cx) <= p.refine_max_delta and abs(ry - cy) <= p.refine_max_delta:
                    cx, cy = rx, ry
                    method = "edge_center+subpix"
            except cv2.error:
                pass
    else:
        # 投影质心改进版：对称窗口质心 + 迭代收敛（可选高斯拟合增强）
        cx, cy, _sym_deg = _projection_center_sym(roi_gray, bw, x, y, bw_, bh, polarity, p)
        method = "projection+sym"
        if _sym_deg:
            # 留痕（v3.3.1）：对称窗口退化过，坐标来自粗质心/上一轮迭代，精度低于正常路径。
            # 只加诊断标签，不改坐标与判定（仍走下方置信度门）。
            method = "projection+sym+coarse"
        if p.use_gaussian_fit:
            gxy = _gaussian_center(roi_gray, bw, x, y, bw_, bh, cx, cy, p)
            if gxy is not None:
                cx, cy = gxy
                method = "projection+gauss"
        if p.subpixel_refine and 0 < cx < roi_gray.shape[1] - 1 and 0 < cy < roi_gray.shape[0] - 1:
            try:
                win = p.refine_win
                gray32 = roi_gray.astype(np.float32)
                # 以质心为中心开小窗做 cornerSubPix 精化
                x0 = max(0, int(round(cx)) - win)
                y0 = max(0, int(round(cy)) - win)
                x1 = min(roi_gray.shape[1], int(round(cx)) + win + 1)
                y1 = min(roi_gray.shape[0], int(round(cy)) + win + 1)
                sub = gray32[y0:y1, x0:x1]
                corner = np.array([[[cx - x0, cy - y0]]], dtype=np.float32)
                cv2.cornerSubPix(sub, corner, (5, 5), (-1, -1),
                                 (cv2.TERM_CRITERIA_EPS + cv2.TERM_CRITERIA_MAX_ITER, 30, 0.01))
                rx, ry = corner[0, 0, 0] + x0, corner[0, 0, 1] + y0
                # 粗十字（高DPI/粗线）中心是线条交叉点，cornerSubPix 会误收敛到附近角点；
                # 偏移超过阈值说明精化不可信，回退投影质心（对粗线更准）。
                if abs(rx - cx) <= p.refine_max_delta and abs(ry - cy) <= p.refine_max_delta:
                    cx, cy = rx, ry
                    method = method + "+subpix"
            except cv2.error:
                pass
    area = float(bw_.item() * bh.item()) if hasattr(bw_, "item") else float(bw_ * bh)
    # 方案D：置信度评分，低置信十字（弱十字/噪声交点）不再静默放行成 OK，
    # 显式标 DETECT_FAIL（保留坐标供查看/手动修正，ok=False 使下游统计排除）。
    conf = _confidence_score(roi_gray, bw, x, y, bw_, bh, cx, cy, polarity, p)
    if conf < p.conf_low:
        return CrossResult(cx, cy, False, method, area, conf=conf, fail_reason="LOW_CONF")
    return CrossResult(cx, cy, True, method, area, conf=conf)


def detect_grid(gray: np.ndarray, rows: int, cols: int, pitch_px: float,
                origin_px: Tuple[float, float], p: CrossDetectParams,
                progress_cb=None, ref_angle_deg: Optional[float] = None) -> Tuple[List[Optional[CrossResult]], np.ndarray]:
    """按 rows×cols 网格遍历检测，返回 (结果列表[None表示失败], 网格原点像素)。
    网格顺序：先行后列（row-major），便于用户选起始点后按方向编号。
    ref_angle_deg: 网格参考方向（纸面X轴图像角°），None=图像轴（旧行为）。"""
    ox, oy = origin_px
    results: List[Optional[CrossResult]] = []
    win = int(pitch_px * p.search_window_scale / 2)
    h, w = gray.shape
    total = rows * cols
    for r in range(rows):
        for c in range(cols):
            cx0 = int(round(ox + c * pitch_px))
            cy0 = int(round(oy + r * pitch_px))
            x0 = max(0, cx0 - win)
            y0 = max(0, cy0 - win)
            x1 = min(w, cx0 + win + 1)
            y1 = min(h, cy0 + win + 1)
            roi = gray[y0:y1, x0:x1]
            res = detect_cross_in_roi(roi, p, ref_angle_deg)
            if res is not None:
                res.x += x0
                res.y += y0
                results.append(res)
            else:
                results.append(None)
            if progress_cb:
                progress_cb((r * cols + c + 1) / total)
    return results, np.array([ox, oy])


def detect_points(gray: np.ndarray, points: np.ndarray, pitch_px: float,
                  p: CrossDetectParams, progress_cb=None,
                  ref_angle_deg: Optional[float] = None) -> List[Optional[CrossResult]]:
    """按任意网格点列表（Nx2 像素坐标）检测，返回与输入同序的结果列表。
    ref_angle_deg: 网格参考方向（纸面X轴图像角°），None=图像轴（旧行为）。"""
    results: List[Optional[CrossResult]] = []
    win = int(pitch_px * p.search_window_scale / 2)
    total = max(1, len(points))
    for i, (px_, py_) in enumerate(points):
        results.append(_detect_with_expand(gray, px_, py_, win, p, ref_angle_deg=ref_angle_deg))
        if progress_cb:
            progress_cb((i + 1) / total)
    return results


def _detect_one_point(gray: np.ndarray, px_: float, py_: float, win: int,
                      p: CrossDetectParams,
                      ref_angle_deg: Optional[float] = None) -> Optional[CrossResult]:
    """在单个网格点附近以窗口半径 win 检测十字，返回整图坐标结果。"""
    h, w = gray.shape
    x0 = max(0, int(round(px_)) - win)
    y0 = max(0, int(round(py_)) - win)
    x1 = min(w, int(round(px_)) + win + 1)
    y1 = min(h, int(round(py_)) + win + 1)
    roi = gray[y0:y1, x0:x1]
    res = detect_cross_in_roi(roi, p, ref_angle_deg)
    if res is not None:
        res.x += x0
        res.y += y0
    return res


def _detect_with_expand(gray: np.ndarray, px_: float, py_: float, base_win: int,
                        p: CrossDetectParams,
                        expand_scales: Tuple[float, ...] = (1.5, 2.0),
                        ref_angle_deg: Optional[float] = None) -> Optional[CrossResult]:
    """单点检测 + 失败自动扩大搜索窗口重试。

    先用基础窗口检测；失败后按 expand_scales 逐级放大窗口（相对 base_win 的倍数）重试，
    用于网格点理论位置偏差较大或十字超出默认搜索窗的场景。
    """
    res = _detect_one_point(gray, px_, py_, base_win, p, ref_angle_deg)
    if res is not None:
        return res
    for k in expand_scales:
        res = _detect_one_point(gray, px_, py_, int(base_win * k), p, ref_angle_deg)
        if res is not None:
            return res
    return None


def detect_points_fast(gray: np.ndarray, points: np.ndarray, pitch_px: float,
                       p: CrossDetectParams, precheck_gray: Optional[np.ndarray] = None,
                       precheck_scale: float = 1.0, refine_window_scale: float = 0.5,
                       max_workers: int = 1, progress_cb=None,
                       ref_angle_deg: Optional[float] = None) -> List[Optional[CrossResult]]:
    """两阶段十字检测（大图加速）：
    1) 预检：在降采样小图上快速定位候选中心（窗口按 precheck_scale 同比缩小，计算量约 1/N²）
    2) 精测：候选中心映射回原图，仅在原图小窗口（refine_window_scale*pitch）内高精度复检
    预检失败的点回退到原大窗口检测；precheck_gray 为空时等价于 detect_points。
    - precheck_scale: 原图尺寸 / 预检图尺寸（>=1）
    - refine_window_scale: 精测窗口直径相对间距的比例（默认 0.5，半径 0.25*pitch）
    - max_workers: 精测阶段并行线程数（>1 时用线程池并行 ROI 精测）
    - ref_angle_deg: 网格参考方向（纸面X轴图像角°），None=图像轴（旧行为）。
    """
    if precheck_gray is None or precheck_scale <= 1.0:
        return detect_points(gray, points, pitch_px, p, progress_cb, ref_angle_deg)
    results: List[Optional[CrossResult]] = []
    total = max(1, len(points))
    coarse: List[Optional[Tuple[float, float]]] = []
    small_pitch = pitch_px / precheck_scale
    small_win = int(small_pitch * p.search_window_scale / 2)
    # 阶段1：小图预检（全部点，快速）
    for i, (px_, py_) in enumerate(points):
        sx, sy = px_ / precheck_scale, py_ / precheck_scale
        res = _detect_one_point(precheck_gray, sx, sy, small_win, p, ref_angle_deg)
        if res is not None:
            coarse.append((res.x * precheck_scale, res.y * precheck_scale))
        else:
            coarse.append(None)
        if progress_cb:
            progress_cb((i + 1) / total * 0.5)
    # 阶段2：原图小窗口精测（可并行）
    win_refine = int(pitch_px * refine_window_scale / 2)
    win_full = int(pitch_px * p.search_window_scale / 2)
    tasks = list(range(total))

    def _do(i: int) -> Optional[CrossResult]:
        px_, py_ = points[i]
        c = coarse[i]
        if c is None:
            # 预检失败：回退原大窗口（仍失败则自动扩窗重试）
            return _detect_with_expand(gray, px_, py_, win_full, p,
                                       ref_angle_deg=ref_angle_deg)
        cx, cy = c
        res = _detect_one_point(gray, cx, cy, win_refine, p, ref_angle_deg)
        if res is not None:
            return res
        # 精测失败：精测窗先扩窗重试，仍失败再回退原大窗口（同样可扩窗）
        res = _detect_with_expand(gray, cx, cy, win_refine, p,
                                  ref_angle_deg=ref_angle_deg)
        if res is not None:
            return res
        return _detect_with_expand(gray, px_, py_, win_full, p,
                                   ref_angle_deg=ref_angle_deg)

    if max_workers and max_workers > 1:
        import concurrent.futures
        done = 0
        with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as ex:
            for res in ex.map(_do, tasks):
                results.append(res)
                done += 1
                if progress_cb:
                    progress_cb(0.5 + done / total * 0.5)
    else:
        for i in tasks:
            results.append(_do(i))
            if progress_cb:
                progress_cb(0.5 + (i + 1) / total * 0.5)
    return results


def rematch_results_to_grid(results: List[Optional[CrossResult]],
                            grid_points: np.ndarray,
                            pitch_px: float,
                            max_shift_ratio: float = 0.5
                            ) -> Tuple[List[Optional[CrossResult]], List[int], int]:
    """串行校验与最近邻重匹配（方案A核心：修正网格原点/方向偏差导致的点号串行）。

    背景：当网格理论原点/方向与实际十字阵列偏差较大（如窗口中心相对真十字偏移
    (800,1030)px ≈ (4.2,5.4)mm）时，检测窗口会框住邻点十字，表现为"全部检到但
    点号串行"（行序互换、错位）。本函数把每个成功检测点重匹配到最近的网格理论点，
    修正点号错位，为后续网格自校正提供正确的点-行列对应关系。

    算法：贪心最近邻指派——对每个成功检测点，计算其到所有网格理论点的距离，
    按距离升序依次指派（每个理论点最多被一个检测点占用），距离超过
    max_shift_ratio*pitch 的视为无匹配（保留原位）。
    返回 (new_results, matches, n_rematch)：
    - new_results: 重排后的结果列表（None 保持 None，长度与输入一致）
    - matches: 原始索引 i -> 重匹配后的网格索引（-1 表示失败点未匹配；未重排时 match[i]==i）
    - n_rematch: 实际发生重排（matches[i]!=i）的点数；0 表示无串行
    """
    n = len(results)
    pts = np.asarray(grid_points, np.float64)
    if pts.ndim != 2 or pts.shape[0] != n or n == 0:
        return list(results), list(range(n)), 0
    valid = [(i, r) for i, r in enumerate(results) if r is not None and r.ok]
    if len(valid) < 2:
        return list(results), list(range(n)), 0
    thr = max_shift_ratio * pitch_px
    cand = []
    for i, r in valid:
        dist = np.hypot(pts[:, 0] - r.x, pts[:, 1] - r.y)
        j = int(np.argmin(dist))
        cand.append((float(dist[j]), i, j))
    cand.sort(key=lambda t: t[0])
    assigned = set()
    assign: dict = {}
    for d, i, j in cand:
        if d > thr:
            break
        if j in assigned:
            continue
        assigned.add(j)
        assign[i] = j
    new_results: List[Optional[CrossResult]] = [None] * n
    matches: List[int] = [-1] * n
    for i in range(n):
        if i in assign:
            new_results[assign[i]] = results[i]
            matches[i] = assign[i]
        else:
            new_results[i] = results[i]
            matches[i] = i
    n_rematch = sum(1 for i in range(n) if matches[i] != i)
    return new_results, matches, n_rematch


# ============================================================================
# v1.9.17 独有新增（自反推源码修复重建）：
#   - _steger_ridge_points / _ridge_center : Steger 亚像素脊线中心（灰度梯度稳健路径）
#   - detect_tiled                         : 分块主路径十字检测（A1 分块调度）
# 修复说明：反推残留 <NODE>/None.xxx/pass 已按 docstring 语义重建。
# ============================================================================

def _steger_ridge_points(gray: np.ndarray, xs: np.ndarray, ys: np.ndarray):
    """Steger 脊线亚像素点：对前景像素（整图坐标）用 Hessian 特征向量沿法向
    做二阶 Taylor 极值偏移，|t|≤0.5 的像素得到亚像素脊线点。

    对边缘不对称、亮度不均、局部反光比二值掩膜均值更稳（灰度梯度信息保留）。
    返回 (rx, ry) 脊线点数组；可用点不足返回 None（调用方回退寻边中线）。"""
    h, w = gray.shape
    gx = cv2.Sobel(gray, cv2.CV_32F, 1, 0, ksize=3)
    gy = cv2.Sobel(gray, cv2.CV_32F, 0, 1, ksize=3)
    gxx = cv2.Sobel(gx, cv2.CV_32F, 1, 0, ksize=3)
    gyy = cv2.Sobel(gy, cv2.CV_32F, 0, 1, ksize=3)
    gxy = cv2.Sobel(gx, cv2.CV_32F, 0, 1, ksize=3)
    xs_i = xs.astype(np.int64)
    ys_i = ys.astype(np.int64)
    m = (xs_i > 1) & (xs_i < w - 2) & (ys_i > 1) & (ys_i < h - 2)
    if int(m.sum()) < 8:
        return None
    yi = ys_i[m]
    xi = xs_i[m]
    gx_v = gx[yi, xi]
    gy_v = gy[yi, xi]
    gxx_v = gxx[yi, xi]
    gyy_v = gyy[yi, xi]
    gxy_v = gxy[yi, xi]
    tr = gxx_v + gyy_v
    disc = np.sqrt(np.maximum((gxx_v - gyy_v) ** 2 + 4 * gxy_v * gxy_v, 0))
    l1 = 0.5 * (tr + disc)
    l2 = 0.5 * (tr - disc)
    lam = np.where(np.abs(l1) >= np.abs(l2), l1, l2)
    nx = np.where(np.abs(l1) >= np.abs(l2), gxy_v, lam - gyy_v)
    ny = np.where(np.abs(l1) >= np.abs(l2), lam - gxx_v, gxy_v)
    nrm = np.hypot(nx, ny)
    ok = nrm > 1e-09
    if int(ok.sum()) < 8:
        return None
    nx = np.divide(nx, nrm, out=np.zeros_like(nx), where=ok)
    ny = np.divide(ny, nrm, out=np.zeros_like(ny), where=ok)
    d1 = nx * gx_v + ny * gy_v
    d2 = nx * nx * gxx_v + 2 * nx * ny * gxy_v + ny * ny * gyy_v
    denom_ok = np.abs(d2) > 1e-09
    t = np.where(denom_ok, -d1 / np.where(denom_ok, d2, 1), 0)
    keep = ok & denom_ok & (np.abs(t) <= 0.5)
    if int(keep.sum()) < 8:
        return None
    rx = xi[keep].astype(np.float64) + t[keep] * nx[keep]
    ry = yi[keep].astype(np.float64) + t[keep] * ny[keep]
    return rx, ry


def _ridge_center(xs: np.ndarray, ys: np.ndarray, cx0: float, cy0: float):
    """Steger 脊线点分四臂做稳健直线拟合，四线交点即亚像素中心（在质心处取值）。

    返回 (mid_x, mid_y) 或 None（任一臂点数不足/拟合失败）。"""
    dx = xs - cx0
    dy = ys - cy0
    horiz = np.abs(dx) >= np.abs(dy)
    vert = ~horiz
    left = horiz & (dx < 0)
    right = horiz & (dx >= 0)
    top = vert & (dy < 0)
    bottom = vert & (dy >= 0)
    if (left.sum() < 5 and right.sum() < 5 and top.sum() < 5) or bottom.sum() < 5:
        return None
    fit_l = _robust_line_fit(xs[left], ys[left])
    fit_r = _robust_line_fit(xs[right], ys[right])
    fit_t = _robust_line_fit(ys[top], xs[top])   # (斜率以 y 为自变量, 截距) → 直线 x = a*y+b
    fit_b = _robust_line_fit(ys[bottom], xs[bottom])
    if fit_l is None or fit_r is None or fit_t is None or fit_b is None:
        return None
    # 水平臂：y = a*x+b；取左右两臂在 cx0 邻域的交点 y
    y_l = fit_l[0] * cx0 + fit_l[1]
    y_r = fit_r[0] * cx0 + fit_r[1]
    mid_y = 0.5 * (y_l + y_r)
    # 垂直臂：x = a*y+b；取上下两臂在 mid_y 邻域的交点 x
    x_t = fit_t[0] * mid_y + fit_t[1]
    x_b = fit_b[0] * mid_y + fit_b[1]
    mid_x = 0.5 * (x_t + x_b)
    return mid_x, mid_y


def detect_tiled(src, points: np.ndarray, pitch_px: float, p: CrossDetectParams,
                 max_workers: int = 1, progress_cb=None, ref_angle_deg: Optional[float] = None,
                 precheck_gray: Optional[np.ndarray] = None,
                 precheck_scale: float = 1.0, refine_window_scale: float = 0.5,
                 release_precheck_cb=None) -> List[Optional[CrossResult]]:
    """分块主路径十字检测（A1）：两阶段语义与 detect_points_fast 逐点对齐。

    阶段1（预检）：在降采样小图上快速定位候选中心（precheck_scale 同比缩小窗），
    阶段2（精测）：候选映射回整图坐标，在所属 tile 灰度视图内小窗精测
    （_resolve_polarity → _threshold → 连通域筛选 → 投影质心/寻边中线），失败
    扩窗重试后回退整图 ROI 全窗扩窗——与 detect_points_fast 的回退链一致，
    保证检测结果与整图模式逐点一致（±0.05px 内）。

    - src: TilePipeline（duck-typing，需 canvas_w/canvas_h/roi/find_tiles_at/
      load_tile/tile_cols/tile_rows/tiles）；也兼容任何实现 roi() 的分块源。
    - 分块语义：每个点优先在所属 tile 灰度视图内检测（ROI 完全在 tile 内时
      与整图切片共享同一份灰度数组，结果一致）；ROI 越出 tile 边界时自动回
      src.roi() 取整图坐标 ROI（跨 tile 无缝，边界十字不截断、不跨 tile 归属歧义）。
    - max_workers: >1 时阶段2用线程池并行精测（每点独立，无共享可变状态）。
    """
    import concurrent.futures
    n = max(1, len(points))
    base_win = int(pitch_px * p.search_window_scale / 2)

    def detect_one(i):
        px_, py_ = float(points[i, 0]), float(points[i, 1])
        # 阶段1预检：降采样图上粗定位候选
        if precheck_gray is not None and precheck_scale > 1.0:
            sx, sy = px_ / precheck_scale, py_ / precheck_scale
            win_pc = max(3, int(base_win / precheck_scale))
            cand = _detect_one_point(precheck_gray, sx, sy, win_pc, p,
                                     ref_angle_deg=ref_angle_deg)
            if cand is not None:
                px_, py_ = cand.x * precheck_scale, cand.y * precheck_scale
        # 阶段2精测：在所属 tile 内小窗精测
        res = None
        if hasattr(src, 'find_tiles_at'):
            idxs = src.find_tiles_at(px_, py_)
            for idx in idxs:
                tile = src.tiles[idx]
                lx, ly = px_ - tile.ox, py_ - tile.oy
                if not (0 <= lx < tile.w and 0 <= ly < tile.h):
                    continue
                tgray = src.load_tile(idx // src.tile_cols, idx % src.tile_cols)
                if tgray is None:
                    continue
                win_r = max(3, int(pitch_px * refine_window_scale / 2))
                res = _detect_one_point(tgray, lx, ly, win_r, p,
                                        ref_angle_deg=ref_angle_deg)
                if res is None:
                    res = _detect_with_expand(tgray, lx, ly, win_r, p,
                                              ref_angle_deg=ref_angle_deg)
                if res is not None:
                    res.x += tile.ox
                    res.y += tile.oy
                    break
        # 回退：整图 ROI 全窗扩窗
        if res is None:
            roi = src.roi(int(px_) - base_win, int(py_) - base_win,
                          2 * base_win + 1, 2 * base_win + 1) if hasattr(src, 'roi') else None
            if roi is not None:
                res = _detect_one_point(roi, base_win, base_win, base_win, p,
                                        ref_angle_deg=ref_angle_deg)
                if res is None:
                    res = _detect_with_expand(roi, base_win, base_win, base_win, p,
                                              ref_angle_deg=ref_angle_deg)
                if res is not None:
                    res.x += (px_ - base_win)
                    res.y += (py_ - base_win)
            else:
                res = None
        if release_precheck_cb is not None and i == n - 1:
            release_precheck_cb()
        return res

    if max_workers > 1:
        with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as ex:
            futures = [ex.submit(detect_one, i) for i in range(n)]
            results = []
            for k, f in enumerate(futures):
                results.append(f.result())
                if progress_cb:
                    progress_cb((k + 1) / n)
    else:
        results = []
        for i in range(n):
            results.append(detect_one(i))
            if progress_cb:
                progress_cb((i + 1) / n)
    return results
