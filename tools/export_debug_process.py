# -*- coding: utf-8 -*-
"""导出识别过程（彩色：红=寻边点，绿=四臂中线，蓝=交点，黄=质心）

用法：
    python tools/export_debug_process.py IMAGE.jpg --out DIR
    python tools/export_debug_process.py IMAGE.jpg --out DIR --rows 5 --cols 5 --pitch-mm 30
"""
from __future__ import annotations

import argparse
import os
import sys

os.environ.setdefault("OPENCV_IO_MAX_IMAGE_PIXELS", "4000000000")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np

from core.image_loader import ImageSource
from core.calibrator import auto_detect_chessboard
from core.cross_detector import detect_points
from core.config import CrossDetectParams
from core.debug_render import export_debug_process


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("image")
    ap.add_argument("--out", required=True)
    ap.add_argument("--rows", type=int, default=0)
    ap.add_argument("--cols", type=int, default=0)
    ap.add_argument("--pitch-mm", type=float, default=0.0)
    ap.add_argument("--method", default="edge_center", choices=["edge_center", "projection"])
    args = ap.parse_args()

    src = ImageSource(args.image, use_full=True)
    gray = src.original_gray()
    ad = auto_detect_chessboard(gray, pattern="cross")
    if args.rows and args.cols and args.pitch_mm > 0:
        rows, cols = args.rows, args.cols
        pitch_px = args.pitch_mm * 4800.0 / 25.4
        # 用图像中心铺网格
        h, w = gray.shape
        cx, cy = w / 2.0, h / 2.0
        pts = np.array([[cx + (c - (cols - 1) / 2.0) * pitch_px,
                         cy + (r - (rows - 1) / 2.0) * pitch_px]
                        for r in range(rows) for c in range(cols)], np.float64)
    else:
        if not ad.ok or ad.corners is None:
            print("FAIL: 十字阵列自动识别失败：", ad.message)
            return 1
        rows, cols = int(ad.rows), int(ad.cols)
        pitch_px = float(ad.square_px)
        pts = np.asarray(ad.corners, np.float64)
    p = CrossDetectParams(method=args.method)
    results = detect_points(gray, pts, pitch_px, p)
    ok = [r for r in results if r is not None and r.ok]
    print("阵列 %dx%d，检测 %d/%d" % (rows, cols, len(ok), len(results)))
    labels = ["P%d" % (i + 1) for i in range(len(results))]
    out = export_debug_process(gray, results, args.out, labels=labels)
    # 日志
    log_path = os.path.join(args.out, "识别过程.log")
    lines = ["识别过程日志：%s" % os.path.basename(args.image),
             "阵列 %dx%d，方法=%s，检测 %d/%d" % (rows, cols, args.method, len(ok), len(results))]
    n_fb = 0
    for rec in out["rows"]:
        if "centroid_fallback" in str(rec["method"]):
            n_fb += 1
        lines.append("点%d %s: method=%s conf=%.2f 交点=(%.2f, %.2f) 质心差=(%s, %s)px 拟合vs均值=%.2fpx 边沿点=%d%s" % (
            rec["index"], rec["label"], rec["method"], rec["conf"], rec["x"], rec["y"],
            ("%.2f" % rec["centroid_dx"]) if np.isfinite(rec["centroid_dx"]) else "N/A",
            ("%.2f" % rec["centroid_dy"]) if np.isfinite(rec["centroid_dy"]) else "N/A",
            rec["fit_vs_mean_px"] if np.isfinite(rec["fit_vs_mean_px"]) else float("nan"),
            rec["n_arm"], (" [回退均值]" if rec["fallback_mean"] else "")))
    n_method = {}
    for rec in out["rows"]:
        n_method[rec["method"]] = n_method.get(rec["method"], 0) + 1
    lines.append("方法统计：%s" % n_method)
    lines.append("质心兜底点数：%d" % n_fb)
    try:
        with open(log_path, "w", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")
    except Exception:
        pass
    print("导出：%d 张图 → %s" % (out["n"], out["dir"]))
    print("CSV：%s" % out["csv"])
    print("日志：%s" % log_path)
    print("方法统计：%s  回退均值：%d" % (n_method, n_fb))
    return 0


if __name__ == "__main__":
    sys.exit(main())
