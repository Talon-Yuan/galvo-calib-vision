# -*- coding: utf-8 -*-
"""标准坐标系补全：内角点自动建系（转正）与方向融合。

对应项目书 v1.10 模块2/3：
1. 由整板内角点像素坐标最小二乘拟合仿射网格基（origin + 列向量 a + 行向量 b），
   吸收旋转、剪切、非均匀缩放（平面单应 4DOF 的仿射近似，参考 OpenCV homography
   与张正友平面标定思路）；
2. 由基向量给出 x/y 方向单位向量与实测间距（实测间距用于建网格，名义值保持设计标称）；
3. 0°/90° 歧义用「走向点选」权威方向消歧（方向约束自适应：后续十字检测的臂方向
   基准即为建系得到的方向，不再写死图像轴）；
4. 提供刚性+剪切显式分解（旋转角 θ、剪切角 γ），供旋转/剪切残差分析。
"""
from dataclasses import dataclass
from typing import List, Optional, Tuple

import numpy as np

from .grid_definition import GridDefinition


@dataclass
class FrameFit:
    """仿射网格拟合结果（像素系）。"""
    ok: bool
    origin: np.ndarray          # (2,) 网格原点（行0列0）
    a: np.ndarray               # (2,) 列方向向量（列索引 +1 的像素位移）
    b: np.ndarray               # (2,) 行方向向量（行索引 +1 的像素位移）
    pitch_x_px: float = 0.0     # 实测列间距(px)
    pitch_y_px: float = 0.0     # 实测行间距(px)
    resid_rms_px: float = 0.0   # 内角点拟合残差 RMS(px)
    rows: int = 0
    cols: int = 0
    message: str = ""


def fit_affine_grid(corners: np.ndarray, rows: int, cols: int) -> FrameFit:
    """对整板内角点（行主序 Nx2）最小二乘拟合仿射网格基。

    模型 p_rc = origin + c*a + r*b（c 列索引 / r 行索引）。
    对 X、Y 分别用最小二乘解 [origin_x, a_x, b_x] = A \\ px，吸收旋转+剪切+非均匀缩放。
    失败（点数不足/退化）返回 ok=False。
    """
    pts = np.asarray(corners, np.float64)
    n = rows * cols
    if pts.shape != (n, 2):
        return FrameFit(False, np.zeros(2), np.zeros(2), np.zeros(2),
                        message=f"角点数 {pts.shape[0]} 与行列 {rows}x{cols} 不匹配")
    if n < 4:
        return FrameFit(False, np.zeros(2), np.zeros(2), np.zeros(2),
                        message="内角点不足 4 个，无法建系")
    # 设计矩阵 A = [1, c, r]
    cc = np.tile(np.arange(cols, dtype=np.float64), rows)
    rr = np.repeat(np.arange(rows, dtype=np.float64), cols)
    A = np.stack([np.ones(n), cc, rr], axis=1)
    try:
        sol_x, *_ = np.linalg.lstsq(A, pts[:, 0], rcond=None)
        sol_y, *_ = np.linalg.lstsq(A, pts[:, 1], rcond=None)
    except np.linalg.LinAlgError:
        return FrameFit(False, np.zeros(2), np.zeros(2), np.zeros(2), message="仿射拟合奇异")
    ox, ax, bx = sol_x
    oy, ay, by = sol_y
    origin = np.array([ox, oy], np.float64)
    a = np.array([ax, ay], np.float64)
    b = np.array([bx, by], np.float64)
    pred = A @ np.stack([sol_x, sol_y], axis=1)
    resid = np.linalg.norm(pts - pred, axis=1)
    return FrameFit(True, origin, a, b,
                    pitch_x_px=float(np.linalg.norm(a)),
                    pitch_y_px=float(np.linalg.norm(b)),
                    resid_rms_px=float(np.sqrt(np.mean(resid ** 2))),
                    rows=rows, cols=cols,
                    message=f"内角点仿射拟合残差 RMS={np.sqrt(np.mean(resid ** 2)):.3f}px")


def frame_direction_candidates(a: np.ndarray, b: np.ndarray) -> List[Tuple[np.ndarray, np.ndarray]]:
    """由仿射基生成两个无向候选 (x_dir, y_dir)：θ 与 θ+90°。

    自动建系存在 90° 走向歧义（内角点网格无法区分行列谁先谁后），
    返回两个候选供「走向点选」消歧。每个候选的方向已归一化。
    """
    na = np.linalg.norm(a)
    nb = np.linalg.norm(b)
    if na < 1e-9 or nb < 1e-9:
        return [(np.array([1.0, 0.0]), np.array([0.0, 1.0]))]
    ua = a / na
    ub = b / nb
    # 候选1：列方向为 X；候选2：行方向为 X（90° 旋转，Y 取 -列方向保持右手系视觉习惯）
    c1 = (ua, ub)
    c2 = (ub, -ua)
    return [c1, c2]


def pick_disambiguate(pick_vec: Optional[np.ndarray],
                      candidates: List[Tuple[np.ndarray, np.ndarray]]) -> Tuple[np.ndarray, np.ndarray]:
    """用点选方向消歧 90° 走向。

    pick_vec 为点选 X 方向（像素向量，未归一化亦可）。若未提供，默认取候选1。
    选取规则：pick_vec 与候选 x_dir 夹角余弦绝对值最大者；两候选角度相同（退化）取候选1。
    """
    if pick_vec is None or len(candidates) <= 1:
        return candidates[0]
    pv = np.asarray(pick_vec, np.float64)
    nv = np.linalg.norm(pv)
    if nv < 1e-9:
        return candidates[0]
    pu = pv / nv
    best = 0
    best_sim = -1.0
    for i, (xdir, _ydir) in enumerate(candidates):
        sim = abs(float(xdir @ pu))
        if sim > best_sim:
            best_sim = sim
            best = i
    return candidates[best]


def validate_frame_vs_pick(fit: FrameFit, pick_vec: Optional[np.ndarray],
                           tol_deg: float = 1.0) -> Tuple[Tuple[np.ndarray, np.ndarray], str]:
    """内角点建系与手动走向的一致性校验（D4 融合）。

    返回 (选定候选 (x_dir, y_dir), 校验消息)。
    - pick_vec 为 None：无手动走向，直接取候选1，消息提示"仅内角点建系（默认走向）"；
    - 有手动走向：与两个 90° 候选的 x_dir 最小夹角若 > tol_deg，产生告警
      （以手动走向为准，用户显式意图优先）；否则提示一致。
    """
    candidates = frame_direction_candidates(fit.a, fit.b)
    if pick_vec is None:
        return candidates[0], "仅内角点建系（无手动走向，采用默认列向为 X 的候选）"
    pv = np.asarray(pick_vec, np.float64)
    nv = np.linalg.norm(pv)
    if nv < 1e-9:
        return candidates[0], "手动走向为零向量，采用默认候选（列向为 X）"
    pu = pv / nv
    best = 0
    best_sim = -1.0
    for i, (xdir, _y) in enumerate(candidates):
        sim = abs(float(xdir @ pu))
        if sim > best_sim:
            best_sim = sim
            best = i
    # 夹角（取 0~90° 语义）：sim 为余弦绝对值，夹角 = acos(sim)
    ang = float(np.degrees(np.arccos(np.clip(best_sim, -1.0, 1.0))))
    cand = candidates[best]
    if ang > tol_deg:
        warn = (f"内角点建系方向与手动点选偏差 {ang:.2f}°（> {tol_deg}° 阈值），"
                f"以手动走向为准；如需以内角点为准请重新点选或清除手动走向后重试")
    else:
        warn = f"内角点建系与手动走向一致（夹角 {ang:.2f}° ≤ {tol_deg}°）"
    return cand, warn


def build_grid_from_frame(fit: FrameFit, rows: int, cols: int,
                          dpi: float, nominal_pitch_mm: float,
                          pick_vec: Optional[np.ndarray] = None,
                          measured_pitch_mm: Optional[float] = None) -> GridDefinition:
    """由仿射拟合 + 点选消歧构建 GridDefinition。

    - origin：内角点网格原点（行0列0）；
    - x/y 方向：消歧后的列/行方向单位向量；
    - measured_pitch_mm：实测间距（未提供时由仿射基像素间距换算，均值）；
    - nominal_pitch_mm：设计标称间距（名义值表专用，不参与建网格）。
    红线：名义表恒用 nominal_pitch_mm，建网格用 measured_pitch_mm。
    """
    if not fit.ok:
        raise ValueError(fit.message)
    xdir, ydir = pick_disambiguate(pick_vec, frame_direction_candidates(fit.a, fit.b))
    mp = measured_pitch_mm
    if not mp or mp <= 0:
        mp = float((fit.pitch_x_px + fit.pitch_y_px) / 2.0) * 25.4 / max(1e-6, dpi)
    return GridDefinition(rows=rows, cols=cols,
                          pitch_mm=nominal_pitch_mm,
                          dpi=dpi,
                          origin_px=(float(fit.origin[0]), float(fit.origin[1])),
                          x_dir_px=(float(xdir[0]), float(xdir[1])),
                          y_dir_px=(float(ydir[0]), float(ydir[1])),
                          measured_pitch_mm=float(mp))


@dataclass
class ManualVsAutoReport:
    """手动基准 vs 自动内角点拟合的差异报告（供 UI 弹窗/状态栏展示）。"""
    ok: bool
    manual_origin_px: Tuple[float, float] = (0.0, 0.0)
    auto_origin_px: Tuple[float, float] = (0.0, 0.0)
    origin_delta_mm: float = 0.0      # 原点偏移(mm，按 dpi 换算)
    angle_delta_deg: float = 0.0      # 方向夹角(°, 0~90° 语义，与手动 X 轴最小夹角)
    pitch_auto_mm: float = 0.0        # 自动实测间距(mm，行列均值)
    pitch_nominal_mm: float = 0.0     # 名义间距(mm)
    pitch_delta_pct: float = 0.0      # 间距偏差百分比
    fit_rms_px: float = 0.0           # 自动拟合内角点残差 RMS(px)
    n_inliers: int = 0                # 参与拟合的内角点数
    message: str = ""


def compare_manual_vs_auto(fit: FrameFit, manual_grid: GridDefinition,
                           dpi: float) -> ManualVsAutoReport:
    """对比手动三点基准与自动内角点拟合，产出差异报告。

    - 方向夹角取 0~90° 语义：手动 X 轴单位向量与两个 90° 候选 x_dir
      的夹角余弦绝对值最大者反推（歧义取最近候选，不因 90° 翻转虚警）；
    - origin_delta_mm = |手动起点 - 自动原点| * 25.4 / dpi；
    - pitch_delta_pct 相对手动名义间距计算。
    """
    if not fit.ok or manual_grid is None:
        return ManualVsAutoReport(False, message="数据不足，无法对比")
    m_origin = np.asarray(manual_grid.origin_px, np.float64)
    a_origin = np.asarray(fit.origin, np.float64)
    origin_delta_mm = float(np.linalg.norm(m_origin - a_origin) * 25.4 / max(1e-6, dpi))
    mx = np.asarray(manual_grid.x_dir_px, np.float64)
    n_mx = np.linalg.norm(mx)
    if n_mx < 1e-9:
        return ManualVsAutoReport(False, message="手动 X 方向未定义，无法对比")
    mxu = mx / n_mx
    best_sim = -1.0
    for xdir, _ydir in frame_direction_candidates(fit.a, fit.b):
        sim = abs(float(xdir @ mxu))
        if sim > best_sim:
            best_sim = sim
    angle_delta_deg = float(np.degrees(np.arccos(np.clip(best_sim, -1.0, 1.0))))
    pitch_auto_mm = float((fit.pitch_x_px + fit.pitch_y_px) / 2.0) * 25.4 / max(1e-6, dpi)
    pitch_nominal_mm = float(manual_grid.pitch_mm)
    pitch_delta_pct = 0.0 if pitch_nominal_mm <= 0 else \
        float(abs(pitch_auto_mm - pitch_nominal_mm) / pitch_nominal_mm * 100.0)
    msg = (f"原点偏移 {origin_delta_mm:.3f}mm，方向夹角 {angle_delta_deg:.2f}°，"
           f"间距偏差 {pitch_delta_pct:.1f}%（自动实测 {pitch_auto_mm:.3f}mm vs 名义 {pitch_nominal_mm:.3f}mm），"
           f"拟合残差 {fit.resid_rms_px:.3f}px（{fit.rows}×{fit.cols}={fit.rows * fit.cols} 点）")
    return ManualVsAutoReport(True,
                              manual_origin_px=(float(m_origin[0]), float(m_origin[1])),
                              auto_origin_px=(float(a_origin[0]), float(a_origin[1])),
                              origin_delta_mm=origin_delta_mm,
                              angle_delta_deg=angle_delta_deg,
                              pitch_auto_mm=pitch_auto_mm,
                              pitch_nominal_mm=pitch_nominal_mm,
                              pitch_delta_pct=pitch_delta_pct,
                              fit_rms_px=float(fit.resid_rms_px),
                              n_inliers=fit.rows * fit.cols,
                              message=msg)


def build_grid_manual_first(fit: FrameFit, manual_grid: GridDefinition,
                            rows: int, cols: int, dpi: float,
                            nominal_pitch_mm: float,
                            use_manual: bool = True) -> GridDefinition:
    """手动为基准建系（方案 A 核心：G1/G2/G5）。

    - use_manual=True 且 manual_grid 有效：origin/x_dir/y_dir 克隆手动网格
      （GridDefinition.__post_init__ 自动归一方向轴），rows/cols 取自动识别值，
      measured_pitch_mm 取自动拟合实测间距（自动补参）；
    - 方向轴需精化时，调用方先 manual_grid.rotate_dirs(-theta) 再传入；
    - use_manual=False 或无 manual_grid：回退全自动 build_grid_from_frame。
    红线：nominal_pitch_mm 恒用设计值（名义表专用），实测间距只进 measured_pitch_mm。
    """
    if use_manual and manual_grid is not None:
        g = GridDefinition(rows=rows, cols=cols,
                           pitch_mm=nominal_pitch_mm,
                           dpi=dpi,
                           origin_px=manual_grid.origin_px,
                           x_dir_px=manual_grid.x_dir_px,
                           y_dir_px=manual_grid.y_dir_px)
        mp = float((fit.pitch_x_px + fit.pitch_y_px) / 2.0) * 25.4 / max(1e-6, dpi)
        g.measured_pitch_mm = float(mp)
        return g
    return build_grid_from_frame(fit, rows, cols, dpi, nominal_pitch_mm)


@dataclass
class RigidShearResult:
    """刚性+剪切显式分解结果（残差分析用，单位 mm）。"""
    ok: bool
    ang_deg: float = 0.0        # 残余旋转角 θ（名义系相对实测系的旋转，deg）
    shear_gamma_deg: float = 0.0  # 剪切角 γ（deg）
    scale_x: float = 1.0        # X 方向缩放
    scale_y: float = 1.0        # Y 方向缩放
    rms_before: float = 0.0     # 原始偏差 RMS
    rms_after: float = 0.0      # 仿射校正后 RMS
    n_points: int = 0
    message: str = ""


def fit_rigid_shear(meas_mm: np.ndarray, nominal_mm: np.ndarray) -> RigidShearResult:
    """实测→名义 的刚性+剪切分解：n = M·m + t，线性部分 M 做 QR 分解 M=R·S。

    - 物理模型：M = R(θ)·S，R 为旋转（吸收摆放/系统残留旋转），
      S=[[sx, sh],[0, sy]] 上三角（缩放 + X 向剪切）；
    - 用 2x2 QR 分解直接求解：Q 即旋转 R(θ)，R 上三角即 S（剪切项在 R[0,1]）；
    - 符号规范化：约定 sx,sy>0（负对角时翻转列）；
    - 旋转角 θ = atan2(Q[1,0], Q[0,0])；剪切角 γ = atan(sh / sy)。
    返回校正前/后 RMS，供判断是否需要吸收进残差层或重建系。
    """
    m = np.asarray(meas_mm, np.float64)
    n = np.asarray(nominal_mm, np.float64)
    if m.ndim != 2 or m.shape != n.shape or len(m) < 3:
        return RigidShearResult(False, message="点数不足或形状不匹配")
    rms0 = float(np.sqrt(np.mean(np.sum((n - m) ** 2, axis=1))))
    A = np.stack([m[:, 0], m[:, 1], np.ones(len(m))], axis=1)
    try:
        sol_x, *_ = np.linalg.lstsq(A, n[:, 0], rcond=None)
        sol_y, *_ = np.linalg.lstsq(A, n[:, 1], rcond=None)
    except np.linalg.LinAlgError:
        return RigidShearResult(False, message="仿射拟合奇异")
    M = np.stack([sol_x[:2], sol_y[:2]], axis=0)  # 2x2 线性部分（axis=0：行=坐标分量）
    t = np.array([sol_x[2], sol_y[2]])
    pred = A @ np.stack([sol_x, sol_y], axis=1)
    rms1 = float(np.sqrt(np.mean(np.sum((n - pred) ** 2, axis=1))))
    # QR 分解：M = Q·S（Q 旋转/反射，S 上三角）
    Q, S = np.linalg.qr(M)
    # 反射修正：det(Q)<0 时翻转第一列使 Q 成为纯旋转（避免角度跳变 ±180°）
    if np.linalg.det(Q) < 0:
        Q[:, 0] = -Q[:, 0]
        S[0, :] = -S[0, :]
    # 符号规范化：sx, sy > 0
    if S[0, 0] < 0:
        Q[:, 0] = -Q[:, 0]
        S[0, :] = -S[0, :]
    if S[1, 1] < 0:
        Q[:, 1] = -Q[:, 1]
        S[1, :] = -S[1, :]
    ang = float(np.degrees(np.arctan2(Q[1, 0], Q[0, 0])))
    sx = float(S[0, 0])
    sy = float(S[1, 1])
    sh = float(S[0, 1])
    gamma = float(np.degrees(np.arctan2(sh, sy)))
    return RigidShearResult(True, ang_deg=ang, shear_gamma_deg=gamma,
                            scale_x=sx, scale_y=sy,
                            rms_before=rms0, rms_after=rms1, n_points=len(m),
                            message=f"旋转 {ang:+.4f}° 剪切 {gamma:+.4f}° 缩放({sx:.6f},{sy:.6f})")


def analyze_residual_rotation_shear(meas_mm: np.ndarray, nominal_mm: np.ndarray) -> str:
    """残差旋转/剪切分析报告文本（供检测完成后提示与导出）。"""
    r = fit_rigid_shear(meas_mm, nominal_mm)
    if not r.ok:
        return f"残差分析失败：{r.message}"
    lines = [
        f"旋转/剪切残差分析（{r.n_points} 点）：",
        f"- 残余旋转角 θ = {r.ang_deg:+.4f}°",
        f"- 剪切角 γ = {r.shear_gamma_deg:+.4f}°",
        f"- 缩放系数 = ({r.scale_x:.6f}, {r.scale_y:.6f})",
        f"- 校正前偏差 RMS = {r.rms_before:.4f} mm，仿射校正后 RMS = {r.rms_after:.4f} mm",
    ]
    if abs(r.ang_deg) > 0.05 or abs(r.shear_gamma_deg) > 0.05:
        lines.append("提示：存在不可忽略的残余旋转/剪切，建议加载二次元基准残差校正（res_fb_*）或重建系转正。")
    else:
        lines.append("结论：残余旋转/剪切在可忽略范围。")
    return "\n".join(lines)
