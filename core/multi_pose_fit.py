# -*- coding: utf-8 -*-
"""多摆放联合系统级残差拟合（B 路线：玻璃板多次摆放扫描 + 刚体剔除）。

背景：A4 玻璃基底校正板在扫描仪幅面内无法做大角度旋转（15° 即超幅），
只能做小角度旋转（0/5/10°）+ 平移。多次摆放扫描后，每次摆放包含
"摆放刚体（旋转/平移） + 真实系统畸变"。本模块通过 SVD 刚体配准把
每次摆放的刚体剔除，仅保留系统畸变成分，跨摆放合并拟合系统级残差层，
实现与工件批次、摆放无关的可复用校正。

工作流（对应明天的实测数据）：
    poses = [PoseData(name, soft_mm, nominal_mm), ...]   # 每摆放软件输出 + 名义真值
    rc, report = fit_system_residual(poses, order=2, grid_nx=13, grid_ny=13)
    lv = leave_one_out_validation(poses, order=2, grid_nx=13, grid_ny=13)
    save_residual_correction("res_fb_system_*.npz", rc)   # GUI 直接可加载

坐标域：soft_mm 为软件基础校准后的输出物理坐标（软件输出系，即 GUI
_to_mm 的输出系）；nominal_mm 为标定板名义/设计坐标（同点序）。
残差定义 residual = nominal - rigid(soft)，rigid(soft) = soft @ R.T + t。
注意：残差层必须以「软件输出系」为自变量（自变量 = soft，因变量目标 =
soft + residual @ R），与 GUI 加载后 _to_mm 末尾叠加的查询坐标系一致；
若以配准后坐标（nominal 系）为自变量，GUI 用 soft 系坐标查询会错位一个
摆放刚体（几十 mm 级），残差层将完全失效。
"""

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

import numpy as np

from core.calibrator import fit_residual_affine
from core.residual_correction import (
    ResidualCorrection,
    fit_residual_correction,
    fit_residual_grid,
)


@dataclass
class PoseData:
    """单次摆放数据。

    Attributes:
        name: 摆放名（如 "pose0_deg0"）。
        soft_mm: Nx2 软件输出物理坐标（基础校准后，名义系）。
        nominal_mm: Nx2 名义/设计坐标（同点序，标定板真值）。
    """
    name: str
    soft_mm: np.ndarray
    nominal_mm: np.ndarray


def rigid_align(src: np.ndarray, dst: np.ndarray) -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
    """SVD 最小二乘相似变换：aligned = src @ R.T + t ≈ dst。

    Returns:
        R: (2,2) 旋转矩阵；t: (2,) 平移；aligned: (N,2) 配准后坐标。
    """
    src = np.asarray(src, np.float64)
    dst = np.asarray(dst, np.float64)
    if src.shape != dst.shape or len(src) < 2:
        raise ValueError("src and dst must be Nx2 with same length (>=2)")
    cs = src.mean(axis=0)
    cd = dst.mean(axis=0)
    H = (src - cs).T @ (dst - cd)
    # C04 守卫：两点重合/全共线时 SVD 仍有解但无意义（R 可能翻转）。
    # 合法输入不改变结果，仅在退化时抛 ValueError 交给调用方提示。
    _sv = np.linalg.svd(H, compute_uv=False)
    if _sv[0] <= 1e-12 or (_sv[-1] / _sv[0]) < 1e-9:
        raise ValueError("刚体配准退化：点集重合或共线，无法求稳定旋转")
    U, _, Vt = np.linalg.svd(H)
    R = Vt.T @ U.T
    if np.linalg.det(R) < 0:
        Vt[-1, :] *= -1.0
        R = Vt.T @ U.T
    t = cd - R @ cs
    aligned = src @ R.T + t
    return R, t, aligned


def _pose_residual(pose: PoseData) -> Dict[str, object]:
    """单摆放配准并返回残差统计与配准后坐标。"""
    R, t, aligned = rigid_align(pose.soft_mm, pose.nominal_mm)
    res = pose.nominal_mm - aligned
    rms = float(np.sqrt(np.mean(res[:, 0] ** 2 + res[:, 1] ** 2)))
    mx = float(np.max(np.sqrt(res[:, 0] ** 2 + res[:, 1] ** 2)))
    theta = float(np.degrees(np.arctan2(R[1, 0], R[0, 0])))
    # 软件输出系残差：res 在 nominal 系，旋转回软件输出系
    res_soft = res @ R
    return {
        "name": pose.name,
        "n": int(len(pose.soft_mm)),
        "R": R,
        "t": t,
        "theta_deg": theta,
        "tx_mm": float(t[0]),
        "ty_mm": float(t[1]),
        "aligned": aligned,
        "residual": res,
        "res_soft": res_soft,
        "rms_mm": rms,
        "max_mm": mx,
    }


def fit_system_residual(poses: List[PoseData], order: int = 2,
                        grid_nx: Optional[int] = None, grid_ny: Optional[int] = None,
                        source: str = "") -> Tuple[ResidualCorrection, dict]:
    """多摆放联合系统级残差拟合。

    1. 每摆放 SVD 刚体配准 soft->nominal，剔除摆放刚体；
    2. 残差 = nominal - rigid(soft)，并旋转回软件输出系（res_soft = res @ R）；
    3. 以软件输出系坐标 soft 为自变量、soft + res_soft 为目标合并所有摆放点，
       拟合残差层（与 GUI 加载后查询坐标系一致）：
       - grid_nx/grid_ny 给出时用网格双线性（加密网格方案，推荐 13×13）；
       - 否则用多项式（order 默认 2）。
    返回 (rc, report)，report 含每摆放配准统计与拟合统计。
    """
    if len(poses) < 2:
        raise ValueError("至少需要 2 个摆放才能联合拟合（建议 3~6 个）")
    infos = [_pose_residual(p) for p in poses]
    all_soft = np.vstack([p.soft_mm for p in poses])
    all_target = np.vstack([p.soft_mm + info["res_soft"] for p, info in zip(poses, infos)])

    if grid_nx is not None and grid_ny is not None:
        rc = fit_residual_grid(all_soft, all_target,
                               grid_nx=grid_nx, grid_ny=grid_ny,
                               order_poly=order, source=source)
        mode = f"grid{grid_nx}x{grid_ny}+poly{order}"
    else:
        rc = fit_residual_correction(all_soft, all_target,
                                     order=order, source=source)
        mode = f"poly{order}"
    stats = dict(rc.stats or {})
    stats["n_poses"] = len(poses)
    stats["mode"] = mode
    rc.stats = stats
    # D3：系统级残差仿射分解（合并所有摆放的软件输出系残差，识别旋转/剪切/各向缩放系统误差）
    all_res_soft = np.vstack([info["res_soft"] for info in infos])
    affine = fit_residual_affine(all_soft, all_res_soft)
    report = {
        "mode": mode,
        "n_poses": len(poses),
        "n_points": int(len(all_soft)),
        "pose": infos,
        "rms_before_mm": stats.get("rms_before_mm", float("nan")),
        "max_before_mm": stats.get("max_before_mm", float("nan")),
        "rms_after_mm": stats.get("rms_after_mm", float("nan")),
        "max_after_mm": stats.get("max_after_mm", float("nan")),
        "affine": affine,
    }
    return rc, report


def leave_one_out_validation(poses: List[PoseData], order: int = 2,
                             grid_nx: Optional[int] = None,
                             grid_ny: Optional[int] = None) -> dict:
    """留一摆放验证（可复用性硬证据）。

    每次留出一个摆放 j：用其余摆放联合拟合 rc_j，然后在摆放 j 上验证：
        pred = soft_j + rc_j.apply(soft_j)     # 软件输出系修正
        residual_v = nominal_j - rigid(pred)   # 再配准剔除验证摆放刚体
    返回每摆放验证统计 + 全量汇总（rms/max）。
    """
    if len(poses) < 2:
        raise ValueError("至少需要 2 个摆放才能做留一验证（建议 3 个以上）")
    rows = []
    all_rms, all_max = [], []
    for j in range(len(poses)):
        train = [p for i, p in enumerate(poses) if i != j]
        rc_j, _ = fit_system_residual(train, order=order,
                                      grid_nx=grid_nx, grid_ny=grid_ny,
                                      source=f"LOO train minus {poses[j].name}")
        soft = poses[j].soft_mm
        pred = soft + rc_j.apply_array(soft)
        _, _, aligned = rigid_align(pred, poses[j].nominal_mm)
        rv = poses[j].nominal_mm - aligned
        rms = float(np.sqrt(np.mean(rv[:, 0] ** 2 + rv[:, 1] ** 2)))
        mx = float(np.max(np.sqrt(rv[:, 0] ** 2 + rv[:, 1] ** 2)))
        rows.append({
            "held_out": poses[j].name,
            "train_poses": [p.name for i, p in enumerate(poses) if i != j],
            "rms_mm": rms,
            "max_mm": mx,
        })
        all_rms.append(rms)
        all_max.append(mx)
    return {
        "rows": rows,
        "rms_mean_mm": float(np.mean(all_rms)),
        "rms_worst_mm": float(np.max(all_rms)),
        "max_worst_mm": float(np.max(all_max)),
        "pass_max_015": bool(np.max(all_max) <= 0.15),
    }


def format_report(report: dict, loo: Optional[dict] = None) -> str:
    """将拟合报告格式化为易读文本（用于控制台/报告文件）。"""
    lines = []
    lines.append(f"=== 多摆放系统级残差拟合报告 ===")
    lines.append(f"模式: {report.get('mode')} | 摆放数: {report.get('n_poses')} | 总点数: {report.get('n_points')}")
    lines.append(f"配准前(含刚体) RMS={report.get('rms_before_mm', float('nan')):.4f}mm max={report.get('max_before_mm', float('nan')):.4f}mm")
    lines.append(f"拟合后(系统级) RMS={report.get('rms_after_mm', float('nan')):.4f}mm max={report.get('max_after_mm', float('nan')):.4f}mm")
    lines.append("")
    lines.append("各摆放配准刚体:")
    for p in report.get("pose", []):
        lines.append(f"  {p['name']:>16s}: θ={p['theta_deg']:+.3f}° t=({p['tx_mm']:+.3f},{p['ty_mm']:+.3f})mm "
                     f"配准残差 RMS={p['rms_mm']:.4f}mm max={p['max_mm']:.4f}mm")
    aff = report.get("affine")
    if aff:
        lines.append("")
        lines.append("D3 系统级残差修正量仿射分解(补偿方向; 旋转已由刚体配准吸收):")
        lines.append(f"  平移      : X {aff['tx_mm']:+.4f}mm  Y {aff['ty_mm']:+.4f}mm")
        lines.append(f"  旋转角    : {aff['rot_deg']:+.4f}°")
        lines.append(f"  剪切项    : {aff['shear']:+.6f} (0=无剪切)")
        lines.append(f"  主轴比例  : X {aff['scale_x']:.6f}  Y {aff['scale_y']:.6f} (1=无缩放)")
        lines.append(f"  解释后剩余: RMS {aff['residual_rms_mm']:.4f}mm")
        flag = "达标" if aff["residual_rms_mm"] <= 0.15 else "超标"
        lines.append(f"  旋转/剪切吸收判定: {flag} (指标 RMS≤0.15mm)")
    if loo:
        lines.append("")
        lines.append("留一摆放验证(可复用性):")
        for r in loo.get("rows", []):
            lines.append(f"  留出 {r['held_out']:>16s}: 验证 RMS={r['rms_mm']:.4f}mm max={r['max_mm']:.4f}mm")
        lines.append(f"  汇总: RMS均值={loo.get('rms_mean_mm', float('nan')):.4f}mm "
                     f"最差RMS={loo.get('rms_worst_mm', float('nan')):.4f}mm "
                     f"最差max={loo.get('max_worst_mm', float('nan')):.4f}mm")
        lines.append(f"  max≤0.15mm 达标: {'✅' if loo.get('pass_max_015') else '❌'}")
    return "\n".join(lines)
