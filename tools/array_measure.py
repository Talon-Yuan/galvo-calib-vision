# -*- coding: utf-8 -*-
"""十字阵列测量工具（v1，2026-11-10）

对一张 4800dpi 扫描图，自动识别十字阵列（5x5/6x6 等），精测每个十字中心，
输出：
  - 行列数、检测点数
  - 行步/列步（mm）、阵列旋转角（deg）
  - 对"最佳拟合规整晶格"的残差 RMS/Max（mm）——即阵列自身不规整度
  - 逐点像素坐标（可选 --json）

用法：
    python tools/array_measure.py IMAGE.jpg
    python tools/array_measure.py IMAGE.jpg --json out.json
    python tools/array_measure.py IMAGE.jpg --pitch-mm 30.0

注意：
  - 用于评估"阵列纸/样件本身"的几何质量，不依赖也不修改标定；
  - 若自动识别失败（旋转过大/干扰），会明确报错，不返回假数据（红线 2）。
"""
from __future__ import annotations

import argparse
import json
import math
import os
import sys

os.environ.setdefault("OPENCV_IO_MAX_IMAGE_PIXELS", "4000000000")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np

from core.image_loader import ImageSource
from core.calibrator import auto_detect_chessboard
from core.cross_detector import detect_points
from core.config import CrossDetectParams


def _cluster_k(v: np.ndarray, k: int) -> np.ndarray:
    """1D 聚类成 k 档：取 k-1 个最大间隙作分界。"""
    v = np.asarray(v, float)
    s = np.sort(v)
    if len(s) <= k:
        return np.arange(len(v))
    gaps = np.diff(s)
    cut = np.sort(np.argsort(gaps)[-(k - 1):])
    bounds = (s[cut] + s[cut + 1]) / 2.0
    return np.searchsorted(bounds, v)


def measure_array(image_path: str, pitch_mm: float | None = None,
                  params: CrossDetectParams | None = None) -> dict:
    """测量一张十字阵列图，返回 dict；识别/检测失败抛 RuntimeError。"""
    src = ImageSource(image_path, use_full=True)
    gray = src.original_gray()
    ad = auto_detect_chessboard(gray, pattern="cross")
    if not ad.ok or ad.corners is None or len(ad.corners) < 4:
        raise RuntimeError(f"十字阵列自动识别失败：{ad.message}")
    seeds = np.asarray(ad.corners, float)
    pitch_px = float(ad.square_px)
    if pitch_mm and pitch_mm > 0:
        pitch_px = float(pitch_mm) * 4800.0 / 25.4
    rr = detect_points(gray, seeds, pitch_px, params or CrossDetectParams())
    det = np.array([[r.x, r.y] for r in rr if r is not None and r.ok], float)
    if len(det) != len(seeds):
        raise RuntimeError(f"十字检测不完整：{len(det)}/{len(seeds)}（不返回不完整结果）")
    # 结果顺序可能被 rematch 打乱：按最近邻对回自动中心
    si = ((det[:, None, :] - seeds[None, :, :]) ** 2).sum(2).argmin(1)
    if len(set(si.tolist())) != len(si):
        raise RuntimeError("十字检测结果对回自动中心不唯一，拒绝输出")
    rows, cols = int(ad.rows), int(ad.cols)
    ri = _cluster_k(seeds[:, 1], rows)
    ci = _cluster_k(seeds[:, 0], cols)
    key = np.lexsort((ci[si], ri[si]))
    pts = det[key]
    idx = np.stack([ri[si][key], ci[si][key]], 1).astype(float)
    X = np.hstack([idx, np.ones((len(pts), 1))])
    coef, *_ = np.linalg.lstsq(X, pts, rcond=None)
    e = np.linalg.norm(pts - X @ coef, axis=1)
    px2mm = 25.4 / 4800.0
    vr, vc = coef[0], coef[1]
    return {
        "image": os.path.basename(image_path),
        "rows": rows,
        "cols": cols,
        "n_points": int(len(pts)),
        "row_step_mm": round(float(np.linalg.norm(vr) * px2mm), 4),
        "col_step_mm": round(float(np.linalg.norm(vc) * px2mm), 4),
        "angle_deg": round(float(math.degrees(math.atan2(vc[1], vc[0]))), 3),
        "residual_rms_mm": round(float(np.sqrt((e ** 2).mean()) * px2mm), 4),
        "residual_max_mm": round(float(e.max() * px2mm), 4),
        "points_px": np.round(pts, 2).tolist(),
    }


def main() -> int:
    ap = argparse.ArgumentParser(description="十字阵列测量：间距/旋转/阵列自身残差")
    ap.add_argument("image", help="扫描图路径")
    ap.add_argument("--json", default="", help="输出 JSON 路径")
    ap.add_argument("--pitch-mm", type=float, default=None, help="可选：标称间距(mm)覆盖自动估计")
    args = ap.parse_args()
    try:
        r = measure_array(args.image, args.pitch_mm)
    except Exception as e:  # noqa: BLE001
        print(f"FAIL: {e}")
        return 1
    print(f"{r['image']}: {r['rows']}x{r['cols']}  n={r['n_points']}")
    print(f"  行步={r['row_step_mm']}mm 列步={r['col_step_mm']}mm 旋转={r['angle_deg']}deg")
    print(f"  阵列残差 RMS={r['residual_rms_mm']}mm Max={r['residual_max_mm']}mm")
    if args.json:
        with open(args.json, "w", encoding="utf-8") as f:
            json.dump(r, f, ensure_ascii=False, indent=2)
        print("saved:", args.json)
    return 0


if __name__ == "__main__":
    sys.exit(main())
