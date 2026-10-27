# -*- coding: utf-8 -*-
"""测量图片比对结果保存：逐点裁剪原图 + 十字标注 + 识别过程记录。

命名 p1.png / p2.png ...（与结果表序号一一对应）；未检出点跳过图片，
但在汇总记录 识别过程记录.csv 中标注"未检出"。识别窗口默认取
自动检测窗口与手动精化窗口的较大者，保证手动点击偏差也能完整呈现。
"""
import csv
import math
import os
from typing import Callable, List, Optional, Tuple

import cv2
import numpy as np

from .cross_detector import CrossResult
from .image_loader import ImageSource
from .point_order import display_no

# 单张比对图最长边上限（像素）。4800dpi 下识别窗口半径约 3.4k px 已够看全，
# 再大仅增加保存耗时与内存，故封顶，中心十字与编号不受影响。
MAX_SAVE_EDGE = 5000
ROI_PAD = 80  # 十字外的额外边距


def _refine_win_for(grid) -> int:
    """手动吸附精化窗口（与 main_window 自适应逻辑一致）：0.25×间距，clamp[81,2400]。
    4800dpi、间距 24.5mm 时约 1158px（≈6.1mm），足以覆盖手动点击容差。"""
    if grid is None:
        return 81
    return max(81, min(2400, int(round(grid.pitch_px * 0.25))))


def _method_label(r: CrossResult, method_name: str) -> str:
    if r.method == "manual":
        return "MANUAL-REFINED" if r.refined else "MANUAL-CLICK"
    return "AUTO-EDGE" if method_name == "寻边中线交点" else "AUTO-PROJ"


def _draw_annotations(img_bgr: np.ndarray, cx: float, cy: float, r: CrossResult,
                      mm_x: float, mm_y: float, dx, dy, dev, verdict, n: int,
                      method_name: str) -> np.ndarray:
    """在比对图上画十字 + 编号/坐标/判定标注。img_bgr 为 ROI 内图像（BGR）。"""
    h, w = img_bgr.shape[:2]
    color = (0, 165, 255) if r.method == "manual" else (0, 255, 0)  # 橙=手动 / 绿=自动
    size = max(24, int(min(w, h) * 0.03))
    thickness = max(3, int(min(w, h) * 0.004))
    lw = 2
    cv2.line(img_bgr, (int(round(cx - size)), int(round(cy))), (int(round(cx + size)), int(round(cy))), color, thickness, cv2.LINE_AA)
    cv2.line(img_bgr, (int(round(cx)), int(round(cy - size))), (int(round(cx)), int(round(cy + size))), color, thickness, cv2.LINE_AA)
    # 半透明黑底条（信息可读性）
    bar_h = 150 if dev is not None else 110
    overlay = img_bgr.copy()
    cv2.rectangle(overlay, (0, 0), (min(w, 980), bar_h), (0, 0, 0), -1)
    cv2.addWeighted(overlay, 0.55, img_bgr, 0.45, 0, img_bgr)
    font = cv2.FONT_HERSHEY_SIMPLEX
    scale = 0.9
    lines = [f"P{n}  {_method_label(r, method_name)}"]
    if dev is not None:
        lines.append(f"px=({r.x:.1f}, {r.y:.1f})  mm=({mm_x:.3f}, {mm_y:.3f})")
        lines.append(f"dev={dev:.3f}mm  {verdict}  (dx={dx:.3f}, dy={dy:.3f})")
    else:
        lines.append(f"px=({r.x:.1f}, {r.y:.1f})  mm=({mm_x:.3f}, {mm_y:.3f})")
    for k, txt in enumerate(lines):
        cv2.putText(img_bgr, txt, (12, 34 + k * 38), font, scale, (255, 255, 255), 2, cv2.LINE_AA)
        cv2.putText(img_bgr, txt, (12, 34 + k * 38), font, scale, (0, 0, 0), 1, cv2.LINE_AA)
    return img_bgr


def _row_metrics(i: int, r: CrossResult, nominal: List[Tuple[float, float]],
                 center: Optional[Tuple[float, float]], grid, coeff_x, coeff_y, order,
                 dpi_value: float, tol_upper: float, tol_lower: float,
                 piecewise=None, res_fb=None):
    """与导出 Excel 一致的点级指标：mm 归零、名义值、偏差、判定。

    res_fb：外部基准残差校正层（二次元实测拟合），叠加语义与 GUI _to_mm
    一致——基础换算后、中心归零前叠加残差增量（修正后坐标 = 基础 + 残差）。
    """
    if nominal and i < len(nominal):
        tx, ty = nominal[i]
    else:
        tx = ty = None
    mm = 25.4 / (grid.dpi if grid else dpi_value)
    mm_x, mm_y = r.x * mm, r.y * mm
    if piecewise is not None:
        mm_x, mm_y = piecewise.apply(r.x, r.y)
    elif coeff_x is not None:
        from .calibrator import apply_correction
        mm_x, mm_y = apply_correction(r.x, r.y, coeff_x, coeff_y, order)
    if res_fb is not None:
        dx_fb, dy_fb = res_fb.apply(mm_x, mm_y)
        mm_x, mm_y = mm_x + dx_fb, mm_y + dy_fb
    if center:
        mm_x, mm_y = mm_x - center[0], mm_y - center[1]
    if grid is not None:
        mm_x, mm_y = grid.to_phys(mm_x, mm_y)
    if tx is not None:
        dx, dy = mm_x - tx, mm_y - ty
        dev = math.hypot(dx, dy)
        ok = (tol_lower <= mm_x - tx <= tol_upper) and (tol_lower <= mm_y - ty <= tol_upper)
        verdict = "Pass" if ok else "NG"
    else:
        dx = dy = dev = None
        verdict = ""
    return mm_x, mm_y, tx, ty, dx, dy, dev, verdict


def save_all(out_dir: str, src: ImageSource, results: List[Optional[CrossResult]],
             grid, sp_win: float, nominal: List[Tuple[float, float]],
             center: Optional[Tuple[float, float]], coeff_x, coeff_y, order,
             dpi_value: float, tol_upper: float, tol_lower: float,
             method_name: str = "投影质心", callback: Optional[Callable[[int, int, str], None]] = None,
             piecewise=None, res_fb=None, order_mode: str = "top_left_row_major",
             lang: str = "zh") -> Tuple[int, int, str]:
    """保存全部检测点的比对图与识别过程记录。

    返回 (已保存图片数, 未检出点数, 记录文件路径)。未检出点不生成图片。
    """
    zh = lang != "en"
    os.makedirs(out_dir, exist_ok=True)
    gray = src.original_gray()
    h_img, w_img = gray.shape[:2]

    auto_r = (grid.pitch_px * sp_win / 2.0) if grid else 300.0
    manual_half = _refine_win_for(grid) // 2
    radius = min(int(max(auto_r, manual_half) + ROI_PAD), MAX_SAVE_EDGE // 2)

    rows: List[dict] = []
    saved = 0
    missing = 0
    total = len(results)

    for i, r in enumerate(results):
        n = display_no(i, grid.rows, grid.cols, order_mode) if grid else i + 1
        if r is None or not r.ok:
            missing += 1
            # F 模块：失败点保留行列位置排入输出表。名义值照常锁定输出（禁反推），
            # 实测坐标留 "--"（不写伪造值），状态标 DETECT_FAIL，误差列留空。
            # 方案D：低置信点（ok=False, fail_reason=LOW_CONF）同样标 DETECT_FAIL，
            # 不生成比对图，备注注明低置信原因，杜绝弱十字被静默放行成 OK。
            tx, ty = (nominal[i] if nominal and i < len(nominal) else (None, None))
            note = "低置信(LOW_CONF)" if (r is not None and r.fail_reason == "LOW_CONF") else ""
            rows.append({"row": i // grid.cols + 1 if grid else "", "col": i % grid.cols + 1 if grid else "",
                         "px_x": None, "px_y": None, "mm_x": None, "mm_y": None,
                         "theo_x": tx, "theo_y": ty, "dx": None, "dy": None, "dev": None,
                         "verdict": "DETECT_FAIL", "method": "-", "radius": "", "status": "DETECT_FAIL",
                         "note": note})
            if callback:
                callback(n, total, f"P{n} 检测失败，跳过" if zh else f"P{n} detection failed, skipped")
            continue
        mm_x, mm_y, tx, ty, dx, dy, dev, verdict = _row_metrics(
            i, r, nominal, center, grid, coeff_x, coeff_y, order, dpi_value, tol_upper, tol_lower,
            piecewise=piecewise, res_fb=res_fb)
        cx0 = max(0, int(round(r.x)) - radius)
        cy0 = max(0, int(round(r.y)) - radius)
        cx1 = min(w_img, int(round(r.x)) + radius + 1)
        cy1 = min(h_img, int(round(r.y)) + radius + 1)
        roi = gray[cy0:cy1, cx0:cx1]
        if roi.size == 0:
            continue
        bgr = cv2.cvtColor(roi, cv2.COLOR_GRAY2BGR)
        bgr = _draw_annotations(bgr, r.x - cx0, r.y - cy0, r, mm_x, mm_y,
                                dx, dy, dev, verdict, n, method_name)
        fn = os.path.join(out_dir, f"p{n}.png")
        ok, buf = cv2.imencode(".png", bgr)
        if not ok:
            continue
        with open(fn, "wb") as fh:
            fh.write(buf.tobytes())
        saved += 1
        rows.append({"row": i // grid.cols + 1 if grid else "", "col": i % grid.cols + 1 if grid else "",
                     "px_x": r.x, "px_y": r.y, "mm_x": mm_x, "mm_y": mm_y,
                     "theo_x": tx, "theo_y": ty, "dx": dx, "dy": dy, "dev": dev,
                     "verdict": verdict, "method": _method_label(r, method_name),
                     "radius": radius, "status": ("手动修正" if zh else "Manual") if r.method == "manual" else "OK"})
        if callback:
            callback(n, total, f"P{n} 已保存" if zh else f"P{n} saved")

    csv_path = os.path.join(out_dir, "识别过程记录.csv" if zh else "Recognition_Record.csv")
    with open(csv_path, "w", newline="", encoding="utf-8-sig") as fh:
        w = csv.writer(fh)
        w.writerow((["编号", "行", "列", "像素X", "像素Y", "物理X(mm)", "物理Y(mm)",
                     "理论X(mm)", "理论Y(mm)", "ΔX(mm)", "ΔY(mm)", "偏差(mm)",
                     "判定", "识别方法", "识别窗口半径(px)", "状态", "备注"]
                    if zh else
                    ["No.", "Row", "Col", "Pixel X", "Pixel Y", "Physical X(mm)", "Physical Y(mm)",
                     "Theoretical X(mm)", "Theoretical Y(mm)", "ΔX(mm)", "ΔY(mm)", "Deviation(mm)",
                     "Verdict", "Method", "Window Radius(px)", "Status", "Note"]))
        for i, row in enumerate(rows):
            n = display_no(i, grid.rows, grid.cols, order_mode) if grid else i + 1
            w.writerow([f"P{n}", row["row"], row["col"],
                        "--" if row["px_x"] is None else f"{row['px_x']:.3f}",
                        "--" if row["px_y"] is None else f"{row['px_y']:.3f}",
                        "--" if row["mm_x"] is None else f"{row['mm_x']:.4f}",
                        "--" if row["mm_y"] is None else f"{row['mm_y']:.4f}",
                        "" if row["theo_x"] is None else f"{row['theo_x']:.4f}",
                        "" if row["theo_y"] is None else f"{row['theo_y']:.4f}",
                        "" if row["dx"] is None else f"{row['dx']:.4f}",
                        "" if row["dy"] is None else f"{row['dy']:.4f}",
                        "" if row["dev"] is None else f"{row['dev']:.4f}",
                        row["verdict"], row["method"], row["radius"], row["status"],
                        (row.get("note") or ("检测失败" if zh else "Detection failed")) if row["status"] == "DETECT_FAIL" else ""])
        # ---- F 模块：报表尾部统计区（失败计数独立、误差统计排除失败行）----
        # 成功口径与 GUI 汇总一致：仅按 DETECT_FAIL 划分；dev 缺失（无模板且
        # 名义值为空）不降级为失败，统计时按可用值计算
        ok_rows = [r for r in rows if r["status"] != "DETECT_FAIL"]
        fail_rows = [r for r in rows if r["status"] == "DETECT_FAIL"]
        w.writerow([])
        w.writerow(["==== 统计区 ===="] if zh else ["==== Statistics ===="])
        w.writerow(["总点数" if zh else "Total points", len(rows)])
        w.writerow(["成功数" if zh else "Passed", len(ok_rows)])
        w.writerow(["失败数" if zh else "Failed", len(fail_rows)])
        w.writerow(["失败率" if zh else "Fail rate", f"{len(fail_rows) / len(rows) * 100:.2f}%" if rows else "0.00%"])
        if ok_rows:
            dxs = [r["dx"] for r in ok_rows if r["dx"] is not None]
            dys = [r["dy"] for r in ok_rows if r["dy"] is not None]
            devs = [r["dev"] for r in ok_rows if r["dev"] is not None]
            def _rms(vals):
                return math.sqrt(sum(v * v for v in vals) / len(vals)) if vals else 0.0
            def _mean(vals):
                return sum(vals) / len(vals) if vals else 0.0
            def _max_abs(vals):
                return max(abs(v) for v in vals) if vals else 0.0
            w.writerow(["误差统计（仅成功点，失败点排除）" if zh else "Error stats (pass only, fail excluded)", "RMS(mm)", "Max(mm)", "Mean(mm)"])
            w.writerow(["X分量" if zh else "X-Comp", f"{_rms(dxs):.4f}", f"{_max_abs(dxs):.4f}", f"{_mean(dxs):.4f}"])
            w.writerow(["Y分量" if zh else "Y-Comp", f"{_rms(dys):.4f}", f"{_max_abs(dys):.4f}", f"{_mean(dys):.4f}"])
            w.writerow(["合成" if zh else "Combined", f"{_rms(devs):.4f}", f"{_max_abs(devs):.4f}", f"{_mean(devs):.4f}"])
        if fail_rows:
            w.writerow([])
            w.writerow(["==== 失败点行列清单（便于定位图纸位置补测）===="] if zh else ["==== Failed points list (for locating & re-test) ===="])
            w.writerow(["编号", "行", "列", "名义X(mm)", "名义Y(mm)", "状态"] if zh else ["No.", "Row", "Col", "Nominal X(mm)", "Nominal Y(mm)", "Status"])
            for i, row in enumerate(rows):
                if row["status"] == "DETECT_FAIL":
                    w.writerow([f"P{display_no(i, grid.rows, grid.cols, order_mode) if grid else i + 1}", row["row"], row["col"],
                                "" if row["theo_x"] is None else f"{row['theo_x']:.4f}",
                                "" if row["theo_y"] is None else f"{row['theo_y']:.4f}",
                                row["status"]])
    return saved, missing, csv_path
